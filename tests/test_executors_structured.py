"""Phase 3A.1: the five executors decide from structure (fields, relations, flags), never from wording."""
from __future__ import annotations

import json

import pytest

from research_harness.state import workflow_runs
from research_harness.state.store import StateStore
from research_harness.workflows.dispatcher import run_workflow, run_workflow_sequence


@pytest.fixture
def proj(tmp_path):
    return StateStore.create(tmp_path / "home", "PRJ_EXEC")


def add(proj, *objs):
    store, token = proj
    with store.write_tx(token=token) as tx:
        for oid, otype, content, edges in objs:
            tx.add_revision(object_id=oid, object_type=otype, content=content, origin={}, edges=list(edges))


def dep(target, rel="depends_on"):
    return (rel, target, 1, {})


RQ = ("RQ_A", "ResearchQuestion", {"text": "Does board independence reduce restatements?"}, [])


def hyp(oid="CLAIM_1", direction="negative", tier="associational", **extra):
    return (oid, "Claim", {"text": "H1: independence reduces restatements.", "claim_tier": tier,
                           "predicted_direction": direction, **extra}, [dep("RQ_A")])


def res(oid="RES_1", summary="Independence coefficient.", edges=None, **fields):
    return (oid, "Result", {"summary": summary, **fields}, edges if edges is not None else [dep("RQ_A")])


def go(proj, wf, **kw):
    kw.setdefault("research_question_id", "RQ_A")
    kw.setdefault("reuse_cached", False)
    return run_workflow(wf, proj[0], **kw)


GOOD = dict(hypothesis_ids=["CLAIM_1"], estimate=-0.12, p_value=0.003, alpha=0.05)


# ==================================================================================================
# Discussion
# ==================================================================================================

def test_discussion_consistent_result_does_not_reopen(proj):
    add(proj, RQ, hyp(), res(**GOOD))
    out = go(proj, "discussion")
    r = out.result
    assert r["reopens_contribution_workflow"] is False and r["contradictions"] == []
    assert r["statistical_classifications"][0]["classification"] == "CONSISTENT"
    assert "No revision indicated for CLAIM_1" in r["revised_contribution_statements"][0]
    assert out.status == "REQUIRES_VERIFICATION"  # interpretation is never certified by the gate


def test_discussion_significant_opposite_sign_reopens_with_its_basis(proj):
    add(proj, RQ, hyp(), res(hypothesis_ids=["CLAIM_1"], estimate=0.2, p_value=0.001, alpha=0.05))
    r = go(proj, "discussion").result
    assert r["reopens_contribution_workflow"] is True
    c = r["contradictions"][0]
    assert (c["claim_id"], c["reconciliation_status"], c["basis"]["kind"]) == ("CLAIM_1", "CHALLENGES_CLAIM", "classification")
    assert c["basis"]["estimate"] == 0.2 and r["reopened_claims"] == {"CLAIM_1": ["RES_1"]}
    assert r["revised_contribution_statements"][0].startswith("REOPEN CLAIM_1@1")


def test_discussion_inconclusive_and_unclassifiable_results_do_not_reopen_and_say_why(proj):
    add(proj, RQ, hyp(), res("RES_N", hypothesis_ids=["CLAIM_1"], estimate=0.01, p_value=0.41, alpha=0.05),
        res("RES_M", "Only prose.", hypothesis_ids=["CLAIM_1"]))
    r = go(proj, "discussion").result
    assert r["reopens_contribution_workflow"] is False
    notes = " | ".join(r["unresolved_questions"])
    assert "RES_N@1 is inconclusive for CLAIM_1" in notes and "RES_M@1 cannot be classified against CLAIM_1: missing" in notes
    assert "NOT_ASSESSED" in r["revised_contribution_statements"][0]


def test_discussion_no_effect_prediction_is_challenged_by_a_significant_effect(proj):
    add(proj, RQ, hyp(direction="no_effect"), res(**GOOD))
    assert go(proj, "discussion").result["reopens_contribution_workflow"] is True


