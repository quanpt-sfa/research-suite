"""Phase 3A.2 (plan v3): Evidence-Claim Adjudication. A correct quote is not a correct reading."""
from __future__ import annotations

from pathlib import Path

import pytest

from research_harness.core.errors import AuthorizationError
from research_harness.knowledge import corpus
from research_harness.knowledge.common import KnowledgeError
from research_harness.state.actors import register_actor
from research_harness.state.store import StateStore
from research_harness.workflows import eca
from research_harness.workflows.adapters import ModelRefusal, ScriptedModelAdapter

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_paper_misstatement.txt"
QUOTE = "the estimator performs well only when every firm is observed in every year of the estimation window"
WIDE = "The estimator is suitable for panel data."
CONDITION = "every firm is observed in every year of the estimation window"


@pytest.fixture
def proj(tmp_path):
    store, token = StateStore.create(tmp_path / "home", "PRJ_ECA")
    corpus.import_source(store, token, citation_key="synth2025", text=FIXTURE.read_text(encoding="utf-8"))
    passage = corpus.add_passage(store, token, source_id="SRC_SYNTH2025", quote=QUOTE, passage_id="EP_BALANCE")
    return store, token, passage


def add_claim(store, token, oid="CLAIM_PANEL", text=WIDE, **attributes):
    with store.write_tx(token=token) as tx:
        tx.add_revision(object_id=oid, object_type="Claim", origin={}, edges=[],
                        content={"text": text, "claim_tier": "predictive", "attributes": attributes})


def revise_claim(store, token, oid, text, **attributes):
    with store.write_tx(token=token) as tx:
        tx.add_revision(object_id=oid, object_type="Claim", origin={}, edges=[],
                        content={"text": text, "claim_tier": "predictive", "attributes": attributes})


def answer(**kw):
    base = {"passage_proposition": "The estimator performs well only for panels in which every firm is observed in every year.",
            "equivalence": "BROADER", "dropped_conditions": ["every firm observed in every year (balanced panel)"],
            "cue_handling": [{"cue": "only when", "handling": "dropped", "note": "the statement drops the condition"}],
            "verdict": "SUPPORTED_IF_QUALIFIED", "required_qualification": ["for balanced panels only"], "rationale": "r"}
    base.update(kw)
    return ScriptedModelAdapter(model_id="scripted-adjudicator", default_response=base)


def stmt(store, oid="CLAIM_PANEL", **kw):
    return eca.resolve_statement(store, oid, **kw)


def test_cue_detector_flags_conditional_wording_but_decides_nothing():
    cues = eca.qualifier_cues(QUOTE)
    assert [c["cue"] for c in cues] == ["only when"] and QUOTE[cues[0]["char_start"]:cues[0]["char_end"]] == "only when"
    assert eca.qualifier_cues("The estimator is consistent for all panels.") == []
    assert {c["pattern"] for c in eca.qualifier_cues("It holds unless firms exit; we caution that results depend on the sample.")} == {
        "unless", "caution", "depends_on"}


