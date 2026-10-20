"""Phase 3B (plan v3): Introduction argument analysis, and its comparison with what the literature engine extracted."""
from __future__ import annotations

import re

import pytest

from research_harness.knowledge import argument, corpus, literature
from research_harness.knowledge.common import KnowledgeError
from research_harness.state.store import StateStore
from research_harness.workflows import eca
from research_harness.workflows.adapters import ScriptedModelAdapter
from tests import m3_fixture as fx

EXTERNAL_QUOTE = "Prior work has shown that accrual-based indicators predict restatements in samples drawn from a single industry"


@pytest.fixture
def proj(tmp_path):
    store, token = StateStore.create(tmp_path / "home", "PRJ_3B")
    fx.import_corpus(store, token)
    return store, token


def eca_adapter(verdict="SUPPORTED_AS_STATED", equivalence="EQUIVALENT"):
    return ScriptedModelAdapter(model_id="scripted-adjudicator", default_response={
        "passage_proposition": "Accrual indicators predict restatements within a single industry.", "equivalence": equivalence,
        "dropped_conditions": [], "cue_handling": [], "verdict": verdict, "required_qualification": []})


def test_intro_components_quote_verified(proj):
    store, token = proj
    res = argument.analyze_introduction(store, token, fx.FOCUS, fx.extractor())
    assert res["created"] and res["status"] == "REQUIRES_VERIFICATION" and res["missing_components"] == []
    arg = store.get_object(res["argument_id"])
    assert arg["object_type"] == "ContributionArgument" and arg["content"]["paper"] == {"source_id": fx.FOCUS, "revision": 1}
    _, text = corpus.source_text(store, fx.FOCUS)
    els = arg["content"]["elements"]
    assert [e["id"] for e in els] == ["phenomenon.1", "established_knowledge.1", "unresolved_tension.1", "proposed_contribution.1", "evidence_needed.1"]
    for e in els:
        (b,) = e["evidence_basis"]
        assert b["kind"] == "source_passage" and b["quote_verified"] is True
        assert text[b["char_start"]:b["char_end"]] == store.get_object(b["ref"])["content"]["quote"]
        assert e["status"] == "AUTHOR_ASSERTED" and e["generated_by"] == "model:scripted-extractor"      # producer and evidence are separate fields
        assert e["risk_tier"] == ("HIGH" if e["component"] in argument.HIGH_COMPONENTS else "STANDARD")
    assert els[3]["contribution_type"] == "extends"
    # the same analysis again is not a new revision
    again = argument.analyze_introduction(store, token, fx.FOCUS, fx.extractor())
    assert again["created"] is False and again["revision"] == res["revision"]


def test_published_intro_never_corroborated_by_itself(proj):
    store, token = proj
    a = argument.analyze_introduction(store, token, fx.PAPER_A, fx.extractor())["argument_id"]
    f = argument.analyze_introduction(store, token, fx.FOCUS, fx.extractor())["argument_id"]
    assert argument.component_status(store, a, "established_knowledge.1")["status"] == "AUTHOR_ASSERTED"

    # the paper's own passage, adjudicated and confirmed as supporting, is still the authors' word
    own = corpus.add_passage(store, token, source_id=fx.PAPER_A, quote=EXTERNAL_QUOTE, passage_id="EP_OWN")
    res = eca.adjudicate(store, token, eca.resolve_statement(store, a, element="established_knowledge.1"), own["object_id"], eca_adapter())
    eca.confirm(store, token, res["adjudication_id"], verdict="SUPPORTED_AS_STATED", rationale="The passage says exactly this.")
    assert eca.statement_assessment(store, eca.resolve_statement(store, a, element="established_knowledge.1"))["assessment"] == "VERIFIED_WITHIN_SCOPE"
    assert argument.component_status(store, a, "established_knowledge.1")["status"] == "AUTHOR_ASSERTED"   # not CORROBORATED

    # a passage from another source, confirmed, corroborates the focus paper's statement
    assert argument.component_status(store, f, "established_knowledge.1")["status"] == "AUTHOR_ASSERTED"
    ext = eca.adjudicate(store, token, eca.resolve_statement(store, f, element="established_knowledge.1"), own["object_id"], eca_adapter())
    assert argument.component_status(store, f, "established_knowledge.1")["status"] == "AUTHOR_ASSERTED"   # a proposal is not enough
    eca.confirm(store, token, ext["adjudication_id"], verdict="SUPPORTED_AS_STATED", rationale="Another paper states the same finding.")
    assert argument.component_status(store, f, "established_knowledge.1")["status"] == "CORROBORATED"

    # not supported, or challenged, is reported as such
    bad = eca.adjudicate(store, token, eca.resolve_statement(store, f, element="unresolved_tension.1"), own["object_id"],
                         eca_adapter(verdict="NOT_SUPPORTED", equivalence="DIFFERENT"))
    assert bad["eca_verdict"] == "NOT_SUPPORTED" and argument.component_status(store, f, "unresolved_tension.1")["status"] == "UNSUPPORTED"
    with store.write_tx(token=token) as tx:
        tx.add_revision(object_id="EP_CHALLENGE", object_type="EvidencePassage", origin={}, content={"locator": "x", "quote": "q"},
                        edges=[("challenges", f, 1, {})])
    assert argument.component_status(store, f, "established_knowledge.1")["status"] == "CONTESTED"