def test_discussion_reopens_on_an_explicit_author_flag_or_a_challenges_relation(proj):
    add(proj, RQ, hyp(), res("RES_FLAG", "Plain text.", contradictory=True),
        res("RES_REL", "Other plain text.", edges=[dep("RQ_A"), ("challenges", "CLAIM_1", 1, {})]),
        res("RES_NONE", "No flag, no link."))
    r = go(proj, "discussion").result
    by_result = {c["result_id"]: c["reconciliation_status"] for c in r["contradictions"]}
    assert by_result == {"RES_FLAG": "FLAGGED_BY_AUTHOR_AS_CONTRADICTORY", "RES_REL": "LINKED_AS_CHALLENGE"}


def test_discussion_results_are_linked_by_relation_when_no_hypothesis_ids(proj):
    add(proj, RQ, hyp(), res(estimate=0.3, p_value=0.001, alpha=0.05, edges=[dep("RQ_A"), ("tests", "CLAIM_1", 1, {})]))
    c = go(proj, "discussion").result["statistical_classifications"][0]
    assert (c["claim_id"], c["classification"], c["linked_by"]) == ("CLAIM_1", "CHALLENGES", "relation:tests")


def test_discussion_passage_challenging_a_claim_reopens_but_its_wording_does_not(proj):
    add(proj, RQ, hyp(),
        ("EP_WORDS", "EvidencePassage", {"locator": "p.1", "quote": "This contradicts and refutes everything; fails."}, [dep("RQ_A")]),
        ("EP_LINK", "EvidencePassage", {"locator": "p.2", "quote": "A neutral sentence."}, [dep("RQ_A"), ("challenges", "CLAIM_1", 1, {})]))
    r = go(proj, "discussion").result
    assert [c["literature_passage_id"] for c in r["contradictions"]] == ["EP_LINK"]
    assert {f["object_id"] for f in r["review_flags"]} == {"EP_WORDS"}  # flagged for a reader, nothing more


def test_review_flags_never_change_a_classification_a_status_or_what_is_reopened(proj, tmp_path):
    """I1 as a property: swapping every text field for trigger-word text changes no decision."""
    cues = "contradicts refutes fails negative null zero regression persist however only when not significant opposite"
    outcomes = []
    for i, text in enumerate(("A plain description.", cues, "")):
        p = StateStore.create(tmp_path / f"h{i}", "PRJ_FUZZ")
        add(p, RQ, ("CLAIM_1", "Claim", {"text": text or "x", "claim_tier": "associational",
            "predicted_direction": "negative"}, [dep("RQ_A")]),
            res("RES_A", text, **GOOD), res("RES_B", text, hypothesis_ids=["CLAIM_1"], estimate=0.1, p_value=0.5, alpha=0.05),
            ("EP_1", "EvidencePassage", {"locator": "p.1", "quote": text or "x"}, [dep("RQ_A")]))
        out = go(p, "discussion")
        r = out.result
        outcomes.append((out.status, r["reopens_contribution_workflow"], [(c["result_id"], c["classification"]) for c in r["statistical_classifications"]],
                         sorted(r["reopened_claims"])))
    assert outcomes[0] == outcomes[1] == outcomes[2]


def test_discussion_alternatives_are_unverified_mechanisms_and_a_planned_analysis_never_counts(proj):
    add(proj, RQ, hyp(), res(**GOOD),
        ("MECH_SEL", "Mechanism", {"label": "Selection of better firms"}, [dep("RQ_A")]),
        ("AN_PLAN", "Analysis", {"status": "PLANNED", "purpose": "robustness", "addresses": ["MECH_SEL"]}, [dep("RQ_A")]))
    alt = go(proj, "discussion").result["alternative_explanations"]
    assert [(a["rival_explanation"], a["verification_status"]) for a in alt] == [("Selection of better firms", "NOT_VERIFIED")]


def test_discussion_layers_are_kept_apart_and_theory_is_not_interpreted(proj):
    add(proj, RQ, hyp(), res(**GOOD), ("TH_1", "Theory", {"label": "Monitoring theory"}, [dep("RQ_A")]))
    r = go(proj, "discussion").result
    layer = r["supported_interpretations"][0]
    assert layer["direct_data_observation"] == "Independence coefficient."
    assert layer["statistical_inference"][0]["classification"] == "CONSISTENT"
    assert layer["theoretical_interpretation"].startswith("NOT_ASSESSED") and layer["speculation"] == []
    assert r["theoretical_implications"] == ["NOT_ASSESSED: whether theory 'Monitoring theory' is supported, bounded or challenged by the findings requires interpretation"]


