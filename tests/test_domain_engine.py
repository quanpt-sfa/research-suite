"""Phase 3A.1b: domain workflows as execution contracts (invariant I18).

The decisive test is `test_domain_workflows_differ_in_execution_not_prompt`: two workflows with *identical* stage
instructions behave differently on the same project - in which steps can run, what blocks the others, which stages
apply and where results send the work back.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from research_harness import taxonomy
from research_harness.cli import main as cli_main
from research_harness.state.store import StateStore
from research_harness.workflows.adapters import ScriptedModelAdapter
from research_harness.workflows.domain_engine import (
    GENERIC_SPEC_ID,
    STAGES,
    DomainWorkflowSpec,
    ProjectFacts,
    SpecError,
    StepState,
    evaluate,
    evaluate_predicate,
    explain,
    facts_from_store,
    list_builtin_specs,
    load_builtin_spec,
    next_steps,
    record_step_run,
    resolve_spec,
    stage_status,
)
from research_harness.workflows.dispatcher import run_workflow
from research_harness.workflows.model_io import output_schema
from research_harness.workflows.runs import get_workflow_run, list_workflow_runs
from research_harness.workflows.status import Applicability, ExecutionStatus

FIXTURE = Path(__file__).parent / "fixtures" / "dw_edu_qualitative_fixture.json"
SHARED = {s: f"Shared stage instruction for {s}." for s in STAGES}


def spec_dict(which: str) -> dict:
    if which == "fraud":
        from research_harness.workflows import domain_engine
        data = load_builtin_spec("LIB_DW_ACC_FRAUD_DETECTION").to_dict()
    else:
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return copy.deepcopy(data)


def with_shared_instructions(data: dict) -> DomainWorkflowSpec:
    for s in STAGES:
        data["stages"][s]["instructions"] = SHARED[s]
    return DomainWorkflowSpec.from_dict(data)


@pytest.fixture
def proj(tmp_path):
    return StateStore.create(tmp_path / "home", "PRJ_DOM")


def add(proj, *objs):
    store, token = proj
    with store.write_tx(token=token) as tx:
        for oid, otype, content, edges in objs:
            tx.add_revision(object_id=oid, object_type=otype, content=content, origin={}, edges=list(edges))


RQ = ("RQ_A", "ResearchQuestion", {"text": "Can fraud be predicted from financial statements?"}, [])


def facts(objects=(), study_design=None, steps=None, scope=None) -> ProjectFacts:
    return ProjectFacts(objects=[{"object_id": f"O{i}", "object_type": t, "revision": 1, "content": c} for i, (t, c) in enumerate(objects)],
                        study_design=study_design, scope=scope or {}, steps=steps or {})


def ok(pred, f):
    return evaluate_predicate(pred, f).ok


# ==================================================================================================
# spec validation: the language is closed
# ==================================================================================================

def test_builtin_specs_load_and_validate():
    assert list_builtin_specs() == ["LIB_DW_ACC_FRAUD_DETECTION", "LIB_DW_CONCEPTUAL_ARGUMENT", "LIB_DW_CRITICAL_SYNTHESIS", "LIB_DW_GENERIC", "LIB_DW_QUALITATIVE_INQUIRY"]
    generic = load_builtin_spec(GENERIC_SPEC_ID)
    assert [s.id for s in generic.steps] == list(STAGES) and generic.study_designs == ()
    pilot = load_builtin_spec("LIB_DW_ACC_FRAUD_DETECTION")
    assert pilot.validation_status == "EXTRACTED" and pilot.pin.startswith("LIB_DW_ACC_FRAUD_DETECTION@0.1.0#")
    assert DomainWorkflowSpec.from_dict(pilot.to_dict()) == pilot  # round trip


@pytest.mark.parametrize("mutate,message", [
    (lambda d: d["steps"][2].update(entry_conditions=[{"eval": "1+1"}]), "unknown predicate 'eval'"),
    (lambda d: d["steps"][2].update(entry_conditions=[{"exists": {"type": "Dataset"}, "all": []}]), "one-key object"),
    (lambda d: d["steps"][2].update(entry_conditions=[{"step_execution": {"step": "no_such_step", "is": "SUCCEEDED"}}]), "unknown step 'no_such_step'"),
    (lambda d: d["steps"][2].update(entry_conditions=[{"step_assessment": {"step": "label_provenance", "at_least": "GREAT"}}]), "at_least"),
    (lambda d: d["steps"][2].update(entry_conditions=[{"diagnostic_signal": {"step": "leakage_check", "diagnostic": "x", "is": "BAD"}}]), "diagnostic_signal"),
    (lambda d: d["steps"][2].update(entry_conditions=[{"study_design_in": ["alchemy"]}]), "study_design"),
    (lambda d: d["steps"][2].update(entry_conditions=[{"count_ge": {"type": "Dataset", "n": "two"}}]), "integer"),
    (lambda d: d["steps"][8]["on_condition"][0].update(goto="nowhere"), "goto 'nowhere'"),
    (lambda d: d["steps"][8]["on_condition"][0].pop("reason"), "reason"),
    (lambda d: d["steps"].append(dict(d["steps"][0])), "duplicate step ids"),
    (lambda d: d["steps"][0].update(stage="analysis"), "unknown stage"),
    (lambda d: d["steps"][0].update(kind="improvise"), "unknown kind"),
    (lambda d: d["stages"]["results"].update(applicable=False), "justification is required"),
    (lambda d: d.update(subfield="asset_pricing"), "does not belong to domain"),
    (lambda d: d.update(domain="astrology"), "Unknown domain"),
])
def test_malformed_specs_are_rejected(mutate, message):
    data = spec_dict("fraud")
    mutate(data)
    with pytest.raises(SpecError, match=message):
        DomainWorkflowSpec.from_dict(data)


def test_a_step_that_is_not_applicable_must_say_why():
    data = spec_dict("edu")
    next(s for s in data["steps"] if s["id"] == "statistical_inference_steps")["applicability"].pop("justification")
    with pytest.raises(SpecError, match="justification"):
        DomainWorkflowSpec.from_dict(data)


# ==================================================================================================
# predicates
# ==================================================================================================

def test_object_predicates():
    f = facts([("Dataset", {"data_role": "label_source", "tags": ["fraud", "sec"]}), ("Dataset", {"data_role": "sample"}), ("Claim", {})])
    assert ok({"exists": {"type": "Dataset", "where": {"data_role": "label_source"}}}, f)
    assert ok({"exists": {"type": "Dataset", "where": {"tags": "sec"}}}, f)  # a list field matches by membership
    assert not ok({"exists": {"type": "Dataset", "where": {"data_role": "nope"}}}, f)
    assert ok({"count_ge": {"type": "Dataset", "n": 2}}, f) and not ok({"count_ge": {"type": "Dataset", "n": 3}}, f)
    assert ok({"field_in": {"type": "Dataset", "field": "data_role", "values": ["sample", "x"]}}, f)
    assert "no Dataset with {'data_role': 'nope'}" in evaluate_predicate({"exists": {"type": "Dataset", "where": {"data_role": "nope"}}}, f).reason


def test_study_design_and_scope_predicates():
    f = facts(study_design="predictive_modeling", scope={"jurisdiction": "US", "period": "2000-2020"})
    assert ok({"study_design_in": ["archival", "predictive_modeling"]}, f) and not ok({"study_design_in": ["archival"]}, f)
    assert not ok({"study_design_in": ["archival"]}, facts())  # unrecorded design satisfies nothing
    assert ok({"scope_matches": {"data_regime": "US"}}, facts(scope={"data_regime": "US"}))
    assert not ok({"scope_matches": {"data_regime": ["EU", "UK"]}}, facts(scope={"data_regime": "US"}))
    bad = spec_dict("fraud")
    bad["stages"]["methods"] = {"applicable": {"scope_matches": {"vibes": "x"}}}
    with pytest.raises(SpecError, match="scope_dimension"):  # the scope dimension must be in the taxonomy
        DomainWorkflowSpec.from_dict(bad)


def test_step_state_predicates():
    steps = {"a": StepState("SUCCEEDED", "PROVISIONAL", {"d": "CONCERN_INDICATED"}, "R1", 1),
             "b": StepState("FAILED", "NOT_ASSESSED", {}, "R2", 2),
             "c": StepState("SUCCEEDED", "VERIFIED_WITHIN_SCOPE", {}, "R3", 3)}
    f = facts(steps=steps)
    assert ok({"step_execution": {"step": "a", "is": "SUCCEEDED"}}, f) and not ok({"step_execution": {"step": "b", "is": "SUCCEEDED"}}, f)
    assert not ok({"step_execution": {"step": "never_run", "is": "SUCCEEDED"}}, f)
    assert ok({"step_assessment": {"step": "a", "at_least": "PROVISIONAL"}}, f) and not ok({"step_assessment": {"step": "a", "at_least": "VERIFIED_WITHIN_SCOPE"}}, f)
    assert ok({"step_assessment": {"step": "c", "at_least": "PROVISIONAL"}}, f) and not ok({"step_assessment": {"step": "b", "at_least": "PROVISIONAL"}}, f)
    assert ok({"diagnostic_signal": {"step": "a", "diagnostic": "d", "is": "CONCERN_INDICATED"}}, f)
    assert not ok({"diagnostic_signal": {"step": "a", "diagnostic": "d", "is": "NO_CONCERN_INDICATED"}}, f)
    assert not ok({"diagnostic_signal": {"step": "a", "diagnostic": "unrecorded", "is": "CONCERN_INDICATED"}}, f)


def test_combinators():
    f = facts([("Claim", {})])
    yes, no = {"exists": {"type": "Claim"}}, {"exists": {"type": "Source"}}
    assert ok({"all": [yes, yes]}, f) and not ok({"all": [yes, no]}, f)
    assert ok({"any": [no, yes]}, f) and not ok({"any": [no, no]}, f)
    assert ok({"not": no}, f) and not ok({"not": yes}, f)
    assert "no Source" in evaluate_predicate({"all": [yes, no]}, f).reason


# ==================================================================================================
# the decisive test
# ==================================================================================================

def methods_view(spec, f):
    return {e.step_id: e for e in evaluate(spec, f) if e.stage == "methods"}


def test_domain_workflows_differ_in_execution_not_prompt(proj):
    """Identical stage instructions; different behaviour on the same project."""
    fraud, edu = with_shared_instructions(spec_dict("fraud")), with_shared_instructions(spec_dict("edu"))
    assert [fraud.stage_instructions(s) for s in STAGES] == [edu.stage_instructions(s) for s in STAGES] == [SHARED[s] for s in STAGES]

    add(proj, RQ,
        ("DS_L", "Dataset", {"data_role": "label_source", "label": "SEC enforcement releases"}, [("depends_on", "RQ_A", 1, {})]),
        ("EP_L", "EvidencePassage", {"topic": "label_definition", "locator": "p.3", "quote": "Fraud is defined as..."}, [("depends_on", "RQ_A", 1, {})]),
        ("EP_S", "EvidencePassage", {"topic": "sampling_rationale", "locator": "p.5", "quote": "We sampled..."}, [("depends_on", "RQ_A", 1, {})]))
    store, _ = proj
    f_fraud = facts_from_store(store, research_question_id="RQ_A", study_design="predictive_modeling")
    f_edu = facts_from_store(store, research_question_id="RQ_A", study_design="qualitative_interview")

    # 1. different steps, in a different order, with different first moves
    assert [s.id for s in fraud.steps if s.stage == "methods"] != [s.id for s in edu.steps if s.stage == "methods"]
    runnable = lambda spec, f: [e.step_id for e in evaluate(spec, f) if e.stage == "methods" and e.runnable]  # noqa: E731
    assert runnable(fraud, f_fraud) == ["label_provenance"] and runnable(edu, f_edu) == ["sampling_rationale"]

    # 2. different entry conditions: the same recorded facts block different things, for different stated reasons
    fm, em = methods_view(fraud, f_fraud), methods_view(edu, f_edu)
    assert "'label_provenance'" in fm["sample_construction"].blocked_by[0] and "not run" in fm["sample_construction"].blocked_by[0]
    assert "'sampling_rationale'" in em["data_collection_protocol"].blocked_by[0] and "not run" in em["data_collection_protocol"].blocked_by[0]
    assert fm["sample_construction"].blocked_by != em["data_collection_protocol"].blocked_by

    # 3. different evidence requirements: remove each workflow's required passage and only that workflow stops
    only_rq = ProjectFacts(objects=[o for o in f_fraud.objects if o["object_id"] not in {"EP_L", "EP_S"}], study_design="predictive_modeling")
    assert methods_view(fraud, only_rq)["label_provenance"].runnable is False
    assert "needs 1 EvidencePassage with {'topic': 'label_definition'}" in methods_view(fraud, only_rq)["label_provenance"].unmet_evidence[0]
    only_label = ProjectFacts(objects=[o for o in f_fraud.objects if o["object_id"] != "EP_S"], study_design="qualitative_interview")
    assert methods_view(edu, only_label)["sampling_rationale"].runnable is False
    assert methods_view(fraud, ProjectFacts(objects=[o for o in f_fraud.objects if o["object_id"] != "EP_S"], study_design="predictive_modeling"))["label_provenance"].runnable

    # 4. different applicability: a stage and a step that exist (or do not) depending on the design
    assert stage_status(fraud, f_fraud, "results").applicability is Applicability.APPLICABLE
    na = stage_status(edu, f_edu, "results")
    assert na.applicability is Applicability.NOT_APPLICABLE and "no separate statistical results stage" in na.justification
    both = spec_dict("edu")
    both["study_designs"] = ["qualitative_interview", "archival"]  # a workflow that covers two designs
    stat = next(e for e in evaluate(DomainWorkflowSpec.from_dict(both), facts(study_design="archival")) if e.step_id == "statistical_inference_steps")
    assert stat.applicability is Applicability.APPLICABLE
    stat = next(e for e in evaluate(edu, f_edu) if e.step_id == "statistical_inference_steps")
    assert stat.applicability is Applicability.NOT_APPLICABLE and "no statistical inference" in stat.applicability_reason

    # 5. different loop-backs: a recorded diagnostic sends the work to a different earlier step
    for step in ("label_provenance", "sample_construction", "feature_construction", "temporal_validation_design",
                 "imbalance_aware_evaluation", "model_comparison_protocol"):
        record_step_run(store, fraud, step, assessment="PROVISIONAL")
    record_step_run(store, fraud, "leakage_check", diagnostic_signals={"feature_timing": "CONCERN_INDICATED"})
    f2 = facts_from_store(store, research_question_id="RQ_A", study_design="predictive_modeling")
    assert next_steps(fraud, f2)[0] == "feature_construction"
    assert methods_view(fraud, f2)["feature_construction"].reopened_by == {
        "by": "leakage_check", "reason": "information from after the prediction date may be in the features"}
    for step in ("sampling_rationale", "data_collection_protocol", "coding_scheme_development"):
        record_step_run(store, edu, step, assessment="PROVISIONAL")
    record_step_run(store, edu, "thematic_analysis", diagnostic_signals={"saturation": "CONCERN_INDICATED"})
    f3 = facts_from_store(store, research_question_id="RQ_A", study_design="qualitative_interview")
    assert next_steps(edu, f3)[0] == "data_collection_protocol"
    assert methods_view(edu, f3)["data_collection_protocol"].reopened_by["reason"] == "themes were still emerging: collect more data"

    # 6. the same prompt, then: both workflows send the model exactly the same request for the same stage
    prompts = []
    for spec in (fraud, edu):
        adapter = ScriptedModelAdapter(default_response={n: ([] if p.get("type") == "array" else {} if p.get("type") == "object" else "x" if p.get("type") == "string" else True)
                                                         for n, p in output_schema("literature")["properties"].items() if n in output_schema("literature")["required"]} | {"evidence_refs": []})
        run_workflow("literature", store, research_question_id="RQ_A", model_adapter=adapter, domain_workflow=spec, reuse_cached=False)
        prompts.append((adapter.recorded_requests[0].system_prompt, adapter.recorded_requests[0].user_prompt))
    assert prompts[0] == prompts[1]
    assert f"## Domain stage instructions\n{SHARED['literature']}" in prompts[0][0]


def test_loop_back_closes_once_the_target_step_has_been_redone(proj):
    fraud = load_builtin_spec("LIB_DW_ACC_FRAUD_DETECTION")
    store, _ = proj
    add(proj, RQ)
    for step in ("label_provenance", "sample_construction", "feature_construction", "temporal_validation_design",
                 "imbalance_aware_evaluation", "model_comparison_protocol"):
        record_step_run(store, fraud, step)
    first = record_step_run(store, fraud, "leakage_check", diagnostic_signals={"feature_timing": "CONCERN_INDICATED"})
    view = lambda: {e.step_id: e for e in evaluate(fraud, facts_from_store(store, study_design="predictive_modeling"))}  # noqa: E731
    assert view()["feature_construction"].reopened_by and not view()["feature_construction"].done
    redo = record_step_run(store, fraud, "feature_construction")  # redone after the concern was recorded
    assert view()["feature_construction"].reopened_by is None and view()["feature_construction"].done
    # the concern is still on record (history), but a new leakage run with a concern reopens it again
    record_step_run(store, fraud, "leakage_check", diagnostic_signals={"feature_timing": "CONCERN_INDICATED"})
    assert view()["feature_construction"].reopened_by is not None
    # every return to a step stays in history
    assert get_workflow_run(store, redo)["parent_run_id"] is not None
    assert get_workflow_run(store, redo)["domain_step_id"] == "feature_construction" and first != redo


def test_a_diagnostic_can_send_work_to_a_different_step_than_another_diagnostic(proj):
    fraud = load_builtin_spec("LIB_DW_ACC_FRAUD_DETECTION")
    store, _ = proj
    add(proj, RQ)
    for step in ("label_provenance", "sample_construction", "feature_construction", "temporal_validation_design",
                 "imbalance_aware_evaluation", "model_comparison_protocol"):
        record_step_run(store, fraud, step)
    record_step_run(store, fraud, "leakage_check", diagnostic_signals={"label_timing": "CONCERN_INDICATED", "feature_timing": "NO_CONCERN_INDICATED"})
    ev = {e.step_id: e for e in evaluate(fraud, facts_from_store(store, study_design="predictive_modeling"))}
    assert ev["label_provenance"].reopened_by and ev["feature_construction"].reopened_by is None


# ==================================================================================================
# stages, steps, explanations
# ==================================================================================================

def test_stage_applies_only_to_the_designs_the_workflow_covers():
    fraud = load_builtin_spec("LIB_DW_ACC_FRAUD_DETECTION")
    off = stage_status(fraud, facts(study_design="qualitative_interview"), "methods")
    assert off.applicability is Applicability.NOT_APPLICABLE and "not 'qualitative_interview'" in off.justification
    assert stage_status(fraud, facts(study_design="archival"), "methods").applicability is Applicability.APPLICABLE
    assert stage_status(fraud, facts(study_design=None), "methods").applicability is Applicability.APPLICABLE  # unrecorded: no claim either way


def test_stage_entry_conditions_block_its_steps_and_say_why():
    fraud = load_builtin_spec("LIB_DW_ACC_FRAUD_DETECTION")
    f = facts(study_design="archival")
    gate = stage_status(fraud, f, "results")
    assert gate.blocked and "model_comparison_protocol" in gate.unmet_entry[0]
    leak = next(e for e in evaluate(fraud, f) if e.step_id == "leakage_check")
    assert leak.runnable is False and any("model_comparison_protocol" in r for r in leak.blocked_by)


def test_explain_one_step_and_unknown_step():
    fraud = load_builtin_spec("LIB_DW_ACC_FRAUD_DETECTION")
    e = explain(fraud, facts(study_design="archival"), "sample_construction")
    assert e["runnable"] is False and e["blocked_by"] and e["kind"] == "plan"
    with pytest.raises(KeyError, match="STEP_NOT_FOUND"):
        explain(fraud, facts(), "nope")


def test_done_steps_are_not_offered_again():
    generic = load_builtin_spec(GENERIC_SPEC_ID)
    f = facts(steps={"methods": StepState("SUCCEEDED", "PROVISIONAL", {}, "R", 1)})
    assert "methods" not in next_steps(generic, f) and "contribution" in next_steps(generic, f)
    f_failed = facts(steps={"methods": StepState("FAILED", "NOT_ASSESSED", {}, "R", 1)})
    assert "methods" in next_steps(generic, f_failed)  # a failed step can be run again


# ==================================================================================================
# dispatcher integration
# ==================================================================================================

def test_stage_not_applicable_is_not_run_not_an_error_and_never_reused(proj):
    edu = DomainWorkflowSpec.from_dict(spec_dict("edu"))
    add(proj, RQ)
    store, _ = proj
    runs = [run_workflow("results", store, research_question_id="RQ_A", domain_workflow=edu, reuse_cached=True) for _ in range(2)]
    for out in runs:
        assert out.status == "NOT_APPLICABLE"
        assert out.axes.execution is ExecutionStatus.NOT_RUN and out.axes.applicability is Applicability.NOT_APPLICABLE
        assert out.axes.assessment.value == "NOT_ASSESSED" and out.axes.reason == "not_applicable"
        assert "no separate statistical results stage" in out.result["justification"]
        assert out.usage.llm_calls == 0
    rows = list_workflow_runs(store, workflow="results")
    assert len(rows) == 2 and all(r["domain_workflow_pin"] == edu.pin and r["applicability"] == "NOT_APPLICABLE" for r in rows)
    assert all(r["execution_status"] == "NOT_RUN" and r["executor"].startswith("domain_gate@") for r in rows)


def test_stage_with_unmet_entry_conditions_is_blocked_until_they_hold(proj):
    fraud = load_builtin_spec("LIB_DW_ACC_FRAUD_DETECTION")
    add(proj, RQ)
    store, _ = proj
    blocked = run_workflow("results", store, research_question_id="RQ_A", domain_workflow=fraud, reuse_cached=False)
    assert blocked.status == "BLOCKED" and blocked.axes.execution is ExecutionStatus.BLOCKED
    assert blocked.axes.reason == "entry_conditions_unmet" and "model_comparison_protocol" in blocked.result["unmet_entry_conditions"][0]
    record_step_run(store, fraud, "model_comparison_protocol", assessment="PROVISIONAL")
    ran = run_workflow("results", store, research_question_id="RQ_A", domain_workflow=fraud, reuse_cached=False)
    assert ran.status not in {"BLOCKED", "NOT_APPLICABLE"}


def test_a_gate_is_evaluated_before_reuse(proj):
    """A stage that ran under one workflow is not served from the registry once another workflow blocks it."""
    fraud = load_builtin_spec("LIB_DW_ACC_FRAUD_DETECTION")
    add(proj, RQ, ("RES_1", "Result", {"summary": "x"}, [("depends_on", "RQ_A", 1, {})]))
    store, _ = proj
    first = run_workflow("results", store, research_question_id="RQ_A", reuse_cached=True)  # generic: runs
    assert first.status != "BLOCKED"
    assert run_workflow("results", store, research_question_id="RQ_A", reuse_cached=True).status == "REUSED"
    assert run_workflow("results", store, research_question_id="RQ_A", domain_workflow=fraud, reuse_cached=True).status == "BLOCKED"


def test_a_generic_stage_run_completes_its_assessment_step(proj):
    add(proj, RQ, ("CLAIM_1", "Claim", {"text": "H", "claim_tier": "associational"}, [("depends_on", "RQ_A", 1, {})]))
    store, _ = proj
    out = run_workflow("contribution", store, research_question_id="RQ_A", reuse_cached=False)
    row = get_workflow_run(store, out.metadata["run_id"])
    assert row["domain_step_id"] == "contribution" and row["domain_workflow_pin"].startswith("LIB_DW_GENERIC@1.0.0#")
    f = facts_from_store(store, research_question_id="RQ_A")
    assert f.steps["contribution"].execution == "SUCCEEDED" and "contribution" not in next_steps(load_builtin_spec(GENERIC_SPEC_ID), f)


def test_a_multi_step_stage_run_is_not_attributed_to_one_step(proj):
    fraud = load_builtin_spec("LIB_DW_ACC_FRAUD_DETECTION")
    add(proj, RQ)
    store, _ = proj
    out = run_workflow("methods", store, research_question_id="RQ_A", domain_workflow=fraud, reuse_cached=False)
    row = get_workflow_run(store, out.metadata["run_id"])
    assert row["domain_step_id"] is None and row["domain_workflow_pin"] == fraud.pin  # methods has six steps, none is "the" stage


# ==================================================================================================
# the instruction fragment reaches the model and the cache identity, per stage
# ==================================================================================================

def test_domain_instructions_reach_the_system_prompt_and_only_that_stages_cache_identity(proj):
    add(proj, RQ, ("CLAIM_1", "Claim", {"text": "H", "claim_tier": "associational"}, [("depends_on", "RQ_A", 1, {})]))
    store, _ = proj
    base = spec_dict("fraud")
    for s in STAGES:
        base["stages"][s]["instructions"] = f"Instruction for {s}, version 1."
    v1 = DomainWorkflowSpec.from_dict(base)
    changed = copy.deepcopy(base)
    changed["stages"]["results"]["instructions"] = "Instruction for results, version 2."
    v2 = DomainWorkflowSpec.from_dict(changed)

    first = run_workflow("contribution", store, research_question_id="RQ_A", domain_workflow=v1, reuse_cached=True)
    assert run_workflow("contribution", store, research_question_id="RQ_A", domain_workflow=v2, reuse_cached=True).status == "REUSED"
    assert first.metadata["run_id"] and v1.stage_digest("contribution") == v2.stage_digest("contribution")
    assert v1.stage_digest("results") != v2.stage_digest("results") and v1.pin != v2.pin


def test_changing_a_stages_instructions_misses_the_cache_and_changes_the_request(proj):
    add(proj, RQ, ("CLAIM_1", "Claim", {"text": "H", "claim_tier": "associational"}, [("depends_on", "RQ_A", 1, {})]))
    store, _ = proj
    answer = {n: ([] if p.get("type") == "array" else {} if p.get("type") == "object" else "x" if p.get("type") == "string" else True)
              for n, p in output_schema("contribution")["properties"].items() if n in output_schema("contribution")["required"]} | {"evidence_refs": []}
    systems = []
    for text in ("Read the closest papers' sample sections first.", "Read the closest papers' sample sections first. Then the label sources."):
        data = spec_dict("fraud")
        data["stages"]["contribution"]["instructions"] = text
        spec = DomainWorkflowSpec.from_dict(data)
        adapter = ScriptedModelAdapter(default_response=answer)
        out = run_workflow("contribution", store, research_question_id="RQ_A", domain_workflow=spec, model_adapter=adapter, reuse_cached=True)
        assert out.status != "REUSED" and len(adapter.recorded_requests) == 1
        systems.append(adapter.recorded_requests[0].system_prompt)
    assert systems[0] != systems[1] and "## Domain stage instructions\nRead the closest" in systems[0]


# ==================================================================================================
# resolution and recording
# ==================================================================================================

def test_spec_resolution_order(proj):
    assert resolve_spec().id == GENERIC_SPEC_ID
    assert resolve_spec("LIB_DW_ACC_FRAUD_DETECTION").id == "LIB_DW_ACC_FRAUD_DETECTION"
    assert resolve_spec(None, research_question_content={"domain_workflow": "LIB_DW_ACC_FRAUD_DETECTION"}).id == "LIB_DW_ACC_FRAUD_DETECTION"
    assert resolve_spec(GENERIC_SPEC_ID, research_question_content={"domain_workflow": "LIB_DW_ACC_FRAUD_DETECTION"}).id == GENERIC_SPEC_ID
    assert resolve_spec(spec_dict("edu")).id == "LIB_DW_EDU_QUALITATIVE_FIXTURE"
    with pytest.raises(KeyError, match="DOMAIN_WORKFLOW_NOT_FOUND"):
        resolve_spec("LIB_DW_NOPE")


def test_the_research_question_can_name_its_workflow_and_design(proj):
    add(proj, ("RQ_A", "ResearchQuestion", {"text": "q", "domain_workflow": "LIB_DW_ACC_FRAUD_DETECTION", "study_design": "qualitative_interview"}, []))
    store, _ = proj
    out = run_workflow("methods", store, research_question_id="RQ_A", reuse_cached=False)
    assert out.status == "NOT_APPLICABLE" and "not 'qualitative_interview'" in out.result["justification"]


def test_record_step_run_validation_and_history(proj):
    fraud = load_builtin_spec("LIB_DW_ACC_FRAUD_DETECTION")
    store, _ = proj
    with pytest.raises(KeyError, match="STEP_NOT_FOUND"):
        record_step_run(store, fraud, "nope")
    with pytest.raises(taxonomy.TaxonomyError):
        record_step_run(store, fraud, "leakage_check", diagnostic_signals={"feature_timing": "MAYBE"})
    with pytest.raises(ValueError):
        record_step_run(store, fraud, "leakage_check", execution="DONE")
    a = record_step_run(store, fraud, "label_provenance")
    b = record_step_run(store, fraud, "label_provenance", assessment="VERIFIED_WITHIN_SCOPE")
    row = get_workflow_run(store, b)
    assert row["parent_run_id"] == a and row["workflow"] == "domain_step" and row["domain_workflow_pin"] == fraud.pin


# ==================================================================================================
# CLI
# ==================================================================================================

def test_cli_domain_list_and_next(proj, capsys):
    store, _ = proj
    add(proj, RQ, ("DS_L", "Dataset", {"data_role": "label_source"}, [("depends_on", "RQ_A", 1, {})]),
        ("EP_L", "EvidencePassage", {"topic": "label_definition", "locator": "p.1", "quote": "q"}, [("depends_on", "RQ_A", 1, {})]))
    assert cli_main(["domain", "list", "--json"]) == 0
    assert {w["id"] for w in json.loads(capsys.readouterr().out)["domain_workflows"]} == {
        "LIB_DW_GENERIC", "LIB_DW_ACC_FRAUD_DETECTION", "LIB_DW_CRITICAL_SYNTHESIS", "LIB_DW_CONCEPTUAL_ARGUMENT", "LIB_DW_QUALITATIVE_INQUIRY"}
    base = ["--project", "PRJ_DOM", "--home", str(store.paths.home)]
    assert cli_main(["domain", "next", *base, "--spec", "LIB_DW_ACC_FRAUD_DETECTION", "--study-design", "predictive_modeling", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "label_provenance" in out["next_steps"] and "sample_construction" not in out["next_steps"]
    assert cli_main(["domain", "next", *base, "--spec", str(FIXTURE), "--study-design", "qualitative_interview"]) == 0
    text = capsys.readouterr().out
    assert "LIB_DW_EDU_QUALITATIVE_FIXTURE" in text and "n/a" in text
    assert cli_main(["domain", "next", *base, "--spec", "LIB_DW_NOPE"]) == 1