def test_new_setting_only_flagged(proj):
    store, token = proj
    res = argument.analyze_introduction(store, token, fx.PAPER_A, fx.extractor())
    flags = {(f["flag"], f.get("element")) for f in res["review_flags"]}
    assert ("NEW_SETTING_ONLY", "proposed_contribution.1") in flags
    note = next(f["note"] for f in res["review_flags"] if f["flag"] == "NEW_SETTING_ONLY")
    assert "not a theoretical contribution" in note
    # the other flags say what is only asserted
    assert ("ESTABLISHED_KNOWLEDGE_WITHOUT_EXTERNAL_SOURCE", "established_knowledge.1") in flags and ("TENSION_IS_THE_AUTHORS_ASSERTION_ONLY", "unresolved_tension.1") in flags
    focus = argument.analyze_introduction(store, token, fx.FOCUS, fx.extractor())
    assert "NEW_SETTING_ONLY" not in {f["flag"] for f in focus["review_flags"]}          # typed `extends`


def test_missing_component_insufficient(proj):
    store, token = proj
    only = ScriptedModelAdapter(default_response={"records": [fx.SCRIPT[("RP_INTRO_ARGUMENT", fx.FOCUS)][0]], "gaps": []})
    res = argument.analyze_introduction(store, token, fx.FOCUS, only)
    assert res["status"] == "INSUFFICIENT_EVIDENCE"
    assert res["missing_components"] == ["established_knowledge", "unresolved_tension", "proposed_contribution"]
    assert [f["component"] for f in res["review_flags"] if f["flag"] == "MISSING_COMPONENT"] == res["missing_components"]
    # a contribution type outside the vocabulary is rejected with the record, not stored
    odd = ScriptedModelAdapter(default_response={"records": [{**fx.SCRIPT[("RP_INTRO_ARGUMENT", fx.FOCUS)][3], "type": "game_changer"}], "gaps": []})
    ext = argument.analyze_introduction(store, token, fx.PAPER_B, odd)
    assert ext["status"] == "INSUFFICIENT_EVIDENCE"


def _contribution_handler(cite_fake: bool = False):
    def handler(system: str, user: str):
        ours = re.search(r"\[(proposed_contribution\.1)\]", user).group(1)
        theirs = re.findall(r"\[(EP_[A-Z0-9_]+)\] \(claimed_contribution\)", user)
        if not theirs:
            return {"differentiation": [], "gaps": ["no contribution statement"]}
        out = [{"dimension": "mechanism", "ours": ours, "theirs": theirs[0], "relation": "overlapping", "substantive_difference": True,
                "reason": "Both test transfer; ours adds out-of-time folds."}]
        if cite_fake:
            out.append({"dimension": "design", "ours": ours, "theirs": "EP_DOES_NOT_EXIST", "relation": "different", "reason": "invented"})
            out.append({"dimension": "design", "ours": "proposed_contribution.9", "theirs": theirs[0], "relation": "different", "reason": "invented"})
        return {"differentiation": out}
    return handler


