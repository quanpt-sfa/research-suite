"""WP6.1 remediation, P1: the researcher's assessment and the suite's check of its basis are different things, shown apart.

Synthetic acceptance only. BASIS_CHECKED means deterministic checks passed; it never means the conclusion is right.
"""
from __future__ import annotations

import json

import pytest

from research_harness.cli import main
from research_harness.discovery import basis as BAS
from research_harness.discovery import landscape as L
from research_harness.export.landscape import build_landscape_export
from research_harness.knowledge import corpus
from research_harness.knowledge.common import KnowledgeError
from research_harness.workflows import eca
from research_harness.workflows.adapters import ScriptedModelAdapter
from tests import inquiry_fixtures as F


@pytest.fixture
def wa(tmp_path):
    w = F.build_world(tmp_path, name="PRJ_B", papers=F.A_PAPERS, statement=F.A_STATEMENT, terms=F.A_TERMS, inquiry=F.A_INQUIRY)
    w["lid"] = L.build(w["store"], w["token"], w["rid"], F.reader(F.A_RECORDS), versions="all")["landscape_id"]
    w["v"] = L.view(w["store"], w["lid"])
    return w


def entry(w, source, field, label=None):
    return next(e for e in L.view(w["store"], w["lid"])["entries"] if e["source_id"] == source and e["field"] == field and (label is None or e.get("label") == label))


def dim(w, eid):
    return next(e for e in L.view(w["store"], w["lid"])["entries"] if e["id"] == eid)["layers"]["evidence"]["inference"]


RIVAL = [{"threat": "rival: attention is not a single pool", "status": "addressed"}]
ARG_OK = {"premises": ["Attention is scarce", "Participation draws on the same pool"], "steps": [{"from": ["P1", "P2"], "infers": "More messages leave less attention per message"}],
          "conclusion": "Participation dilutes collective attention per message", "rival_assumptions": ["attention is not a single pool"]}


def assess(w, eid, criterion, verdict="SUPPORTED_WITHIN_SCOPE", threats=None, **kw):
    return L.assess_method(w["store"], w["token"], w["lid"], eid, verdict=verdict, criteria=[criterion], threats=RIVAL if threats is None else threats, rationale="r", **kw)


# ------------------------------------------------------------------------------------------------ a sentence is not a traceable basis

def test_a_basis_that_is_only_a_sentence_keeps_the_researchers_verdict_and_is_shown_as_not_traceable(wa):
    claim = entry(wa, "SRC_ATT", "theoretical_claim")["id"]
    out = assess(wa, claim, {"criterion": "the derivation is valid", "basis": "the paper's derivation", "basis_kind": "logical_analysis", "met": "yes"})
    assert out["verdict"] == "SUPPORTED_WITHIN_SCOPE" and out["verification"]["status"] == "BASIS_NOT_TRACEABLE"                 # compatible with the earlier rule: the verdict is the researcher's
    d = dim(wa, claim)
    assert d["assessed_by"] == "RESEARCHER_ASSESSED" and d["judgment"] == "INFERENCE_JUDGMENT" and d["verdict"] == "SUPPORTED_WITHIN_SCOPE"
    assert d["verification"]["status"] == "BASIS_NOT_TRACEABLE" and "recorded without a traceable basis" in d["verification_note"] and "not upgraded" in d["verification_note"]
    assert "score" not in json.dumps(d).lower().replace("scored", "")


def test_an_assessment_recorded_before_basis_checking_existed_is_not_upgraded(wa):
    finding = entry(wa, "SRC_AI", "finding")
    legacy = {"entry": finding["id"], "passage": [finding["author_claim"]["passage_id"], finding["author_claim"]["passage_revision"]], "declared_verdict": "SUPPORTED_WITHIN_SCOPE",
              "verdict": "SUPPORTED_WITHIN_SCOPE", "blockers": [], "criteria": [{"criterion": "randomised", "basis": "a design text", "methodological_literature": True, "met": "yes"}],
              "threats": [{"threat": "demand effects", "status": "addressed"}], "code_evidence": [], "rationale": "r", "generated_by": "researcher"}      # no route, no basis_refs: a WP6-era record
    L._append(wa["store"], wa["token"], wa["lid"], "assessments", legacy, "landscape_assess_method")
    d = dim(wa, finding["id"])
    assert d["route"] == "empirical" and d["verdict"] == "SUPPORTED_WITHIN_SCOPE" and d["verification"]["status"] == "BASIS_NOT_TRACEABLE" and "not upgraded" in d["verification_note"]


