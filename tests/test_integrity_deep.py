"""Phase C: Comprehensive Negative Cryptographic and Relational Integrity Tests.

Verifies that any deliberate tampering or corruption fails closed:
1. Content digest corruption -> FAIL (OBJECT_CONTENT_DIGEST_MISMATCH)
2. Origin digest corruption -> FAIL (OBJECT_ORIGIN_DIGEST_MISMATCH)
3. Edge tampering / unsealed late edges -> FAIL (REVISION_DIGEST_MISMATCH)
4. Corrupted CAS artifact blob -> FAIL (ARTIFACT_BLOB_CORRUPT)
5. Missing referenced CAS artifact -> FAIL (REFERENCED_ARTIFACT_MISSING)
6. Orphan CAS artifact -> Advisory INFO only (PASS)
7. Approval digest mismatch (approving mutated revision) -> FAIL (APPROVAL_DIGEST_MISMATCH)
8. Approval by unregistered actor -> FAIL (APPROVAL_ACTOR_UNKNOWN)
9. Tampered event log hash chain -> FAIL (EVENT_CHAIN_BROKEN)
10. Tampered actor table out of sync with events -> FAIL (ACTOR_EVENT_MISMATCH)
"""
from __future__ import annotations

import json
import sqlite3
import pytest

from research_harness.core.auth import authenticate
from research_harness.core.canonical import canonical_json_hash
from research_harness.core.model import IntegrityStatus, ReviewStatus
from research_harness.proposals.gateway import import_proposal
from research_harness.protocols.snapshot import export_snapshot
from research_harness.validation.integrity import audit_project_integrity, verify_database_integrity
from research_harness.validation.review import record_review
from tests.conftest import make_proposal


@pytest.fixture
def populated_project(harness_env):
    """Fixture providing a project with an RQ, a Source, a Claim, and an EvidencePassage."""
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap = export_snapshot(store, actor, scope="full")

    # Store a CAS artifact
    sample_bytes = b"Sample artifact raw data for semantic testing"
    art_hash = store.artifacts.put(sample_bytes)

    ops = [
        {
            "operation": "propose_object",
            "proposed_id": "RQ_INT_01",
            "object_type": "ResearchQuestion",
            "content": {"label": "RQ", "text": "Does integrity auditing prevent silent tampering?"},
            "relations": [],
        },
        {
            "operation": "propose_object",
            "proposed_id": "SRC_INT_01",
            "object_type": "Source",
            "content": {"citation_key": "Lamport1982", "bibliographic_identity": "Byzantine Agreement"},
            "relations": [],
        },
        {
            "operation": "propose_object",
            "proposed_id": "EP_INT_01",
            "object_type": "EvidencePassage",
            "content": {
                "locator": "p. 382",
                "normalized_claim": "Byzantine fault tolerance requires signed messages.",
            },
            "relations": [{"relation": "derived_from", "target_id": "SRC_INT_01"}],
        },
        {
            "operation": "propose_object",
            "proposed_id": "ART_INT_01",
            "object_type": "ManuscriptArtifact",
            "content": {
                "label": "Manuscript Draft",
                "section": "Methods",
                "artifact_sha256": art_hash,
            },
            "relations": [],
        },
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_INT_01",
            "claim_tier": "descriptive",
            "text": "Byzantine fault tolerance requires cryptographic proof.",
            "depends_on": ["RQ_INT_01"],
            "relations": [
                {"relation": "depends_on", "target_id": "RQ_INT_01"},
                {"relation": "depends_on", "target_id": "EP_INT_01"},
            ],
        },
    ]

    prop = make_proposal(store.project_id, snap, "PROP_INT_01", ops)
    rec = import_proposal(store, json.dumps(prop), bob_token)
    assert rec["decision"] == "ACCEPTED"

    return {
        **harness_env,
        "artifact_hash": art_hash,
    }


def test_clean_project_passes_integrity_audit(populated_project):
    store = populated_project["store"]
    audit = audit_project_integrity(store)
    assert audit["status"] == IntegrityStatus.PASS.value
    hard_findings = [f for f in audit["findings"] if f.get("severity") != "INFO"]
    assert len(hard_findings) == 0


