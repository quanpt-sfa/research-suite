"""Tests for Dependency Graph, Cascading Invalidation, and Cycle Tolerance."""
from __future__ import annotations

import json
import pytest

from research_harness.core.auth import authenticate
from research_harness.core.model import Decision, ReviewStatus
from research_harness.graph.dependency import stale_pinned_edges, traverse
from research_harness.proposals.gateway import import_proposal
from research_harness.protocols.snapshot import export_snapshot
from research_harness.validation.review import record_review
from tests.conftest import make_proposal


def test_cascading_invalidation_on_proxy_revision(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap0 = export_snapshot(store, actor, scope="full", token=owner_token)

    # 1. Propose Construct and Proxy
    ops1 = [
        {
            "operation": "propose_object",
            "proposed_id": "CONST_001",
            "object_type": "Construct",
            "content": {"label": "Code Complexity", "definition": "Cognitive burden of source code"},
            "relations": [],
        },
        {
            "operation": "propose_object",
            "proposed_id": "PROXY_001",
            "object_type": "Proxy",
            "content": {"label": "AST Depth Proxy", "definition": "Maximum depth of AST", "unit": "levels"},
            "relations": [{"relation": "operationalizes", "target_id": "CONST_001"}],
        },
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_001",
            "claim_tier": "causal",
            "text": "AST depth predicts defect density.",
            "depends_on": ["PROXY_001"],
            "relations": [{"relation": "depends_on", "target_id": "PROXY_001"}],
        },
    ]
    prop1 = make_proposal(store.project_id, snap0, "PROP_INVAL_01", ops1)
    rec1 = import_proposal(store, json.dumps(prop1), bob_token)
    assert rec1["decision"] == Decision.ACCEPTED.value

    # Approve the claim initially
    rev_rec = record_review(
        store,
        owner_token,
        object_id="CLAIM_001",
        revision=1,
        kind="author_approval",
        decision="approve",
        rationale="Initial author sign-off",
    )
    assert rev_rec["decision"] == "RECORDED"
    assert rev_rec["new_review_status"] == ReviewStatus.AUTHOR_APPROVED.value

    # Export snapshot with current state
    snap1 = export_snapshot(store, actor, scope="full", token=owner_token)

    # 2. Revise Proxy to revision 2
    ops2 = [
        {
            "operation": "revise_object",
            "object_id": "PROXY_001",
            "base_revision": 1,
            "content": {"label": "AST Depth Proxy", "definition": "Refined AST depth with token weighting", "unit": "weighted_levels"},
            "relations": [{"relation": "operationalizes", "target_id": "CONST_001"}],
        }
    ]
    prop2 = make_proposal(store.project_id, snap1, "PROP_INVAL_02", ops2, read_set={"PROXY_001": 1, "CONST_001": 1})
    rec2 = import_proposal(store, json.dumps(prop2), bob_token)
    assert rec2["decision"] == Decision.ACCEPTED.value
    assert len(rec2["invalidated_objects"]) == 1
    assert rec2["invalidated_objects"][0]["object_id"] == "CLAIM_001"
    assert rec2["invalidated_objects"][0]["revision"] == 1

    # Check status of CLAIM_001 in database
    with store.read() as conn:
        status_row = conn.execute(
            "SELECT scientific_review_status, prior_review_status FROM object_status WHERE object_id='CLAIM_001' AND revision=1"
        ).fetchone()
        assert status_row["scientific_review_status"] == ReviewStatus.NEEDS_REVIEW.value
        assert status_row["prior_review_status"] == ReviewStatus.AUTHOR_APPROVED.value

        inv_row = conn.execute(
            "SELECT cause_object_id, cause_to_revision, status FROM invalidations WHERE object_id='CLAIM_001' AND status='OPEN'"
        ).fetchone()
        assert inv_row is not None
        assert inv_row["cause_object_id"] == "PROXY_001"
        assert inv_row["cause_to_revision"] == 2


def test_cascading_invalidation_on_result_revision(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap0 = export_snapshot(store, actor, scope="full", token=owner_token)

    # Create Result and dependent Claim
    ops1 = [
        {
            "operation": "propose_object",
            "proposed_id": "RESULT_001",
            "object_type": "Result",
            "content": {"label": "Benchmark Result 1", "summary": "Accuracy was 82%"},
            "relations": [],
        },
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_RES_001",
            "claim_tier": "predictive",
            "text": "Model accuracy exceeds 80%.",
            "depends_on": ["RESULT_001"],
            "relations": [{"relation": "depends_on", "target_id": "RESULT_001"}],
        },
    ]
    prop1 = make_proposal(store.project_id, snap0, "PROP_RES_01", ops1)
    rec1 = import_proposal(store, json.dumps(prop1), bob_token)
    assert rec1["decision"] == Decision.ACCEPTED.value

    # Approve Claim
    record_review(store, owner_token, object_id="CLAIM_RES_001", revision=1, kind="author_approval", decision="approve")

    snap1 = export_snapshot(store, actor, scope="full", token=owner_token)

    # Revise Result
    ops2 = [
        {
            "operation": "revise_object",
            "object_id": "RESULT_001",
            "base_revision": 1,
            "content": {"label": "Benchmark Result 1", "summary": "Corrected accuracy is 74% due to metric bug"},
            "relations": [],
        }
    ]
    prop2 = make_proposal(store.project_id, snap1, "PROP_RES_02", ops2, read_set={"RESULT_001": 1})
    rec2 = import_proposal(store, json.dumps(prop2), bob_token)
    assert rec2["decision"] == Decision.ACCEPTED.value

    with store.read() as conn:
        c_status = conn.execute(
            "SELECT scientific_review_status FROM object_status WHERE object_id='CLAIM_RES_001' AND revision=1"
        ).fetchone()[0]
        assert c_status == ReviewStatus.NEEDS_REVIEW.value