def test_discussion_without_results_is_insufficient_evidence(proj):
    add(proj, RQ, hyp())
    out = go(proj, "discussion")
    assert out.status == "INSUFFICIENT_EVIDENCE" and out.axes.assessment.value == "NOT_ASSESSED"


def test_discussion_reads_structured_records_from_an_upstream_results_run(proj):
    add(proj, RQ, hyp(), res(**GOOD))
    results_run = go(proj, "results")
    assert results_run.result["structured_run_records"][0]["structured"]["estimate"] == -0.12
    # the Results run is the only source of the finding here: drop the live result from the discussion's view
    add(proj, res("RES_2", "Late result.", hypothesis_ids=["CLAIM_1"], estimate=0.4, p_value=0.01, alpha=0.05))
    outs = run_workflow_sequence(proj[0], ["results", "discussion"], research_question_id="RQ_A", reuse_cached=False)
    classes = {(c["result_id"], c["classification"]) for c in outs[1].result["statistical_classifications"]}
    assert classes == {("RES_1", "CONSISTENT"), ("RES_2", "CHALLENGES")}
    assert outs[1].result["upstream_artifacts_consumed"]


# ==================================================================================================
# Literature
# ==================================================================================================

def test_literature_relationship_comes_from_relations_not_wording(proj):
    add(proj, RQ, hyp(),
        ("SRC_1", "Source", {"citation_key": "A2020", "abstract": "a"}, [dep("RQ_A")]),
        ("EP_SUP", "EvidencePassage", {"locator": "p.1", "quote": "This contradicts nothing and fails to refute."},
         [dep("RQ_A"), ("derived_from", "SRC_1", 1, {}), ("supports", "CLAIM_1", 1, {})]),
        ("EP_CHAL", "EvidencePassage", {"locator": "p.2", "quote": "Consistent with the hypothesis, as predicted."},
         [dep("RQ_A"), ("derived_from", "SRC_1", 1, {}), ("challenges", "CLAIM_1", 1, {})]),
        ("EP_NONE", "EvidencePassage", {"locator": "p.3", "quote": "Confirms and supports and validates."}, [dep("RQ_A")]))
    r = go(proj, "literature").result
    rel = {row["passage_id"]: row["relationship"] for row in r["evidence_matrix"]}
    assert rel == {"EP_SUP": "SUPPORTS_CLAIM", "EP_CHAL": "CHALLENGES_CLAIM", "EP_NONE": "NOT_ASSESSED"}
    assert [d["status"] for d in r["unresolved_disagreements"]] == ["UNRESOLVED_EMPIRICAL_DISAGREEMENT"]
    assert len(r["supported_findings"]) == 1 and r["supported_findings"][0].startswith("[A2020, p.1]")
    assert next(x for x in r["evidence_matrix"] if x["passage_id"] == "EP_CHAL")["evidence_conflict"] is True


def test_literature_synthesis_does_not_claim_agreement_or_disagreement(proj):
    add(proj, RQ, ("SRC_1", "Source", {"citation_key": "A", "abstract": "a"}, [dep("RQ_A")]),
        ("EP_1", "EvidencePassage", {"locator": "p.1", "quote": "Lower."}, [dep("RQ_A"), ("derived_from", "SRC_1", 1, {})]),
        ("EP_2", "EvidencePassage", {"locator": "p.2", "quote": "Higher."}, [dep("RQ_A"), ("derived_from", "SRC_1", 1, {})]))
    r = go(proj, "literature").result
    assert "has not been assessed" in r["theoretical_synthesis"] and "NOT_ASSESSED" in r["theoretical_synthesis"]
    assert r["supported_findings"] == [] and r["unresolved_disagreements"] == []