def test_corrupt_content_digest_fails_audit(populated_project):
    store = populated_project["store"]

    # Drop append-only trigger to simulate direct low-level sqlite file tampering
    with sqlite3.connect(store.paths.db) as conn:
        conn.execute("DROP TRIGGER IF EXISTS objects_no_update")
        conn.execute(
            "UPDATE objects SET content = json_set(content, '$.text', 'Tampered claim text') WHERE object_id='CLAIM_INT_01'"
        )
        conn.commit()

    audit = audit_project_integrity(store)
    assert audit["status"] == IntegrityStatus.FAIL.value
    rules = [f["rule"] for f in audit["findings"]]
    assert "OBJECT_CONTENT_DIGEST_MISMATCH" in rules


def test_corrupt_origin_digest_fails_audit(populated_project):
    store = populated_project["store"]

    with sqlite3.connect(store.paths.db) as conn:
        conn.execute("DROP TRIGGER IF EXISTS objects_no_update")
        conn.execute(
            "UPDATE objects SET origin = json_set(origin, '$.actor_id', 'malicious_actor') WHERE object_id='CLAIM_INT_01'"
        )
        conn.commit()

    audit = audit_project_integrity(store)
    assert audit["status"] == IntegrityStatus.FAIL.value
    rules = [f["rule"] for f in audit["findings"]]
    assert "OBJECT_ORIGIN_DIGEST_MISMATCH" in rules


def test_edge_tampering_fails_audit(populated_project):
    store = populated_project["store"]

    # Drop seal trigger to simulate raw disk/file insertion of an unsealed edge
    with sqlite3.connect(store.paths.db) as conn:
        conn.execute("DROP TRIGGER IF EXISTS edges_sealed_at_revision_creation")
        conn.execute(
            "INSERT INTO edges (source_id, source_revision, target_id, target_revision, relation, provenance, created_state_revision) "
            "VALUES ('CLAIM_INT_01', 1, 'SRC_INT_01', 1, 'tests', '{}', 1)"
        )
        conn.commit()

    audit = audit_project_integrity(store)
    assert audit["status"] == IntegrityStatus.FAIL.value
    rules = [f["rule"] for f in audit["findings"]]
    assert "REVISION_DIGEST_MISMATCH" in rules


def test_corrupted_cas_artifact_fails_audit(populated_project):
    store = populated_project["store"]
    art_hash = populated_project["artifact_hash"]

    # Tamper with the artifact blob file on disk
    blob_path = store.artifacts.path(art_hash)
    with open(blob_path, "wb") as f:
        f.write(b"Corrupted bytes replacing original data")

    audit = audit_project_integrity(store)
    assert audit["status"] == IntegrityStatus.FAIL.value
    rules = [f["rule"] for f in audit["findings"]]
    assert "ARTIFACT_BLOB_CORRUPT" in rules or "REFERENCED_ARTIFACT_CORRUPT" in rules


def test_missing_referenced_cas_artifact_fails_audit(populated_project):
    store = populated_project["store"]
    art_hash = populated_project["artifact_hash"]

    # Delete the artifact file from CAS
    blob_path = store.artifacts.path(art_hash)
    blob_path.unlink()

    audit = audit_project_integrity(store)
    assert audit["status"] == IntegrityStatus.FAIL.value
    rules = [f["rule"] for f in audit["findings"]]
    assert "REFERENCED_ARTIFACT_MISSING" in rules


def test_orphan_cas_artifact_advisory_only(populated_project):
    store = populated_project["store"]

    # Put a blob that is never referenced in object content or proposals
    orphan_hash = store.artifacts.put(b"Unreferenced orphan blob data")

    audit = audit_project_integrity(store)
    # Orphan blob produces INFO finding, so overall status remains PASS
    assert audit["status"] == IntegrityStatus.PASS.value
    info_findings = [f for f in audit["findings"] if f.get("rule") == "ORPHAN_ARTIFACT_BLOB"]
    assert any(f["digest"] == orphan_hash for f in info_findings)
    assert all(f["severity"] == "INFO" for f in info_findings)


