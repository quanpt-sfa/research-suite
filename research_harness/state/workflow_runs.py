"""Append-only registry of workflow runs (schema 9.0.3).

Every execution of a workflow (including refusals, failures, invalid model answers and reuses) is one row. A row
points at the output stored in the content-addressed store, at the exact request that was (or would have been)
sent, and at the context manifest, together with the four status axes, the input fingerprint and the links between
runs (`parent_run_id`: the previous run of the same question; `reused_from_run_id`; `upstream_run_ids`).

The table is derived data. It is not part of `compute_state_digest`, rows are inserted without bumping the state
revision, and database triggers forbid UPDATE and DELETE. Outputs of the 9.1.1 JSON cache are imported once, at
upgrade, as `legacy` rows: kept as history, never reusable, each tagged with the defects the audit found.
"""
from __future__ import annotations

import contextvars
import json
import shutil
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

from research_harness.core.errors import HarnessError

JSON_COLUMNS = ("focus_claims", "input_revisions", "model_descriptor", "usage", "upstream_run_ids",
                "playbook_pins", "journal_policy_pins", "known_defects")
_COLUMNS = (
    "run_id", "workflow", "created_at", "state_revision", "research_question_id", "focus_claims", "input_fingerprint",
    "input_revisions", "skill_digest", "executor", "model_descriptor", "context_manifest_sha256", "prompt_sha256",
    "artifact_sha256", "execution_status", "context_status", "assessment_status", "applicability", "legacy_status",
    "status_reason", "usage", "reused_from_run_id", "parent_run_id", "upstream_run_ids", "plan_id", "step_id",
    "domain_step_id", "domain_workflow_pin", "playbook_pins", "journal_policy_pins", "closure_digest", "legacy",
    "known_defects", "task_id", "task_step_id",
)
_REQUIRED = ("workflow", "input_fingerprint", "executor", "execution_status", "context_status", "assessment_status",
             "applicability", "legacy_status")
_JSON_DEFAULTS = {"focus_claims": [], "input_revisions": [], "model_descriptor": None, "usage": {}, "upstream_run_ids": [],
                  "playbook_pins": [], "journal_policy_pins": [], "known_defects": []}

# Defects the independent audit of 9.1.1 (commit 9728134) found in the outputs of each workflow.
_COMMON_DEFECTS = ["status_lost_on_reuse", "unstable_cache_fingerprint"]
LEGACY_KNOWN_DEFECTS: dict[str, list[str]] = {
    "contribution": ["lexical_verdicts", "competing_sources_not_read", "claims_not_scoped_to_question", *_COMMON_DEFECTS],
    "literature": ["lexical_verdicts", "loaded_full_text_not_read", "empty_run_reported_success", *_COMMON_DEFECTS],
    "methods": ["fixed_econometric_template", "researcher_methods_ignored", *_COMMON_DEFECTS],
    "results": ["self_attested_verification", "author_summary_listed_as_supported", *_COMMON_DEFECTS],
    "discussion": ["lexical_verdicts", "templated_interpretation", "empty_run_reported_success", *_COMMON_DEFECTS],
}


def new_run_id() -> str:
    return "RUN_" + uuid.uuid4().hex[:16].upper()


def has_registry(conn: Any) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_runs'").fetchone() is not None


def require_registry(conn: Any) -> None:
    if not has_registry(conn):
        raise HarnessError(
            "SCHEMA_UPGRADE_REQUIRED",
            "this project's database predates the workflow run registry (schema 9.0.3); "
            "run `research project upgrade-schema` with the owner token")


def _decode(row: Any) -> dict[str, Any]:
    d = dict(row)
    for col in JSON_COLUMNS:
        if d.get(col) is not None:
            d[col] = json.loads(d[col])
    d["legacy"] = bool(d["legacy"])
    return d


class TaskScope:
    """While a task step runs, every run row inserted by this thread belongs to the step: `task_id` and `task_step_id` are filled in, and the
    ids and usage of those runs are collected so the step can be summarised, charged against the budget and linked to its runs."""

    def __init__(self, task_id: str, step_id: str):
        self.task_id, self.step_id = task_id, step_id
        self.run_ids: list[str] = []
        self.llm_calls = 0
        self.tokens = 0


_TASK_SCOPE: contextvars.ContextVar = contextvars.ContextVar("research_task_scope", default=None)


@contextmanager
def task_scope(task_id: str, step_id: str):
    scope = TaskScope(task_id, step_id)
    token = _TASK_SCOPE.set(scope)
    try:
        yield scope
    finally:
        _TASK_SCOPE.reset(token)


def has_task_columns(conn: Any) -> bool:
    return any(r[1] == "task_id" for r in conn.execute("PRAGMA table_info(workflow_runs)"))


def tokens_of(usage: Optional[dict]) -> int:
    u = usage or {}
    return int(u.get("input_tokens") or 0) + int(u.get("output_tokens") or 0)


