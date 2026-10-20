"""Phase 6.2 (plan v3): Gate K tooling, exercised end to end on synthetic material. The "researcher" here is the test; in the real
evaluation every reference answer and score is the researcher's own."""
from __future__ import annotations

import json

import pytest

from research_harness.benchmarks import gate_k
from research_harness.knowledge import corpus, lifecycle, literature, playbooks
from research_harness.knowledge.common import KnowledgeError
from research_harness.knowledge.replication import add_replication_package
from research_harness.state.store import StateStore
from research_harness.workflows import eca
from research_harness.workflows.adapters import ScriptedModelAdapter
from tests import m3_fixture as fx
from tests.library_fixture import PANEL_CSV

CTX = {"domain": "accounting", "subfield": "archival_financial_reporting", "study_design": "predictive_modeling",
       "estimand": "probability of a later restatement", "measurement": "label = amended annual report"}
SCOPE = {"study_design": "predictive_modeling", "subfield": "archival_financial_reporting"}


def filled_form(source=fx.PAPER_A, annotator="the researcher"):
    form = gate_k.blank_annotation_form([source], "PKG_PANEL")
    form["annotator"] = annotator
    answers = {
        "label_source_and_timing": ("The label is one when the firm later filed an amended annual report for an accounting error.",
                                    "The outcome label equals one when the firm later filed an amended annual report for an accounting error"),
        "sample_construction": ("Firms with fewer than three consecutive annual observations are dropped.", "drop firms with fewer than three consecutive annual observations"),
        "estimand_or_prediction_target": ("Whether a later amended report follows.", "The outcome label equals one when the firm later filed an amended annual report"),
        "standard_errors_or_validation_scheme": ("Train on earlier years, test on the last two.", "Models are validated by training on the earlier years and testing on the last two years"),
    }
    for e in form["elements"]:
        if e["id"] in answers:
            e["reference"], e["quote"] = answers[e["id"]]
            e["source_id"] = source
        else:
            e["not_stated"], e["not_stated_reason"] = True, "the paper does not describe this"
    return form


def eca_ok():
    return ScriptedModelAdapter(model_id="scripted-adjudicator", default_response={
        "passage_proposition": "p", "equivalence": "EQUIVALENT", "dropped_conditions": [], "cue_handling": [], "verdict": "SUPPORTED_AS_STATED", "required_qualification": []})


def build_system(store, token, tmp, *, adapt=True):
    pkg = tmp / "pkg"
    pkg.mkdir(exist_ok=True)
    (pkg / "panel.csv").write_bytes(PANEL_CSV.encode("utf-8"))
    add_replication_package(store, token, package_id="PKG_PANEL", label="panel", directory=pkg, files=["panel.csv"], entitlement={"status": "confirmed", "license": "synthetic", "basis": "test"},
                            expected_outputs=[{"id": "pos", "file": "panel.csv", "column": "label", "stat": "positive_rate", "expected": 0.25, "tolerance": 1e-9}])
    review = literature.run_literature(store, token, "method", [fx.PAPER_A], fx.extractor())
    playbooks.draft_playbook_from_methods(store, token, review, playbook_id="PB_K", label="pb", scope=SCOPE, replication_package="PKG_PANEL", replication_links={"estimand": ["pos"]})
    for e in store.get_object("PB_K")["content"]["elements"]:
        r = eca.adjudicate(store, token, eca.resolve_statement(store, "PB_K", element=e["id"]), e["evidence_basis"][0]["ref"], eca_ok())
        eca.confirm(store, token, r["adjudication_id"], verdict="SUPPORTED_AS_STATED", rationale="Reads the passage correctly.")
    ref = store.get_object("PB_K")["content"]["elements"][0]["evidence_basis"][0]["ref"]
    lifecycle.validate(store, token, "PB_K", assessment={"context": CTX, "confirmed_by_researcher": True, "evidence_basis": [{"kind": "source_passage", "ref": ref, "methodological_literature": True}]})
    els = {e["category"]: e["id"] for e in store.get_object("PB_K")["content"]["elements"]}
    steps = [{"id": "sample", "element": els["sample_construction"], "fidelity": "inherited", "command": ["python", "x.py"]},
             {"id": "label", "element": els["estimand"], "fidelity": "adapted" if adapt else "inherited", "command": ["python", "x.py"],
              **({"changes": "window shortened", "rationale": "data end earlier"} if adapt else {})},
             {"id": "valid", "element": els["validation_scheme"], "fidelity": "inherited", "command": ["python", "x.py"]}]
    plan = playbooks.draft_analysis_plan(store, "PB_K", plan_id="PLAN_K", label="plan", context=CTX, steps=steps)
    assert playbooks.submit_operations(store, token, plan["operations"], proposal_id="PROP_K")["decision"] == "ACCEPTED"