def test_correct_quote_wrong_interpretation_detected_or_unverified(proj):
    store, token, passage = proj
    add_claim(store, token)
    # the quote is verbatim, so the source match passes: it is the reading that is wrong
    assert corpus.verify_passage(store, "EP_BALANCE")["source_match"] == "VERIFIED"

    # (a) the adjudicator sees that the statement is broader than the passage
    refuse = eca.adjudicate(store, token, stmt(store), "EP_BALANCE", answer(equivalence="BROADER", verdict="NOT_SUPPORTED"))
    assert refuse["eca_verdict"] == "NOT_SUPPORTED" and refuse["guard"] is None
    out = eca.statement_assessment(store, stmt(store))
    assert out["assessment"] == "CONTESTED" and out["researcher_confirmed"] is False

    # ... or says it would stand with the condition written in: still not verified as written
    add_claim(store, token, "CLAIM_PANEL2")
    qual = eca.adjudicate(store, token, stmt(store, "CLAIM_PANEL2"), "EP_BALANCE", answer())
    assert qual["eca_verdict"] == "SUPPORTED_IF_QUALIFIED"
    assert eca.statement_assessment(store, stmt(store, "CLAIM_PANEL2"))["assessment"] == "PROVISIONAL"

    # (b) a wrong adjudicator says EQUIVALENT and ignores the flagged cue: the guard turns that into UNRESOLVED
    add_claim(store, token, "CLAIM_PANEL3")
    wrong = answer(equivalence="EQUIVALENT", dropped_conditions=[], cue_handling=[], verdict="SUPPORTED_AS_STATED", required_qualification=[])
    res = eca.adjudicate(store, token, stmt(store, "CLAIM_PANEL3"), "EP_BALANCE", wrong)
    assert res["model_verdict"] == "SUPPORTED_AS_STATED" and res["eca_verdict"] == "UNRESOLVED"
    assert res["guard"] == "qualifier_cues_not_accounted_for"
    record = store.get_object(res["adjudication_id"])["content"]
    assert record["eca_verdict"] == "UNRESOLVED" and record["verdict"] == "UNCLEAR" and record["model"]["proposed_verdict"] == "SUPPORTED_AS_STATED"
    out = eca.statement_assessment(store, stmt(store, "CLAIM_PANEL3"))
    assert out["assessment"] != "VERIFIED_WITHIN_SCOPE" and out["assessment"] == "PROVISIONAL"


def test_guard_rules_only_ever_move_a_verdict_toward_unresolved():
    cue = [{"cue": "only when"}]
    ok = {"verdict": "SUPPORTED_AS_STATED", "equivalence": "EQUIVALENT", "dropped_conditions": [],
          "cue_handling": [{"cue": "only when", "handling": "carried"}]}
    assert eca.consistency_guard(ok, cue) == ("SUPPORTED_AS_STATED", None)
    assert eca.consistency_guard({**ok, "cue_handling": [{"cue": "only when", "handling": "dropped"}]}, cue)[0] == "UNRESOLVED"
    assert eca.consistency_guard({**ok, "equivalence": "BROADER"}, cue)[0] == "UNRESOLVED"
    assert eca.consistency_guard({**ok, "dropped_conditions": ["x"]}, cue)[0] == "UNRESOLVED"
    assert eca.consistency_guard({"verdict": "SUPPORTED_IF_QUALIFIED", "equivalence": "BROADER", "dropped_conditions": ["x"],
                                  "required_qualification": []}, [])[1] == "qualified_verdict_without_a_qualification"
    assert eca.consistency_guard({"verdict": "NOT_SUPPORTED", "equivalence": "EQUIVALENT", "dropped_conditions": []}, cue) == ("NOT_SUPPORTED", None)
    assert eca.consistency_guard({"verdict": "SUPPORTED_AS_STATED", "equivalence": "UNRESOLVED", "dropped_conditions": []}, [])[0] == "UNRESOLVED"


def test_generator_and_basis_are_separate_fields(proj):
    store, token, passage = proj
    add_claim(store, token, generated_by="model:drafting-assistant")
    s = stmt(store)
    assert s.generated_by == "model:drafting-assistant"
    res = eca.adjudicate(store, token, s, "EP_BALANCE", answer(equivalence="NARROWER", dropped_conditions=[], verdict="SUPPORTED_AS_STATED",
                         cue_handling=[{"cue": "only when", "handling": "carried"}], required_qualification=[]))
    rec = store.get_object(res["adjudication_id"])["content"]
    assert rec["generated_by"] == "model:scripted-adjudicator"                       # who produced the adjudication
    assert rec["statement"]["generated_by"] == "model:drafting-assistant"            # who produced the statement
    assert rec["cited_evidence"] == [] and rec["passage"]["object_id"] == "EP_BALANCE"  # what the reading rests on
    assert rec["source_match"]["status"] == "VERIFIED" and rec["confirmed_by_researcher"] is False
    # a model-generated statement with a supporting but unconfirmed adjudication is only provisional
    assert eca.statement_assessment(store, s)["assessment"] == "PROVISIONAL"


