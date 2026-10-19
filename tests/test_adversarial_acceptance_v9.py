"""CODEX TASK — Research Harness 9.0: Final Adversarial Acceptance Remediation.

Comprehensive test suite verifying all mandatory acceptance gates:
- Gate A (P0): Snapshot identity impersonation impossible through supported API
- Gate B (P0): Evidence-bound adjudication recorded and integrity-verified
- Gate C (P0): Default causal policy enforces Result AND authorized adjudication
- Gate D (P1): Historical adjudication distinct from current adjudication
- Gate E (P1): Seven claim tiers have consistent positive and negative tests
- Gate F: Prior migration fixes remain valid
- Gate G: Six end-to-end acceptance scenarios pass
"""
from __future__ import annotations

import copy
import json
import pytest
from pathlib import Path

from research_harness.core.auth import Actor, authenticate
from research_harness.core.errors import AuthorizationError, HarnessError
from research_harness.core.model import Decision, IntegrityStatus, ReleaseEligibility, ReviewStatus, WorkflowStatus
from research_harness.legacy.adapter import migrate_legacy_project
from research_harness.proposals.gateway import import_proposal
from research_harness.protocols.snapshot import export_snapshot, verify_snapshot_document
from research_harness.state.actors import disable_actor, register_actor, update_actor
from research_harness.state.store import StateStore
from research_harness.validation.integrity import audit_project_integrity, release_check
from research_harness.validation.review import record_adjudication, record_review
from tests.conftest import make_proposal


# ==============================================================================================
# Helper Fixtures & Setup
# ==============================================================================================

def _setup_adversarial_env(tmp_path: Path):
    store, owner_token = StateStore.create(tmp_path, "PRJ_ADV_01", owner_id="owner_alice")
    update_actor(store, owner_token, actor_id="owner_alice", independence_group="group_owner")

    bob_res = register_actor(
        store, owner_token, actor_id="author_bob", display_name="Author Bob",
        roles=["author", "importer"], independence_group="group_alpha"
    )
    bob_token = bob_res["token"]

    carol_res = register_actor(
        store, owner_token, actor_id="rev_carol", display_name="Reviewer Carol",
        roles=["independent_reviewer"], independence_group="group_beta"
    )
    carol_token = carol_res["token"]

    return {
        "store": store,
        "owner_token": owner_token,
        "bob_token": bob_token,
        "carol_token": carol_token,
    }


# ==============================================================================================
# PART A — P0: Close snapshot-export identity impersonation (Gate A)
# ==============================================================================================

def test_a_neg_1_existing_owner_impersonation(tmp_path: Path):
    """A-NEG-1: Attempt snapshot export using existing owner Actor without token."""
    env = _setup_adversarial_env(tmp_path)
    store = env["store"]

    # Attempt impersonation using real owner identity with token=None
    fake_owner = Actor("owner_alice", frozenset({"owner"}))
    with pytest.raises(AuthorizationError) as exc_info:
        export_snapshot(store, actor=fake_owner, token=None)
    assert exc_info.value.code == "AUTHENTICATION_FAILED"

    # Verify no snapshot created, no success audit record, no mutation to scientific state
    with store.read() as conn:
        snap_cnt = conn.execute("SELECT count(*) FROM audit_log WHERE action='snapshot_export' AND decision='RECORDED'").fetchone()[0]
        assert snap_cnt == 0
        denied_cnt = conn.execute("SELECT count(*) FROM audit_log WHERE action='snapshot_export' AND decision='DENIED'").fetchone()[0]
        assert denied_cnt >= 1
        objs_cnt = conn.execute("SELECT count(*) FROM objects").fetchone()[0]
        assert objs_cnt == 0


def test_a_neg_2_wrong_actor_with_valid_token(tmp_path: Path):
    """A-NEG-2: Supplying valid author token while passing owner Actor object."""
    env = _setup_adversarial_env(tmp_path)
    store = env["store"]
    bob_token = env["bob_token"]

    fake_owner = Actor("owner_alice", frozenset({"owner"}))
    with pytest.raises(AuthorizationError) as exc_info:
        export_snapshot(store, actor=fake_owner, token=bob_token)
    assert exc_info.value.code == "IDENTITY_MISMATCH"


def test_a_neg_3_disabled_token(tmp_path: Path):
    """A-NEG-3: Disable an existing actor, then attempt snapshot export with old token."""
    env = _setup_adversarial_env(tmp_path)
    store = env["store"]
    owner_token = env["owner_token"]
    bob_token = env["bob_token"]

    # Disable Bob
    disable_actor(store, owner_token, actor_id="author_bob")

    with pytest.raises(AuthorizationError) as exc_info:
        export_snapshot(store, token=bob_token)
    assert exc_info.value.code == "AUTHENTICATION_FAILED"


def test_a_pos_1_authenticated_owner(tmp_path: Path):
    """A-POS-1: Export snapshot with valid owner token."""
    env = _setup_adversarial_env(tmp_path)
    store = env["store"]
    owner_token = env["owner_token"]

    snap = export_snapshot(store, token=owner_token, scope="full")
    assert snap["snapshot_id"] == "SNAP_00001"
    assert snap["project_id"] == "PRJ_ADV_01"
    assert snap["exported_by"] == "owner_alice"
    assert "integrity" in snap
    assert "body_hmac_sha256" in snap["integrity"]

    # Verify snapshot with verifier
    ver = verify_snapshot_document(store, json.dumps(snap))
    assert ver["status"] == "PASS"


# ==============================================================================================
# PART B — P0: Repair adjudication evidence-link execution (Gate B)
# ==============================================================================================

def test_b_neg_1_unknown_cited_evidence(tmp_path: Path):
    """B-NEG-1: Attempt to record adjudication referring to non-existent evidence."""
    env = _setup_adversarial_env(tmp_path)
    store = env["store"]
    bob_token = env["bob_token"]
    carol_token = env["carol_token"]
    owner_token = env["owner_token"]

    snap = export_snapshot(store, token=owner_token)
    ops = [
        {"operation": "propose_object", "proposed_id": "RQ_01", "object_type": "ResearchQuestion", "content": {"label": "RQ", "text": "Question?"}, "relations": []},
        {"operation": "propose_claim", "proposed_id": "CLAIM_01", "claim_tier": "causal", "text": "Claim text", "depends_on": [], "relations": []},
    ]
    prop = make_proposal(store.project_id, snap, "PROP_B1", ops)
    import_proposal(store, json.dumps(prop), bob_token)

    # Reference nonexistent evidence RES_999
    res = record_adjudication(
        store,
        carol_token,
        subject_id="CLAIM_01",
        subject_revision=1,
        assessment_kind="semantic_entailment",
        verdict="SUPPORTED",
        rationale="Evidence supports claim",
        cited_evidence=[{"object_id": "RES_999", "revision": 1}],
        is_independent=True,
    )
    assert res["decision"] == "REFUSED"
    assert res["rule"] == "CITED_EVIDENCE_UNKNOWN"

    with store.read() as conn:
        adjs = conn.execute("SELECT count(*) FROM object_heads WHERE object_type='Adjudication'").fetchone()[0]
        assert adjs == 0
        edges = conn.execute("SELECT count(*) FROM edges WHERE source_id LIKE 'ADJ_%'").fetchone()[0]
        assert edges == 0