# ------------------------------------------------------------------------------------------------ a source or passage reference

def test_a_cited_source_is_traceable_and_a_source_that_does_not_exist_is_a_failed_check(wa):
    claim = entry(wa, "SRC_ATT", "theoretical_claim")
    ok = assess(wa, claim["id"], {"criterion": "consistent with the theory it extends", "basis": "the theory paper", "basis_kind": "theoretical_literature", "met": "yes",
                                  "basis_refs": [{"kind": "source", "ref": "SRC_ATT", "revision": 1}]})
    assert ok["verification"]["status"] == "BASIS_TRACEABLE" and ok["verdict"] == "SUPPORTED_WITHIN_SCOPE"
    bad = assess(wa, claim["id"], {"criterion": "c", "basis": "b", "basis_kind": "theoretical_literature", "met": "yes", "basis_refs": [{"kind": "source", "ref": "SRC_NOPE"}]})
    assert bad["verification"]["status"] == "BASIS_CHECK_FAILED" and bad["verdict"] == "LIMITED" and any("failed its checks" in b for b in bad["blockers"])      # a failed basis does not count
    with pytest.raises(KnowledgeError, match="BASIS_REFERENCE_INVALID"):
        assess(wa, claim["id"], {"criterion": "c", "basis": "b", "met": "yes", "basis_refs": [{"kind": "hunch", "ref": "x"}]})


def test_a_passage_whose_text_does_not_contain_what_the_criterion_relies_on_fails_the_check_and_one_that_does_is_checked(wa):
    claim = entry(wa, "SRC_ATT", "theoretical_claim")
    pid, prev = claim["author_claim"]["passage_id"], claim["author_claim"]["passage_revision"]
    out_ok = L.assess_method(wa["store"], wa["token"], wa["lid"], entry(wa, "SRC_ATT", "theoretical_claim")["id"], verdict="SUPPORTED_WITHIN_SCOPE", threats=RIVAL, rationale="r", criteria=[
        {"criterion": "consistent with the theory", "basis": "the passage", "basis_kind": "theoretical_literature", "met": "yes",
         "basis_refs": [{"kind": "passage", "ref": pid, "revision": prev, "excerpt": "dilutes collective attention per message", "supports_only": "that the authors state the dilution claim; not that it holds"}]}])
    assert out_ok["verification"]["status"] == "BASIS_CHECKED" and out_ok["verdict"] == "SUPPORTED_WITHIN_SCOPE"
    wrong = L.assess_method(wa["store"], wa["token"], wa["lid"], claim["id"], verdict="SUPPORTED_WITHIN_SCOPE", threats=RIVAL, rationale="r", criteria=[
        {"criterion": "consistent with the theory", "basis": "the passage", "basis_kind": "theoretical_literature", "met": "yes",
         "basis_refs": [{"kind": "passage", "ref": pid, "revision": prev, "excerpt": "engagement is expressed through effortful communication", "supports_only": "x"}]}])
    assert wrong["verification"]["status"] == "BASIS_CHECK_FAILED" and wrong["verdict"] == "LIMITED" and "the passage does not contain what the criterion relies on" in wrong["verification"]["criteria"][0]["problems"][0]
    traceable = L.assess_method(wa["store"], wa["token"], wa["lid"], claim["id"], verdict="SUPPORTED_WITHIN_SCOPE", threats=RIVAL, rationale="r", criteria=[
        {"criterion": "consistent with the theory", "basis": "the passage", "basis_kind": "theoretical_literature", "met": "yes", "basis_refs": [{"kind": "passage", "ref": pid, "revision": prev,
                                                                                                                                              "excerpt": "dilutes collective attention per message"}]}])
    assert traceable["verification"]["status"] == "BASIS_TRACEABLE" and any("supports_only" in p for p in traceable["verification"]["criteria"][0]["problems"])    # without the limit of support, only traceable
    notp = L.assess_method(wa["store"], wa["token"], wa["lid"], claim["id"], verdict="LIMITED", threats=[], rationale="r", criteria=[
        {"criterion": "c", "basis": "b", "basis_kind": "theoretical_literature", "met": "yes", "basis_refs": [{"kind": "passage", "ref": "SRC_ATT"}]}])
    assert notp["verification"]["status"] == "BASIS_CHECK_FAILED"                                                                  # a Source is not an EvidencePassage


