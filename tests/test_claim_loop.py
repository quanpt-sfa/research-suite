"""Phase 5a (plan v3): claim change from the interpretation layer, and the check of the Introduction's promises."""
from __future__ import annotations

import pytest

from research_harness.execution import dag
from research_harness.knowledge import argument, loop
from research_harness.knowledge.common import KnowledgeError
from research_harness.workflows import eca
from tests import m3_fixture as fx
from tests.test_results_dag import make_plan, run, std_steps, world  # noqa: F401  (the DAG fixture is reused)


def effect(claim, kind, steps=("main", "robust"), **extra):
    return {"claim_id": claim, "effect": kind, "reasoning": f"{kind} because of the diagnostics and the robustness check.", "depends_on": list(steps), **extra}


def add_claim(store, token, cid, text="The model transfers across industries."):
    with store.write_tx(token=token) as tx:
        tx.add_revision(object_id=cid, object_type="Claim", origin={}, edges=[], content={"text": text, "claim_tier": "predictive"})


@pytest.fixture
def ran(world):  # noqa: F811
    make_plan(world, std_steps())          # the robustness step diverges by 0.05 against a declared tolerance of 0.02
    run(world)
    add_claim(world["store"], world["token"], "CLAIM_T")
    return world


def interpret(w, effects, iid="INTERP_1"):
    return dag.interpret_results(w["store"], w["token"], "PLAN_X", effects, interpretation_id=iid)


@pytest.mark.parametrize("effects,divergences,expected", [
    ([], [], None),
    ([{"effect": "NOT_ASSESSED"}], [], None),
    ([{"effect": "NO_BEARING"}], [], "UNCHANGED"),
    ([{"effect": "STRENGTHENS"}, {"effect": "NO_BEARING"}], [], "STRENGTHENED"),
    ([{"effect": "LIMITS"}], [], "QUALIFIED"),
    ([{"effect": "LIMITS"}], [{"dimension": "period", "outcome": "WITHIN_DECLARED_TOLERANCE"}], "QUALIFIED"),
    ([{"effect": "LIMITS"}], [{"dimension": "period", "outcome": "EXCEEDS_DECLARED_TOLERANCE"}], "NARROWED"),
    ([{"effect": "STRENGTHENS"}, {"effect": "LIMITS"}], [], "QUALIFIED"),
    ([{"effect": "CHANGES"}], [], "WEAKENED"),
    ([{"effect": "CHANGES", "evidence_kind": "judgement", "reverses": True}], [], "WEAKENED"),            # a reversal needs structured evidence
    ([{"effect": "CHANGES", "evidence_kind": "structured", "reverses": True}], [], "REVERSED"),
    ([{"effect": "CHANGES", "evidence_kind": "judgement", "contradiction": {"x": 1}}], [], "WEAKENED"),
    ([{"effect": "CHANGES", "evidence_kind": "structured", "contradiction": {"estimate": -0.2, "ci": [-0.3, -0.1]}}], [], "NOT_SUPPORTED"),
])
def test_claim_change_table(effects, divergences, expected):
    assert loop.derive_change(effects, divergences)[0] == expected


def test_claim_change_is_high_risk_provisional_until_confirmed(ran):
    store, token = ran["store"], ran["token"]
    interpret(ran, [effect("CLAIM_T", "LIMITS")])
    out = loop.assess_claim_change(store, token, "CLAIM_T", change_id="CHG_T")
    assert out["change"] == "NARROWED" and out["detail"] == {"narrowed_on": ["period"]} and out["status"] == "REQUIRES_VERIFICATION"
    assert out["assessment"] == "PROVISIONAL"
    stored = store.get_object("CHG_T")["content"]
    assert stored["risk_tier"] == "HIGH" and stored["layer"] == "claim_change" and stored["researcher_confirmed"] is False
    assert stored["divergences"][0]["dimension"] == "period" and stored["divergences"][0]["outcome"] == "EXCEEDS_DECLARED_TOLERANCE"
    # the claim and what the change rests on are HIGH from now on (D15); its words are untouched, so adjudications stay valid
    claim = store.get_object("CLAIM_T")
    assert claim["content"]["attributes"]["risk_tier"] == "HIGH" and claim["revision"] == 2
    assert eca.resolve_statement(store, "CLAIM_T").risk_tier == "HIGH"
    assert [i["state"] for i in eca.queue(store)] == ["NOT_ADJUDICATED"]
    with pytest.raises(KnowledgeError, match="RATIONALE_REQUIRED"):
        loop.confirm_claim_change(store, token, "CHG_T", rationale="ok")
    done = loop.confirm_claim_change(store, token, "CHG_T", rationale="The claim holds only for the period examined.")
    assert done["assessment"] == "VERIFIED_WITHIN_SCOPE" and store.get_object("CHG_T")["content"]["generated_by"] == "researcher"


