"""WP6.1 remediation: the method of a literature review as a record, and its audit against the corpus."""
from __future__ import annotations

import copy
import json

import pytest

from research_harness.cli import main
from research_harness.core.errors import AuthorizationError
from research_harness.discovery import problem as P
from research_harness.discovery import retrieval as RT
from research_harness.export.manifest import verify_export, write_export
from research_harness.export.review import build_review_export
from research_harness.knowledge import corpus
from research_harness.knowledge import review_protocol as RP
from research_harness.knowledge.common import KnowledgeError
from research_harness.state.actors import register_actor
from tests import inquiry_fixtures as F
from tests import remediation_fixtures as RF


@pytest.fixture
def wa(tmp_path):
    return F.build_world(tmp_path, name="PRJ_RV", papers=F.A_PAPERS, statement=F.A_STATEMENT, terms=F.A_TERMS, inquiry=F.A_INQUIRY)


def status(w):
    return {k: v["status"] for k, v in RP.component_statuses(w["store"].get_object(w["pid"])["content"].get("review_protocol")).items()}


def test_a_shape_that_is_not_a_review_protocol_is_refused_and_a_missing_part_is_reported_not_refused(wa):
    for bad in ({}, {"streams": "S1"}, {"nonsense": 1}, {"review_type": "wishful"}, {"search": {"sources": [{"name": "x", "kind": "magic", "status": "executed"}]}},
                {"screening": {"log": [{"record": "r", "stage": "dedup", "decision": "exclude"}]}}):
        with pytest.raises(KnowledgeError):
            RP.set_review_protocol(wa["store"], wa["token"], wa["pid"], bad, "x")
    with pytest.raises(KnowledgeError, match="RATIONALE_REQUIRED"):
        RP.set_review_protocol(wa["store"], wa["token"], wa["pid"], RF.PROTOCOL_A, " ")
    RP.set_review_protocol(wa["store"], wa["token"], wa["pid"], {"review_type": "narrative", "streams": [{"id": "S1", "name": "n", "question": "q"}]}, "a first, partial draft")
    st = status(wa)
    assert st["search"] == "INCOMPLETE" and st["screening"] == "NOT_DECLARED" and st["appraisal"] == "NOT_DECLARED"


def test_a_plan_drafted_by_an_assistant_is_a_draft_until_the_researcher_adopts_exactly_that_plan(wa):
    RF.adopt_protocol(wa, adopt=False)
    st = status(wa)
    assert st == {"search": "DRAFT_COMPLETE", "screening": "DRAFT_COMPLETE", "appraisal": "DRAFT_COMPLETE", "extraction": "DRAFT_COMPLETE", "synthesis": "DRAFT_COMPLETE"}
    rp = wa["store"].get_object(wa["pid"])["content"]["review_protocol"]
    assert rp["status"] == "DRAFT" and rp["drafted_by"] == "assistant:test" and not RP.is_adopted(rp)
    RP.adopt_review_protocol(wa["store"], wa["token"], wa["pid"], "this is the plan I will follow")
    st = status(wa)
    assert st["search"] == "COMPLETE" and st["screening"] == "COMPLETE" and st["appraisal"] == "NOT_PERFORMED_JUSTIFIED"
    rp = wa["store"].get_object(wa["pid"])["content"]["review_protocol"]
    assert rp["status"] == "ADOPTED_BY_RESEARCHER" and rp["adopted_by"] and rp["adopted_at"] and RP.is_adopted(rp)
    viewer = register_actor(wa["store"], wa["token"], actor_id="viewer_r", display_name="viewer", roles=["importer"])
    with pytest.raises(AuthorizationError):
        RP.adopt_review_protocol(wa["store"], viewer["token"], wa["pid"], "x")