def scores(**over):
    base = {"label_source_and_timing": ("JUSTIFIED_ADAPTATION", "window shortened, recorded in the plan"), "sample_construction": ("MATCH", ""),
            "estimand_or_prediction_target": ("JUSTIFIED_ADAPTATION", "same step as the label"), "specification_or_feature_set": ("MATCH", ""),
            "standard_errors_or_validation_scheme": ("MATCH", ""), "diagnostics": ("MATCH", ""), "interpretation_limits": ("MATCH", "")}
    base.update(over)
    return [{"element": k, "score": v[0], "note": v[1]} for k, v in base.items()]


@pytest.fixture
def world(tmp_path):
    store, token = StateStore.create(tmp_path / "home", "PRJ_GK", owner_id="researcher")
    fx.import_corpus(store, token, sources=(fx.PAPER_A,))
    return {"store": store, "token": token, "tmp": tmp_path}


def test_the_form_asks_the_seven_elements_and_leaves_every_answer_empty():
    form = gate_k.blank_annotation_form([fx.PAPER_A])
    assert [e["id"] for e in form["elements"]] == list(gate_k.ELEMENTS) and len(form["elements"]) == 7
    assert all(e["reference"] == "" and e["quote"] == "" and e["not_stated"] is False for e in form["elements"])
    assert "BEFORE you look at anything the system produced" in form["instructions"]


def test_an_incomplete_or_unverifiable_annotation_is_refused(world):
    store, token = world["store"], world["token"]
    form = filled_form()
    form["annotator"] = ""
    form["elements"][0]["reference"] = ""
    form["elements"][1]["quote"] = "a sentence that is not in the paper"
    form["elements"][4]["not_stated"], form["elements"][4]["reference"] = True, ""
    with pytest.raises(KnowledgeError, match="ANNOTATION_INCOMPLETE") as e:
        gate_k.record_reference_annotation(store, token, form)
    probs = " | ".join(e.value.details["problems"])
    assert "annotator is not named" in probs and "no reference answer" in probs and "quote is not in the stored text" in probs and "not_stated needs a reason" in probs


def test_gate_k_passes_on_a_blind_annotation_and_a_faithful_plan(world):
    store, token, tmp = world["store"], world["token"], world["tmp"]
    ann = gate_k.record_reference_annotation(store, token, filled_form())                 # before any system output
    assert gate_k.blindness(store, ann["annotation_id"])["blind"] is True
    build_system(store, token, tmp)
    blind = gate_k.blindness(store, ann["annotation_id"])
    assert blind["blind"] is True and blind["system_outputs_after_annotation"] > 0
    sheet = gate_k.comparison_sheet(store, ann["annotation_id"], "PB_K", "PLAN_K")
    assert all(r["score"] == "" and r["note"] == "" for r in sheet["rows"])                # the system leaves the scoring to the researcher
    rows = {r["element"]: r for r in sheet["rows"]}
    assert rows["label_source_and_timing"]["system"][0]["plan_steps"][0]["fidelity"] == "adapted" and rows["diagnostics"]["system"] == []
    res = gate_k.record_scores(store, token, ann["annotation_id"], "PB_K", "PLAN_K", scores(), scorer="the researcher")
    assert {s["element"]: s["flagged_in_fidelity_report"] for s in res["scores"]}["label_source_and_timing"] is True
    report = gate_k.evaluate_gate_k(store, res["scores_id"], package_id="PKG_PANEL", real_pilot=False)
    assert report["passed"] is True and all(report[k]["passed"] for k in ("K1", "K2", "K3", "K4"))
    assert report["K3"]["reproduction"]["computational_reproducibility"] == "REPRODUCED_WITHIN_TOLERANCE" and report["K4"]["assessments"] == 1


