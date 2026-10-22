"""WP6.1 remediation, P0: whether the literature already addresses a theoretical tension is stated only from an inspection record, never from a model's empty list.

Synthetic acceptance only (invented papers, scripted models). Nothing here says anything about a real literature.
"""
from __future__ import annotations

import json

import pytest

from research_harness.cli import main
from research_harness.core.errors import AuthorizationError
from research_harness.discovery import inspection as INS
from research_harness.discovery import landscape as L
from research_harness.export.landscape import build_landscape_export
from research_harness.knowledge import corpus
from research_harness.knowledge.common import KnowledgeError
from research_harness.state.actors import register_actor
from research_harness.workflows.adapters import ScriptedModelAdapter
from research_harness.workflows.domain_engine import evaluate, facts_from_store, load_builtin_spec
from tests import inquiry_fixtures as F
from tests import remediation_fixtures as RF


def synth(proposals):
    return ScriptedModelAdapter(model_id="scripted-synthesiser", default_response={"proposals": proposals})


@pytest.fixture
def w(tmp_path):
    w = F.build_world(tmp_path, name="PRJ_REC", papers=F.A_PAPERS, statement=F.A_STATEMENT, terms=F.A_TERMS, inquiry=F.A_INQUIRY)
    w["lid"] = RF.build_landscape(w)
    w["v"] = L.view(w["store"], w["lid"])
    RF.add_extra_papers(w)
    return w


def propose(w, **kw):
    out = L.propose_relations(w["store"], w["token"], w["lid"], synth([RF.div_proposal(w["v"], **kw)]))
    assert out["accepted"], out["rejected"]
    return out["accepted"][0]


def rec(w, iid):
    return next(i for i in L.view(w["store"], w["lid"])["inferences"] if i["id"] == iid)["recognition"]


def res(sid, result, **kw):
    return {"source_id": sid, "result": result, "inspected": "whole text", **kw}


# ------------------------------------------------------------------------------------------------ what a model's list does and does not mean

def test_a_model_that_names_no_source_has_assessed_nothing_and_one_that_names_a_source_has_only_hinted(w):
    none = propose(w)
    assert rec(w, none)["status"] == "RECOGNITION_NOT_ASSESSED" and rec(w, none)["addressed_by"] == [] and "not a finding that none exists" in rec(w, none)["note"]
    ent = lambda src, fld: next(e["id"] for e in w["v"]["entries"] if e["source_id"] == src and e["field"] == fld)   # noqa: E731
    hint = propose(w, topic="a hinted tension", entries=[ent("SRC_ATT", "assumed_mechanism"), ent("SRC_ENG1", "theoretical_claim")], already_addressed_by=[
        {"entry": next(e["id"] for e in w["v"]["entries"] if e["source_id"] == "SRC_PART" and e["field"] == "theoretical_claim"), "how": "it separates the two ideas"}])
    r = rec(w, hint)
    assert r["status"] == "POTENTIAL_ADDRESSING_SOURCES" and r["addressed_by"] == [] and r["model_hints"][0]["verified"] is False and "nobody has inspected" in r["note"]
    assert L.view(w["store"], w["lid"])["inspections"] == []


# ------------------------------------------------------------------------------------------------ a paper the model missed, found and inspected

