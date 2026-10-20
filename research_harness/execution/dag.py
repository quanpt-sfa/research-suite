"""Execution DAG of an analysis plan (plan v3, 3E). Standard library only.

`run_plan` runs the steps of an `AnalysisPlan` in topological order. For every step it hashes the inputs, the script and the
outputs, keeps the log in the content-addressed store, evaluates the step's **execution checks**, and appends one row to the
run registry (`workflow="execution"`, `plan_id`, `step_id`).

Three layers are kept apart (invariant I17):

1. **Execution checks** (exit code, files exist, expected columns, hashes, replication numbers within a declared tolerance):
   boolean, and the only thing that can stop the DAG. A failed step makes its dependants `BLOCKED(upstream_failed)`.
2. **Statistical diagnostics** (`diagnostics.py`): evidence with the source of each criterion. A concern is recorded and
   never blocks anything; no lack of concern is reported as a pass. Their signals are also written to the domain
   workflow's step state, where its `on_condition` rules can send the researcher back to an earlier step.
3. **Scientific interpretation** (`interpret_results`): claim effects, `PROVISIONAL` until the researcher confirms.

Each step that yields numbers also yields an `Analysis` and a `Result`. A `Result` whose step failed or was blocked is
`NOT_ASSESSED`, naming the step. A `Result` whose artifact hash matches the run's output is `VERIFIED_RUN`: an
execution-level fact that does not by itself raise its assessment above `PROVISIONAL`.

The commands in a plan run with the privileges of the user who runs it, never through a shell, so running a plan needs the
owner's token. R is optional: a missing `Rscript` is a failed step with an install hint, not a crash.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from research_harness import taxonomy
from research_harness.core.auth import authenticate
from research_harness.core.errors import AuthorizationError
from research_harness.execution.diagnostics import (
    OPS, DiagnosticSpecError, dig, divergence, evaluate_diagnostic, nonsignificance_reading)
from research_harness.knowledge.common import KnowledgeError, engine_origin, id_fragment, register_run
from research_harness.state import workflow_runs
from research_harness.state.store import StateStore
from research_harness.workflows.status import (
    Applicability, AssessmentStatus, ContextStatus, ExecutionStatus, Status, apply_caps)

EXECUTOR = "execution_dag@1"
_RESULT_PROVENANCE = ("VERIFIED_RUN", "AUTHOR_REPORTED", "NO_OUTPUT")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe(workdir: Path, rel: str) -> Path:
    p = (workdir / rel).resolve()
    if workdir.resolve() not in p.parents and p != workdir.resolve():
        raise KnowledgeError("PATH_OUTSIDE_WORKDIR", f"{rel!r} resolves outside the working directory")
    return p


def load_plan(store: StateStore, plan_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    plan = store.get_object(plan_id)
    if not plan or plan["object_type"] != "AnalysisPlan":
        raise KnowledgeError("PLAN_NOT_FOUND", f"{plan_id} is not an AnalysisPlan")
    return plan, list(plan["content"].get("steps") or [])


def topological_order(steps: list[dict[str, Any]]) -> list[str]:
    ids = [s["id"] for s in steps]
    deps = {s["id"]: list(s.get("depends_on_steps") or []) for s in steps}
    for sid, ds in deps.items():
        for d in ds:
            if d not in deps:
                raise KnowledgeError("PLAN_INVALID", f"step {sid} depends on unknown step {d}")
    order: list[str] = []
    done: set[str] = set()
    while len(order) < len(ids):
        ready = [i for i in ids if i not in done and all(d in done for d in deps[i])]
        if not ready:
            raise KnowledgeError("PLAN_CYCLE", "the plan's steps depend on each other in a cycle")
        order.append(ready[0])
        done.add(ready[0])
    return order


def validate_for_run(steps: list[dict[str, Any]]) -> None:
    """Refuse a malformed plan before anything runs (a diagnostic with a threshold that has no source, a bad check)."""
    topological_order(steps)
    for s in steps:
        for d in s.get("diagnostics") or []:
            evaluate_diagnostic(d, {})
        for c in s.get("execution_checks") or []:
            if c.get("kind") not in {"file_exists", "file_columns", "hash_equals", "numeric_within"}:
                raise KnowledgeError("PLAN_INVALID", f"step {s['id']}: unknown execution check {c.get('kind')!r}")
            if c["kind"] == "numeric_within" and not c.get("basis"):
                raise KnowledgeError("PLAN_INVALID", f"step {s['id']}: a numeric check needs the basis of its expected value and tolerance")


# --------------------------------------------------------------------------------------------------
# execution checks
# --------------------------------------------------------------------------------------------------

def run_checks(workdir: Path, step: dict[str, Any], exit_code: Optional[int]) -> list[dict[str, Any]]:
    checks = [{"kind": "exit_zero", "ok": exit_code == 0, "detail": f"exit code {exit_code}"}]
    for c in step.get("execution_checks") or []:
        kind = c["kind"]
        try:
            path = _safe(workdir, c.get("path") or c.get("file", ""))
            if kind == "file_exists":
                ok, detail = path.is_file(), str(c["path"])
            elif kind == "file_columns":
                import csv
                with open(path, newline="", encoding="utf-8") as f:
                    header = next(csv.reader(f), [])
                missing = [col for col in c["columns"] if col not in header]
                ok, detail = not missing, "missing columns: " + ", ".join(missing) if missing else "columns present"
            elif kind == "hash_equals":
                got = sha256_file(path)
                ok, detail = got == c["sha256"], f"sha256 {got[:16]}"
            else:  # numeric_within
                value = dig(json.loads(path.read_text(encoding="utf-8")), c["key"])
                ok = isinstance(value, (int, float)) and abs(value - c["expected"]) <= c.get("tolerance", 0)
                detail = f"{c['key']} = {value}, expected {c['expected']} ± {c.get('tolerance', 0)}"
        except (OSError, ValueError, KeyError, StopIteration, KnowledgeError) as exc:
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        checks.append({"kind": kind, "ok": bool(ok), "detail": detail, "basis": c.get("basis")})
    return checks


# --------------------------------------------------------------------------------------------------
# running one step
# --------------------------------------------------------------------------------------------------

def _resolve_command(command: list[str]) -> tuple[Optional[list[str]], Optional[str]]:
    exe = command[0]
    if exe in {"python", "python3"}:
        return [sys.executable, *command[1:]], None
    found = shutil.which(exe)
    if found is None:
        hint = " Install R and put Rscript on PATH, or run the step by another route." if exe.lower().startswith("rscript") else ""
        return None, f"{exe} not found on PATH.{hint}"
    return [found, *command[1:]], None


def _execute(store: StateStore, workdir: Path, step: dict[str, Any], timeout: int) -> dict[str, Any]:
    rec: dict[str, Any] = {"step": step["id"], "command": step.get("command"), "input_hashes": {}, "script_hashes": {}, "output_hashes": {},
                           "exit_code": None, "log_sha256": None, "metrics": {}, "reason": None}
    for rel in step.get("inputs") or []:
        p = _safe(workdir, rel)
        if not p.is_file():
            rec["reason"] = f"missing_input:{rel}"
            rec["checks"] = [{"kind": "inputs_exist", "ok": False, "detail": f"{rel} is not a file"}]
            return rec
        rec["input_hashes"][rel] = sha256_file(p)
    for arg in (step["command"][1:] if step.get("command") else []):
        try:
            p = _safe(workdir, arg)
        except KnowledgeError:
            continue
        if p.is_file():
            rec["script_hashes"][arg] = sha256_file(p)
    argv, err = _resolve_command(step["command"])
    if argv is None:
        rec["reason"] = "missing_runtime"
        rec["log_sha256"] = store.artifacts.put_bytes(err.encode("utf-8"))  # type: ignore[union-attr]
        rec["checks"] = [{"kind": "runtime_available", "ok": False, "detail": err}]
        return rec
    try:
        proc = subprocess.run(argv, cwd=str(workdir), capture_output=True, timeout=timeout, shell=False)
        rec["exit_code"] = proc.returncode
        log = proc.stdout + (b"\n--- stderr ---\n" + proc.stderr if proc.stderr else b"")
    except subprocess.TimeoutExpired as exc:
        rec["reason"] = "timeout"
        log = (exc.stdout or b"") + b"\n--- timed out ---"
    except OSError as exc:
        rec["reason"] = "exception"
        log = str(exc).encode("utf-8")
    rec["log_sha256"] = store.artifacts.put_bytes(log)
    for rel in step.get("outputs") or []:
        p = _safe(workdir, rel)
        if p.is_file():
            rec["output_hashes"][rel] = sha256_file(p)
    mf = step.get("metrics_file") or f"{step['id']}.metrics.json"
    mp = _safe(workdir, mf)
    if mp.is_file():
        rec["metrics_file"] = mf
        rec["metrics_sha256"] = store.artifacts.put_bytes(mp.read_bytes())
        rec["output_hashes"].setdefault(mf, rec["metrics_sha256"])
        try:
            rec["metrics"] = json.loads(mp.read_text(encoding="utf-8"))
        except ValueError:
            rec["metrics"] = {}
            rec["reason"] = rec["reason"] or "invalid_output"
    rec["checks"] = run_checks(workdir, step, rec["exit_code"])
    return rec


# --------------------------------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------------------------------

def _latest_states(store: StateStore, plan_id: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    with store.read() as conn:
        rows = conn.execute("SELECT * FROM workflow_runs WHERE workflow='execution' AND plan_id=? ORDER BY rowid", (plan_id,)).fetchall()
        for r in rows:
            d = workflow_runs._decode(r)
            art = {}
            if d["artifact_sha256"] and store.artifacts.has(d["artifact_sha256"]):
                art = json.loads(store.artifacts.get_bytes(d["artifact_sha256"]).decode("utf-8"))
            out[d["step_id"]] = {"execution": d["execution_status"], "run_id": d["run_id"], "metrics": art.get("metrics") or {},
                                 "artifact": art, "assessment": d["assessment_status"], "reason": d["status_reason"]}
    return out


def _result_objects(store: StateStore, token: Optional[str], plan: dict[str, Any], step: dict[str, Any], rec: dict[str, Any], execution: str,
                    run_id: str, blocked_by: list[str], upstream_assessments: list[AssessmentStatus], diagnostics: list[dict[str, Any]],
                    divergences: list[dict[str, Any]], nonsig: Optional[dict[str, Any]]) -> dict[str, str]:
    frag = f"{id_fragment(plan['object_id'], 24)}_{id_fragment(step['id'], 24)}"
    aid, rid = f"AN_{frag}", f"RES_{frag}"
    ok = execution == ExecutionStatus.SUCCEEDED.value
    assessment = apply_caps(AssessmentStatus.PROVISIONAL, execution=ExecutionStatus(execution), generated_by="deterministic:execution_dag@1",
                            upstream=upstream_assessments)
    axes = Status(ExecutionStatus(execution), ContextStatus.COMPLETE if ok else ContextStatus.INSUFFICIENT, assessment, Applicability.APPLICABLE,
                  reason=("upstream_failed" if blocked_by else rec.get("reason")), omissions=tuple(f"step:{b}" for b in blocked_by))
    metrics_text = ", ".join(f"{k}={v}" for k, v in list((rec.get("metrics") or {}).items())[:8] if isinstance(v, (int, float, str))) or "no metrics"
    summary = (f"Step {step['id']} of {plan['object_id']}: {metrics_text}." if ok
               else f"Step {step['id']} of {plan['object_id']} did not produce a result ({'blocked by ' + ', '.join(blocked_by) if blocked_by else execution}).")
    result = {"label": f"Result of step {step['id']}", "summary": summary, "plan_id": plan["object_id"], "step_id": step["id"], "run_id": run_id,
              "execution": execution, "metrics": rec.get("metrics") or {}, "output_hashes": rec.get("output_hashes") or {},
              "provenance": "NO_OUTPUT", "axes": axes.to_dict(), "blocked_by": blocked_by,
              "diagnostics": [{"diagnostic": d["diagnostic"], "signal": d["signal"]} for d in diagnostics], "divergences": divergences}
    if ok and rec.get("metrics_sha256"):
        result["artifact_sha256"] = rec["metrics_sha256"]
        result["provenance"] = "VERIFIED_RUN" if store.artifacts.verify(rec["metrics_sha256"]) else "AUTHOR_REPORTED"
    if nonsig is not None:
        result["nonsignificance"] = nonsig
    with store.write_tx(token=token, require_roles=("owner",)) as tx:
        pl_rev = tx.head(plan["object_id"])["head_revision"]
        tx.add_revision(object_id=aid, object_type="Analysis", edges=[("depends_on", plan["object_id"], pl_rev, {"origin": "execution"})],
                        content={"label": f"Analysis step {step['id']}", "method_family": step.get("method_family", "") or "", "plan_id": plan["object_id"],
                                 "step_id": step["id"]}, origin=engine_origin("execution_dag", tx.actor_id))
        edges = [("estimated_with", aid, tx.head(aid)["head_revision"], {"origin": "execution"})]
        for dep in step.get("depends_on_steps") or []:
            dh = tx.head(f"RES_{id_fragment(plan['object_id'], 24)}_{id_fragment(dep, 24)}")
            if dh:
                edges.append(("depends_on", f"RES_{id_fragment(plan['object_id'], 24)}_{id_fragment(dep, 24)}", dh["head_revision"], {"origin": "execution"}))
        tx.add_revision(object_id=rid, object_type="Result", edges=edges, content=result, origin=engine_origin("execution_dag", tx.actor_id))
    return {"analysis_id": aid, "result_id": rid, "assessment": assessment.value, "provenance": result["provenance"]}


def run_plan(store: StateStore, token: Optional[str], plan_id: str, workdir: Path | str, *, only: Optional[str] = None,
             from_step: Optional[str] = None, timeout: int = 600, domain_spec: Any = None) -> dict[str, Any]:
    """Run the steps of a plan (see the module docstring). Needs the owner's token: the plan's commands execute on this machine."""
    with store.read() as conn:
        actor = authenticate(conn, token)
    if actor is None or not actor.has_role("owner"):
        raise AuthorizationError("ROLE_REQUIRED", "running a plan executes its commands on this machine and needs the owner's token")
    workdir = Path(workdir).resolve()
    if not workdir.is_dir():
        raise KnowledgeError("WORKDIR_MISSING", f"{workdir} is not a directory")
    plan, steps = load_plan(store, plan_id)
    validate_for_run(steps)
    order = topological_order(steps)
    by_id = {s["id"]: s for s in steps}
    for name in (only, from_step):
        if name and name not in by_id:
            raise KnowledgeError("STEP_UNKNOWN", f"{name} is not a step of {plan_id}")
    selected = [only] if only else order[order.index(from_step):] if from_step else order
    states = _latest_states(store, plan_id)
    declared = plan["content"].get("declared") or {}
    out: dict[str, dict[str, Any]] = {}
    for sid in selected:
        step = by_id[sid]
        if step["fidelity"] == "omitted":
            rid = register_run(store, workflow="execution", fingerprint=hashlib.sha256(f"{plan_id}:{sid}:omitted".encode()).hexdigest(), executor=EXECUTOR,
                               result={"step": sid, "omitted": step.get("omit_reason")}, execution="NOT_RUN", assessment="NOT_ASSESSED",
                               reason="not_applicable", applicability="NOT_APPLICABLE", plan_id=plan_id, step_id=sid)
            states[sid] = {"execution": "NOT_RUN", "run_id": rid, "metrics": {}, "artifact": {}}
            out[sid] = {"execution": "NOT_RUN", "run_id": rid, "reason": "omitted"}
            continue
        deps = list(step.get("depends_on_steps") or [])
        # a dependency that is omitted is replaced by the step named in `replaced_by`; the dependant waits for that one instead
        deps = [by_id[d].get("replaced_by", d) if by_id[d]["fidelity"] == "omitted" else d for d in deps
                if not (by_id[d]["fidelity"] == "omitted" and not by_id[d].get("replaced_by"))]   # an omitted step with no replacement waits for nothing
        blocked = [d for d in deps if (states.get(d) or {}).get("execution") != "SUCCEEDED"]
        if blocked:
            rec = {"step": sid, "blocked_by": blocked, "metrics": {}, "output_hashes": {}, "checks": []}
            rid = register_run(store, workflow="execution", fingerprint=hashlib.sha256(f"{plan_id}:{sid}:blocked".encode()).hexdigest(), executor=EXECUTOR,
                               result=rec, execution="BLOCKED", context="INSUFFICIENT", assessment="NOT_ASSESSED", reason="upstream_failed", plan_id=plan_id, step_id=sid)
            states[sid] = {"execution": "BLOCKED", "run_id": rid, "metrics": {}, "artifact": rec}
            objs = _result_objects(store, token, plan, step, rec, "BLOCKED", rid, blocked, [], [], [], None) if step.get("produces_result") else {}
            if domain_spec is not None and step.get("domain_step"):
                from research_harness.workflows.domain_engine import record_step_run
                record_step_run(store, domain_spec, step["domain_step"], execution="BLOCKED", assessment="NOT_ASSESSED")
            out[sid] = {"execution": "BLOCKED", "run_id": rid, "blocked_by": blocked, **objs}
            continue
        rec = _execute(store, workdir, step, timeout)
        ok = all(c["ok"] for c in rec["checks"]) and not rec.get("reason")
        diagnostics: list[dict[str, Any]] = []
        divergences: list[dict[str, Any]] = []
        nonsig = None
        if ok:
            for dspec in step.get("diagnostics") or []:
                diagnostics.append(evaluate_diagnostic(dspec, rec["metrics"]))
            rb = step.get("robustness")
            if rb:
                divergences.append(divergence((states.get(rb["baseline_step"]) or {}).get("metrics") or {}, rec["metrics"], rb))
            ns = step.get("nonsignificance")
            if ns:
                vals = {k: dig(rec["metrics"], ns[f"{k}_key"]) for k in ("p_value", "ci_low", "ci_high", "mde", "equivalence_p") if ns.get(f"{k}_key")}
                concern = any(d["signal"] == "CONCERN_INDICATED" and d["diagnostic"] == ns.get("measurement_diagnostic") for d in diagnostics)
                nonsig = nonsignificance_reading(vals, declared, measurement_concern=concern)
        rec.update(diagnostics=diagnostics, divergences=divergences, nonsignificance=nonsig)
        execution = "SUCCEEDED" if ok else "FAILED"
        reason = None if ok else (rec.get("reason") or "exception")
        run_id = register_run(store, workflow="execution", fingerprint=hashlib.sha256(json.dumps(
            {"plan": plan_id, "step": sid, "inputs": rec["input_hashes"], "scripts": rec["script_hashes"]}, sort_keys=True).encode()).hexdigest(),
            executor=EXECUTOR, result=rec, execution=execution, context="COMPLETE" if ok else "PARTIAL",
            assessment="PROVISIONAL" if ok else "NOT_ASSESSED", reason=reason, plan_id=plan_id, step_id=sid,
            usage={"llm_calls": 0, "usage_source": "not_available"})
        states[sid] = {"execution": execution, "run_id": run_id, "metrics": rec["metrics"], "artifact": rec}
        ups = [AssessmentStatus(states[d]["assessment"]) for d in deps if states.get(d, {}).get("assessment")]
        objs = {}
        if step.get("produces_result") or (ok and rec["metrics"]):
            objs = _result_objects(store, token, plan, step, rec, execution, run_id, [], ups, diagnostics, divergences, nonsig)
            states[sid]["assessment"] = objs["assessment"]
        if domain_spec is not None and step.get("domain_step"):
            from research_harness.workflows.domain_engine import record_step_run
            record_step_run(store, domain_spec, step["domain_step"], execution=execution, assessment="PROVISIONAL" if ok else "NOT_ASSESSED",
                            diagnostic_signals={d["diagnostic"]: d["signal"] for d in diagnostics})
        out[sid] = {"execution": execution, "run_id": run_id, "failed_checks": [c for c in rec["checks"] if not c["ok"]],
                    "signals": {d["diagnostic"]: d["signal"] for d in diagnostics}, "divergences": divergences, **objs}
    return {"plan": plan_id, "order": order, "ran": selected, "steps": out}


def plan_execution_summary(store: StateStore, plan_id: str) -> dict[str, Any]:
    """The latest run of each step: status, failed checks, diagnostic signals, divergences, and what blocked it."""
    st = _latest_states(store, plan_id)
    return {sid: {"execution": s["execution"], "run_id": s["run_id"], "reason": s.get("reason"),
                  "failed_checks": [c for c in (s["artifact"].get("checks") or []) if not c["ok"]],
                  "diagnostics": [{"diagnostic": d["diagnostic"], "signal": d["signal"], "criteria_disagree": d.get("criteria_disagree")}
                                  for d in s["artifact"].get("diagnostics") or []],
                  "divergences": s["artifact"].get("divergences") or [], "blocked_by": s["artifact"].get("blocked_by") or []}
            for sid, s in st.items()}


# --------------------------------------------------------------------------------------------------
# results reported by the author, and claim effects
# --------------------------------------------------------------------------------------------------

def result_provenance(store: StateStore, result_id: str) -> str:
    """`VERIFIED_RUN` only if the Result's artifact hash equals the output hash recorded by a run of its step (an
    execution-level fact; the Result's assessment is a separate matter). Otherwise `AUTHOR_REPORTED`."""
    res = store.get_object(result_id)
    if not res or res["object_type"] != "Result":
        raise KnowledgeError("RESULT_NOT_FOUND", f"{result_id} is not a Result")
    c = res["content"]
    sha, plan_id, step_id = c.get("artifact_sha256"), c.get("plan_id"), c.get("step_id")
    if not (sha and plan_id and step_id and store.artifacts.has(sha) and store.artifacts.verify(sha)):
        return "AUTHOR_REPORTED"
    run = (_latest_states(store, plan_id).get(step_id) or {}).get("artifact") or {}
    return "VERIFIED_RUN" if sha in (run.get("output_hashes") or {}).values() else "AUTHOR_REPORTED"


