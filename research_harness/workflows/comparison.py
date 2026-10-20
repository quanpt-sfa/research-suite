"""Section-aligned comparison retrieval across sources.

`compare_sources(store, ids, "sample_construction", budget_tokens=...)` reads, for every source, the part of the
paper that answers the same question (its data/sample sections), so papers are compared like with like instead of
being loaded whole.

Nothing is dropped or cut silently:
* a source without stored full text is reported `NO_FULLTEXT`; one without a matching section `NO_MATCHING_SECTION`
  together with the sections it does have;
* a section that does not fit its share of the budget is returned truncated with `next_offset` (resume with
  `get_fulltext_page`), and sections not reached are listed in `unread_sections` with their spans;
* every block carries a locator `Source@revision[start:end]`.
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from research_harness.state.store import StateStore
from research_harness.workflows.contracts import render_comparison_block as render_comparison  # noqa: F401
from research_harness.workflows.fulltext import body_of, text_sha256
from research_harness.workflows.instrumentation import estimate_tokens
from research_harness.workflows.sections import detect_sections, sections_for_dimension


def _tokens(text: str) -> int:
    return estimate_tokens(text)[0]


def _longest_prefix_within(text: str, max_tokens: int) -> int:
    """Largest k such that tokens(text[:k]) <= max_tokens (token counts grow with length)."""
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _tokens(text[:mid]) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1
    return lo


def compare_source(store: StateStore, source_id: str, dimension: str, *, budget_tokens: int,
                   conn: Any = None) -> dict[str, Any]:
    obj = store.get_object(source_id, conn=conn)
    if not obj or obj.get("object_type") != "Source":
        raise KeyError(f"SOURCE_NOT_FOUND: {source_id}")
    revision = obj["revision"]
    content = obj.get("content") or {}
    base: dict[str, Any] = {"source_id": source_id, "revision": revision,
                            "citation_key": content.get("citation_key", source_id), "dimension": dimension,
                            "blocks": [], "unread_sections": [], "available_sections": [], "complete": False}
    text, status = body_of(store, content)
    if text is None:
        return {**base, "status": "NO_FULLTEXT", "reason": status}
    sections = detect_sections(text)
    base.update(total_chars=len(text), sha256=text_sha256(text),
                available_sections=[{"label": s.label, "heading": s.heading, "confidence": s.confidence} for s in sections])
    matched = sections_for_dimension(sections, dimension)
    if not matched:
        return {**base, "status": "NO_MATCHING_SECTION", "complete": True}

    remaining = max(0, budget_tokens)
    blocks, unread = [], []
    for s in matched:
        seg = text[s.char_start:s.char_end]
        cost = _tokens(seg)
        locator = f"{source_id}@{revision}"
        if cost <= remaining:
            blocks.append({"label": s.label, "heading": s.heading, "char_start": s.char_start, "char_end": s.char_end,
                           "text": seg, "truncated": False, "next_offset": None, "confidence": s.confidence,
                           "locator": f"{locator}[{s.char_start}:{s.char_end}]"})
            remaining -= cost
        elif remaining > 0:
            k = _longest_prefix_within(seg, remaining)
            blocks.append({"label": s.label, "heading": s.heading, "char_start": s.char_start,
                           "char_end": s.char_start + k, "text": seg[:k], "truncated": True,
                           "next_offset": s.char_start + k, "confidence": s.confidence,
                           "locator": f"{locator}[{s.char_start}:{s.char_start + k}]"})
            remaining = 0
        else:
            unread.append({"label": s.label, "heading": s.heading, "char_start": s.char_start, "char_end": s.char_end})
    complete = not unread and not any(b["truncated"] for b in blocks)
    return {**base, "status": "MATCHED", "blocks": blocks, "unread_sections": unread, "complete": complete}


def compare_sources(store: StateStore, source_ids: Sequence[str], dimension: str, *, budget_tokens: int = 4000,
                    conn: Any = None) -> list[dict[str, Any]]:
    """Section-aligned reads of `source_ids`, splitting `budget_tokens` evenly between them."""
    ids = list(dict.fromkeys(source_ids))
    if not ids:
        return []
    share = max(0, budget_tokens // len(ids))
    return [compare_source(store, sid, dimension, budget_tokens=share, conn=conn) for sid in ids]
