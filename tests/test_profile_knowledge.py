"""Phase 3A.1c: the new knowledge object types, and static profiles migrated into scoped KnowledgeItems."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from research_harness import taxonomy
from research_harness.core.auth import authenticate
from research_harness.core.model import ID_PATTERN, OBJECT_TYPES, SERVER_ONLY_TYPES, Decision
from research_harness.proposals.gateway import import_proposal
from research_harness.protocols.schema import PROPOSABLE_TYPES, all_schemas, content_schema, validate_proposal_structure
from research_harness.protocols.snapshot import export_snapshot
from research_harness.workflows.domain_engine import load_builtin_spec
from research_harness.workflows.profiles.knowledge import build_proposal, profile_knowledge_operations
from research_harness.workflows.profiles.registry import get_profile, list_profiles
from tests.conftest import make_proposal

NEW_TYPES = ("DomainWorkflow", "MethodPlaybook", "JournalPolicy", "KnowledgeItem", "ContributionArgument", "AnalysisPlan")
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate_profiles.py"


def test_new_types_are_registered_after_the_existing_ones_and_are_proposable():
    assert OBJECT_TYPES[:14] == ("ResearchQuestion", "Theory", "Construct", "Mechanism", "Proxy", "Dataset", "Source",
                                 "EvidencePassage", "Analysis", "Result", "Claim", "ManuscriptArtifact", "Adjudication", "Approval")
    assert OBJECT_TYPES[14:] == NEW_TYPES
    assert all(t in PROPOSABLE_TYPES and t not in SERVER_ONLY_TYPES for t in NEW_TYPES)
    assert all(f"object_{t}.v" in " ".join(all_schemas()) for t in NEW_TYPES)


def test_knowledge_item_schema_uses_the_shared_vocabularies():
    props = content_schema("KnowledgeItem")["properties"]
    assert tuple(props["kind"]["enum"]) == taxonomy.vocabulary("knowledge_kind")
    assert tuple(content_schema("JournalPolicy")["properties"]["policy_type"]["enum"]) == taxonomy.vocabulary("journal_policy_type")
    assert content_schema("KnowledgeItem")["required"] == ["label", "kind"]


@pytest.mark.parametrize("profile_id", [p["identifier"] for p in list_profiles()])
def test_migration_scopes_each_item_to_what_the_profile_actually_states(profile_id):
    prof = get_profile(profile_id)
    ops = profile_knowledge_operations(profile_id)
    assert len(ops) == len(prof.methodological_checklist) + len(prof.validity_questions)
    ids = [o["proposed_id"] for o in ops]
    assert len(set(ids)) == len(ids) and all(re.match(ID_PATTERN, i) for i in ids)
    for op in ops:
        c = op["content"]
        assert op["object_type"] == "KnowledgeItem" and c["kind"] == "checklist" and c["generated_by"] == "import"
        assert c["scope"] == {"domain": profile_id, "data_regime": {"data_source": list(prof.preferred_evidence_types)}}
        assert "Not established outside the data sources this profile lists" in c["exclusions"][0]
        assert c["evidence_basis"] == []  # nothing supports it yet: it cannot be promoted into a shared library
        assert c["validation"] == {"source_fidelity": "NOT_CHECKED", "computational_reproducibility": "NOT_ATTEMPTED", "methodological_validity": []}
        assert "jurisdiction" not in c["scope"]["data_regime"]  # not stated by the profile, so not inferred


def test_the_accounting_profile_is_scoped_to_its_own_sources_not_to_the_field():
    scope = profile_knowledge_operations("accounting")[0]["content"]["scope"]
    assert "Compustat and CRSP financial panels" in scope["data_regime"]["data_source"] and scope["domain"] == "accounting"
    assert profile_knowledge_operations("education")[0]["content"]["scope"]["domain"] == "education"
    assert profile_knowledge_operations("accounting")[0]["content"]["scope"] != profile_knowledge_operations("education")[0]["content"]["scope"]


def test_unknown_profile_and_undeclared_domain():
    with pytest.raises(KeyError):
        profile_knowledge_operations("astrology")


def test_migrated_operations_are_a_valid_proposal_that_imports_through_the_gateway(harness_env):
    store, owner, bob = harness_env["store"], harness_env["owner_token"], harness_env["actors"]["bob"]["token"]
    with store.read() as conn:
        actor = authenticate(conn, owner)
    snap = export_snapshot(store, actor, scope="full", token=owner)
    ops = profile_knowledge_operations("accounting")
    proposal = build_proposal(store.project_id, snap, "PROP_PROFILES1", ops)
    assert validate_proposal_structure(proposal) == []
    receipt = import_proposal(store, json.dumps(proposal).encode("utf-8"), bob)
    assert receipt["decision"] == Decision.ACCEPTED.value
    assert sorted(receipt["created_objects"]) == sorted(o["proposed_id"] for o in ops)
    stored = store.get_object(ops[0]["proposed_id"])
    assert stored["object_type"] == "KnowledgeItem" and stored["content"]["scope"]["domain"] == "accounting"


def test_a_malformed_knowledge_item_is_rejected_by_the_gateway(harness_env):
    store, owner, bob = harness_env["store"], harness_env["owner_token"], harness_env["actors"]["bob"]["token"]
    with store.read() as conn:
        actor = authenticate(conn, owner)
    snap = export_snapshot(store, actor, scope="full", token=owner)
    bad = [{"operation": "propose_object", "proposed_id": "KI_BAD", "object_type": "KnowledgeItem",
            "content": {"label": "x", "kind": "vibes"}, "relations": []}]
    receipt = import_proposal(store, json.dumps(make_proposal(store.project_id, snap, "PROP_BAD0001", bad)).encode("utf-8"), bob)
    assert receipt["decision"] != Decision.ACCEPTED.value


def test_a_domain_workflow_spec_can_be_proposed_as_an_object(harness_env):
    store, owner, bob = harness_env["store"], harness_env["owner_token"], harness_env["actors"]["bob"]["token"]
    with store.read() as conn:
        actor = authenticate(conn, owner)
    snap = export_snapshot(store, actor, scope="full", token=owner)
    spec = load_builtin_spec("LIB_DW_ACC_FRAUD_DETECTION")
    ops = [{"operation": "propose_object", "proposed_id": "DW_FRAUD_PILOT", "object_type": "DomainWorkflow",
            "content": {"label": spec.id, "spec": spec.to_dict(), "spec_digest": spec.digest()}, "relations": []}]
    receipt = import_proposal(store, json.dumps(make_proposal(store.project_id, snap, "PROP_DW000001", ops)).encode("utf-8"), bob)
    assert receipt["decision"] == Decision.ACCEPTED.value
    assert store.get_object("DW_FRAUD_PILOT")["content"]["spec_digest"] == spec.digest()


def test_the_migration_script_prints_operations_and_writes_nothing_to_a_project(tmp_path, capsys):
    mod = importlib.util.spec_from_file_location("migrate_profiles", SCRIPT)
    m = importlib.util.module_from_spec(mod)
    mod.loader.exec_module(m)
    assert m.main(["education"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["operations"] and {o["content"]["scope"]["domain"] for o in out["operations"]} == {"education"}
    target = tmp_path / "ops.json"
    assert m.main(["--out", str(target)]) == 0
    all_ops = json.loads(target.read_text(encoding="utf-8"))["operations"]
    assert {o["content"]["scope"]["domain"] for o in all_ops} == {"accounting", "finance", "education"}