def test_b_neg_2_invalid_evidence_revision(tmp_path: Path):
    """B-NEG-2: Refer to an invalid or stale evidence revision."""
    env = _setup_adversarial_env(tmp_path)
    store = env["store"]
    bob_token = env["bob_token"]
    carol_token = env["carol_token"]
    owner_token = env["owner_token"]

    snap0 = export_snapshot(store, token=owner_token)
    ops1 = [
        {"operation": "propose_object", "proposed_id": "RQ_B2", "object_type": "ResearchQuestion", "content": {"label": "RQ", "text": "Question?"}, "relations": []},
        {"operation": "propose_object", "proposed_id": "RES_B2", "object_type": "Result", "content": {"label": "Res 1", "summary": "Data 1"}, "relations": []},
        {"operation": "propose_claim", "proposed_id": "CLAIM_B2", "claim_tier": "causal", "text": "Claim text", "depends_on": ["RES_B2"], "relations": [{"relation": "depends_on", "target_id": "RES_B2"}]},
    ]
    prop1 = make_proposal(store.project_id, snap0, "PROP_B2_1", ops1)
    import_proposal(store, json.dumps(prop1), bob_token)

    # Revise RES_B2 to revision 2
    snap1 = export_snapshot(store, token=owner_token)
    ops2 = [
        {"operation": "revise_object", "object_id": "RES_B2", "base_revision": 1, "content": {"label": "Res 2", "summary": "Data 2"}, "relations": []}
    ]
    prop2 = make_proposal(store.project_id, snap1, "PROP_B2_2", ops2)
    import_proposal(store, json.dumps(prop2), bob_token)

    # Attempt to cite stale revision 1 of RES_B2 when head is 2
    res = record_adjudication(
        store,
        carol_token,
        subject_id="CLAIM_B2",
        subject_revision=1,
        assessment_kind="semantic_entailment",
        verdict="SUPPORTED",
        rationale="Evidence supports claim",
        cited_evidence=[{"object_id": "RES_B2", "revision": 1}],
        is_independent=True,
    )
    assert res["decision"] == "REFUSED"
    assert res["rule"] == "STALE_CITED_EVIDENCE"


def test_b_neg_3_unauthorized_adjudication(tmp_path: Path):
    """B-NEG-3: Attempt to record independent adjudication using importer/author token."""
    env = _setup_adversarial_env(tmp_path)
    store = env["store"]
    bob_token = env["bob_token"]
    owner_token = env["owner_token"]

    snap = export_snapshot(store, token=owner_token)
    ops = [
        {"operation": "propose_object", "proposed_id": "RQ_B3", "object_type": "ResearchQuestion", "content": {"label": "RQ", "text": "Question?"}, "relations": []},
        {"operation": "propose_claim", "proposed_id": "CLAIM_B3", "claim_tier": "causal", "text": "Claim text", "depends_on": [], "relations": []},
    ]
    prop = make_proposal(store.project_id, snap, "PROP_B3", ops)
    import_proposal(store, json.dumps(prop), bob_token)

    res = record_adjudication(
        store,
        bob_token,  # Author Bob lacks independent_reviewer role
        subject_id="CLAIM_B3",
        subject_revision=1,
        assessment_kind="semantic_entailment",
        verdict="SUPPORTED",
        rationale="Author attempts independent adjudication",
        is_independent=True,
    )
    assert res["decision"] == "UNAUTHORIZED"
    assert res["rule"] == "ROLE_REQUIRED"


def test_b_neg_4_agent_origin_adjudication_rejected(tmp_path: Path):
    """B-NEG-4: Submit an agent proposal containing an authoritative Adjudication."""
    env = _setup_adversarial_env(tmp_path)
    store = env["store"]
    bob_token = env["bob_token"]
    owner_token = env["owner_token"]

    snap = export_snapshot(store, token=owner_token)
    ops = [
        {
            "operation": "propose_object",
            "proposed_id": "ADJ_AGENT_01",
            "object_type": "Adjudication",  # Server-only type!
            "content": {"verdict": "SUPPORTED", "assessment_kind": "semantic_entailment"},
            "relations": [],
        }
    ]
    prop = make_proposal(store.project_id, snap, "PROP_B4", ops)
    rec = import_proposal(store, json.dumps(prop), bob_token)
    assert rec["decision"] == Decision.UNAUTHORIZED.value
    assert rec["rule"] in ("PRIVILEGED_OBJECT_TYPE", "SERVER_ONLY_TYPE")


def test_b_pos_1_valid_cited_evidence(tmp_path: Path):
    """B-POS-1: Valid cited evidence adjudication with depends_on relation."""
    env = _setup_adversarial_env(tmp_path)
    store = env["store"]
    bob_token = env["bob_token"]
    carol_token = env["carol_token"]
    owner_token = env["owner_token"]

    snap = export_snapshot(store, token=owner_token)
    ops = [
        {"operation": "propose_object", "proposed_id": "RQ_BP1", "object_type": "ResearchQuestion", "content": {"label": "RQ", "text": "Does refactoring help?"}, "relations": []},
        {"operation": "propose_object", "proposed_id": "RES_001", "object_type": "Result", "content": {"label": "Benchmark Result", "summary": "Defects fell 50%"}, "relations": []},
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_001",
            "claim_tier": "causal",
            "text": "Refactoring causally reduces defects.",
            "depends_on": ["RES_001"],
            "relations": [
                {"relation": "depends_on", "target_id": "RQ_BP1"},
                {"relation": "depends_on", "target_id": "RES_001"},
            ],
        },
    ]
    prop = make_proposal(store.project_id, snap, "PROP_BP1", ops)
    import_proposal(store, json.dumps(prop), bob_token)

    adj_res = record_adjudication(
        store,
        carol_token,
        subject_id="CLAIM_001",
        subject_revision=1,
        assessment_kind="semantic_entailment",
        verdict="SUPPORTED",
        rationale="The evidence supports the claim.",
        cited_evidence=[{"object_id": "RES_001", "revision": 1}],
        is_independent=True,
    )
    assert adj_res["decision"] == "RECORDED"
    aid = adj_res["adjudication_id"]

    # Verify edge in database uses valid relation (depends_on, NOT evaluates)
    with store.read() as conn:
        edges = conn.execute("SELECT relation, target_id, target_revision FROM edges WHERE source_id=?", (aid,)).fetchall()
        assert any(e[0] == "supports" and e[1] == "CLAIM_001" and e[2] == 1 for e in edges)
        assert any(e[0] == "depends_on" and e[1] == "RES_001" and e[2] == 1 for e in edges)
        assert not any(e[0] == "evaluates" for e in edges)

    # Verify integrity passes
    aud = audit_project_integrity(store)
    assert aud["status"] == IntegrityStatus.PASS.value

    # Carol also records independent review approval
    record_review(store, carol_token, object_id="CLAIM_001", revision=1, kind="independent_review", decision="approve", rationale="Approved")

    # Release check succeeds
    rel = release_check(store)
    assert rel["release_eligibility"] == ReleaseEligibility.ELIGIBLE.value


# ==============================================================================================
# PART C — P0: Enforce Documented Causal Scientific Policy (Gate C)
# Truth Table: Empirical Result x Authorized SUPPORTED Adjudication
# ==============================================================================================

def test_c_causal_truth_table_scenario_1_no_result_no_adj(tmp_path: Path):
    """Causal Gate: Empirical Result = No, Adjudication = No -> BLOCKED."""
    env = _setup_adversarial_env(tmp_path)
    store = env["store"]
    bob_token = env["bob_token"]
    carol_token = env["carol_token"]
    owner_token = env["owner_token"]

    snap = export_snapshot(store, token=owner_token)
    ops = [
        {"operation": "propose_object", "proposed_id": "RQ_C1", "object_type": "ResearchQuestion", "content": {"label": "RQ", "text": "Inquiry?"}, "relations": []},
        {"operation": "propose_claim", "proposed_id": "CLAIM_C1", "claim_tier": "causal", "text": "Claim with no result or adj", "depends_on": [], "relations": [{"relation": "depends_on", "target_id": "RQ_C1"}]},
    ]
    prop = make_proposal(store.project_id, snap, "PROP_C1", ops)
    import_proposal(store, json.dumps(prop), bob_token)
    record_review(store, carol_token, object_id="CLAIM_C1", revision=1, kind="independent_review", decision="approve", rationale="Approved")

    res = release_check(store)
    assert res["release_eligibility"] == ReleaseEligibility.BLOCKED.value
    assert any("CAUSAL_CLAIM_LACKS_EMPIRICAL_SUPPORT" in r for r in res["block_reasons"])
    assert any("CAUSAL_CLAIM_LACKS_ADJUDICATION" in r for r in res["block_reasons"])


