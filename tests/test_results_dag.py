"""Phase 3E (plan v3): the execution DAG and its three layers (execution checks, diagnostics, interpretation)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_harness.core.errors import AuthorizationError
from research_harness.execution import dag
from research_harness.execution.diagnostics import DiagnosticSpecError, evaluate_diagnostic, nonsignificance_reading
from research_harness.knowledge.common import KnowledgeError
from research_harness.state.actors import register_actor
from research_harness.state.store import StateStore
from research_harness.workflows.domain_engine import evaluate, facts_from_store, load_builtin_spec, next_steps, record_step_run

BASIS_A, BASIS_B = ["Source A, rule of thumb"], ["Source B, stricter rule"]


def script(workdir: Path, name: str, body: str) -> None:
    (workdir / name).write_text(body, encoding="utf-8")


def metrics_script(values: dict, output: str | None = None, sleep_fail: bool = False) -> str:
    out = f"open({output!r}, 'w').write('firm,label\\nF1,0\\n')\n" if output else ""
    return f"import json\n{out}json.dump({values!r}, open(__import__('sys').argv[1], 'w'))\n"


@pytest.fixture
def world(tmp_path):
    store, token = StateStore.create(tmp_path / "home", "PRJ_DAG", owner_id="researcher")
    wd = tmp_path / "work"
    wd.mkdir()
    script(wd, "prep.py", "open('sample.csv','w').write('firm,label\\nF1,0\\nF2,1\\n')\n")
    script(wd, "main.py", "import json\njson.dump({'ap': 0.12, 'leak_corr': 0.45, 'p': 0.2, 'ci_low': -0.05, 'ci_high': 0.2, 'mde': 0.12}, open('main.metrics.json','w'))\n")
    script(wd, "robust.py", "import json\njson.dump({'ap': 0.07}, open('robust.metrics.json','w'))\n")
    script(wd, "report.py", "open('report.txt','w').write('done')\n")
    script(wd, "bad.py", "import sys\nprint('about to fail')\nsys.exit(3)\n")
    return {"store": store, "token": token, "wd": wd}


def make_plan(w, steps, declared=None, plan_id="PLAN_X"):
    with w["store"].write_tx(token=w["token"]) as tx:
        tx.add_revision(object_id=plan_id, object_type="AnalysisPlan", origin={}, edges=[],
                        content={"label": "plan", "steps": steps, "declared": declared or {}})
    return plan_id


def std_steps(**over):
    steps = [
        {"id": "prep", "fidelity": "inherited", "command": ["python", "prep.py"], "outputs": ["sample.csv"],
         "execution_checks": [{"kind": "file_exists", "path": "sample.csv"}, {"kind": "file_columns", "path": "sample.csv", "columns": ["firm", "label"]}]},
        {"id": "main", "fidelity": "inherited", "command": ["python", "main.py"], "depends_on_steps": ["prep"], "inputs": ["sample.csv"], "produces_result": True,
         "diagnostics": [{"diagnostic": "feature_timing", "value_key": "leak_corr", "criteria": [
             {"name": "rule A", "concern_if": {"op": ">", "threshold": 0.3}, "basis": BASIS_A},
             {"name": "rule B", "concern_if": {"op": ">", "threshold": 0.6}, "basis": BASIS_B}]}]},
        {"id": "robust", "fidelity": "adapted", "changes": "x", "rationale": "y", "command": ["python", "robust.py"], "depends_on_steps": ["main"], "produces_result": True,
         "robustness": {"dimension": "period", "metric": "ap", "baseline_step": "main", "tolerance": 0.02, "tolerance_basis": ["declared by the researcher"],
                        "interpretation_if_holds": "the effect is stable across periods", "interpretation_if_fails": "the effect is period-specific"}},
        {"id": "report", "fidelity": "inherited", "command": ["python", "report.py"], "depends_on_steps": ["robust"], "outputs": ["report.txt"]},
    ]
    for s in steps:
        s.update(over.get(s["id"], {}))
    return steps


def run(w, plan="PLAN_X", **kw):
    return dag.run_plan(w["store"], w["token"], plan, w["wd"], **kw)


def runs(w, plan="PLAN_X"):
    with w["store"].read() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM workflow_runs WHERE workflow='execution' AND plan_id=? ORDER BY rowid", (plan,))]


def test_dag_blocks_only_on_execution_failure(world):
    steps = std_steps(prep={"command": ["python", "bad.py"]})
    steps.append({"id": "side", "fidelity": "added", "command": ["python", "report.py"], "outputs": ["report.txt"]})   # independent of prep
    make_plan(world, steps)
    out = run(world)["steps"]
    assert out["prep"]["execution"] == "FAILED" and out["prep"]["failed_checks"][0]["kind"] == "exit_zero"
    assert [out[s]["execution"] for s in ("main", "robust", "report")] == ["BLOCKED"] * 3
    assert out["main"]["blocked_by"] == ["prep"] and out["robust"]["blocked_by"] == ["main"]       # the chain names its cause at each step
    assert out["side"]["execution"] == "SUCCEEDED"                                              # not downstream of the failure: it runs
    rows = {r["step_id"]: r for r in runs(world)}
    assert rows["main"]["execution_status"] == "BLOCKED" and rows["main"]["status_reason"] == "upstream_failed" and rows["main"]["context_status"] == "INSUFFICIENT"


def test_step_run_registered_with_hashes(world):
    make_plan(world, std_steps())
    out = run(world)
    assert [out["steps"][s]["execution"] for s in ("prep", "main", "robust", "report")] == ["SUCCEEDED"] * 4
    store = world["store"]
    by = {r["step_id"]: r for r in runs(world)}
    art = json.loads(store.artifacts.get_bytes(by["main"]["artifact_sha256"]))
    assert by["main"]["workflow"] == "execution" and by["main"]["plan_id"] == "PLAN_X" and by["main"]["execution_status"] == "SUCCEEDED"
    assert art["input_hashes"] == {"sample.csv": dag.sha256_file(world["wd"] / "sample.csv")}
    assert art["script_hashes"] == {"main.py": dag.sha256_file(world["wd"] / "main.py")}
    assert art["output_hashes"]["main.metrics.json"] == dag.sha256_file(world["wd"] / "main.metrics.json") and art["exit_code"] == 0
    assert store.artifacts.verify(art["log_sha256"]) and "bad" not in store.artifacts.get_bytes(art["log_sha256"]).decode()
    failing = make_plan(world, std_steps(prep={"command": ["python", "bad.py"]}), plan_id="PLAN_F")
    run(world, failing)
    log = json.loads(store.artifacts.get_bytes([r for r in runs(world, "PLAN_F") if r["step_id"] == "prep"][0]["artifact_sha256"]))["log_sha256"]
    assert "about to fail" in store.artifacts.get_bytes(log).decode()                              # the log of a failure is kept


def test_diagnostic_concern_never_blocks_or_passes(world):
    make_plan(world, std_steps(main={"diagnostics": [{"diagnostic": "feature_timing", "value_key": "leak_corr", "criteria": [
        {"name": "rule A", "concern_if": {"op": ">", "threshold": 0.3}, "basis": BASIS_A},
        {"name": "rule B", "concern_if": {"op": ">", "threshold": 0.4}, "basis": BASIS_B}]}]}))
    out = run(world)["steps"]
    assert out["main"]["signals"] == {"feature_timing": "CONCERN_INDICATED"}                       # every criterion indicates a concern
    # a concern stops nothing: the step succeeded and everything downstream ran
    assert [out[s]["execution"] for s in ("main", "robust", "report")] == ["SUCCEEDED"] * 3
    res = world["store"].get_object(out["main"]["result_id"])["content"]
    assert res["axes"]["execution"] == "SUCCEEDED" and res["axes"]["assessment"] == "PROVISIONAL"
    # and a lack of concern is not a pass: nothing in the record is a boolean pass/fail, and nothing is verified
    quiet = evaluate_diagnostic({"diagnostic": "d", "value_key": "v", "criteria": [{"name": "c", "concern_if": {"op": ">", "threshold": 1}, "basis": ["s"]}]}, {"v": 0.1})
    assert quiet["signal"] == "NO_CONCERN_INDICATED" and "pass" not in json.dumps(quiet).lower() and "PASS" not in json.dumps(out)
    assert res["diagnostics"] == [{"diagnostic": "feature_timing", "signal": "CONCERN_INDICATED"}] and res["axes"]["assessment"] != "VERIFIED_WITHIN_SCOPE"


def test_conflicting_criteria_both_reported(world):
    make_plan(world, std_steps())          # rule A (> 0.3) flags 0.45, rule B (> 0.6) does not
    out = run(world)["steps"]
    assert out["main"]["signals"] == {"feature_timing": "INCONCLUSIVE"}
    art = json.loads(world["store"].artifacts.get_bytes([r for r in runs(world) if r["step_id"] == "main"][0]["artifact_sha256"]))
    (d,) = art["diagnostics"]
    assert d["criteria_disagree"] is True and {c["criterion"]: c["signal"] for c in d["reference_criteria"]} == {
        "rule A": "CONCERN_INDICATED", "rule B": "NO_CONCERN_INDICATED"}
    assert {tuple(c["basis"]) for c in d["reference_criteria"]} == {tuple(BASIS_A), tuple(BASIS_B)} and "The criteria disagree; all are shown" in d["reading"]


def test_thresholds_are_never_defaulted(world):
    with pytest.raises(DiagnosticSpecError, match="no reference criteria"):
        evaluate_diagnostic({"diagnostic": "d", "value_key": "v", "criteria": []}, {"v": 1})
    with pytest.raises(DiagnosticSpecError, match="the source it comes from"):
        evaluate_diagnostic({"diagnostic": "d", "value_key": "v", "criteria": [{"name": "c", "concern_if": {"op": ">", "threshold": 1}}]}, {"v": 1})
    assert evaluate_diagnostic({"diagnostic": "d", "value_key": "v", "criteria": [{"name": "c", "concern_if": {"op": ">", "threshold": 1}, "basis": ["s"]}]},
                               {})["signal"] == "NOT_COMPUTABLE"
    bad = std_steps(main={"diagnostics": [{"diagnostic": "x", "value_key": "leak_corr", "criteria": [{"name": "c", "concern_if": {"op": ">", "threshold": 1}}]}]})
    make_plan(world, bad)
    with pytest.raises(DiagnosticSpecError):           # refused before anything runs
        run(world)
    assert runs(world) == []


def test_robustness_divergence_recorded_as_evidence(world):
    make_plan(world, std_steps())
    out = run(world)["steps"]
    (div,) = out["robust"]["divergences"]
    assert out["robust"]["execution"] == "SUCCEEDED"                                            # divergence is evidence, not failure
    assert div["dimension"] == "period" and abs(div["magnitude"] - 0.05) < 1e-9 and div["outcome"] == "EXCEEDS_DECLARED_TOLERANCE"
    assert div["interpretation"] == "the effect is period-specific" and div["sign_changed"] is False
    assert out["report"]["execution"] == "SUCCEEDED"
    # no declared tolerance with a basis: the divergence is measured, and no reading is chosen
    make_plan(world, std_steps(robust={"robustness": {"dimension": "period", "metric": "ap", "baseline_step": "main"}}), plan_id="PLAN_N")
    d2 = run(world, "PLAN_N")["steps"]["robust"]["divergences"][0]
    assert d2["outcome"] == "NO_RULE_DECLARED" and d2["interpretation"] is None and d2["magnitude"] > 0
    with pytest.raises(DiagnosticSpecError, match="a dimension"):
        from research_harness.execution.diagnostics import divergence
        divergence({}, {}, {"dimension": "vibes", "metric": "ap"})


def test_result_on_failed_step_not_assessed(world):
    make_plan(world, std_steps(prep={"command": ["python", "bad.py"]}))
    out = run(world)["steps"]
    res = world["store"].get_object(out["main"]["result_id"])["content"]
    assert res["axes"]["execution"] == "BLOCKED" and res["axes"]["assessment"] == "NOT_ASSESSED" and res["axes"]["reason"] == "upstream_failed"
    assert res["blocked_by"] == ["prep"] and res["axes"]["omissions"] == ["step:prep"] and res["provenance"] == "NO_OUTPUT" and "did not produce a result" in res["summary"]
    make_plan(world, std_steps(main={"command": ["python", "bad.py"]}), plan_id="PLAN_G")
    res2 = world["store"].get_object(run(world, "PLAN_G")["steps"]["main"]["result_id"])["content"]
    assert res2["axes"]["execution"] == "FAILED" and res2["axes"]["assessment"] == "NOT_ASSESSED"


def test_hash_verified_result_still_provisional(world):
    store = world["store"]
    make_plan(world, std_steps())
    out = run(world)["steps"]["main"]
    assert out["provenance"] == "VERIFIED_RUN" and out["assessment"] == "PROVISIONAL"            # an execution fact; the conclusion is not established
    assert dag.result_provenance(store, out["result_id"]) == "VERIFIED_RUN"
    assert store.get_object(out["result_id"])["content"]["axes"]["assessment"] == "PROVISIONAL"
    sha = store.get_object(out["result_id"])["content"]["artifact_sha256"]
    ok = dag.add_reported_result(store, world["token"], result_id="RES_REPORTED_OK", plan_id="PLAN_X", step_id="main", summary="AP 0.12", artifact_sha256=sha)
    other = store.artifacts.put_bytes(b'{"ap": 0.99}')
    bad = dag.add_reported_result(store, world["token"], result_id="RES_REPORTED_BAD", plan_id="PLAN_X", step_id="main", summary="AP 0.99", artifact_sha256=other)
    none = dag.add_reported_result(store, world["token"], result_id="RES_REPORTED_NONE", plan_id="PLAN_X", step_id="main", summary="AP 0.2")
    assert (ok["provenance"], bad["provenance"], none["provenance"]) == ("VERIFIED_RUN", "AUTHOR_REPORTED", "AUTHOR_REPORTED")


def test_nonsignificance_readings_need_declared_sesoi_and_alpha():
    values = {"p_value": 0.2, "ci_low": -0.05, "ci_high": 0.2, "mde": 0.12, "equivalence_p": 0.4}
    none = nonsignificance_reading(values, {})
    assert none["readings"][0]["reading"] == "INCONCLUSIVE_REASON_NOT_ASSESSED" and "sesoi, alpha, basis" in none["readings"][0]["why"]
    assert nonsignificance_reading(values, {"sesoi": 0.1, "alpha": 0.05})["readings"][0]["reading"] == "INCONCLUSIVE_REASON_NOT_ASSESSED"   # no basis: no reading
    declared = {"sesoi": 0.1, "alpha": 0.05, "basis": ["the smallest effect that matters for the audit decision"]}
    under = nonsignificance_reading(values, declared)
    assert {r["reading"] for r in under["readings"]} == {"UNDERPOWERED", "IMPRECISE"} and under["significant"] is False
    assert all(r["basis"] == declared["basis"] for r in under["readings"])
    negligible = nonsignificance_reading({"p_value": 0.4, "ci_low": -0.03, "ci_high": 0.04, "mde": 0.05, "equivalence_p": 0.01}, declared)
    assert [r["reading"] for r in negligible["readings"]] == ["NEGLIGIBLE_EFFECT_SUPPORTED"]
    assert nonsignificance_reading({**values, "p_value": 0.001}, declared) == {"significant": True, "readings": []}
    assert "refute" not in json.dumps([under, negligible]).lower()
    concern = nonsignificance_reading({"p_value": 0.5, "ci_low": -0.01, "ci_high": 0.01, "mde": 0.01, "equivalence_p": 0.5}, declared, measurement_concern=True)
    assert [r["reading"] for r in concern["readings"]] == ["MEASUREMENT_CONCERN"]


def test_nonsignificance_in_a_run_uses_the_plans_declaration(world):
    ns = {"nonsignificance": {"p_value_key": "p", "ci_low_key": "ci_low", "ci_high_key": "ci_high", "mde_key": "mde"}}
    make_plan(world, std_steps(main=ns), declared={"sesoi": 0.1, "alpha": 0.05, "basis": ["declared by the researcher"]})
    res = world["store"].get_object(run(world)["steps"]["main"]["result_id"])["content"]
    assert {r["reading"] for r in res["nonsignificance"]["readings"]} == {"UNDERPOWERED", "IMPRECISE"}
    make_plan(world, std_steps(main=ns), plan_id="PLAN_U")                                          # nothing declared
    res2 = world["store"].get_object(run(world, "PLAN_U")["steps"]["main"]["result_id"])["content"]
    assert res2["nonsignificance"]["readings"][0]["reading"] == "INCONCLUSIVE_REASON_NOT_ASSESSED"


def test_missing_rscript_reported_not_crash(world):
    make_plan(world, std_steps(main={"command": ["Rscript-not-installed-xyz", "main.R"]}))
    out = run(world)["steps"]
    assert out["main"]["execution"] == "FAILED" and out["main"]["failed_checks"][0]["kind"] == "runtime_available"
    assert out["robust"]["execution"] == "BLOCKED"
    row = [r for r in runs(world) if r["step_id"] == "main"][0]
    assert row["status_reason"] == "missing_runtime"
    log = json.loads(world["store"].artifacts.get_bytes(row["artifact_sha256"]))["checks"][0]["detail"]
    assert "not found on PATH" in log


def test_running_a_plan_needs_the_owner_and_stays_inside_the_workdir(world):
    make_plan(world, std_steps())
    other = register_actor(world["store"], world["token"], actor_id="an_author", display_name="a", roles=["author", "importer"])
    with pytest.raises(AuthorizationError, match="ROLE_REQUIRED"):
        dag.run_plan(world["store"], other["token"], "PLAN_X", world["wd"])
    assert runs(world) == []
    make_plan(world, std_steps(prep={"inputs": ["../outside.txt"]}), plan_id="PLAN_P")
    with pytest.raises(KnowledgeError, match="PATH_OUTSIDE_WORKDIR"):
        run(world, "PLAN_P")
    make_plan(world, std_steps(prep={"depends_on_steps": ["report"]}), plan_id="PLAN_C")
    with pytest.raises(KnowledgeError, match="PLAN_CYCLE"):
        run(world, "PLAN_C")


def test_only_and_from_step_use_earlier_runs(world):
    make_plan(world, std_steps())
    run(world)
    again = run(world, from_step="robust")
    assert again["ran"] == ["robust", "report"] and again["steps"]["robust"]["execution"] == "SUCCEEDED"
    only = run(world, only="main")
    assert only["ran"] == ["main"]
    summary = dag.plan_execution_summary(world["store"], "PLAN_X")
    assert set(summary) == {"prep", "main", "robust", "report"} and summary["main"]["diagnostics"][0]["signal"] == "INCONCLUSIVE"
    with pytest.raises(KnowledgeError, match="STEP_UNKNOWN"):
        run(world, only="nope")


def test_omitted_step_is_recorded_and_waits_for_its_replacement(world):
    steps = std_steps()
    steps.insert(1, {"id": "old", "fidelity": "omitted", "omit_reason": "replaced_by", "replaced_by": "main", "depends_on_steps": ["prep"]})
    steps[3]["depends_on_steps"] = ["old"]                      # robust depended on the omitted step: it now waits for `main`
    make_plan(world, steps)
    out = run(world)["steps"]
    assert out["old"]["execution"] == "NOT_RUN" and out["robust"]["execution"] == "SUCCEEDED"
    row = [r for r in runs(world) if r["step_id"] == "old"][0]
    assert row["applicability"] == "NOT_APPLICABLE" and row["status_reason"] == "not_applicable"


def test_on_condition_branch_from_diagnostic_signal(world):
    """A diagnostic concern from the DAG reaches the domain workflow's state, where its rule sends the researcher back."""
    store, token, wd = world["store"], world["token"], world["wd"]
    spec = load_builtin_spec("LIB_DW_ACC_FRAUD_DETECTION")
    with store.write_tx(token=token) as tx:
        tx.add_revision(object_id="DS_LABELS", object_type="Dataset", origin={}, edges=[], content={"label": "labels", "data_role": "label_source"})
    for step in ("label_provenance", "sample_construction", "feature_construction", "temporal_validation_design", "imbalance_aware_evaluation",
                 "model_comparison_protocol"):
        record_step_run(store, spec, step)
    script(wd, "leak.py", "import json\njson.dump({'feature_gap_days': -12}, open('leak.metrics.json','w'))\n")
    steps = [{"id": "leak", "fidelity": "inherited", "command": ["python", "leak.py"], "domain_step": "leakage_check",
              "diagnostics": [{"diagnostic": "feature_timing", "value_key": "feature_gap_days", "criteria": [
                  {"name": "features dated after the prediction date", "concern_if": {"op": "<", "threshold": 0}, "basis": ["the plan: no feature may postdate the forecast date"]}]}]}]
    make_plan(world, steps, plan_id="PLAN_LEAK")
    facts = facts_from_store(store, study_design="predictive_modeling")
    assert "leakage_check" in next_steps(spec, facts) and not any(e.reopened_by for e in evaluate(spec, facts))
    out = run(world, "PLAN_LEAK", domain_spec=spec)
    assert out["steps"]["leak"]["signals"] == {"feature_timing": "CONCERN_INDICATED"} and out["steps"]["leak"]["execution"] == "SUCCEEDED"
    facts = facts_from_store(store, study_design="predictive_modeling")
    reopened = {e.step_id: e.reopened_by for e in evaluate(spec, facts) if e.reopened_by}
    assert reopened["feature_construction"]["reason"].startswith("information from after the prediction date")      # the loop-back fires
    assert "feature_construction" in next_steps(spec, facts)