def test_a_source_revised_after_it_was_cited_makes_the_basis_stale_whatever_the_verdict(wa):
    claim = entry(wa, "SRC_ATT", "theoretical_claim")
    pid, prev = claim["author_claim"]["passage_id"], claim["author_claim"]["passage_revision"]
    L.assess_method(wa["store"], wa["token"], wa["lid"], claim["id"], verdict="SUPPORTED_WITHIN_SCOPE", threats=RIVAL, rationale="r", criteria=[
        {"criterion": "consistent with the theory", "basis": "the passage", "basis_kind": "theoretical_literature", "met": "yes",
         "basis_refs": [{"kind": "passage", "ref": pid, "revision": prev, "excerpt": "dilutes collective attention", "supports_only": "that the authors state it"}]}])
    assert dim(wa, claim["id"])["verification"]["status"] == "BASIS_CHECKED"
    corpus.set_excluded(wa["store"], wa["token"], "SRC_ATT", "checking")
    corpus.set_excluded(wa["store"], wa["token"], "SRC_ATT", None)                                 # a new revision of the cited source
    d = dim(wa, claim["id"])
    assert d["verification"]["status"] == "BASIS_STALE" and d["verdict"] == "SUPPORTED_WITHIN_SCOPE"                      # the researcher's verdict is untouched; the suite says the basis moved
    assert any("was revised" in p for c in d["verification"]["criteria"] for p in c["problems"])


# ------------------------------------------------------------------------------------------------ a logical analysis is an argument record, not a label

def test_logical_analysis_needs_a_complete_argument_record_before_it_is_checked(wa):
    claim = entry(wa, "SRC_ATT", "theoretical_claim")["id"]
    ok = assess(wa, claim, {"criterion": "the step from participation to dilution is valid", "basis": "reconstructed argument", "basis_kind": "logical_analysis", "met": "yes", "argument": ARG_OK})
    assert ok["verification"]["status"] == "BASIS_CHECKED" and ok["verdict"] == "SUPPORTED_WITHIN_SCOPE"
    for broken, needle in (({**ARG_OK, "steps": [{"from": ["P1", "P3"], "infers": "x"}]}, "relies on P3"), ({**ARG_OK, "premises": []}, "at least one premise"),
                           ({**ARG_OK, "conclusion": " "}, "state the conclusion"), ({**ARG_OK, "rival_assumptions": []}, "rival assumptions"),
                           ({**ARG_OK, "steps": [{"from": ["S1"], "infers": "x"}]}, "relies on S1"), ({**ARG_OK, "steps": []}, "at least one inference step")):
        out = assess(wa, claim, {"criterion": "c", "basis": "b", "basis_kind": "logical_analysis", "met": "yes", "argument": broken})
        assert out["verification"]["status"] == "BASIS_CHECK_FAILED" and any(needle in p for p in out["verification"]["criteria"][0]["problems"]), needle
        assert out["verdict"] == "LIMITED"
    justified = assess(wa, claim, {"criterion": "c", "basis": "b", "basis_kind": "logical_analysis", "met": "yes", "argument": {**ARG_OK, "rival_assumptions": [], "no_rivals_reason": "the claim is conditional on the premises"}})
    assert justified["verification"]["status"] == "BASIS_CHECKED"
    assert BAS.verify_argument(ARG_OK) == ("BASIS_CHECKED", [])
    assert BAS.verify_assessment(wa["store"], [], ("logical_analysis",))["status"] == "NO_ACCEPTED_BASIS"


# ------------------------------------------------------------------------------------------------ the verdict stays the researcher's; other routes and A5 unchanged

def test_an_interpretive_finding_with_an_open_alternative_interpretation_is_capped_whatever_the_basis_check_says(tmp_path):
    w = F.build_world(tmp_path, name="PRJ_I", papers=F.B_PAPERS, statement=F.B_STATEMENT, terms=F.B_TERMS, inquiry=F.B_INQUIRY)
    w["lid"] = L.build(w["store"], w["token"], w["rid"], F.reader(F.B_RECORDS))["landscape_id"]
    theme = entry(w, "SRC_MON", "interpretive_finding", "checking by execution")
    pid, prev = theme["author_claim"]["passage_id"], theme["author_claim"]["passage_revision"]
    out = L.assess_method(w["store"], w["token"], w["lid"], theme["id"], verdict="SUPPORTED_WITHIN_SCOPE", rationale="r", threats=[{"threat": "alternative reading: checking by habit", "status": "open"}], criteria=[
        {"criterion": "the excerpt shows the participants' own words", "basis": "the passage", "basis_kind": "textual_evidence", "met": "yes",
         "basis_refs": [{"kind": "passage", "ref": pid, "revision": prev, "excerpt": "whether it ran", "supports_only": "that students described checking by execution; not that this is general"}]}])
    assert out["verification"]["status"] == "BASIS_CHECKED" and out["verdict"] == "LIMITED" and any("rival explanation or threat is still open" in b for b in out["blockers"])
    d = dim(w, theme["id"])
    assert d["route"] == "interpretive" and d["verdict"] == "LIMITED" and d["verification"]["status"] == "BASIS_CHECKED"       # the two statuses are independent


