"""Tests for Claim-Proportional Scientific Release Policy (Phase A4).

Validates the 12 required test scenarios:
1. Empty project blocked.
2. Unsupported claim blocked.
3. Claim with unrelated source blocked.
4. Required adjudication missing -> blocked.
5. Required adjudication unresolved -> blocked.
6. Rejected claim blocked.
7. Unreviewed claim blocked when review is required.
8. Stale dependent evidence blocked until appropriately resolved.
9. Properly supported descriptive claim evaluated under descriptive policy.
10. Properly supported predictive claim evaluated under predictive policy.
11. Causal claim not automatically accepted using only descriptive evidence.
12. Fully compliant project eligible.
"""
from __future__ import annotations

import json
import pytest

from research_harness.core.auth import authenticate
from research_harness.core.model import ReleaseEligibility, ReviewStatus, WorkflowStatus
from research_harness.proposals.gateway import import_proposal
from research_harness.protocols.snapshot import export_snapshot
from research_harness.validation.integrity import release_check
from research_harness.validation.review import record_adjudication, record_review
from tests.conftest import make_proposal


def test_empty_project_blocked(harness_env):
    store = harness_env["store"]
    res = release_check(store)
    assert res["release_eligibility"] == ReleaseEligibility.BLOCKED.value
    assert res["workflow_status"] == WorkflowStatus.INCOMPLETE.value
    assert any("missing core scientific objects" in r for r in res["block_reasons"])


def test_unsupported_claim_blocked(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]
    carol_token = harness_env["actors"]["carol"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap = export_snapshot(store, actor, scope="full")

    # Propose RQ and an isolated Claim with no evidence relations (only depends on RQ)
    ops = [
        {
            "operation": "propose_object",
            "proposed_id": "RQ_UNSUP_01",
            "object_type": "ResearchQuestion",
            "content": {"label": "RQ", "text": "Does modularity help?"},
            "relations": [],
        },
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_UNSUP_01",
            "claim_tier": "descriptive",
            "text": "Modularity exists in codebase.",
            "depends_on": ["RQ_UNSUP_01"],
            "relations": [{"relation": "depends_on", "target_id": "RQ_UNSUP_01"}],
        },
    ]
    prop = make_proposal(store.project_id, snap, "PROP_UNSUP", ops)
    rec = import_proposal(store, json.dumps(prop), bob_token)
    assert rec["decision"] == "ACCEPTED"

    # Approve the claim
    record_review(store, carol_token, object_id="CLAIM_UNSUP_01", revision=1,
                  kind="independent_review", decision="approve", rationale="Looks good")

    # Release check must block: Claim has no linked evidence!
    res = release_check(store)
    assert res["release_eligibility"] == ReleaseEligibility.BLOCKED.value
    assert any("CLAIM_UNSUPPORTED" in r for r in res["block_reasons"])


def test_claim_with_unrelated_source_blocked(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]
    carol_token = harness_env["actors"]["carol"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap = export_snapshot(store, actor, scope="full")

    # Propose RQ, a generic Source, and a Claim that doesn't reference the Source
    ops = [
        {
            "operation": "propose_object",
            "proposed_id": "RQ_UNRELATED_01",
            "object_type": "ResearchQuestion",
            "content": {"label": "RQ", "text": "Unrelated inquiry"},
            "relations": [],
        },
        {
            "operation": "propose_object",
            "proposed_id": "SRC_UNRELATED_01",
            "object_type": "Source",
            "content": {"citation_key": "Knuth1984", "bibliographic_identity": "Literate Programming"},
            "relations": [],
        },
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_UNRELATED_01",
            "claim_tier": "descriptive",
            "text": "Code readability reduces bug incidence.",
            "depends_on": ["RQ_UNRELATED_01"],
            "relations": [{"relation": "depends_on", "target_id": "RQ_UNRELATED_01"}],
        },
    ]
    prop = make_proposal(store.project_id, snap, "PROP_UNRELATED", ops)
    rec = import_proposal(store, json.dumps(prop), bob_token)
    assert rec["decision"] == "ACCEPTED"

    record_review(store, carol_token, object_id="CLAIM_UNRELATED_01", revision=1,
                  kind="independent_review", decision="approve", rationale="Approved")

    res = release_check(store)
    assert res["release_eligibility"] == ReleaseEligibility.BLOCKED.value
    assert any("CLAIM_UNSUPPORTED" in r for r in res["block_reasons"])