def test_an_annotation_stored_after_system_output_is_not_blind(world):
    store, token, tmp = world["store"], world["token"], world["tmp"]
    build_system(store, token, tmp)                                                         # the system has already read the paper
    ann = gate_k.record_reference_annotation(store, token, filled_form(), annotation_id="REFANN_LATE")
    blind = gate_k.blindness(store, ann["annotation_id"])
    assert blind["blind"] is False and any(w.startswith("reading_protocol") for w in blind["system_outputs_before_annotation"])
    with pytest.raises(KnowledgeError, match="ANNOTATION_NOT_BLIND"):
        gate_k.record_scores(store, token, ann["annotation_id"], "PB_K", "PLAN_K", scores(), scorer="the researcher")
    with pytest.raises(KnowledgeError, match="ANNOTATION_EXISTS"):
        gate_k.record_reference_annotation(store, token, filled_form(), annotation_id="REFANN_LATE")      # never rewritten after the fact


def test_scores_must_be_coherent_with_what_the_plan_and_playbook_record(world):
    store, token, tmp = world["store"], world["token"], world["tmp"]
    ann = gate_k.record_reference_annotation(store, token, filled_form())["annotation_id"]
    build_system(store, token, tmp, adapt=False)                                            # nothing adapted in the plan
    def attempt(**over):
        return gate_k.record_scores(store, token, ann, "PB_K", "PLAN_K", scores(**over), scorer="the researcher")
    with pytest.raises(KnowledgeError, match="SCORES_INCOMPLETE") as e:
        attempt()
    assert "JUSTIFIED_ADAPTATION needs an adapted step" in " ".join(e.value.details["problems"])
    with pytest.raises(KnowledgeError, match="needs a note"):
        attempt(label_source_and_timing=("MATCH", ""), estimand_or_prediction_target=("MISMATCH", ""))
    with pytest.raises(KnowledgeError, match="SCORER_NOT_DESIGNATED"):
        gate_k.record_scores(store, token, ann, "PB_K", "PLAN_K", scores(), scorer="somebody else")


def test_a_mismatch_that_the_report_does_not_flag_fails_k2(world):
    store, token, tmp = world["store"], world["token"], world["tmp"]
    ann = gate_k.record_reference_annotation(store, token, filled_form())["annotation_id"]
    build_system(store, token, tmp, adapt=False)
    res = gate_k.record_scores(store, token, ann, "PB_K", "PLAN_K", scores(
        label_source_and_timing=("MATCH", ""), estimand_or_prediction_target=("MISMATCH", "the plan tests a different target"),
        sample_construction=("PARTIAL", "the filter threshold differs")), scorer="the researcher")
    report = gate_k.evaluate_gate_k(store, res["scores_id"], package_id="PKG_PANEL")
    assert report["passed"] is False and report["K2"]["passed"] is False
    assert report["K2"]["unflagged_mismatch"] == ["estimand_or_prediction_target"] and {s["element"] for s in report["K2"]["not_passing"]} == {"estimand_or_prediction_target", "sample_construction"}
    assert report["K1"]["passed"] and report["K3"]["passed"] and report["K4"]["passed"]       # the other conditions are judged separately


