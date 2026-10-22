"""M5 WP4: ResearchProblem. A phenomenon that is not yet a question, with several readings, the researcher's decisions, and a path to a question."""
from __future__ import annotations

import json

import jsonschema
import pytest

from research_harness.cli import main
from research_harness.core.errors import AuthorizationError, HarnessError
from research_harness.discovery import problem as P
from research_harness.knowledge.common import KnowledgeError
from research_harness.protocols import schema
from research_harness.state import workflow_runs
from research_harness.state.actors import register_actor
from research_harness.state.store import StateStore
from research_harness.tasks import engine as T
from research_harness.validation.integrity import audit_project_integrity
from research_harness.workflows.adapters import ModelError, ScriptedModelAdapter
from research_harness.workflows.context import build_context

STATEMENT = "Firms that commit reporting fraud seem to change how they report after detection models become widely known."


def proposals(*texts, extra=None):
    return ScriptedModelAdapter(model_id="scripted-framer", default_response={"interpretations": [
        {"text": t, "constructs": ["detection", "adaptation"], "unit_of_analysis": "firm-year", "answer_kind": "causal",
         "search_terms": ["earnings manipulation", "detection model"], **(extra or {})} for t in texts]})


@pytest.fixture
def world(tmp_path):
    store, token = StateStore.create(tmp_path / "home", "PRJ_PROB", owner_id="researcher")
    return {"store": store, "token": token, "tmp": tmp_path, "home": tmp_path / "home"}


def make(w, **kw):
    return P.new_problem(w["store"], w["token"], statement=kw.pop("statement", STATEMENT), **kw)["problem_id"]


def content(w, pid, revision=None):
    return w["store"].get_object(pid, revision)["content"]


# ------------------------------------------------------------------------------------------------ creation

def test_a_problem_needs_no_corpus_introduction_or_question(world):
    w = world
    out = P.new_problem(w["store"], w["token"], statement=STATEMENT, kind="phenomenon", scope={"domain": "accounting", "population": "US listed firms"},
                        exclusions=["non-US firms"], interpretations=["Managers shift from accruals to real activities.", "Fraudulent firms exit instead of adapting."])
    pid = out["problem_id"]
    assert out["status"] == "OPEN" and out["interpretations"] == ["I1", "I2"]
    c = content(w, pid)
    assert c["interpretations"][0]["generated_by"] == "researcher" and c["chosen_interpretations"] == [] and c["promoted_to"] == []
    jsonschema.validate(c, schema.content_schema("ResearchProblem"))
    assert "ResearchProblem" not in schema.PROPOSABLE_TYPES
    with w["store"].read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM object_heads WHERE object_type IN ('Source','ResearchQuestion')").fetchone()[0] == 0


def test_input_is_validated(world):
    w = world
    for kw, code in ((dict(statement="too short"), "STATEMENT_REQUIRED"), (dict(statement=STATEMENT, kind="hunch"), "PROBLEM_KIND_UNKNOWN"),
                     (dict(statement=STATEMENT, interpretations=["The same reading.", "the  same reading."]), "INTERPRETATION_DUPLICATE")):
        with pytest.raises(KnowledgeError, match=code):
            P.new_problem(w["store"], w["token"], **kw)


def test_a_question_function_still_refuses_a_problem_id(world):
    w = world
    pid = make(w)
    with pytest.raises(KeyError, match="RESEARCH_QUESTION_NOT_FOUND"):
        build_context(w["store"], "methods", research_question_id=pid)


# ------------------------------------------------------------------------------------------------ several readings, model proposals