def test_required_adjudication_missing_blocked(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]
    carol_token = harness_env["actors"]["carol"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap = export_snapshot(store, actor, scope="full")

    ops = [
        {
            "operation": "propose_object",
            "proposed_id": "RQ_ADJ_01",
            "object_type": "ResearchQuestion",
            "content": {"label": "RQ", "text": "Adjudication requirement check"},
            "relations": [],
        },
        {
            "operation": "propose_object",
            "proposed_id": "SRC_ADJ_01",
            "object_type": "Source",
            "content": {"citation_key": "SourceRef", "bibliographic_identity": "Source Identity"},
            "relations": [],
        },
        {
            "operation": "propose_object",
            "proposed_id": "EP_ADJ_01",
            "object_type": "EvidencePassage",
            "content": {"locator": "p. 50", "normalized_claim": "Passage text"},
            "relations": [{"relation": "derived_from", "target_id": "SRC_ADJ_01"}],
        },
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_ADJ_01",
            "claim_tier": "descriptive",
            "text": "Descriptive claim from passage",
            "depends_on": ["EP_ADJ_01"],
            "relations": [
                {"relation": "depends_on", "target_id": "RQ_ADJ_01"},
                {"relation": "depends_on", "target_id": "EP_ADJ_01"},
            ],
        },
    ]
    prop = make_proposal(store.project_id, snap, "PROP_ADJ_MISSING", ops)
    rec = import_proposal(store, json.dumps(prop), bob_token)
    assert rec["decision"] == "ACCEPTED"

    record_review(store, carol_token, object_id="CLAIM_ADJ_01", revision=1,
                  kind="independent_review", decision="approve", rationale="Approved")

    # Evaluate under policy requiring adjudication
    policy = {"require_adjudication": True}
    res = release_check(store, policy=policy)
    assert res["release_eligibility"] == ReleaseEligibility.BLOCKED.value
    assert any("REQUIRED_ADJUDICATION_MISSING" in r for r in res["block_reasons"])


def test_required_adjudication_unresolved_blocked(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]
    carol_token = harness_env["actors"]["carol"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap = export_snapshot(store, actor, scope="full")

    ops = [
        {
            "operation": "propose_object",
            "proposed_id": "RQ_ADJ_02",
            "object_type": "ResearchQuestion",
            "content": {"label": "RQ", "text": "Unresolved adjudication test"},
            "relations": [],
        },
        {
            "operation": "propose_object",
            "proposed_id": "SRC_ADJ_02",
            "object_type": "Source",
            "content": {"citation_key": "SourceRef2", "bibliographic_identity": "Source Identity 2"},
            "relations": [],
        },
        {
            "operation": "propose_object",
            "proposed_id": "EP_ADJ_02",
            "object_type": "EvidencePassage",
            "content": {"locator": "p. 100", "normalized_claim": "Passage text 2"},
            "relations": [{"relation": "derived_from", "target_id": "SRC_ADJ_02"}],
        },
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_ADJ_02",
            "claim_tier": "descriptive",
            "text": "Descriptive claim with unclear adjudication",
            "depends_on": ["EP_ADJ_02"],
            "relations": [
                {"relation": "depends_on", "target_id": "RQ_ADJ_02"},
                {"relation": "depends_on", "target_id": "EP_ADJ_02"},
            ],
        },
    ]
    prop = make_proposal(store.project_id, snap, "PROP_ADJ_UNCLEAR", ops)
    rec = import_proposal(store, json.dumps(prop), bob_token)
    assert rec["decision"] == "ACCEPTED"

    record_adjudication(
        store,
        carol_token,
        object_id="CLAIM_ADJ_02",
        revision=1,
        verdict="UNCLEAR",
        rationale="Evidence text is ambiguous regarding claim scope",
        assessment_kind="semantic_entailment",
    )

    record_review(store, carol_token, object_id="CLAIM_ADJ_02", revision=1,
                  kind="independent_review", decision="approve", rationale="Approved")

    res = release_check(store)
    assert res["release_eligibility"] == ReleaseEligibility.BLOCKED.value
    assert res["semantic_adjudication_status"] == "UNCLEAR"
    assert any("ADJUDICATION_UNRESOLVED" in r for r in res["block_reasons"])