def test_a_paper_that_addresses_the_tension_but_was_left_out_by_the_model_is_found_by_the_candidates_and_recorded_with_verbatim_evidence(w):
    iid = propose(w)
    cands = L.tension_candidates(w["store"], w["lid"], iid, limit=10)
    part = next(c for c in cands["candidates"] if c["source_id"] == "SRC_PART")
    assert part["model_hint"] is False and part["readable"] is True and "not evidence that it addresses the tension" in cands["note"]
    with pytest.raises(KnowledgeError, match="EVIDENCE_REQUIRED"):
        L.inspect_tension(w["store"], w["token"], w["lid"], iid, rationale="r", results=[res("SRC_PART", "ADDRESSES")])
    with pytest.raises(KnowledgeError, match="QUOTE_NOT_IN_SOURCE"):
        L.inspect_tension(w["store"], w["token"], w["lid"], iid, rationale="r", results=[res("SRC_PART", "ADDRESSES", quotes=["A sentence this paper never wrote about the matter."])])
    with pytest.raises(KnowledgeError, match="INSPECTED_SCOPE_REQUIRED"):
        L.inspect_tension(w["store"], w["token"], w["lid"], iid, rationale="r", results=[{"source_id": "SRC_PART", "result": "DOES_NOT_ADDRESS", "inspected": " "}])
    assert L.view(w["store"], w["lid"])["inspections"] == []                                                           # nothing was recorded by the refused attempts
    out = L.inspect_tension(w["store"], w["token"], w["lid"], iid, rationale="I read the paper's argument section.",
                            results=[res("SRC_PART", "ADDRESSES", quotes=[F.A["part_claim"]], note="it separates participation from engagement where messages are cheap")])
    assert out["status"] == "ADDRESSED_IN_INSPECTED_SET" and out["inspection"] == "N001"
    r = rec(w, iid)
    p = r["addressed_by"][0]
    assert r["status"] == "ADDRESSED_IN_INSPECTED_SET" and p["source_id"] == "SRC_PART" and p["passages"][0]["quote"] == F.A["part_claim"] and r["decision"]["by"] and r["decision"]["rationale"]
    ep = w["store"].get_object(p["passages"][0]["passage_id"], p["passages"][0]["passage_revision"])
    assert ep["object_type"] == "EvidencePassage" and ep["content"]["quote_verified"] is True and ep["content"]["source_id"] == "SRC_PART"
    assert corpus.source_text(w["store"], "SRC_PART")[1][ep["content"]["char_start"]:ep["content"]["char_end"]] == F.A["part_claim"]
    # the contract step that waits for an inspection is now done by a record the researcher wrote
    spec = load_builtin_spec("LIB_DW_CRITICAL_SYNTHESIS")
    ev = {e.step_id: e for e in evaluate(spec, facts_from_store(w["store"], problem_id=w["pid"]))}
    assert ev["inspect_recognition"].completion == "DECLARED_IN_RECORD"


# ------------------------------------------------------------------------------------------------ mention is not resolution

def test_a_paper_that_only_mentions_the_phenomenon_does_not_address_the_tension(w):
    iid = propose(w)
    out = L.inspect_tension(w["store"], w["token"], w["lid"], iid, rationale="I read it; it counts posts and takes no position.", candidate_limit=1, terms=["message volume team channels"],
                            results=[res("SRC_MENTION", "MENTIONS_PHENOMENON_ONLY", quotes=[RF.R["mention_a"]], note="describes volume, not what posting presupposes")])
    r = rec(w, iid)
    assert out["status"] == "NOT_FOUND_IN_INSPECTED_SET" and r["status"] == "NOT_FOUND_IN_INSPECTED_SET" and r["addressed_by"] == [] and r["mentions_only"] == ["SRC_MENTION"]
    assert "1 source(s) inspected of 1 candidate(s)" in r["scope"] and "not about the literature and not about novelty" in r["note"]
    assert "a statement about this inspected set in this corpus snapshot" in r["note"]


# ------------------------------------------------------------------------------------------------ a working paper's argument, withdrawn in the journal version

def test_an_argument_in_a_working_paper_that_the_journal_version_withdraws_is_evidence_at_the_working_paper_with_a_stated_conflict(w):
    iid = propose(w)
    L.inspect_tension(w["store"], w["token"], w["lid"], iid, rationale="Both versions read.", terms=["sending is not investing"], extra_candidates=["SRC_WPX", "SRC_JX"], candidate_limit=2, results=[
        res("SRC_WPX", "ADDRESSES", quotes=[RF.R["wp_arg"]], note="argues the separation"),
        res("SRC_JX", "DOES_NOT_ADDRESS", note="the journal version says it does not defend the separation")])
    r = rec(w, iid)
    assert r["status"] == "ADDRESSED_IN_INSPECTED_SET" and [a["source_id"] for a in r["addressed_by"]] == ["SRC_WPX"] and r["addressed_by"][0]["source_revision"] == 1
    assert any("work W_SEND" in q and "SRC_WPX addresses the tension but SRC_JX does not" in q and "withdrawn" in q for q in r["qualifications"])
    assert r["not_addressing"] == ["SRC_JX"]                                                                              # each version is judged on its own; neither stands for the work