def test_editing_the_plan_voids_the_adoption_but_appending_to_the_log_does_not(wa):
    RF.adopt_protocol(wa)
    RP.record_screening(wa["store"], wa["token"], wa["pid"], [{"record": "late-1", "stage": "screening", "decision": "exclude", "reason": "out of scope"}], "second pass")
    assert status(wa)["search"] == "COMPLETE"
    plan = copy.deepcopy(RF.PROTOCOL_A)
    plan["screening"]["criteria"]["exclude"].append("conference abstracts")
    RP.set_review_protocol(wa["store"], wa["token"], wa["pid"], plan, "tightened the exclusion criteria")
    st = status(wa)
    assert st["search"] == "DRAFT_COMPLETE" and st["screening"] == "DRAFT_COMPLETE"                       # the plan changed: adopt it again
    assert len(wa["store"].get_object(wa["pid"])["content"]["review_protocol"]["screening"]["log"]) == len(RF.screening_log()) + 1       # the decisions already made are kept
    log = RP.audit_review(wa["store"], wa["pid"])
    assert log["protocol_status"] == "DRAFT" and any("not adopted" in b for b in log["readiness"]["blockers"])


def test_each_component_says_what_is_missing(wa):
    plan = copy.deepcopy(RF.PROTOCOL_A)
    del plan["search"]["date_to"]
    plan["screening"]["criteria"]["exclude"] = []
    plan["appraisal"] = {"performed": True}
    plan["extraction"] = {"fields": ["theory invoked"]}
    plan["synthesis"] = {"method": "m"}
    RP.set_review_protocol(wa["store"], wa["token"], wa["pid"], plan, "an incomplete plan")
    RP.adopt_review_protocol(wa["store"], wa["token"], wa["pid"], "adopted although incomplete")
    c = RP.component_statuses(wa["store"].get_object(wa["pid"])["content"]["review_protocol"])
    assert c["search"]["status"] == "INCOMPLETE" and any("date_to" in p for p in c["search"]["problems"])
    assert c["screening"]["status"] == "INCOMPLETE" and any("exclusion criteria" in p for p in c["screening"]["problems"]) and any("log is empty" in p for p in c["screening"]["problems"])
    assert c["appraisal"]["status"] == "INCOMPLETE" and c["extraction"]["status"] == "INCOMPLETE" and c["synthesis"]["status"] == "INCOMPLETE"
    assert any("tension" in p for p in c["synthesis"]["problems"])


def test_the_audit_compares_the_record_with_the_corpus(wa):
    RF.adopt_protocol(wa, log=RF.screening_log([{"record": "ghost2019", "stage": "eligibility", "decision": "include", "stream": "S2"}]))
    corpus.import_metadata_only(wa["store"], wa["token"], citation_key="meta1", title="Held As Metadata Only", year=2020)
    RP.record_screening(wa["store"], wa["token"], wa["pid"], [{"record": "meta1", "stage": "eligibility", "decision": "include", "stream": "S1"}], "a paper I know but do not hold")
    a = RP.audit_review(wa["store"], wa["pid"])
    assert a["protocol_status"] == "ADOPTED_BY_RESEARCHER" and a["prisma"]["identified"] == 180 and a["prisma"]["duplicates_removed"] == 2
    assert a["included_not_in_corpus"] == ["ghost2019"] and a["included_without_full_text"] == ["meta1"]
    assert {r["record"]: r["full_text"] for r in a["included"]}["att"] is True and a["readiness"]["can_build_landscape"] is False
    assert any("ghost2019" not in b and "not in the corpus" in b for b in a["readiness"]["blockers"]) and any("no full text" in b for b in a["readiness"]["blockers"])
    assert a["held_but_never_screened"] == []                                                                  # the five corpus papers and the metadata-only one were all mentioned
    assert {s["id"]: s["included_with_full_text"] for s in a["streams"]}["S3"] == 2
    assert "does not say the search was adequate" in " ".join(a["limits"])


def test_a_clean_adopted_review_is_ready_and_a_paper_nobody_screened_is_reported(wa):
    RF.adopt_protocol(wa)
    corpus.import_source(wa["store"], wa["token"], citation_key="stray", title="A Stray Paper", text="A Stray Paper\n\nAbstract\nParticipation.\n\n1 Introduction\nText.\n")
    a = RP.audit_review(wa["store"], wa["pid"])
    assert a["held_but_never_screened"] == ["SRC_STRAY"] and a["readiness"]["can_build_landscape"] is True
    assert RP.audit_review(wa["store"], "PROB_000001")["protocol_status"] == "ADOPTED_BY_RESEARCHER"