def add_reported_result(store: StateStore, token: Optional[str], *, result_id: str, plan_id: str, step_id: str, summary: str,
                        metrics: Optional[dict[str, Any]] = None, artifact_sha256: Optional[str] = None) -> dict[str, Any]:
    """Record a result the author reports. It is `VERIFIED_RUN` only if `artifact_sha256` matches a run of that step."""
    content = {"label": f"Reported result of {step_id}", "summary": summary, "plan_id": plan_id, "step_id": step_id, "metrics": metrics or {},
               "execution": "UNKNOWN", "provenance": "AUTHOR_REPORTED", "axes": Status(ExecutionStatus.NOT_RUN, ContextStatus.PARTIAL,
               AssessmentStatus.PROVISIONAL, Applicability.APPLICABLE, reason="author_reported").to_dict()}
    if artifact_sha256:
        content["artifact_sha256"] = artifact_sha256
    with store.write_tx(token=token, require_roles=("importer", "owner", "author")) as tx:
        tx.add_revision(object_id=result_id, object_type="Result", content=content, edges=[], origin=engine_origin("result_add", tx.actor_id))
    prov = result_provenance(store, result_id)
    if prov != "AUTHOR_REPORTED":
        with store.write_tx(token=token, require_roles=("importer", "owner", "author")) as tx:
            cur = tx.get(result_id)
            tx.add_revision(object_id=result_id, object_type="Result", content={**cur["content"], "provenance": prov}, edges=[],
                            origin=engine_origin("result_add", tx.actor_id))
    return {"result_id": result_id, "provenance": prov}


