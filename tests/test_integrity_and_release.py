"""Tests for Database Cryptographic Integrity, CAS Artifacts, and Release Gates."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_harness.core.auth import authenticate
from research_harness.core.model import Decision, IntegrityStatus, ReleaseEligibility, ReviewStatus, WorkflowStatus
from research_harness.proposals.gateway import import_proposal
from research_harness.protocols.snapshot import export_snapshot
from research_harness.validation.integrity import audit_project_integrity, release_check, verify_database_integrity
from research_harness.validation.review import record_review, record_adjudication
from tests.conftest import make_proposal


def test_empty_project_release_check(harness_env):
    store = harness_env["store"]

    res = release_check(store)
    # The prompt explicitly mandates:
    # An empty project must yield: Integrity = PASS, Completeness = INCOMPLETE,
    # Scientific Review = UNASSESSED, Release Eligibility = BLOCKED.
    assert res["integrity_status"] == IntegrityStatus.PASS.value
    assert res["workflow_status"] == WorkflowStatus.INCOMPLETE.value
    assert res["scientific_review_status"] == ReviewStatus.UNASSESSED.value
    assert res["release_eligibility"] == ReleaseEligibility.BLOCKED.value
    assert any("missing core scientific objects" in r for r in res["block_reasons"])


def test_complete_and_reviewed_project_release_check(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]
    carol_token = harness_env["actors"]["carol"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap0 = export_snapshot(store, actor, scope="full", token=owner_token)

    # Populate core scientific objects: ResearchQuestion, Proxy, Result, Claim
    ops = [
        {
            "operation": "propose_object",
            "proposed_id": "RQ_COMPLETE_01",
            "object_type": "ResearchQuestion",
            "content": {"label": "RQ Core", "text": "Does refactoring improve codebase stability?"},
            "relations": [],
        },
        {
            "operation": "propose_object",
            "proposed_id": "PROXY_COMPLETE_01",
            "object_type": "Proxy",
            "content": {"label": "Failure Rate Proxy", "definition": "Test suite failure count", "unit": "count"},
            "relations": [],
        },
        {
            "operation": "propose_object",
            "proposed_id": "RESULT_COMPLETE_01",
            "object_type": "Result",
            "content": {"label": "Empirical Test Result", "summary": "Failures dropped from 15% to 0%"},
            "relations": [],
        },
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_COMPLETE_01",
            "claim_tier": "causal",
            "text": "Refactoring directly eliminated regressions.",
            "depends_on": ["RESULT_COMPLETE_01"],
            "relations": [{"relation": "depends_on", "target_id": "RESULT_COMPLETE_01"}],
        },
    ]
    prop = make_proposal(store.project_id, snap0, "PROP_COMPLETE", ops)
    import_proposal(store, json.dumps(prop), bob_token)

    # Review Claim with Independent Reviewer Carol
    record_review(
        store,
        carol_token,
        object_id="CLAIM_COMPLETE_01",
        revision=1,
        kind="independent_review",
        decision="approve",
        rationale="Empirical results and causality verified independently.",
    )
    adj_res = record_adjudication(
        store,
        carol_token,
        subject_id="CLAIM_COMPLETE_01",
        subject_revision=1,
        assessment_kind="semantic_entailment",
        verdict="SUPPORTED",
        rationale="Empirical results and causality verified independently.",
        cited_evidence=[{"object_id": "RESULT_COMPLETE_01", "revision": 1}],
        is_independent=True,
    )

    # Release check should now be ELIGIBLE
    res = release_check(store)
    assert res["integrity_status"] == IntegrityStatus.PASS.value
    assert res["workflow_status"] == WorkflowStatus.COMPLETE.value
    assert res["scientific_review_status"] == ReviewStatus.INDEPENDENTLY_REVIEWED.value
    assert res["release_eligibility"] == ReleaseEligibility.ELIGIBLE.value
    assert not res["block_reasons"]


def test_tampered_database_fails_integrity_audit(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap0 = export_snapshot(store, actor, scope="full", token=owner_token)

    ops = [
        {
            "operation": "propose_object",
            "proposed_id": "RQ_INTEG_01",
            "object_type": "ResearchQuestion",
            "content": {"label": "Original Question", "text": "Original text"},
            "relations": [],
        }
    ]
    prop = make_proposal(store.project_id, snap0, "PROP_INT_01", ops)
    import_proposal(store, json.dumps(prop), bob_token)

    # Verify audit passes before tampering
    aud_before = audit_project_integrity(store)
    assert aud_before["status"] == IntegrityStatus.PASS.value

    # Direct SQLite tamper bypassing transactions and digest recalculation
    conn = store.connect()
    try:
        conn.execute("UPDATE object_status SET scientific_review_status='AUTHOR_APPROVED' WHERE object_id='RQ_INTEG_01'")
        conn.commit()
    finally:
        conn.close()

    # Audit must detect tamper
    aud_after = audit_project_integrity(store)
    assert aud_after["status"] == IntegrityStatus.FAIL.value
    assert any(f["rule"] == "STATE_DIGEST_MISMATCH" for f in aud_after["findings"])

    # Release check must also FAIL and be BLOCKED
    res = release_check(store)
    assert res["integrity_status"] == IntegrityStatus.FAIL.value
    assert res["release_eligibility"] == ReleaseEligibility.BLOCKED.value
    assert "PROJECT_INTEGRITY_FAILED" in res["block_reasons"]


def test_tampered_artifact_blob_fails_audit(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap0 = export_snapshot(store, actor, scope="full", token=owner_token)

    ops = [
        {
            "operation": "propose_object",
            "proposed_id": "RQ_BLOB_01",
            "object_type": "ResearchQuestion",
            "content": {"label": "Blob Test", "text": "Valid text"},
            "relations": [],
        }
    ]
    prop = make_proposal(store.project_id, snap0, "PROP_BLOB_01", ops)
    rec = import_proposal(store, json.dumps(prop), bob_token)
    assert rec["decision"] == Decision.ACCEPTED.value

    # Find the proposal artifact blob in the CAS store
    blobs = list(store.artifacts.blobs())
    assert len(blobs) > 0
    first_digest = blobs[0]
    blob_path = store.artifacts.path_for(first_digest)
    assert blob_path.is_file()

    # Tamper with the artifact blob file
    blob_path.write_bytes(b"Corrupted bytes replacing original proposal payload")

    # Verify audit catches corrupted blob
    findings = verify_database_integrity(store)
    assert any(f["rule"] == "ARTIFACT_BLOB_CORRUPT" and f["digest"] == first_digest for f in findings)

    aud = audit_project_integrity(store)
    assert aud["status"] == IntegrityStatus.FAIL.value
