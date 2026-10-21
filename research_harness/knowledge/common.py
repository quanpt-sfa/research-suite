"""Shared helpers of the knowledge modules."""
from __future__ import annotations

import re
from typing import Any, Optional

from research_harness.core.canonical import canonical_json_hash
from research_harness.core.errors import HarnessError
from research_harness.core.model import OriginKind
from research_harness.state.store import StateStore

LIBRARY_ID = "LIBRARY"
LIB_PREFIX = "LIB_"
WRITE_ROLES = ("importer", "owner", "author")
_ID_CLEAN = re.compile(r"[^A-Z0-9]+")


class KnowledgeError(HarnessError):
    """A refused knowledge operation. `code` names the rule."""

    def __init__(self, code: str, message: str = "", **details: Any):
        super().__init__(code, message, **details)


def engine_origin(operation: str, actor_id: str, **extra: Any) -> dict[str, Any]:
    return {"kind": OriginKind.SYSTEM_DERIVED.value, "operation": operation, "authenticated_actor": actor_id, **extra}


def id_fragment(text: str, limit: int = 40) -> str:
    frag = _ID_CLEAN.sub("_", text.upper()).strip("_")
    return (frag or "X")[:limit]


def scope_id(store: StateStore, object_id: str) -> str:
    """Ids written to the library carry the LIB_ prefix (plan v3, 2.1); in a project they are left as they are."""
    if store.project_id == LIBRARY_ID and not object_id.startswith(LIB_PREFIX):
        return LIB_PREFIX + object_id
    return object_id


def digest16(content_digest: str) -> str:
    return content_digest[:16]


def head_or_raise(tx_or_store: Any, object_id: str, expected_type: Optional[str] = None) -> dict[str, Any]:
    head = tx_or_store.head(object_id)
    if head is None:
        raise KnowledgeError("OBJECT_UNKNOWN", f"{object_id} does not exist")
    if expected_type and head["object_type"] != expected_type:
        raise KnowledgeError("OBJECT_TYPE_MISMATCH", f"{object_id} is a {head['object_type']}, not a {expected_type}")
    return head


def digest_of(value: Any) -> str:
    return canonical_json_hash(value)


def next_numbered_id(conn: Any, prefix: str, object_type: str) -> str:
    n = conn.execute("SELECT COUNT(*) FROM object_heads WHERE object_type=?", (object_type,)).fetchone()[0] + 1
    while conn.execute("SELECT 1 FROM object_heads WHERE object_id=?", (f"{prefix}{n:06d}",)).fetchone():
        n += 1
    return f"{prefix}{n:06d}"


def store_of(obj: Any) -> StateStore:
    return obj


def register_run(store: StateStore, **kw: Any) -> str:
    """Append a run to the registry (I5), storing `result` in the content-addressed store. Returns the run id.
    A run that also writes objects uses `run_row` and `workflow_runs.insert_run_tx` inside its own transaction instead (I22)."""
    from research_harness.state import workflow_runs
    return workflow_runs.append_run(store, run_row(store, **kw))


def run_row(store: StateStore, *, workflow: str, fingerprint: str, executor: str, result: Optional[dict[str, Any]] = None,
            execution: str = "SUCCEEDED", context: str = "COMPLETE", assessment: str = "PROVISIONAL", reason: Optional[str] = None,
            usage: Optional[dict] = None, model: Optional[dict] = None, input_revisions: Optional[list] = None,
            upstream_run_ids: Optional[list] = None, **columns: Any) -> dict[str, Any]:
    """The registry row of a run; `result` is written to the content-addressed store first (a blob before the row that names it)."""
    import json
    artifact = store.artifacts.put_bytes(json.dumps(result, sort_keys=True, default=str).encode("utf-8")) if result is not None else None
    return {
        "workflow": workflow, "input_fingerprint": fingerprint, "executor": executor, "execution_status": execution,
        "context_status": context, "assessment_status": assessment, "applicability": "APPLICABLE",
        "legacy_status": "SUCCESS" if execution == "SUCCEEDED" else "FAILED", "status_reason": reason, "usage": usage or {},
        "model_descriptor": model, "artifact_sha256": artifact, "input_revisions": input_revisions or [],
        "upstream_run_ids": upstream_run_ids or [], **columns}