def test_literature_method_difference_matrix_shows_where_studies_differ_and_what_is_unreported(proj):
    add(proj, RQ,
        ("SRC_1", "Source", {"citation_key": "A", "abstract": "a", "study_design": "Archival panel", "sample_context": "US", "time_period": "2000-2010"}, [dep("RQ_A")]),
        ("SRC_2", "Source", {"citation_key": "B", "abstract": "b", "study_design": "Lab experiment", "sample_context": "US"}, [dep("RQ_A")]),
        ("SRC_3", "Source", {"citation_key": "C", "abstract": "c"}, [dep("RQ_A")]))
    m = {row["field"]: row for row in go(proj, "literature").result["method_difference_matrix"]}
    assert m["study_design"]["differs"] and m["study_design"]["not_reported_in"] == ["C"]
    assert not m["sample_context"]["differs"] and m["sample_context"]["not_reported_in"] == ["C"]
    assert not m["time_period"]["differs"] and sorted(m["time_period"]["not_reported_in"]) == ["B", "C"]


PAPER = "Introduction\nWe study X.\n\nData\nCompustat.\n\nRobustness Checks\nResults hold.\n\nLimitations\nSelf-selection is possible.\n"


def test_literature_locates_the_sections_that_qualify_a_finding(proj):
    add(proj, RQ, ("SRC_F", "Source", {"citation_key": "F", "abstract": "a", "full_text": PAPER}, [dep("RQ_A")]))
    loaded = go(proj, "literature", fulltext_policy="always").result["sections_requiring_review"]
    assert [(s["label"], s["text_state"]) for s in loaded] == [("robustness", "LOADED"), ("limitations", "LOADED")]
    assert PAPER[loaded[1]["char_start"]:loaded[1]["char_end"]].startswith("Limitations")
    deferred = go(proj, "literature", fulltext_policy="always", max_input_tokens=2600).result["sections_requiring_review"]
    assert {s["text_state"] for s in deferred} <= {"LOADED", "NOT_LOADED"} and {s["label"] for s in deferred} == {"robustness", "limitations"}


def test_literature_statuses(proj):
    add(proj, RQ)
    assert go(proj, "literature").status == "INSUFFICIENT_EVIDENCE"
    add(proj, ("SRC_1", "Source", {"citation_key": "A", "abstract": "a", "full_text": PAPER}, [dep("RQ_A")]),
        ("EP_1", "EvidencePassage", {"locator": "p.1", "quote": "x"}, [dep("RQ_A"), ("derived_from", "SRC_1", 1, {})]))
    assert go(proj, "literature").status == "REQUIRES_VERIFICATION"  # full text not loaded, passage unassessed
    add(proj, hyp())
    add(proj, ("EP_1", "EvidencePassage", {"locator": "p.1", "quote": "x"}, [dep("RQ_A"), ("derived_from", "SRC_1", 1, {}), ("supports", "CLAIM_1", 1, {})]))
    assert go(proj, "literature", fulltext_policy="always").status == "SUCCESS"  # every gate satisfied; still not a verdict


# ==================================================================================================
# Contribution
# ==================================================================================================

def _ident_project(proj, *, adjudication=True, analysis=True, scope="identification"):
    objs = [RQ, hyp(tier="causal")]
    if analysis:
        objs.append(("AN_1", "Analysis", {"identification_strategy": "Difference-in-differences", "status": "EXECUTED"},
                     [dep("RQ_A"), ("tests", "CLAIM_1", 1, {})]))
    add(proj, *objs)
    if adjudication:
        add(proj, ("ADJ_1", "Adjudication", {"subject": {"object_id": "CLAIM_1"}, "scope": scope, "verdict": "SUPPORTED", "rationale": "r"}, []))


@pytest.mark.parametrize("kwargs,expected", [
    (dict(), "IDENTIFIED_BY_VERIFIED_DESIGN"),
    (dict(adjudication=False), "IDENTIFICATION_NOT_VERIFIED"),
    (dict(analysis=False), "IDENTIFICATION_NOT_VERIFIED"),
    (dict(scope="claim_support"), "IDENTIFICATION_NOT_VERIFIED"),
])
def test_contribution_identification_needs_both_an_adjudication_and_a_design(proj, kwargs, expected):
    _ident_project(proj, **kwargs)
    assert go(proj, "contribution", focus_claims=["CLAIM_1"]).result["identification_status"] == expected


def test_contribution_non_causal_claims_do_not_need_identification(proj):
    add(proj, RQ, hyp(tier="associational"))
    r = go(proj, "contribution").result
    assert r["candidate_contributions"][0]["identification_status"] == "IDENTIFICATION_NOT_REQUIRED_FOR_TIER"


