"""Synthetic logic benchmark (plan v3, 6.1).

Each case builds its own tiny project from invented text, runs the real code with scripted model answers, and records what
was *observed*. A case passes only if the observation equals what the design requires. Every case is run a second time with
one input perturbed, and the observation must change: a benchmark whose answers are hard-coded would not.

This confirms that the software follows the designed logic. It says nothing about whether the software is useful to a
researcher; that is what Gates K and L are for.
"""
from __future__ import annotations

import itertools
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional

from research_harness.knowledge import closure, corpus, lifecycle
from research_harness.knowledge.library import add_object, create_library, pin
from research_harness.knowledge.replication import reproduce_package
from research_harness.state.store import StateStore
from research_harness.workflows import eca
from research_harness.workflows.adapters import ScriptedModelAdapter
from research_harness.workflows.domain_engine import evaluate, facts_from_store, load_builtin_spec, next_steps
from research_harness.workflows.status import (
    Applicability, AssessmentStatus, ContextStatus, ExecutionStatus, apply_caps)

SOURCE_TEXT = (
    "SYNTHETIC BENCHMARK TEXT. Invented for the benchmark.\n\n1 Introduction\nWe study panel models.\n\n3 Method\n"
    "The estimator performs well only when every firm is observed in every year of the estimation window. "
    "Standard errors are clustered by firm.\n\n4 Results\nAccuracy differs across industries.\n")
QUOTE = "The estimator performs well only when every firm is observed in every year of the estimation window"
WIDE = "The estimator is suitable for panel data."
PANEL_CSV = "firm,label\n" + "\n".join(f"F{i},{1 if i in (2, 5) else 0}" for i in range(1, 9)) + "\n"


def _adapter(**kw: Any) -> ScriptedModelAdapter:
    base = {"passage_proposition": "The estimator performs well only for balanced panels.", "equivalence": "BROADER",
            "dropped_conditions": ["every firm observed in every year"], "cue_handling": [{"cue": "only when", "handling": "dropped"}],
            "verdict": "NOT_SUPPORTED", "required_qualification": []}
    base.update(kw)
    return ScriptedModelAdapter(model_id="benchmark-adjudicator", default_response=base)


def _project(root: Path, name: str) -> tuple[StateStore, str]:
    store, token = StateStore.create(root / "home", name, owner_id="researcher")
    corpus.import_source(store, token, citation_key="bench2026", text=SOURCE_TEXT)
    corpus.add_passage(store, token, source_id="SRC_BENCH2026", quote=QUOTE, passage_id="EP_BALANCE")
    return store, token


def _claim(store: StateStore, token: str, oid: str, text: str = WIDE, **attrs: Any) -> None:
    with store.write_tx(token=token) as tx:
        tx.add_revision(object_id=oid, object_type="Claim", origin={}, edges=[],
                        content={"text": text, "claim_tier": "predictive", "attributes": attrs})


def _assessment(store: StateStore, oid: str) -> str:
    return eca.statement_assessment(store, eca.resolve_statement(store, oid))["assessment"]


# --------------------------------------------------------------------------------------------------
# cases: each returns (observed, perturbed_observed)
# --------------------------------------------------------------------------------------------------