def test_c_causal_truth_table_scenario_2_yes_result_no_adj(tmp_path: Path):
    """Causal Gate: Empirical Result = Yes, Adjudication = No -> BLOCKED."""
    env = _setup_adversarial_env(tmp_path)
    store = env["store"]
    bob_token = env["bob_token"]
    carol_token = env["carol_token"]
    owner_token = env["owner_token"]

    snap = export_snapshot(store, token=owner_token)
    ops = [
        {"operation": "propose_object", "proposed_id": "RQ_C2", "object_type": "ResearchQuestion", "content": {"label": "RQ", "text": "Inquiry?"}, "relations": []},
        {"operation": "propose_object", "proposed_id": "RES_C2", "object_type": "Result", "content": {"label": "Empirical Result", "summary": "Found p < 0.01"}, "relations": []},
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_C2",
            "claim_tier": "causal",
            "text": "Claim with empirical result but no adjudication",
            "depends_on": ["RES_C2"],
            "relations": [
                {"relation": "depends_on", "target_id": "RQ_C2"},
                {"relation": "depends_on", "target_id": "RES_C2"},
            ],
        },
    ]
    prop = make_proposal(store.project_id, snap, "PROP_C2", ops)
    import_proposal(store, json.dumps(prop), bob_token)
    record_review(store, carol_token, object_id="CLAIM_C2", revision=1, kind="independent_review", decision="approve", rationale="Approved")

    res = release_check(store)
    assert res["release_eligibility"] == ReleaseEligibility.BLOCKED.value
    assert any("CAUSAL_CLAIM_LACKS_ADJUDICATION" in r for r in res["block_reasons"])
    assert not any("CAUSAL_CLAIM_LACKS_EMPIRICAL_SUPPORT" in r for r in res["block_reasons"])


def test_c_causal_truth_table_scenario_3_no_result_yes_adj(tmp_path: Path):
    """Causal Gate: Empirical Result = No, Adjudication = Yes -> BLOCKED."""
    env = _setup_adversarial_env(tmp_path)
    store = env["store"]
    bob_token = env["bob_token"]
    carol_token = env["carol_token"]
    owner_token = env["owner_token"]

    snap = export_snapshot(store, token=owner_token)
    ops = [
        {"operation": "propose_object", "proposed_id": "RQ_C3", "object_type": "ResearchQuestion", "content": {"label": "RQ", "text": "Inquiry?"}, "relations": []},
        {"operation": "propose_object", "proposed_id": "SRC_C3", "object_type": "Source", "content": {"citation_key": "Src1", "bibliographic_identity": "Src1"}, "relations": []},
        {"operation": "propose_object", "proposed_id": "EP_C3", "object_type": "EvidencePassage", "content": {"locator": "p. 1", "normalized_claim": "Text"}, "relations": [{"relation": "derived_from", "target_id": "SRC_C3"}]},
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_C3",
            "claim_tier": "causal",
            "text": "Claim with passage and adjudication, but no empirical Result",
            "depends_on": ["EP_C3"],
            "relations": [
                {"relation": "depends_on", "target_id": "RQ_C3"},
                {"relation": "depends_on", "target_id": "EP_C3"},
            ],
        },
    ]
    prop = make_proposal(store.project_id, snap, "PROP_C3", ops)
    import_proposal(store, json.dumps(prop), bob_token)

    record_adjudication(
        store,
        carol_token,
        subject_id="CLAIM_C3",
        subject_revision=1,
        assessment_kind="semantic_entailment",
        verdict="SUPPORTED",
        rationale="Passage supports claim logic",
        cited_evidence=[{"object_id": "EP_C3", "revision": 1}],
        is_independent=True,
    )
    record_review(store, carol_token, object_id="CLAIM_C3", revision=1, kind="independent_review", decision="approve", rationale="Approved")

    res = release_check(store)
    assert res["release_eligibility"] == ReleaseEligibility.BLOCKED.value
    assert any("CAUSAL_CLAIM_LACKS_EMPIRICAL_SUPPORT" in r for r in res["block_reasons"])
    assert not any("CAUSAL_CLAIM_LACKS_ADJUDICATION" in r for r in res["block_reasons"])


def test_c_causal_truth_table_scenario_4_yes_result_yes_adj(tmp_path: Path):
    """Causal Gate: Empirical Result = Yes, Adjudication = Yes -> ELIGIBLE."""
    env = _setup_adversarial_env(tmp_path)
    store = env["store"]
    bob_token = env["bob_token"]
    carol_token = env["carol_token"]
    owner_token = env["owner_token"]

    snap = export_snapshot(store, token=owner_token)
    ops = [
        {"operation": "propose_object", "proposed_id": "RQ_C4", "object_type": "ResearchQuestion", "content": {"label": "RQ", "text": "Inquiry?"}, "relations": []},
        {"operation": "propose_object", "proposed_id": "RES_C4", "object_type": "Result", "content": {"label": "Empirical Result", "summary": "RCT effect p < 0.001"}, "relations": []},
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_C4",
            "claim_tier": "causal",
            "text": "Claim with both empirical result and authorized adjudication",
            "depends_on": ["RES_C4"],
            "relations": [
                {"relation": "depends_on", "target_id": "RQ_C4"},
                {"relation": "depends_on", "target_id": "RES_C4"},
            ],
        },
    ]
    prop = make_proposal(store.project_id, snap, "PROP_C4", ops)
    import_proposal(store, json.dumps(prop), bob_token)

    record_adjudication(
        store,
        carol_token,
        subject_id="CLAIM_C4",
        subject_revision=1,
        assessment_kind="semantic_entailment",
        verdict="SUPPORTED",
        rationale="Identification strategy verified via RCT.",
        cited_evidence=[{"object_id": "RES_C4", "revision": 1}],
        is_independent=True,
    )
    record_review(store, carol_token, object_id="CLAIM_C4", revision=1, kind="independent_review", decision="approve", rationale="Approved")

    res = release_check(store)
    assert res["release_eligibility"] == ReleaseEligibility.ELIGIBLE.value
    assert not res["block_reasons"]


# ==============================================================================================
# PART D — P1: Distinguish Current and Historical Adjudications (Gate D)
# ==============================================================================================

def test_d_historical_supported_alone_blocked_by_default(tmp_path: Path):
    """Historical SUPPORTED verdict exists, but default policy requires current adjudication -> BLOCKED."""
    env = _setup_adversarial_env(tmp_path)
    store = env["store"]
    bob_token = env["bob_token"]
    carol_token = env["carol_token"]
    owner_token = env["owner_token"]

    snap = export_snapshot(store, token=owner_token)
    ops = [
        {"operation": "propose_object", "proposed_id": "RQ_D1", "object_type": "ResearchQuestion", "content": {"label": "RQ", "text": "Inquiry?"}, "relations": []},
        {"operation": "propose_object", "proposed_id": "RES_D1", "object_type": "Result", "content": {"label": "Result", "summary": "Data"}, "relations": []},
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_D1",
            "claim_tier": "causal",
            "text": "Causal claim with historical adjudication",
            "depends_on": ["RES_D1"],
            "relations": [{"relation": "depends_on", "target_id": "RQ_D1"}, {"relation": "depends_on", "target_id": "RES_D1"}],
        },
    ]
    prop = make_proposal(store.project_id, snap, "PROP_D1", ops)
    import_proposal(store, json.dumps(prop), bob_token)
    record_review(store, carol_token, object_id="CLAIM_D1", revision=1, kind="independent_review", decision="approve", rationale="Approved")

    # Manually insert a historical adjudication (as legacy migration would produce)
    with store.write_tx(token=owner_token) as tx:
        content = {
            "subject": {"object_id": "CLAIM_D1", "revision": 1},
            "assessment_kind": "semantic_entailment",
            "verdict": "SUPPORTED",
            "rationale": "Historical legacy judgment",
            "is_historical": True,
        }
        tx.add_revision(
            object_id="ADJ_HIST_01",
            object_type="Adjudication",
            content=content,
            edges=[("supports", "CLAIM_D1", 1, {"origin": "legacy_import"})],
            origin={"kind": "LEGACY_MIGRATED", "operation": "legacy_migration"},
        )

    # Under default policy: historical adjudication does not satisfy current causal adjudication requirement
    res_default = release_check(store)
    assert res_default["release_eligibility"] == ReleaseEligibility.BLOCKED.value
    assert any("HISTORICAL_ADJUDICATION_NOT_CURRENT" in r for r in res_default["block_reasons"])
    assert res_default["adjudications_summary"]["LEGACY_HISTORICAL"] == 1
    assert res_default["adjudications_summary"]["CURRENT_AUTHORIZED"] == 0
    assert res_default["historical_adjudications_accepted"] is False

    # Under explicit legacy-compatible policy: historical adjudication accepted
    policy_legacy = {"allow_historical_adjudication": True}
    res_legacy = release_check(store, policy=policy_legacy)
    assert res_legacy["release_eligibility"] == ReleaseEligibility.ELIGIBLE.value
    assert res_legacy["historical_adjudications_accepted"] is True


