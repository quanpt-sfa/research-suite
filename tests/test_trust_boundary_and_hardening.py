"""Trust-boundary reproduction and acceptance hardening tests.

Mandatory invariants:
1. Untrusted actors propose scientific content.
2. Deterministic code validates technical properties.
3. Authenticated and appropriately authorized humans record scientific judgments.
4. Historical states remain immutable.
5. No evidence or authorization is inherited across revisions without an explicit valid rule.
"""
from __future__ import annotations

import csv
import json
import pytest
from pathlib import Path

from research_harness.core.auth import (
    Actor,
    authenticate,
    new_token,
    token_hash,
)
from research_harness.core.errors import AuthorizationError, HarnessError, ProposalRejection
from research_harness.core.model import OriginKind, ReviewStatus, SERVER_ONLY_TYPES
from research_harness.legacy.adapter import migrate_legacy_project
from research_harness.proposals.gateway import import_proposal
from research_harness.protocols.snapshot import export_snapshot
from research_harness.state.actors import register_actor
from research_harness.state.store import StateStore
from research_harness.validation.integrity import release_check
from research_harness.validation.review import record_review


def _create_test_project(tmp_path: Path):
    store, owner_token = StateStore.create(tmp_path, "proj_hardening")
    # Register an author
    author_res = register_actor(store, owner_token, actor_id="author_alice", roles=["author", "importer"])
    author_token = author_res["token"]
    # Register an independent reviewer
    rev_res = register_actor(
        store, owner_token, actor_id="reviewer_bob", roles=["independent_reviewer"],
        independence_group="external_inst"
    )
    rev_token = rev_res["token"]
    return store, owner_token, author_token, rev_token


# ==============================================================================================
# PART A: Eliminate InternalCapability bypass
# ==============================================================================================

def test_part_a_unauthenticated_actor_id_rejected(tmp_path: Path):
    """Part A: Calling write_tx with actor_id and no token must be rejected."""
    store, owner_token, _, _ = _create_test_project(tmp_path)
    with pytest.raises(AuthorizationError) as exc:
        with store.write_tx(actor_id="owner", require_roles=("owner",)) as tx:
            tx.audit("pwn", "UNAUTHORIZED")
    assert "AUTHENTICATION_FAILED" in str(exc.value.code)


def test_part_a_internal_capability_cannot_confer_runtime_privilege(tmp_path: Path):
    """Part A: Runtime write_tx must not accept unauthenticated internal capability."""
    store, owner_token, _, _ = _create_test_project(tmp_path)
    # Even if issue_internal_capability exists or someone crafts a capability, write_tx must reject it
    try:
        from research_harness.core.auth import issue_internal_capability
        cap = issue_internal_capability("owner", "maintenance")
    except (ImportError, AttributeError):
        cap = None

    if cap is not None:
        with pytest.raises(AuthorizationError) as exc:
            with store.write_tx(internal_capability=cap, require_roles=("owner",)) as tx:
                tx.audit("pwn", "UNAUTHORIZED")
        assert "AUTHENTICATION_FAILED" in str(exc.value.code)


def test_part_a_snapshot_export_requires_token(tmp_path: Path):
    """Part A: export_snapshot with unregistered actor must be rejected."""
    store, owner_token, _, _ = _create_test_project(tmp_path)
    untrusted_owner_actor = Actor("unregistered_attacker", frozenset(["owner"]), None, "Attacker")
    with pytest.raises((AuthorizationError, HarnessError)):
        export_snapshot(store, actor=untrusted_owner_actor, token=None)