def case_eca_wrong_interpretation(root: Path) -> tuple[Any, Any]:
    out = []
    for n, variant in enumerate(("model_sees_broader", "model_wrongly_equivalent_cue_unaccounted")):
        store, token = _project(root, f"BENCH_ECA1_{n}")
        _claim(store, token, "CLAIM_W")
        if variant == "model_sees_broader":
            adapter = _adapter()
        else:
            adapter = _adapter(equivalence="EQUIVALENT", dropped_conditions=[], cue_handling=[], verdict="SUPPORTED_AS_STATED")
        res = eca.adjudicate(store, token, eca.resolve_statement(store, "CLAIM_W"), "EP_BALANCE", adapter)
        out.append({"quote_verifies": corpus.verify_passage(store, "EP_BALANCE")["source_match"], "verdict": res["eca_verdict"], "guard": res["guard"],
                    "assessment": _assessment(store, "CLAIM_W")})
    # perturbation: a statement that really is equivalent, with its cue carried, is supported (and still only provisional)
    store, token = _project(root, "BENCH_ECA1_P")
    _claim(store, token, "CLAIM_W", "The estimator performs well only when every firm is observed in every year.")
    good = _adapter(equivalence="EQUIVALENT", dropped_conditions=[], cue_handling=[{"cue": "only when", "handling": "carried"}], verdict="SUPPORTED_AS_STATED")
    res = eca.adjudicate(store, token, eca.resolve_statement(store, "CLAIM_W"), "EP_BALANCE", good)
    return out, {"verdict": res["eca_verdict"], "guard": res["guard"], "assessment": _assessment(store, "CLAIM_W")}


def case_eca_dropped_condition(root: Path) -> tuple[Any, Any]:
    def run(dropped: list[str], name: str) -> dict[str, Any]:
        store, token = _project(root, name)
        _claim(store, token, "CLAIM_D")
        a = _adapter(equivalence="NARROWER", dropped_conditions=dropped, cue_handling=[{"cue": "only when", "handling": "carried"}],
                     verdict="SUPPORTED_AS_STATED")
        r = eca.adjudicate(store, token, eca.resolve_statement(store, "CLAIM_D"), "EP_BALANCE", a)
        return {"verdict": r["eca_verdict"], "guard": r["guard"]}
    return run(["balanced panel"], "BENCH_ECA2_A"), run([], "BENCH_ECA2_B")


def case_eca_counter_evidence(root: Path) -> tuple[Any, Any]:
    def run(challenge: bool, name: str) -> str:
        store, token = _project(root, name)
        _claim(store, token, "CLAIM_C", "The estimator needs a balanced panel.", generated_by="researcher")
        ok = _adapter(equivalence="EQUIVALENT", dropped_conditions=[], cue_handling=[{"cue": "only when", "handling": "carried"}], verdict="SUPPORTED_AS_STATED")
        res = eca.adjudicate(store, token, eca.resolve_statement(store, "CLAIM_C"), "EP_BALANCE", ok)
        eca.confirm(store, token, res["adjudication_id"], verdict="SUPPORTED_AS_STATED", rationale="Carries the condition exactly.")
        if challenge:
            with store.write_tx(token=token) as tx:
                tx.add_revision(object_id="EP_COUNTER", object_type="EvidencePassage", origin={}, content={"locator": "x", "quote": "q"},
                                edges=[("challenges", "CLAIM_C", 1, {})])
        return _assessment(store, "CLAIM_C")
    return run(True, "BENCH_ECA3_A"), run(False, "BENCH_ECA3_B")