def test_d_historical_on_old_revision_cannot_support_current(tmp_path: Path):
    """Historical adjudication bound to revision 1 cannot support revision 2."""
    env = _setup_adversarial_env(tmp_path)
    store = env["store"]
    bob_token = env["bob_token"]
    carol_token = env["carol_token"]
    owner_token = env["owner_token"]

    snap0 = export_snapshot(store, token=owner_token)
    ops1 = [
        {"operation": "propose_object", "proposed_id": "RQ_D2", "object_type": "ResearchQuestion", "content": {"label": "RQ", "text": "Inquiry?"}, "relations": []},
        {"operation": "propose_object", "proposed_id": "RES_D2", "object_type": "Result", "content": {"label": "Result", "summary": "Data"}, "relations": []},
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_D2",
            "claim_tier": "causal",
            "text": "Claim v1",
            "depends_on": ["RES_D2"],
            "relations": [{"relation": "depends_on", "target_id": "RQ_D2"}, {"relation": "depends_on", "target_id": "RES_D2"}],
        },
    ]
    prop1 = make_proposal(store.project_id, snap0, "PROP_D2_1", ops1)
    import_proposal(store, json.dumps(prop1), bob_token)

    # Insert historical adjudication on CLAIM_D2 revision 1
    with store.write_tx(token=owner_token) as tx:
        content = {
            "subject": {"object_id": "CLAIM_D2", "revision": 1},
            "assessment_kind": "semantic_entailment",
            "verdict": "SUPPORTED",
            "is_historical": True,
        }
        tx.add_revision(
            object_id="ADJ_HIST_D2",
            object_type="Adjudication",
            content=content,
            edges=[("supports", "CLAIM_D2", 1, {"origin": "legacy_import"})],
            origin={"kind": "LEGACY_MIGRATED", "operation": "legacy_migration"},
        )

    # Now revise CLAIM_D2 to revision 2
    snap1 = export_snapshot(store, token=owner_token)
    ops2 = [
        {
            "operation": "revise_object",
            "object_id": "CLAIM_D2",
            "base_revision": 1,
            "content": {"text": "Claim v2", "claim_tier": "causal"},
            "relations": [{"relation": "depends_on", "target_id": "RQ_D2"}, {"relation": "depends_on", "target_id": "RES_D2"}],
        }
    ]
    prop2 = make_proposal(store.project_id, snap1, "PROP_D2_2", ops2)
    import_proposal(store, json.dumps(prop2), bob_token)
    record_review(store, carol_token, object_id="CLAIM_D2", revision=2, kind="independent_review", decision="approve", rationale="Approved v2")

    # Even with allow_historical_adjudication=True, revision 1 verdict cannot support revision 2!
    res = release_check(store, policy={"allow_historical_adjudication": True})
    assert res["release_eligibility"] == ReleaseEligibility.BLOCKED.value
    assert any("CAUSAL_CLAIM_LACKS_ADJUDICATION" in r for r in res["block_reasons"])
    assert res["adjudications_summary"]["SUPERSEDED"] == 1


# ==============================================================================================
# PART E — P1: Reconcile All Seven Claim Tiers (Gate E)
# ==============================================================================================

def test_e_1_descriptive_tier(tmp_path: Path):
    """Tier 1 (descriptive): Positive fixture with EvidencePassage; Negative fixture with Source only."""
    env = _setup_adversarial_env(tmp_path)
    store = env["store"]
    bob_token = env["bob_token"]
    carol_token = env["carol_token"]
    owner_token = env["owner_token"]

    # Negative: Descriptive claim supported only by bare Source (no passage)
    snap0 = export_snapshot(store, token=owner_token)
    ops_neg = [
        {"operation": "propose_object", "proposed_id": "RQ_E1_NEG", "object_type": "ResearchQuestion", "content": {"label": "RQ", "text": "Literature review?"}, "relations": []},
        {"operation": "propose_object", "proposed_id": "SRC_E1_NEG", "object_type": "Source", "content": {"citation_key": "Src1", "bibliographic_identity": "Src1"}, "relations": []},
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_E1_NEG",
            "claim_tier": "descriptive",
            "text": "Descriptive claim referencing bare source",
            "depends_on": ["SRC_E1_NEG"],
            "relations": [{"relation": "depends_on", "target_id": "RQ_E1_NEG"}, {"relation": "depends_on", "target_id": "SRC_E1_NEG"}],
        },
    ]
    prop_neg = make_proposal(store.project_id, snap0, "PROP_E1_NEG", ops_neg)
    import_proposal(store, json.dumps(prop_neg), bob_token)
    record_review(store, carol_token, object_id="CLAIM_E1_NEG", revision=1, kind="independent_review", decision="approve", rationale="Approved")

    res_neg = release_check(store)
    assert res_neg["release_eligibility"] == ReleaseEligibility.BLOCKED.value
    assert any("CLAIM_UNSUPPORTED_DESCRIPTIVE_SOURCE_ONLY" in r for r in res_neg["block_reasons"])

    # Positive: Descriptive claim supported by EvidencePassage
    snap1 = export_snapshot(store, token=owner_token)
    ops_pos = [
        {"operation": "propose_object", "proposed_id": "EP_E1_POS", "object_type": "EvidencePassage", "content": {"locator": "p. 10", "normalized_claim": "Passage findings"}, "relations": [{"relation": "derived_from", "target_id": "SRC_E1_NEG"}]},
        {
            "operation": "revise_object",
            "object_id": "CLAIM_E1_NEG",
            "base_revision": 1,
            "content": {"text": "Descriptive claim with evidence passage", "claim_tier": "descriptive"},
            "relations": [{"relation": "depends_on", "target_id": "RQ_E1_NEG"}, {"relation": "depends_on", "target_id": "EP_E1_POS"}],
        },
    ]
    prop_pos = make_proposal(store.project_id, snap1, "PROP_E1_POS", ops_pos)
    import_proposal(store, json.dumps(prop_pos), bob_token)
    record_review(store, carol_token, object_id="CLAIM_E1_NEG", revision=2, kind="independent_review", decision="approve", rationale="Approved v2")

    res_pos = release_check(store)
    assert res_pos["release_eligibility"] == ReleaseEligibility.ELIGIBLE.value