def test_claim_effects_are_provisional_and_follow_the_steps_they_rest_on(world):
    store, token = world["store"], world["token"]
    with store.write_tx(token=token) as tx:
        tx.add_revision(object_id="CLAIM_1", object_type="Claim", origin={}, edges=[], content={"text": "The model transfers.", "claim_tier": "predictive"})
    make_plan(world, std_steps(robust={"command": ["python", "bad.py"]}))
    run(world)
    res = dag.interpret_results(store, token, "PLAN_X", [
        {"claim_id": "CLAIM_1", "effect": "LIMITS", "reasoning": "Performance is modest and the leakage criteria disagree.", "depends_on": ["main"]},
        {"claim_id": "CLAIM_1", "effect": "STRENGTHENS", "reasoning": "Robust to the period change.", "depends_on": ["robust"]}], interpretation_id="INTERP_1")
    first, second = res["effects"]
    assert (first["effect"], first["assessment"]) == ("LIMITS", "PROVISIONAL") and first["runs"]
    assert second["effect"] == "NOT_ASSESSED" and second["proposed_effect"] == "STRENGTHENS" and "robust" in second["note"]
    with pytest.raises(KnowledgeError, match="RATIONALE_REQUIRED"):
        dag.confirm_effect(store, token, "INTERP_1", 0, rationale="ok")
    with pytest.raises(KnowledgeError, match="EFFECT_NOT_ASSESSED"):
        dag.confirm_effect(store, token, "INTERP_1", 1, rationale="Confirming despite the failed step.")
    done = dag.confirm_effect(store, token, "INTERP_1", 0, rationale="The diagnostics and AP support a limit on the claim.")
    assert done["effect"]["assessment"] == "VERIFIED_WITHIN_SCOPE" and done["effect"]["generated_by"] == "researcher"
    with pytest.raises(KnowledgeError, match="CLAIM_NOT_FOUND"):
        dag.interpret_results(store, token, "PLAN_X", [{"claim_id": "NOPE", "effect": "LIMITS", "reasoning": "r"}], interpretation_id="INTERP_2")