def case_status_caps(root: Path) -> tuple[Any, Any]:
    """Exhaustive over every combination of the inputs of `apply_caps`: the invariants hold on all of them."""
    violations, total = [], 0
    reachable_verified = 0
    for ex, ctx, app, gen, eca_v, counter, conf, req in itertools.product(
            list(ExecutionStatus), list(ContextStatus), list(Applicability), (None, "researcher", "model:x", "deterministic:y"),
            (None, "SUPPORTED_AS_STATED", "SUPPORTED_IF_QUALIFIED", "NOT_SUPPORTED", "UNRESOLVED"), (False, True), (False, True), list(AssessmentStatus)):
        total += 1
        got = apply_caps(req, execution=ex, context=ctx, applicability=app, generated_by=gen, eca_verdict=eca_v, counter_evidence=counter,
                         researcher_confirmed=conf)
        if got is AssessmentStatus.VERIFIED_WITHIN_SCOPE:
            reachable_verified += 1
            bad = (not conf or ex is not ExecutionStatus.SUCCEEDED or ctx is not ContextStatus.COMPLETE or app is Applicability.NOT_APPLICABLE
                   or counter or eca_v in {"NOT_SUPPORTED", "UNRESOLVED"} or (gen and gen.startswith("model") and eca_v not in {"SUPPORTED_AS_STATED", "SUPPORTED_IF_QUALIFIED"}))
            if bad:
                violations.append((ex.value, ctx.value, app.value, gen, eca_v, counter, conf))
        if (ex is not ExecutionStatus.SUCCEEDED or app is Applicability.NOT_APPLICABLE or ctx is ContextStatus.INSUFFICIENT) and got is not AssessmentStatus.NOT_ASSESSED:
            violations.append(("not_assessed_expected", ex.value, ctx.value, app.value, got.value))
    # perturbation: without the confirmation requirement the answer would be different, so the invariant is doing the work
    perturbed = apply_caps(AssessmentStatus.VERIFIED_WITHIN_SCOPE, researcher_confirmed=True, generated_by="researcher")
    unconfirmed = apply_caps(AssessmentStatus.VERIFIED_WITHIN_SCOPE, researcher_confirmed=False, generated_by="researcher")
    return {"combinations": total, "violations": violations, "verified_reachable_only_with_confirmation": reachable_verified > 0}, \
           {"confirmed": perturbed.value, "unconfirmed": unconfirmed.value}


def case_domain_difference(root: Path) -> tuple[Any, Any]:
    pilot, contrast = load_builtin_spec("LIB_DW_ACC_FRAUD_DETECTION"), load_builtin_spec("LIB_DW_GENERIC")
    store, token = StateStore.create(root / "home", "BENCH_DOM", owner_id="researcher")
    with store.write_tx(token=token) as tx:
        tx.add_revision(object_id="DS_L", object_type="Dataset", origin={}, edges=[], content={"label": "labels", "data_role": "label_source"})
    facts = facts_from_store(store, study_design="predictive_modeling")
    def shape(spec: Any) -> dict[str, Any]:
        ev = evaluate(spec, facts)
        return {"steps": [s.id for s in spec.steps], "runnable": sorted(next_steps(spec, facts)),
                "blocked": sorted(e.step_id for e in ev if not e.runnable and not e.done and e.applicability.value == "APPLICABLE"),
                "loop_backs": sorted(s.id for s in spec.steps if s.on_condition), "stage_instructions_equal": pilot.stage_instructions("results") == contrast.stage_instructions("results")}
    a, b = shape(pilot), shape(contrast)
    differ = {k: a[k] != b[k] for k in ("steps", "runnable", "blocked", "loop_backs")}
    # perturbation: the pilot on a project with a study design outside its own is not applicable where the generic one is
    off = evaluate(pilot, facts_from_store(store, study_design="qualitative_interview"))
    on = sorted({e.stage for e in evaluate(pilot, facts) if e.applicability.value == "APPLICABLE"})
    return {"differ_in": differ, "pilot": {k: v for k, v in a.items() if k != "steps"}, "pilot_applicable_stages_for_predictive": on}, {
        "pilot_applicable_stages_for_qualitative": sorted({e.stage for e in off if e.applicability.value == "APPLICABLE"})}


def case_closure_offline(root: Path) -> tuple[Any, Any]:
    home = root / "home_closure"
    lib, ltoken = create_library(home)
    corpus.import_source(lib, ltoken, citation_key="bench2026", text=SOURCE_TEXT)
    corpus.add_passage(lib, ltoken, source_id="LIB_SRC_BENCH2026", quote=QUOTE, passage_id="EP_BALANCE")
    sha = lib.artifacts.put_bytes(PANEL_CSV.encode())
    pkg = add_object(lib, ltoken, "LIB_KI_PKG", "KnowledgeItem", {"label": "pkg", "kind": "replication_package", "body": {
        "files": [{"name": "p.csv", "sha256": sha}], "expected_outputs": [{"id": "pos", "file": "p.csv", "column": "label", "stat": "positive_rate", "expected": 0.25, "tolerance": 1e-9}]}},
        edges=[("derived_from", "LIB_SRC_BENCH2026", 1)])
    project, ptoken = StateStore.create(home, "BENCH_USE", owner_id="researcher")
    closure.use_in_project(project, ptoken, lib, pkg["pin"])
    os.rename(home / "LIBRARY", home / "LIBRARY_AWAY")
    try:
        verified = closure.verify_closure(project)["status"]
        repro = reproduce_package(project, "LIB_KI_PKG")["computational_reproducibility"]
        updates = closure.check_updates(project, home)["status"]
        project.artifacts.path_for(sha).write_bytes(b"tampered")
        tampered = closure.verify_closure(project)["status"]
    finally:
        os.rename(home / "LIBRARY_AWAY", home / "LIBRARY")
    return {"verified": verified, "reproduces": repro, "library_status": updates}, {"after_tampering": tampered}


