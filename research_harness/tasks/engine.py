"""The task engine (M5 WP3): create, plan, approve, run, decide, revise, retry, cancel, continue.

A task is a `ResearchTask` object in the project's own store (no parallel store). Step state is not kept on the object: it is derived from the
run registry (`task_step:*` rows carrying the task id), so the registry stays the single record of what ran (I5). The object changes only when
the plan, the budget, a researcher decision, the state or the lease changes; every change is a revision written with the revision the writer
read (`expected_head`), so two writers cannot overwrite each other.

Steps are `run` (a capability) or `decide` (a checkpoint only the researcher can clear). A completed step is reused when its fingerprint is
unchanged: the same capability, inputs, pinned objects (id, revision, digest) and upstream steps, and, for a model step, the same model and
configuration. A decision is bound to the fingerprint it was taken on: when what it decided changes, it no longer applies.

Spend is read from the charge rows the budget layer writes for every model call (`task_call:*`), never from the runs those calls were made for.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from research_harness.core.canonical import canonical_json_hash
from research_harness.core.errors import HarnessError
from research_harness.knowledge.common import KnowledgeError, engine_origin, next_numbered_id
from research_harness.state import workflow_runs
from research_harness.state.store import StateStore
from research_harness.tasks import budget as B
from research_harness.tasks import goals
from research_harness.tasks.capabilities import CAPABILITIES, Ctx
from research_harness.workflows import eca
from research_harness.workflows.adapters import adapter_identity

ROLES = ("owner", "author")
LEASE_TTL = 1800
RUNNABLE = ("APPROVED", "WAITING_FOR_RESEARCHER", "BLOCKED", "FAILED", "RUNNING")
TRANSITIONS: dict[str, set[str]] = {
    "PLANNED": {"PLANNED", "APPROVED", "CANCELLED"},
    "APPROVED": {"APPROVED", "RUNNING", "PLANNED", "CANCELLED"},
    "RUNNING": {"RUNNING", "APPROVED", "WAITING_FOR_RESEARCHER", "BLOCKED", "FAILED", "COMPLETED", "CANCELLED"},
    "WAITING_FOR_RESEARCHER": {"WAITING_FOR_RESEARCHER", "RUNNING", "PLANNED", "CANCELLED", "BLOCKED"},
    "BLOCKED": {"BLOCKED", "RUNNING", "PLANNED", "CANCELLED"},
    "FAILED": {"FAILED", "RUNNING", "PLANNED", "CANCELLED"},
    "COMPLETED": set(), "CANCELLED": set(),
}
DONE = ("DONE", "DECIDED")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(t: datetime) -> str:
    return t.isoformat()


# ------------------------------------------------------------------------------------------------ the object

def load_task(store: StateStore, task_id: str) -> dict[str, Any]:
    o = store.get_object(task_id)
    if not o or o["object_type"] != "ResearchTask":
        raise KnowledgeError("TASK_NOT_FOUND", f"{task_id} is not a ResearchTask in this project")
    return o


def _require_task_support(store: StateStore) -> None:
    with store.read() as conn:
        if not workflow_runs.has_registry(conn) or not workflow_runs.has_task_columns(conn):
            raise KnowledgeError("SCHEMA_UPGRADE_REQUIRED", "this project's database predates task support (schema 9.0.4); "
                                 "run `research project upgrade-schema` with the owner token (on a copy first, if the store is evaluation evidence)")


def _transition(content: dict[str, Any], new: str, reason: Optional[str] = None, **changes: Any) -> dict[str, Any]:
    cur = content["state"]
    if new not in TRANSITIONS[cur]:
        raise KnowledgeError("TASK_STATE_INVALID", f"a task that is {cur} cannot become {new}")
    return {**content, "state": new, "state_reason": reason, **changes}


def _write(store: StateStore, token: Optional[str], task_id: str, content: dict[str, Any], operation: str, *, expected_head: int,
           edges: Optional[list] = None, roles: tuple = ROLES) -> int:
    with store.write_tx(token=token, require_roles=roles) as tx:
        if edges is None:
            head = tx.head(task_id)
            rows = tx.conn.execute("SELECT relation,target_id,target_revision,provenance FROM edges WHERE source_id=? AND source_revision=?",
                                   (task_id, head["head_revision"])).fetchall() if head else []
            edges = [(r[0], r[1], r[2], json.loads(r[3])) for r in rows]
        res = tx.add_revision(object_id=task_id, object_type="ResearchTask", content=content, edges=edges, expected_head=expected_head,
                              origin=engine_origin(operation, tx.actor_id))
        tx.audit(operation, "RECORDED", {"task_id": task_id, "revision": res["revision"], "state": content["state"]})
    return res["revision"]


# ------------------------------------------------------------------------------------------------ plans

def _validate_plan(plan: dict[str, Any]) -> None:
    seen: set[str] = set()
    for s in plan["steps"]:
        if s["step_id"] in seen:
            raise KnowledgeError("PLAN_INVALID", f"duplicate step {s['step_id']}")
        for d in s["depends_on"]:
            if d not in seen:
                raise KnowledgeError("PLAN_INVALID", f"{s['step_id']} depends on {d}, which is not an earlier step")
        if s["kind"] == "run" and s["capability"] not in CAPABILITIES:
            raise KnowledgeError("PLAN_INVALID", f"{s['step_id']}: unknown capability {s['capability']}")
        seen.add(s["step_id"])
    for m in plan.get("mandatory_checkpoints", []):
        if m not in seen:
            raise KnowledgeError("PLAN_INVALID", f"mandatory checkpoint {m} is not a step")


class _Unresolved(Exception):
    pass


def _resolve(value: Any, outputs: dict[str, Any]) -> Any:
    if isinstance(value, dict) and "$step" in value:
        sid = value["$step"]
        if sid not in outputs:
            raise _Unresolved(sid)
        out = outputs[sid]
        return out if value.get("key") is None else out.get(value["key"])
    if isinstance(value, dict):
        return {k: _resolve(v, outputs) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, outputs) for v in value]
    return value


def _estimate_step(store: StateStore, step: dict[str, Any], outputs: dict[str, Any]) -> dict[str, Any]:
    if step["kind"] == "decide":
        return {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "basis": "researcher decision"}
    try:
        resolved = _resolve(step["inputs"], outputs)
    except _Unresolved as exc:
        return {"llm_calls": None, "input_tokens": None, "output_tokens": None, "basis": f"unknown until {exc.args[0]} has run"}
    try:
        return CAPABILITIES[step["capability"]].estimate(store, resolved)
    except KnowledgeError as exc:
        return {"llm_calls": None, "input_tokens": None, "output_tokens": None, "basis": f"not estimable: {exc.code}"}


def _with_estimates(store: StateStore, plan: dict[str, Any]) -> dict[str, Any]:
    return {**plan, "steps": [{**s, "estimate": _estimate_step(store, s, {})} for s in plan["steps"]]}


def _budget(max_tokens: Optional[int], max_llm_calls: Optional[int], unlimited: bool) -> dict[str, Any]:
    for name, v in (("max_tokens", max_tokens), ("max_llm_calls", max_llm_calls)):
        if v is not None and int(v) < 1:
            raise KnowledgeError("BUDGET_INVALID", f"{name} must be at least 1")
    return {"max_tokens": max_tokens, "max_llm_calls": max_llm_calls, "unlimited": bool(unlimited)}


# ------------------------------------------------------------------------------------------------ analysis (derived step state)

def _fingerprint(step: dict[str, Any], upstream: dict[str, str], pins: list) -> str:
    if step["kind"] == "decide":
        return canonical_json_hash({"decide": step["decision"], "title": step["title"], "inputs": step["inputs"], "upstream": upstream})
    cap = CAPABILITIES[step["capability"]]
    return canonical_json_hash({"capability": cap.name, "version": cap.version, "inputs": step["inputs"], "pins": pins, "upstream": upstream})


def _load_output(store: StateStore, row: dict[str, Any]) -> Any:
    if not row.get("artifact_sha256") or not store.artifacts.has(row["artifact_sha256"]):
        return None
    return json.loads(store.artifacts.get_bytes(row["artifact_sha256"]).decode("utf-8"))


def _analyze(store: StateStore, task_id: str, content: dict[str, Any], model_identity: Optional[dict] = None) -> dict[str, dict[str, Any]]:
    """Per step: fingerprint, status and (when done) its output.

    Statuses: DONE, DECIDED, PENDING, FAILED, STALE, AWAITING_DECISION, STALE_DECISION, INPUT_MISSING, WAITING_ON_UPSTREAM.
    `fp` identifies what the step is asked to do; `eff` is what dependents rest on: `fp` plus the model that actually produced the output, so a
    change of model (or of its configuration) makes every step downstream of a model step stale, not only the model step itself.
    With `model_identity` None the recorded model is accepted (the caller has chosen not to check it)."""
    with store.read() as conn:
        rows = workflow_runs.task_rows(conn, task_id, steps=True)
    by_step: dict[str, list[dict]] = {}
    for r in rows:
        by_step.setdefault(r["task_step_id"], []).append(r)
    decisions = {c["step_id"]: c.get("decision") for c in content["checkpoints"]}
    out: dict[str, dict[str, Any]] = {}
    outputs: dict[str, Any] = {}
    for step in content["plan"]["steps"]:
        sid = step["step_id"]
        upstream = {d: out[d]["eff"] for d in step["depends_on"]}
        ready = all(out[d]["status"] in DONE for d in step["depends_on"])
        info: dict[str, Any] = {"step": step, "ready": ready, "row": None, "resolved": None, "error": None}
        if step["kind"] == "decide":
            info["fp"] = info["eff"] = _fingerprint(step, upstream, [])
            dec = decisions.get(sid)
            info["status"] = ("DECIDED" if dec and dec.get("step_fingerprint") == info["fp"] else "STALE_DECISION" if dec else "AWAITING_DECISION")
            info["decision"] = dec
            try:
                info["resolved"] = _resolve(step["inputs"], outputs)
            except _Unresolved:
                pass
            out[sid] = info
            continue
        try:
            info["resolved"] = _resolve(step["inputs"], outputs)
            pins = CAPABILITIES[step["capability"]].pins(store, info["resolved"])
        except _Unresolved:
            pins = []
        except KnowledgeError as exc:
            pins, info["error"] = [], f"{exc.code}: {exc.message}"
        info["fp"] = _fingerprint(step, upstream, pins)
        mine = by_step.get(sid, [])
        same = [r for r in mine if r["execution_status"] == "SUCCEEDED" and r["input_fingerprint"] == info["fp"]]
        wants_model = step["model"] and model_identity is not None
        hit = same[0] if same and not wants_model else next((r for r in same if r.get("model_descriptor") == model_identity), None) if same else None
        recorded_model = (hit or {}).get("model_descriptor") if step["model"] else None
        if hit is not None:
            info["status"], info["row"] = "DONE", hit
            outputs[sid] = _load_output(store, hit)
            info["eff"] = canonical_json_hash({"fp": info["fp"], "model": recorded_model})
        else:
            info["eff"] = canonical_json_hash({"fp": info["fp"], "pending": True, "model": model_identity if step["model"] else None})
            if same:        # finished before, but not by the model that is now in use
                info["status"], info["row"] = "STALE", same[0]
                info["stale_model"] = True
                info["stale_reason"] = (f"the model or its configuration changed (it was made by {(same[0].get('model_descriptor') or {}).get('model_id')}, "
                                        f"now {model_identity.get('model_id')})")
            elif info["error"]:
                info["status"] = "INPUT_MISSING"
            elif mine and mine[0]["input_fingerprint"] == info["fp"] and mine[0]["execution_status"] == "FAILED":
                info["status"], info["row"] = "FAILED", mine[0]
            elif any(r["execution_status"] == "SUCCEEDED" for r in mine):
                info["status"], info["stale_reason"] = "STALE", "an input, a pinned object or an upstream step changed since it ran"
                prev = next(r for r in mine if r["execution_status"] == "SUCCEEDED")
                if wants_model and prev.get("model_descriptor") != model_identity:
                    info["stale_model"] = True      # it was last made by another model: redoing it must ask the new one
            else:
                info["status"] = "PENDING" if ready else "WAITING_ON_UPSTREAM"
        out[sid] = info
    return out


def _spent(store: StateStore, task_id: str, *, include_in_flight: bool = True) -> dict[str, int]:
    """What the task has been charged: the charge rows of its model calls, plus (for display) calls whose answer has not been accounted yet."""
    with store.read() as conn:
        spent = workflow_runs.task_spent(conn, task_id)
    if include_in_flight:
        for it in B.outstanding_intents(store, task_id):
            spent = {"llm_calls": spent["llm_calls"] + 1, "tokens": spent["tokens"] + int(it["input_reserved"]) + int(it["output_reserved"])}
    return spent


# ------------------------------------------------------------------------------------------------ create, view

def start_task(store: StateStore, token: Optional[str], *, goal: str, statement: str, params: Optional[dict] = None,
               max_tokens: Optional[int] = None, max_llm_calls: Optional[int] = None, unlimited: bool = False,
               label: Optional[str] = None, research_question_id: Optional[str] = None, parent: Optional[dict] = None,
               continued_from: Optional[dict] = None, domain_workflow: Optional[str] = None) -> dict[str, Any]:
    """A new task, PLANNED. Needs no research question, manuscript, methods or results: only a goal and what it needs."""
    _require_task_support(store)
    if not statement or not statement.strip():
        raise KnowledgeError("STATEMENT_REQUIRED", "say in your own words what the task is for")
    params = params or {}
    plan = _with_estimates(store, goals.plan_for(goal, params))
    _validate_plan(plan)
    edges: list[tuple] = []
    with store.read() as conn:
        task_id = next_numbered_id(conn, "TASK_", "ResearchTask")
        if research_question_id:
            rq = store.get_object(research_question_id, conn=conn)
            if not rq or rq["object_type"] != "ResearchQuestion":
                raise KnowledgeError("RQ_NOT_FOUND", f"{research_question_id} is not a ResearchQuestion")
            edges.append(("depends_on", rq["object_id"], rq["revision"], {"edge_kind": "addresses"}))
        if parent:
            edges.append(("derived_from", parent["task_id"], parent["revision"], {"edge_kind": "continues"}))
    stmt = {"text": statement.strip(), **({"research_question_id": research_question_id} if research_question_id else {})}
    content = {"label": label or f"{goal}: {statement.strip()[:80]}", "goal": goal, "goal_version": goals.GOAL_VERSION, "params": params,
               "statement": stmt, "parent_task": parent, "continued_from": continued_from, "domain_workflow": domain_workflow, "plan": plan,
               "budget": _budget(max_tokens, max_llm_calls, unlimited), "state": "PLANNED", "state_reason": None,
               "checkpoints": [{"step_id": s["step_id"], "decision": None} for s in plan["steps"] if s["kind"] == "decide"], "lease": None,
               "generated_by": f"planner:{plan['planner']}"}
    rev = _write(store, token, task_id, content, "task_start", expected_head=0, edges=edges)
    return {"task_id": task_id, "revision": rev, "state": "PLANNED", "steps": len(plan["steps"]),
            "checkpoints": [s["step_id"] for s in plan["steps"] if s["kind"] == "decide"]}


def task_view(store: StateStore, task_id: str, *, model_identity: Optional[dict] = None) -> dict[str, Any]:
    task = load_task(store, task_id)
    c = task["content"]
    analysis = _analyze(store, task_id, c, model_identity)
    steps, known_calls, known_tokens, unknown = [], 0, 0, 0
    for s in c["plan"]["steps"]:
        a = analysis[s["step_id"]]
        est = s.get("estimate") or {}
        if a["status"] not in DONE:
            if est.get("llm_calls") is None:
                unknown += 1 if s["kind"] == "run" else 0
            else:
                known_calls += est["llm_calls"]
                known_tokens += (est.get("input_tokens") or 0) + (est.get("output_tokens") or 0)
        steps.append({"step_id": s["step_id"], "kind": s["kind"], "title": s["title"], "capability": s.get("capability"), "decision": s.get("decision"),
                      "model": s["model"], "depends_on": s["depends_on"], "status": a["status"], "stale_reason": a.get("stale_reason"),
                      "error": a.get("error"), "estimate": est, "run_id": (a["row"] or {}).get("run_id"),
                      "produced_by": ((a["row"] or {}).get("model_descriptor") or {}).get("model_id") if s["model"] else None,
                      "decided_by": (a.get("decision") or {}).get("by") if s["kind"] == "decide" else None})
    return {"task_id": task_id, "revision": task["revision"], "goal": c["goal"], "label": c["label"], "state": c["state"], "state_reason": c["state_reason"],
            "statement": c["statement"], "budget": c["budget"], "spent": _spent(store, task_id), "in_flight_calls": len(B.outstanding_intents(store, task_id)),
            "lease": c.get("lease"), "mandatory_checkpoints": c["plan"].get("mandatory_checkpoints", []), "steps": steps,
            "remaining_estimate": {"llm_calls": known_calls, "tokens": known_tokens, "steps_with_unknown_estimate": unknown,
                                   "note": "estimates are planning aids with a stated basis; the budget is enforced per call"}}


# ------------------------------------------------------------------------------------------------ approve, revise, replan, cancel

def approve_task(store: StateStore, token: Optional[str], task_id: str) -> dict[str, Any]:
    task = load_task(store, task_id)
    c = task["content"]
    b = c["budget"]
    if b.get("max_tokens") is None and b.get("max_llm_calls") is None and not b.get("unlimited"):
        raise KnowledgeError("BUDGET_REQUIRED", "set a budget (max tokens and/or model calls) or state explicitly that it is unlimited")
    rev = _write(store, token, task_id, _transition(c, "APPROVED", None), "task_approve", expected_head=task["revision"])
    return {"task_id": task_id, "revision": rev, "state": "APPROVED"}


def revise_task(store: StateStore, token: Optional[str], task_id: str, *, drop: tuple = (), set_inputs: Optional[dict[str, Any]] = None,
                add_checkpoint_after: Optional[str] = None, checkpoint_title: str = "Researcher checkpoint") -> dict[str, Any]:
    """Redirect the plan: drop steps, change a step's input, add a checkpoint. The task goes back to PLANNED: the researcher approves the new plan."""
    task = load_task(store, task_id)
    c = task["content"]
    plan = json.loads(json.dumps(c["plan"]))
    steps = plan["steps"]
    mandatory = set(plan.get("mandatory_checkpoints", []))
    ids = {s["step_id"] for s in steps}
    for sid in drop:
        if sid not in ids:
            raise KnowledgeError("STEP_UNKNOWN", f"{sid} is not a step of {task_id}")
        if sid in mandatory:
            raise KnowledgeError("CHECKPOINT_REQUIRED", f"{sid} is a mandatory checkpoint of this goal and cannot be dropped")
        dependents = [s["step_id"] for s in steps if sid in s["depends_on"] and s["step_id"] not in drop]
        if dependents:
            raise KnowledgeError("STEP_HAS_DEPENDENTS", f"{', '.join(dependents)} depend on {sid}; drop them too or change what they depend on")
    steps = [s for s in steps if s["step_id"] not in drop]
    for key, value in (set_inputs or {}).items():
        sid, _, param = key.partition(".")
        step = next((s for s in steps if s["step_id"] == sid), None)
        if step is None or not param:
            raise KnowledgeError("STEP_UNKNOWN", f"use STEP.param=value with an existing step, not {key!r}")
        step["inputs"] = {**step["inputs"], param: value}
    if add_checkpoint_after:
        after = next((i for i, s in enumerate(steps) if s["step_id"] == add_checkpoint_after), None)
        if after is None:
            raise KnowledgeError("STEP_UNKNOWN", f"{add_checkpoint_after} is not a step of {task_id}")
        new_id = f"{add_checkpoint_after}c"
        if any(s["step_id"] == new_id for s in steps):
            raise KnowledgeError("STEP_EXISTS", f"{new_id} already exists")
        new = goals._decide(new_id, "generic", checkpoint_title, {}, [add_checkpoint_after])
        for s in steps[after + 1:]:
            if add_checkpoint_after in s["depends_on"]:
                s["depends_on"] = [*s["depends_on"], new_id]
        steps.insert(after + 1, new)
        mandatory.add(new_id)
    plan = {**plan, "steps": steps, "mandatory_checkpoints": sorted(m for m in mandatory if any(s["step_id"] == m for s in steps)),
            "revised_by_researcher": True}
    _validate_plan(plan)
    plan = _with_estimates(store, plan)
    keep = {s["step_id"] for s in steps if s["kind"] == "decide"}
    old = {x["step_id"]: x for x in c["checkpoints"]}
    checkpoints = [old.get(sid, {"step_id": sid, "decision": None}) for sid in sorted(keep)]
    rev = _write(store, token, task_id, _transition({**c, "plan": plan, "checkpoints": checkpoints, "lease": None}, "PLANNED", "plan revised"),
                 "task_revise", expected_head=task["revision"])
    return {"task_id": task_id, "revision": rev, "state": "PLANNED", "steps": [s["step_id"] for s in steps]}