def test_model_proposals_are_stored_as_proposed_and_change_nothing_else(world):
    w = world
    pid = make(w, scope={"domain": "accounting"}, interpretations=["Managers shift from accruals to real activities."])
    before = content(w, pid)
    adapter = proposals("Detection raises the cost of manipulation, so fraud moves to less visible channels.", "Detected firms are replaced by new fraudsters.",
                        "Managers shift from accruals to real activities.",             # a repeat of the researcher's own reading
                        extra={"scope": {"domain": "law"}, "statement": "rewritten", "chosen": True})        # fields the model may not set are ignored
    out = P.frame(w["store"], w["token"], pid, adapter, k=4)
    assert out["status"] == "PROPOSED" and out["proposed"] == ["I2", "I3"] and out["duplicates_dropped"] == 1 and out["model"] == "model:scripted-framer"
    after = content(w, pid)
    assert after["statement"] == before["statement"] and after["scope"] == before["scope"] == {"domain": "accounting"}
    assert after["chosen_interpretations"] == [] and after["status"] == "OPEN"
    assert [i["status"] for i in after["interpretations"]] == ["STATED", "PROPOSED", "PROPOSED"]
    assert after["interpretations"][1]["generated_by"] == "model:scripted-framer" and after["interpretations"][1]["answer_kind"] == "causal"
    with w["store"].read() as conn:
        (run,) = workflow_runs.list_runs(conn, workflow="problem_framing")
    assert run["execution_status"] == "SUCCEEDED" and run["usage"]["llm_calls"] == 1 and run["skill_digest"] == P.SKILL_DIGEST
    assert run["input_revisions"][0]["object_id"] == pid
    # the model is shown what is already listed and told not to repeat it
    assert "Managers shift from accruals" in adapter.recorded_requests[0].user_prompt


def test_a_failed_or_unusable_answer_stores_nothing_and_is_registered(world):
    w = world
    pid = make(w)

    class Down:
        model_id = "down"

        def generate(self, *a, **k):
            raise ModelError("HTTP 503")

    failed = P.frame(w["store"], w["token"], pid, Down())
    assert failed["status"] == "FAILED" and failed["reason"] == "ModelError"
    bad = P.frame(w["store"], w["token"], pid, ScriptedModelAdapter(model_id="bad", default_response={"interpretations": [{"text": "short", "answer_kind": "causal"}]}))
    assert bad["status"] == "FAILED" and bad["reason"] == "invalid_output"
    assert content(w, pid)["interpretations"] == [] and w["store"].get_object(pid)["revision"] == 1
    with w["store"].read() as conn:
        rows = workflow_runs.list_runs(conn, workflow="problem_framing")
    assert [r["execution_status"] for r in rows] == ["FAILED", "FAILED"]


def test_a_refused_write_registers_a_failed_run_and_leaves_no_proposal(world):
    w = world
    pid = make(w)
    with pytest.raises(AuthorizationError):
        P.frame(w["store"], "not-a-token", pid, proposals("Detection changes which frauds are observed, not which are committed."))
    assert content(w, pid)["interpretations"] == []
    with w["store"].read() as conn:
        (run,) = workflow_runs.list_runs(conn, workflow="problem_framing")
    assert run["execution_status"] == "FAILED" and run["status_reason"].startswith("write_refused")


# ------------------------------------------------------------------------------------------------ the researcher decides

def test_choosing_is_the_researchers_act_with_a_reason(world):
    w = world
    pid = make(w, interpretations=["Managers shift from accruals to real activities.", "Fraudulent firms exit instead of adapting."])
    P.frame(w["store"], w["token"], pid, proposals("Detection changes which frauds are observed, not which are committed."))
    with pytest.raises(KnowledgeError, match="RATIONALE_REQUIRED"):
        P.choose(w["store"], w["token"], pid, ["I1"], " ")
    with pytest.raises(KnowledgeError, match="INTERPRETATION_REQUIRED"):
        P.choose(w["store"], w["token"], pid, [], "because")
    with pytest.raises(KnowledgeError, match="INTERPRETATION_UNKNOWN"):
        P.choose(w["store"], w["token"], pid, ["I9"], "because")
    out = P.choose(w["store"], w["token"], pid, ["I3", "I1"], "I want the adaptation reading and the observation reading side by side.")
    assert out["status"] == "FRAMED"
    c = content(w, pid)
    assert c["chosen_interpretations"] == ["I1", "I3"] and [i["status"] for i in c["interpretations"]] == ["CHOSEN", "NOT_CHOSEN", "CHOSEN"]
    assert c["decisions"][-1]["action"] == "choose" and c["decisions"][-1]["by"] == "researcher" and "side by side" in c["decisions"][-1]["rationale"]
    imp = register_actor(w["store"], w["token"], actor_id="importer_only", display_name="imp", roles=["importer"])
    with pytest.raises(AuthorizationError):
        P.choose(w["store"], imp["token"], pid, ["I2"], "an importer may not decide")