def test_part_a_legacy_migration_requires_token(tmp_path: Path):
    """Part A: migrate_legacy_project without token must be rejected."""
    store, owner_token, _, _ = _create_test_project(tmp_path)
    legacy_dir = tmp_path / "legacy_raw"
    m = legacy_dir / "00_master"
    m.mkdir(parents=True)
    (m / "research_questions.csv").write_text("rq_id,rq_text\nRQ_01,Test RQ\n", encoding="utf-8")
    (m / "proposition_registry.csv").write_text("proposition_id,proposition_text\nPROP_01,Test claim\n", encoding="utf-8")

    untrusted_actor = Actor("owner", frozenset(["owner", "importer"]), None, "Owner")
    with pytest.raises((AuthorizationError, HarnessError)):
        migrate_legacy_project(legacy_dir, store, actor=untrusted_actor, token=None)


# ==============================================================================================
# PART B: Separate proposed adjudication from authorized scientific adjudication
# ==============================================================================================

def test_part_b_agent_cannot_propose_adjudication(tmp_path: Path):
    """Part B: Adjudication must be in SERVER_ONLY_TYPES and rejected at proposal gateway."""
    store, owner_token, author_token, rev_token = _create_test_project(tmp_path)

    # 1. Propose RQ
    snap = export_snapshot(store, token=author_token, scope="full")
    prop_doc = {
        "protocol_version": "9.0.0",
        "project_id": store.project_id,
        "proposal_id": "PROP_ADJ_ATTEMPT",
        "origin_channel": "codex",
        "base": {
            "snapshot_id": snap["snapshot_id"],
            "state_revision": snap["state_revision"],
            "state_sha256": snap["state_sha256"],
        },
        "read_set": {},
        "operations": [
            {
                "operation": "propose_object",
                "proposed_id": "RQ_001",
                "object_type": "ResearchQuestion",
                "content": {"text": "How does X affect Y?"},
            },
            {
                "operation": "propose_claim",
                "proposed_id": "CLAIM_001",
                "claim_tier": "descriptive",
                "text": "X is observed in Y.",
                "depends_on": ["RQ_001"],
            },
            {
                "operation": "propose_object",
                "proposed_id": "ADJ_FORGED",
                "object_type": "Adjudication",
                "content": {
                    "subject": {"object_id": "CLAIM_001", "revision": 1},
                    "assessment_kind": "semantic_entailment",
                    "verdict": "SUPPORTED",
                    "rationale": "Agent self-certification of validity.",
                },
            },
        ],
    }

    # Gateway must reject Adjudication as a server-only object type
    res = import_proposal(store, json.dumps(prop_doc), token=author_token)
    assert res["decision"] in {"REJECTED", "UNAUTHORIZED"}
    assert res.get("rule") == "PRIVILEGED_OBJECT_TYPE" or "Adjudication" in str(res)


# ==============================================================================================
# PART D: Prevent legacy repair from reversing legitimate research revisions
# ==============================================================================================