def test_the_empirical_route_keeps_its_rule_and_nothing_else_changes_the_verification(wa):
    finding = entry(wa, "SRC_AI", "finding")["id"]
    weak = assess(wa, finding, {"criterion": "randomised", "basis": "a logic argument", "basis_kind": "logical_analysis", "argument": ARG_OK, "met": "yes"}, threats=[{"threat": "demand effects", "status": "addressed"}])
    assert weak["route"] == "empirical" and weak["verdict"] == "LIMITED" and any("methodological literature" in b for b in weak["blockers"]) and weak["verification"]["status"] == "NO_ACCEPTED_BASIS"
    ok = assess(wa, finding, {"criterion": "randomised", "basis": "a design text", "methodological_literature": True, "met": "yes"}, threats=[{"threat": "demand effects", "status": "addressed"}])
    assert ok["verdict"] == "SUPPORTED_WITHIN_SCOPE" and ok["verification"]["status"] == "BASIS_NOT_TRACEABLE"
    before = dim(wa, finding)["verification"]
    st = eca.resolve_statement(wa["store"], wa["lid"], element=finding)
    r = eca.adjudicate(wa["store"], wa["token"], st, eca.basis_passage(wa["store"], wa["lid"], finding),
                       ScriptedModelAdapter(model_id="adj", default_response={"passage_proposition": "p", "equivalence": "EQUIVALENT", "dropped_conditions": [], "cue_handling": [], "verdict": "SUPPORTED_AS_STATED", "required_qualification": []}))
    eca.confirm(wa["store"], wa["token"], r["adjudication_id"], verdict="SUPPORTED_AS_STATED", rationale="a fair reading")
    after = dim(wa, finding)
    assert after["verification"] == before and entry(wa, "SRC_AI", "finding")["layers"]["evidence"]["reading"]["value"] == "READING_CONFIRMED"      # a correct reading does not check the basis of an inference


# ------------------------------------------------------------------------------------------------ both shown apart, in the CLI and in the export

def test_the_researchers_verdict_and_the_suites_verification_are_shown_apart_in_the_cli_and_the_export(wa, tmp_path, capsys):
    claim = entry(wa, "SRC_ATT", "theoretical_claim")["id"]
    assess(wa, claim, {"criterion": "valid", "basis": "reconstructed argument", "basis_kind": "logical_analysis", "met": "yes", "argument": ARG_OK})
    tf = tmp_path / "token"
    tf.write_text(wa["token"], encoding="utf-8")
    base = ["--project", "PRJ_B", "--home", str(wa["home"])]
    assert main(["landscape", "show", wa["lid"], *base]) == 0
    shown = capsys.readouterr().out
    assert "researcher's verdict SUPPORTED_WITHIN_SCOPE, conceptual route; suite's check of the basis: BASIS_CHECKED" in shown
    md = build_landscape_export(wa["store"], wa["lid"]).files["research_landscape.md"].decode("utf-8")
    assert "researcher's verdict SUPPORTED_WITHIN_SCOPE (conceptual); suite's check of the basis BASIS_CHECKED" in md
    assert "RESEARCHER_ASSESSED, INFERENCE_JUDGMENT" in md and "BASIS_NOT_TRACEABLE (a sentence only; every assessment recorded before basis checking existed is shown so)" in md
    assert "not a finding that the basis supports the conclusion" in md and "| suite's check of the basis |" in md
    cf = tmp_path / "criteria.json"
    cf.write_text(json.dumps([{"criterion": "valid", "basis": "b", "basis_kind": "logical_analysis", "met": "yes", "argument": ARG_OK}]), encoding="utf-8")
    auth = [*base, "--token-file", str(tf)]
    assert main(["landscape", "assess", wa["lid"], claim, "--verdict", "SUPPORTED_WITHIN_SCOPE", "--criteria", str(cf), "--threats", json.dumps(RIVAL), "--rationale", "r", *auth, "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["verification"]["status"] == "BASIS_CHECKED" and out["verdict"] == "SUPPORTED_WITHIN_SCOPE"
