"""Decision D15: adjudicate every HIGH statement, a stratified 20% of STANDARD ones, more where errors appear; whatever a
material conclusion rests on is HIGH."""
from __future__ import annotations

import pytest

from research_harness.knowledge import corpus
from research_harness.state.store import StateStore
from research_harness.workflows import eca
from research_harness.workflows.adapters import ScriptedModelAdapter
from tests import m3_fixture as fx

QUOTE = "drop firms with fewer than three consecutive annual observations"


@pytest.fixture
def proj(tmp_path):
    store, token = StateStore.create(tmp_path / "home", "PRJ_SAMPLE", owner_id="researcher")
    fx.import_corpus(store, token, sources=(fx.PAPER_A,))
    corpus.add_passage(store, token, source_id=fx.PAPER_A, quote=QUOTE, passage_id="EP_Q")
    with store.write_tx(token=token) as tx:
        for i in range(10):
            tx.add_revision(object_id=f"CLAIM_S{i:02d}", object_type="Claim", origin={}, edges=[],
                            content={"text": f"Standard statement number {i}.", "claim_tier": "descriptive"})
        for i in range(3):
            tx.add_revision(object_id=f"CLAIM_H{i}", object_type="Claim", origin={}, edges=[],
                            content={"text": f"High-impact statement {i}.", "claim_tier": "predictive", "attributes": {"risk_tier": "HIGH"}})
        tx.add_revision(object_id="CLAIM_LONE", object_type="Claim", origin={}, edges=[], content={"text": "Alone.", "claim_tier": "descriptive"})
        tx.add_revision(object_id="KI_X", object_type="KnowledgeItem", origin={}, edges=[], content={
            "label": "x", "kind": "checklist", "elements": [{"id": "C1", "text": "Check one.", "evidence_basis": []}]})
    return store, token


def adapter(verdict="SUPPORTED_AS_STATED", equivalence="EQUIVALENT"):
    return ScriptedModelAdapter(model_id="scripted-adjudicator", default_response={
        "passage_proposition": "p", "equivalence": equivalence, "dropped_conditions": [], "cue_handling": [], "verdict": verdict, "required_qualification": []})


def sampled_ids(plan):
    return sorted(s["object_id"] for s in plan["standard_sampled"])


def test_every_high_statement_and_a_stratified_sample_of_the_rest(proj):
    store, _ = proj
    plan = eca.sampling_plan(store)
    assert sorted(s["object_id"] for s in plan["high"]) == ["CLAIM_H0", "CLAIM_H1", "CLAIM_H2"]
    claim = plan["by_stratum"]["Claim"]
    assert claim["statements"] == 11 and claim["rate"] == 0.2 and claim["sampled"] == 3        # ceil(0.2 * 11), deterministic
    assert plan["by_stratum"]["KnowledgeItem:element"]["sampled"] == 1                          # at least one per stratum
    assert sampled_ids(plan) == sampled_ids(eca.sampling_plan(store))                           # same seed, same sample
    assert plan["policy"]["standard_rate"] == 0.2 and "doubles" in plan["policy"]["escalation"]


def test_coverage_report_lists_what_is_still_to_adjudicate(proj):
    store, token = proj
    cov = eca.coverage_report(store)
    assert cov["high_total"] == 3 and cov["high_adjudicated"] == 0 and len(cov["high_not_adjudicated"]) == 3 and cov["complete"] is False
    for oid in ("CLAIM_H0", "CLAIM_H1", "CLAIM_H2"):
        eca.adjudicate(store, token, eca.resolve_statement(store, oid), "EP_Q", adapter())
    cov = eca.coverage_report(store)
    assert cov["high_adjudicated"] == 3 and cov["high_not_adjudicated"] == [] and cov["complete"] is False        # the sample is still open
    for s in cov["sample_not_adjudicated"]:
        eca.adjudicate(store, token, eca.resolve_statement(store, s["object_id"], element=s["element"]), "EP_Q", adapter())
    assert eca.coverage_report(store)["complete"] is True


def test_the_rate_doubles_for_each_problem_found_in_a_stratum(proj):
    store, token = proj
    assert eca.escalated_rate(0.2, 0) == 0.2 and eca.escalated_rate(0.2, 1) == 0.4 and eca.escalated_rate(0.2, 2) == 0.8 and eca.escalated_rate(0.2, 3) == 1.0
    before = eca.sampling_plan(store)
    eca.adjudicate(store, token, eca.resolve_statement(store, "CLAIM_S00"), "EP_Q", adapter(verdict="NOT_SUPPORTED", equivalence="DIFFERENT"))
    assert eca.error_counts(store) == {"Claim": 1}
    after = eca.sampling_plan(store)
    assert after["by_stratum"]["Claim"]["rate"] == 0.4 and after["by_stratum"]["Claim"]["sampled"] == 5 > before["by_stratum"]["Claim"]["sampled"]
    assert after["by_stratum"]["KnowledgeItem:element"]["rate"] == 0.2                          # other strata are not affected
    # a guard that fired is a problem too, even though the model said "supported"
    wrong = ScriptedModelAdapter(default_response={"passage_proposition": "p", "equivalence": "BROADER", "dropped_conditions": ["x"], "cue_handling": [],
                                                   "verdict": "SUPPORTED_AS_STATED", "required_qualification": []})
    eca.adjudicate(store, token, eca.resolve_statement(store, "CLAIM_S01"), "EP_Q", wrong)
    assert eca.error_counts(store) == {"Claim": 2} and eca.sampling_plan(store)["by_stratum"]["Claim"]["rate"] == 0.8


def test_whatever_a_conclusion_rests_on_becomes_high(proj):
    store, token = proj
    res = eca.use_in_conclusion(store, token, [("CLAIM_S05", None), ("CLAIM_H0", None), ("KI_X", "C1")])
    assert [m["object_id"] for m in res["made_high"]] == ["CLAIM_S05", "KI_X"] and res["already_high"] == 1
    plan = eca.sampling_plan(store)
    assert "CLAIM_S05" in {s["object_id"] for s in plan["high"]} and ("KI_X", "C1") in {(s["object_id"], s["element"]) for s in plan["high"]}
    assert eca.resolve_statement(store, "CLAIM_S05").risk_tier == "HIGH"
    # an adjudication made before the promotion stays valid: the statement's words did not change
    s = eca.resolve_statement(store, "CLAIM_S06")
    eca.adjudicate(store, token, s, "EP_Q", adapter())
    eca.use_in_conclusion(store, token, [("CLAIM_S06", None)])
    assert len(eca.statement_assessment(store, eca.resolve_statement(store, "CLAIM_S06"))["adjudications"]) == 1
