"""`research task ...` (M5 WP3). The commands only call the engine; everything a task does is recorded in the store and the run registry."""
from __future__ import annotations

import argparse
import json
from typing import Any, Callable, Optional


def _task_text(v: dict) -> None:
    sp, cap = v["spent"], v["budget"]
    print(f"{v['task_id']} r{v['revision']} [{v['state']}] {v['goal']}: {v['statement']['text']}")
    if v.get("state_reason"):
        print(f"  reason: {v['state_reason']}")
    print(f"  budget: tokens {cap.get('max_tokens') or ('unlimited' if cap.get('unlimited') else '-')}, model calls {cap.get('max_llm_calls') or '-'}"
          f" · spent {sp['tokens']} tokens, {sp['llm_calls']} calls")
    for s in v["steps"]:
        est = s["estimate"]
        cost = "" if est.get("llm_calls") is None else f" ~{est['llm_calls']} call(s)"
        mark = " (mandatory)" if s["step_id"] in v["mandatory_checkpoints"] else ""
        what = (s["capability"] + (f" [{s['produced_by']}]" if s.get("produced_by") else "")) if s["kind"] == "run" else f"DECIDE {s['decision']}{mark}"
        extra = f" — {s['stale_reason']}" if s.get("stale_reason") else (f" — {s['error']}" if s.get("error") else "")
        print(f"  {s['step_id']:<5}{s['status']:<20}{what}: {s['title']}{cost}{extra}")
    rem = v["remaining_estimate"]
    print(f"  still to do (estimate): ~{rem['llm_calls']} call(s), ~{rem['tokens']} tokens; {rem['steps_with_unknown_estimate']} step(s) unknown until upstream runs")


def _run_text(o: dict) -> None:
    print(f"{o['task_id']}: {o.get('state')}" + (f" — {o['reason']}" if o.get("reason") else ""))
    unknown = o.get("recovered_unknown_calls") or []
    print(f"  ran {', '.join(o['executed']) or 'nothing'}; already done {', '.join(o['reused']) or 'nothing'}"
          + (f"; {len(unknown)} call(s) of an earlier run had no answer and were charged at their reservation" if unknown else ""))