def test_rejected_claim_blocked(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]
    carol_token = harness_env["actors"]["carol"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap = export_snapshot(store, actor, scope="full")

    ops = [
        {
            "operation": "propose_object",
            "proposed_id": "RQ_REJ_01",
            "object_type": "ResearchQuestion",
            "content": {"label": "RQ", "text": "Rejection test"},
            "relations": [],
        },
        {
            "operation": "propose_object",
            "proposed_id": "RES_REJ_01",
            "object_type": "Result",
            "content": {"label": "Result", "summary": "Negative result"},
            "relations": [],
        },
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_REJ_01",
            "claim_tier": "associational",
            "text": "Refactoring associates with higher defects.",
            "depends_on": ["RES_REJ_01"],
            "relations": [
                {"relation": "depends_on", "target_id": "RQ_REJ_01"},
                {"relation": "depends_on", "target_id": "RES_REJ_01"},
            ],
        },
    ]
    prop = make_proposal(store.project_id, snap, "PROP_REJ", ops)
    rec = import_proposal(store, json.dumps(prop), bob_token)
    assert rec["decision"] == "ACCEPTED"

    # Carol REJECTS the claim
    record_review(store, carol_token, object_id="CLAIM_REJ_01", revision=1,
                  kind="independent_review", decision="reject", rationale="Methodological flaws in measurement")

    res = release_check(store)
    assert res["release_eligibility"] == ReleaseEligibility.BLOCKED.value
    assert res["scientific_review_status"] == ReviewStatus.REJECTED.value
    assert any("CONTAINS_REJECTED_CLAIMS" in r for r in res["block_reasons"])


def test_unreviewed_claim_blocked(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap = export_snapshot(store, actor, scope="full")

    ops = [
        {
            "operation": "propose_object",
            "proposed_id": "RQ_UNREV_01",
            "object_type": "ResearchQuestion",
            "content": {"label": "RQ", "text": "Unreviewed test"},
            "relations": [],
        },
        {
            "operation": "propose_object",
            "proposed_id": "RES_UNREV_01",
            "object_type": "Result",
            "content": {"label": "Result", "summary": "Summary"},
            "relations": [],
        },
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_UNREV_01",
            "claim_tier": "associational",
            "text": "Unreviewed claim.",
            "depends_on": ["RES_UNREV_01"],
            "relations": [
                {"relation": "depends_on", "target_id": "RQ_UNREV_01"},
                {"relation": "depends_on", "target_id": "RES_UNREV_01"},
            ],
        },
    ]
    prop = make_proposal(store.project_id, snap, "PROP_UNREV", ops)
    rec = import_proposal(store, json.dumps(prop), bob_token)
    assert rec["decision"] == "ACCEPTED"

    # Do not record any review -> Claim starts UNASSESSED
    res = release_check(store)
    assert res["release_eligibility"] == ReleaseEligibility.BLOCKED.value
    assert res["scientific_review_status"] == ReviewStatus.UNASSESSED.value
    assert any("CLAIMS_UNASSESSED" in r for r in res["block_reasons"])


def test_stale_dependent_evidence_blocked(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]
    carol_token = harness_env["actors"]["carol"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap0 = export_snapshot(store, actor, scope="full")

    ops1 = [
        {
            "operation": "propose_object",
            "proposed_id": "RQ_STALE_01",
            "object_type": "ResearchQuestion",
            "content": {"label": "RQ", "text": "Stale test"},
            "relations": [],
        },
        {
            "operation": "propose_object",
            "proposed_id": "RES_STALE_01",
            "object_type": "Result",
            "content": {"label": "Result v1", "summary": "Initial results"},
            "relations": [],
        },
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_STALE_01",
            "claim_tier": "associational",
            "text": "Claim depends on result v1.",
            "depends_on": ["RES_STALE_01"],
            "relations": [
                {"relation": "depends_on", "target_id": "RQ_STALE_01"},
                {"relation": "depends_on", "target_id": "RES_STALE_01"},
            ],
        },
    ]
    prop1 = make_proposal(store.project_id, snap0, "PROP_STALE_1", ops1)
    rec1 = import_proposal(store, json.dumps(prop1), bob_token)
    assert rec1["decision"] == "ACCEPTED"

    record_review(store, carol_token, object_id="CLAIM_STALE_01", revision=1,
                  kind="independent_review", decision="approve", rationale="Approved v1")

    # Now revise RES_STALE_01 to revision 2!
    snap1 = export_snapshot(store, actor, scope="full")
    ops2 = [
        {
            "operation": "revise_object",
            "object_id": "RES_STALE_01",
            "base_revision": 1,
            "content": {"label": "Result v2", "summary": "Completely updated results changing effect size"},
            "relations": [],
        }
    ]
    prop2 = make_proposal(store.project_id, snap1, "PROP_STALE_2", ops2)
    rec2 = import_proposal(store, json.dumps(prop2), bob_token)
    assert rec2["decision"] == "ACCEPTED"

    # Claim CLAIM_STALE_01 now points to superseded revision 1 of RES_STALE_01
    res = release_check(store)
    assert res["release_eligibility"] == ReleaseEligibility.BLOCKED.value
    assert any("STALE_DEPENDENCIES_PINNED" in r or "OPEN_INVALIDATIONS_EXIST" in r for r in res["block_reasons"])