# ------------------------------------------------------------------------------------------------ a source we hold only as an abstract

def test_an_important_source_held_only_as_an_abstract_makes_the_inspection_incomplete_never_negative(w):
    RF.add_abstract_only(w)
    iid = propose(w)
    cands = L.tension_candidates(w["store"], w["lid"], iid, limit=1, terms=["participation without engagement conceptual account"])
    unread = [c for c in cands["candidates"] if not c["readable"]]
    assert unread and unread[0]["source_id"] == "SRC_ABSONLY" and unread[0]["tier"] in ("ABSTRACT_ONLY", "METADATA_ONLY")
    readable = [c for c in cands["candidates"] if c["readable"]]
    with pytest.raises(KnowledgeError, match="SOURCE_NOT_READABLE"):
        L.inspect_tension(w["store"], w["token"], w["lid"], iid, rationale="r", candidate_limit=1, terms=["participation without engagement conceptual account"],
                          results=[res(c["source_id"], "DOES_NOT_ADDRESS") for c in readable] + [res("SRC_ABSONLY", "DOES_NOT_ADDRESS")])               # one cannot inspect what one does not hold
    out = L.inspect_tension(w["store"], w["token"], w["lid"], iid, rationale="I read the full texts; the abstract-only paper cannot be read.", candidate_limit=1,
                            terms=["participation without engagement conceptual account"],
                            results=[res(c["source_id"], "DOES_NOT_ADDRESS") for c in readable] + [{"source_id": "SRC_ABSONLY", "result": "NOT_READABLE", "note": "abstract only"}])
    assert out["status"] == "INSPECTION_INCOMPLETE" and "SRC_ABSONLY" in out["unreadable"]
    r = rec(w, iid)
    assert r["status"] == "INSPECTION_INCOMPLETE" and any("no full text" in q for q in r["qualifications"]) and "no absence statement is made" in r["note"]
    spec = load_builtin_spec("LIB_DW_CRITICAL_SYNTHESIS")
    ev = {e.step_id: e for e in evaluate(spec, facts_from_store(w["store"], problem_id=w["pid"]))}
    assert ev["inspect_recognition"].done is False                                                                       # an incomplete inspection does not complete the step


def test_a_candidate_nobody_inspected_makes_the_inspection_incomplete(w):
    iid = propose(w)
    cands = L.tension_candidates(w["store"], w["lid"], iid, limit=3)
    readable = [c["source_id"] for c in cands["candidates"] if c["readable"]]
    out = L.inspect_tension(w["store"], w["token"], w["lid"], iid, rationale="I read only the first.", candidate_limit=3, results=[res(readable[0], "DOES_NOT_ADDRESS")])
    assert out["status"] == "INSPECTION_INCOMPLETE" and set(out["uninspected"]) == set(readable[1:])


# ------------------------------------------------------------------------------------------------ staleness