def test_k3_and_k4_are_computed_not_assumed(world):
    store, token, tmp = world["store"], world["token"], world["tmp"]
    ann = gate_k.record_reference_annotation(store, token, filled_form())["annotation_id"]
    build_system(store, token, tmp)
    res = gate_k.record_scores(store, token, ann, "PB_K", "PLAN_K", scores(), scorer="the researcher")
    assert gate_k.evaluate_gate_k(store, res["scores_id"])["K3"]["passed"] is False                  # no package named: nothing reproduced
    pkg = store.get_object("PKG_PANEL")
    store.artifacts.path_for(pkg["content"]["body"]["files"][0]["sha256"]).write_bytes(b"firm,label\nF1,1\n")
    assert gate_k.evaluate_gate_k(store, res["scores_id"], package_id="PKG_PANEL")["K3"]["passed"] is False


def test_exported_artifacts_are_checked_and_a_synthetic_run_is_never_a_real_pilot(world):
    store, token, tmp = world["store"], world["token"], world["tmp"]
    ann = gate_k.record_reference_annotation(store, token, filled_form())["annotation_id"]
    build_system(store, token, tmp)
    res = gate_k.record_scores(store, token, ann, "PB_K", "PLAN_K", scores(), scorer="the researcher")
    out = tmp / "gate_k"
    manifest = gate_k.export_artifacts(store, res["scores_id"], out, package_id="PKG_PANEL", real_pilot=False)
    assert manifest["passed"] is True and set(manifest["files"]) >= {"reference_annotation.json", "scores.json", "gate_k_report.json", "comparison_sheet.json"}
    assert gate_k.validate_artifacts(out) == {"ok": False, "problems": ["the artefacts are not marked as a real pilot"]}
    real = gate_k.export_artifacts(store, res["scores_id"], tmp / "gate_k_real", package_id="PKG_PANEL", real_pilot=True)
    assert real["real_pilot"] is True and gate_k.validate_artifacts(tmp / "gate_k_real") == {"ok": True, "problems": []}
    # tampering with a file, or with the order of events, is caught
    (tmp / "gate_k_real" / "scores.json").write_text("{}", encoding="utf-8")
    assert "scores.json is missing or does not match its recorded hash" in gate_k.validate_artifacts(tmp / "gate_k_real")["problems"]
    assert gate_k.validate_artifacts(tmp / "nowhere")["ok"] is False


def test_the_system_cannot_match_what_it_does_not_have_nor_what_the_paper_does_not_state(world):
    store, token, tmp = world["store"], world["token"], world["tmp"]
    form = filled_form()
    for e in form["elements"]:
        if e["id"] == "diagnostics":      # the paper states diagnostics; the playbook has none
            e.update(not_stated=False, not_stated_reason="", reference="Checks leakage between folds.", quote="", source_id="")
        if e["id"] == "sample_construction":   # the paper states no sample construction; the playbook has an element for it
            e.update(not_stated=True, not_stated_reason="not described", reference="", quote="", source_id="")
    ann = gate_k.record_reference_annotation(store, token, form)["annotation_id"]
    build_system(store, token, tmp)
    with pytest.raises(KnowledgeError, match="SCORES_INCOMPLETE") as e:
        gate_k.record_scores(store, token, ann, "PB_K", "PLAN_K", scores(), scorer="the researcher")
    probs = " | ".join(e.value.details["problems"])
    assert "diagnostics: the paper states this and the playbook has no element for it" in probs
    assert "sample_construction: the paper does not state this but the playbook has an element for it" in probs
    res = gate_k.record_scores(store, token, ann, "PB_K", "PLAN_K", scores(diagnostics=("MISMATCH", "the system has no diagnostics element"),
                                                                            sample_construction=("PARTIAL", "the system added a sample step")), scorer="the researcher")
    assert gate_k.evaluate_gate_k(store, res["scores_id"], package_id="PKG_PANEL")["K2"]["unflagged_mismatch"] == ["diagnostics"]