def test_cycle_tolerance_in_dependency_traversal(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]

    # In research workflows, object-level cycles (Theory -> Claim -> Theory v2) can occur.
    # The revision graph is DAG, but let's test traversal BFS with intentional cycles.
    with store.write_tx(token=owner_token) as tx:
        # Create T1 rev 1
        tx.add_revision(
            object_id="THEORY_CYCLE",
            object_type="Theory",
            content={"label": "Cycle Theory", "text": "Base theory"},
            origin={"kind": "PROPOSAL_IMPORTED"},
            edges=[],
        )
        # Create C1 rev 1 pointing to T1 rev 1
        tx.add_revision(
            object_id="CLAIM_CYCLE",
            object_type="Claim",
            content={"label": "Cycle Claim", "text": "Derived claim", "claim_tier": "causal"},
            origin={"kind": "PROPOSAL_IMPORTED"},
            edges=[("derived_from", "THEORY_CYCLE", 1, {})],
        )
        # Create T1 rev 2 pointing back to C1 rev 1 (forming object cycle)
        tx.add_revision(
            object_id="THEORY_CYCLE",
            object_type="Theory",
            content={"label": "Cycle Theory v2", "text": "Extended theory based on claim"},
            origin={"kind": "PROPOSAL_IMPORTED"},
            edges=[("supports", "CLAIM_CYCLE", 1, {})],
        )

    # Test BFS traverse terminates without infinite loop
    with store.read() as conn:
        affected = traverse(conn, [("THEORY_CYCLE", 1)])
        # BFS terminates cleanly and returns affected dependents
        assert isinstance(affected, list)


def test_unrelated_object_stability(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap0 = export_snapshot(store, actor, scope="full", token=owner_token)

    # Two independent branches: Branch A (PA -> CA) and Branch B (PB -> CB)
    ops = [
        {
            "operation": "propose_object",
            "proposed_id": "PROXY_A",
            "object_type": "Proxy",
            "content": {"label": "Proxy A", "definition": "A def", "unit": "units"},
            "relations": [],
        },
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_A",
            "claim_tier": "causal",
            "text": "Claim A",
            "depends_on": ["PROXY_A"],
            "relations": [{"relation": "depends_on", "target_id": "PROXY_A"}],
        },
        {
            "operation": "propose_object",
            "proposed_id": "PROXY_B",
            "object_type": "Proxy",
            "content": {"label": "Proxy B", "definition": "B def", "unit": "units"},
            "relations": [],
        },
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_B",
            "claim_tier": "causal",
            "text": "Claim B",
            "depends_on": ["PROXY_B"],
            "relations": [{"relation": "depends_on", "target_id": "PROXY_B"}],
        },
    ]
    prop = make_proposal(store.project_id, snap0, "PROP_BRANCHES", ops)
    import_proposal(store, json.dumps(prop), bob_token)

    # Approve both claims
    record_review(store, owner_token, object_id="CLAIM_A", revision=1, kind="author_approval", decision="approve")
    record_review(store, owner_token, object_id="CLAIM_B", revision=1, kind="author_approval", decision="approve")

    snap1 = export_snapshot(store, actor, scope="full", token=owner_token)

    # Revise PROXY_A only
    ops_revA = [
        {
            "operation": "revise_object",
            "object_id": "PROXY_A",
            "base_revision": 1,
            "content": {"label": "Proxy A revised", "definition": "A def updated", "unit": "units"},
            "relations": [],
        }
    ]
    prop_revA = make_proposal(store.project_id, snap1, "PROP_REV_A", ops_revA, read_set={"PROXY_A": 1})
    rec = import_proposal(store, json.dumps(prop_revA), bob_token)
    assert rec["decision"] == Decision.ACCEPTED.value

    with store.read() as conn:
        status_A = conn.execute(
            "SELECT scientific_review_status FROM object_status WHERE object_id='CLAIM_A' AND revision=1"
        ).fetchone()[0]
        status_B = conn.execute(
            "SELECT scientific_review_status FROM object_status WHERE object_id='CLAIM_B' AND revision=1"
        ).fetchone()[0]

        # Branch A is invalidated
        assert status_A == ReviewStatus.NEEDS_REVIEW.value
        # Branch B is completely untouched and remains AUTHOR_APPROVED!
        assert status_B == ReviewStatus.AUTHOR_APPROVED.value


def test_stale_pinned_edge_detection(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]

    with store.write_tx(token=owner_token) as tx:
        # Create Target Object rev 1
        tx.add_revision(
            object_id="TARGET_OBJ",
            object_type="Proxy",
            content={"label": "Target rev 1", "definition": "d", "unit": "u"},
            origin={"kind": "PROPOSAL_IMPORTED"},
            edges=[],
        )
        # Create Source Object rev 1 pinned to Target Object rev 1
        tx.add_revision(
            object_id="SOURCE_OBJ",
            object_type="Claim",
            content={"label": "Source Claim", "text": "Claim text", "claim_tier": "causal"},
            origin={"kind": "PROPOSAL_IMPORTED"},
            edges=[("depends_on", "TARGET_OBJ", 1, {})],
        )
        # Now bump Target Object to rev 2
        tx.add_revision(
            object_id="TARGET_OBJ",
            object_type="Proxy",
            content={"label": "Target rev 2", "definition": "d2", "unit": "u"},
            origin={"kind": "PROPOSAL_IMPORTED"},
            edges=[],
        )

    with store.read() as conn:
        pins = stale_pinned_edges(conn, "SOURCE_OBJ", 1)
        assert len(pins) == 1
        assert pins[0]["pinned"] == "TARGET_OBJ@1"
        assert pins[0]["current_head"] == 2