def interpret_results(store: StateStore, token: Optional[str], plan_id: str, effects: list[dict[str, Any]], *, interpretation_id: str) -> dict[str, Any]:
    """Record claim effects (scientific interpretation). Each effect `{claim_id, effect, reasoning, depends_on: [step ids],
    generated_by, evidence_kind}` is `PROVISIONAL` at most; an effect resting on a step that did not succeed is `NOT_ASSESSED` and says so."""
    states = _latest_states(store, plan_id)
    out_effects = []
    deps_all: set[str] = set()
    for e in effects:
        taxonomy.validate_term("claim_effect", e["effect"])
        claim = store.get_object(e["claim_id"])
        if not claim or claim["object_type"] != "Claim":
            raise KnowledgeError("CLAIM_NOT_FOUND", f"{e['claim_id']} is not a Claim")
        if not e.get("reasoning"):
            raise KnowledgeError("REASONING_REQUIRED", f"effect on {e['claim_id']} needs its reasoning")
        steps = e.get("depends_on") or []
        bad = [s for s in steps if (states.get(s) or {}).get("execution") != "SUCCEEDED"]
        gen = e.get("generated_by", "researcher")
        effect, assessment, why = e["effect"], apply_caps(AssessmentStatus.PROVISIONAL, generated_by=gen), None
        if bad:
            effect, assessment, why = "NOT_ASSESSED", AssessmentStatus.NOT_ASSESSED, "depends on step(s) that did not succeed: " + ", ".join(bad)
        deps_all.update(steps)
        out_effects.append({"claim_id": e["claim_id"], "effect": effect, "proposed_effect": e["effect"], "reasoning": e["reasoning"], "generated_by": gen,
                            "depends_on": steps, "runs": [states[s]["run_id"] for s in steps if s in states], "evidence_kind": e.get("evidence_kind", "judgement"),
                            "assessment": assessment.value, "researcher_confirmed": False, "note": why, "reverses": bool(e.get("reverses")),
                            "contradiction": e.get("contradiction")})
    frag = id_fragment(plan_id, 24)
    edges = []
    with store.write_tx(token=token, require_roles=("importer", "owner", "author")) as tx:
        for s in sorted(deps_all):
            h = tx.head(f"RES_{frag}_{id_fragment(s, 24)}")
            if h:
                edges.append(("depends_on", h["object_id"] if "object_id" in h else f"RES_{frag}_{id_fragment(s, 24)}", h["head_revision"], {"origin": "interpretation"}))
        for cid in sorted({e["claim_id"] for e in out_effects}):
            edges.append(("depends_on", cid, tx.head(cid)["head_revision"], {"origin": "interpretation"}))
        res = tx.add_revision(object_id=interpretation_id, object_type="Result", edges=edges, origin=engine_origin("interpret_results", tx.actor_id),
                              content={"label": f"Interpretation of {plan_id}", "summary": f"{len(out_effects)} claim effect(s) for {plan_id}.", "plan_id": plan_id,
                                       "layer": "scientific_interpretation", "claim_effects": out_effects, "provenance": "NO_OUTPUT"})
    return {"interpretation_id": interpretation_id, "revision": res["revision"], "effects": out_effects}