def test_3b_contribution_comparison_integrates_3c_output(proj):
    store, token = proj
    focus = argument.analyze_introduction(store, token, fx.FOCUS, fx.extractor())["argument_id"]
    # real 3C output: the contribution review of two stored papers, with verified passages
    review = literature.run_literature(store, token, "contribution", [fx.PAPER_A, fx.PAPER_B], fx.extractor())
    assert review["purpose"] == "contribution" and review["run_id"]
    their_quotes = {r["passage_id"]: r["quote"] for p in review["contribution_records"] for r in p["records"] if r["field"] == "claimed_contribution"}
    assert len(their_quotes) == 2

    comparer = ScriptedModelAdapter(model_id="scripted-comparer", custom_handler=_contribution_handler(cite_fake=True))
    out = argument.compare_contribution(store, token, focus, review, comparer)
    assert out["llm_calls"] == 2 and len(comparer.recorded_requests) == 2
    # the model was shown the very quotes 3C extracted, and nothing else about the other papers
    shown = "\n".join(r.user_prompt for r in comparer.recorded_requests)
    assert all(q in shown for q in their_quotes.values()) and "Agency theory" not in shown
    # the differentiation rests on 3C's passages: same ids, same verified spans
    assert len(out["differentiation"]) == 2 and len(out["rejected"]) == 4
    assert {r["reason_rejected"] for r in out["rejected"]} == {"cites a passage that is not in the contribution review",
                                                               "cites an element that is not in the focus argument"}
    for d in out["differentiation"]:
        pid = d["theirs"]["passage_id"]
        assert pid in their_quotes and d["theirs"]["quote"] == their_quotes[pid]
        _, text = corpus.source_text(store, d["theirs"]["source_id"])
        assert text[d["theirs"]["char_start"]:d["theirs"]["char_end"]] == d["theirs"]["quote"]
        assert d["basis"] == [{"kind": "source_passage", "ref": pid}] and corpus.verify_passage(store, pid)["source_match"] == "VERIFIED"
        assert d["substantive"] == "model_assessed" and d["assessment"] == "PROVISIONAL" and d["generated_by"] == "model:scripted-comparer"
    # it is stored on the focus argument, linked to the review run and to the passages it cites
    stored = store.get_object(focus)
    assert stored["revision"] == 2 and len(stored["content"]["differentiation"]) == 2
    with store.read() as conn:
        assert {e["target_id"] for e in store.edges_from(conn, focus, 2) if e["relation"] == "depends_on"} >= set(their_quotes)
        row = conn.execute("SELECT upstream_run_ids FROM workflow_runs WHERE run_id=?", (out["run_id"],)).fetchone()
    assert review["run_id"] in row[0]
    # the elements' text did not change, so their adjudications stay valid
    assert argument.component_status(store, focus, "proposed_contribution.1")["status"] == "AUTHOR_ASSERTED"

    # the researcher's judgement replaces the model's, and only then is the entry verified
    with pytest.raises(KnowledgeError, match="RATIONALE_REQUIRED"):
        argument.confirm_difference(store, token, focus, 0, substantive=True, rationale="yes")
    done = argument.confirm_difference(store, token, focus, 0, substantive=True, rationale="Out-of-time folds change what is learned.")
    assert done["entry"]["substantive"] == "researcher_confirmed" and done["entry"]["assessment"] == "VERIFIED_WITHIN_SCOPE"
    assert done["entry"]["generated_by"] == "researcher" and store.get_object(focus)["content"]["differentiation"][1]["assessment"] == "PROVISIONAL"


def test_comparison_refuses_what_is_not_a_real_3c_review(proj):
    store, token = proj
    focus = argument.analyze_introduction(store, token, fx.FOCUS, fx.extractor())["argument_id"]
    with pytest.raises(KnowledgeError, match="NOT_A_CONTRIBUTION_REVIEW"):
        argument.compare_contribution(store, token, focus, {"purpose": "method", "method_records": []}, fx.extractor())
    # a hand-made review whose passages are not in the store cannot be cited
    stub = {"purpose": "contribution", "run_id": None, "contribution_records": [{"source_id": fx.PAPER_A, "records": [
        {"passage_id": "EP_MADE_UP", "field": "claimed_contribution", "quote": "We invented this.", "char_start": 0, "char_end": 5}]}]}
    comparer = ScriptedModelAdapter(custom_handler=_contribution_handler())
    out = argument.compare_contribution(store, token, focus, stub, comparer)
    assert out["differentiation"] == [] and comparer.recorded_requests == []
    with pytest.raises(KnowledgeError, match="COMPARISON_INVALID_OUTPUT"):
        review = literature.run_literature(store, token, "contribution", [fx.PAPER_B], fx.extractor())
        argument.compare_contribution(store, token, focus, review, ScriptedModelAdapter(default_response={"differentiation": [{"dimension": "colour"}]}))