def test_properly_supported_descriptive_claim_eligible(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]
    carol_token = harness_env["actors"]["carol"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap = export_snapshot(store, actor, scope="full")

    # Descriptive research: Literature question, source, evidence passage, and descriptive claim
    ops = [
        {
            "operation": "propose_object",
            "proposed_id": "RQ_DESC_01",
            "object_type": "ResearchQuestion",
            "content": {"label": "Literature RQ", "text": "What is the historical evolution of research harnesses?"},
            "relations": [],
        },
        {
            "operation": "propose_object",
            "proposed_id": "SRC_DESC_01",
            "object_type": "Source",
            "content": {"citation_key": "Harness2020", "bibliographic_identity": "Harness Evolution Study"},
            "relations": [],
        },
        {
            "operation": "propose_object",
            "proposed_id": "EP_DESC_01",
            "object_type": "EvidencePassage",
            "content": {"locator": "sec. 3.2", "normalized_claim": "Harness versions progressed from v1 to v8"},
            "relations": [{"relation": "derived_from", "target_id": "SRC_DESC_01"}],
        },
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_DESC_01",
            "claim_tier": "descriptive",
            "text": "Research harnesses have evolved systematically across eight generations.",
            "depends_on": ["EP_DESC_01"],
            "relations": [
                {"relation": "depends_on", "target_id": "RQ_DESC_01"},
                {"relation": "depends_on", "target_id": "EP_DESC_01"},
            ],
        },
    ]
    prop = make_proposal(store.project_id, snap, "PROP_DESC", ops)
    rec = import_proposal(store, json.dumps(prop), bob_token)
    assert rec["decision"] == "ACCEPTED"

    record_review(store, carol_token, object_id="CLAIM_DESC_01", revision=1,
                  kind="independent_review", decision="approve", rationale="Grounded in evidence passage.")

    res = release_check(store)
    assert res["integrity_status"] == "PASS"
    assert res["workflow_status"] == "COMPLETE"
    assert res["scientific_review_status"] == ReviewStatus.INDEPENDENTLY_REVIEWED.value
    assert res["release_eligibility"] == ReleaseEligibility.ELIGIBLE.value
    assert not res["block_reasons"]


def test_properly_supported_predictive_claim_eligible(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]
    carol_token = harness_env["actors"]["carol"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap = export_snapshot(store, actor, scope="full")

    ops = [
        {
            "operation": "propose_object",
            "proposed_id": "RQ_PRED_01",
            "object_type": "ResearchQuestion",
            "content": {"label": "Prediction RQ", "text": "Can test suite metrics predict production incidents?"},
            "relations": [],
        },
        {
            "operation": "propose_object",
            "proposed_id": "RES_PRED_01",
            "object_type": "Result",
            "content": {"label": "Out-of-sample Result", "summary": "Out-of-sample ROC AUC = 0.88 with Brier score 0.09"},
            "relations": [],
        },
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_PRED_01",
            "claim_tier": "predictive",
            "text": "Flaky test density predicts defect rates in next release.",
            "depends_on": ["RES_PRED_01"],
            "relations": [
                {"relation": "depends_on", "target_id": "RQ_PRED_01"},
                {"relation": "depends_on", "target_id": "RES_PRED_01"},
            ],
        },
    ]
    prop = make_proposal(store.project_id, snap, "PROP_PRED", ops)
    rec = import_proposal(store, json.dumps(prop), bob_token)
    assert rec["decision"] == "ACCEPTED"

    record_review(store, carol_token, object_id="CLAIM_PRED_01", revision=1,
                  kind="independent_review", decision="approve", rationale="Predictive metrics validated out-of-sample.")

    res = release_check(store)
    assert res["release_eligibility"] == ReleaseEligibility.ELIGIBLE.value
    assert not res["block_reasons"]