def test_e_2_associational_tier(tmp_path: Path):
    """Tier 2 (associational): Positive fixture with Result; Negative fixture without Result/Passage."""
    env = _setup_adversarial_env(tmp_path)
    store = env["store"]
    bob_token = env["bob_token"]
    carol_token = env["carol_token"]
    owner_token = env["owner_token"]

    # Negative: Associational claim without Result or Passage
    snap0 = export_snapshot(store, token=owner_token)
    ops_neg = [
        {"operation": "propose_object", "proposed_id": "RQ_E2", "object_type": "ResearchQuestion", "content": {"label": "RQ", "text": "Correlation?"}, "relations": []},
        {"operation": "propose_claim", "proposed_id": "CLAIM_E2", "claim_tier": "associational", "text": "Claim with no result", "depends_on": [], "relations": [{"relation": "depends_on", "target_id": "RQ_E2"}]},
    ]
    prop_neg = make_proposal(store.project_id, snap0, "PROP_E2_NEG", ops_neg)
    import_proposal(store, json.dumps(prop_neg), bob_token)
    record_review(store, carol_token, object_id="CLAIM_E2", revision=1, kind="independent_review", decision="approve", rationale="Approved")

    res_neg = release_check(store)
    assert res_neg["release_eligibility"] == ReleaseEligibility.BLOCKED.value
    assert any("ASSOCIATIONAL_CLAIM_LACKS_SUPPORT" in r for r in res_neg["block_reasons"])

    # Positive: With Result
    snap1 = export_snapshot(store, token=owner_token)
    ops_pos = [
        {"operation": "propose_object", "proposed_id": "RES_E2", "object_type": "Result", "content": {"label": "Correlation result", "summary": "r = 0.65, p < 0.01"}, "relations": []},
        {
            "operation": "revise_object",
            "object_id": "CLAIM_E2",
            "base_revision": 1,
            "content": {"text": "Claim with correlation result", "claim_tier": "associational"},
            "relations": [{"relation": "depends_on", "target_id": "RQ_E2"}, {"relation": "depends_on", "target_id": "RES_E2"}],
        },
    ]
    prop_pos = make_proposal(store.project_id, snap1, "PROP_E2_POS", ops_pos)
    import_proposal(store, json.dumps(prop_pos), bob_token)
    record_review(store, carol_token, object_id="CLAIM_E2", revision=2, kind="independent_review", decision="approve", rationale="Approved v2")

    res_pos = release_check(store)
    assert res_pos["release_eligibility"] == ReleaseEligibility.ELIGIBLE.value


def test_e_3_predictive_tier(tmp_path: Path):
    """Tier 3 (predictive): Positive fixture with validation Result; Negative without."""
    env = _setup_adversarial_env(tmp_path)
    store = env["store"]
    bob_token = env["bob_token"]
    carol_token = env["carol_token"]
    owner_token = env["owner_token"]

    # Negative: Predictive claim without Result
    snap0 = export_snapshot(store, token=owner_token)
    ops_neg = [
        {"operation": "propose_object", "proposed_id": "RQ_E3", "object_type": "ResearchQuestion", "content": {"label": "RQ", "text": "Prediction?"}, "relations": []},
        {"operation": "propose_claim", "proposed_id": "CLAIM_E3", "claim_tier": "predictive", "text": "Predictive claim without validation", "depends_on": [], "relations": [{"relation": "depends_on", "target_id": "RQ_E3"}]},
    ]
    prop_neg = make_proposal(store.project_id, snap0, "PROP_E3_NEG", ops_neg)
    import_proposal(store, json.dumps(prop_neg), bob_token)
    record_review(store, carol_token, object_id="CLAIM_E3", revision=1, kind="independent_review", decision="approve", rationale="Approved")

    res_neg = release_check(store)
    assert res_neg["release_eligibility"] == ReleaseEligibility.BLOCKED.value
    assert any("PREDICTIVE_CLAIM_LACKS_VALIDATION_RESULTS" in r for r in res_neg["block_reasons"])

    # Positive: With predictive validation result
    snap1 = export_snapshot(store, token=owner_token)
    ops_pos = [
        {"operation": "propose_object", "proposed_id": "RES_E3", "object_type": "Result", "content": {"label": "Out of sample validation", "summary": "AUC = 0.89, Brier = 0.08"}, "relations": []},
        {
            "operation": "revise_object",
            "object_id": "CLAIM_E3",
            "base_revision": 1,
            "content": {"text": "Predictive claim with validation", "claim_tier": "predictive"},
            "relations": [{"relation": "depends_on", "target_id": "RQ_E3"}, {"relation": "depends_on", "target_id": "RES_E3"}],
        },
    ]
    prop_pos = make_proposal(store.project_id, snap1, "PROP_E3_POS", ops_pos)
    import_proposal(store, json.dumps(prop_pos), bob_token)
    record_review(store, carol_token, object_id="CLAIM_E3", revision=2, kind="independent_review", decision="approve", rationale="Approved v2")

    res_pos = release_check(store)
    assert res_pos["release_eligibility"] == ReleaseEligibility.ELIGIBLE.value


def test_e_4_mechanism_consistent_tier(tmp_path: Path):
    """Tier 4 (mechanism_consistent): Positive with Mechanism; Negative without Mechanism."""
    env = _setup_adversarial_env(tmp_path)
    store = env["store"]
    bob_token = env["bob_token"]
    carol_token = env["carol_token"]
    owner_token = env["owner_token"]

    # Negative: Mechanism consistent claim without linked Mechanism object
    snap0 = export_snapshot(store, token=owner_token)
    ops_neg = [
        {"operation": "propose_object", "proposed_id": "RQ_E4", "object_type": "ResearchQuestion", "content": {"label": "RQ", "text": "Mechanism inquiry?"}, "relations": []},
        {"operation": "propose_object", "proposed_id": "RES_E4", "object_type": "Result", "content": {"label": "Result", "summary": "Trace observation"}, "relations": []},
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_E4",
            "claim_tier": "mechanism_consistent",
            "text": "Claim consistent with mechanism (unlinked)",
            "depends_on": ["RES_E4"],
            "relations": [{"relation": "depends_on", "target_id": "RQ_E4"}, {"relation": "depends_on", "target_id": "RES_E4"}],
        },
    ]
    prop_neg = make_proposal(store.project_id, snap0, "PROP_E4_NEG", ops_neg)
    import_proposal(store, json.dumps(prop_neg), bob_token)
    record_review(store, carol_token, object_id="CLAIM_E4", revision=1, kind="independent_review", decision="approve", rationale="Approved")

    res_neg = release_check(store)
    assert res_neg["release_eligibility"] == ReleaseEligibility.BLOCKED.value
    assert any("MECHANISM_CLAIM_LACKS_MECHANISM" in r for r in res_neg["block_reasons"])

    # Positive: With linked Mechanism object
    snap1 = export_snapshot(store, token=owner_token)
    ops_pos = [
        {"operation": "propose_object", "proposed_id": "MECH_E4", "object_type": "Mechanism", "content": {"label": "Attention routing", "text": "Step by step routing"}, "relations": []},
        {
            "operation": "revise_object",
            "object_id": "CLAIM_E4",
            "base_revision": 1,
            "content": {"text": "Claim linked to mechanism", "claim_tier": "mechanism_consistent"},
            "relations": [
                {"relation": "depends_on", "target_id": "RQ_E4"},
                {"relation": "depends_on", "target_id": "RES_E4"},
                {"relation": "depends_on", "target_id": "MECH_E4"},
            ],
        },
    ]
    prop_pos = make_proposal(store.project_id, snap1, "PROP_E4_POS", ops_pos)
    import_proposal(store, json.dumps(prop_pos), bob_token)
    record_review(store, carol_token, object_id="CLAIM_E4", revision=2, kind="independent_review", decision="approve", rationale="Approved v2")

    res_pos = release_check(store)
    assert res_pos["release_eligibility"] == ReleaseEligibility.ELIGIBLE.value


