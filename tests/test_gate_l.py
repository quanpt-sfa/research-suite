"""Phase 6.3 (plan v3): Gate L machinery, run on invented material. A synthetic run checks the procedure; it can never pass Gate L (L5)."""
from __future__ import annotations

import copy

import pytest

from research_harness.benchmarks import gate_l
from research_harness.knowledge.common import KnowledgeError
from research_harness.state.store import StateStore


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    root = tmp_path_factory.mktemp("gatel")
    return root, gate_l.run_synthetic_gate_l(root)


def test_synthetic_cold_warm_run_follows_the_designed_logic(synth):
    root, r = synth
    rep, cold, warm = r["report"], r["cold"], r["warm"]
    assert r["synthetic_machinery_ok"] is True and rep["rubric_declared_before_runs"] is True
    # L1: the reuse families cost less, measured from the registry
    assert rep["L1"]["passed"] and set(rep["L1"]["families_with_reuse"]) == {"T2", "T3"}
    assert cold["effort"]["model_calls"] > warm["effort"]["model_calls"] == 0 and warm["effort"]["reused_runs"] >= 2
    # L2: quality no worse on every family, within the declared tolerance (T3 is better: validated knowledge was reused)
    assert rep["L2"]["passed"] and rep["L2"]["families"]["T3"]["cold"] == 0.0 and rep["L2"]["families"]["T3"]["warm"] == 1.0
    assert all(f["no_worse"] for f in rep["L2"]["families"].values())
    # L3, L4
    assert rep["L3"]["passed"] and rep["L4"]["passed"] and r["study_c"]["false_transfers"] == 0
    assert r["study_c"]["excluded_with_reasons"][0]["because"] == ["exclusion applies here: label known only after the prediction date"]
    assert r["compatible_context_for_comparison"]["transferred"] == 1                      # the same library does transfer where the scope allows it
    # L5 cannot be met by invented material
    assert rep["L5"]["passed"] is False and r["real_gate_passed"] is False and "never passes Gate L" in r["statement"]
    # effort is the registry's, not a typed number: recompute it from the stores the run left behind
    for name, rec in (("STUDYB_COLD", cold), ("STUDYB_WARM", warm)):
        again = gate_l.effort_from_registry(StateStore.open(root / "home", name))
        assert (again["model_calls"], again["reused_runs"], again["registry_runs"]) == (rec["effort"]["model_calls"], rec["effort"]["reused_runs"], rec["effort"]["registry_runs"])
    assert "scripted" in cold["effort"]["tokens"]["basis"]                                  # the basis of every token figure is stated


def test_compare_detects_each_way_the_gate_can_fail(synth):
    _, r = synth
    rubric = {"tolerance": {"T1": 0.02, "T2": 0.02, "T3": 0.0, "T4": 0.0}}
    declared = "2000-01-01T00:00:00Z"
    cold, warm, study_c = r["cold"], r["warm"], r["study_c"]
    real = {"real_paper": True, "real_code": True, "paper_source": "SRC_X", "package": "PKG_X"}
    base = gate_l.compare(rubric, declared, cold, warm, study_c, real)
    assert base["passed"] is True                                                           # with real evidence recorded, the computed L1-L4 pass
    worse = copy.deepcopy(warm)
    worse["scores"]["T2"]["score"] = 0.5
    assert gate_l.compare(rubric, declared, cold, worse, study_c, real)["L2"]["passed"] is False
    more_errors = copy.deepcopy(warm)
    more_errors["scores"]["T5"]["score"] = 3
    assert gate_l.compare(rubric, declared, cold, more_errors, study_c, real)["L3"]["passed"] is False
    costlier = copy.deepcopy(warm)
    costlier["effort"]["family_units"] = {"T2": 99, "T3": 99}
    assert gate_l.compare(rubric, declared, cold, costlier, study_c, real)["L1"]["passed"] is False
    no_reuse = copy.deepcopy(warm)
    no_reuse["reuse"] = {}
    assert gate_l.compare(rubric, declared, cold, no_reuse, study_c, real)["L1"]["passed"] is False
    leaky = {**study_c, "false_transfers": 1}
    assert gate_l.compare(rubric, declared, cold, warm, leaky, real)["L4"]["passed"] is False
    assert gate_l.compare(rubric, declared, cold, warm, {"false_transfers": 0, "offered": 0, "excluded_with_reasons": []}, real)["L4"]["passed"] is False   # nothing was offered: nothing was tested
    late = gate_l.compare(rubric, "2100-01-01T00:00:00Z", cold, warm, study_c, real)
    assert late["rubric_declared_before_runs"] is False and late["passed"] is False and late["runs_started_before_rubric"] == ["cold", "warm"]
    assert gate_l.compare(rubric, declared, cold, warm, study_c, None)["L5"]["passed"] is False
    assert gate_l.compare(rubric, declared, cold, warm, study_c, {**real, "real_code": False})["L5"]["passed"] is False