def test_causal_claim_rejected_with_only_descriptive_evidence(harness_env):
    """Causal claim cannot be automatically accepted using only descriptive evidence."""
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]
    carol_token = harness_env["actors"]["carol"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap = export_snapshot(store, actor, scope="full")

    # A CAUSAL claim supported ONLY by an unadjudicated descriptive EvidencePassage
    ops = [
        {
            "operation": "propose_object",
            "proposed_id": "RQ_CAUSAL_FAIL_01",
            "object_type": "ResearchQuestion",
            "content": {"label": "Causal RQ", "text": "Does code review cause defect reduction?"},
            "relations": [],
        },
        {
            "operation": "propose_object",
            "proposed_id": "SRC_CAUSAL_01",
            "object_type": "Source",
            "content": {"citation_key": "ReviewSurvey2022", "bibliographic_identity": "Survey on Code Review"},
            "relations": [],
        },
        {
            "operation": "propose_object",
            "proposed_id": "EP_CAUSAL_DESC_01",
            "object_type": "EvidencePassage",
            "content": {"locator": "p. 15", "normalized_claim": "Survey respondents reported fewer bugs after reviews"},
            "relations": [{"relation": "derived_from", "target_id": "SRC_CAUSAL_01"}],
        },
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_CAUSAL_FAIL_01",
            "claim_tier": "causal",  # Causal tier!
            "text": "Code reviews causally reduce bugs by 40%.",
            "depends_on": ["EP_CAUSAL_DESC_01"],
            "relations": [
                {"relation": "depends_on", "target_id": "RQ_CAUSAL_FAIL_01"},
                {"relation": "depends_on", "target_id": "EP_CAUSAL_DESC_01"},
            ],
        },
    ]
    prop = make_proposal(store.project_id, snap, "PROP_CAUSAL_FAIL", ops)
    rec = import_proposal(store, json.dumps(prop), bob_token)
    assert rec["decision"] == "ACCEPTED"

    record_review(store, carol_token, object_id="CLAIM_CAUSAL_FAIL_01", revision=1,
                  kind="independent_review", decision="approve", rationale="Approved")

    res = release_check(store)
    # MUST BE BLOCKED: causal claim lacks empirical identification or verified adjudication
    assert res["release_eligibility"] == ReleaseEligibility.BLOCKED.value
    assert any("CAUSAL_CLAIM_LACKS_EMPIRICAL_OR_ADJUDICATED_SUPPORT" in r for r in res["block_reasons"])


def test_fully_compliant_project_eligible(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]
    carol_token = harness_env["actors"]["carol"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap = export_snapshot(store, actor, scope="full")

    # Complete causal project with empirical result and valid causal identification
    ops = [
        {
            "operation": "propose_object",
            "proposed_id": "RQ_COMPLIANT_01",
            "object_type": "ResearchQuestion",
            "content": {"label": "Compliant RQ", "text": "Does automated verification improve stability?"},
            "relations": [],
        },
        {
            "operation": "propose_object",
            "proposed_id": "RES_COMPLIANT_01",
            "object_type": "Result",
            "content": {"label": "Empirical Result", "summary": "Randomized control trial shows p < 0.001 causal effect"},
            "relations": [],
        },
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_COMPLIANT_01",
            "claim_tier": "causal",
            "text": "Automated verification causally improves system stability.",
            "depends_on": ["RES_COMPLIANT_01"],
            "relations": [
                {"relation": "depends_on", "target_id": "RQ_COMPLIANT_01"},
                {"relation": "depends_on", "target_id": "RES_COMPLIANT_01"},
            ],
        },
    ]
    prop = make_proposal(store.project_id, snap, "PROP_COMPLIANT", ops)
    rec = import_proposal(store, json.dumps(prop), bob_token)
    assert rec["decision"] == "ACCEPTED"

    record_review(store, carol_token, object_id="CLAIM_COMPLIANT_01", revision=1,
                  kind="independent_review", decision="approve", rationale="Verified with RCT design.")

    res = release_check(store)
    assert res["integrity_status"] == "PASS"
    assert res["structural_integrity_status"] == "PASS"
    assert res["workflow_status"] == "COMPLETE"
    assert res["scientific_review_status"] == ReviewStatus.INDEPENDENTLY_REVIEWED.value
    assert res["release_eligibility"] == ReleaseEligibility.ELIGIBLE.value
    assert not res["block_reasons"]