def _exempt(items: Optional[list[str]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for it in items or []:
        key, sep, reason = it.partition("=")
        if not sep or "#" not in key:
            raise ValueError(f"--exempt {it!r}: use OBJECT#ELEMENT=reason")
        out[key] = reason
    return out


def _set_pairs(items: Optional[list[str]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for it in items or []:
        key, sep, raw = it.partition("=")
        if not sep:
            raise ValueError(f"--set {it!r}: use STEP.param=value (value is JSON, or text)")
        try:
            out[key] = json.loads(raw)
        except ValueError:
            out[key] = raw
    return out


def register(sub: Any) -> None:
    from research_harness import cli_knowledge as K
    from research_harness.tasks import engine as T
    from research_harness.tasks.goals import GOALS

    p = sub.add_parser("task", help="Goal-driven tasks: a recorded plan that stops at every decision that is yours")
    s = p.add_subparsers(dest="task_cmd", required=True)

    def tok(a: argparse.Namespace) -> Optional[str]:
        return K._helpers()._resolve_token(a)

    def run_kwargs(a: argparse.Namespace) -> dict[str, Any]:
        return {"adapter_factory": lambda: K._adapter(a), "until": getattr(a, "until", None), "rerun_stale": getattr(a, "rerun_stale", False),
                "break_lease": getattr(a, "break_lease", False), "accept_recorded_models": getattr(a, "accept_recorded_models", False)}

    def add(name: str, help_: str, fn: Callable[[Any, argparse.Namespace], Any], text: Optional[Callable[[Any], None]] = None, *,
            token: bool = True, model: bool = False, store: bool = True) -> argparse.ArgumentParser:
        q = s.add_parser(name, help=help_)
        K._common(q, token=token)
        if model:
            q.add_argument("--model")
        q.set_defaults(func=lambda a, fn=fn, text=text, store=store: K._run(a, lambda: fn(K._store(a) if store else None, a), text))
        return q

    def goals_rows(st: Any, a: argparse.Namespace) -> list[dict]:
        return [{"goal": g.name, "description": g.description, "available": g.planner is not None, "needs": list(g.needs), "missing": list(g.missing)}
                for g in GOALS.values()]

    def goals_text(rows: list[dict]) -> None:
        for r in rows:
            print(f"{r['goal']:<24}{'available' if r['available'] else 'NOT YET':<10}{r['description']}"
                  + (f"  [needs: {', '.join(r['needs'])}]" if r["needs"] else "") + (f"  [missing: {'; '.join(r['missing'])}]" if r["missing"] else ""))

    add("goals", "Goals you can ask for, and what is missing for the others", goals_rows, goals_text, token=False, store=False)

    q = add("start", "Create a task: PLANNED, with its steps, checkpoints and estimates; nothing runs",
            lambda st, a: T.start_task(st, tok(a), goal=a.goal, statement=a.statement, params=K._json_arg(a.params, "--params") or {},
                                       max_tokens=a.budget_tokens, max_llm_calls=a.budget_calls, unlimited=a.unlimited, label=a.label,
                                       research_question_id=a.rq),
            lambda o: print(f"{o['task_id']} planned: {o['steps']} steps; checkpoints {', '.join(o['checkpoints'])}. "
                            f"Review with `research task plan {o['task_id']}`, then approve."))
    q.add_argument("--goal", required=True, choices=sorted(GOALS))
    q.add_argument("--statement", required=True, help="In your own words: what this task is for")
    q.add_argument("--params", help="Goal parameters as JSON, or a path to a JSON file")
    q.add_argument("--budget-tokens", type=int, dest="budget_tokens")
    q.add_argument("--budget-calls", type=int, dest="budget_calls")
    q.add_argument("--unlimited", action="store_true", help="State explicitly that no budget applies")
    q.add_argument("--label")
    q.add_argument("--rq", help="A ResearchQuestion this task addresses (optional)")

    for name in ("plan", "status"):
        q = add(name, "The steps, their state, the checkpoints, what is spent and what is estimated", lambda st, a: T.task_view(st, a.task), _task_text, token=False)
        q.add_argument("task")

    q = add("approve", "Approve the plan (a budget is set, or explicitly unlimited)", lambda st, a: T.approve_task(st, tok(a), a.task),
            lambda o: print(f"{o['task_id']} approved"))
    q.add_argument("task")

    q = add("revise", "Redirect the plan; the task goes back to PLANNED for your approval",
            lambda st, a: T.revise_task(st, tok(a), a.task, drop=tuple(a.drop or ()), set_inputs=_set_pairs(a.set),
                                        add_checkpoint_after=a.add_checkpoint_after, checkpoint_title=a.checkpoint_title),
            lambda o: print(f"{o['task_id']} revised: {', '.join(o['steps'])} (PLANNED)"))
    q.add_argument("task")
    q.add_argument("--drop", action="append", help="Step to drop (not a mandatory checkpoint)")
    q.add_argument("--set", action="append", help="STEP.param=value (JSON or text)")
    q.add_argument("--add-checkpoint-after", dest="add_checkpoint_after")
    q.add_argument("--checkpoint-title", dest="checkpoint_title", default="Researcher checkpoint")

    q = add("run", "Run the next steps; stops at your checkpoints, at a failure, or at a budget limit",
            lambda st, a: T.run_task(st, tok(a), a.task, **run_kwargs(a)), _run_text, model=True)
    q.add_argument("task")
    q.add_argument("--until", help="Pause before this step")
    q.add_argument("--rerun-stale", action="store_true", dest="rerun_stale", help="Redo steps whose inputs changed since they ran")
    q.add_argument("--break-lease", action="store_true", dest="break_lease", help="Owner only: take over a task whose runner is dead")
    q.add_argument("--accept-recorded-models", action="store_true", dest="accept_recorded_models",
                   help="Reuse finished model steps whatever model is configured now (otherwise a changed model makes them stale)")

    q = add("retry", "Run again from a failed or blocked step", lambda st, a: T.retry_task(st, tok(a), a.task, a.step, **run_kwargs(a)), _run_text, model=True)
    q.add_argument("task")
    q.add_argument("step")
    q.add_argument("--rerun-stale", action="store_true", dest="rerun_stale")
    q.add_argument("--break-lease", action="store_true", dest="break_lease")
    q.add_argument("--accept-recorded-models", action="store_true", dest="accept_recorded_models")

    q = add("decide", "Clear a checkpoint with your own reason (author or owner token); there is no default",
            lambda st, a: T.decide(st, tok(a), a.task, a.step, a.rationale, exempt=_exempt(a.exempt)),
            lambda o: print(f"{o['step_id']} {'was already decided' if o['already_decided'] else 'decided'} by {o['by']}"))
    q.add_argument("task")
    q.add_argument("step")
    q.add_argument("--rationale", required=True)
    q.add_argument("--exempt", action="append", metavar="OBJECT#ELEMENT=REASON",
                   help="Set aside a HIGH statement that has no adjudication, with your reason (recorded in the decision)")

    q = add("cancel", "Stop the task; runs already registered stay registered", lambda st, a: T.cancel_task(st, tok(a), a.task, a.reason),
            lambda o: print(f"{o['task_id']} cancelled"))
    q.add_argument("task")
    q.add_argument("--reason", required=True)

    q = add("replan", "Plan again from the stored parameters; unchanged steps keep their runs and your decisions",
            lambda st, a: T.replan_task(st, tok(a), a.task),
            lambda o: print(f"{o['task_id']} replanned: +{len(o['diff']['added'])} -{len(o['diff']['removed'])} ~{len(o['diff']['changed'])} (PLANNED)"))
    q.add_argument("task")

    q = add("continue", "A new task that starts from what a finished task produced",
            lambda st, a: T.continue_task(st, tok(a), a.parent, goal=a.goal, statement=a.statement, params=K._json_arg(a.params, "--params") or {},
                                          max_tokens=a.budget_tokens, max_llm_calls=a.budget_calls, unlimited=a.unlimited),
            lambda o: print(f"{o['task_id']} planned as a continuation; review with `research task plan {o['task_id']}`"))
    q.add_argument("parent")
    q.add_argument("--goal", required=True, choices=sorted(GOALS))
    q.add_argument("--statement", required=True)
    q.add_argument("--params", help='JSON; {"$parent_step": "S03", "key": "playbook_id"} takes the output of a finished step of the parent')
    q.add_argument("--budget-tokens", type=int, dest="budget_tokens")
    q.add_argument("--budget-calls", type=int, dest="budget_calls")
    q.add_argument("--unlimited", action="store_true")
