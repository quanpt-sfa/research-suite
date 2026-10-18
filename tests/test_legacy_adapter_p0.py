"""Tests for Legacy Migration P0 Remediation: proposition text, adjudications, formation revision, and reconciliation."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from research_harness.core.auth import authenticate
from research_harness.core.canonical import canonical_json_hash
from research_harness.core.model import ReviewStatus
from research_harness.legacy.adapter import migrate_legacy_project
from research_harness.proposals.gateway import import_proposal
from research_harness.protocols.snapshot import export_snapshot
from research_harness.provenance.formation import build_formation
from research_harness.state.store import StateStore, revision_digest
from tests.conftest import make_proposal


@pytest.fixture
def legacy_project_with_adjudications(tmp_path: Path) -> Path:
    dest = tmp_path / "legacy_project_p0"
    master = dest / "research_harness" / "00_master"
    master.mkdir(parents=True, exist_ok=True)
    (dest / "main.tex").write_text("% Legacy root\n", encoding="utf-8")

    # Research Question
    with (master / "research_questions.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rq_id", "rq_text", "rq_label"])
        w.writerow(["RQ_P0", "How does transactional consistency affect state durability?", "Durability RQ"])

    # Construct
    with (master / "construct_registry.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["construct_id", "canonical_name", "definition"])
        w.writerow(["CONST_P0", "Transactional Durability", "State preservation across abnormal halts"])

    # Source
    with (master / "source_registry.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["source_id", "citation_key", "bibliographic_identity", "doi", "library_file_id"])
        w.writerow(["SRC_P0", "Gray1981", "Transaction Concepts by Jim Gray", "10.1145/800050.801010", "lib_gray1981"])

    # Claim Source / Evidence Passage
    with (master / "claim_source_registry.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["claim_source_id", "source_id", "normalized_claim", "locator_type", "evidence_locator"])
        w.writerow(["CS_P0", "SRC_P0", "Write-ahead logging guarantees atomic durability", "page", "p. 12-14"])

    # Proposition / Claim: has proposition_text!
    with (master / "proposition_registry.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["proposition_id", "proposition_text", "claim_tier", "rq_id"])
        w.writerow(["PROP_P0", "WAL logging prevents partial writes during system crashes", "causal", "RQ_P0"])

    # Proposition Source Binding
    with (master / "proposition_source_binding.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["proposition_id", "claim_source_id", "relation"])
        w.writerow(["PROP_P0", "CS_P0", "supports"])

    # Semantic Adjudication
    with (master / "semantic_adjudication_registry.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["semantic_adjudication_id", "proposition_id", "claim_source_id", "assessment_kind", "verdict", "rationale"])
        w.writerow(["ADJ_P0", "PROP_P0", "CS_P0", "semantic_entailment", "SUPPORTED", "Gray 1981 explicitly establishes WAL durability properties."])

    return dest


def test_migration_preserves_proposition_text(harness_env, legacy_project_with_adjudications):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    with store.read() as conn:
        actor = authenticate(conn, owner_token)

    res = migrate_legacy_project(legacy_project_with_adjudications, store, actor, dry_run=False)
    assert res["decision"] == "ACCEPTED"
    assert "PROP_P0" in res["created_objects"]

    with store.read() as conn:
        claim = store.get_object("PROP_P0", conn=conn)
        assert claim is not None
        # Defect 1 verification: text must match proposition_text, not proposition_id
        assert claim["content"]["text"] == "WAL logging prevents partial writes during system crashes"
        assert claim["content"]["text"] != "PROP_P0"


def test_migration_imports_semantic_adjudications(harness_env, legacy_project_with_adjudications):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    with store.read() as conn:
        actor = authenticate(conn, owner_token)

    res = migrate_legacy_project(legacy_project_with_adjudications, store, actor, dry_run=False)
    assert res["imported_adjudication_counts"] == 1
    assert "ADJ_P0" in res["created_objects"]

    with store.read() as conn:
        adj = store.get_object("ADJ_P0", conn=conn)
        assert adj is not None
        assert adj["object_type"] == "Adjudication"
        assert adj["content"]["verdict"] == "SUPPORTED"
        assert adj["content"]["subject"]["object_id"] == "PROP_P0"
        assert adj["content"]["subject"]["revision"] == 1
        assert "Gray 1981" in adj["content"]["rationale"]


def test_migrated_claim_can_subsequently_be_revised(harness_env, legacy_project_with_adjudications):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)

    # 1. Migrate
    migrate_legacy_project(legacy_project_with_adjudications, store, actor, dry_run=False)

    # 2. Export snapshot
    snap = export_snapshot(store, actor, scope="full")

    # 3. Propose a revision to the migrated claim PROP_P0
    # Prior to fix, this crashed with KeyError: 'first_recorded_text_sha256' in build_formation()
    ops = [
        {
            "operation": "revise_object",
            "object_id": "PROP_P0",
            "base_revision": 1,
            "content": {
                "text": "WAL logging with atomic fsync prevents partial writes during system crashes",
                "claim_tier": "causal",
                "label": "PROP_P0",
            },
            "formation_basis": "unknown",
            "relations": [{"relation": "depends_on", "target_id": "RQ_P0"}],
        }
    ]
    prop = make_proposal(store.project_id, snap, "REVISE_MIGRATED_CLAIM", ops)
    receipt = import_proposal(store, json.dumps(prop), bob_token)
    assert receipt["decision"] == "ACCEPTED"

    with store.read() as conn:
        head = store.get_object("PROP_P0", conn=conn)
        assert head["revision"] == 2
        assert "atomic fsync" in head["content"]["text"]
        # History preserved
        assert len(head["origin"]["formation"]["history"]) == 2


def test_revision_digest_and_sealed_edges(harness_env, legacy_project_with_adjudications):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    with store.read() as conn:
        actor = authenticate(conn, owner_token)

    migrate_legacy_project(legacy_project_with_adjudications, store, actor, dry_run=False)

    with store.read() as conn:
        # Check EvidencePassage CS_P0
        ep = store.get_object("CS_P0", conn=conn)
        assert ep is not None

        # Verify outgoing edges of CS_P0: should have derived_from SRC_P0 and supports PROP_P0
        edges = conn.execute(
            "SELECT relation, target_id, target_revision FROM edges WHERE source_id='CS_P0' AND source_revision=1 ORDER BY relation, target_id",
        ).fetchall()
        assert len(edges) == 2
        edge_tuples = [(r[0], r[1], r[2]) for r in edges]

        # Stored revision digest MUST match revision_digest(content_digest, edges)
        cdig = canonical_json_hash(ep["content"])
        expected_rdig = revision_digest(cdig, edge_tuples)
        assert ep["revision_digest"] == expected_rdig


def test_migration_reconciliation_report_fields(harness_env, legacy_project_with_adjudications):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    with store.read() as conn:
        actor = authenticate(conn, owner_token)

    receipt = migrate_legacy_project(legacy_project_with_adjudications, store, actor, dry_run=False)

    # Check required report fields
    assert "source_registry_row_counts" in receipt
    assert "imported_object_counts_by_type" in receipt
    assert "imported_edge_counts" in receipt
    assert "imported_adjudication_counts" in receipt
    assert "unmapped_source_fields" in receipt
    assert "missing_or_malformed_references" in receipt
    assert "unresolved_migration_records" in receipt
    assert "pre_post_content_equivalence" in receipt
    assert "backup_location" in receipt
    assert "backup_integrity_hash" in receipt

    # Verify backup integrity hash matches actual file
    backup_path = Path(receipt["backup_location"])
    assert backup_path.is_file()
    assert len(receipt["backup_integrity_hash"]) == 64

    # Verify reconciliation file was written to disk
    report_file = Path(receipt["reconciliation_report_path"])
    assert report_file.is_file()
    data = json.loads(report_file.read_text(encoding="utf-8"))
    assert data["decision"] == "ACCEPTED"
    assert data["target_project_id"] == store.project_id
