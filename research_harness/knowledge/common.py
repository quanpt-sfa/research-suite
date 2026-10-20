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
