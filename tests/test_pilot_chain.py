"""Milestone M3 (plan v3): one pilot subfield runs question -> Introduction argument -> three literature reviews -> a playbook-selected
plan -> the DAG -> results -> claim change -> promise check, on synthetic material, with every hand-off real (no stubs between stages).

Study design: accounting / archival financial reporting / predictive modeling (restatement prediction), domain workflow
LIB_DW_ACC_FRAUD_DETECTION. Every text is invented; the model steps are scripted.
"""
from __future__ import annotations

import json

import pytest

from research_harness.execution import dag
from research_harness.knowledge import argument, literature, loop, lifecycle, playbooks
from research_harness.knowledge.replication import record_reproduction
from research_harness.state.store import StateStore
from research_harness.workflows import eca
from research_harness.workflows.adapters import ScriptedModelAdapter
from research_harness.workflows.domain_engine import evaluate, facts_from_store, load_builtin_spec, next_steps, record_step_run
from tests import m3_fixture as fx
from tests.library_fixture import PANEL_CSV

CTX = {"domain": "accounting", "subfield": "archival_financial_reporting", "study_design": "predictive_modeling",
       "estimand": "probability of a later restatement", "measurement": "label = amended annual report", "assumptions": ["firm-years observed"]}
SCOPE = {"study_design": "predictive_modeling", "subfield": "archival_financial_reporting"}
BASIS = ["declared by the researcher for this pilot"]


def eca_ok():
    return ScriptedModelAdapter(model_id="scripted-adjudicator", default_response={
        "passage_proposition": "As quoted.", "equivalence": "EQUIVALENT", "dropped_conditions": [], "cue_handling": [], "verdict": "SUPPORTED_AS_STATED",
        "required_qualification": []})


def runs_of(store, workflow_prefix):
    with store.read() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM workflow_runs WHERE workflow LIKE ? ORDER BY rowid", (workflow_prefix + "%",))]