def test_task_family_scores_are_computed_from_outputs_and_gold():
    gold = [("S", 10, 20), ("S", 50, 60), ("T", 0, 10)]
    got = gate_l.score_retrieval(gold, [("S", 12, 22), ("S", 200, 210), ("T", 0, 10)])
    assert (got["gold"], got["retrieved"]) == (3, 3) and abs(got["precision"] - 2 / 3) < 1e-9 and abs(got["recall"] - 2 / 3) < 1e-9
    assert gate_l.score_retrieval([], [])["f1"] == 0.0
    texts = {"S": "The sample drops short panels. The label is later amended."}
    g = [{"source": "S", "field": "sample_selection", "quote": "drops short panels"}, {"source": "S", "field": "label_construction", "quote": "The label is later amended"}]
    recs = [{"source": "S", "field": "sample_selection", "char_start": 11, "char_end": 29}, {"source": "S", "field": "label_construction", "char_start": 31, "char_end": 40},
            {"source": "S", "field": "diagnostics", "char_start": 0, "char_end": 5}]
    ext = gate_l.score_extraction(g, recs, texts)
    assert (ext["gold"], ext["records"]) == (2, 3) and abs(ext["recall"] - 1.0) < 1e-9 and abs(ext["precision"] - 2 / 3) < 1e-9
    assert gate_l.score_extraction(g, [{**recs[0], "field": "label_construction"}], texts)["recall"] == 0.0           # right place, wrong field
    rows = [{"item_id": "PB_1", "status": "applicable"}, {"item_id": "PB_2", "status": "applicable"}]
    assert gate_l.score_method_selection({"chosen": ["PB_1"], "status": "applicable"}, rows)["score"] == 1.0
    assert gate_l.score_method_selection({"chosen": ["PB_2"], "status": "applicable"}, rows)["score"] == 0.0
    assert gate_l.score_method_selection({"chosen": ["PB_1"], "status": "applicable"}, [{"item_id": "PB_1", "status": "candidate_validity_not_established_here"}])["score"] == 0.0
    assert gate_l.score_method_selection({"chosen": ["PB_1"], "status": "applicable"}, [])["score"] == 0.0
    assert gate_l.score_fidelity([{"score": "MATCH"}, {"score": "JUSTIFIED_ADAPTATION"}, {"score": "PARTIAL"}, {"score": "MISMATCH"}])["score"] == 0.5


def test_inference_errors_are_counted_from_adjudications(tmp_path):
    from research_harness.knowledge import corpus
    from research_harness.workflows import eca
    from research_harness.workflows.adapters import ScriptedModelAdapter
    store, token = StateStore.create(tmp_path / "home", "PRJ_T5", owner_id="researcher")
    corpus.import_source(store, token, citation_key="gla2026", text=gate_l.PAPER_A)
    corpus.add_passage(store, token, source_id="SRC_GLA2026", quote="We drop firms with fewer than three consecutive annual observations", passage_id="EP_Q")
    with store.write_tx(token=token) as tx:
        for i in range(3):
            tx.add_revision(object_id=f"CLAIM_{i}", object_type="Claim", origin={}, edges=[], content={"text": f"statement {i}", "claim_tier": "descriptive"})
    def ad(verdict, eq="EQUIVALENT", dropped=()):
        return ScriptedModelAdapter(default_response={"passage_proposition": "p", "equivalence": eq, "dropped_conditions": list(dropped), "cue_handling": [],
                                                      "verdict": verdict, "required_qualification": []})
    eca.adjudicate(store, token, eca.resolve_statement(store, "CLAIM_0"), "EP_Q", ad("SUPPORTED_AS_STATED"))
    eca.adjudicate(store, token, eca.resolve_statement(store, "CLAIM_1"), "EP_Q", ad("NOT_SUPPORTED", "DIFFERENT"))
    eca.adjudicate(store, token, eca.resolve_statement(store, "CLAIM_2"), "EP_Q", ad("SUPPORTED_AS_STATED", "BROADER", ["a condition"]))
    out = gate_l.count_inference_errors(store, extra_flags=[{"flag": "overgeneralisation", "by": "researcher"}])
    assert out["score"] == 3 and len(out["from_adjudications"]) == 2 and len(out["researcher_flags"]) == 1


def test_a_rubric_is_complete_and_never_rewritten(tmp_path):
    store, token = StateStore.create(tmp_path / "home", "GL_R", owner_id="researcher")
    with pytest.raises(KnowledgeError, match="RUBRIC_INCOMPLETE"):
        gate_l.declare_rubric(store, token, {"tolerance": {"T1": 0.0}, "gold": {}})
    rubric = {"tolerance": {f: 0.0 for f in gate_l.QUALITY_FAMILIES}, "gold": {f: [] for f in ("T1", "T2", "T3", "T4")}}
    gate_l.declare_rubric(store, token, rubric)
    with pytest.raises(KnowledgeError, match="RUBRIC_EXISTS"):
        gate_l.declare_rubric(store, token, rubric)
    with pytest.raises(KnowledgeError, match="RUN_RECORD_INCOMPLETE"):
        gate_l.run_record("x", started_at="2026-01-01T00:00:00Z", scores={"T1": {"score": 1}}, effort={}, reuse={})
