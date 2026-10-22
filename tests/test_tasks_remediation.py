"""Review remediation for M5 WP3 (2026-10-22): persistent token accounting, model-aware reuse, ECA checkpoint completeness, long-running leases.

Each test fails on the code of commit b74b580 for the reason named in its docstring and passes after the fix.
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from research_harness.core.errors import HarnessError
from research_harness.knowledge.common import KnowledgeError
from research_harness.state import workflow_runs
from research_harness.state.store import StateStore
from research_harness.tasks import budget as B
from research_harness.tasks import engine as T
from research_harness.workflows import eca
from research_harness.workflows.adapters import ModelError, ScriptedModelAdapter, adapter_identity
from tests import m3_fixture as fx

ADJ = {"passage_proposition": "As quoted.", "equivalence": "EQUIVALENT", "dropped_conditions": [], "cue_handling": [],
       "verdict": "SUPPORTED_AS_STATED", "required_qualification": [], "rationale": "ok"}


def both(model_id: str = "scripted-deepseek") -> ScriptedModelAdapter:
    extract = fx.extractor().custom_handler

    def handler(system: str, user: str):
        return dict(ADJ) if "adversarial checker" in system else extract(system, user)
    return ScriptedModelAdapter(model_id=model_id, custom_handler=handler)


class NoUsage:
    """A provider that does not report usage."""

    def __init__(self, inner):
        self.inner, self.model_id = inner, inner.model_id

    def describe(self):
        return adapter_identity(self.inner)

    def generate(self, system_prompt, user_prompt, *, expected_schema=None, temperature=0.0):
        result, _ = self.inner.generate(system_prompt, user_prompt, expected_schema=expected_schema, temperature=temperature)
        return result, SimpleNamespace(input_tokens=None, output_tokens=None, llm_calls=1, usage_source="provider")


class ErrorWithoutUsage:
    model_id = "erroring"

    def generate(self, system_prompt, user_prompt, *, expected_schema=None, temperature=0.0):
        raise ModelError("HTTP 503")


class Boom(BaseException):
    pass


class DyingAdapter:
    model_id = "dying"

    def generate(self, system_prompt, user_prompt, *, expected_schema=None, temperature=0.0):
        raise Boom()


@pytest.fixture
def world(tmp_path):
    store, token = StateStore.create(tmp_path / "home", "PRJ_REM", owner_id="researcher")
    fx.import_corpus(store, token, sources=(fx.PAPER_A,))
    return {"store": store, "token": token, "tmp": tmp_path, "home": tmp_path / "home"}


def lit_task(w, **kw):
    tid = T.start_task(w["store"], w["token"], goal="review_literature", statement="read it", params={"sources": [fx.PAPER_A], "purpose": "theory"},
                       max_tokens=kw.pop("max_tokens", 5_000_000), **kw)["task_id"]
    T.approve_task(w["store"], w["token"], tid)
    T.decide(w["store"], w["token"], tid, "S01", "yes")
    return tid


def methods_task(w, *, limit=None, name="PB_R"):
    tid = T.start_task(w["store"], w["token"], goal="evaluate_methods", statement="which method?",
                       params={"sources": [fx.PAPER_A], "playbook_id": name, "label": "p", "limit": limit, "out": str(w["tmp"] / f"exp_{name}")},
                       max_tokens=5_000_000)["task_id"]
    T.approve_task(w["store"], w["token"], tid)
    T.decide(w["store"], w["token"], tid, "S01", "yes")
    return tid


def reopened(w):
    """A new process: a fresh handle on the same files."""
    return StateStore.open(w["home"], "PRJ_REM")


# ------------------------------------------------------------------------------------------------ 1. persistent token accounting

def test_tokens_charged_for_a_provider_that_reports_no_usage_survive_a_restart(world):
    """Before: the ledger charged the reservation, but the registry held 0 tokens, so a restart forgot the spend."""
    w = world
    tid = lit_task(w)
    r = T.run_task(w["store"], w["token"], tid, adapter=NoUsage(both()))
    assert r["state"] == "COMPLETED" and r["spent"]["tokens"] > 0
    assert T.task_view(reopened(w), tid)["spent"]["tokens"] == r["spent"]["tokens"]
    with reopened(w).read() as conn:
        charges = [x for x in workflow_runs.task_rows(conn, tid, steps=False) if x["workflow"].startswith("task_call:")]
    assert charges and all(x["usage"]["usage_source"] == "reserved" for x in charges)


def test_a_failed_api_call_is_charged_and_the_charge_survives_a_restart(world):
    """Before: an API error charged the ledger (its reservation) but registered a run with no tokens."""
    w = world
    tid = lit_task(w)
    r = T.run_task(w["store"], w["token"], tid, adapter=ErrorWithoutUsage())
    assert r["state"] == "FAILED" and r["spent"]["tokens"] > 0 and r["spent"]["llm_calls"] == 1
    assert T.task_view(reopened(w), tid)["spent"] == {"llm_calls": 1, "tokens": r["spent"]["tokens"]}


def test_unknown_outcome_is_charged_once_and_inner_runs_are_not_counted_again(world):
    w = world
    tid = lit_task(w)
    with pytest.raises(Boom):
        T.run_task(w["store"], w["token"], tid, adapter=DyingAdapter())
    first = T.task_view(reopened(w), tid)["spent"]["tokens"]
    assert first > 0
    ok = both()
    r = T.run_task(w["store"], w["token"], tid, adapter=ok)
    with reopened(w).read() as conn:
        rows = workflow_runs.task_rows(conn, tid, steps=False)
        inner = [x for x in rows if not x["workflow"].startswith("task_call:")]
        charges = [x for x in rows if x["workflow"].startswith("task_call:")]
    reported = sum(int(x["usage"].get("input_tokens") or 0) + int(x["usage"].get("output_tokens") or 0) for x in inner)
    assert reported > 0                                       # the inner runs do carry usage ...
    spent = T.task_view(reopened(w), tid)["spent"]["tokens"]
    assert spent == sum(x["usage"]["charged_tokens"] for x in charges) == first + reported       # ... and it is counted once, through the charge rows
    assert r["spent"]["tokens"] == spent and len([x for x in charges if x["workflow"] == "task_call:unknown_outcome"]) == 1


# ------------------------------------------------------------------------------------------------ 2. model-aware reuse

def test_changing_the_model_makes_finished_steps_stale_even_when_the_adapter_is_created_lazily(world):
    """Before: with a lazy adapter factory a DONE step was reused without ever asking which model produced it."""
    w = world
    store, token = w["store"], w["token"]
    tid = methods_task(w)
    deepseek, gemini = both("deepseek-v4-pro"), both("gemini-2.5-pro")
    assert T.run_task(store, token, tid, adapter=deepseek)["state"] == "WAITING_FOR_RESEARCHER"
    made_by_deepseek = len(deepseek.recorded_requests)
    assert made_by_deepseek > 0

    blocked = T.run_task(store, token, tid, adapter_factory=lambda: gemini)
    assert blocked["state"] == "BLOCKED" and "STALE_STEPS: S02" in blocked["reason"] and "model" in blocked["reason"]
    assert gemini.recorded_requests == [] and blocked["executed"] == []

    redo = T.run_task(store, token, tid, adapter_factory=lambda: gemini, rerun_stale=True)
    assert redo["executed"] == ["S02", "S03", "S04"] and len(deepseek.recorded_requests) == made_by_deepseek and gemini.recorded_requests
    view = {s["step_id"]: s for s in T.task_view(store, tid)["steps"]}
    assert view["S02"]["produced_by"] == "gemini-2.5-pro" and view["S04"]["produced_by"] == "gemini-2.5-pro"
    assert view["S05"]["status"] == "AWAITING_DECISION"


def test_a_downstream_step_is_stale_when_its_upstream_model_changed(world):
    w = world
    store, token = w["store"], w["token"]
    tid = methods_task(w)
    T.run_task(store, token, tid, adapter=both("deepseek-v4-pro"))
    content = T.load_task(store, tid)["content"]
    status = {sid: a["status"] for sid, a in T._analyze(store, tid, content, adapter_identity(both("gemini-2.5-pro"))).items()}
    assert status["S02"] == "STALE" and status["S03"] in ("STALE", "WAITING_ON_UPSTREAM") and status["S04"] != "DONE"
    same = {sid: a["status"] for sid, a in T._analyze(store, tid, content, adapter_identity(both("deepseek-v4-pro"))).items()}
    assert same["S02"] == same["S03"] == same["S04"] == "DONE"


def test_a_change_of_adapter_configuration_makes_the_step_stale(world):
    w = world
    tid = lit_task(w)
    T.run_task(w["store"], w["token"], tid, adapter=both("deepseek-v4-pro"))
    content = T.load_task(w["store"], tid)["content"]
    ident = adapter_identity(both("deepseek-v4-pro"))
    assert T._analyze(w["store"], tid, content, ident)["S02"]["status"] == "DONE"
    assert T._analyze(w["store"], tid, content, {**ident, "max_output_tokens": 8000})["S02"]["status"] == "STALE"
    assert T._analyze(w["store"], tid, content, {**ident, "base_url": "https://other.example"})["S02"]["status"] == "STALE"


def test_recorded_outputs_can_be_accepted_explicitly_without_an_adapter(world):
    w = world
    store, token = w["store"], w["token"]
    tid = methods_task(w)
    T.run_task(store, token, tid, adapter=both("deepseek-v4-pro"))
    nothing = T.run_task(store, token, tid, adapter_factory=lambda: (_ for _ in ()).throw(RuntimeError("no API key")))
    assert nothing["state"] == "BLOCKED" and nothing["reason"].startswith("ADAPTER_REQUIRED") and "no API key" in nothing["reason"]
    accepted = T.run_task(store, token, tid, accept_recorded_models=True)
    assert accepted["state"] == "WAITING_FOR_RESEARCHER" and accepted["executed"] == [] and "S05" in accepted["reason"]


# ------------------------------------------------------------------------------------------------ 3. ECA checkpoint completeness

def test_the_adjudication_checkpoint_cannot_close_while_high_statements_have_no_proposal(world):
    """Before: only unconfirmed proposals blocked it, so run_pending with a limit left HIGH statements unexamined and the checkpoint still closed."""
    w = world
    store, token = w["store"], w["token"]
    tid = methods_task(w, limit=1)
    r = T.run_task(store, token, tid, adapter=both())
    assert r["state"] == "WAITING_FOR_RESEARCHER" and "S05" in r["reason"]
    # one proposal exists (awaiting you), the other HIGH statements have none: the two are reported apart
    with pytest.raises(KnowledgeError) as e:
        T.decide(store, token, tid, "S05", "done")
    assert e.value.code == "DECISION_PRECONDITION" and len(e.value.details["awaiting_confirmation"]) == 1 and len(e.value.details["not_adjudicated"]) == 2
    sheet = eca.review_sheet(store)
    eca.confirm_batch(store, token, [{"adjudication_id": x["adjudication_id"], "verdict": "SUPPORTED_AS_STATED", "rationale": "reads correctly"} for x in sheet])
    with pytest.raises(KnowledgeError) as e2:
        T.decide(store, token, tid, "S05", "done")
    assert e2.value.details["awaiting_confirmation"] == [] and len(e2.value.details["not_adjudicated"]) == 2
    # the researcher adjudicates the rest, confirms it, and the checkpoint closes
    eca.run_pending(store, token, both(), only_objects={"PB_R"})
    eca.confirm_batch(store, token, [{"adjudication_id": x["adjudication_id"], "verdict": "SUPPORTED_AS_STATED", "rationale": "reads correctly"} for x in eca.review_sheet(store)])
    assert T.decide(store, token, tid, "S05", "every HIGH statement is adjudicated and confirmed")["already_decided"] is False


def test_an_unadjudicated_statement_can_be_set_aside_only_with_a_recorded_reason(world):
    w = world
    store, token = w["store"], w["token"]
    tid = methods_task(w, limit=1)
    T.run_task(store, token, tid, adapter=both())
    eca.confirm_batch(store, token, [{"adjudication_id": x["adjudication_id"], "verdict": "SUPPORTED_AS_STATED", "rationale": "reads the passage correctly"} for x in eca.review_sheet(store)])
    with pytest.raises(KnowledgeError, match="DECISION_PRECONDITION"):
        T.decide(store, token, tid, "S05", "done", exempt={"PB_R#E02": " "})                     # a blank reason is no reason
    pending = eca.completeness(store, {"PB_R"})["not_adjudicated"]
    exempt = {f"{p['object_id']}#{p['element']}": "this element restates a table caption; not used in any conclusion" for p in pending}
    out = T.decide(store, token, tid, "S05", "done", exempt=exempt)
    assert out["already_decided"] is False
    decision = next(c for c in T.load_task(store, tid)["content"]["checkpoints"] if c["step_id"] == "S05")["decision"]
    assert set(decision["exempt"]) == set(exempt) and decision["unadjudicated_set_aside"] == len(exempt)


def test_completeness_reports_each_high_statement_once(world):
    w = world
    store, token = w["store"], w["token"]
    tid = methods_task(w, limit=1)
    T.run_task(store, token, tid, adapter=both())
    c = eca.completeness(store, {"PB_R"})
    assert c["high_total"] == 3 and len(c["awaiting_confirmation"]) == 1 and len(c["not_adjudicated"]) == 2 and c["confirmed"] == 0
    assert {x["element"] for x in c["awaiting_confirmation"]} .isdisjoint({x["element"] for x in c["not_adjudicated"]})


# ------------------------------------------------------------------------------------------------ 4. long-running lease

class SlowAdapter:
    """Holds the first call longer than the lease lasts; `during` runs while it is in progress."""

    def __init__(self, inner, during, first=1.5, after=0.8):
        self.inner, self.during, self.first, self.after, self.calls = inner, during, first, after, 0
        self.model_id = inner.model_id

    def describe(self):
        return adapter_identity(self.inner)

    def generate(self, system_prompt, user_prompt, *, expected_schema=None, temperature=0.0):
        self.calls += 1
        if self.calls == 1:
            time.sleep(self.first)
            self.during()
            time.sleep(self.after)
        return self.inner.generate(system_prompt, user_prompt, expected_schema=expected_schema, temperature=temperature)


def test_a_step_longer_than_the_lease_keeps_its_lease_and_no_second_runner_starts_it(world):
    """Before: the lease was renewed only between steps, so it lapsed mid-step and a second runner could take the task and run the same step."""
    w = world
    store, token = w["store"], w["token"]
    tid = lit_task(w)
    seen: dict = {}

    def second_runner():
        try:
            T.run_task(store, token, tid, adapter=both("second"))
            seen["second"] = "RAN"
        except KnowledgeError as exc:
            seen["second"] = exc.code

    slow = SlowAdapter(both("first"), second_runner)
    r = T.run_task(store, token, tid, adapter=slow, lease_ttl=1.0)
    assert seen["second"] == "TASK_LEASED"
    assert r["state"] == "COMPLETED" and r["executed"] == ["S02"]
    assert not [t for t in threading.enumerate() if t.name.startswith("task-lease-")]          # the heartbeat stopped with the run
    assert T.load_task(store, tid)["content"]["lease"] is None


def test_a_runner_that_lost_its_lease_does_not_overwrite_the_task_or_register_a_success(world):
    """Before: the step was registered SUCCEEDED and the final write failed with STALE_REVISION, leaving the thief's task state clobbered or an error."""
    w = world
    store, token = w["store"], w["token"]
    tid = lit_task(w)

    def steal():
        thief = {"holder": "run-thief", "acquired_at": "2000-01-01T00:00:00+00:00", "expires_at": "2999-01-01T00:00:00+00:00"}
        for _ in range(50):
            cur = T.load_task(store, tid)
            try:
                T._write(store, token, tid, {**cur["content"], "lease": thief}, "test_steal", expected_head=cur["revision"])
                return
            except HarnessError:
                time.sleep(0.05)
        raise AssertionError("could not take the lease")

    r = T.run_task(store, token, tid, adapter=SlowAdapter(both("first"), steal, first=0.5, after=0.3), lease_ttl=1.0)
    assert r["state"] == "LEASE_LOST" and "S02" in r["reason"]
    assert T.load_task(store, tid)["content"]["lease"]["holder"] == "run-thief"                  # the task was not touched by the loser
    with store.read() as conn:
        steps = workflow_runs.task_rows(conn, tid, steps=True)
    assert [x["execution_status"] for x in steps] == ["FAILED"] and steps[0]["status_reason"].startswith("lease_lost")
    assert T.task_view(store, tid)["steps"][1]["status"] == "FAILED"                              # not reusable