def test_pilot_chain_end_to_end_on_synthetic_fixture(tmp_path):
    home, wd = tmp_path / "home", tmp_path / "work"
    wd.mkdir()
    store, token = StateStore.create(home, "PRJ_PILOT", owner_id="researcher")
    spec = load_builtin_spec("LIB_DW_ACC_FRAUD_DETECTION")

    # ---- the question, its design, its data --------------------------------------------------------------------------------------
    with store.write_tx(token=token) as tx:
        tx.add_revision(object_id="RQ_PILOT", object_type="ResearchQuestion", origin={}, edges=[], content={
            "text": "Does a restatement model estimated on some industries transfer to industries it has not seen?",
            "study_design": "predictive_modeling", "scope": SCOPE})
        tx.add_revision(object_id="DS_LABELS", object_type="Dataset", origin={}, edges=[], content={"label": "restatement labels", "data_role": "label_source"})
        tx.add_revision(object_id="CLAIM_MAIN", object_type="Claim", origin={}, edges=[], content={
            "text": "A restatement model transfers across industries.", "claim_tier": "predictive"})
    facts = facts_from_store(store)
    assert facts.study_design == "predictive_modeling" and set(next_steps(spec, facts)) >= {"contribution_assessment", "literature_assessment"}

    # ---- 3B: the Introduction argument of the focus manuscript ---------------------------------------------------------------------
    fx.import_corpus(store, token)
    extractor = fx.extractor()
    intro = argument.analyze_introduction(store, token, fx.FOCUS, extractor)
    assert intro["status"] == "REQUIRES_VERIFICATION"

    # ---- 3C: three literature reviews over the closest papers ----------------------------------------------------------------------
    papers = [fx.PAPER_A, fx.PAPER_B]
    theory = literature.run_literature(store, token, "theory", papers, extractor)
    method = literature.run_literature(store, token, "method", papers, extractor)
    contribution = literature.run_literature(store, token, "contribution", papers, extractor)
    assert set(theory["divergent_constructs"]) == {"restatement risk"}
    assert [m["source_fidelity"] for m in method["method_records"]] == ["QUOTE_VERIFIED"] * 2

    # ---- 3B x 3C: the contribution comparison consumes the contribution review -----------------------------------------------------
    def comparer(system, user):
        import re
        ours = re.search(r"\[(proposed_contribution\.1)\]", user).group(1)
        theirs = re.findall(r"\[(EP_[A-Z0-9_]+)\] \(claimed_contribution\)", user)[0]
        return {"differentiation": [{"dimension": "design", "ours": ours, "theirs": theirs, "relation": "overlapping", "substantive_difference": True,
                                     "reason": "ours evaluates transfer with out-of-time folds"}]}
    cmp_ = argument.compare_contribution(store, token, intro["argument_id"], contribution, ScriptedModelAdapter(model_id="scripted-comparer", custom_handler=comparer))
    assert len(cmp_["differentiation"]) == 2 and all(d["assessment"] == "PROVISIONAL" for d in cmp_["differentiation"])
    with store.read() as conn:
        assert contribution["run_id"] in conn.execute("SELECT upstream_run_ids FROM workflow_runs WHERE run_id=?", (cmp_["run_id"],)).fetchone()[0]

    # ---- 3D: a playbook from the method review, with a replication package ---------------------------------------------------------
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "panel.csv").write_bytes(PANEL_CSV.encode("utf-8"))
    from research_harness.knowledge.replication import add_replication_package
    add_replication_package(store, token, package_id="PKG_PANEL", label="panel package", directory=pkg_dir, files=["panel.csv"], expected_outputs=[
        {"id": "n_rows", "file": "panel.csv", "stat": "count", "expected": 8, "tolerance": 0},
        {"id": "pos", "file": "panel.csv", "column": "label", "stat": "positive_rate", "expected": 0.25, "tolerance": 1e-9}],
        entitlement={"status": "confirmed", "license": "synthetic data", "basis": "generated for this test"})
    pb = playbooks.draft_playbook_from_methods(store, token, method, playbook_id="PB_RESTATE", label="Out-of-time validation for restatement prediction",
                                               scope=SCOPE, exclusions=["unbalanced panels"], replication_package="PKG_PANEL",
                                               replication_links={"estimand": ["pos"], "sample_construction": ["n_rows"]})
    assert pb["critical"] == 6
    record_reproduction(store, token, "PKG_PANEL")
    row = {r["item_id"]: r for r in playbooks.select_playbooks(store, CTX)}["PB_RESTATE"]
    assert row["status"] == "candidate_validity_not_established_here"            # extracted, quote-verified and reproduced: still not valid by default
    assert row["dimensions"]["computational_reproducibility"] == "REPRODUCED_WITHIN_TOLERANCE"

    # the researcher adjudicates every HIGH element and assesses validity for this context
    for e in store.get_object("PB_RESTATE")["content"]["elements"]:
        res = eca.adjudicate(store, token, eca.resolve_statement(store, "PB_RESTATE", element=e["id"]), e["evidence_basis"][0]["ref"], eca_ok())
        eca.confirm(store, token, res["adjudication_id"], verdict="SUPPORTED_AS_STATED", rationale=f"Element {e['id']} reads its passage correctly.")
    lifecycle.validate(store, token, "PB_RESTATE", assessment={
        "context": CTX, "confirmed_by_researcher": True,
        "evidence_basis": [{"kind": "source_passage", "ref": store.get_object("PB_RESTATE")["content"]["elements"][0]["evidence_basis"][0]["ref"],
                            "methodological_literature": True}]})
    row = {r["item_id"]: r for r in playbooks.select_playbooks(store, CTX)}["PB_RESTATE"]
    assert row["status"] == "applicable"

    # ---- the plan: a proposal the researcher imports --------------------------------------------------------------------------------
    els: dict[str, list[str]] = {}
    for e in store.get_object("PB_RESTATE")["content"]["elements"]:
        els.setdefault(e["category"], []).append(e["id"])
    (wd / "step.py").write_text("import json, sys\njson.dump(json.loads(sys.argv[2]), open(sys.argv[1] + '.metrics.json', 'w'))\n", encoding="utf-8")

    def cmd(step, metrics):
        return ["python", "step.py", step, json.dumps(metrics)]
    steps = [
        {"id": "S_sample", "element": els["sample_construction"][0], "fidelity": "inherited", "command": cmd("S_sample", {"rows": 8}),
         "execution_checks": [{"kind": "file_exists", "path": "S_sample.metrics.json"}]},
        {"id": "S_sample_dup", "element": els["sample_construction"][1], "fidelity": "omitted", "omit_reason": "replaced_by", "replaced_by": "S_sample"},
        {"id": "S_label", "element": els["estimand"][0], "fidelity": "adapted", "command": cmd("S_label", {"positives": 2}), "depends_on_steps": ["S_sample"],
         "changes": "label window shortened to two years", "rationale": "the pilot data end earlier than the source's"},
        {"id": "S_label_dup", "element": els["estimand"][1], "fidelity": "omitted", "omit_reason": "not_applicable"},
        {"id": "S_valid", "element": els["validation_scheme"][0], "fidelity": "inherited", "command": cmd("S_valid", {"folds": 3}), "depends_on_steps": ["S_label"]},
        {"id": "S_valid_dup", "element": els["validation_scheme"][1], "fidelity": "omitted", "omit_reason": "not_applicable"},
        {"id": "leakage_check", "fidelity": "added", "rationale": "the domain workflow asks for a leakage check", "basis": [{"kind": "researcher_judgement", "ref": "owner"}],
         "command": cmd("leakage_check", {"feature_gap_days": -12}), "depends_on_steps": ["S_valid"], "domain_step": "leakage_check",
         "diagnostics": [{"diagnostic": "feature_timing", "value_key": "feature_gap_days", "criteria": [
             {"name": "no feature may postdate the prediction date", "concern_if": {"op": "<", "threshold": 0}, "basis": BASIS}]}]},
        {"id": "main", "fidelity": "added", "rationale": "main model", "basis": [{"kind": "researcher_judgement", "ref": "owner"}], "produces_result": True,
         "command": cmd("main", {"ap": 0.12}), "depends_on_steps": ["leakage_check"]},
        {"id": "robust", "fidelity": "added", "rationale": "out-of-industry holdout", "basis": [{"kind": "researcher_judgement", "ref": "owner"}], "produces_result": True,
         "command": cmd("robust", {"ap": 0.06}), "depends_on_steps": ["main"],
         "robustness": {"dimension": "sample", "metric": "ap", "baseline_step": "main", "tolerance": 0.02, "tolerance_basis": BASIS,
                        "interpretation_if_holds": "the model transfers", "interpretation_if_fails": "the model does not transfer across industries"}},
    ]
    plan = playbooks.draft_analysis_plan(store, "PB_RESTATE", plan_id="PLAN_PILOT", label="Pilot plan", context=CTX, steps=steps,
                                         declared={"sesoi": 0.02, "alpha": 0.05, "basis": BASIS}, domain_workflow=spec.pin)
    assert plan["playbook_status"] == "applicable" and len(plan["fidelity_summary"]["omitted"]) == 3
    assert store.get_object("PLAN_PILOT") is None                                 # nothing is written until the researcher imports it
    receipt = playbooks.submit_operations(store, token, plan["operations"], proposal_id="PROP_PILOT_PLAN")
    assert receipt["decision"] == "ACCEPTED", receipt

    # ---- 3E: the DAG, with the domain workflow watching the diagnostic signals ------------------------------------------------------
    for step in ("label_provenance", "sample_construction", "feature_construction", "temporal_validation_design", "imbalance_aware_evaluation",
                 "model_comparison_protocol"):
        record_step_run(store, spec, step)
    out = dag.run_plan(store, token, "PLAN_PILOT", wd, domain_spec=spec)
    assert [out["steps"][s]["execution"] for s in ("S_sample", "S_label", "S_valid", "leakage_check", "main", "robust")] == ["SUCCEEDED"] * 6
    assert out["steps"]["S_sample_dup"]["execution"] == "NOT_RUN"
    assert out["steps"]["leakage_check"]["signals"] == {"feature_timing": "CONCERN_INDICATED"}       # a concern: recorded, nothing stopped
    assert out["steps"]["main"]["provenance"] == "VERIFIED_RUN" and out["steps"]["main"]["assessment"] == "PROVISIONAL"
    reopened = {e.step_id for e in evaluate(spec, facts_from_store(store)) if e.reopened_by}
    assert "feature_construction" in reopened                                    # the domain loop-back fired from the DAG's diagnostic
    report = playbooks.fidelity_report(store, "PLAN_PILOT")
    assert report["adapted"][0]["step"] == "S_label" and {o["reason"] for o in report["omitted"]} == {"replaced_by", "not_applicable"}
    assert report["execution"]["leakage_check"]["diagnostics"][0]["signal"] == "CONCERN_INDICATED" and report["execution"]["robust"]["divergences"][0]["outcome"] == "EXCEEDS_DECLARED_TOLERANCE"

    # ---- interpretation -> 5a: claim change and promise check ----------------------------------------------------------------------
    dag.interpret_results(store, token, "PLAN_PILOT", [{
        "claim_id": "CLAIM_MAIN", "effect": "LIMITS", "depends_on": ["main", "robust"],
        "reasoning": "Average precision halves on the out-of-industry holdout, beyond the declared tolerance."}], interpretation_id="INTERP_PILOT")
    change = loop.assess_claim_change(store, token, "CLAIM_MAIN", change_id="CHG_MAIN")
    assert change["change"] == "NARROWED" and change["detail"] == {"narrowed_on": ["sample"]} and change["assessment"] == "PROVISIONAL"
    loop.link_promises(store, token, intro["argument_id"], {"proposed_contribution.1": ["CLAIM_MAIN"]})
    check = loop.promise_check(store, token, intro["argument_id"], check_id="PROMISE_PILOT")
    assert check["promises"][0]["status"] == "AT_RISK" and check["manuscript_revision_proposal"]["suggestions"][0]["applied"] is False
    assert store.get_object(fx.FOCUS)["revision"] == 1                           # the manuscript text was not touched

    # ---- the whole chain: nothing was verified without the researcher, everything is registered -----------------------------------
    assert store.get_object("CHG_MAIN")["content"]["assessment"] == "PROVISIONAL" and store.get_object("CLAIM_MAIN")["content"]["attributes"]["risk_tier"] == "HIGH"
    assert eca.statement_assessment(store, eca.resolve_statement(store, "CLAIM_MAIN"))["assessment"] != "VERIFIED_WITHIN_SCOPE"
    assert [i["state"] for i in eca.queue(store) if i["statement"]["object_id"] == "CLAIM_MAIN"] == ["NOT_ADJUDICATED"]
    stages = {w: len(runs_of(store, w)) for w in ("reading_protocol", "literature_review", "contribution_comparison", "eca_adjudication", "execution", "domain_step")}
    assert all(n > 0 for n in stages.values()), stages
    cov = eca.coverage_report(store)
    assert any(s["object_id"] == "CLAIM_MAIN" for s in cov["high_not_adjudicated"])     # the coverage policy sees the open high-impact claim
    done = loop.confirm_claim_change(store, token, "CHG_MAIN", rationale="The claim holds only within the industries examined.")
    assert done["assessment"] == "VERIFIED_WITHIN_SCOPE"