def confirm_effect(store: StateStore, token: Optional[str], interpretation_id: str, index: int, *, rationale: str) -> dict[str, Any]:
    """The researcher confirms a claim effect (author or owner): only then does it reach VERIFIED_WITHIN_SCOPE."""
    if len((rationale or "").strip()) < 10:
        raise KnowledgeError("RATIONALE_REQUIRED", "give the reason (10 characters at least)")
    with store.write_tx(token=token, require_roles=("author", "owner")) as tx:
        cur = tx.get(interpretation_id)
        effs = list(cur["content"]["claim_effects"])
        if not 0 <= index < len(effs):
            raise KnowledgeError("ENTRY_UNKNOWN", f"no claim effect {index}")
        if effs[index]["effect"] == "NOT_ASSESSED":
            raise KnowledgeError("EFFECT_NOT_ASSESSED", "an effect that rests on a failed step cannot be confirmed; rerun the step first")
        effs[index] = {**effs[index], "researcher_confirmed": True, "researcher_rationale": rationale.strip(), "generated_by": "researcher",
                       "assessment": apply_caps(AssessmentStatus.VERIFIED_WITHIN_SCOPE, generated_by="researcher", researcher_confirmed=True).value}
        rows = tx.conn.execute("SELECT relation,target_id,target_revision,provenance FROM edges WHERE source_id=? AND source_revision=?",
                               (interpretation_id, cur["revision"])).fetchall()
        res = tx.add_revision(object_id=interpretation_id, object_type="Result", content={**cur["content"], "claim_effects": effs},
                              edges=[(r[0], r[1], r[2], json.loads(r[3])) for r in rows], origin=engine_origin("confirm_effect", tx.actor_id))
    return {"interpretation_id": interpretation_id, "revision": res["revision"], "effect": effs[index]}
