"""WP6.1 remediation, P0: Methods and Results in the inquiry contracts. Not estimating is not having no method; a stage is a capability group, not a manuscript section.

Synthetic acceptance only (invented papers, scripted models).
"""
from __future__ import annotations

import copy

import pytest

from research_harness.discovery import landscape as L
from research_harness.discovery import problem as P
from research_harness.knowledge import review_protocol as RP
from research_harness.state.store import StateStore
from research_harness.workflows.domain_engine import (DomainWorkflowSpec, ProjectFacts, evaluate, facts_from_store, load_builtin_spec, record_step_run, stage_status, suggest_contracts)
from research_harness.workflows.dispatcher import run_workflow
from research_harness.workflows.status import Applicability
from tests import inquiry_fixtures as F
from tests import remediation_fixtures as RF


def promoted(w, text="How does AI-assisted communication separate participation from relational engagement at work?"):
    P.add_interpretation(w["store"], w["token"], w["pid"], "AI assistance separates producing a message from investing in a relationship.")
    P.choose(w["store"], w["token"], w["pid"], ["I1"], "the reading pursued")
    return P.promote_to_rq(w["store"], w["token"], w["pid"], "I1", text, "my wording")["research_question"]


def steps(spec, w, rq):
    return {e.step_id: e for e in evaluate(spec, facts_from_store(w["store"], research_question_id=rq))}


def add_objects(store, token, *objs):
    with store.write_tx(token=token) as tx:
        for oid, otype, content in objs:
            tx.add_revision(object_id=oid, object_type=otype, content=content, origin={}, edges=[])


@pytest.fixture
def wa(tmp_path):
    return F.build_world(tmp_path, name="PRJ_A", papers=F.A_PAPERS, statement=F.A_STATEMENT, terms=F.A_TERMS, inquiry=F.A_INQUIRY)


# ================================================================================================ a critical review has a method and findings, and no estimation

def test_a_critical_review_has_a_method_that_is_declared_checked_and_reported(wa):
    spec = load_builtin_spec("LIB_DW_CRITICAL_SYNTHESIS")
    RF.adopt_protocol(wa)
    wa["lid"] = RF.build_landscape(wa)
    rq = promoted(wa)
    ev = steps(spec, wa, rq)
    method = {k: ev[k] for k in ("search_strategy", "screening_and_selection", "appraisal", "extraction_protocol", "synthesis_protocol")}
    assert all(m.applicability is Applicability.APPLICABLE or k == "appraisal" for k, m in method.items())
    assert [k for k, m in method.items() if m.done and m.completion == "DECLARED_IN_RECORD"] == ["search_strategy", "screening_and_selection", "extraction_protocol", "synthesis_protocol"]
    assert method["appraisal"].applicability is Applicability.NOT_APPLICABLE and "no appraisal is performed" in method["appraisal"].applicability_reason     # the protocol says so, and why
    out = run_workflow("methods", wa["store"], research_question_id=rq, domain_workflow=spec, reuse_cached=False)
    assert out.status == "SUCCESS" and out.result["pending"] == [] and out.result["completion_kinds"]["DECLARED_IN_RECORD"] == 4 and out.result["completion_kinds"]["RECORDED_BY_RESEARCHER"] == 0
    assert out.result["activity"].startswith("the review method") and "where the genre has one" in out.result["manuscript"] and out.usage.llm_calls == 0
    assert not any(k in " ".join(s["step"] for s in out.result["steps"]) for k in ("estimat", "regress", "identif", "robust"))
    # the work of reading is a capability's: the landscape exists, and the steps it completes say so
    assert ev["reconstruct_theories"].completion == "EXECUTED_BY_CAPABILITY" and not ev["reconstruct_theories"].out_of_order and ev["reconstruct_constructs_assumptions"].done
    lit = run_workflow("literature", wa["store"], research_question_id=rq, domain_workflow=spec, reuse_cached=False)
    assert lit.status == "INSUFFICIENT_EVIDENCE" and lit.result["pending"] == ["compare_theories_and_scopes"]


