"""Tests for Snapshot Export, Verification, and Proposal Gateway."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from research_harness import PROTOCOL_VERSION
from research_harness.core.auth import authenticate
from research_harness.core.model import Decision
from research_harness.proposals.gateway import import_proposal, static_check
from research_harness.protocols.snapshot import export_snapshot, verify_snapshot_document
from tests.conftest import make_proposal


def test_snapshot_export_and_verify(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)

    snapshot = export_snapshot(store, actor, scope="full", token=owner_token)
    assert snapshot["snapshot_id"] == "SNAP_00001"
    assert snapshot["project_id"] == store.project_id
    assert snapshot["state_revision"] == 0
    assert "integrity" in snapshot
    assert "body_hmac_sha256" in snapshot["integrity"]

    # Verify snapshot
    raw_snap = json.dumps(snapshot)
    ver = verify_snapshot_document(store, raw_snap)
    assert ver["status"] == "PASS"
    assert not ver["findings"]

    # Verify tampered snapshot fails
    tampered = copy.deepcopy(snapshot)
    tampered["notice"] = "Tampered message in transit"
    ver_tampered = verify_snapshot_document(store, json.dumps(tampered))
    assert ver_tampered["status"] == "FAIL"
    assert any("MISMATCH" in f["rule"] or "INVALID" in f["rule"] for f in ver_tampered["findings"])


def test_proposal_valid_import_and_receipt(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap = export_snapshot(store, actor, scope="full", token=owner_token)

    # Propose Research Question
    ops = [
        {
            "operation": "propose_object",
            "proposed_id": "RQ_0001",
            "object_type": "ResearchQuestion",
            "content": {
                "label": "Impact of LLM reasoning on code architecture",
                "text": "How do reasoning models improve research harness architectures?",
                "sensitivity": "public",
            },
            "relations": [],
        }
    ]
    prop = make_proposal(store.project_id, snap, "PROP_00001", ops)
    prop_bytes = json.dumps(prop).encode("utf-8")

    # Static check
    doc = static_check(prop_bytes)
    assert doc["proposal_id"] == "PROP_00001"

    # Gateway import
    receipt = import_proposal(store, prop_bytes, bob_token)
    assert receipt["decision"] == Decision.ACCEPTED.value
    assert receipt["new_state_revision"] == 1
    assert "RQ_0001" in receipt["created_objects"]

    # Check store has object
    with store.read() as conn:
        rq = store.get_object("RQ_0001", conn=conn)
        assert rq is not None
        assert rq["object_type"] == "ResearchQuestion"
        assert rq["content"]["label"] == "Impact of LLM reasoning on code architecture"


def test_proposal_idempotency_duplicate(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap = export_snapshot(store, actor, scope="full", token=owner_token)

    ops = [
        {
            "operation": "propose_object",
            "proposed_id": "THEORY_0001",
            "object_type": "Theory",
            "content": {"label": "Information Dynamics Theory", "text": "Formulated theory"},
            "relations": [],
        }
    ]
    prop = make_proposal(store.project_id, snap, "PROP_00002", ops)
    prop_bytes = json.dumps(prop).encode("utf-8")

    rec1 = import_proposal(store, prop_bytes, bob_token)
    assert rec1["decision"] == Decision.ACCEPTED.value

    # Re-submit identical proposal
    rec2 = import_proposal(store, prop_bytes, bob_token)
    assert rec2["decision"] == Decision.DUPLICATE.value
    assert rec2["original_decision"] == Decision.ACCEPTED.value


def test_proposal_id_reuse_with_different_content_rejected(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap = export_snapshot(store, actor, scope="full", token=owner_token)

    ops1 = [
        {
            "operation": "propose_object",
            "proposed_id": "MECH_0001",
            "object_type": "Mechanism",
            "content": {"label": "Mechanism A", "text": "Description A"},
            "relations": [],
        }
    ]
    prop1 = make_proposal(store.project_id, snap, "PROP_REUSE_01", ops1)
    rec1 = import_proposal(store, json.dumps(prop1), bob_token)
    assert rec1["decision"] == Decision.ACCEPTED.value

    # Re-use PROP_REUSE_01 with different operation
    ops2 = [
        {
            "operation": "propose_object",
            "proposed_id": "MECH_0002",
            "object_type": "Mechanism",
            "content": {"label": "Mechanism B", "text": "Description B"},
            "relations": [],
        }
    ]
    prop2 = make_proposal(store.project_id, snap, "PROP_REUSE_01", ops2)
    rec2 = import_proposal(store, json.dumps(prop2), bob_token)
    assert rec2["decision"] == Decision.CONFLICT.value
    assert rec2["rule"] == "PROPOSAL_ID_REUSED_WITH_DIFFERENT_CONTENT"


def test_read_set_conflict_on_concurrent_modification(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap0 = export_snapshot(store, actor, scope="full", token=owner_token)

    # Step 1: Create Object X
    op_create = [
        {
            "operation": "propose_object",
            "proposed_id": "PROXY_0001",
            "object_type": "Proxy",
            "content": {"label": "Code Complexity Proxy", "definition": "AST depth", "unit": "nodes"},
            "relations": [],
        }
    ]
    prop_create = make_proposal(store.project_id, snap0, "PROP_C1", op_create)
    rec = import_proposal(store, json.dumps(prop_create), bob_token)
    assert rec["decision"] == Decision.ACCEPTED.value

    # Step 2: Export snap1 containing PROXY_0001 rev 1
    snap1 = export_snapshot(store, actor, scope="full", token=owner_token)
    assert snap1["object_revisions"]["PROXY_0001"] == 1

    # Step 3: Branch A revises PROXY_0001 to rev 2
    op_revA = [
        {
            "operation": "revise_object",
            "object_id": "PROXY_0001",
            "base_revision": 1,
            "content": {"label": "Code Complexity Proxy", "definition": "Cyclomatic complexity", "unit": "branches"},
            "relations": [],
        }
    ]
    prop_revA = make_proposal(store.project_id, snap1, "PROP_REVA", op_revA, read_set={"PROXY_0001": 1})
    recA = import_proposal(store, json.dumps(prop_revA), bob_token)
    assert recA["decision"] == Decision.ACCEPTED.value

    # Step 4: Branch B also tries to revise PROXY_0001 based on snap1 (where rev was 1)
    op_revB = [
        {
            "operation": "revise_object",
            "object_id": "PROXY_0001",
            "base_revision": 1,
            "content": {"label": "Code Complexity Proxy", "definition": "Halstead length", "unit": "tokens"},
            "relations": [],
        }
    ]
    prop_revB = make_proposal(store.project_id, snap1, "PROP_REVB", op_revB, read_set={"PROXY_0001": 1})
    recB = import_proposal(store, json.dumps(prop_revB), bob_token)
    assert recB["decision"] == Decision.CONFLICT.value
    assert recB["rule"] == "READ_SET_CONFLICT"
    assert recB["conflicts"][0]["object_id"] == "PROXY_0001"
    assert recB["conflicts"][0]["expected_revision"] == 1
    assert recB["conflicts"][0]["actual_revision"] == 2


def test_invalid_reference_rejected(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap = export_snapshot(store, actor, scope="full", token=owner_token)

    # Propose claim pointing to non-existent target
    ops = [
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_0001",
            "claim_tier": "causal",
            "text": "Reasoning models increase test coverage.",
            "depends_on": ["NON_EXISTENT_RESULT_001"],
            "relations": [],
        }
    ]
    prop = make_proposal(store.project_id, snap, "PROP_BAD_REF", ops)
    rec = import_proposal(store, json.dumps(prop), bob_token)
    assert rec["decision"] == Decision.REJECTED.value
    assert rec["rule"] == "REFERENCE_UNKNOWN"


def test_unauthorized_operation_rejected(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap = export_snapshot(store, actor, scope="full", token=owner_token)

    # 1. Unknown operation name
    ops1 = [{"operation": "delete_everything", "object_id": "RQ_0001"}]
    prop1 = make_proposal(store.project_id, snap, "PROP_BAD_OP", ops1)
    rec1 = import_proposal(store, json.dumps(prop1), bob_token)
    assert rec1["decision"] == Decision.UNAUTHORIZED.value
    assert rec1["rule"] == "OPERATION_NOT_ALLOWED"

    # 2. Privileged server-only object type (Approval)
    ops2 = [
        {
            "operation": "propose_object",
            "proposed_id": "APR_FORGED_01",
            "object_type": "Approval",
            "content": {"decision": "approve", "kind": "independent_review"},
        }
    ]
    prop2 = make_proposal(store.project_id, snap, "PROP_FORGE_APR", ops2)
    rec2 = import_proposal(store, json.dumps(prop2), bob_token)
    assert rec2["decision"] == Decision.UNAUTHORIZED.value
    assert rec2["rule"] == "PRIVILEGED_OBJECT_TYPE"


def test_forbidden_fields_rejected(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap = export_snapshot(store, actor, scope="full", token=owner_token)

    # Attempt to inject server-controlled fields (e.g. approved_by, scientific_review_status)
    ops = [
        {
            "operation": "propose_object",
            "proposed_id": "RQ_FORGE_REV",
            "object_type": "ResearchQuestion",
            "approved_by": "hacker_mallory",
            "content": {"label": "Injected Question", "text": "Valid text"},
            "relations": [],
        }
    ]
    prop = make_proposal(store.project_id, snap, "PROP_FORBIDDEN", ops)
    rec = import_proposal(store, json.dumps(prop), bob_token)
    assert rec["decision"] == Decision.UNAUTHORIZED.value
    assert rec["rule"] == "FORBIDDEN_FIELD"


def test_secrets_and_path_traversal_rejected(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap = export_snapshot(store, actor, scope="full", token=owner_token)

    # 1. Path traversal in ID
    ops_traversal = [
        {
            "operation": "propose_object",
            "proposed_id": "../../etc/shadow",
            "object_type": "Dataset",
            "content": {"label": "Shadow dataset", "data_ref": "valid_data_ref"},
        }
    ]
    prop_t = make_proposal(store.project_id, snap, "PROP_TRAVERSAL", ops_traversal)
    rec_t = import_proposal(store, json.dumps(prop_t), bob_token)
    assert rec_t["decision"] in (Decision.INVALID_SCHEMA.value, Decision.REJECTED.value)

    # 2. Secret / token keys in content
    ops_secret = [
        {
            "operation": "propose_object",
            "proposed_id": "DATASET_SECRET",
            "object_type": "Dataset",
            "content": {
                "label": "Leaked credentials dataset",
                "data_ref": "ref_1",
                "attributes": {"api_key": "sk-proj-supersecretkey123456789012345678"},
            },
        }
    ]
    prop_s = make_proposal(store.project_id, snap, "PROP_SECRET", ops_secret)
    rec_s = import_proposal(store, json.dumps(prop_s), bob_token)
    assert rec_s["decision"] == Decision.REJECTED.value
    assert rec_s["rule"] == "SECRET_LIKE_CONTENT"


def test_protocol_version_mismatch(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap = export_snapshot(store, actor, scope="full", token=owner_token)

    ops = [
        {
            "operation": "propose_object",
            "proposed_id": "RQ_MISMATCH",
            "object_type": "ResearchQuestion",
            "content": {"label": "Label", "text": "Text"},
        }
    ]
    prop = make_proposal(store.project_id, snap, "PROP_MISMATCH", ops)
    prop["protocol_version"] = "99.0.0"

    rec = import_proposal(store, json.dumps(prop), bob_token)
    assert rec["decision"] == Decision.REJECTED.value
    assert rec["rule"] == "PROTOCOL_VERSION_UNSUPPORTED"


def test_actor_authentication_and_role_required(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    carol_token = harness_env["actors"]["carol"]["token"]  # only independent_reviewer, not importer

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap = export_snapshot(store, actor, scope="full", token=owner_token)

    ops = [
        {
            "operation": "propose_object",
            "proposed_id": "RQ_AUTH",
            "object_type": "ResearchQuestion",
            "content": {"label": "Label", "text": "Text"},
        }
    ]
    prop = make_proposal(store.project_id, snap, "PROP_AUTH", ops)

    # 1. Invalid token
    rec1 = import_proposal(store, json.dumps(prop), "rhk_fake_invalid_token")
    assert rec1["decision"] == Decision.UNAUTHORIZED.value
    assert rec1["rule"] == "AUTHENTICATION_FAILED"

    # 2. Valid token but lacks importer role
    rec2 = import_proposal(store, json.dumps(prop), carol_token)
    assert rec2["decision"] == Decision.UNAUTHORIZED.value
    assert rec2["rule"] == "ROLE_IMPORTER_REQUIRED"
