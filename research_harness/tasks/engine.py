"""The task engine (M5 WP3): create, plan, approve, run, decide, revise, retry, cancel, continue.

A task is a `ResearchTask` object in the project's own store (no parallel store). Step state is not kept on the object: it is derived from the
run registry (`task_step:*` rows carrying the task id), so the registry stays the single record of what ran (I5). The object changes only when
the plan, the budget, a researcher decision, the state or the lease changes; every change is a revision written with the revision the writer
read (`expected_head`), so two writers cannot overwrite each other.

Steps are `run` (a capability) or `decide` (a checkpoint only the researcher can clear). A completed step is reused when its fingerprint is
unchanged: the same capability, inputs, pinned objects (id, revision, digest) and upstream fingerprints. A decision is bound to the fingerprint
it was taken on: when what it decided changes, it no longer applies.
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from research_harness.core.canonical import canonical_json_hash
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
    """Per step: fingerprint, status and (when done) its output. Statuses: DONE, DECIDED, PENDING, FAILED, STALE, AWAITING_DECISION,
    STALE_DECISION, INPUT_MISSING, WAITING_ON_UPSTREAM."""
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
        upstream = {d: out[d]["fp"] for d in step["depends_on"]}
        ready = all(out[d]["status"] in DONE for d in step["depends_on"])
        info: dict[str, Any] = {"step": step, "ready": ready, "row": None, "resolved": None, "error": None}
        if step["kind"] == "decide":
            info["fp"] = _fingerprint(step, upstream, [])
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
        hit = next((r for r in mine if r["execution_status"] == "SUCCEEDED" and r["input_fingerprint"] == info["fp"]), None)
        if hit is not None:
            info["status"], info["row"] = "DONE", hit
            if step["model"] and model_identity is not None and hit.get("model_descriptor") != model_identity:
                info["status"] = "STALE"
                info["stale_reason"] = "the model is not the one that produced this step"
            else:
                outputs[sid] = _load_output(store, hit)
        elif info["error"]:
            info["status"] = "INPUT_MISSING"
        elif mine and mine[0]["input_fingerprint"] == info["fp"] and mine[0]["execution_status"] == "FAILED":
            info["status"], info["row"] = "FAILED", mine[0]
        elif any(r["execution_status"] == "SUCCEEDED" for r in mine):
            info["status"], info["stale_reason"] = "STALE", "an input, a pinned object or an upstream step changed since it ran"
        else:
            info["status"] = "PENDING" if ready else "WAITING_ON_UPSTREAM"
        out[sid] = info
    return out


def _spent(store: StateStore, task_id: str) -> dict[str, int]:
    with store.read() as conn:
        return workflow_runs.task_spent(conn, task_id)


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
                      "decided_by": (a.get("decision") or {}).get("by") if s["kind"] == "decide" else None})
    return {"task_id": task_id, "revision": task["revision"], "goal": c["goal"], "label": c["label"], "state": c["state"], "state_reason": c["state_reason"],
            "statement": c["statement"], "budget": c["budget"], "spent": _spent(store, task_id), "lease": c.get("lease"),
            "mandatory_checkpoints": c["plan"].get("mandatory_checkpoints", []), "steps": steps,
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

def decide(store: StateStore, token: Optional[str], task_id: str, step_id: str, rationale: str) -> dict[str, Any]:
    """The researcher clears a checkpoint (author or owner). The decision is bound to the step's current fingerprint."""
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
        resolved = a["resolved"] or {}
        objs = set(resolved.get("objects") or ([resolved["playbook"]] if resolved.get("playbook") else [])) or None
        waiting = [q for q in eca.queue(store) if q["state"] == "AWAITING_CONFIRMATION" and (objs is None or q["statement"]["object_id"] in objs)]
        if waiting:
            raise KnowledgeError("DECISION_PRECONDITION", f"{len(waiting)} proposed adjudication(s) still wait for your confirmation "
                                 "(research adjudicate sheet, then confirm-batch)", awaiting=[q["adjudication_id"] for q in waiting][:50])
        note = {"awaiting_confirmation": 0}
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
    """A call whose answer never arrived is charged at its reservation and recorded; it is never silently free."""
    ids = []
    for it in B.outstanding_intents(store, task_id):
        ids.append(workflow_runs.append_run(store, {
            "workflow": "task_call:unknown_outcome", "input_fingerprint": canonical_json_hash({"intent": it["_path"], "at": it["at"]}),
            "executor": "task_engine@1", "execution_status": "FAILED", "context_status": "PARTIAL", "assessment_status": "NOT_ASSESSED",
            "applicability": "APPLICABLE", "legacy_status": "FAILED", "status_reason": "unknown_outcome_charged_at_reservation",
            "usage": {"llm_calls": 1, "input_tokens": it["input_reserved"], "output_tokens": it["output_reserved"], "usage_source": "reserved"},
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


def run_task(store: StateStore, token: Optional[str], task_id: str, *, adapter: Any = None, adapter_factory: Optional[Callable[[], Any]] = None,
             until: Optional[str] = None, rerun_stale: bool = False, lease_ttl: int = LEASE_TTL, break_lease: bool = False) -> dict[str, Any]:
    """Run the next steps. Stops at the first checkpoint that waits for the researcher, at a failure, at a budget limit, or when done."""
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
    state = {"rev": rev}
    try:
        return _run_loop(store, token, task_id, state, holder, adapter, adapter_factory, until, rerun_stale, lease_ttl)
    except BaseException:
        try:
            cur = load_task(store, task_id)
            if cur["revision"] == state["rev"] and (cur["content"].get("lease") or {}).get("holder") == holder:
                _write(store, token, task_id, {**cur["content"], "lease": None, "state_reason": "interrupted"}, "task_interrupted",
                       expected_head=cur["revision"])
        except Exception:  # noqa: BLE001 - the original error is the one to report
            pass
        raise


def _finish(store: StateStore, token: Optional[str], task_id: str, state: dict, new_state: str, reason: Optional[str]) -> int:
    cur = load_task(store, task_id)
    rev = _write(store, token, task_id, _transition({**cur["content"], "lease": None}, new_state, reason), f"task_{new_state.lower()}", expected_head=state["rev"])
    state["rev"] = rev
    return rev


def _run_loop(store, token, task_id, state, holder, adapter, adapter_factory, until, rerun_stale, lease_ttl) -> dict[str, Any]:
    recovered = _recover_unknown_calls(store, task_id)
    task = load_task(store, task_id)
    b = task["content"]["budget"]
    spent = _spent(store, task_id)
    ledger = B.Ledger(b.get("max_tokens"), b.get("max_llm_calls"), spent["tokens"], spent["llm_calls"])
    executed: list[str] = []
    reused: list[str] = []
    result: dict[str, Any] = {"task_id": task_id, "executed": executed, "reused": reused, "recovered_unknown_calls": recovered}

    def stop(new_state: str, reason: Optional[str]) -> dict[str, Any]:
        result.update(state=new_state, reason=reason, revision=_finish(store, token, task_id, state, new_state, reason),
                      spent={"tokens": ledger.spent_tokens, "llm_calls": ledger.spent_calls})
        return result

    for step in task["content"]["plan"]["steps"]:
        cur = load_task(store, task_id)
        if cur["revision"] != state["rev"]:
            result.update(state=cur["content"]["state"], reason="the task was changed while it was running (cancelled, revised or replanned)",
                          spent={"tokens": ledger.spent_tokens, "llm_calls": ledger.spent_calls})
            return result
        lease = cur["content"]["lease"]
        if lease["expires_at"] < _iso(_now() + timedelta(seconds=lease_ttl / 2)):       # renew before it can lapse mid-step
            state["rev"] = _write(store, token, task_id, {**cur["content"], "lease": {**lease, "expires_at": _iso(_now() + timedelta(seconds=lease_ttl))}},
                                  "task_lease_renew", expected_head=cur["revision"])
        identity = adapter_identity(adapter) if adapter is not None else None
        a = _analyze(store, task_id, cur["content"], identity)[step["step_id"]]
        sid = step["step_id"]
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
            adapter = adapter_factory() if adapter_factory else None
            if adapter is None:
                return stop("BLOCKED", f"ADAPTER_REQUIRED: {sid} calls a model")
        failure: Optional[str] = None
        output: Any = None
        with workflow_runs.task_scope(task_id, sid) as scope:
            try:
                output = cap.run(Ctx(store, token, B.BudgetedAdapter(adapter, ledger, store, task_id, sid) if cap.model else None, task_id, sid), a["resolved"])
                failure = _failure_of(output)
            except Exception as exc:  # noqa: BLE001 - a failed step is recorded, not lost
                failure = f"{getattr(exc, 'code', type(exc).__name__)}: {getattr(exc, 'message', None) or exc}"
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


def _parent_steps(value: Any) -> set[str]:
    if isinstance(value, dict):
        own = {value["$parent_step"]} if "$parent_step" in value else set()
        return own.union(*(_parent_steps(v) for v in value.values())) if value else own
    if isinstance(value, list):
        return set().union(*(_parent_steps(v) for v in value)) if value else set()
    return set()