def test_a_plan_that_is_only_a_draft_does_not_count_as_a_declared_method(wa):
    spec = load_builtin_spec("LIB_DW_CRITICAL_SYNTHESIS")
    rq = promoted(wa)
    RF.adopt_protocol(wa, adopt=False)
    ev = steps(spec, wa, rq)
    assert not any(ev[k].done for k in ("search_strategy", "screening_and_selection", "extraction_protocol", "synthesis_protocol"))
    out = run_workflow("methods", wa["store"], research_question_id=rq, domain_workflow=spec, reuse_cached=False)
    assert out.status == "INSUFFICIENT_EVIDENCE" and "search_strategy" in out.result["pending"]
    assert ev["search_strategy"].runnable and ev["screening_and_selection"].blocked_by                                                 # the first step can be done; nothing after it can


def test_a_step_recorded_by_the_researcher_is_not_a_step_a_capability_or_a_checked_record_completed(wa):
    spec = load_builtin_spec("LIB_DW_CRITICAL_SYNTHESIS")
    rq = promoted(wa)
    for step in ("search_strategy", "screening_and_selection", "appraisal", "extraction_protocol", "synthesis_protocol"):
        record_step_run(wa["store"], spec, step)
    ev = steps(spec, wa, rq)
    assert all(ev[k].done and ev[k].completion == "RECORDED_BY_RESEARCHER" for k in ("search_strategy", "screening_and_selection", "extraction_protocol", "synthesis_protocol"))
    out = run_workflow("methods", wa["store"], research_question_id=rq, domain_workflow=spec, reuse_cached=False)
    assert out.status == "REQUIRES_VERIFICATION" and len(out.result["done_by_assertion_only"]) == 5 and out.result["completion_kinds"]["DECLARED_IN_RECORD"] == 0
    RF.adopt_protocol(wa)                                                      # the same steps, now backed by an adopted, checked record: the stronger label wins
    ev = steps(spec, wa, rq)
    assert ev["search_strategy"].completion == "DECLARED_IN_RECORD" and ev["appraisal"].applicability is Applicability.NOT_APPLICABLE
    assert run_workflow("methods", wa["store"], research_question_id=rq, domain_workflow=spec, reuse_cached=False).status == "SUCCESS"


def test_findings_of_a_critical_review_are_a_conceptual_reconstruction_a_tension_and_a_synthesis(wa):
    spec = load_builtin_spec("LIB_DW_CRITICAL_SYNTHESIS")
    RF.adopt_protocol(wa)
    wa["lid"] = RF.build_landscape(wa)
    rq = promoted(wa)
    record_step_run(wa["store"], spec, "compare_theories_and_scopes")
    res = run_workflow("results", wa["store"], research_question_id=rq, domain_workflow=spec, reuse_cached=False)
    assert res.status == "INSUFFICIENT_EVIDENCE" and res.result["pending"] == ["identify_tensions", "inspect_recognition", "interpretive_synthesis"]
    assert res.result["activity"].startswith("the findings of the synthesis") and "may have no Results heading" in res.result["manuscript"]
    L.review_inference(wa["store"], wa["token"], wa["lid"], "I001", "DISMISSED", "an observation about wording only")
    add_objects(wa["store"], wa["token"], ("RES_MAP", "Result", {"label": "Conceptual map", "summary": "participation, attention and engagement placed by level", "attributes": {"result_kind": "conceptual_map"}}))
    ev = steps(spec, wa, rq)
    assert ev["identify_tensions"].completion == "DECLARED_IN_RECORD" and ev["interpretive_synthesis"].done and not ev["inspect_recognition"].done
    assert ev["inspect_recognition"].entry_met and ev["inspect_recognition"].runnable                                                  # it needs an inspection record, not a coefficient


# ================================================================================================ qualitative: findings without coefficients