def case_scope_mismatch(root: Path) -> tuple[Any, Any]:
    store, token = StateStore.create(root / "home", "BENCH_SCOPE", owner_id="researcher")
    ctx = {"subfield": "archival_financial_reporting", "study_design": "predictive_modeling", "estimand": "restatement"}
    lifecycle.acquire(store, token, item_id="KI_RULE", kind="checklist", label="rule", acquired_from="source",
                      elements=[{"id": "C1", "text": "State the label source.", "evidence_basis": [{"kind": "researcher_judgement", "ref": "owner"}]}])
    obj = store.get_object("KI_RULE")
    with store.write_tx(token=token) as tx:
        c = {**obj["content"], "scope": {"study_design": "predictive_modeling"}, "exclusions": ["label known only after the prediction date"],
             "validation": {**obj["content"]["validation"], "validity_assessments": [lifecycle.assess_validity(
                 store, context=ctx, evidence_basis=[{"kind": "researcher_judgement", "ref": "owner", "methodological_literature": True}], confirmed_by_researcher=True)]}}
        tx.add_revision(object_id="KI_RULE", object_type="KnowledgeItem", content=c, edges=[], origin={})
    def status(context: dict[str, Any]) -> dict[str, Any]:
        row = lifecycle.match(store, context)[0]
        return {"status": row["status"], "because": row["because"]}
    same = status(ctx)
    other_design = status({**ctx, "study_design": "qualitative_interview"})
    excluded = status({**ctx, "conditions": ["label known only after the prediction date"]})
    return {"other_study_design": other_design["status"], "exclusion_hit": excluded["status"], "reasons_shown": bool(other_design["because"] and excluded["because"])}, \
           {"same_scope_is_not_marked_not_applicable": same["status"] != "related_not_applicable"}