def test_a_new_statement_withdraws_the_choice_but_a_new_scope_keeps_it(world):
    w = world
    pid = make(w, interpretations=["Managers shift from accruals to real activities."])
    P.choose(w["store"], w["token"], pid, ["I1"], "this one")
    P.revise_problem(w["store"], w["token"], pid, rationale="the population needs narrowing", scope={"population": "US listed firms"})
    assert content(w, pid)["chosen_interpretations"] == ["I1"] and content(w, pid)["status"] == "FRAMED"
    out = P.revise_problem(w["store"], w["token"], pid, rationale="the problem is about disclosure, not manipulation as a whole",
                           statement="Fraudulent firms seem to change how they disclose after detection models become widely known.")
    c = content(w, pid)
    assert c["chosen_interpretations"] == [] and c["status"] == "OPEN" and c["interpretations"][0]["status"] == "STATED"
    assert c["decisions"][-1]["choice_withdrawn"] is True and out["revision"] == 4
    with pytest.raises(KnowledgeError, match="NOTHING_TO_REVISE"):
        P.revise_problem(w["store"], w["token"], pid, rationale="x")


def test_set_aside_and_reopen(world):
    w = world
    pid = make(w, interpretations=["Managers shift from accruals to real activities."])
    P.set_aside(w["store"], w["token"], pid, "no data for it this year")
    assert content(w, pid)["status"] == "SET_ASIDE"
    with pytest.raises(KnowledgeError, match="PROBLEM_SET_ASIDE"):
        P.choose(w["store"], w["token"], pid, ["I1"], "x")
    P.reopen(w["store"], w["token"], pid, "the data arrived")
    assert content(w, pid)["status"] == "OPEN"
    with pytest.raises(KnowledgeError, match="PROBLEM_NOT_SET_ASIDE"):
        P.reopen(w["store"], w["token"], pid, "again")


def test_two_writers_cannot_both_change_the_problem(world):
    w = world
    pid = make(w)
    stale = P.load_problem(w["store"], pid)["revision"]
    P.add_interpretation(w["store"], w["token"], pid, "Managers shift from accruals to real activities.")
    with pytest.raises(HarnessError) as e:
        P._write(w["store"], w["token"], pid, lambda c, tx: c, "test", expected_head=stale)
    assert e.value.code == "STALE_REVISION"


# ------------------------------------------------------------------------------------------------ promotion to a question, with history

def test_promotion_makes_a_question_the_researcher_writes_and_keeps_the_whole_history(world):
    w = world
    store, token = w["store"], w["token"]
    pid = make(w, interpretations=["Managers shift from accruals to real activities.", "Fraudulent firms exit instead of adapting."])
    with pytest.raises(KnowledgeError, match="INTERPRETATION_NOT_CHOSEN"):
        P.promote_to_rq(store, token, pid, "I1", "Do firms shift from accrual to real manipulation once detection models spread?", "the main reading")
    P.choose(store, token, pid, ["I1"], "the adaptation reading is the one I can test")
    with pytest.raises(KnowledgeError, match="RQ_TEXT_REQUIRED"):
        P.promote_to_rq(store, token, pid, "I1", "short", "x")
    out = P.promote_to_rq(store, token, pid, "I1", "Do firms shift from accrual to real manipulation once detection models spread?", "the main reading")
    rq = store.get_object(out["research_question"])
    assert rq["object_type"] == "ResearchQuestion" and rq["content"]["text"].startswith("Do firms shift")
    assert rq["content"]["attributes"]["promoted_from"] == {"problem": pid, "revision": out["problem_revision"], "interpretation": "I1"}
    with store.read() as conn:
        (edge,) = store.edges_from(conn, rq["object_id"], rq["revision"])
    assert (edge["relation"], edge["target_id"], edge["target_revision"], json.loads(edge["provenance"])["edge_kind"]) == ("derived_from", pid, out["problem_revision"], "promoted_from")
    c = content(w, pid)
    assert c["status"] == "PROMOTED_TO_RQ" and c["promoted_to"] == [rq["object_id"]] and c["decisions"][-1]["action"] == "promote_to_rq"
    # the history is intact: revision 1 is the problem as first stated, with no choice and no promotion
    first = content(w, pid, 1)
    assert first["status"] == "OPEN" and first["promoted_to"] == [] and first["chosen_interpretations"] == []
    assert [r["revision"] for r in P.problem_view(store, pid)["revisions"]] == [1, 2, 3]
    # a question function accepts the new question and still refuses the problem
    assert build_context(store, "methods", research_question_id=rq["object_id"]) is not None