def test_contribution_novelty_states(proj):
    add(proj, RQ, hyp())
    assert go(proj, "contribution").result["novelty_status"] == "NOVELTY_NOT_VERIFIED"  # no papers != novelty
    add(proj, ("SRC_1", "Source", {"citation_key": "A", "abstract": "a", "full_text": "Body."}, [dep("RQ_A")]))
    assert go(proj, "contribution").result["novelty_status"] == "NOVELTY_NOT_VERIFIED"  # full text not read
    assert go(proj, "contribution", fulltext_policy="always").result["novelty_status"] == "NOVELTY_PENDING_REVIEW"
    add(proj, hyp(), ("SRC_1", "Source", {"citation_key": "A", "abstract": "a", "full_text": "Body."}, [dep("RQ_A"), ("challenges", "CLAIM_1", 1, {})]))
    r = go(proj, "contribution", fulltext_policy="always").result
    assert r["novelty_status"] == "NOVELTY_CONTESTED" and r["closest_competing_contributions"][0]["relation_to_claim"] == "challenges"
    assert r["candidate_contributions"][0]["challenged_by_sources"] == ["SRC_1"]


def test_contribution_a_challenging_passage_contests_novelty_through_its_source(proj):
    add(proj, RQ, hyp(), ("SRC_1", "Source", {"citation_key": "A", "abstract": "a"}, [dep("RQ_A")]),
        ("EP_1", "EvidencePassage", {"locator": "p.1", "quote": "Already shown."}, [dep("RQ_A"), ("derived_from", "SRC_1", 1, {}), ("challenges", "CLAIM_1", 1, {})]))
    assert go(proj, "contribution").result["novelty_status"] == "NOVELTY_CONTESTED"


def test_contribution_evidence_is_classified_by_structure_and_the_rest_is_unclassified(proj):
    add(proj, RQ, hyp(), res("RES_OK", **GOOD), res("RES_BAD", hypothesis_ids=["CLAIM_1"], estimate=0.5, p_value=0.001, alpha=0.05),
        res("RES_INC", hypothesis_ids=["CLAIM_1"], estimate=0.01, p_value=0.4, alpha=0.05), res("RES_FREE", "Unlinked prose."),
        ("EP_NEUTRAL", "EvidencePassage", {"locator": "p.1", "quote": "Contradicts fails refutes."}, [dep("RQ_A")]))
    r = go(proj, "contribution").result
    ids = lambda items: sorted(e.get("result_id") or e.get("passage_id") for e in items)  # noqa: E731
    assert ids(r["supporting_evidence"]) == ["RES_OK"] and ids(r["contradicting_evidence"]) == ["RES_BAD"]
    assert ids(r["unclassified_evidence"]) == ["EP_NEUTRAL", "RES_FREE", "RES_INC"]


def test_contribution_lists_every_rival_mechanism_and_keeps_own_claims_apart(proj):
    add(proj, RQ, hyp(), ("CLAIM_2", "Claim", {"text": "H2.", "claim_tier": "associational"}, [dep("RQ_A")]),
        ("M1", "Mechanism", {"label": "Monitoring"}, [dep("RQ_A")]), ("M2", "Mechanism", {"label": "Selection"}, [dep("RQ_A")]))
    r = go(proj, "contribution", focus_claims=["CLAIM_1"]).result
    assert r["candidate_contributions"][0]["rival_mechanisms"] == ["Monitoring", "Selection"]
    assert [c["claim_id"] for c in r["related_project_claims"]] == ["CLAIM_2"]
    assert r["closest_competing_contributions"] == []


def test_contribution_without_claims_is_insufficient_and_never_certified(proj):
    add(proj, RQ)
    assert go(proj, "contribution").status == "INSUFFICIENT_EVIDENCE"
    add(proj, hyp())
    out = go(proj, "contribution")
    assert out.status == "REQUIRES_VERIFICATION" and out.result["is_preliminary"] is True
    assert "Not a verified contribution." in out.result["confidence_explanation"]


def test_contribution_takes_discussion_contradictions_from_the_structured_handoff(proj):
    add(proj, RQ, hyp(), res("RES_FLAG", "Prose.", contradictory=True))
    outs = run_workflow_sequence(proj[0], ["discussion", "contribution"], research_question_id="RQ_A", reuse_cached=False)
    r = outs[1].result
    assert r["upstream_artifacts_consumed"] and any("Discussion run" in m for m in r["missing_verification"])
    assert any("reconcile" in a for a in r["alternative_contribution_formulations"])


