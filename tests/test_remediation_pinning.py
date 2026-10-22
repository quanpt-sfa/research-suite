"""WP6.1 remediation, P1: a question promoted from a problem keeps the inquiry of the problem revision it was promoted from, until the researcher says otherwise."""
from __future__ import annotations

import json

import pytest

from research_harness.cli import main
from research_harness.core.errors import AuthorizationError
from research_harness.discovery import problem as P
from research_harness.knowledge.common import KnowledgeError
from research_harness.state.actors import register_actor
from research_harness.workflows.dispatcher import run_workflow
from research_harness.workflows.domain_engine import facts_from_store, load_builtin_spec, resolve_spec, stage_status, suggest_contracts
from research_harness.workflows.status import Applicability
from tests import inquiry_fixtures as F

EVAL = {"research_purposes": ["evaluation"], "research_design": "design_based", "evidence_forms": ["qualitative_interpretation"], "discipline": {"domain": "education", "subfield": "design_based_research"}}


@pytest.fixture
def scenario(tmp_path):
    """Problem revision 3 has the purpose critical_synthesis; it is promoted to RQ_000001; revision 5 then says evaluation."""
    w = F.build_world(tmp_path, name="PRJ_PIN", papers=F.A_PAPERS, statement=F.A_STATEMENT, terms=F.A_TERMS, inquiry=F.A_INQUIRY, search=False)
    s, t, pid = w["store"], w["token"], w["pid"]
    P.add_interpretation(s, t, pid, "AI assistance separates producing a message from investing in a relationship.")
    P.choose(s, t, pid, ["I1"], "the reading pursued")
    assert s.get_object(pid)["revision"] == 3 and s.get_object(pid)["content"]["inquiry"]["research_purposes"] == ["critical_synthesis", "theory_development"]
    w["rq"] = P.promote_to_rq(s, t, pid, "I1", "How does AI-assisted communication separate participation from relational engagement at work?", "my wording")["research_question"]
    assert w["rq"] == "RQ_000001" and s.get_object(pid)["revision"] == 4
    with s.write_tx(token=t) as tx:                                   # something that rests on the question
        tx.add_revision(object_id="CL_ON_RQ", object_type="Claim", content={"text": "A claim about the question.", "claim_tier": "conceptual"}, origin={},
                        edges=[("depends_on", w["rq"], 1, {})])
    P.revise_problem(s, t, pid, rationale="the study became an evaluation of a design", inquiry=EVAL)
    assert s.get_object(pid)["revision"] == 5 and s.get_object(pid)["content"]["inquiry"]["research_purposes"] == ["evaluation"]
    return w


def test_the_question_keeps_the_inquiry_of_the_revision_it_was_promoted_from(scenario):
    w = scenario
    f = facts_from_store(w["store"], research_question_id=w["rq"])
    assert f.purposes() == {"critical_synthesis", "theory_development"} and f.study_design == "narrative_review"
    assert f.inquiry_source == {"problem_id": w["pid"], "revision": 4, "pinned": True}
    head = facts_from_store(w["store"], problem_id=w["pid"])                                                         # asking about the problem itself reads its head
    assert head.purposes() == {"evaluation"} and head.study_design == "design_based" and head.inquiry_source["pinned"] is False


def test_the_engine_does_not_change_contract_until_the_researcher_updates_the_question(scenario):
    w = scenario
    f = facts_from_store(w["store"], research_question_id=w["rq"])
    s = suggest_contracts(f)
    assert s["candidates"] == ["LIB_DW_CRITICAL_SYNTHESIS"]                                                          # not the evaluation contracts
    qual = load_builtin_spec("LIB_DW_QUALITATIVE_INQUIRY")
    assert stage_status(qual, f, "methods").applicability is Applicability.NOT_APPLICABLE
    crit = load_builtin_spec("LIB_DW_CRITICAL_SYNTHESIS")
    assert run_workflow("methods", w["store"], research_question_id=w["rq"], domain_workflow=crit, reuse_cached=False).status == "INSUFFICIENT_EVIDENCE"       # the contract applies
    assert run_workflow("methods", w["store"], research_question_id=w["rq"], domain_workflow=qual, reuse_cached=False).status == "NOT_APPLICABLE"
    with w["store"].read() as conn:       # reframing the problem flags what rests on it (WP4); it does not by itself make the question follow the new inquiry
        causes = [r[0] for r in conn.execute("SELECT cause_object_id FROM invalidations WHERE object_id='CL_ON_RQ'").fetchall()]
    assert causes and set(causes) == {w["pid"]}
    view = P.problem_view(w["store"], w["pid"])
    assert view["questions_to_review"][w["rq"]]["pinned_to_older_problem_revision"]                                  # the problem has moved on; the question says so and stays as it was