def replan_task(store: StateStore, token: Optional[str], task_id: str) -> dict[str, Any]:
    """Run the goal's planner again on the stored parameters. Steps whose fingerprint is unchanged keep their runs and decisions."""
    task = load_task(store, task_id)
    c = task["content"]
    plan = _with_estimates(store, goals.plan_for(c["goal"], c["params"]))
    _validate_plan(plan)
    before = {s["step_id"]: {k: v for k, v in s.items() if k != "estimate"} for s in c["plan"]["steps"]}
    after = {s["step_id"]: {k: v for k, v in s.items() if k != "estimate"} for s in plan["steps"]}
    diff = {"added": sorted(set(after) - set(before)), "removed": sorted(set(before) - set(after)),
            "changed": sorted(k for k in set(before) & set(after) if before[k] != after[k])}
    old = {x["step_id"]: x for x in c["checkpoints"]}
    checkpoints = [old.get(s["step_id"], {"step_id": s["step_id"], "decision": None}) for s in plan["steps"] if s["kind"] == "decide"]
    rev = _write(store, token, task_id, _transition({**c, "plan": plan, "checkpoints": checkpoints, "lease": None}, "PLANNED", "replanned"),
                 "task_replan", expected_head=task["revision"])
    return {"task_id": task_id, "revision": rev, "state": "PLANNED", "diff": diff}