# ==================================================================================================
# Methods
# ==================================================================================================

def test_methods_proposes_nothing_by_default(proj):
    add(proj, RQ, hyp(tier="causal"))
    r = go(proj, "methods").result
    assert r["candidate_research_designs"] == [] and r["specification_plans"] == [] and r["alternative_methods"] == []
    assert r["diagnostic_tests"] == [] and r["assumptions"] == []
    assert any("none is proposed by default" in m for m in r["limitations_and_unresolved_choices"])
    blob = json.dumps(r)
    for template in ("Fixed", "Cluster", "clustered", "Leverage", "Difference-in-Differences", "2SLS", "parallel trends"):
        assert template not in blob


def test_methods_reports_what_the_researcher_recorded_and_judges_none_of_it(proj):
    add(proj, RQ, hyp(),
        ("CON_1", "Construct", {"label": "Earnings quality"}, [dep("RQ_A")]),
        ("CON_2", "Construct", {"label": "Board independence"}, [dep("RQ_A")]),
        ("PX_1", "Proxy", {"label": "Absolute accruals", "construct_id": "CON_1"}, [dep("RQ_A")]),
        ("DS_1", "Dataset", {"label": "Panel", "structure": "panel"}, [dep("RQ_A")]),
        ("AN_1", "Analysis", {"label": "Main", "status": "PLANNED", "purpose": "main", "estimator": "OLS",
                              "identification_strategy": "Event study", "assumptions": ["No anticipation"],
                              "specification": {"fixed_effects": ["firm"]}}, [dep("RQ_A"), ("estimated_with", "DS_1", 1, {})]))
    r = go(proj, "methods", candidate_methods=["regression discontinuity"]).result
    designs = {d["design_name"]: d for d in r["candidate_research_designs"]}
    assert set(designs) == {"regression discontinuity", "Event study"}
    assert all(d["justified"] is False and d["justification_status"] == "NOT_ASSESSED" for d in designs.values())
    assert r["assumptions"] == ["AN_1: No anticipation"]
    req = " | ".join(r["identification_or_measurement_requirements"])
    assert "Construct 'Earnings quality' is operationalized by: Absolute accruals" in req
    assert "Construct 'Board independence' has no linked Proxy" in req and "structure panel" in req
    plan = r["specification_plans"][0]
    assert (plan["status"], plan["estimator"], plan["specification"], plan["datasets_used"]) == ("PLANNED", "OLS", {"fixed_effects": ["firm"]}, ["DS_1"])


def test_methods_profile_checklist_is_never_marked_addressed(proj):
    add(proj, RQ)
    r = go(proj, "methods", discipline_profile="accounting").result
    assert r["profile_checklist_results"]["checklist_evaluations"]
    assert {i["compliance_status"] for i in r["profile_checklist_results"]["checklist_evaluations"]} == {"REQUIRES_INFORMATION"}


# ==================================================================================================
# Results
# ==================================================================================================

def _execution_run(proj, payload=b"table"):
    store, _ = proj
    sha = store.artifacts.put(payload)
    run_id = workflow_runs.append_run(store, {
        "workflow": "execution", "input_fingerprint": "fp", "executor": "capture", "artifact_sha256": sha,
        "execution_status": "SUCCEEDED", "context_status": "COMPLETE", "assessment_status": "NOT_ASSESSED",
        "applicability": "APPLICABLE", "legacy_status": "SUCCESS"})
    return run_id, sha