def test_recording_a_plan_does_not_make_a_retrieval_stale_or_flag_the_questions_of_the_problem(wa):
    P.add_interpretation(wa["store"], wa["token"], wa["pid"], "AI assistance separates producing a message from investing in a relationship.")
    P.choose(wa["store"], wa["token"], wa["pid"], ["I1"], "the reading pursued")
    rq = P.promote_to_rq(wa["store"], wa["token"], wa["pid"], "I1", "How does AI-assisted communication separate participation from engagement?", "my wording")["research_question"]
    rid = RT.new_run(wa["store"], wa["token"], wa["pid"], terms=F.A_TERMS, top_n=50)["retrieval_id"]
    RT.search(wa["store"], wa["token"], rid)
    assert RT.run_view(wa["store"], rid)["currency"]["problem"]["status"] == "CURRENT"
    RF.adopt_protocol(wa)
    assert RT.run_view(wa["store"], rid)["currency"]["problem"]["status"] == "CURRENT"                       # the plan is not among the inputs of the queries
    with wa["store"].read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM invalidations WHERE object_id=?", (rq,)).fetchone()[0] == 0


def test_the_review_export_states_the_record_and_verifies(wa, tmp_path):
    RF.adopt_protocol(wa, adopt=False)
    exp = build_review_export(wa["store"], wa["pid"])
    md = exp.files["search_protocol.md"].decode("utf-8")
    assert "Protocol status: **DRAFT**" in md and "has not adopted this plan" in md and "drafted by assistant:test" in md and "model_synthesised" in md
    assert "Not ready" in md and "does not say that the search was adequate" in md
    assert exp.files["screening_log.csv"].decode("utf-8").startswith("record,stage,decision,reason,stream,by,at\n")
    inv = exp.files["corpus_inventory.csv"].decode("utf-8")
    assert inv.splitlines()[0].startswith("source_id,revision,citation_key,doi,title,year,tier") and "SRC_ATT" in inv
    RP.adopt_review_protocol(wa["store"], wa["token"], wa["pid"], "adopted")
    exp2 = build_review_export(wa["store"], wa["pid"])
    assert "adopted by" in exp2.files["search_protocol.md"].decode("utf-8") and "researcher_confirmed" in exp2.files["search_protocol.md"].decode("utf-8")
    write_export(wa["store"], exp2, tmp_path / "rv_export")
    assert verify_export(wa["store"], tmp_path / "rv_export", lambda scope: build_review_export(wa["store"], scope["problem"]))["status"] == "VERIFIED"
    empty = F.build_world(tmp_path / "e", name="PRJ_E", papers=F.A_PAPERS, statement=F.A_STATEMENT, terms=F.A_TERMS)
    ex = build_review_export(empty["store"], empty["pid"])
    assert ex.not_available and "NOT_AVAILABLE" in ex.files["search_protocol.md"].decode("utf-8")


def test_review_protocol_cli_end_to_end(wa, tmp_path, capsys):
    tf = tmp_path / "token"
    tf.write_text(wa["token"], encoding="utf-8")
    pf, lf = tmp_path / "protocol.json", tmp_path / "log.json"
    pf.write_text(json.dumps(RF.PROTOCOL_A), encoding="utf-8")
    lf.write_text(json.dumps(RF.screening_log()), encoding="utf-8")
    base = ["--project", "PRJ_RV", "--home", str(wa["home"])]
    auth = [*base, "--token-file", str(tf)]

    def j(*argv, rc=0):
        assert main([*map(str, argv), "--json"]) == rc, capsys.readouterr().err
        return json.loads(capsys.readouterr().out)

    j("review-protocol", "set", wa["pid"], "--protocol", pf, "--rationale", "record the plan", "--drafted-by", "assistant:test", *auth)
    j("review-protocol", "screen", wa["pid"], "--entries", lf, "--rationale", "first pass", *auth)
    assert j("review-protocol", "audit", wa["pid"], *base)["protocol_status"] == "DRAFT"
    j("review-protocol", "adopt", wa["pid"], "--rationale", "mine", *auth)
    assert main(["review-protocol", "audit", wa["pid"], *base]) == 0
    shown = capsys.readouterr().out
    assert "ADOPTED_BY_RESEARCHER" in shown and "ready to build a landscape: yes" in shown
    out_dir = tmp_path / "rv"
    j("export", "review", wa["pid"], "--out", out_dir, *base)
    assert main(["export", "verify", str(out_dir), *base]) == 0 and capsys.readouterr().out.strip() == "VERIFIED"