def cancel_task(store: StateStore, token: Optional[str], task_id: str, reason: str) -> dict[str, Any]:
    """Stop the task. Runs already registered stay registered; a model call in flight is not interrupted and is still recorded."""
    task = load_task(store, task_id)
    rev = _write(store, token, task_id, _transition({**task["content"], "lease": None}, "CANCELLED", reason or "cancelled"),
                 "task_cancel", expected_head=task["revision"])
    return {"task_id": task_id, "revision": rev, "state": "CANCELLED"}


# ------------------------------------------------------------------------------------------------ decisions

def _scope_objects(resolved: dict[str, Any]) -> Optional[set]:
    objs = set(resolved.get("objects") or ([resolved["playbook"]] if resolved.get("playbook") else []))
    return objs or None


def decide(store: StateStore, token: Optional[str], task_id: str, step_id: str, rationale: str, *,
           exempt: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """The researcher clears a checkpoint (author or owner). The decision is bound to the step's current fingerprint.

    The adjudication checkpoint closes only when every HIGH statement in the task's scope has a researcher confirmation for its current words.
    Two things can stand in the way and are reported apart: a proposal nobody has confirmed, and a statement that has no proposal at all (for
    instance because `limit` stopped the run, or it has no passage). A statement with no proposal can be set aside only with `exempt`
    (`{"OBJECT#ELEMENT": "reason"}`); the reasons are recorded in the decision."""
    if not rationale or not rationale.strip():
        raise KnowledgeError("RATIONALE_REQUIRED", "a decision needs the researcher's reason; there is no default")
    task = load_task(store, task_id)
    c = task["content"]
    step = next((s for s in c["plan"]["steps"] if s["step_id"] == step_id), None)
    if step is None or step["kind"] != "decide":
        raise KnowledgeError("STEP_NOT_A_CHECKPOINT", f"{step_id} is not a checkpoint of {task_id}")
    if c["state"] in ("COMPLETED", "CANCELLED"):
        raise KnowledgeError("TASK_STATE_INVALID", f"the task is {c['state']}")
    a = _analyze(store, task_id, c)[step_id]
    if not a["ready"]:
        raise KnowledgeError("STEP_NOT_READY", f"{step_id} waits for {', '.join(d for d in step['depends_on'])} to be done")
    if a["status"] == "DECIDED":
        return {"task_id": task_id, "step_id": step_id, "already_decided": True, "by": a["decision"]["by"]}
    note: dict[str, Any] = {}
    if step["decision"] == "adjudications":
        comp = eca.completeness(store, _scope_objects(a["resolved"] or {}))
        applied = {k: v.strip() for k, v in (exempt or {}).items() if isinstance(v, str) and v.strip()
                   and any(f"{x['object_id']}#{x['element']}" == k for x in comp["not_adjudicated"])}
        missing = [x for x in comp["not_adjudicated"] if f"{x['object_id']}#{x['element']}" not in applied]
        if comp["awaiting_confirmation"] or missing:
            raise KnowledgeError(
                "DECISION_PRECONDITION",
                f"{len(comp['awaiting_confirmation'])} proposed adjudication(s) wait for your confirmation (research adjudicate sheet, then confirm-batch) and "
                f"{len(missing)} HIGH statement(s) have no adjudication yet (research adjudicate run-pending, or set them aside with a reason)",
                awaiting_confirmation=[x["adjudication_id"] for x in comp["awaiting_confirmation"]][:100],
                not_adjudicated=[f"{x['object_id']}#{x['element']}" for x in missing][:100])
        note = {"high_statements": comp["high_total"], "confirmed": comp["confirmed"], "unadjudicated_set_aside": len(applied), "exempt": applied}
    decision = {"at": None, "by": None, "rationale": rationale.strip(), "step_fingerprint": a["fp"], **note}
    with store.write_tx(token=token, require_roles=ROLES) as tx:
        decision["at"], decision["by"] = tx.at, tx.actor_id
        checkpoints = [x for x in c["checkpoints"] if x["step_id"] != step_id] + [{"step_id": step_id, "decision": decision}]
        head = tx.head(task_id)
        rows = tx.conn.execute("SELECT relation,target_id,target_revision,provenance FROM edges WHERE source_id=? AND source_revision=?",
                               (task_id, head["head_revision"])).fetchall()
        res = tx.add_revision(object_id=task_id, object_type="ResearchTask", expected_head=task["revision"],
                              content={**c, "checkpoints": sorted(checkpoints, key=lambda x: x["step_id"])},
                              edges=[(r[0], r[1], r[2], json.loads(r[3])) for r in rows], origin=engine_origin("task_decide", tx.actor_id))
        tx.audit("task_decide", "RECORDED", {"task_id": task_id, "step_id": step_id, "revision": res["revision"]})
    return {"task_id": task_id, "step_id": step_id, "revision": res["revision"], "by": decision["by"], "already_decided": False}


# ------------------------------------------------------------------------------------------------ run

def _recover_unknown_calls(store: StateStore, task_id: str) -> list[str]:
    """A call whose answer never arrived is charged at its reservation and recorded as a charge row; it is never silently free."""
    ids = []
    for it in B.outstanding_intents(store, task_id):
        charged = int(it["input_reserved"]) + int(it["output_reserved"])
        ids.append(workflow_runs.append_run(store, {
            "workflow": "task_call:unknown_outcome", "input_fingerprint": canonical_json_hash({"intent": it["_path"], "at": it["at"]}),
            "executor": "task_engine@1", "execution_status": "FAILED", "context_status": "PARTIAL", "assessment_status": "NOT_ASSESSED",
            "applicability": "APPLICABLE", "legacy_status": "FAILED", "status_reason": "unknown_outcome_charged_at_reservation",
            "usage": {"llm_calls": 1, "input_tokens": it["input_reserved"], "output_tokens": it["output_reserved"], "charged_tokens": charged,
                      "usage_source": "reserved"},
            "model_descriptor": it.get("model"), "task_id": task_id, "task_step_id": it["step_id"]}))
        B.mark_accounted(it)
    return ids


def _failure_of(out: Any) -> Optional[str]:
    if isinstance(out, dict):
        if out.get("status") == "FAILED":
            return str(out.get("message") or out.get("reason") or "the step reported FAILED")
        if out.get("failed"):
            return f"{len(out['failed'])} item(s) failed: " + "; ".join(f"{f.get('object_id', '')} {f.get('error', '')}".strip() for f in out["failed"][:3])
    return None


class _Run:
    """One run of a task: the lease it holds, the revision it last wrote, and the heartbeat that keeps the lease alive while a step executes.

    A step can last longer than the lease. A background heartbeat renews the lease every third of its length; if the task changed or was
    taken over, the heartbeat marks the run lost. Before a step's result is registered the run checks, under the same lock, that the task is still
    exactly as it left it, so a runner that lost its lease cannot register a result or write the task state."""

    def __init__(self, store: StateStore, token: Optional[str], task_id: str, holder: str, ttl: float, rev: int):
        self.store, self.token, self.task_id, self.holder, self.ttl, self.rev = store, token, task_id, holder, ttl, rev
        self.lock = threading.RLock()
        self.lost: Optional[str] = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._beat, name=f"task-lease-{task_id}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=self.ttl + 5)

    def _beat(self) -> None:
        interval = max(self.ttl / 3.0, 0.05)
        while not self._stop.wait(interval):
            with self.lock:
                if self.lost or self._stop.is_set():
                    return
                try:
                    cur = load_task(self.store, self.task_id)
                    lease = cur["content"].get("lease") or {}
                    if cur["revision"] != self.rev or lease.get("holder") != self.holder:
                        self.lost = "the task was changed or taken over while this run held it"
                        return
                    self.rev = _write(self.store, self.token, self.task_id,
                                      {**cur["content"], "lease": {**lease, "expires_at": _iso(_now() + timedelta(seconds=self.ttl))}},
                                      "task_lease_renew", expected_head=cur["revision"])
                except HarnessError as exc:
                    if exc.code == "STALE_REVISION":
                        self.lost = "the task was changed while the lease was being renewed"
                        return
                except Exception:  # noqa: BLE001 - a transient failure: try again at the next beat; the final check is what decides
                    continue

    def still_mine(self) -> bool:
        with self.lock:
            if self.lost:
                return False
            cur = load_task(self.store, self.task_id)
            return cur["revision"] == self.rev and (cur["content"].get("lease") or {}).get("holder") == self.holder


def run_task(store: StateStore, token: Optional[str], task_id: str, *, adapter: Any = None, adapter_factory: Optional[Callable[[], Any]] = None,
             until: Optional[str] = None, rerun_stale: bool = False, lease_ttl: float = LEASE_TTL, break_lease: bool = False,
             accept_recorded_models: bool = False) -> dict[str, Any]:
    """Run the next steps. Stops at the first checkpoint that waits for the researcher, at a failure, at a budget limit, or when done.

    A step that called a model is reused only if the model in use is the one that produced it (same model id and configuration); otherwise it is
    STALE. Pass `accept_recorded_models` to say that recorded outputs stand whatever model is configured now."""
    _require_task_support(store)
    task = load_task(store, task_id)
    c = task["content"]
    if c["state"] == "PLANNED":
        raise KnowledgeError("TASK_NOT_APPROVED", "approve the plan first: the researcher sees the steps, checkpoints and estimates before anything runs")
    if c["state"] not in RUNNABLE:
        raise KnowledgeError("TASK_STATE_INVALID", f"a task that is {c['state']} cannot run")
    lease = c.get("lease")
    if lease and lease["expires_at"] > _iso(_now()) and not break_lease:
        raise KnowledgeError("TASK_LEASED", f"another run holds this task until {lease['expires_at']} (an owner may pass break_lease if that run is dead)",
                             holder=lease["holder"], expires_at=lease["expires_at"])
    holder = f"run-{os.getpid()}-{secrets.token_hex(3)}"
    new_lease = {"holder": holder, "acquired_at": _iso(_now()), "expires_at": _iso(_now() + timedelta(seconds=lease_ttl))}
    rev = _write(store, token, task_id, _transition(c, "RUNNING", None, lease=new_lease), "task_run", expected_head=task["revision"],
                 roles=("owner",) if (lease and break_lease) else ROLES)
    run = _Run(store, token, task_id, holder, lease_ttl, rev)
    run.start()
    try:
        return _run_loop(store, token, task_id, run, adapter, adapter_factory, until, rerun_stale, accept_recorded_models)
    except BaseException:
        run.stop()
        try:
            cur = load_task(store, task_id)
            if cur["revision"] == run.rev and (cur["content"].get("lease") or {}).get("holder") == holder:
                _write(store, token, task_id, {**cur["content"], "lease": None, "state_reason": "interrupted"}, "task_interrupted", expected_head=cur["revision"])
        except Exception:  # noqa: BLE001 - the original error is the one to report
            pass
        raise
    finally:
        run.stop()


def _obtain_adapter(factory: Optional[Callable[[], Any]]) -> tuple[Any, Optional[str]]:
    if factory is None:
        return None, "no model is configured"
    try:
        ad = factory()
    except Exception as exc:  # noqa: BLE001 - a missing key or option is a block, not a crash
        return None, f"{type(exc).__name__}: {exc}"
    return (ad, None) if ad is not None else (None, "no model is configured")


def _run_loop(store, token, task_id, run: _Run, adapter, adapter_factory, until, rerun_stale, accept_recorded) -> dict[str, Any]:
    recovered = _recover_unknown_calls(store, task_id)
    task = load_task(store, task_id)
    b = task["content"]["budget"]
    spent = _spent(store, task_id, include_in_flight=False)
    ledger = B.Ledger(b.get("max_tokens"), b.get("max_llm_calls"), spent["tokens"], spent["llm_calls"])
    executed: list[str] = []
    reused: list[str] = []
    result: dict[str, Any] = {"task_id": task_id, "executed": executed, "reused": reused, "recovered_unknown_calls": recovered}

    def totals() -> dict[str, int]:
        return {"tokens": ledger.spent_tokens, "llm_calls": ledger.spent_calls}

    def lost(reason: str) -> dict[str, Any]:
        result.update(state="LEASE_LOST", reason=reason, spent=totals())
        return result

    def stop(new_state: str, reason: Optional[str]) -> dict[str, Any]:
        run.stop()
        with run.lock:
            if not run.still_mine():
                return lost("another runner or the owner took the task before this run could record its state")
            cur = load_task(store, task_id)
            run.rev = _write(store, token, task_id, _transition({**cur["content"], "lease": None}, new_state, reason), f"task_{new_state.lower()}",
                             expected_head=run.rev)
        result.update(state=new_state, reason=reason, revision=run.rev, spent=totals())
        return result

    for step in task["content"]["plan"]["steps"]:
        with run.lock:
            if run.lost:
                return lost(run.lost)
            cur = load_task(store, task_id)
            if cur["revision"] != run.rev:
                result.update(state=cur["content"]["state"], reason="the task was changed while it was running (cancelled, revised, replanned or taken over)",
                              spent=totals())
                return result
        sid = step["step_id"]
        identity = adapter_identity(adapter) if adapter is not None else None
        a = _analyze(store, task_id, cur["content"], identity)[sid]
        if step["kind"] == "run" and step["model"] and adapter is None and not accept_recorded and a["status"] != "WAITING_ON_UPSTREAM":
            # a finished model step is reused only if its model is the one in use: ask for the adapter before deciding, not after
            adapter, err = _obtain_adapter(adapter_factory)
            if adapter is None:
                return stop("BLOCKED", f"ADAPTER_REQUIRED: {sid} calls a model, and which model produced earlier outputs cannot be checked without one "
                                       f"({err}); configure a provider, or accept the recorded outputs explicitly")
            identity = adapter_identity(adapter)
            a = _analyze(store, task_id, cur["content"], identity)[sid]
        if a["status"] in DONE:
            reused.append(sid)
            continue
        if step["kind"] == "decide":
            return stop("WAITING_FOR_RESEARCHER", f"{sid}: {step['title']}")
        if until and sid > until:
            return stop("APPROVED", f"paused before {sid} (--until {until})")
        if a["status"] == "INPUT_MISSING":
            return stop("BLOCKED", f"{sid}: {a['error']}")
        if a["status"] == "STALE" and not rerun_stale:
            return stop("BLOCKED", f"STALE_STEPS: {sid} ({a.get('stale_reason')}); run again with rerun_stale to redo it")
        if not a["ready"] or a["resolved"] is None:
            return stop("BLOCKED", f"{sid}: its upstream steps are not done")
        cap = CAPABILITIES[step["capability"]]
        if ledger.near_limit():
            return stop("BLOCKED", f"BUDGET_NEAR_LIMIT: {ledger.spent_tokens} of {ledger.max_tokens} tokens spent; raise the budget to continue")
        est = cap.estimate(store, a["resolved"])
        if est.get("llm_calls") is not None:
            need = (est.get("input_tokens") or 0) + (est.get("output_tokens") or 0)
            if ledger.max_tokens is not None and ledger.spent_tokens + need > ledger.max_tokens:
                return stop("BLOCKED", f"BUDGET_EXCEEDED: {sid} is estimated at {need} tokens, {ledger.max_tokens - ledger.spent_tokens} remain")
            if ledger.max_calls is not None and ledger.spent_calls + est["llm_calls"] > ledger.max_calls:
                return stop("BLOCKED", f"BUDGET_EXCEEDED: {sid} is estimated at {est['llm_calls']} calls, {ledger.max_calls - ledger.spent_calls} remain")
        if cap.model and adapter is None:
            adapter, err = _obtain_adapter(adapter_factory)
            if adapter is None:
                return stop("BLOCKED", f"ADAPTER_REQUIRED: {sid} calls a model ({err})")
        failure: Optional[str] = None
        output: Any = None
        with workflow_runs.task_scope(task_id, sid) as scope:
            try:
                output = cap.run(Ctx(store, token, B.BudgetedAdapter(adapter, ledger, store, task_id, sid) if cap.model else None, task_id, sid,
                                      fresh=bool(a.get("stale_model"))), a["resolved"])
                failure = _failure_of(output)
            except Exception as exc:  # noqa: BLE001 - a failed step is recorded, not lost
                failure = f"{getattr(exc, 'code', type(exc).__name__)}: {getattr(exc, 'message', None) or exc}"
        mine = run.still_mine()          # fencing: a run that lost the task registers a failure, never a result
        if not mine:
            failure = "lease_lost: another runner or the owner took the task while this step ran; its result is not used"
        with store.read() as conn:
            made = [r for r in scope.run_ids if workflow_runs.get_run(conn, r)]
        artifact = store.artifacts.put_bytes(json.dumps(output if output is not None else {"error": failure}, sort_keys=True, default=str).encode("utf-8"))
        workflow_runs.append_run(store, {
            "workflow": f"task_step:{cap.name}", "input_fingerprint": a["fp"], "executor": "task_engine@1",
            "execution_status": "FAILED" if failure else "SUCCEEDED", "context_status": "COMPLETE",
            "assessment_status": "NOT_ASSESSED" if failure else "PROVISIONAL", "applicability": "APPLICABLE",
            "legacy_status": "FAILED" if failure else "SUCCESS", "status_reason": failure,
            "usage": {"llm_calls": scope.llm_calls, "tokens": scope.tokens, "usage_source": "step_total"},
            "model_descriptor": adapter_identity(adapter) if cap.model and adapter is not None else None, "artifact_sha256": artifact,
            "upstream_run_ids": made, "task_id": task_id, "task_step_id": sid})
        if not mine:
            return lost(f"{sid}: {failure}")
        if failure:
            return stop("BLOCKED" if ledger.blocked_by else "FAILED", f"{sid}: {failure}" + (f" (budget: {ledger.blocked_by})" if ledger.blocked_by else ""))
        executed.append(sid)
    return stop("COMPLETED", None)


def retry_task(store: StateStore, token: Optional[str], task_id: str, step_id: str, **kw: Any) -> dict[str, Any]:
    """Run again from a failed or blocked step. A failed run is never reused, so the step is executed again; finished steps are not."""
    task = load_task(store, task_id)
    a = _analyze(store, task_id, task["content"]).get(step_id)
    if a is None:
        raise KnowledgeError("STEP_UNKNOWN", f"{step_id} is not a step of {task_id}")
    if a["status"] in DONE:
        raise KnowledgeError("STEP_NOT_FAILED", f"{step_id} is already {a['status']}")
    return run_task(store, token, task_id, **kw)


# ------------------------------------------------------------------------------------------------ continue

def _resolve_parent(value: Any, outputs: dict[str, Any]) -> Any:
    if isinstance(value, dict) and "$parent_step" in value:
        sid = value["$parent_step"]
        if sid not in outputs:
            raise KnowledgeError("PARENT_STEP_NOT_DONE", f"step {sid} of the parent task has no finished output to continue from")
        return outputs[sid] if value.get("key") is None else outputs[sid].get(value["key"])
    if isinstance(value, dict):
        return {k: _resolve_parent(v, outputs) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_parent(v, outputs) for v in value]
    return value


def _parent_steps(value: Any) -> set[str]:
    if isinstance(value, dict):
        own = {value["$parent_step"]} if "$parent_step" in value else set()
        return own.union(*(_parent_steps(v) for v in value.values())) if value else own
    if isinstance(value, list):
        return set().union(*(_parent_steps(v) for v in value)) if value else set()
    return set()


def continue_task(store: StateStore, token: Optional[str], parent_id: str, *, goal: str, statement: str, params: Optional[dict] = None, **kw: Any) -> dict[str, Any]:
    """A new task that starts from what a finished task produced: `{"$parent_step": "S03", "key": "playbook_id"}` in `params` is replaced by that
    output, and the child records which revision of the parent it continues. Nothing is copied from the parent beyond those values."""
    parent = load_task(store, parent_id)
    analysis = _analyze(store, parent_id, parent["content"])
    outputs = {sid: _load_output(store, a["row"]) for sid, a in analysis.items() if a["status"] == "DONE" and a["row"]}
    resolved = _resolve_parent(params or {}, outputs)
    used = sorted(_parent_steps(params or {}))
    return start_task(store, token, goal=goal, statement=statement, params=resolved, parent={"task_id": parent_id, "revision": parent["revision"]},
                      continued_from={"task_id": parent_id, "revision": parent["revision"], "steps": used}, **kw)