def test_e_5_causal_tier(tmp_path: Path):
    """Tier 5 (causal): Tested thoroughly in Part C; verified here as part of tier sweep."""
    env = _setup_adversarial_env(tmp_path)
    store = env["store"]
    bob_token = env["bob_token"]
    carol_token = env["carol_token"]
    owner_token = env["owner_token"]

    snap = export_snapshot(store, token=owner_token)
    ops = [
        {"operation": "propose_object", "proposed_id": "RQ_E5", "object_type": "ResearchQuestion", "content": {"label": "RQ", "text": "Causal query"}, "relations": []},
        {"operation": "propose_object", "proposed_id": "RES_E5", "object_type": "Result", "content": {"label": "RCT Effect", "summary": "tau = 3.2, p < 0.001"}, "relations": []},
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_E5",
            "claim_tier": "causal",
            "text": "Intervention causes performance improvement",
            "depends_on": ["RES_E5"],
            "relations": [{"relation": "depends_on", "target_id": "RQ_E5"}, {"relation": "depends_on", "target_id": "RES_E5"}],
        },
    ]
    prop = make_proposal(store.project_id, snap, "PROP_E5", ops)
    import_proposal(store, json.dumps(prop), bob_token)

    # Negative: Before adjudication
    record_review(store, carol_token, object_id="CLAIM_E5", revision=1, kind="independent_review", decision="approve", rationale="Approved")
    res_neg = release_check(store)
    assert res_neg["release_eligibility"] == ReleaseEligibility.BLOCKED.value
    assert any("CAUSAL_CLAIM_LACKS_ADJUDICATION" in r for r in res_neg["block_reasons"])

    # Positive: After authorized adjudication with cited evidence
    record_adjudication(
        store,
        carol_token,
        subject_id="CLAIM_E5",
        subject_revision=1,
        assessment_kind="semantic_entailment",
        verdict="SUPPORTED",
        rationale="RCT causal identification authenticated",
        cited_evidence=[{"object_id": "RES_E5", "revision": 1}],
        is_independent=True,
    )
    res_pos = release_check(store)
    assert res_pos["release_eligibility"] == ReleaseEligibility.ELIGIBLE.value


def test_e_6_measurement_validation_tier(tmp_path: Path):
    """Tier 6 (measurement_validation): Requires Construct + Proxy operationalization AND empirical validation Result."""
    env = _setup_adversarial_env(tmp_path)
    store = env["store"]
    bob_token = env["bob_token"]
    carol_token = env["carol_token"]
    owner_token = env["owner_token"]

    # Negative 1: Missing operationalization (construct/proxy)
    snap0 = export_snapshot(store, token=owner_token)
    ops_neg1 = [
        {"operation": "propose_object", "proposed_id": "RQ_E6", "object_type": "ResearchQuestion", "content": {"label": "RQ", "text": "Measurement validity?"}, "relations": []},
        {"operation": "propose_object", "proposed_id": "RES_E6", "object_type": "Result", "content": {"label": "Psychometric study", "summary": "Cronbach alpha = 0.91"}, "relations": []},
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_E6",
            "claim_tier": "measurement_validation",
            "text": "Survey measures developer cognitive load accurately",
            "depends_on": ["RES_E6"],
            "relations": [{"relation": "depends_on", "target_id": "RQ_E6"}, {"relation": "depends_on", "target_id": "RES_E6"}],
        },
    ]
    prop_neg1 = make_proposal(store.project_id, snap0, "PROP_E6_NEG1", ops_neg1)
    import_proposal(store, json.dumps(prop_neg1), bob_token)
    record_review(store, carol_token, object_id="CLAIM_E6", revision=1, kind="independent_review", decision="approve", rationale="Approved")

    res_neg1 = release_check(store)
    assert res_neg1["release_eligibility"] == ReleaseEligibility.BLOCKED.value
    assert any("MEASUREMENT_CLAIM_LACKS_OPERATIONALIZATION" in r for r in res_neg1["block_reasons"])

    # Negative 2: Has construct and proxy, but lacks empirical validation Result
    snap1 = export_snapshot(store, token=owner_token)
    ops_neg2 = [
        {"operation": "propose_object", "proposed_id": "CONST_E6", "object_type": "Construct", "content": {"label": "Cognitive Load", "definition": "Mental effort"}, "relations": []},
        {"operation": "propose_object", "proposed_id": "PROXY_E6", "object_type": "Proxy", "content": {"label": "NASA-TLX Score", "definition": "Questionnaire score", "unit": "points"}, "relations": [{"relation": "operationalizes", "target_id": "CONST_E6"}]},
        {
            "operation": "revise_object",
            "object_id": "CLAIM_E6",
            "base_revision": 1,
            "content": {"text": "Claim with proxy and construct only (no validation result)", "claim_tier": "measurement_validation"},
            "relations": [
                {"relation": "depends_on", "target_id": "RQ_E6"},
                {"relation": "depends_on", "target_id": "CONST_E6"},
                {"relation": "depends_on", "target_id": "PROXY_E6"},
            ],
        },
    ]
    prop_neg2 = make_proposal(store.project_id, snap1, "PROP_E6_NEG2", ops_neg2)
    import_proposal(store, json.dumps(prop_neg2), bob_token)
    record_review(store, carol_token, object_id="CLAIM_E6", revision=2, kind="independent_review", decision="approve", rationale="Approved v2")

    res_neg2 = release_check(store)
    assert res_neg2["release_eligibility"] == ReleaseEligibility.BLOCKED.value
    assert any("MEASUREMENT_CLAIM_LACKS_VALIDATION_RESULT" in r for r in res_neg2["block_reasons"])

    # Positive: Construct + Proxy + validation Result
    snap2 = export_snapshot(store, token=owner_token)
    ops_pos = [
        {
            "operation": "revise_object",
            "object_id": "CLAIM_E6",
            "base_revision": 2,
            "content": {"text": "Claim with proxy, construct, and validation result", "claim_tier": "measurement_validation"},
            "relations": [
                {"relation": "depends_on", "target_id": "RQ_E6"},
                {"relation": "depends_on", "target_id": "CONST_E6"},
                {"relation": "depends_on", "target_id": "PROXY_E6"},
                {"relation": "depends_on", "target_id": "RES_E6"},
            ],
        }
    ]
    prop_pos = make_proposal(store.project_id, snap2, "PROP_E6_POS", ops_pos)
    import_proposal(store, json.dumps(prop_pos), bob_token)
    record_review(store, carol_token, object_id="CLAIM_E6", revision=3, kind="independent_review", decision="approve", rationale="Approved v3")

    res_pos = release_check(store)
    assert res_pos["release_eligibility"] == ReleaseEligibility.ELIGIBLE.value


def test_e_7_conceptual_tier(tmp_path: Path):
    """Tier 7 (conceptual): Requires Theory or Construct; never requires empirical data."""
    env = _setup_adversarial_env(tmp_path)
    store = env["store"]
    bob_token = env["bob_token"]
    carol_token = env["carol_token"]
    owner_token = env["owner_token"]

    # Negative: Conceptual claim without Theory or Construct
    snap0 = export_snapshot(store, token=owner_token)
    ops_neg = [
        {"operation": "propose_object", "proposed_id": "RQ_E7", "object_type": "ResearchQuestion", "content": {"label": "RQ", "text": "Theoretical inquiry?"}, "relations": []},
        {"operation": "propose_claim", "proposed_id": "CLAIM_E7", "claim_tier": "conceptual", "text": "Pure assertion without theory", "depends_on": [], "relations": [{"relation": "depends_on", "target_id": "RQ_E7"}]},
    ]
    prop_neg = make_proposal(store.project_id, snap0, "PROP_E7_NEG", ops_neg)
    import_proposal(store, json.dumps(prop_neg), bob_token)
    record_review(store, carol_token, object_id="CLAIM_E7", revision=1, kind="independent_review", decision="approve", rationale="Approved")

    res_neg = release_check(store)
    assert res_neg["release_eligibility"] == ReleaseEligibility.BLOCKED.value
    assert any("CONCEPTUAL_CLAIM_LACKS_THEORY_OR_CONSTRUCT" in r for r in res_neg["block_reasons"])

    # Positive: Linked to Theory (no empirical data needed!)
    snap1 = export_snapshot(store, token=owner_token)
    ops_pos = [
        {"operation": "propose_object", "proposed_id": "TH_E7", "object_type": "Theory", "content": {"label": "Complexity Theory", "text": "Formal theory formulation"}, "relations": []},
        {
            "operation": "revise_object",
            "object_id": "CLAIM_E7",
            "base_revision": 1,
            "content": {"text": "Conceptual claim grounded in theory", "claim_tier": "conceptual"},
            "relations": [{"relation": "depends_on", "target_id": "RQ_E7"}, {"relation": "depends_on", "target_id": "TH_E7"}],
        },
    ]
    prop_pos = make_proposal(store.project_id, snap1, "PROP_E7_POS", ops_pos)
    import_proposal(store, json.dumps(prop_pos), bob_token)
    record_review(store, carol_token, object_id="CLAIM_E7", revision=2, kind="independent_review", decision="approve", rationale="Approved v2")

    res_pos = release_check(store)
    assert res_pos["release_eligibility"] == ReleaseEligibility.ELIGIBLE.value