def test_high_impact_requires_confirmation(proj, tmp_path):
    store, token, passage = proj
    add_claim(store, token, "CLAIM_HIGH", text="The estimator needs every firm observed in every year.", risk_tier="HIGH", generated_by="researcher")
    s = stmt(store, "CLAIM_HIGH")
    assert s.risk_tier == "HIGH" and eca.risk_tier("contribution") == "HIGH" and eca.risk_tier("literature_summary") == "STANDARD"
    assert [(i["statement"]["object_id"], i["state"]) for i in eca.queue(store)] == [("CLAIM_HIGH", "NOT_ADJUDICATED")]
    # even a researcher-written statement with a supporting model adjudication cannot be verified unconfirmed
    res = eca.adjudicate(store, token, s, "EP_BALANCE", answer(equivalence="EQUIVALENT", dropped_conditions=[], verdict="SUPPORTED_AS_STATED",
                         cue_handling=[{"cue": "only when", "handling": "carried"}], required_qualification=[]))
    assert res["eca_verdict"] == "SUPPORTED_AS_STATED" and res["guard"] is None
    assert eca.statement_assessment(store, s)["assessment"] == "PROVISIONAL"
    assert [(i["adjudication_id"], i["state"]) for i in eca.queue(store)] == [(res["adjudication_id"], "AWAITING_CONFIRMATION")]

    # confirming needs a human author or owner, and a reason
    store_, _ = store, None
    imp = register_actor(store, token, actor_id="importer_only", display_name="imp", roles=["importer"])
    with pytest.raises(AuthorizationError, match="ROLE_REQUIRED"):
        eca.confirm(store, imp["token"], res["adjudication_id"], verdict="SUPPORTED_AS_STATED", rationale="looks right to me")
    with pytest.raises(KnowledgeError, match="RATIONALE_REQUIRED"):
        eca.confirm(store, token, res["adjudication_id"], verdict="SUPPORTED_AS_STATED", rationale="ok")
    conf = eca.confirm(store, token, res["adjudication_id"], verdict="SUPPORTED_AS_STATED", rationale="The statement carries the condition.")
    out = eca.statement_assessment(store, s)
    assert out["assessment"] == "VERIFIED_WITHIN_SCOPE" and out["researcher_confirmed"] is True
    assert eca.queue(store) == []
    assert store.get_object(conf["adjudication_id"])["content"]["confirms"] == res["adjudication_id"]  # the proposal is kept, append-only


def test_a_researcher_can_override_a_guard_but_it_is_recorded(proj):
    store, token, passage = proj
    add_claim(store, token, "CLAIM_OVR", text="The estimator needs a balanced panel.", risk_tier="HIGH")
    s = stmt(store, "CLAIM_OVR")
    wrong = answer(equivalence="EQUIVALENT", dropped_conditions=[], cue_handling=[], verdict="SUPPORTED_AS_STATED", required_qualification=[])
    res = eca.adjudicate(store, token, s, "EP_BALANCE", wrong)
    assert res["eca_verdict"] == "UNRESOLVED"
    conf = eca.confirm(store, token, res["adjudication_id"], verdict="SUPPORTED_AS_STATED",
                       rationale="Balanced panel means every firm in every year; the condition is carried.")
    rec = store.get_object(conf["adjudication_id"])["content"]
    assert rec["overrides"] == {"proposed": "UNRESOLVED", "guard": "qualifier_cues_not_accounted_for"} and rec["generated_by"] == "researcher"
    assert eca.statement_assessment(store, s)["assessment"] == "VERIFIED_WITHIN_SCOPE"


