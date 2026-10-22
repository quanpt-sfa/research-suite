"""M5 WP3: research tasks. A goal becomes a recorded plan that runs existing capabilities and stops at the researcher's decisions."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest

from research_harness.core.errors import HarnessError
from research_harness.knowledge import corpus
from research_harness.knowledge.common import KnowledgeError
from research_harness.protocols import schema
from research_harness.state import workflow_runs
from research_harness.state.schema import upgrade_schema
from research_harness.state.store import StateStore
from research_harness.tasks import budget as B
from research_harness.tasks import engine as T
from research_harness.validation.integrity import audit_project_integrity
from research_harness.workflows import eca
from research_harness.workflows.adapters import ModelError, ScriptedModelAdapter
from tests import m3_fixture as fx

ADJ = {"passage_proposition": "As quoted.", "equivalence": "EQUIVALENT", "dropped_conditions": [], "cue_handling": [],
       "verdict": "SUPPORTED_AS_STATED", "required_qualification": [], "rationale": "ok"}


def both(model_id: str = "scripted-both") -> ScriptedModelAdapter:
    extract = fx.extractor().custom_handler

    def handler(system: str, user: str):
        return dict(ADJ) if "adversarial checker" in system else extract(system, user)
    return ScriptedModelAdapter(model_id=model_id, custom_handler=handler)


class Boom(BaseException):
    """A process that dies mid-call (not an Exception: nothing in the engine may swallow it)."""


class DyingAdapter:
    model_id = "dying"

    def generate(self, system_prompt, user_prompt, *, expected_schema=None, temperature=0.0):
        raise Boom()


class FailingAdapter:
    model_id = "failing"

    def generate(self, system_prompt, user_prompt, *, expected_schema=None, temperature=0.0):
        raise ModelError("the provider is down")


@pytest.fixture
def world(tmp_path):
    store, token = StateStore.create(tmp_path / "home", "PRJ_TASK", owner_id="researcher")
    fx.import_corpus(store, token, sources=(fx.PAPER_A,))
    return {"store": store, "token": token, "tmp": tmp_path}


def lit_task(w, **kw):
    kw.setdefault("max_tokens", 5_000_000)
    return T.start_task(w["store"], w["token"], goal="review_literature", statement="What do the stored sources say about restatement prediction?",
                        params={"sources": [fx.PAPER_A], "purpose": "theory"}, **kw)["task_id"]


def steps(w, tid):
    return {s["step_id"]: s["status"] for s in T.task_view(w["store"], tid)["steps"]}


# ------------------------------------------------------------------------------------------------ creation and plan

def test_task_starts_without_question_manuscript_methods_or_results(world):
    w = world
    out = T.start_task(w["store"], w["token"], goal="review_literature", statement="Read what is stored", params={"sources": [fx.PAPER_A]}, max_tokens=1_000_000)
    assert out["state"] == "PLANNED" and out["checkpoints"] == ["S01"]
    view = T.task_view(w["store"], out["task_id"])
    assert [s["step_id"] for s in view["steps"]] == ["S01", "S02"] and view["mandatory_checkpoints"] == ["S01"]
    est = view["steps"][1]["estimate"]
    assert est["llm_calls"] == 1 and est["basis"].startswith("heuristic") and view["spent"] == {"llm_calls": 0, "tokens": 0}
    assert w["store"].get_object(out["task_id"])["object_type"] == "ResearchTask"
    assert w["store"].get_object(out["task_id"])["content"]["statement"] == {"text": "Read what is stored"}


def test_unavailable_goals_say_what_is_missing(world):
    with pytest.raises(KnowledgeError) as e:
        T.start_task(world["store"], world["token"], goal="discover_contribution", statement="a phenomenon")
    assert e.value.code == "GOAL_NOT_AVAILABLE" and "candidate generation" in e.value.message and "WP7" in e.value.message
    with pytest.raises(KnowledgeError, match="GOAL_UNKNOWN"):
        T.start_task(world["store"], world["token"], goal="make_it_publishable", statement="x")
    with pytest.raises(KnowledgeError, match="GOAL_PARAMS_MISSING"):
        T.start_task(world["store"], world["token"], goal="review_literature", statement="x")
    with pytest.raises(KnowledgeError, match="STATEMENT_REQUIRED"):
        T.start_task(world["store"], world["token"], goal="assess_claims", statement=" ")


def test_task_object_validates_against_its_schema(world):
    tid = lit_task(world)
    jsonschema.validate(world["store"].get_object(tid)["content"], schema.content_schema("ResearchTask"))
    assert "ResearchTask" not in schema.PROPOSABLE_TYPES        # written by the engine, never proposed


def test_run_needs_approval_and_a_budget_decision(world):
    w = world
    tid = lit_task(w)
    with pytest.raises(KnowledgeError, match="TASK_NOT_APPROVED"):
        T.run_task(w["store"], w["token"], tid, adapter=both())
    nob = T.start_task(w["store"], w["token"], goal="assess_claims", statement="x")["task_id"]
    with pytest.raises(KnowledgeError, match="BUDGET_REQUIRED"):
        T.approve_task(w["store"], w["token"], nob)
    unlimited = T.start_task(w["store"], w["token"], goal="assess_claims", statement="x", unlimited=True)["task_id"]
    assert T.approve_task(w["store"], w["token"], unlimited)["state"] == "APPROVED"


# ------------------------------------------------------------------------------------------------ running, checkpoints, reuse

def test_task_stops_at_the_researchers_decision_then_runs(world):
    w = world
    store, token = w["store"], w["token"]
    tid = lit_task(w)
    T.approve_task(store, token, tid)
    adapter = both()
    first = T.run_task(store, token, tid, adapter=adapter)
    assert first["state"] == "WAITING_FOR_RESEARCHER" and first["executed"] == [] and adapter.recorded_requests == []      # nothing ran past S01
    with pytest.raises(KnowledgeError, match="RATIONALE_REQUIRED"):
        T.decide(store, token, tid, "S01", " ")
    with pytest.raises(KnowledgeError, match="STEP_NOT_A_CHECKPOINT"):
        T.decide(store, token, tid, "S02", "go")
    d = T.decide(store, token, tid, "S01", "These are the papers I mean to read.")
    assert d["by"] == "researcher" and d["already_decided"] is False
    assert T.decide(store, token, tid, "S01", "again")["already_decided"] is True          # idempotent, no second decision
    done = T.run_task(store, token, tid, adapter=adapter)
    assert done["state"] == "COMPLETED" and done["executed"] == ["S02"] and len(adapter.recorded_requests) == 1
    assert steps(w, tid) == {"S01": "DECIDED", "S02": "DONE"}
    # the runs the step made belong to the task; the step summary links to them
    with store.read() as conn:
        mine = workflow_runs.task_rows(conn, tid)
    made = [r for r in mine if not r["workflow"].startswith("task_step:")]
    summary = next(r for r in mine if r["workflow"] == "task_step:literature_review")
    assert made and all(r["task_step_id"] == "S02" for r in mine) and set(summary["upstream_run_ids"]) == {r["run_id"] for r in made}
    assert summary["usage"]["llm_calls"] == 1 and T.task_view(store, tid)["spent"]["llm_calls"] == 1
    with pytest.raises(KnowledgeError, match="TASK_STATE_INVALID"):
        T.run_task(store, token, tid, adapter=adapter)


def test_a_failed_step_is_recorded_and_retry_does_not_reuse_it(world):
    w = world
    store, token = w["store"], w["token"]
    tid = lit_task(w)
    T.approve_task(store, token, tid)
    T.decide(store, token, tid, "S01", "yes")
    bad = T.run_task(store, token, tid, adapter=FailingAdapter())
    assert bad["state"] == "FAILED" and bad["reason"].startswith("S02:") and steps(w, tid)["S02"] == "FAILED"
    ok = T.retry_task(store, token, tid, "S02", adapter=both())
    assert ok["state"] == "COMPLETED" and ok["executed"] == ["S02"]
    with store.read() as conn:
        rows = [r for r in workflow_runs.task_rows(conn, tid, steps=True)]
    assert [r["execution_status"] for r in rows] == ["SUCCEEDED", "FAILED"]
    with pytest.raises(KnowledgeError, match="STEP_NOT_FAILED"):
        T.retry_task(store, token, tid, "S02", adapter=both())


def test_changed_source_makes_the_step_stale_until_the_researcher_reruns_it(world):
    w = world
    store, token = w["store"], w["token"]
    tid = lit_task(w)
    T.revise_task(store, token, tid, add_checkpoint_after="S02", checkpoint_title="Read the review before going on")
    T.approve_task(store, token, tid)
    T.run_task(store, token, tid, adapter=both())                       # waits at S01
    T.decide(store, token, tid, "S01", "yes")
    adapter = both()
    r = T.run_task(store, token, tid, adapter=adapter)
    assert r["state"] == "WAITING_FOR_RESEARCHER" and r["executed"] == ["S02"] and "S02c" in r["reason"]
    original = corpus.source_text(store, fx.PAPER_A)[1]
    corpus.import_source(store, token, citation_key="synth2025", text=original + "\n\nAppendix. A paragraph added in a corrected version.", title="synth2025")
    assert steps(w, tid)["S02"] == "STALE"
    blocked = T.run_task(store, token, tid, adapter=adapter)
    assert blocked["state"] == "BLOCKED" and blocked["reason"].startswith("STALE_STEPS: S02") and blocked["executed"] == []
    again = T.run_task(store, token, tid, adapter=adapter, rerun_stale=True)
    assert again["executed"] == ["S02"] and steps(w, tid)["S02"] == "DONE"
    # the decision on S01 stayed valid: what it decided (the source list) did not change, so it was not asked again
    assert steps(w, tid)["S01"] == "DECIDED"


def test_a_decision_stops_applying_when_what_it_decided_changes(world):
    w = world
    store, token = w["store"], w["token"]
    tid = lit_task(w)
    T.approve_task(store, token, tid)
    T.decide(store, token, tid, "S01", "yes")
    T.revise_task(store, token, tid, set_inputs={"S01.sources": [fx.PAPER_A, fx.PAPER_B]})
    assert steps(w, tid)["S01"] == "STALE_DECISION"


def test_cancel_keeps_the_runs_already_registered(world):
    w = world
    store, token = w["store"], w["token"]
    tid = lit_task(w)
    T.approve_task(store, token, tid)
    T.decide(store, token, tid, "S01", "yes")
    T.run_task(store, token, tid, adapter=both(), until="S01")        # nothing before S01... pauses before S02
    assert T.task_view(store, tid)["state"] in ("APPROVED", "WAITING_FOR_RESEARCHER")
    assert T.cancel_task(store, token, tid, "no longer needed")["state"] == "CANCELLED"
    with pytest.raises(KnowledgeError, match="TASK_STATE_INVALID"):
        T.run_task(store, token, tid, adapter=both())


# ------------------------------------------------------------------------------------------------ plan changes

def test_revision_cannot_drop_a_mandatory_checkpoint_or_a_step_others_need(world):
    w = world
    tid = lit_task(w)
    with pytest.raises(KnowledgeError, match="CHECKPOINT_REQUIRED"):
        T.revise_task(w["store"], w["token"], tid, drop=("S01",))
    with pytest.raises(KnowledgeError, match="STEP_UNKNOWN"):
        T.revise_task(w["store"], w["token"], tid, drop=("S99",))
    out = T.revise_task(w["store"], w["token"], tid, drop=("S02",))
    assert out["steps"] == ["S01"] and out["state"] == "PLANNED"
    ev = T.start_task(w["store"], w["token"], goal="evaluate_methods", statement="x", params={"sources": [fx.PAPER_A], "playbook_id": "PB_Z", "label": "z"})["task_id"]
    with pytest.raises(KnowledgeError, match="STEP_HAS_DEPENDENTS"):
        T.revise_task(w["store"], w["token"], ev, drop=("S03",))


def test_replan_keeps_decisions_whose_fingerprint_is_unchanged(world):
    w = world
    store, token = w["store"], w["token"]
    tid = lit_task(w)
    T.approve_task(store, token, tid)
    T.decide(store, token, tid, "S01", "yes")
    out = T.replan_task(store, token, tid)
    assert out["state"] == "PLANNED" and out["diff"] == {"added": [], "removed": [], "changed": []}
    assert steps(w, tid)["S01"] == "DECIDED"


# ------------------------------------------------------------------------------------------------ budget and crash recovery

def test_budget_blocks_a_step_before_any_call_is_made(world):
    w = world
    store, token = w["store"], w["token"]
    tid = lit_task(w, max_tokens=300)
    T.approve_task(store, token, tid)
    T.decide(store, token, tid, "S01", "yes")
    adapter = both()
    r = T.run_task(store, token, tid, adapter=adapter)
    assert r["state"] == "BLOCKED" and r["reason"].startswith("BUDGET_EXCEEDED: S02") and adapter.recorded_requests == []


def test_every_call_is_capped_by_the_remaining_budget(world):
    seen = {}

    class Adapter:
        model_id = "capped"
        max_output_tokens = 64000

        def generate(self, system_prompt, user_prompt, *, expected_schema=None, temperature=0.0):
            seen["cap"] = self.max_output_tokens
            return {"ok": True}, SimpleNamespace(input_tokens=None, output_tokens=None)        # the provider did not report usage

    store = world["store"]
    ledger = B.Ledger(max_tokens=3000, max_calls=None)
    wrapped = B.BudgetedAdapter(Adapter(), ledger, store, "TASK_X", "S01")
    wrapped.generate("system " * 20, "user " * 20)
    assert seen["cap"] < 3000 and Adapter.max_output_tokens == 64000 and wrapped.max_output_tokens == 64000    # capped for the call, restored after
    assert ledger.spent_tokens > seen["cap"] and ledger.spent_calls == 1       # unreported usage is charged at the reservation
    with store.read() as conn:
        assert workflow_runs.task_spent(conn, "TASK_X") == {"llm_calls": 1, "tokens": ledger.spent_tokens}      # and it is on record
    with pytest.raises(B.BudgetExceeded):
        for _ in range(5):
            wrapped.generate("system " * 20, "user " * 20)                        # the budget runs out: the call is refused, not made
    assert ledger.blocked_by == "max_tokens"
    calls = B.Ledger(max_tokens=None, max_calls=1)
    w2 = B.BudgetedAdapter(Adapter(), calls, store, "TASK_Y", "S01")
    w2.generate("s", "u")
    with pytest.raises(B.BudgetExceeded, match="model calls"):
        w2.generate("s", "u")


def test_a_call_that_died_unanswered_is_charged_and_the_step_runs_again(world):
    w = world
    store, token = w["store"], w["token"]
    tid = lit_task(w)
    T.approve_task(store, token, tid)
    T.decide(store, token, tid, "S01", "yes")
    with pytest.raises(Boom):
        T.run_task(store, token, tid, adapter=DyingAdapter())
    assert len(B.outstanding_intents(store, tid)) == 1                  # the intent was written before the call
    c = T.load_task(store, tid)["content"]
    assert c["lease"] is None and c["state"] == "RUNNING"               # the lease was released; a killed process would leave it to expire
    r = T.run_task(store, token, tid, adapter=both())
    assert r["state"] == "COMPLETED", r["reason"]
    assert len(r["recovered_unknown_calls"]) == 1 and r["state"] == "COMPLETED" and r["executed"] == ["S02"]
    with store.read() as conn:
        unknown = [x for x in workflow_runs.task_rows(conn, tid, steps=False) if x["workflow"] == "task_call:unknown_outcome"]
    assert len(unknown) == 1 and unknown[0]["usage"]["usage_source"] == "reserved" and unknown[0]["usage"]["input_tokens"] > 0
    assert T.task_view(store, tid)["spent"]["tokens"] >= unknown[0]["usage"]["input_tokens"]
    assert B.outstanding_intents(store, tid) == []


def test_a_second_runner_cannot_take_a_held_lease(world):
    w = world
    store, token = w["store"], w["token"]
    tid = lit_task(w)
    T.approve_task(store, token, tid)
    task = T.load_task(store, tid)
    future = {"holder": "run-other", "acquired_at": "2000-01-01T00:00:00+00:00", "expires_at": "2999-01-01T00:00:00+00:00"}
    T._write(store, token, tid, T._transition(task["content"], "RUNNING", None, lease=future), "test_lease", expected_head=task["revision"])
    with pytest.raises(KnowledgeError, match="TASK_LEASED"):
        T.run_task(store, token, tid, adapter=both())
    assert T.run_task(store, token, tid, adapter=both(), break_lease=True)["state"] == "WAITING_FOR_RESEARCHER"      # the owner took it over


def test_an_expired_lease_is_taken_over(world):
    w = world
    store, token = w["store"], w["token"]
    tid = lit_task(w)
    T.approve_task(store, token, tid)
    task = T.load_task(store, tid)
    old = {"holder": "run-dead", "acquired_at": "2000-01-01T00:00:00+00:00", "expires_at": "2000-01-01T00:30:00+00:00"}
    T._write(store, token, tid, T._transition(task["content"], "RUNNING", None, lease=old), "test_lease", expected_head=task["revision"])
    assert T.run_task(store, token, tid, adapter=both())["state"] == "WAITING_FOR_RESEARCHER"


def test_two_writers_cannot_both_change_the_task(world):
    w = world
    tid = lit_task(w)
    task = T.load_task(w["store"], tid)
    T._write(w["store"], w["token"], tid, T._transition(task["content"], "APPROVED"), "a", expected_head=task["revision"])
    with pytest.raises(HarnessError) as e:
        T._write(w["store"], w["token"], tid, T._transition(task["content"], "CANCELLED", "b"), "b", expected_head=task["revision"])
    assert e.value.code == "STALE_REVISION"


# ------------------------------------------------------------------------------------------------ end to end

def test_evaluate_methods_runs_to_a_readable_export_through_two_checkpoints(world):
    w = world
    store, token = w["store"], w["token"]
    tid = T.start_task(store, token, goal="evaluate_methods", statement="Which method does the stored paper use, and is it read correctly?",
                       params={"sources": [fx.PAPER_A], "playbook_id": "PB_TASK", "label": "Playbook from the task",
                               "out": str(w["tmp"] / "task_export")}, max_tokens=5_000_000)["task_id"]
    T.approve_task(store, token, tid)
    adapter = both()
    assert T.run_task(store, token, tid, adapter=adapter)["state"] == "WAITING_FOR_RESEARCHER"
    T.decide(store, token, tid, "S01", "The stored paper is the one I mean.")
    r = T.run_task(store, token, tid, adapter=adapter)
    assert r["executed"] == ["S02", "S03", "S04"] and r["state"] == "WAITING_FOR_RESEARCHER" and "S05" in r["reason"]
    # the adjudications are proposals: the checkpoint cannot be cleared until the researcher has confirmed each one
    with pytest.raises(KnowledgeError, match="DECISION_PRECONDITION"):
        T.decide(store, token, tid, "S05", "looks fine")
    sheet = eca.review_sheet(store)
    assert sheet
    eca.confirm_batch(store, token, [{"adjudication_id": x["adjudication_id"], "verdict": "SUPPORTED_AS_STATED", "rationale": "reads the passage correctly"}
                                     for x in sheet])
    T.decide(store, token, tid, "S05", "Every proposal was confirmed individually.")
    final = T.run_task(store, token, tid, adapter=adapter)
    assert final["state"] == "COMPLETED" and final["executed"] == ["S06"]
    assert (w["tmp"] / "task_export" / "playbook.md").is_file() and (w["tmp"] / "task_export" / "manifest.json").is_file()
    assert set(steps(w, tid).values()) == {"DONE", "DECIDED"}
    view = T.task_view(store, tid)
    assert view["spent"]["llm_calls"] == len(adapter.recorded_requests) and view["state"] == "COMPLETED"
    assert audit_project_integrity(store)["status"] == "PASS"

    # a follow-up task starts from what this one produced, by value, and records which revision it continues
    child = T.continue_task(store, token, tid, goal="assess_claims", statement="Check the claims that rest on that playbook",
                            params={"objects": [{"$parent_step": "S03", "key": "playbook_id"}]}, max_tokens=1_000_000)
    c = store.get_object(child["task_id"])
    assert c["content"]["params"] == {"objects": ["PB_TASK"]} and c["content"]["parent_task"]["task_id"] == tid
    assert c["content"]["continued_from"]["steps"] == ["S03"]
    with store.read() as conn:
        edges = store.edges_from(conn, child["task_id"], c["revision"])
    assert [(e["relation"], e["target_id"], json.loads(e["provenance"])["edge_kind"]) for e in edges] == [("derived_from", tid, "continues")]
    with pytest.raises(KnowledgeError, match="PARENT_STEP_NOT_DONE"):
        T.continue_task(store, token, tid, goal="assess_claims", statement="x", params={"objects": [{"$parent_step": "S99", "key": "x"}]})


# ------------------------------------------------------------------------------------------------ schema

def test_a_store_without_task_columns_must_be_upgraded_first(world):
    w = world
    store, token = w["store"], w["token"]
    conn = store.connect()
    try:
        conn.execute("DROP INDEX workflow_runs_task")
        conn.execute("ALTER TABLE workflow_runs DROP COLUMN task_id")
        conn.execute("ALTER TABLE workflow_runs DROP COLUMN task_step_id")
        conn.execute("UPDATE meta SET value='9.0.3' WHERE key='db_schema_version'")
    finally:
        conn.close()
    with pytest.raises(KnowledgeError, match="SCHEMA_UPGRADE_REQUIRED"):
        T.start_task(store, token, goal="assess_claims", statement="x")
    res = upgrade_schema(store, token)
    assert (res["status"], res["from_version"], res["to_version"]) == ("UPGRADED", "9.0.3", "9.0.4")
    with store.read() as c:
        assert workflow_runs.has_task_columns(c)
    assert T.start_task(store, token, goal="assess_claims", statement="x")["state"] == "PLANNED"
    assert upgrade_schema(store, token)["status"] == "NOOP"


# ------------------------------------------------------------------------------------------------ command line

def test_task_cli_round_trip(world, monkeypatch, capsys):
    from research_harness import cli_knowledge
    from research_harness.cli import main
    w = world
    tf = w["tmp"] / "token"
    tf.write_text(w["token"], encoding="utf-8")
    monkeypatch.setattr(cli_knowledge, "_adapter", lambda args: both())
    base = ["--project", "PRJ_TASK", "--home", str(w["tmp"] / "home")]
    auth = [*base, "--token-file", str(tf)]

    def j(*argv, rc=0):
        assert main([*map(str, argv), "--json"]) == rc, capsys.readouterr().err
        return json.loads(capsys.readouterr().out)

    goals = {g["goal"]: g for g in j("task", "goals")}
    assert goals["review_literature"]["available"] and not goals["discover_contribution"]["available"]
    started = j("task", "start", "--goal", "review_literature", "--statement", "read the stored paper", "--params",
                json.dumps({"sources": [fx.PAPER_A], "purpose": "theory"}), "--budget-tokens", 5000000, *auth)
    tid = started["task_id"]
    assert j("task", "approve", tid, *auth)["state"] == "APPROVED"
    assert j("task", "run", tid, *auth)["state"] == "WAITING_FOR_RESEARCHER"
    assert main(["task", "decide", tid, "S01", "--rationale", " ", *auth, "--json"]) == 1
    capsys.readouterr()
    assert j("task", "decide", tid, "S01", "--rationale", "yes, these", *auth)["already_decided"] is False
    done = j("task", "run", tid, *auth)
    assert done["state"] == "COMPLETED" and done["executed"] == ["S02"]
    view = j("task", "status", tid, *base)
    assert [s["status"] for s in view["steps"]] == ["DECIDED", "DONE"]
    assert main(["task", "status", tid, *base]) == 0
    text = capsys.readouterr().out
    assert "COMPLETED" in text and "DECIDE reading_list (mandatory)" in text
    assert main(["task", "start", "--goal", "discover_contribution", "--statement", "x", *auth]) == 1
    assert "GOAL_NOT_AVAILABLE" in capsys.readouterr().err
