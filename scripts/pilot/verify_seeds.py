"""Verify candidate seed studies against Crossref (metadata only).

For each seed the script asks Crossref for the best bibliographic matches and accepts a match only if the registered title is close to the title the seed gives, the first author's
family name matches, and the year is within one year. It records what Crossref returned: DOI, registered title, authors, container, issued date, type, and any relation or update
Crossref lists (a retraction or correction notice, a preprint). A seed with no acceptable match is NOT_VERIFIED, with the nearest candidates for a human to look at; it is never
filled in from memory. No full text is fetched and nothing but bibliographic metadata is stored.

    python scripts/pilot/verify_seeds.py --seeds pilots/ai_communication_critical_review/seeds.json --out pilots/ai_communication_critical_review/seeds_verified.json
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://api.crossref.org/works"
FIELDS = "DOI,title,subtitle,issued,container-title,type,author,relation,update-to,updated-by,publisher,volume,issue,page,is-referenced-by-count"
TITLE_THRESHOLD = 0.90


def norm(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", re.sub(r"\s+", " ", (t or "").casefold().replace("&", "and"))).strip()


def title_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def fetch(query: str, rows: int = 5) -> dict:
    url = f"{API}?{urllib.parse.urlencode({'query.bibliographic': query, 'rows': rows, 'select': FIELDS})}"
    req = urllib.request.Request(url, headers={"User-Agent": "research-suite-pilot/1.0 (metadata verification; no personal data)"})
    for attempt in range(5):                       # Crossref asks clients to back off when it answers 429
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return {"url": url, "body": json.load(r)}
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == 4:
                raise
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("unreachable")


def full_title(item: dict) -> str:
    """Crossref registers some titles in two parts (title and subtitle, as ACM and others do)."""
    t = (item.get("title") or [""])[0]
    sub = (item.get("subtitle") or [""])[0]
    return f"{t}: {sub}" if sub and norm(sub) not in norm(t) else t


def year_of(item: dict) -> int | None:
    parts = (item.get("issued") or {}).get("date-parts") or [[None]]
    return parts[0][0]


def match(seed: dict, items: list[dict]) -> tuple[dict | None, list[dict]]:
    scored = []
    for it in items:
        title = full_title(it)
        authors = [a.get("family", "") for a in it.get("author") or []]
        first_ok = bool(authors) and norm(seed["first_author"]) in norm(authors[0])
        y = year_of(it)
        scored.append({"doi": it.get("DOI"), "title": title, "year": y, "first_author": authors[0] if authors else None, "similarity": round(title_similarity(seed["title"], title), 3),
                       "first_author_matches": first_ok, "year_ok": y is not None and abs(y - seed["year"]) <= 1, "type": it.get("type"), "_item": it})
    ok = [s for s in scored if s["similarity"] >= TITLE_THRESHOLD and s["first_author_matches"] and s["year_ok"]]
    best = max(ok, key=lambda s: s["similarity"]) if ok else None
    return (best["_item"] if best else None), [{k: v for k, v in s.items() if k != "_item"} for s in sorted(scored, key=lambda s: -s["similarity"])]


def publication_status(it: dict) -> dict:
    typ = it.get("type")
    notes = []
    flags = {"retraction_or_correction_noticed": False}
    for key in ("update-to", "updated-by"):
        for u in it.get(key) or []:
            notes.append(f"{key}: {u.get('type') or u.get('label')} {u.get('DOI')}")
            flags["retraction_or_correction_noticed"] = True
    for rel, vals in (it.get("relation") or {}).items():
        for v in vals:
            notes.append(f"relation {rel}: {v.get('id')}")
    label = {"journal-article": "published journal article (per Crossref type; peer review is not asserted by Crossref)", "proceedings-article": "conference proceedings paper",
             "posted-content": "posted content (preprint or similar)", "book-chapter": "book chapter", "book": "book"}.get(typ, f"Crossref type {typ}")
    return {"crossref_type": typ, "label": label, **flags, "notes": notes}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pause", type=float, default=1.5)
    a = ap.parse_args()
    seeds = json.loads(Path(a.seeds).read_text(encoding="utf-8"))
    out = {"verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": "Crossref REST API (api.crossref.org), query.bibliographic", "threshold": {"title_similarity": TITLE_THRESHOLD,
           "rule": "title similarity >= threshold AND first author's family name matches AND year within one year"}, "results": []}
    for s in seeds["seeds"]:
        q = f"{s['title']} {s['first_author']} {s['year']}"
        try:
            got = fetch(q)
        except Exception as exc:  # noqa: BLE001 - a network failure is recorded, not hidden
            out["results"].append({"key": s["key"], "status": "NOT_VERIFIED", "reason": f"lookup failed: {type(exc).__name__}: {exc}", "query_url": None})
            continue
        item, near = match(s, got["body"]["message"]["items"])
        row = {"key": s["key"], "query_url": got["url"], "nearest": near[:3]}
        if item is None:
            row.update(status="NOT_VERIFIED", reason="no Crossref record matched title, first author and year together" + ("; the seed is a " + s["kind"].replace("_", " ") + " and may have no DOI" if s["kind"] in ("book_chapter",) else ""))
        else:
            authors = [f"{x.get('family', '')}, {x.get('given', '')}".strip(", ") for x in item.get("author") or []]
            row.update(status="VERIFIED_CROSSREF", doi=item["DOI"], title=full_title(item), authors=authors, year=year_of(item), container=(item.get("container-title") or [""])[0],
                       volume=item.get("volume"), issue=item.get("issue"), pages=item.get("page"), publisher=item.get("publisher"), issued=(item.get("issued") or {}).get("date-parts"),
                       publication=publication_status(item), cited_by_count_at_query=item.get("is-referenced-by-count"))
        out["results"].append(row)
        time.sleep(a.pause)
    out["summary"] = {"seeds": len(seeds["seeds"]), "verified": sum(1 for r in out["results"] if r["status"] == "VERIFIED_CROSSREF"), "not_verified": sum(1 for r in out["results"] if r["status"] != "VERIFIED_CROSSREF")}
    Path(a.out).write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(out["summary"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