def test_a_negative_inspection_goes_stale_when_the_corpus_changes_and_any_inspection_when_an_inspected_source_is_revised(w):
    iid = propose(w)
    L.inspect_tension(w["store"], w["token"], w["lid"], iid, rationale="Read.", candidate_limit=1, terms=["message volume team channels"],
                      results=[res("SRC_MENTION", "MENTIONS_PHENOMENON_ONLY", quotes=[RF.R["mention_a"]])])
    assert rec(w, iid)["needs_reassessment"] == []
    corpus.import_source(w["store"], w["token"], citation_key="late", title="A Late Paper On Message Volume", text="A Late Paper\n\nAbstract\nMessage volume.\n\n1 Introduction\nVolume of messages.\n")
    r = rec(w, iid)
    assert r["status"] == "NOT_FOUND_IN_INSPECTED_SET" and any("corpus changed since this inspection" in x for x in r["needs_reassessment"])           # the label stays; the flag says to look again
    inf = next(i for i in L.view(w["store"], w["lid"])["inferences"] if i["id"] == iid)
    assert inf["needs_reassessment"] == r["needs_reassessment"]
    corpus.set_excluded(w["store"], w["token"], "SRC_MENTION", "withdrawn")
    assert any("SRC_MENTION was revised" in x for x in rec(w, iid)["needs_reassessment"])


def test_a_positive_inspection_is_not_made_stale_by_an_unrelated_new_source_but_is_by_a_revision_of_the_source_it_rests_on(w):
    iid = propose(w)
    L.inspect_tension(w["store"], w["token"], w["lid"], iid, rationale="Read.", results=[res("SRC_PART", "ADDRESSES", quotes=[F.A["part_claim"]])])
    corpus.import_source(w["store"], w["token"], citation_key="late2", title="Another Late Paper", text="Another\n\nAbstract\nX.\n\n1 Introduction\nY.\n")
    assert rec(w, iid)["needs_reassessment"] == []
    corpus.set_excluded(w["store"], w["token"], "SRC_PART", "withdrawn")
    assert any("SRC_PART was revised" in x for x in rec(w, iid)["needs_reassessment"])


def test_a_redefined_tension_is_a_different_tension():
    base = {"kind": "conceptual_divergence", "relationship": "t", "sides": [{"entry": "E1", "passage_id": "EP_1", "passage_revision": 1}, {"entry": "E2", "passage_id": "EP_2", "passage_revision": 1}],
            "shared": "s", "differences": "d", "rationale": "r", "meaning_difference": "m", "levels": {}}
    assert INS.tension_digest(base) == INS.tension_digest(dict(base))
    assert INS.tension_digest(base) != INS.tension_digest({**base, "differences": "d, differently put"})
    assert INS.tension_digest(base) != INS.tension_digest({**base, "sides": [{**base["sides"][0], "passage_revision": 2}, base["sides"][1]]})
    insp = {"id": "N001", "inference": "I9", "tension_digest": INS.tension_digest(base), "corpus_snapshot_id": "a" * 64, "candidates": [{"source_id": "S"}], "uninspected": [], "unreadable": [],
            "results": [{"source_id": "S", "source_revision": 1, "result": "DOES_NOT_ADDRESS", "work_id": "S"}], "search": {"terms": ["x"]}, "decision": {"by": "r"}, "limitations": []}
    ok = INS.recognition_view({**base, "id": "I9", "derivation": "model_proposed"}, [insp], "a" * 64, lambda sid: 1)
    assert ok["needs_reassessment"] == [] and ok["status"] == "NOT_FOUND_IN_INSPECTED_SET"
    changed = INS.recognition_view({**base, "id": "I9", "derivation": "model_proposed", "differences": "changed"}, [insp], "a" * 64, lambda sid: 1)
    assert any("redefined" in x for x in changed["needs_reassessment"])


# ------------------------------------------------------------------------------------------------ no global novelty

def test_no_export_states_novelty_or_that_a_tension_is_unrecognised(w):
    iid = propose(w)
    L.inspect_tension(w["store"], w["token"], w["lid"], iid, rationale="Read.", candidate_limit=1, terms=["message volume team channels"],
                      results=[res("SRC_MENTION", "MENTIONS_PHENOMENON_ONLY", quotes=[RF.R["mention_a"]])])
    exp = build_landscape_export(w["store"], w["lid"])
    md = exp.files["research_landscape.md"].decode("utf-8")
    low = md.lower().replace("not a statement about novelty", "").replace("and not novelty", "").replace("not about novelty", "")
    for banned in ("unrecognised", "unrecognized", "not been recognised", "novel", "no prior", "first to", "never been addressed", "gap in the literature"):
        assert banned not in low, banned
    assert "## Recognition inspections" in md and "NOT_FOUND_IN_INSPECTED_SET" in md and "the inspected set" in md and "corpus snapshot" in md
    assert "Decision by" in md and "Read." in md
    assert "NOT_FOUND_IN_INSPECTED_SET" in exp.files["relations.csv"].decode("utf-8")


