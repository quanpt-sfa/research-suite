"""Tests for Review Authority Boundary and Independence Verification."""
from __future__ import annotations

import json
import pytest

from research_harness.core.auth import authenticate
from research_harness.core.model import Decision, ReviewStatus
from research_harness.proposals.gateway import import_proposal
from research_harness.protocols.snapshot import export_snapshot
from research_harness.validation.review import record_review
from tests.conftest import make_proposal


def test_unauthenticated_and_unauthorized_review_rejected(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap0 = export_snapshot(store, actor, scope="full", token=owner_token)

    # Create Claim
    ops = [
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_REV_AUTH_01",
            "claim_tier": "causal",
            "text": "Review authority claim.",
            "depends_on": [],
            "relations": [],
        }
    ]
    prop = make_proposal(store.project_id, snap0, "PROP_REV_AUTH_01", ops)
    import_proposal(store, json.dumps(prop), bob_token)

    # 1. Missing / invalid token
    res1 = record_review(
        store,
        "rhk_invalid_token_12345",
        object_id="CLAIM_REV_AUTH_01",
        revision=1,
        kind="author_approval",
        decision="approve",
    )
    assert res1["decision"] == "UNAUTHORIZED"
    assert res1["rule"] == "AUTHENTICATION_FAILED"

    # 2. Bob (author, importer) lacks role independent_reviewer
    res2 = record_review(
        store,
        bob_token,
        object_id="CLAIM_REV_AUTH_01",
        revision=1,
        kind="independent_review",
        decision="approve",
    )
    assert res2["decision"] == "UNAUTHORIZED"
    assert res2["rule"] == "ROLE_REQUIRED"


def test_independent_review_rejected_when_reviewer_is_contributor(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]

    # Give Carol both author and independent_reviewer roles
    with store.write_tx(token=owner_token, bump=False) as tx:
        tx.conn.execute("UPDATE actors SET roles=? WHERE actor_id='rev_carol'", (json.dumps(["author", "importer", "independent_reviewer"]),))

    carol_token = harness_env["actors"]["carol"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap0 = export_snapshot(store, actor, scope="full", token=owner_token)

    # Carol submits a claim
    ops = [
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_CAROL_SELF",
            "claim_tier": "predictive",
            "text": "Carol's self-authored claim.",
            "depends_on": [],
            "relations": [],
        }
    ]
    prop = make_proposal(store.project_id, snap0, "PROP_CAROL_01", ops)
    import_proposal(store, json.dumps(prop), carol_token)

    # Carol attempts to independently review her own claim
    res = record_review(
        store,
        carol_token,
        object_id="CLAIM_CAROL_SELF",
        revision=1,
        kind="independent_review",
        decision="approve",
    )
    assert res["decision"] == "REFUSED"
    assert res["rule"] == "INDEPENDENCE_POLICY_NOT_MET"
    assert "REVIEWER_IS_CONTRIBUTOR" in res["problems"]


def test_independent_review_rejected_when_sharing_independence_group(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]    # group_alpha
    dave_token = harness_env["actors"]["dave"]["token"]  # independent_reviewer, but also in group_alpha!

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap0 = export_snapshot(store, actor, scope="full", token=owner_token)

    # Bob (group_alpha) submits claim
    ops = [
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_BOB_GRP",
            "claim_tier": "causal",
            "text": "Bob's claim in group alpha.",
            "depends_on": [],
            "relations": [],
        }
    ]
    prop = make_proposal(store.project_id, snap0, "PROP_BOB_GRP", ops)
    import_proposal(store, json.dumps(prop), bob_token)

    # Dave (group_alpha) attempts independent review of Bob's claim
    res = record_review(
        store,
        dave_token,
        object_id="CLAIM_BOB_GRP",
        revision=1,
        kind="independent_review",
        decision="approve",
    )
    assert res["decision"] == "REFUSED"
    assert res["rule"] == "INDEPENDENCE_POLICY_NOT_MET"
    assert "REVIEWER_SHARES_GROUP_WITH_CONTRIBUTOR" in res["problems"]