def task_spent(conn: Any, task_id: str) -> dict[str, int]:
    """Model calls and tokens charged to a task: every run row carrying its id except the step summaries (which repeat their runs)."""
    calls = tokens = 0
    for (usage,) in conn.execute("SELECT usage FROM workflow_runs WHERE task_id=? AND workflow NOT LIKE 'task_step:%'", (task_id,)):
        u = json.loads(usage or "{}")
        calls += int(u.get("llm_calls") or 0)
        tokens += tokens_of(u)
    return {"llm_calls": calls, "tokens": tokens}


def insert_run(conn: Any, row: dict[str, Any], *, state_revision: int) -> str:
    """Insert one run row on `conn` (the caller owns the transaction). Returns the run id."""
    from research_harness.workflows.status import Applicability, AssessmentStatus, ContextStatus, ExecutionStatus

    for key in _REQUIRED:
        if not row.get(key):
            raise ValueError(f"workflow run is missing required field '{key}'")
    unknown = set(row) - set(_COLUMNS)
    if unknown:
        raise ValueError(f"unknown workflow_runs columns: {sorted(unknown)}")
    ExecutionStatus(row["execution_status"]), ContextStatus(row["context_status"])
    AssessmentStatus(row["assessment_status"]), Applicability(row["applicability"])

    values = dict(row)
    scope = _TASK_SCOPE.get()
    if scope is not None and not values.get("task_id"):
        if not has_task_columns(conn):
            raise HarnessError("SCHEMA_UPGRADE_REQUIRED", "this project's database predates task support (schema 9.0.4); "
                               "run `research project upgrade-schema` with the owner token")
        values["task_id"], values["task_step_id"] = scope.task_id, scope.step_id
    values.setdefault("run_id", new_run_id())
    values.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    values["state_revision"] = state_revision
    values["legacy"] = 1 if values.get("legacy") else 0
    for col, default in _JSON_DEFAULTS.items():
        value = values.get(col, default)
        values[col] = None if (value is None and col == "model_descriptor") else json.dumps(value, sort_keys=True)
    cols = [c for c in _COLUMNS if c in values]
    conn.execute(f"INSERT INTO workflow_runs({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                 [values[c] for c in cols])
    if scope is not None and values.get("task_id") == scope.task_id:
        scope.run_ids.append(values["run_id"])
        scope.llm_calls += int((json.loads(values["usage"]) if isinstance(values["usage"], str) else values["usage"]).get("llm_calls") or 0)
        scope.tokens += tokens_of(json.loads(values["usage"]) if isinstance(values["usage"], str) else values["usage"])
    return values["run_id"]


def insert_run_tx(tx: Any, row: dict[str, Any]) -> str:
    """Insert a run row inside an open write transaction, so a run and the objects it produced commit together or not at all
    (I22: no SUCCEEDED run without its outputs). The row carries the state revision this transaction creates, if it changes state."""
    require_registry(tx.conn)
    return insert_run(tx.conn, row, state_revision=tx.revision if tx.changed else tx.base_revision)


def append_run(store: Any, row: dict[str, Any]) -> str:
    """Insert a run row in its own short transaction (no state-revision bump, no token: derived data)."""
    conn = store.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            require_registry(conn)
            run_id = insert_run(conn, row, state_revision=store.current_revision(conn))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        return run_id
    finally:
        conn.close()


def get_run(conn: Any, run_id: str) -> Optional[dict[str, Any]]:
    r = conn.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
    return _decode(r) if r else None


def list_runs(conn: Any, *, workflow: Optional[str] = None, research_question_id: Optional[str] = None,
              legacy: Optional[bool] = None, limit: Optional[int] = None) -> list[dict[str, Any]]:
    clauses, args = [], []
    if workflow:
        clauses.append("workflow=?"); args.append(workflow)
    if research_question_id:
        clauses.append("research_question_id=?"); args.append(research_question_id)
    if legacy is not None:
        clauses.append("legacy=?"); args.append(1 if legacy else 0)
    sql = "SELECT * FROM workflow_runs" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY rowid DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [_decode(r) for r in conn.execute(sql, args)]


def latest_reusable(conn: Any, workflow: str, fingerprint: str, reusable_statuses: Sequence[str]) -> Optional[dict[str, Any]]:
    """The most recent original (non-reuse, non-legacy) run with this fingerprint that may stand in for a re-run:
    it succeeded, its context was not insufficient, and its combined status is one of `reusable_statuses`."""
    marks = ",".join("?" * len(reusable_statuses))
    r = conn.execute(
        "SELECT * FROM workflow_runs WHERE workflow=? AND input_fingerprint=? AND legacy=0 AND reused_from_run_id IS NULL "
        f"AND execution_status='SUCCEEDED' AND context_status!='INSUFFICIENT' AND legacy_status IN ({marks}) "
        "ORDER BY rowid DESC LIMIT 1", [workflow, fingerprint, *reusable_statuses]).fetchone()
    return _decode(r) if r else None


def previous_run_id(conn: Any, workflow: str, research_question_id: Optional[str], focus_claims: Sequence[str]) -> Optional[str]:
    """The run this one supersedes: the latest original run of the same workflow and question for the same focus
    (a legacy run matches any focus: the 9.1.1 cache did not record it)."""
    r = conn.execute(
        "SELECT run_id FROM workflow_runs WHERE workflow=? AND research_question_id IS ? AND reused_from_run_id IS NULL "
        "AND (focus_claims=? OR legacy=1) ORDER BY rowid DESC LIMIT 1",
        (workflow, research_question_id, json.dumps(sorted(focus_claims)))).fetchone()
    return r[0] if r else None


def find_by_artifact(conn: Any, artifact_sha256: str) -> Optional[dict[str, Any]]:
    r = conn.execute("SELECT * FROM workflow_runs WHERE artifact_sha256=? ORDER BY rowid LIMIT 1", (artifact_sha256,)).fetchone()
    return _decode(r) if r else None


def artifact_digests(conn: Any) -> set[str]:
    """Every CAS digest the registry refers to (so `StateStore.recover()` does not call them orphans)."""
    if not has_registry(conn):
        return set()
    out: set[str] = set()
    for r in conn.execute("SELECT artifact_sha256, prompt_sha256, context_manifest_sha256 FROM workflow_runs"):
        out.update(v for v in r if v)
    return out


# ---------------------------------------------------------------------------------------------------
# 9.1.1 cache -> legacy history
# ---------------------------------------------------------------------------------------------------

def import_legacy_cache(store: Any, conn: Any) -> tuple[int, list[Path]]:
    """Register each readable `workflow_cache/*.json` as a legacy run (inside the caller's transaction).

    Returns `(rows imported, files to archive)`. Unreadable files are left where they are.
    """
    from research_harness.workflows.status import AssessmentStatus, axes_from_legacy

    cache_dir = Path(store.paths.root) / "workflow_cache"
    if not cache_dir.is_dir():
        return 0, []
    types = {r[0]: r[1] for r in conn.execute("SELECT object_id, object_type FROM object_heads")}
    revision = store.current_revision(conn)
    imported, files = 0, []
    for f in sorted(cache_dir.glob("*.json")):
        raw = f.read_bytes()
        try:
            data = json.loads(raw)
            workflow = data.get("workflow") or f.name.split("_")[0]
            axes = axes_from_legacy(data.get("status", "SUCCESS"))
        except (ValueError, AttributeError):
            continue
        sha = store.artifacts.put(raw)
        refs = [r.get("object_id") for r in data.get("provenance_refs", []) if isinstance(r, dict)]
        rq = next((o for o in refs if types.get(o) == "ResearchQuestion"), None)
        insert_run(conn, {
            "workflow": workflow, "created_at": data.get("timestamp") or datetime.now(timezone.utc).isoformat(),
            "research_question_id": rq,
            "input_fingerprint": data.get("input_fingerprint") or f.stem.split("_")[-1],
            "input_revisions": [{"object_id": r.get("object_id"), "revision": r.get("revision")}
                                for r in data.get("provenance_refs", []) if isinstance(r, dict)],
            "executor": "legacy_9_1_1", "artifact_sha256": sha,
            "execution_status": axes.execution.value, "context_status": axes.context.value,
            "assessment_status": AssessmentStatus.NOT_ASSESSED.value, "applicability": axes.applicability.value,
            "legacy_status": data.get("status", "SUCCESS"), "status_reason": "legacy_unverified",
            "usage": data.get("usage") or {}, "legacy": True,
            "known_defects": LEGACY_KNOWN_DEFECTS.get(workflow, _COMMON_DEFECTS),
        }, state_revision=revision)
        imported += 1
        files.append(f)
    return imported, files


def archive_legacy_cache(store: Any, files: Sequence[Path]) -> None:
    """Move imported cache files to `workflow_cache_legacy/` unchanged (after the import has committed)."""
    if not files:
        return
    dest = Path(store.paths.root) / "workflow_cache_legacy"
    dest.mkdir(parents=True, exist_ok=True)
    for f in files:
        target = dest / f.name
        n = 1
        while target.exists():
            target = dest / f"{f.stem}.{n}{f.suffix}"
            n += 1
        shutil.move(str(f), str(target))
    try:
        (Path(store.paths.root) / "workflow_cache").rmdir()
    except OSError:
        pass  # not empty (e.g. an unreadable file was left in place)


def task_rows(conn: Any, task_id: str, *, steps: Optional[bool] = None) -> list[dict[str, Any]]:
    """Rows that belong to a task, newest first: `steps=True` only the step summaries, `False` only the runs they produced."""
    sql = "SELECT * FROM workflow_runs WHERE task_id=?"
    if steps is True:
        sql += " AND workflow LIKE 'task_step:%'"
    elif steps is False:
        sql += " AND workflow NOT LIKE 'task_step:%'"
    return [_decode(r) for r in conn.execute(sql + " ORDER BY rowid DESC", (task_id,))]