def test_not_supported_is_never_produced_from_non_significance(ran):
    store, token = ran["store"], ran["token"]
    ns = {"nonsignificance": {"p_value_key": "p", "ci_low_key": "ci_low", "ci_high_key": "ci_high", "mde_key": "mde"}}
    make_plan(ran, std_steps(main=ns), declared={"sesoi": 0.1, "alpha": 0.05, "basis": ["declared"]}, plan_id="PLAN_NS")
    run(ran, "PLAN_NS")
    res = store.get_object("RES_PLAN_NS_MAIN")["content"]
    assert {r["reading"] for r in res["nonsignificance"]["readings"]} == {"UNDERPOWERED", "IMPRECISE"}   # a non-significant result, with readings
    dag.interpret_results(store, token, "PLAN_NS", [effect("CLAIM_T", "CHANGES", ("main",), evidence_kind="judgement",
                                                           contradiction={"note": "p > 0.05"})], interpretation_id="INTERP_NS")
    out = loop.assess_claim_change(store, token, "CLAIM_T", change_id="CHG_NS", interpretation_ids=["INTERP_NS"])
    assert out["change"] == "WEAKENED"                       # not NOT_SUPPORTED, and not REVERSED
    assert loop.derive_change([{"effect": "LIMITS"}], [])[0] == "QUALIFIED"


def test_effects_resting_on_failed_steps_give_no_change(ran):
    store, token = ran["store"], ran["token"]
    make_plan(ran, std_steps(prep={"command": ["python", "bad.py"]}), plan_id="PLAN_B")
    run(ran, "PLAN_B")
    dag.interpret_results(store, token, "PLAN_B", [effect("CLAIM_T", "STRENGTHENS", ("main",))], interpretation_id="INTERP_B")
    out = loop.assess_claim_change(store, token, "CLAIM_T", change_id="CHG_B", interpretation_ids=["INTERP_B"])
    assert out["change"] is None and out["status"] == "INSUFFICIENT_EVIDENCE" and "did not succeed" in out["because"][0]
    assert store.get_object("CHG_B")["content"]["assessment"] == "NOT_ASSESSED"
    with pytest.raises(KnowledgeError, match="NOTHING_TO_CONFIRM"):
        loop.confirm_claim_change(store, token, "CHG_B", rationale="Confirming a change that was not derived.")
    with pytest.raises(KnowledgeError, match="CLAIM_NOT_FOUND"):
        loop.assess_claim_change(store, token, "NOPE", change_id="CHG_X")


def test_promise_check_proposes_revisions_and_applies_none(ran):
    store, token = ran["store"], ran["token"]
    fx.import_corpus(store, token, sources=(fx.FOCUS,))
    arg = argument.analyze_introduction(store, token, fx.FOCUS, fx.extractor())["argument_id"]
    source_before = store.get_object(fx.FOCUS)["revision"]
    add_claim(store, token, "CLAIM_OK", "The accrual indicators predict restatements in the sample.")
    interpret(ran, [effect("CLAIM_T", "LIMITS"), effect("CLAIM_OK", "STRENGTHENS", ("main",))])
    # nothing is linked yet, so nothing is tested
    assert loop.promise_check(store, token, arg, check_id="PROMISE_0")["promises"][0]["status"] == "NOT_TESTED"
    loop.link_promises(store, token, arg, {"proposed_contribution.1": ["CLAIM_T"]})
    assert loop.promise_check(store, token, arg, check_id="PROMISE_1")["promises"][0]["because"] == "none of its claims has a derived change yet"
    loop.assess_claim_change(store, token, "CLAIM_T", change_id="CHG_T")
    loop.assess_claim_change(store, token, "CLAIM_OK", change_id="CHG_OK")
    res = loop.promise_check(store, token, arg, check_id="PROMISE_2")
    (p,) = res["promises"]
    assert p["status"] == "AT_RISK" and p["changes"][0]["change"] == "NARROWED" and p["changes"][0]["assessment"] == "PROVISIONAL"
    (s,) = res["manuscript_revision_proposal"]["suggestions"]
    assert s["applied"] is False and s["needs_researcher_decision"] and s["basis"] == [{"kind": "project_run", "ref": "CHG_T"}]
    assert res["manuscript_revision_proposal"]["status"] == "PROPOSED"
    assert store.get_object(fx.FOCUS)["revision"] == source_before                       # the manuscript is untouched
    assert store.get_object(arg)["content"]["elements"][3]["text"] == "Evaluate out-of-industry transfer with out-of-time folds."
    # a promise carried only by a strengthened claim is kept, with no suggestion
    loop.link_promises(store, token, arg, {"proposed_contribution.1": ["CLAIM_OK"]})
    kept = loop.promise_check(store, token, arg, check_id="PROMISE_3")
    assert kept["promises"][0]["status"] == "KEPT" and kept["manuscript_revision_proposal"]["suggestions"] == []
    # reversed or unsupported claims mean the promise is not kept
    assert loop.derive_change([{"effect": "CHANGES", "evidence_kind": "structured", "reverses": True}], [])[0] == "REVERSED"
    with pytest.raises(KnowledgeError, match="ELEMENT_UNKNOWN"):
        loop.link_promises(store, token, arg, {"phenomenon.1": ["CLAIM_T"]})
