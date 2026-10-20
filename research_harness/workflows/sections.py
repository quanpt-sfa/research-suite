"""Section detection for stored full texts.

Maps the headings of a paper onto a small controlled set of section labels (`taxonomy/sections.json`) with exact
character spans, so that a comparison across papers can read the *same part* of each one (their sample
construction, say) instead of loading every paper whole.

Detection is structural, not scientific: it says where a heading is and which label its wording matches, with a
confidence. It never feeds a verdict (invariant I1). A heading that matches nothing is kept with label `other`;
text before the first heading is `preamble`. No text is dropped: the sections tile the document.

Text extracted from PDFs is noisy, and the rules are tuned on real papers: a `#` only opens a Markdown heading after a
blank line (in a figure, "# Misstatements" is an axis label); an all-capitals line is a heading only if it has two or more
words and no digits (so "EBIT" and table rows are not); a numbered sub-section ("2.1 ...") that matches no label inherits
the label of the section it sits in, and says so (`confidence: inherited`, `inherited_from`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from research_harness import taxonomy

_MARKDOWN = re.compile(r"^\s{0,3}(?P<hashes>#{1,6})\s+(?P<h>\S.*?)\s*#*\s*$")
_NUM_LEVEL = re.compile(r"^\s*(?P<n>\d+(?:\.\d+)*)\.?\s+")
_NUMBERED = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?|[IVXLC]+\.|[A-Z]\.)\s+(?P<h>[A-Z][^.!?;:]{0,78})\s*$")
_NUM_PREFIX = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?|[IVXLC]+\.|[A-Z]\.)\s+")
_STRIP = re.compile(r"[^a-z0-9 ]+")
# "7. Limitations: because ..." : a known heading word followed by running text on the same line (common in text
# extracted from PDFs). Only known heading words qualify, so ordinary sentences are not mistaken for headings.
_RUN_IN = re.compile(r"^\s*(?P<h>(?:(?:\d+(?:\.\d+)*\.?|[IVXLC]+\.)\s+)?[A-Za-z][A-Za-z ]{2,40}?)\s*[:.\u2014-]\s+\S")
_MAX_PLAIN_HEADING = 60
_TITLE_SMALL_WORDS = frozenset({"and", "of", "the", "for", "in", "on", "to", "a"})


@dataclass(frozen=True)
class Section:
    label: str
    heading: str
    char_start: int
    char_end: int
    confidence: str  # "high" | "low" | "inherited" | "none"
    level: int = 1   # nesting depth: "2" is 1, "2.1" is 2, "#" is 1, "##" is 2
    inherited_from: Optional[str] = None  # heading of the section whose label this one took

    def to_dict(self) -> dict:
        return {"label": self.label, "heading": self.heading, "char_start": self.char_start,
                "char_end": self.char_end, "confidence": self.confidence, "level": self.level,
                "inherited_from": self.inherited_from}


def normalize_heading(heading: str) -> str:
    h = _NUM_PREFIX.sub("", heading.strip()).lower()
    return " ".join(_STRIP.sub(" ", h).split())


def classify_heading(heading: str) -> tuple[str, str]:
    """Return `(label, confidence)` for a heading's wording."""
    norm = normalize_heading(heading)
    if not norm:
        return "other", "none"
    patterns = taxonomy.hierarchy("section_headings")
    low: Optional[str] = None
    for label, pats in patterns.items():
        for p in pats:
            if norm == p or norm.startswith(p + " "):
                return label, "high"
            if low is None and re.search(rf"\b{re.escape(p)}\b", norm):
                low = label
    return (low, "low") if low else ("other", "none")


def _known_plain_heading(line: str) -> bool:
    norm = normalize_heading(line)
    if not norm or len(line.strip()) > _MAX_PLAIN_HEADING or line.strip().endswith((".", ",", ";")):
        return False
    patterns = [p for pats in taxonomy.hierarchy("section_headings").values() for p in pats]
    if any(norm == p for p in patterns):
        return True
    # "Robustness Checks", "Data and Sample Selection": a known heading word followed by a short Title Case tail.
    # A wrapped sentence such as "Data are collected from" has lowercase content words and is not a heading.
    words = _NUM_PREFIX.sub("", line.strip()).split()
    title_case = all(w[:1].isupper() or w.lower() in _TITLE_SMALL_WORDS for w in words)
    return len(words) <= 5 and title_case and any(norm.startswith(p + " ") for p in patterns)


def _heading_level(line: str, markdown_hashes: Optional[str]) -> int:
    if markdown_hashes:
        return len(markdown_hashes)
    m = _NUM_LEVEL.match(line)
    return len(m.group("n").split(".")) if m else 1


def _is_heading(line: str, prev_blank: bool = True) -> Optional[str]:
    stripped = line.strip()
    if not stripped:
        return None
    m = _MARKDOWN.match(line)
    if m and prev_blank:
        return m.group("h")
    if m:
        return None  # "# Misstatements" inside a figure: not a heading
    if len(stripped) <= _MAX_PLAIN_HEADING:
        m = _NUMBERED.match(line)
        if m and len(m.group("h").split()) <= 10:
            return stripped
        letters = [c for c in stripped if c.isalpha()]
        if (letters and all(c.isupper() for c in letters) and len(letters) >= 4 and len(stripped.split()) >= 2
                and not any(c.isdigit() or c == "/" for c in stripped) and not stripped.endswith((".", ","))):
            return stripped
        if _known_plain_heading(line):
            return stripped
    m = _RUN_IN.match(line)
    if m and _known_plain_heading(m.group("h")):
        return m.group("h").strip()
    return None


def detect_sections(text: str) -> list[Section]:
    """Partition `text` into sections. The returned spans are contiguous and cover the whole text."""
    if not text:
        return []
    heads: list[tuple[int, str]] = []  # (char offset of the heading line, heading text)
    levels: list[int] = []
    offset, prev_blank = 0, True
    for line in text.splitlines(keepends=True):
        bare = line.rstrip("\r\n")
        h = _is_heading(bare, prev_blank)
        if h is not None:
            heads.append((offset, h))
            mk = _MARKDOWN.match(bare)
            levels.append(_heading_level(bare, mk.group("hashes") if mk else None))
        prev_blank = not bare.strip()
        offset += len(line)

    if not heads:
        return [Section("other", "", 0, len(text), "none")]
    sections: list[Section] = []
    first = heads[0][0]
    if first > 0 and text[:first].strip():
        sections.append(Section("preamble", "", 0, first, "none"))
    elif heads and first > 0:
        heads[0] = (0, heads[0][1])
    for i, (start, heading) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(text)
        label, conf = classify_heading(heading)
        level, parent = levels[i], None
        if label == "other" and level >= 2:
            # a sub-section with no label of its own takes the label of the nearest enclosing section
            for j in range(len(sections) - 1, -1, -1):
                if sections[j].heading and sections[j].level < level:
                    if sections[j].label != "other":
                        label, conf, parent = sections[j].label, "inherited", sections[j].heading
                    break
        sections.append(Section(label, heading, start, end, conf, level, parent))
    return sections


def sections_for_dimension(sections: list[Section], dimension: str) -> list[Section]:
    """Sections whose label is read for a comparison dimension, in document order."""
    taxonomy.validate_term("comparison_dimension", dimension)
    wanted = set(taxonomy.hierarchy("comparison_dimensions")[dimension])
    return [s for s in sections if s.label in wanted]