def test_part_d_counterexample_legitimate_revision_not_reverted(tmp_path: Path):
    """Part D: Legitimate post-migration research revision must not be reverted by migration re-run."""
    store, owner_token, author_token, _ = _create_test_project(tmp_path)

    legacy_dir = tmp_path / "legacy_proj"
    m = legacy_dir / "00_master"
    m.mkdir(parents=True)
    (m / "research_questions.csv").write_text("rq_id,rq_text\nRQ_01,Research question\n", encoding="utf-8")
    (m / "proposition_registry.csv").write_text(
        "proposition_id,proposition_text,claim_tier\nPROP_01,Initial legacy text A,descriptive\n", encoding="utf-8"
    )

    # 1. First migration
    mig_res = migrate_legacy_project(legacy_dir, store, token=owner_token)
    assert mig_res["status"] in {"PASS", "PASS_WITH_WARNINGS"}

    claim_head1 = store.get_object("PROP_01")
    assert claim_head1["content"]["text"] == "Initial legacy text A"
    assert claim_head1["head_revision"] == 1

    # 2. Researcher revises the claim to Text B through proposal gateway
    snap = export_snapshot(store, token=author_token, scope="full")
    prop_doc = {
        "protocol_version": "9.0.0",
        "project_id": store.project_id,
        "proposal_id": "PROP_LEGIT_REVISE",
        "origin_channel": "codex",
        "base": {
            "snapshot_id": snap["snapshot_id"],
            "state_revision": snap["state_revision"],
            "state_sha256": snap["state_sha256"],
        },
        "read_set": {"PROP_01": 1},
        "operations": [
            {
                "operation": "revise_object",
                "object_id": "PROP_01",
                "base_revision": 1,
                "content": {
                    "text": "Legitimate researcher revised text B",
                    "claim_tier": "descriptive",
                    "label": "PROP_01",
                },
            }
        ],
    }
    rev_res = import_proposal(store, json.dumps(prop_doc), token=author_token)
    assert rev_res["decision"] == "ACCEPTED"

    claim_head2 = store.get_object("PROP_01")
    assert claim_head2["head_revision"] == 2
    assert claim_head2["content"]["text"] == "Legitimate researcher revised text B"

    # 3. Re-run legacy migration with unchanged legacy source (containing text A)
    mig_res2 = migrate_legacy_project(legacy_dir, store, token=owner_token)
    assert mig_res2["status"] in {"PASS", "PASS_WITH_WARNINGS"}

    # Invariant: Head must remain Text B, revision must remain 2, no automatic repair to Text A
    claim_head3 = store.get_object("PROP_01")
    assert claim_head3["head_revision"] == 2
    assert claim_head3["content"]["text"] == "Legitimate researcher revised text B"


# ==============================================================================================
# PART C: Explicit and consistent claim-tier scientific policies
# ==============================================================================================

def test_part_c_predictive_claim_requires_predictive_validation(tmp_path: Path):
    """Part C: Predictive claim linked only to generic passage lacks predictive validation."""
    store, owner_token, _, _ = _create_test_project(tmp_path)
    with store.write_tx(token=owner_token) as tx:
        tx.add_revision(object_id="RQ_01", object_type="ResearchQuestion", content={"text": "RQ"}, origin={"kind": "PROPOSAL_IMPORTED"}, edges=[])
        tx.add_revision(object_id="EV_01", object_type="EvidencePassage", content={"locator": "p. 10"}, origin={"kind": "PROPOSAL_IMPORTED"}, edges=[])
        tx.add_revision(
            object_id="CLAIM_PRED", object_type="Claim",
            content={"text": "Predictive model achieves accuracy X", "claim_tier": "predictive"},
            origin={"kind": "PROPOSAL_IMPORTED"},
            edges=[("depends_on", "RQ_01", 1, {}), ("supports", "EV_01", 1, {})]
        )
    rel = release_check(store)
    assert rel["release_eligibility"] == "BLOCKED"
    assert any("PREDICTIVE" in r for r in rel["block_reasons"])


def test_part_c_measurement_validation_requires_proxy_and_construct(tmp_path: Path):
    """Part C: Measurement validation claim with construct only lacks proxy operationalization."""
    store, owner_token, _, _ = _create_test_project(tmp_path)
    with store.write_tx(token=owner_token) as tx:
        tx.add_revision(object_id="RQ_01", object_type="ResearchQuestion", content={"text": "RQ"}, origin={"kind": "PROPOSAL_IMPORTED"}, edges=[])
        tx.add_revision(object_id="CONST_01", object_type="Construct", content={"definition": "Def", "label": "CONST_01"}, origin={"kind": "PROPOSAL_IMPORTED"}, edges=[])
        tx.add_revision(
            object_id="CLAIM_MEAS", object_type="Claim",
            content={"text": "Construct X is measured by Y", "claim_tier": "measurement_validation"},
            origin={"kind": "PROPOSAL_IMPORTED"},
            edges=[("depends_on", "RQ_01", 1, {}), ("depends_on", "CONST_01", 1, {})]
        )
    rel = release_check(store)
    assert rel["release_eligibility"] == "BLOCKED"
    assert any("MEASUREMENT" in r or "proxy" in r.lower() for r in rel["block_reasons"])