def test_independent_review_accepted_for_bona_fide_independent_reviewer(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]      # group_alpha
    carol_token = harness_env["actors"]["carol"]["token"]  # group_beta, independent_reviewer

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap0 = export_snapshot(store, actor, scope="full", token=owner_token)

    ops = [
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_PEER_01",
            "claim_tier": "causal",
            "text": "Peer reviewed claim.",
            "depends_on": [],
            "relations": [],
        }
    ]
    prop = make_proposal(store.project_id, snap0, "PROP_PEER_01", ops)
    import_proposal(store, json.dumps(prop), bob_token)

    # Carol (group_beta) reviews Bob's claim (group_alpha)
    res = record_review(
        store,
        carol_token,
        object_id="CLAIM_PEER_01",
        revision=1,
        kind="independent_review",
        decision="approve",
        rationale="Independent peer review confirms statistical validity.",
    )
    assert res["decision"] == "RECORDED"
    assert res["new_review_status"] == ReviewStatus.INDEPENDENTLY_REVIEWED.value
    assert res["approval_id"].startswith("APR_")

    with store.read() as conn:
        head_row = conn.execute("SELECT scientific_review_status FROM object_status WHERE object_id='CLAIM_PEER_01'").fetchone()
        assert head_row[0] == ReviewStatus.INDEPENDENTLY_REVIEWED.value


def test_review_refused_on_open_invalidations_without_acknowledgment(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]
    carol_token = harness_env["actors"]["carol"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap0 = export_snapshot(store, actor, scope="full", token=owner_token)

    # Setup Proxy and Claim
    ops1 = [
        {
            "operation": "propose_object",
            "proposed_id": "PROXY_INV_01",
            "object_type": "Proxy",
            "content": {"label": "Proxy 1", "definition": "Def 1", "unit": "units"},
            "relations": [],
        },
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_TO_INVALIDATE",
            "claim_tier": "causal",
            "text": "Claim to invalidate",
            "depends_on": ["PROXY_INV_01"],
            "relations": [{"relation": "depends_on", "target_id": "PROXY_INV_01"}],
        },
    ]
    prop1 = make_proposal(store.project_id, snap0, "PROP_PI_01", ops1)
    import_proposal(store, json.dumps(prop1), bob_token)

    # Initial Author Approval
    record_review(store, owner_token, object_id="CLAIM_TO_INVALIDATE", revision=1, kind="author_approval", decision="approve")

    snap1 = export_snapshot(store, actor, scope="full", token=owner_token)

    # Revise Proxy -> triggers invalidation of CLAIM_TO_INVALIDATE
    ops2 = [
        {
            "operation": "revise_object",
            "object_id": "PROXY_INV_01",
            "base_revision": 1,
            "content": {"label": "Proxy 1 revised", "definition": "Def 1 revised", "unit": "units"},
            "relations": [],
        }
    ]
    prop2 = make_proposal(store.project_id, snap1, "PROP_PI_02", ops2, read_set={"PROXY_INV_01": 1})
    rec2 = import_proposal(store, json.dumps(prop2), bob_token)
    assert rec2["decision"] == Decision.ACCEPTED.value

    # Attempt to re-approve claim without acknowledging invalidation -> REFUSED
    res_fail = record_review(
        store,
        carol_token,
        object_id="CLAIM_TO_INVALIDATE",
        revision=1,
        kind="independent_review",
        decision="approve",
        acknowledge_invalidations=False,
    )
    assert res_fail["decision"] == "REFUSED"
    assert res_fail["rule"] == "OPEN_INVALIDATIONS_UNACKNOWLEDGED"

    # Now approve with explicit invalidation acknowledgment
    res_pass = record_review(
        store,
        carol_token,
        object_id="CLAIM_TO_INVALIDATE",
        revision=1,
        kind="independent_review",
        decision="approve",
        rationale="Reviewed updated proxy definition; claim logic still holds.",
        acknowledge_invalidations=True,
    )
    assert res_pass["decision"] == "RECORDED"
    assert res_pass["new_review_status"] == ReviewStatus.INDEPENDENTLY_REVIEWED.value
    assert len(res_pass["resolved_invalidations"]) == 1

    # Check database: invalidation status is RESOLVED
    with store.read() as conn:
        inv = conn.execute("SELECT status FROM invalidations WHERE object_id='CLAIM_TO_INVALIDATE'").fetchone()
        assert inv["status"] == "RESOLVED"