# ==============================================================================================
# PART F — Preserve Previously Remediated Migration Behavior (Gate F)
# ==============================================================================================

def test_f_migration_invariants_preserved(tmp_path: Path):
    """Preserve repair of historical imports, dry-run, changed legacy detection, and non-reversal of legitimate revisions."""
    legacy_dir = tmp_path / "legacy_v8"
    m = legacy_dir / "00_master"
    m.mkdir(parents=True)
    (m / "research_questions.csv").write_text("rq_id,rq_text\nRQ_F1,How does harness work?\n", encoding="utf-8")
    (m / "proposition_registry.csv").write_text("proposition_id,proposition_text\nPROP_F1,Harness accelerates research.\n", encoding="utf-8")

    store, owner_token = StateStore.create(tmp_path / "prj_mig", "PRJ_MIG", owner_id="owner_alice")
    bob_token = register_actor(store, owner_token, actor_id="author_bob", roles=["author", "importer"])["token"]

    # 1. Dry run should not mutate store
    dry = migrate_legacy_project(legacy_dir, store, token=owner_token, dry_run=True)
    assert dry["decision"] == "DRY_RUN"
    with store.read() as conn:
        assert conn.execute("SELECT count(*) FROM objects").fetchone()[0] == 0

    # 2. Real migration
    res1 = migrate_legacy_project(legacy_dir, store, token=owner_token)
    assert res1["status"] in ("PASS", "PASS_WITH_WARNINGS")
    assert res1["created_objects_count"] >= 2

    # 3. Legitimate post-migration revision by author
    snap = export_snapshot(store, token=owner_token)
    claim_id = [o["object_id"] for o in snap["objects"] if o["object_type"] == "Claim"][0]
    ops = [
        {
            "operation": "revise_object",
            "object_id": claim_id,
            "base_revision": 1,
            "content": {"text": "Refined claim post-migration by human researcher", "claim_tier": "descriptive"},
            "relations": [],
        }
    ]
    prop = make_proposal(store.project_id, snap, "PROP_LEGIT_REV", ops)
    rec = import_proposal(store, json.dumps(prop), bob_token)
    assert rec["decision"] == "ACCEPTED"
    assert rec["new_state_revision"] > 1

    # 4. Repeat migration must NOT overwrite or reverse legitimate claim revision
    res2 = migrate_legacy_project(legacy_dir, store, token=owner_token)
    with store.read() as conn:
        head = store.get_object(claim_id, conn=conn)
        assert head["head_revision"] == 2
        assert head["content"]["text"] == "Refined claim post-migration by human researcher"


# ==============================================================================================
# PART G — Independent End-to-End Acceptance Scenarios (Gate G: G1 - G6)
# ==============================================================================================

def test_g1_valid_descriptive_research_workflow(tmp_path: Path):
    """G1: Create -> Snapshot -> Proposal -> Import -> Evidence -> Review -> Release (ELIGIBLE)."""
    env = _setup_adversarial_env(tmp_path)
    store = env["store"]
    owner_token = env["owner_token"]
    bob_token = env["bob_token"]
    carol_token = env["carol_token"]

    snap0 = export_snapshot(store, token=owner_token)
    ops = [
        {"operation": "propose_object", "proposed_id": "RQ_G1", "object_type": "ResearchQuestion", "content": {"label": "RQ G1", "text": "What is the structure of V9?"}, "relations": []},
        {"operation": "propose_object", "proposed_id": "SRC_G1", "object_type": "Source", "content": {"citation_key": "Arch2026", "bibliographic_identity": "Research Harness Architecture"}, "relations": []},
        {"operation": "propose_object", "proposed_id": "EP_G1", "object_type": "EvidencePassage", "content": {"locator": "p. 5", "normalized_claim": "Harness uses SQLite write transactions"}, "relations": [{"relation": "derived_from", "target_id": "SRC_G1"}]},
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_G1",
            "claim_tier": "descriptive",
            "text": "Research Harness 9.0 enforces relational consistency via SQLite transactions.",
            "depends_on": ["EP_G1"],
            "relations": [{"relation": "depends_on", "target_id": "RQ_G1"}, {"relation": "depends_on", "target_id": "EP_G1"}],
        },
    ]
    prop = make_proposal(store.project_id, snap0, "PROP_G1", ops)
    rec = import_proposal(store, json.dumps(prop), bob_token)
    assert rec["decision"] == "ACCEPTED"

    # Review with independent reviewer Carol
    rev_res = record_review(store, carol_token, object_id="CLAIM_G1", revision=1, kind="independent_review", decision="approve", rationale="Verified against source.")
    assert rev_res["decision"] == "RECORDED"

    # Verify project integrity and release check
    aud = audit_project_integrity(store)
    assert aud["status"] == IntegrityStatus.PASS.value

    rel = release_check(store)
    assert rel["integrity_status"] == "PASS"
    assert rel["workflow_status"] == "COMPLETE"
    assert rel["scientific_review_status"] == ReviewStatus.INDEPENDENTLY_REVIEWED.value
    assert rel["release_eligibility"] == ReleaseEligibility.ELIGIBLE.value
    assert not rel["block_reasons"]


def test_g2_valid_empirical_causal_research_workflow(tmp_path: Path):
    """G2: Create -> Snapshot -> Proposal -> Import -> Result -> Authorized Adjudication with Cited Evidence -> Review -> Release."""
    env = _setup_adversarial_env(tmp_path)
    store = env["store"]
    owner_token = env["owner_token"]
    bob_token = env["bob_token"]
    carol_token = env["carol_token"]

    snap0 = export_snapshot(store, token=owner_token)
    ops = [
        {"operation": "propose_object", "proposed_id": "RQ_G2", "object_type": "ResearchQuestion", "content": {"label": "Causal Inquiry", "text": "Does write token auth eliminate impersonation?"}, "relations": []},
        {"operation": "propose_object", "proposed_id": "RES_G2", "object_type": "Result", "content": {"label": "Adversarial Test Suite Result", "summary": "100% of unauthenticated attempts rejected"}, "relations": []},
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_G2",
            "claim_tier": "causal",
            "text": "Mandatory bearer tokens causally prevent snapshot identity impersonation.",
            "depends_on": ["RES_G2"],
            "relations": [{"relation": "depends_on", "target_id": "RQ_G2"}, {"relation": "depends_on", "target_id": "RES_G2"}],
        },
    ]
    prop = make_proposal(store.project_id, snap0, "PROP_G2", ops)
    rec = import_proposal(store, json.dumps(prop), bob_token)
    assert rec["decision"] == "ACCEPTED"

    # Authorized adjudication with cited evidence
    adj_res = record_adjudication(
        store,
        carol_token,
        subject_id="CLAIM_G2",
        subject_revision=1,
        assessment_kind="semantic_entailment",
        verdict="SUPPORTED",
        rationale="Empirical results establish causal prevention.",
        cited_evidence=[{"object_id": "RES_G2", "revision": 1}],
        is_independent=True,
    )
    assert adj_res["decision"] == "RECORDED"

    # Independent review
    rev_res = record_review(store, carol_token, object_id="CLAIM_G2", revision=1, kind="independent_review", decision="approve", rationale="Approved.")
    assert rev_res["decision"] == "RECORDED"

    # Release check under documented default causal policy
    aud = audit_project_integrity(store)
    assert aud["status"] == IntegrityStatus.PASS.value

    rel = release_check(store)
    assert rel["integrity_status"] == "PASS"
    assert rel["workflow_status"] == "COMPLETE"
    assert rel["scientific_review_status"] == ReviewStatus.INDEPENDENTLY_REVIEWED.value
    assert rel["release_eligibility"] == ReleaseEligibility.ELIGIBLE.value
    assert not rel["block_reasons"]