def test_counter_evidence_marks_contested(proj):
    store, token, passage = proj
    add_claim(store, token, "CLAIM_C", text="The estimator needs a balanced panel.", generated_by="researcher")
    s = stmt(store, "CLAIM_C")
    res = eca.adjudicate(store, token, s, "EP_BALANCE", answer(equivalence="EQUIVALENT", dropped_conditions=[], verdict="SUPPORTED_AS_STATED",
                         cue_handling=[{"cue": "only when", "handling": "carried"}], required_qualification=[]))
    eca.confirm(store, token, res["adjudication_id"], verdict="SUPPORTED_AS_STATED", rationale="Carries the condition exactly.")
    assert eca.statement_assessment(store, s)["assessment"] == "VERIFIED_WITHIN_SCOPE"
    # a passage that challenges the statement is a structural fact: the statement is contested from then on
    corpus.add_passage(store, token, source_id="SRC_SYNTH2025", quote="Accuracy differs across industries", passage_id="EP_COUNTER")
    with store.write_tx(token=token) as tx:
        tx.add_revision(object_id="EP_COUNTER", object_type="EvidencePassage", origin={}, edges=[("challenges", "CLAIM_C", 1, {})],
                        content={**tx.get("EP_COUNTER")["content"]})
    out = eca.statement_assessment(store, s)
    assert out["assessment"] == "CONTESTED" and out["counter_evidence"][0]["object_id"] == "EP_COUNTER"
    assert any("challenged by EP_COUNTER" in r for r in out["reasons"])


def test_qualified_verdict_requires_revised_statement(proj):
    store, token, passage = proj
    add_claim(store, token, "CLAIM_Q", generated_by="researcher")
    s1 = stmt(store, "CLAIM_Q")
    res = eca.adjudicate(store, token, s1, "EP_BALANCE", answer())
    assert res["eca_verdict"] == "SUPPORTED_IF_QUALIFIED"
    # the verdict cannot be accepted for the statement as written
    with pytest.raises(KnowledgeError, match="QUALIFICATION_NOT_APPLIED"):
        eca.confirm(store, token, res["adjudication_id"], verdict="SUPPORTED_IF_QUALIFIED", rationale="Fine with the qualification.")
    with pytest.raises(KnowledgeError, match="QUALIFICATION_NOT_APPLIED"):
        eca.confirm(store, token, res["adjudication_id"], verdict="SUPPORTED_IF_QUALIFIED", rationale="Fine with the qualification.",
                    qualification_applied_in={"object_id": "CLAIM_Q", "revision": 1})
    revise_claim(store, token, "CLAIM_Q", "The estimator is suitable for balanced panels only.", generated_by="researcher")
    s2 = stmt(store, "CLAIM_Q")
    assert s2.revision == 2 and eca.statement_assessment(store, s2)["assessment"] == "PROVISIONAL"  # the old adjudication is not this text's
    with pytest.raises(KnowledgeError, match="QUALIFICATION_REVISION_NOT_HEAD"):
        eca.confirm(store, token, res["adjudication_id"], verdict="SUPPORTED_IF_QUALIFIED", rationale="Fine with the qualification.",
                    qualification_applied_in={"object_id": "CLAIM_Q", "revision": 3})
    eca.confirm(store, token, res["adjudication_id"], verdict="SUPPORTED_IF_QUALIFIED", rationale="Revision 2 states the balanced-panel condition.",
                qualification_applied_in={"object_id": "CLAIM_Q", "revision": 2})
    assert eca.statement_assessment(store, s2)["assessment"] == "VERIFIED_WITHIN_SCOPE"
    assert eca.statement_assessment(store, s1)["assessment"] != "VERIFIED_WITHIN_SCOPE"