def test_qualitative_inquiry_has_findings_that_are_themes_traceable_to_material_and_no_coefficients(tmp_path):
    w = F.build_world(tmp_path, name="PRJ_Q", papers=F.B_PAPERS, statement=F.B_STATEMENT, terms=F.B_TERMS,
                      inquiry={"research_purposes": ["interpretation"], "research_design": "design_based", "evidence_forms": ["qualitative_interpretation"],
                               "discipline": {"domain": "education", "subfield": "design_based_research"}})
    rq = promoted(w, "How do students monitor and revise work they delegate to an agentic tool?")
    spec = load_builtin_spec("LIB_DW_QUALITATIVE_INQUIRY")
    for step in ("sampling_rationale", "data_generation_protocol", "coding_and_interpretation", "alternative_interpretations", "credibility_checks"):
        record_step_run(w["store"], spec, step)
    res0 = run_workflow("results", w["store"], research_question_id=rq, domain_workflow=spec, reuse_cached=False)
    assert res0.status == "INSUFFICIENT_EVIDENCE" and res0.result["pending"] == ["findings", "findings_trace", "design_principles"] and res0.status != "NOT_APPLICABLE"
    add_objects(w["store"], w["token"],
                ("RES_T1", "Result", {"label": "Checking by execution", "summary": "Students judged agent output by whether it ran.", "attributes": {"result_kind": "theme"}}),
                ("RES_T2", "Result", {"label": "Specification stability", "summary": "Most students did not revise the task after first delegation.", "attributes": {"result_kind": "pattern"}}),
                ("RES_P1", "Result", {"label": "Make checking visible", "summary": "Design principle: make the learner's checking a visible part of the product.", "attributes": {"result_kind": "design_principle"}}),
                ("EP_EXCERPT", "EvidencePassage", {"label": "Excerpt", "locator": "interview 4, turn 12", "quote": "I just ran it and when it ran I figured it was fine.", "quote_verified": True}))
    record_step_run(w["store"], spec, "findings_trace")
    ev = steps(spec, w, rq)
    assert ev["findings"].completion == "DECLARED_IN_RECORD" and ev["design_principles"].completion == "DECLARED_IN_RECORD" and ev["findings_trace"].completion == "RECORDED_BY_RESEARCHER"
    res = run_workflow("results", w["store"], research_question_id=rq, domain_workflow=spec, reuse_cached=False)
    assert res.status == "REQUIRES_VERIFICATION" and res.result["done_by_assertion_only"] == ["findings_trace"]                    # the trace of findings to material is the researcher's assertion
    assert "no coefficients are implied" in res.result["manuscript"]
    with w["store"].read() as conn:
        types = {r[0] for r in conn.execute("SELECT object_type FROM object_heads").fetchall()}
    assert "Dataset" not in types and "Analysis" not in types


def test_a_qualitative_study_that_is_not_design_based_has_no_design_principles_step_and_says_why(tmp_path):
    spec = load_builtin_spec("LIB_DW_QUALITATIVE_INQUIRY")
    f = ProjectFacts(study_design="qualitative_interview")
    f.research_purpose = "interpretation"
    ev = {e.step_id: e for e in evaluate(spec, f)}
    assert ev["design_principles"].applicability is Applicability.NOT_APPLICABLE and "only design-based research" in ev["design_principles"].applicability_reason
    assert [k for k, e in ev.items() if e.applicability is Applicability.NOT_APPLICABLE] == ["design_principles"]
    g = ProjectFacts(study_design="design_based")
    g.research_purpose = "interpretation"
    assert [k for k, e in {e.step_id: e for e in evaluate(spec, g)}.items() if e.applicability is Applicability.NOT_APPLICABLE] == []


# ================================================================================================ conceptual: a framework is a product

def test_a_conceptual_paper_presents_propositions_and_a_framework_and_is_not_missing_empirical_results(tmp_path):
    w = F.build_world(tmp_path, name="PRJ_C0", papers=F.B_PAPERS, statement=F.B_STATEMENT, terms=F.B_TERMS, inquiry=F.B_INQUIRY)
    rq = promoted(w, "What can an artifact produced with an agentic tool support an inference about?")
    spec = load_builtin_spec("LIB_DW_CONCEPTUAL_ARGUMENT")
    add_objects(w["store"], w["token"], ("CL_1", "Claim", {"text": "A completed artifact does not by itself support an inference about evaluative judgement.", "claim_tier": "conceptual"}))
    ev = steps(spec, w, rq)
    assert ev["state_argument"].completion == "DECLARED_IN_RECORD" and not ev["propositions_and_framework"].done
    add_objects(w["store"], w["token"], ("CL_2", "Claim", {"text": "Assessment of agentic work needs evidence of how the learner checked delegated output.", "claim_tier": "conceptual"}),
                ("RES_CHAIN", "Result", {"label": "Inference chain", "summary": "premises, steps and conclusion", "attributes": {"result_kind": "inference_chain"}}))
    for step in ("rival_explanations", "boundary_conditions", "implications_and_limits"):
        record_step_run(w["store"], spec, step)
    ev = steps(spec, w, rq)
    assert ev["propositions_and_framework"].completion == "DECLARED_IN_RECORD" and ev["reconstruct_inference_chain"].completion == "DECLARED_IN_RECORD"
    out = run_workflow("results", w["store"], research_question_id=rq, domain_workflow=spec, reuse_cached=False)
    assert out.status == "REQUIRES_VERIFICATION" and out.result["pending"] == [] and out.result["done_by_assertion_only"] == ["boundary_conditions"]
    assert "no empirical Results section is implied" in out.result["manuscript"]
    assert all(e.applicability is Applicability.APPLICABLE for e in ev.values())                                                  # nothing in a conceptual contract is 'not applicable'
    meth = run_workflow("methods", w["store"], research_question_id=rq, domain_workflow=spec, reuse_cached=False)
    assert meth.status == "REQUIRES_VERIFICATION" and meth.result["done_by_assertion_only"] == ["rival_explanations"]