# ------------------------------------------------------------------------------------------------ who may, and what may not be inspected

def test_only_a_proposed_relation_is_inspected_and_only_by_an_author_or_owner(w):
    iid = propose(w)
    with pytest.raises(KnowledgeError, match="INSPECTION_NOT_APPLICABLE"):
        L.inspect_tension(w["store"], w["token"], w["lid"], "I001", rationale="r", results=[res("SRC_PART", "DOES_NOT_ADDRESS")])           # a rule-derived observation
    imp = L.propose_relations(w["store"], w["token"], w["lid"], synth([{"kind": "implied_assumption", "topic": "t", "entries": [RF.div_proposal(w["v"])["entries"][0]], "text": "x y z",
                                                                         "reasoning": "r", "missing_information": []}]))["accepted"][0]
    with pytest.raises(KnowledgeError, match="INSPECTION_NOT_APPLICABLE"):
        L.inspect_tension(w["store"], w["token"], w["lid"], imp, rationale="r", results=[res("SRC_PART", "DOES_NOT_ADDRESS")])
    with pytest.raises(KnowledgeError, match="INSPECTION_RESULTS_REQUIRED"):
        L.inspect_tension(w["store"], w["token"], w["lid"], iid, rationale="r", results=[])
    with pytest.raises(KnowledgeError, match="SOURCE_NOT_HELD"):
        L.inspect_tension(w["store"], w["token"], w["lid"], iid, rationale="r", results=[res("SRC_NOPE", "DOES_NOT_ADDRESS")])
    with pytest.raises(KnowledgeError, match="INSPECTION_DUPLICATE_SOURCE"):
        L.inspect_tension(w["store"], w["token"], w["lid"], iid, rationale="r", results=[res("SRC_PART", "DOES_NOT_ADDRESS"), res("SRC_PART", "DOES_NOT_ADDRESS")])
    with pytest.raises(KnowledgeError, match="SOURCE_IS_READABLE"):
        L.inspect_tension(w["store"], w["token"], w["lid"], iid, rationale="r", results=[{"source_id": "SRC_PART", "result": "NOT_READABLE"}])
    viewer = register_actor(w["store"], w["token"], actor_id="viewer_i", display_name="viewer", roles=["importer"])
    with pytest.raises(AuthorizationError):
        L.inspect_tension(w["store"], viewer["token"], w["lid"], iid, rationale="r", results=[res("SRC_PART", "DOES_NOT_ADDRESS")])


def test_cli_candidates_and_inspect(w, tmp_path, capsys):
    iid = propose(w)
    tf = tmp_path / "token"
    tf.write_text(w["token"], encoding="utf-8")
    base = ["--project", "PRJ_REC", "--home", str(w["home"])]
    auth = [*base, "--token-file", str(tf)]
    rf = tmp_path / "results.json"
    rf.write_text(json.dumps([res("SRC_PART", "ADDRESSES", quotes=[F.A["part_claim"]], note="separates the ideas")]), encoding="utf-8")
    assert main(["landscape", "candidates", w["lid"], iid, *base]) == 0
    shown = capsys.readouterr().out
    assert "not evidence that it addresses the tension" in shown and "SRC_PART" in shown
    assert main(["landscape", "inspect", w["lid"], iid, "--results", str(rf), "--rationale", "read it", *auth]) == 0
    assert "ADDRESSED_IN_INSPECTED_SET" in capsys.readouterr().out
    assert main(["landscape", "show", w["lid"], *base]) == 0
    out = capsys.readouterr().out
    assert "recognition ADDRESSED_IN_INSPECTED_SET: SRC_PART@1" in out