def test_part_c_causal_claim_requires_empirical_and_adjudication(tmp_path: Path):
    """Part C: Causal claim with generic result but no authorized adjudication fails release."""
    store, owner_token, _, _ = _create_test_project(tmp_path)
    with store.write_tx(token=owner_token) as tx:
        tx.add_revision(object_id="RQ_01", object_type="ResearchQuestion", content={"text": "RQ"}, origin={"kind": "PROPOSAL_IMPORTED"}, edges=[])
        tx.add_revision(object_id="RES_01", object_type="Result", content={"summary": "Empirical regression table", "label": "RES_01"}, origin={"kind": "PROPOSAL_IMPORTED"}, edges=[])
        tx.add_revision(
            object_id="CLAIM_CAUSAL", object_type="Claim",
            content={"text": "Treatment X causally increases Y", "claim_tier": "causal"},
            origin={"kind": "PROPOSAL_IMPORTED"},
            edges=[("depends_on", "RQ_01", 1, {}), ("supports", "RES_01", 1, {})]
        )
    rel = release_check(store, policy={"require_causal_adjudication": True})
    assert rel["release_eligibility"] == "BLOCKED"
    assert any("CAUSAL" in r for r in rel["block_reasons"])


def test_part_c_mechanism_consistent_requires_mechanism(tmp_path: Path):
    """Part C: Mechanism-consistent claim requires mechanism linkage."""
    store, owner_token, _, _ = _create_test_project(tmp_path)
    with store.write_tx(token=owner_token) as tx:
        tx.add_revision(object_id="RQ_01", object_type="ResearchQuestion", content={"text": "RQ"}, origin={"kind": "PROPOSAL_IMPORTED"}, edges=[])
        tx.add_revision(object_id="RES_01", object_type="Result", content={"summary": "Empirical observation", "label": "RES_01"}, origin={"kind": "PROPOSAL_IMPORTED"}, edges=[])
        tx.add_revision(
            object_id="CLAIM_MECH", object_type="Claim",
            content={"text": "Findings are consistent with proposed channel", "claim_tier": "mechanism_consistent"},
            origin={"kind": "PROPOSAL_IMPORTED"},
            edges=[("depends_on", "RQ_01", 1, {}), ("supports", "RES_01", 1, {})]
        )
    rel = release_check(store)
    assert rel["release_eligibility"] == "BLOCKED"
    assert any("MECHANISM" in r for r in rel["block_reasons"])


def test_part_c_conceptual_claim_does_not_require_dataset(tmp_path: Path):
    """Part C: Conceptual claim links to theory/construct and does not require empirical dataset."""
    store, owner_token, _, _ = _create_test_project(tmp_path)
    with store.write_tx(token=owner_token) as tx:
        tx.add_revision(object_id="RQ_01", object_type="ResearchQuestion", content={"text": "RQ"}, origin={"kind": "PROPOSAL_IMPORTED"}, edges=[])
        tx.add_revision(object_id="THEORY_01", object_type="Theory", content={"text": "Formal theory", "label": "THEORY_01"}, origin={"kind": "PROPOSAL_IMPORTED"}, edges=[])
        tx.add_revision(
            object_id="CLAIM_CONC", object_type="Claim",
            content={"text": "Theoretical proposition", "claim_tier": "conceptual"},
            origin={"kind": "PROPOSAL_IMPORTED"},
            edges=[("depends_on", "RQ_01", 1, {}), ("depends_on", "THEORY_01", 1, {})]
        )
    # Review claim so review status doesn't block
    record_review(store, owner_token, object_id="CLAIM_CONC", revision=1, kind="author_approval", decision="approve")
    rel = release_check(store)
    # Should not be blocked by lack of empirical dataset
    assert not any("DATASET" in r or "EMPIRICAL" in r for r in rel["block_reasons"])