# ================================================================================================ the empirical workflow keeps its gates

def test_the_archival_fraud_workflow_keeps_every_gate_and_gained_no_derived_completion(tmp_path):
    fraud = load_builtin_spec("LIB_DW_ACC_FRAUD_DETECTION")
    assert fraud.pin == "LIB_DW_ACC_FRAUD_DETECTION@0.1.0#13aa49ba3271f63b" and all(s.completed_by is None for s in fraud.steps) and all(st.assessed_by == "skill" for st in fraud.stages.values())
    w = F.build_world(tmp_path, name="PRJ_C", papers=F.C_PAPERS, statement=F.C_STATEMENT, terms=F.C_TERMS, inquiry=F.C_INQUIRY)
    rq = promoted(w, "How does the evaluation split change the measured performance of fraud detection models?")
    results = run_workflow("results", w["store"], research_question_id=rq, domain_workflow=fraud, reuse_cached=False)
    assert results.status == "BLOCKED" and "model_comparison_protocol" in results.result["unmet_entry_conditions"][0]
    ev = steps(fraud, w, rq)
    assert not ev["label_provenance"].runnable and any("label_source" in b for b in ev["label_provenance"].blocked_by)
    assert [k for k, e in ev.items() if e.runnable] == ["contribution_assessment", "literature_assessment"]
    # the stage that does run is the empirical skill, not a contract report
    meth = run_workflow("methods", w["store"], research_question_id=rq, domain_workflow=fraud, reuse_cached=False)
    assert meth.status == "REQUIRES_VERIFICATION" and "steps" not in meth.result and "candidate_research_designs" in meth.result


# ================================================================================================ NOT_APPLICABLE only when the function is not part of the design

def test_not_applicable_is_used_only_where_the_function_is_not_part_of_the_inquiry():
    for sid, design, purpose in (("LIB_DW_CRITICAL_SYNTHESIS", "systematic_review", "critical_synthesis"), ("LIB_DW_CONCEPTUAL_ARGUMENT", "conceptual", "theory_development"),
                                 ("LIB_DW_QUALITATIVE_INQUIRY", "design_based", "interpretation")):
        spec = load_builtin_spec(sid)
        f = ProjectFacts(study_design=design)
        f.research_purpose = purpose
        assert {st: stage_status(spec, f, st).applicability for st in spec.stages} == {st: Applicability.APPLICABLE for st in spec.stages}, sid
        assert [k for k, e in {e.step_id: e for e in evaluate(spec, f)}.items() if e.applicability is Applicability.NOT_APPLICABLE] == [], sid
        other = ProjectFacts(study_design="predictive_modeling")
        other.research_purpose = "prediction"
        assert all(stage_status(spec, other, st).applicability is Applicability.NOT_APPLICABLE for st in spec.stages)             # a design the contract does not cover
        assert not any(st.applicable is False for st in spec.stages.values())                                                    # no stage is declared inapplicable by the contract itself
        assert all(st.activity for k, st in spec.stages.items() if k != "contribution")
        assert DomainWorkflowSpec.from_dict(spec.to_dict()) == spec