def test_tampered_approval_digest_binding_fails_audit(populated_project):
    store = populated_project["store"]
    carol_token = populated_project["actors"]["carol"]["token"]

    # Record legitimate review approval
    rec = record_review(store, carol_token, object_id="CLAIM_INT_01", revision=1,
                        kind="independent_review", decision="approve", rationale="Verified")
    app_id = rec["approval_id"]

    audit_before = audit_project_integrity(store)
    assert audit_before["status"] == IntegrityStatus.PASS.value

    # Tamper with subject_revision_digest inside the Approval object content
    with sqlite3.connect(store.paths.db) as conn:
        conn.execute("DROP TRIGGER IF EXISTS objects_no_update")
        row = conn.execute("SELECT content FROM objects WHERE object_id=?", (app_id,)).fetchone()
        c_dict = json.loads(row[0])
        c_dict["subject_revision_digest"] = "0" * 64
        new_c_str = json.dumps(c_dict, sort_keys=True)
        new_c_dig = canonical_json_hash(c_dict)
        conn.execute(
            "UPDATE objects SET content = ?, content_digest = ? WHERE object_id=?",
            (new_c_str, new_c_dig, app_id)
        )
        conn.commit()

    audit_after = audit_project_integrity(store)
    assert audit_after["status"] == IntegrityStatus.FAIL.value
    rules = [f["rule"] for f in audit_after["findings"]]
    assert "APPROVAL_DIGEST_MISMATCH" in rules


def test_approval_by_unknown_actor_fails_audit(populated_project):
    store = populated_project["store"]
    carol_token = populated_project["actors"]["carol"]["token"]

    rec = record_review(store, carol_token, object_id="CLAIM_INT_01", revision=1,
                        kind="independent_review", decision="approve", rationale="Verified")
    app_id = rec["approval_id"]

    # Alter the actor_id inside Approval content to an actor not in the registry
    with sqlite3.connect(store.paths.db) as conn:
        conn.execute("DROP TRIGGER IF EXISTS objects_no_update")
        row = conn.execute("SELECT content FROM objects WHERE object_id=?", (app_id,)).fetchone()
        c_dict = json.loads(row[0])
        c_dict["actor_id"] = "ghost_actor_999"
        new_c_str = json.dumps(c_dict, sort_keys=True)
        new_c_dig = canonical_json_hash(c_dict)
        conn.execute(
            "UPDATE objects SET content = ?, content_digest = ? WHERE object_id=?",
            (new_c_str, new_c_dig, app_id)
        )
        conn.commit()

    audit = audit_project_integrity(store)
    assert audit["status"] == IntegrityStatus.FAIL.value
    rules = [f["rule"] for f in audit["findings"]]
    assert "APPROVAL_ACTOR_UNKNOWN" in rules


def test_tampered_event_log_hash_chain_fails_audit(populated_project):
    store = populated_project["store"]

    # Tamper with payload of event sequence 2 directly in SQLite
    with sqlite3.connect(store.paths.db) as conn:
        conn.execute("DROP TRIGGER IF EXISTS events_no_update")
        conn.execute(
            "UPDATE events SET payload = json_set(payload, '$.tampered', 1) WHERE seq = 2"
        )
        conn.commit()

    audit = audit_project_integrity(store)
    assert audit["status"] == IntegrityStatus.FAIL.value
    rules = [f["rule"] for f in audit["findings"]]
    assert any("EVENT" in r for r in rules)


def test_tampered_actor_registry_fails_audit(populated_project):
    store = populated_project["store"]

    # Insert an actor directly into actors table without an event
    with sqlite3.connect(store.paths.db) as conn:
        conn.execute(
            "INSERT INTO actors (actor_id, display_name, roles, token_sha256, created_at) "
            "VALUES ('rogue_actor', 'Rogue', '[\"owner\"]', 'hash123', '2026-01-01T00:00:00Z')"
        )
        conn.commit()

    audit = audit_project_integrity(store)
    assert audit["status"] == IntegrityStatus.FAIL.value
    rules = [f["rule"] for f in audit["findings"]]
    assert any("ACTOR" in r for r in rules)
