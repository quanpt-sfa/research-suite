"""Quote verification: does a cited span exist verbatim in stored text?

This is the *source-match* check only (plan v3, ECA field `source_match`). A quote that matches says nothing
about whether the statement built on it is a fair reading of the passage; that is Evidence-Claim
Adjudication (Phase 3A.2).

Matching is exact except that runs of whitespace are collapsed on both sides, because extracted full texts
and model-returned quotes routinely differ in line breaks and spacing. Case and punctuation are significant.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_WS = re.compile(r"\s+")


def normalize_whitespace(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Collapse whitespace runs, returning the normalized text and, for every normalized character, the index
    of the original character it came from (so a match can be mapped back to an offset in the original)."""
    out: list[str] = []
    index_map: list[int] = []
    prev_space = True  # drops leading whitespace
    for i, ch in enumerate(text):
        if ch.isspace():
            if not prev_space:
                out.append(" ")
                index_map.append(i)
            prev_space = True
        else:
            out.append(ch)
            index_map.append(i)
            prev_space = False
    if out and out[-1] == " ":
        out.pop()
        index_map.pop()
    return "".join(out), index_map


def find_quote(text: str, quote: str) -> Optional[tuple[int, int]]:
    """Return `(start, end)` offsets in the original `text` of the first whitespace-normalized occurrence of
    `quote`, or None. `text[start:end]` is the matched passage."""
    needle = normalize_whitespace(quote)
    if not needle:
        return None
    hay, index_map = _normalize_with_map(text)
    pos = hay.find(needle)
    if pos < 0:
        return None
    start = index_map[pos]
    end = index_map[pos + len(needle) - 1] + 1
    return start, end


@dataclass(frozen=True)
class QuoteCheck:
    ok: bool
    reason: str = ""
    start: Optional[int] = None
    end: Optional[int] = None


def verify_span(text: Optional[str], start: Optional[int], end: Optional[int], quote: Optional[str] = None) -> QuoteCheck:
    """Verify that `text[start:end]` is (whitespace-normalized) equal to `quote`.

    With no span, the quote must occur somewhere in `text` and its location is returned. With no quote, only
    the span bounds are checked.
    """
    if not text:
        return QuoteCheck(False, "no stored text to verify against")
    if start is None and end is None:
        if not quote:
            return QuoteCheck(False, "neither span nor quote given")
        found = find_quote(text, quote)
        return QuoteCheck(True, "found", *found) if found else QuoteCheck(False, "quote not found in stored text")
    if start is None or end is None:
        return QuoteCheck(False, "span needs both char_start and char_end")
    if not (0 <= start <= end <= len(text)):
        return QuoteCheck(False, f"span [{start}:{end}] outside text of length {len(text)}")
    if quote is None:
        return QuoteCheck(True, "span within bounds", start, end)
    if normalize_whitespace(text[start:end]) != normalize_whitespace(quote):
        return QuoteCheck(False, "text at the cited span differs from the quote", start, end)
    return QuoteCheck(True, "verified", start, end)
