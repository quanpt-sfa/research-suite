"""Read access to the workflow run registry: list, show, history, and field-level diffs between runs.

A run is found by its id, or by the digest of its stored output (`find_run_by_artifact`), without any cache
file. Outputs are verified against their content hash before being returned.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from research_harness.state import workflow_runs
from research_harness.state.store import StateStore
from research_harness.workflows.contracts import WorkflowOutput

_MAX_DIFF_ENTRIES = 200


def list_workflow_runs(store: StateStore, *, workflow: Optional[str] = None, research_question_id: Optional[str] = None,
                       legacy: Optional[bool] = None, limit: Optional[int] = None) -> list[dict[str, Any]]:
    with store.read() as conn:
        workflow_runs.require_registry(conn)
        return workflow_runs.list_runs(conn, workflow=workflow, research_question_id=research_question_id,
                                       legacy=legacy, limit=limit)


def get_workflow_run(store: StateStore, run_id: str) -> dict[str, Any]:
    with store.read() as conn:
        workflow_runs.require_registry(conn)
        row = workflow_runs.get_run(conn, run_id)
    if row is None:
        raise KeyError(f"WORKFLOW_RUN_NOT_FOUND: {run_id}")
    return row


def find_run_by_artifact(store: StateStore, artifact_sha256: str) -> Optional[dict[str, Any]]:
    with store.read() as conn:
        workflow_runs.require_registry(conn)
        return workflow_runs.find_by_artifact(conn, artifact_sha256)


def _load_verified(store: StateStore, sha: Optional[str], what: str, run_id: str) -> bytes:
    if not sha:
        raise LookupError(f"RUN_HAS_NO_{what.upper()}: {run_id}")
    if not store.artifacts.has(sha):
        raise LookupError(f"{what.upper()}_MISSING_FROM_STORE: {run_id} ({sha[:16]})")
    if not store.artifacts.verify(sha):
        raise ValueError(f"{what.upper()}_FAILED_VERIFICATION: {run_id} ({sha[:16]})")
    return store.artifacts.get_bytes(sha)


def load_workflow_output(store: StateStore, run_id: str) -> WorkflowOutput:
    row = get_workflow_run(store, run_id)
    return WorkflowOutput.from_dict(json.loads(_load_verified(store, row["artifact_sha256"], "artifact", run_id)))


def load_run_prompt(store: StateStore, run_id: str) -> Optional[dict[str, Any]]:
    """The exact request of a model-backed run (system prompt, user prompt, schema digest, token count), or None."""
    row = get_workflow_run(store, run_id)
    if not row["prompt_sha256"]:
        return None
    return json.loads(_load_verified(store, row["prompt_sha256"], "prompt", run_id))


def run_history(store: StateStore, run_id: str) -> list[dict[str, Any]]:
    """The run followed back through the runs it superseded (`parent_run_id`), newest first."""
    chain, seen = [], set()
    current: Optional[str] = run_id
    while current and current not in seen:
        seen.add(current)
        row = get_workflow_run(store, current)
        chain.append(row)
        current = row["parent_run_id"] or row["reused_from_run_id"]
    return chain


def diff_json(before: Any, after: Any, path: str = "$") -> list[dict[str, Any]]:
    """Field-level differences between two JSON values. Lists are compared element-wise when equally long;
    otherwise the whole list is reported as changed with both lengths."""
    out: list[dict[str, Any]] = []

    def walk(a: Any, b: Any, p: str) -> None:
        if len(out) >= _MAX_DIFF_ENTRIES or a == b:
            return
        if isinstance(a, dict) and isinstance(b, dict):
            for k in sorted(set(a) | set(b)):
                if k not in a:
                    out.append({"path": f"{p}.{k}", "change": "added", "after": b[k]})
                elif k not in b:
                    out.append({"path": f"{p}.{k}", "change": "removed", "before": a[k]})
                else:
                    walk(a[k], b[k], f"{p}.{k}")
        elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            for i, (x, y) in enumerate(zip(a, b)):
                walk(x, y, f"{p}[{i}]")
        else:
            entry: dict[str, Any] = {"path": p, "change": "changed", "before": a, "after": b}
            if isinstance(a, list) and isinstance(b, list):
                entry["before_length"], entry["after_length"] = len(a), len(b)
            out.append(entry)

    walk(before, after, path)
    return out


def diff_runs(store: StateStore, run_a: str, run_b: str) -> dict[str, Any]:
    """What changed between two runs: status axes, input revisions and the result itself."""
    a, b = get_workflow_run(store, run_a), get_workflow_run(store, run_b)
    out_a, out_b = load_workflow_output(store, run_a), load_workflow_output(store, run_b)
    axes_a, axes_b = out_a.axes.to_dict(), out_b.axes.to_dict()
    def revisions(row: dict[str, Any]) -> dict[str, list[int]]:
        by_object: dict[str, list[int]] = {}
        for i in row["input_revisions"]:
            by_object.setdefault(i["object_id"], []).append(i["revision"])
        return {o: sorted(r) for o, r in by_object.items()}

    ra, rb = revisions(a), revisions(b)
    head = lambda revs: max(revs) if revs else None  # noqa: E731  (a run may read a predecessor revision too)
    return {
        "run_a": run_a, "run_b": run_b, "workflow_a": a["workflow"], "workflow_b": b["workflow"],
        "legacy_a": a["legacy"], "legacy_b": b["legacy"],
        "status_changes": diff_json(axes_a, axes_b, "$.status_axes"),
        "input_changes": [{"object_id": o, "before": head(ra.get(o)), "after": head(rb.get(o)),
                           "before_revisions": ra.get(o, []), "after_revisions": rb.get(o, [])}
                          for o in sorted(set(ra) | set(rb)) if ra.get(o) != rb.get(o)],
        "result_changes": diff_json(out_a.result, out_b.result, "$.result"),
    }