def test_a_critical_review_whose_protocol_performs_an_appraisal_has_that_step(wa):
    spec = load_builtin_spec("LIB_DW_CRITICAL_SYNTHESIS")
    rq = promoted(wa)
    plan = copy.deepcopy(RF.PROTOCOL_A)
    plan["appraisal"] = {"performed": True, "approach": "each included study is read for the fit between its claim and its design", "criteria": ["fit of design to claim", "transparency of reasoning"]}
    RP.set_review_protocol(wa["store"], wa["token"], wa["pid"], plan, "an appraisal is performed")
    RP.record_screening(wa["store"], wa["token"], wa["pid"], RF.screening_log(), "first pass")
    RP.adopt_review_protocol(wa["store"], wa["token"], wa["pid"], "adopted")
    ev = steps(spec, wa, rq)
    assert ev["appraisal"].applicability is Applicability.APPLICABLE and ev["appraisal"].completion == "DECLARED_IN_RECORD"


# ================================================================================================ choosing a contract is the researcher's

def test_the_suite_suggests_contracts_for_the_recorded_inquiry_and_never_chooses_for_the_researcher(tmp_path, wa):
    s = suggest_contracts(facts_from_store(wa["store"], problem_id=wa["pid"]))
    assert s["status"] == "CANDIDATES_FOUND" and s["candidates"] == ["LIB_DW_CRITICAL_SYNTHESIS"]
    assert {r["spec"]: r["fit"] for r in s["contracts"]}["LIB_DW_GENERIC"] == "NO_GUIDANCE" and {r["spec"]: r["fit"] for r in s["contracts"]}["LIB_DW_ACC_FRAUD_DETECTION"] == "DOES_NOT_FIT"
    none = F.build_world(tmp_path / "n", name="PRJ_N", papers=F.A_PAPERS, statement=F.A_STATEMENT, terms=F.A_TERMS)
    s0 = suggest_contracts(facts_from_store(none["store"], problem_id=none["pid"]))
    assert s0["status"] == "INQUIRY_NOT_RECORDED" and s0["candidates"] == [] and "record the inquiry" in s0["note"]
    odd = F.build_world(tmp_path / "o", name="PRJ_O", papers=F.A_PAPERS, statement=F.A_STATEMENT, terms=F.A_TERMS, inquiry={"research_purposes": ["description"], "research_design": "survey"})
    so = suggest_contracts(facts_from_store(odd["store"], problem_id=odd["pid"]))
    assert so["status"] == "RESEARCHER_DECISION_REQUIRED" and so["candidates"] == [] and all(r["fit"] in ("DOES_NOT_FIT", "NO_GUIDANCE") for r in so["contracts"])
    c = F.build_world(tmp_path / "c", name="PRJ_CC", papers=F.C_PAPERS, statement=F.C_STATEMENT, terms=F.C_TERMS, inquiry=F.C_INQUIRY)
    assert suggest_contracts(facts_from_store(c["store"], problem_id=c["pid"]))["candidates"] == ["LIB_DW_ACC_FRAUD_DETECTION"]
    only_design = F.build_world(tmp_path / "d", name="PRJ_D", papers=F.A_PAPERS, statement=F.A_STATEMENT, terms=F.A_TERMS, inquiry={"research_design": "conceptual"})
    sd = suggest_contracts(facts_from_store(only_design["store"], problem_id=only_design["pid"]))
    assert sd["candidates"] == [] and {r["spec"]: r["fit"] for r in sd["contracts"]}["LIB_DW_CONCEPTUAL_ARGUMENT"] == "INQUIRY_INCOMPLETE"      # a design alone does not say what the work is for


def test_specs_that_use_none_of_the_new_fields_keep_the_digest_they_had_before_so_every_stored_pin_still_resolves():
    """Compatibility: the optional fields added in the remediation (activity, manuscript, assessed_by, completed_by, review_component) are absent from the dict of a spec that does not use them.
    The digests below were computed with the engine of b631b64 (before the remediation) from the same spec files."""
    import json
    from pathlib import Path

    from research_harness.workflows.domain_engine import DomainWorkflowSpec

    root = Path(__file__).resolve().parents[1] / "research_harness" / "workflows" / "domain_specs"
    before = {"generic": "02fc4faaefb4f38842e5a70f010cb21798a2ca2b2349549ccb76363dee4ad91c", "acc_fraud_detection": "13aa49ba3271f63b15e6fd021ac7523910c45b99e9d785edb906a480f4cabdbb"}
    for name, digest in before.items():
        spec = DomainWorkflowSpec.from_dict(json.loads((root / f"{name}.json").read_text(encoding="utf-8")))
        assert spec.digest() == digest, name