def test_updating_the_question_is_an_explicit_recorded_act_that_flags_what_rests_on_it(scenario):
    w = scenario
    s, t = w["store"], w["token"]
    with pytest.raises(KnowledgeError, match="RATIONALE_REQUIRED"):
        P.update_question_inquiry(s, t, w["rq"], " ")
    with pytest.raises(KnowledgeError, match="RQ_NOT_FOUND"):
        P.update_question_inquiry(s, t, "RQ_999999", "x")
    viewer = register_actor(s, t, actor_id="viewer_p", display_name="viewer", roles=["importer"])
    with pytest.raises(AuthorizationError):
        P.update_question_inquiry(s, viewer["token"], w["rq"], "x")
    out = P.update_question_inquiry(s, t, w["rq"], "the study is now an evaluation of the design; the question follows it")
    assert out["revision"] == 2 and out["from_problem_revision"] == 4 and out["to_problem_revision"] == 5 and out["inquiry_before"]["research_purposes"] == ["critical_synthesis", "theory_development"]
    assert out["inquiry_after"]["research_purposes"] == ["evaluation"] and "CL_ON_RQ@1" in out["needs_review"]
    f = facts_from_store(s, research_question_id=w["rq"])
    assert f.purposes() == {"evaluation"} and f.study_design == "design_based" and f.inquiry_source == {"problem_id": w["pid"], "revision": 5, "pinned": True}
    c = s.get_object(w["rq"])["content"]
    log = c["attributes"]["inquiry_updates"]
    assert len(log) == 1 and log[0]["by"] and log[0]["at"] and log[0]["rationale"].startswith("the study is now") and log[0]["from_problem_revision"] == 4 and c["attributes"]["promoted_from"]["revision"] == 5
    assert s.get_object(w["rq"], 1)["content"]["attributes"]["promoted_from"]["revision"] == 4                      # the history is kept: revision 1 of the question still says what it said
    with s.read() as conn:                 # and now the researcher's act is itself a cause
        causes = {r[0] for r in conn.execute("SELECT cause_object_id FROM invalidations WHERE object_id='CL_ON_RQ'").fetchall()}
    assert causes == {w["pid"], w["rq"]}
    assert "LIB_DW_QUALITATIVE_INQUIRY" in suggest_contracts(f)["candidates"] and "LIB_DW_CRITICAL_SYNTHESIS" not in suggest_contracts(f)["candidates"]
    with pytest.raises(KnowledgeError, match="NOTHING_TO_UPDATE"):
        P.update_question_inquiry(s, t, w["rq"], "again")
    assert not P.problem_view(s, w["pid"])["questions_to_review"][w["rq"]]["pinned_to_older_problem_revision"]


def test_a_question_that_was_not_promoted_has_no_problem_inquiry_to_follow(tmp_path):
    w = F.build_world(tmp_path, name="PRJ_NP", papers=F.A_PAPERS, statement=F.A_STATEMENT, terms=F.A_TERMS, inquiry=F.A_INQUIRY, search=False)
    with w["store"].write_tx(token=w["token"]) as tx:
        tx.add_revision(object_id="RQ_X", object_type="ResearchQuestion", content={"text": "A question written directly in the store."}, origin={}, edges=[])
    with pytest.raises(KnowledgeError, match="RQ_NOT_PROMOTED"):
        P.update_question_inquiry(w["store"], w["token"], "RQ_X", "x")
    f = facts_from_store(w["store"], research_question_id="RQ_X")
    assert f.purposes() == set() and f.study_design is None and f.inquiry_source is None                             # nothing is borrowed from a problem it was not promoted from


def test_an_undeclared_purpose_never_selects_the_empirical_workflow(tmp_path, capsys):
    w = F.build_world(tmp_path, name="PRJ_UND", papers=F.A_PAPERS, statement=F.A_STATEMENT, terms=F.A_TERMS, search=False)        # a problem with no inquiry
    P.add_interpretation(w["store"], w["token"], w["pid"], "AI assistance separates producing a message from investing in a relationship.")
    P.choose(w["store"], w["token"], w["pid"], ["I1"], "the reading pursued")
    rq = P.promote_to_rq(w["store"], w["token"], w["pid"], "I1", "How does AI-assisted communication separate participation from engagement?", "my wording")["research_question"]
    f = facts_from_store(w["store"], research_question_id=rq)
    assert f.purposes() == set() and f.study_design is None
    s = suggest_contracts(f)
    assert s["status"] == "INQUIRY_NOT_RECORDED" and s["candidates"] == [] and "LIB_DW_ACC_FRAUD_DETECTION" not in s["candidates"]
    assert {r["spec"]: r["fit"] for r in s["contracts"]}["LIB_DW_ACC_FRAUD_DETECTION"] == "INQUIRY_INCOMPLETE" and resolve_spec(None).id == "LIB_DW_GENERIC"
    tf = tmp_path / "token"
    tf.write_text(w["token"], encoding="utf-8")
    assert main(["domain", "suggest", "--project", "PRJ_UND", "--home", str(w["home"]), "--question", rq]) == 0
    out = capsys.readouterr().out
    assert "INQUIRY_NOT_RECORDED" in out and "no contract is chosen" in out and "NO_GUIDANCE" in out
    assert main(["problem", "update-question", rq, "--project", "PRJ_UND", "--home", str(w["home"]), "--token-file", str(tf), "--rationale", "nothing recorded yet"]) == 1
    assert "NOTHING_TO_UPDATE" in capsys.readouterr().err


def test_cli_update_question(scenario, tmp_path, capsys):
    w = scenario
    tf = tmp_path / "token"
    tf.write_text(w["token"], encoding="utf-8")
    base = ["--project", "PRJ_PIN", "--home", str(w["home"])]
    assert main(["problem", "update-question", w["rq"], *base, "--token-file", str(tf), "--rationale", "follow the evaluation"]) == 0
    assert "now follows PROB_000001 r5 (was r4)" in capsys.readouterr().out
    assert main(["domain", "suggest", *base, "--question", w["rq"], "--json"]) == 0
    assert "LIB_DW_QUALITATIVE_INQUIRY" in json.loads(capsys.readouterr().out)["candidates"]
