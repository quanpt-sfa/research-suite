"""Deterministic text helpers for the readable exports: bounded quotes, safe table cells, provenance counting."""
from __future__ import annotations

import unicodedata
from collections import Counter
from typing import Any, Iterable, Optional

from research_harness.export.contracts import CLASSES, NOT_AVAILABLE, QUOTE_MAX, VALUE_MAX

_PIPE = "\\|"


def nfc(text: Any) -> str:
    return unicodedata.normalize("NFC", str(text))


def bounded(text: Optional[str], limit: int) -> str:
    """One line, at most `limit` characters; the original length is shown when something was cut."""
    s = " ".join(nfc(text or "").split())
    if len(s) <= limit:
        return s
    return f"{s[:limit - 1]}… ({len(s)} chars)"


def quote(text: Optional[str]) -> str:
    return bounded(text, QUOTE_MAX)


def value(text: Optional[str]) -> str:
    return bounded(text, VALUE_MAX)


def cell(text: Any, limit: int = VALUE_MAX) -> str:
    s = bounded("" if text is None else str(text), limit)
    return s.replace("|", _PIPE) or "-"


class Doc:
    """One markdown file: lines in order, and how many rows each provenance class contributed."""

    def __init__(self, title: str, header: str):
        self.lines: list[str] = [header, f"# {nfc(title)}", ""]
        self.counts: Counter = Counter()
        self.not_available: Optional[str] = None

    def add(self, *lines: str) -> "Doc":
        self.lines.extend(nfc(line) for line in lines)
        return self

    def table(self, head: list[str], rows: Iterable[tuple[str, list[Any]]], classes: bool = True) -> "Doc":
        """`rows` are (provenance class, cells). The class becomes the last column and is counted."""
        cols = head + (["class"] if classes else [])
        self.lines += ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        n = 0
        for cls, cells in rows:
            assert cls in CLASSES, cls
            self.counts[cls] += 1
            self.lines.append("| " + " | ".join(cell(c) for c in cells) + (f" | {cls} |" if classes else " |"))
            n += 1
        if n == 0:
            self.lines.append("| " + " | ".join(["(none)"] + [""] * (len(cols) - 1)) + " |")
        self.lines.append("")
        return self

    def unavailable(self, reason: str) -> "Doc":
        self.not_available = reason
        self.lines.insert(2, f"**{NOT_AVAILABLE}: {nfc(reason)}**")
        self.lines.insert(3, "")
        return self

    def text(self) -> bytes:
        return ("\n".join(self.lines).rstrip("\n") + "\n").encode("utf-8")
