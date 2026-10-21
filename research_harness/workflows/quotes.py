"""Quote verification: does a cited span exist verbatim in stored text?

This is the *source-match* check only (plan v3, ECA field `source_match`). A quote that matches says nothing
about whether the statement built on it is a fair reading of the passage; that is Evidence-Claim
Adjudication (Phase 3A.2).

Matching is exact except for differences that do not change the words and that extracted full texts and model-returned quotes
routinely show: whitespace runs, typographic quotes, ligatures, and hyphens and dashes (a word hyphenated at a line end, a compound
broken across lines). Both sides are reduced the same way (see `_normalize_with_map`). Letters, digits, case and other punctuation are
significant.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_WS = re.compile(r"\s+")


def normalize_whitespace(text: str) -> str:
    return _WS.sub(" ", text).strip()


_TYPO = {0x2018: "'", 0x2019: "'", 0x201B: "'", 0x201C: '"', 0x201D: '"', 0x2212: "-"}
_LIGATURES = {0xFB00: "ff", 0xFB01: "fi", 0xFB02: "fl", 0xFB03: "ffi", 0xFB04: "ffl"}
_DASHES = frozenset("-­‐‑‒–—―−")


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """The form in which a quote is compared with a text, with, for every character of it, the index of the original character it came
    from (so a match maps back to an offset in the original).

    PDF text and model output differ in ways that do not change the words: line breaks, typographic quotes, ligatures, and above all
    hyphens (a word hyphenated at a line end is "neg- ative", a compound is "F1-score" or "F1- score"). Both sides are therefore reduced
    the same way: whitespace runs collapse to one space; typographic quotes become plain ones; ligatures are expanded; every hyphen and
    dash is removed together with the whitespace around it. Letters, digits, case and all other punctuation stay significant."""
    out: list[str] = []
    index_map: list[int] = []
    prev_space = True            # drops leading whitespace
    drop_ws = False             # whitespace right after a removed hyphen is removed too
    for i, ch in enumerate(text):
        if ch in _DASHES:
            while out and out[-1] == " ":
                out.pop()
                index_map.pop()
            drop_ws = True
            prev_space = True if not out else False
            continue
        if ch.isspace():
            if not prev_space and not drop_ws and not (out and out[-1] == "["):   # "[ 35]" and "[35]" are the same citation
                out.append(" ")
                index_map.append(i)
            prev_space = True
            continue
        drop_ws = False
        if ch == "]" and out and out[-1] == " ":
            out.pop()
            index_map.pop()
        for c in _LIGATURES.get(ord(ch), _TYPO.get(ord(ch), ch)):
            out.append(c)
            index_map.append(i)
        prev_space = False
    if out and out[-1] == " ":
        out.pop()
        index_map.pop()
    return "".join(out), index_map


def normalize_for_match(text: str) -> str:
    return _normalize_with_map(text)[0]


def find_quote(text: str, quote: str) -> Optional[tuple[int, int]]:
    """Return `(start, end)` offsets in the original `text` of the first occurrence of `quote` under the comparison form of
    `_normalize_with_map`, or None. `text[start:end]` is the matched passage."""
    needle = normalize_for_match(quote)
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
    if normalize_for_match(text[start:end]) != normalize_for_match(quote):
        return QuoteCheck(False, "text at the cited span differs from the quote", start, end)
    return QuoteCheck(True, "verified", start, end)