CASES: dict[str, tuple[str, Callable[[Path], tuple[Any, Any]], Callable[[Any], bool]]] = {
    "eca_correct_quote_wrong_interpretation": (
        "A verbatim quote with a wrong reading is not verified: contested when seen, UNRESOLVED when the model misses the cue",
        case_eca_wrong_interpretation,
        lambda o: (o[0]["quote_verifies"] == "VERIFIED" and o[0]["verdict"] == "NOT_SUPPORTED" and o[0]["assessment"] == "CONTESTED"
                   and o[1]["verdict"] == "UNRESOLVED" and o[1]["guard"] == "qualifier_cues_not_accounted_for" and o[1]["assessment"] == "PROVISIONAL")),
    "eca_dropped_condition": (
        "SUPPORTED_AS_STATED with a listed dropped condition becomes UNRESOLVED", case_eca_dropped_condition,
        lambda o: o == {"verdict": "UNRESOLVED", "guard": "dropped_conditions_listed_but_supported_as_stated"}),
    "eca_counter_evidence": (
        "A challenging passage makes a confirmed statement CONTESTED", case_eca_counter_evidence, lambda o: o == "CONTESTED"),
    "status_axis_caps": (
        "Over every input combination, VERIFIED needs confirmation, success, complete context, applicability and no contrary ECA or challenge",
        case_status_caps, lambda o: o["violations"] == [] and o["combinations"] > 10000 and o["verified_reachable_only_with_confirmation"]),
    "domain_engines_differ": (
        "The pilot and contrast specs differ in steps, what can run, what is blocked and loop-backs, with identical stage instructions", case_domain_difference,
        lambda o: all(o["differ_in"].values()) and o["pilot"]["stage_instructions_equal"] is True and bool(o["pilot_applicable_stages_for_predictive"])),
    "closure_offline_reproduction": (
        "A project verifies, reproduces and notes the missing library with the library directory renamed away; tampering is caught", case_closure_offline,
        lambda o: o == {"verified": "VERIFIED", "reproduces": "REPRODUCED_WITHIN_TOLERANCE", "library_status": "LIBRARY_UNAVAILABLE"}),
    "scope_mismatch_non_transfer": (
        "An item is not offered across a study-design mismatch or an exclusion hit, and says why", case_scope_mismatch,
        lambda o: o["other_study_design"] == "related_not_applicable" and o["exclusion_hit"] == "related_not_applicable" and o["reasons_shown"]),
}
PERTURBATION_CHECKS: dict[str, Callable[[Any, Any], bool]] = {
    "eca_correct_quote_wrong_interpretation": lambda o, p: p["verdict"] == "SUPPORTED_AS_STATED" and p["guard"] is None and p["assessment"] == "PROVISIONAL",
    "eca_dropped_condition": lambda o, p: p == {"verdict": "SUPPORTED_AS_STATED", "guard": None},
    "eca_counter_evidence": lambda o, p: p == "VERIFIED_WITHIN_SCOPE",
    "status_axis_caps": lambda o, p: p["confirmed"] == "VERIFIED_WITHIN_SCOPE" and p["unconfirmed"] == "PROVISIONAL",
    "domain_engines_differ": lambda o, p: p["pilot_applicable_stages_for_qualitative"] == [] and o["pilot_applicable_stages_for_predictive"] != [],
    "closure_offline_reproduction": lambda o, p: p["after_tampering"] == "FAILED",
    "scope_mismatch_non_transfer": lambda o, p: p["same_scope_is_not_marked_not_applicable"] is True,
}


def run_logic_benchmark(work_dir: Optional[Path] = None) -> dict[str, Any]:
    """Run every case and its perturbation. The report is computed from what was observed."""
    ctx = tempfile.TemporaryDirectory() if work_dir is None else None
    root = Path(work_dir) if work_dir is not None else Path(ctx.name)  # type: ignore[union-attr]
    root.mkdir(parents=True, exist_ok=True)
    cases = []
    try:
        for cid, (description, fn, expect) in CASES.items():
            case_root = root / cid
            case_root.mkdir(exist_ok=True)
            try:
                observed, perturbed = fn(case_root)
                passed = bool(expect(observed if not isinstance(observed, tuple) else observed))
                perturbed_ok = bool(PERTURBATION_CHECKS[cid](observed, perturbed))
                error = None
            except Exception as exc:  # noqa: BLE001 - a crashing case is a failed case, reported
                observed, perturbed, passed, perturbed_ok, error = None, None, False, False, f"{type(exc).__name__}: {exc}"
            cases.append({"id": cid, "description": description, "observed": observed if cid != "status_axis_caps" else {
                k: (v if k != "violations" else len(v)) for k, v in (observed or {}).items()},
                "perturbation": perturbed, "passed": passed, "perturbation_changes_outcome": perturbed_ok, "error": error})
    finally:
        if ctx is not None:
            ctx.cleanup()
    return {"benchmark": "synthetic_logic", "cases": cases, "passed": sum(c["passed"] for c in cases), "total": len(cases),
            "all_passed": all(c["passed"] and c["perturbation_changes_outcome"] for c in cases), "computed_only": True,
            "statement": "This shows that the software follows its designed logic on invented cases. It is not evidence that the software is scientifically useful."}