def test_reframing_flags_the_promoted_question_for_review(world):
    w = world
    store, token = w["store"], w["token"]
    pid = make(w, interpretations=["Managers shift from accruals to real activities."])
    P.choose(store, token, pid, ["I1"], "this one")
    rid = P.promote_to_rq(store, token, pid, "I1", "Do firms shift from accrual to real manipulation once detection models spread?", "main")["research_question"]
    before = P.problem_view(store, pid)["questions_to_review"][rid]
    assert before["review_status"] == "UNASSESSED" and before["pinned_to_older_problem_revision"] == []
    out = P.revise_problem(store, token, pid, rationale="the problem is really about disclosure", statement="Fraudulent firms seem to change how they disclose after detection.")
    assert out["needs_review"] == [f"{rid}@1"]
    after = P.problem_view(store, pid)["questions_to_review"][rid]
    assert after["review_status"] == "NEEDS_REVIEW" and after["pinned_to_older_problem_revision"]


# ------------------------------------------------------------------------------------------------ with tasks, integrity

def test_a_task_can_address_a_problem(world):
    w = world
    pid = make(w)
    tid = T.start_task(w["store"], w["token"], goal="assess_claims", statement="check what rests on this", problem_id=pid, unlimited=True)["task_id"]
    task = w["store"].get_object(tid)
    assert task["content"]["statement"]["problem_id"] == pid
    with w["store"].read() as conn:
        (edge,) = w["store"].edges_from(conn, tid, task["revision"])
    assert (edge["relation"], edge["target_id"], json.loads(edge["provenance"])["edge_kind"]) == ("depends_on", pid, "addresses")
    with pytest.raises(KnowledgeError, match="PROBLEM_NOT_FOUND"):
        T.start_task(w["store"], w["token"], goal="assess_claims", statement="x", problem_id="PROB_999999")


def test_the_store_stays_consistent_with_problems_in_it(world):
    w = world
    pid = make(w, interpretations=["Managers shift from accruals to real activities."])
    P.frame(w["store"], w["token"], pid, proposals("Detection changes which frauds are observed, not which are committed."))
    P.choose(w["store"], w["token"], pid, ["I1"], "this one")
    P.promote_to_rq(w["store"], w["token"], pid, "I1", "Do firms shift from accrual to real manipulation once detection models spread?", "main")
    assert audit_project_integrity(w["store"])["status"] == "PASS"


# ------------------------------------------------------------------------------------------------ command line

def test_problem_cli_end_to_end(world, monkeypatch, capsys):
    from research_harness import cli_knowledge
    w = world
    tf = w["tmp"] / "token"
    tf.write_text(w["token"], encoding="utf-8")
    monkeypatch.setattr(cli_knowledge, "_adapter", lambda args: proposals("Detection changes which frauds are observed, not which are committed."))
    base = ["--project", "PRJ_PROB", "--home", str(w["home"])]
    auth = [*base, "--token-file", str(tf)]

    def j(*argv, rc=0):
        assert main([*map(str, argv), "--json"]) == rc, capsys.readouterr().err
        return json.loads(capsys.readouterr().out)

    pid = j("problem", "new", "--statement", STATEMENT, "--kind", "puzzle", "--scope", json.dumps({"domain": "accounting"}), "--interpretation",
            "Managers shift from accruals to real activities.", *auth)["problem_id"]
    assert j("problem", "frame", pid, "--k", 2, *auth)["proposed"] == ["I2"]
    assert j("problem", "add-interpretation", pid, "--text", "Auditors, not managers, change their behaviour.", *auth)["interpretation"] == "I3"
    assert main(["problem", "choose", pid, "--interpretations", "I1", "--rationale", " ", *auth, "--json"]) == 1
    capsys.readouterr()
    assert j("problem", "choose", pid, "--interpretations", "I1,I3", "--rationale", "two readings I can test", *auth)["status"] == "FRAMED"
    done = j("problem", "promote-to-rq", pid, "--interpretation", "I1", "--text", "Do firms shift from accrual to real manipulation after detection models spread?",
             "--rationale", "the testable one", *auth)
    assert done["research_question"].startswith("RQ_")
    assert main(["problem", "show", pid, *base]) == 0
    text = capsys.readouterr().out
    assert "[PROMOTED_TO_RQ]" in text and "* I1" in text and "promote_to_rq" in text and done["research_question"] in text
    assert j("problem", "history", pid, *base)["revisions"][-1]["revision"] == 5