def test_an_adjudication_goes_stale_when_the_statement_is_revised(proj):
    store, token, passage = proj
    add_claim(store, token, "CLAIM_S", generated_by="researcher")
    res = eca.adjudicate(store, token, stmt(store, "CLAIM_S"), "EP_BALANCE", answer(equivalence="NARROWER", dropped_conditions=[], verdict="SUPPORTED_AS_STATED",
                         cue_handling=[{"cue": "only when", "handling": "carried"}], required_qualification=[]))
    revise_claim(store, token, "CLAIM_S", "A different statement.", generated_by="researcher")
    with pytest.raises(KnowledgeError, match="STATEMENT_REVISED_SINCE"):
        eca.confirm(store, token, res["adjudication_id"], verdict="SUPPORTED_AS_STATED", rationale="Confirming an old text.")
    assert eca.statement_assessment(store, stmt(store, "CLAIM_S"))["adjudications"] == []
    with store.read() as conn:  # the dependency graph already knows the record depends on the old revision
        assert {(e["relation"], e["target_id"], e["target_revision"]) for e in store.edges_from(conn, res["adjudication_id"], 1)} >= {
            ("depends_on", "CLAIM_S", 1), ("depends_on", "EP_BALANCE", 1)}


def test_a_passage_that_does_not_verify_gets_no_model_reading(proj):
    store, token, passage = proj
    add_claim(store, token)
    with store.write_tx(token=token) as tx:
        tx.add_revision(object_id="EP_FAKE", object_type="EvidencePassage", origin={}, edges=[],
                        content={"locator": "x", "quote": "A sentence the paper never contains.", "source_id": "SRC_SYNTH2025",
                                 "source_revision": 1, "char_start": 10, "char_end": 40})
    adapter = answer()
    res = eca.adjudicate(store, token, stmt(store), "EP_FAKE", adapter)
    assert adapter.recorded_requests == [] and res["llm_calls"] == 0
    assert res["eca_verdict"] == "UNRESOLVED" and res["guard"] == "source_match_not_verified"


def test_repeat_adjudication_is_reused_and_unusable_answers_write_nothing(proj):
    store, token, passage = proj
    add_claim(store, token)
    adapter = answer()
    first = eca.adjudicate(store, token, stmt(store), "EP_BALANCE", adapter)
    again = eca.adjudicate(store, token, stmt(store), "EP_BALANCE", adapter)
    assert again["reused"] is True and again["adjudication_id"] == first["adjudication_id"] and len(adapter.recorded_requests) == 1
    add_claim(store, token, "CLAIM_BAD")
    for bad in ("not json", {"equivalence": "SIMILAR", "passage_proposition": "x", "dropped_conditions": [], "verdict": "SUPPORTED_AS_STATED"}):
        out = eca.adjudicate(store, token, stmt(store, "CLAIM_BAD"), "EP_BALANCE", ScriptedModelAdapter(default_response=bad))
        assert out["status"] == "FAILED" and out["reason"] == "invalid_output"

    def refuse(system, user):
        raise ModelRefusal("declined")
    assert eca.adjudicate(store, token, stmt(store, "CLAIM_BAD"), "EP_BALANCE", ScriptedModelAdapter(custom_handler=refuse))["reason"] == "ModelRefusal"
    assert eca.statement_assessment(store, stmt(store, "CLAIM_BAD"))["adjudications"] == []
    with store.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM workflow_runs WHERE workflow='eca_adjudication'").fetchone()[0] == 5


def test_the_adjudication_request_is_one_call_with_the_flagged_cues_shown(proj):
    store, token, passage = proj
    add_claim(store, token)
    adapter = answer()
    eca.adjudicate(store, token, stmt(store), "EP_BALANCE", adapter)
    (req,) = adapter.recorded_requests
    assert "adversarial" in req.system_prompt and "AS WRITTEN" in req.system_prompt
    assert WIDE in req.user_prompt and QUOTE in req.user_prompt and '"only when"' in req.user_prompt
    # the ECA record is an Adjudication with the legacy fields set, so the existing review machinery still understands it
    adj = store.get_object("ADJ_000001")
    assert adj["object_type"] == "Adjudication" and adj["content"]["assessment_kind"] == "semantic_entailment"
    assert adj["content"]["verdict"] == "SUPPORTED" and adj["origin"]["operation"] == "adjudicate"
