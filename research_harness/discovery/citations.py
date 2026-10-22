"""Which stored papers cite which other stored papers (M5 WP5, stage R3a: in-corpus citations).

Only what can be shown from the stored texts: a held paper's reference list contains another held paper's DOI, or its title (at least three words).
It does not parse bibliographic styles, does not look anything up, and finds nothing about papers that are not in the corpus. Each link carries the
place in the reference list where it was found, so it can be checked by eye.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

_DOI = re.compile(r"10\.\d{4,9}/[^\s\"<>]+", re.IGNORECASE)
MIN_TITLE_TOKENS = 3


def normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Case-folded text with every run of non-alphanumerics reduced to one space, and for each output character the index it came from."""
    out: list[str] = []
    idx: list[int] = []
    prev_space = True
    for i, ch in enumerate(unicodedata.normalize("NFKC", text)):
        if ch.isalnum():
            for c in ch.casefold():
                out.append(c)
                idx.append(i)
            prev_space = False
        elif not prev_space:
            out.append(" ")
            idx.append(i)
            prev_space = True
    return "".join(out).strip(), idx[:len("".join(out).strip())]


def _clean_doi(d: str) -> str:
    return d.strip().rstrip(".,;)]").lower()


def reference_text(text: str, sections: list[dict[str, Any]]) -> tuple[str, list[tuple[int, int]]]:
    """The reference-list sections of a paper (as labelled by the section detector) and their spans."""
    spans = [(s["char_start"], s["char_end"]) for s in sections if s.get("label") == "references"]
    return "\n".join(text[a:b] for a, b in spans), spans


def find_links(papers: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """`papers[source_id] = {text, sections, title, doi}` for the held, non-excluded papers. Returns one link per (citing, cited) pair, in a fixed order."""
    refs: dict[str, tuple[str, list[int], tuple[int, int]]] = {}
    parsed: dict[str, bool] = {}
    for sid, p in papers.items():
        if not p.get("text"):
            continue
        rtext, spans = reference_text(p["text"], p.get("sections") or [])
        parsed[sid] = bool(spans)
        if spans:
            norm, idx = normalize_with_map(rtext)
            refs[sid] = (norm, idx, spans[0])
    links = []
    for citing in sorted(refs):
        norm, idx, (a, b) = refs[citing]
        raw_refs = papers[citing]["text"][a:b]
        for cited in sorted(papers):
            if cited == citing:
                continue
            t = papers[cited]
            doi = _clean_doi(t["doi"]) if t.get("doi") else None
            hit = None
            if doi:
                for m in _DOI.finditer(raw_refs):
                    if _clean_doi(m.group(0)) == doi:
                        hit = {"match": "doi", "matched": doi, "chars": [a + m.start(), a + m.end()]}
                        break
            if hit is None and t.get("title"):
                tn, _ = normalize_with_map(t["title"])
                if len(tn.split()) >= MIN_TITLE_TOKENS:
                    pos = norm.find(tn)
                    if pos >= 0 and (pos == 0 or norm[pos - 1] == " ") and (pos + len(tn) == len(norm) or norm[pos + len(tn)] == " "):
                        hit = {"match": "title", "matched": t["title"], "chars": [a + idx[pos], a + idx[pos + len(tn) - 1] + 1]}
            if hit:
                links.append({"citing": citing, "cited": cited, **hit,
                              "locator": f"{citing} chars {hit['chars'][0]}-{hit['chars'][1]} (reference list)"})
    return links


def references_parsed(papers: dict[str, dict[str, Any]]) -> dict[str, bool]:
    return {sid: bool(reference_text(p["text"], p.get("sections") or [])[1]) for sid, p in papers.items() if p.get("text")}