def test_results_verified_only_against_a_real_execution_record(proj):
    run_id, sha = _execution_run(proj)
    add(proj, RQ, hyp(),
        res("RES_OK", "Backed by a run.", run_id=run_id, artifact_sha256=sha, **GOOD),
        res("RES_WRONG_HASH", "Run exists, output differs.", run_id=run_id, artifact_sha256="0" * 64, **GOOD),
        res("RES_FAKE_RUN", "Claims a run that does not exist.", run_id="RUN_NOPE", artifact_sha256=sha, provenance="VERIFIED_RUN"),
        res("RES_AUTHOR", "Reported by the author.", provenance="AUTHOR_REPORTED"),
        res("RES_BARE", "Nothing recorded."))
    out = go(proj, "results")
    ver = {r["result_id"]: r["verification"] for r in out.result["structured_run_records"]}
    assert ver == {"RES_OK": "VERIFIED_RUN", "RES_WRONG_HASH": "UNVERIFIED", "RES_FAKE_RUN": "CLAIMED_VERIFIED_RUN_NOT_FOUND",
                   "RES_AUTHOR": "AUTHOR_REPORTED", "RES_BARE": "UNVERIFIED"}
    assert out.result["findings_supported_by_available_outputs"] == ["[RES_OK:rev1] Backed by a run."]
    assert {f["result_id"] for f in out.result["author_reported_findings"]} == {"RES_WRONG_HASH", "RES_FAKE_RUN", "RES_AUTHOR", "RES_BARE"}
    assert out.result["reproducibility_metadata"]["reproducibility_status"] == "COMPUTATIONAL_REPRODUCTION_NOT_VERIFIED"
    assert out.status == "REQUIRES_VERIFICATION"


def test_results_all_verified_is_success_and_reproducibility_is_verified(proj):
    run_id, sha = _execution_run(proj)
    add(proj, RQ, hyp(), res("RES_OK", run_id=run_id, artifact_sha256=sha, **GOOD))
    out = go(proj, "results")
    assert out.status == "SUCCESS" and out.result["reproducibility_metadata"]["reproducibility_status"] == "VERIFIED_COMPUTATIONAL_RUN"
    assert out.result["reproducibility_metadata"]["deterministic_verification"] is True


def test_results_caller_assertions_are_listed_as_ignored_and_metadata_stays_author_reported(proj):
    add(proj, RQ, res(**GOOD))
    out = go(proj, "results", run_metadata={"verified_computation": True, "random_seed": 7, "script_path": "a.py"})
    repro = out.result["reproducibility_metadata"]
    assert repro["reproducibility_status"] == "COMPUTATIONAL_REPRODUCTION_NOT_VERIFIED" and repro["deterministic_verification"] is False
    assert (repro["random_seed"], repro["script_path"], repro["estimator"]) == (7, "a.py", None)
    assert repro["metadata_source"] == "author_reported_run_metadata"
    assert out.result["ignored_assertions"] == ["run_metadata.verified_computation: asserted by the caller but not accepted without an execution record"]


def test_results_revision_comparison_shows_fields_and_which_claims_changed_status(proj):
    add(proj, RQ, hyp(), res("RES_1", "First.", **GOOD))
    add(proj, res("RES_1", "Second, with firm fixed effects.", hypothesis_ids=["CLAIM_1"], estimate=-0.02, p_value=0.4, alpha=0.05))
    r = go(proj, "results").result
    cmp = r["result_comparisons"][0]
    paths = {c["path"] for c in cmp["structured_changes"]}
    assert (cmp["prior_revision"], cmp["head_revision"]) == (1, 2) and {"$.structured.estimate", "$.structured.p_value"} <= paths
    ch = r["changed_results_and_affected_claims"][0]
    assert ch["affected_claims"] == ["CLAIM_1"] and ch["status"] == "REQUIRES_REASSESSMENT"
    assert ch["claim_status_changes"] == [{"claim_id": "CLAIM_1", "classification_before": "CONSISTENT",
                                           "classification_after": "INCONCLUSIVE", "classification_changed": True}]


def test_results_isolated_revision_and_robustness_listing(proj):
    add(proj, RQ, res("RES_1", "v1", analysis_id="AN_R"), ("AN_R", "Analysis", {"purpose": "robustness", "status": "EXECUTED"}, [dep("RQ_A")]))
    add(proj, res("RES_1", "v2", analysis_id="AN_R"))
    r = go(proj, "results").result
    assert r["changed_results_and_affected_claims"][0]["status"] == "RESOLVED_ISOLATED"
    assert r["robustness_summaries"] == [{"analysis_id": "AN_R", "purpose": "robustness", "status": "EXECUTED", "results": ["RES_1"]}]


def test_results_with_nothing_recorded_is_insufficient(proj):
    add(proj, RQ)
    assert go(proj, "results").status == "INSUFFICIENT_EVIDENCE"