def test_g3_unauthorized_identity_workflow(tmp_path: Path):
    """G3: Existing owner ID without owner token -> Snapshot export (rejection with zero snapshot created)."""
    env = _setup_adversarial_env(tmp_path)
    store = env["store"]

    untrusted_actor = Actor("owner_alice", frozenset({"owner"}))
    with pytest.raises(AuthorizationError) as exc_info:
        export_snapshot(store, actor=untrusted_actor, token=None)
    assert exc_info.value.code == "AUTHENTICATION_FAILED"

    with store.read() as conn:
        assert conn.execute("SELECT count(*) FROM audit_log WHERE action='snapshot_export' AND decision='RECORDED'").fetchone()[0] == 0


def test_g4_agent_self_certification_workflow(tmp_path: Path):
    """G4: Agent proposal -> Adjudication SUPPORTED -> Import (gateway rejection)."""
    env = _setup_adversarial_env(tmp_path)
    store = env["store"]
    bob_token = env["bob_token"]
    owner_token = env["owner_token"]

    snap0 = export_snapshot(store, token=owner_token)
    ops = [
        {"operation": "propose_object", "proposed_id": "CLAIM_G4", "object_type": "Claim", "content": {"text": "Self-certified claim", "claim_tier": "causal"}, "relations": []},
        {"operation": "propose_object", "proposed_id": "ADJ_AGENT", "object_type": "Adjudication", "content": {"verdict": "SUPPORTED", "assessment_kind": "semantic_entailment"}, "relations": []},
    ]
    prop = make_proposal(store.project_id, snap0, "PROP_G4", ops)
    rec = import_proposal(store, json.dumps(prop), bob_token)
    assert rec["decision"] == Decision.UNAUTHORIZED.value
    assert rec["rule"] in ("PRIVILEGED_OBJECT_TYPE", "SERVER_ONLY_TYPE")


def test_g5_historical_adjudication_workflow(tmp_path: Path):
    """G5: Legacy migration -> Historical SUPPORTED verdict -> default release -> BLOCKED until current adjudication recorded."""
    env = _setup_adversarial_env(tmp_path)
    store = env["store"]
    owner_token = env["owner_token"]
    bob_token = env["bob_token"]
    carol_token = env["carol_token"]

    snap0 = export_snapshot(store, token=owner_token)
    ops = [
        {"operation": "propose_object", "proposed_id": "RQ_G5", "object_type": "ResearchQuestion", "content": {"label": "RQ", "text": "Question?"}, "relations": []},
        {"operation": "propose_object", "proposed_id": "RES_G5", "object_type": "Result", "content": {"label": "Result", "summary": "Data"}, "relations": []},
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_G5",
            "claim_tier": "causal",
            "text": "Claim with historical adjudication only",
            "depends_on": ["RES_G5"],
            "relations": [{"relation": "depends_on", "target_id": "RQ_G5"}, {"relation": "depends_on", "target_id": "RES_G5"}],
        },
    ]
    prop = make_proposal(store.project_id, snap0, "PROP_G5", ops)
    import_proposal(store, json.dumps(prop), bob_token)
    record_review(store, carol_token, object_id="CLAIM_G5", revision=1, kind="independent_review", decision="approve", rationale="Approved")

    # Insert historical adjudication
    with store.write_tx(token=owner_token) as tx:
        content = {
            "subject": {"object_id": "CLAIM_G5", "revision": 1},
            "assessment_kind": "semantic_entailment",
            "verdict": "SUPPORTED",
            "rationale": "Migrated historical judgment",
            "is_historical": True,
        }
        tx.add_revision(
            object_id="ADJ_HIST_G5",
            object_type="Adjudication",
            content=content,
            edges=[("supports", "CLAIM_G5", 1, {"origin": "legacy_migration"})],
            origin={"kind": "LEGACY_MIGRATED", "operation": "legacy_migration"},
        )

    # Under default policy: BLOCKED
    res_blocked = release_check(store)
    assert res_blocked["release_eligibility"] == ReleaseEligibility.BLOCKED.value
    assert any("HISTORICAL_ADJUDICATION_NOT_CURRENT" in r for r in res_blocked["block_reasons"])

    # Now record properly authorized current adjudication
    record_adjudication(
        store,
        carol_token,
        subject_id="CLAIM_G5",
        subject_revision=1,
        assessment_kind="semantic_entailment",
        verdict="SUPPORTED",
        rationale="Contemporary scientific review verifies validity.",
        cited_evidence=[{"object_id": "RES_G5", "revision": 1}],
        is_independent=True,
    )

    # Now ELIGIBLE under default policy!
    res_eligible = release_check(store)
    assert res_eligible["release_eligibility"] == ReleaseEligibility.ELIGIBLE.value
    assert res_eligible["adjudications_summary"]["CURRENT_AUTHORIZED"] == 1
    assert res_eligible["adjudications_summary"]["LEGACY_HISTORICAL"] == 1


def test_g6_preservation_workflow(tmp_path: Path):
    """G6: Legacy migration -> Authorized claim revision -> Repeat migration -> no reversal of legitimate claim revision."""
    legacy_dir = tmp_path / "legacy_src"
    m = legacy_dir / "00_master"
    m.mkdir(parents=True)
    (m / "research_questions.csv").write_text("rq_id,rq_text\nRQ_G6,Preservation inquiry?\n", encoding="utf-8")
    (m / "proposition_registry.csv").write_text("proposition_id,proposition_text\nPROP_G6,Original claim text.\n", encoding="utf-8")

    store, owner_token = StateStore.create(tmp_path / "prj_g6", "PRJ_G6", owner_id="owner_alice")
    bob_token = register_actor(store, owner_token, actor_id="author_bob", roles=["author", "importer"])["token"]

    # Initial migration
    m1 = migrate_legacy_project(legacy_dir, store, token=owner_token)
    assert m1["status"] in ("PASS", "PASS_WITH_WARNINGS")

    snap = export_snapshot(store, token=owner_token)
    claim_id = [o["object_id"] for o in snap["objects"] if o["object_type"] == "Claim"][0]

    # Authorized revision by researcher Bob
    ops = [
        {
            "operation": "revise_object",
            "object_id": claim_id,
            "base_revision": 1,
            "content": {"text": "Authorized researcher modification", "claim_tier": "descriptive"},
            "relations": [],
        }
    ]
    prop = make_proposal(store.project_id, snap, "PROP_G6_REV", ops)
    rec = import_proposal(store, json.dumps(prop), bob_token)
    assert rec["decision"] == "ACCEPTED"

    # Repeat migration must not reverse Bob's revision
    m2 = migrate_legacy_project(legacy_dir, store, token=owner_token)
    with store.read() as conn:
        head = store.get_object(claim_id, conn=conn)
        assert head["head_revision"] == 2
        assert head["content"]["text"] == "Authorized researcher modification"
