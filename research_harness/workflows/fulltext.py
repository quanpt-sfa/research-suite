"""Access to the stored full text of a Source: whole body for context assembly, bounded pages on demand.

The body of a Source lives either inline in its content (`full_text`) or in the content-addressed store
(`artifact_sha256`). Nothing here summarises or truncates silently: a page says where it ended
(`next_offset`) and carries the sha256 of the whole text, so a caller can resume and can tell if the text changed.
"""
from __future__ import annotations

import hashlib
from typing import Any, Optional

from research_harness.state.store import StateStore

DEFAULT_PAGE_CHARS = 8000


def body_of(store: StateStore, content: dict[str, Any]) -> tuple[Optional[str], str]:
    """Return `(text, status)` for a Source's content.

    status: `INLINE`, `CAS`, `MISSING`, or `RETRIEVAL_FAILED` (an artifact is referenced but cannot be read).
    An abstract is never returned as the body.
    """
    inline = content.get("full_text") or content.get("fulltext")
    if inline:
        return inline, "INLINE"
    sha = content.get("artifact_sha256")
    if sha:
        if not store.artifacts.has(sha):
            return None, "RETRIEVAL_FAILED"
        try:
            return store.artifacts.get_bytes(sha).decode("utf-8", errors="replace"), "CAS"
        except Exception:  # noqa: BLE001
            return None, "RETRIEVAL_FAILED"
    return None, "MISSING"


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def get_fulltext_page(store: StateStore, source_id: str, *, offset: int = 0, limit: int = DEFAULT_PAGE_CHARS,
                      revision: Optional[int] = None) -> dict[str, Any]:
    """Read at most `limit` characters of a Source's full text starting at `offset` (the analogue of paginated
    `read_file` in Deep Agents). Raises KeyError if `source_id` is not a Source, LookupError if it has no body."""
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError("limit must be a positive integer")
    obj = store.get_object(source_id, revision=revision)
    if not obj or obj.get("object_type") != "Source":
        raise KeyError(f"SOURCE_NOT_FOUND: {source_id}")
    text, status = body_of(store, obj.get("content") or {})
    if text is None:
        raise LookupError(f"FULLTEXT_{status}: {source_id}")
    if offset > len(text):
        raise ValueError(f"offset {offset} is beyond the end of the text ({len(text)} characters)")
    end = min(len(text), offset + limit)
    return {
        "source_id": source_id,
        "revision": obj.get("revision", revision),
        "text": text[offset:end],
        "offset": offset,
        "end": end,
        "next_offset": end if end < len(text) else None,
        "total_chars": len(text),
        "sha256": text_sha256(text),
        "source_status": status,
    }
