"""Readable export of a retrieval run: reading list, not-read candidates, coverage and queries (M5 WP5).

A reading list is a list of what to read, in a stated order, with the reason each paper is on it; it says nothing about what the papers claim.
Papers the suite does not hold in full are listed apart as possibly relevant and not read. The coverage report states what was actually searched, which
corpus snapshot it rests on, what changed since, and what was not done (no external source was consulted in this version).
"""
from __future__ import annotations

import csv
import io
from typing import Any

from research_harness.export import render as R
from research_harness.export.contracts import DATA_DERIVED, EXPORTER_VERSION, MODEL_SYNTHESISED, RESEARCHER_CONFIRMED
from research_harness.export.manifest import Export, digest_of
from research_harness.discovery import retrieval as RT
from research_harness.knowledge import corpus
from research_harness.state.store import StateStore


def _csv(rows: list[list[Any]], head: list[str]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(head)
    for r in rows:
        w.writerow(["" if v is None else R.nfc(v) for v in r])
    return buf.getvalue().encode("utf-8")


def _versions(g: dict[str, Any]) -> str:
    return "; ".join(f"{v['source_id']}@{v.get('source_revision')}" + (" (shown for the work)" if v["source_id"] == g["representative"] else "") + ("" if v.get("retrieved", True) else " (held, not retrieved)")
                     + f" ({v.get('document_type') or '?'}, {v.get('review_status') or '?'}, public {v.get('first_public_date') or 'date unknown'})" for v in g["versions"])


def _expansion_class(e: dict[str, Any]) -> str:
    if e["status"] in ("ACCEPTED",) and e.get("decided_by"):
        return RESEARCHER_CONFIRMED
    return MODEL_SYNTHESISED if str(e["source"]).startswith("model:") else RESEARCHER_CONFIRMED if e["source"] == "researcher" else DATA_DERIVED


def build_retrieval_export(store: StateStore, run_id: str) -> Export:
    run = RT.load_run(store, run_id)
    view = RT.run_view(store, run_id)
    c = run["content"]
    snapshot = corpus.corpus_snapshot_id(store)
    closure: list[list[Any]] = [[run_id, run["revision"], run["revision_digest"]]]
    problem = store.get_object(c["problem_id"], c["problem_revision"])
    closure.append([problem["object_id"], problem["revision"], problem["revision_digest"]])
    seen = {c["problem_id"], run_id}
    sources = []
    for sid in sorted({h["source_id"] for h in c["hits"]} | {n["source_id"] for n in c["neighbours"]} | {e.get("source_id") for e in c["edits"] if e.get("source_id")}):
        o = store.get_object(sid)
        if o and sid not in seen:
            seen.add(sid)
            closure.append([sid, o["revision"], o["revision_digest"]])
            sources.append({"source_id": sid, "revision": o["revision"], "text_sha256": o["content"].get("text_sha256"), "fulltext_status": o["content"].get("fulltext_status")})
    header = (f"<!-- research-suite export · retrieval · {run_id}@{run['revision']} · closure {digest_of(closure)[:12]} · corpus {snapshot[:12]} "
              f"· exporter {EXPORTER_VERSION} -->")
    cur, pc, kc = view["currency"]["corpus"], view["currency"]["problem"], view["currency"]["search_config"]
    status_line = (f"corpus snapshot {c.get('corpus_snapshot_id') or '-'}: **{cur['status']}**" + (f" (since: +{len(cur['added'])} added, {len(cur['revised'])} revised, "
                   f"{len(cur['removed'])} removed — conclusions from the absence of a source rest on the old snapshot until the search is run again)" if cur["status"] == "STALE_FOR_CORPUS" else "")
                   + f"; problem input (r{c['problem_revision']}): **{pc['status']}**" + (f" ({', '.join(pc.get('changed') or [])} changed since — do not build a landscape from this run)" if pc["status"] == "STALE" else "")
                   + f"; search configuration: **{kc['status']}**")

    rl = R.Doc(f"Reading list for {c['problem_id']} ({run_id})", header)
    cov = R.Doc(f"Coverage of {run_id}", header)
    qx = R.Doc(f"Queries and expansions of {run_id}", header)
    if c["state"] != "SEARCHED":
        rl.unavailable("the search has not been run (research retrieval search)")
        cov.unavailable("the search has not been run (research retrieval search)")
    else:
        rl.add(status_line, "", "Ordered by rank within the retrieval; a rank says what to read first, not what a paper says. Only full texts are listed here.",
               *(["", "**The accepted expansions changed since the search: run it again.**"] if view["search_outdated"] else []), "")
        rl.table(["rank", "work", "versions held", "stage", "why it is here", "where", "queries"],
                 [(DATA_DERIVED, [g["position"], g["work_id"], _versions(g), g["stage"], g["reason"], g["locator"] or "-",
                                  ", ".join(f"{q['query']}#{q['rank']}" for q in g["queries"]) or "-"]) for g in view["reading_list"]])
        rl.add("## Set aside by you for this run", "")
        rl.table(["source", "your reason"], [(RESEARCHER_CONFIRMED, [x["source_id"], x["reason"]]) for x in view["set_aside"]])
        rl.add("## Neighbours: held papers that cite or are cited by a paper on the list (R3a)", "")
        rl.table(["source", "tier", "relation", "found at"], [(DATA_DERIVED, [n["source_id"], n["tier"], "; ".join(r["relation"].replace("_", " ") + " " + r["via"] for r in n["relations"]),
                                                                          n["relations"][0]["locator"]]) for n in c["neighbours"]])
        k = c["coverage"]
        cov.add(status_line, "", f"- queries: {', '.join(q['id'] + ' (' + q['interpretation'] + ')' for q in c['queries'])}",
                f"- corpus: {k['corpus']['sources_total']} sources, of which {k['corpus']['full_text']} full text, {k['corpus']['abstract_only']} abstract only, "
                f"{k['corpus']['metadata_only']} metadata only, {k['corpus']['excluded']} excluded; {k['corpus']['works']} distinct works",
                f"- assessed (retrieved): {k['assessed']['retrieved_sources']} sources, {k['assessed']['retrieved_full_text']} of the {k['assessed']['of_full_text_in_corpus']} full texts held. "
                f"{k['assessed']['note']}",
                f"- stages: R1 {k['stages']['R1']}; found only through accepted expansions {k['stages']['R2_only']}; R3a neighbours {k['stages']['R3a_neighbours']}; "
                f"expansions enabled: {k['stages']['R2_enabled']}",
                f"- floor: {k['floor']['full_text_retrieved']} full texts retrieved ({view['floor_after_your_review']['full_texts_on_the_list']} remain on your list after your review) "
                f"against a minimum of {k['floor']['min_fulltext_sources']}: "
                + ("**BELOW THE FLOOR: a conclusion from absence would be unsafe**" if view["floor_after_your_review"]["below_floor"] else "met")
                + ". Being retrieved is not a judgement of relevance.",
                f"- external sources: {k['external_calls']['metadata_sources']}; calls made: {k['external_calls']['calls']}",
                f"- known gaps: {k['known_gaps']['not_read_candidates']} possibly relevant papers not read; years {k['known_gaps']['year_range']}; languages {k['known_gaps']['languages']}; "
                f"reference lists found in {len(k['known_gaps']['reference_lists_parsed'])} papers, not found in {len(k['known_gaps']['reference_lists_not_found'])}",
                f"- unreadable full texts: {', '.join(k['corpus']['unreadable_full_texts']) or 'none'}", "", "## Excluded from the corpus", "")
        cov.table(["source", "reason"], [(RESEARCHER_CONFIRMED, [x["source_id"], x["reason"]]) for x in k["exclusions"]])
        cov.add("## Limits", "", *[f"- {x}" for x in k["limits"]])
    qx.add("## Queries", "")
    qx.table(["id", "reading", "from", "terms (after tokenizing)"], [(DATA_DERIVED, [q["id"], q["interpretation"], "; ".join(q["from"]), ", ".join(q["tokens"])]) for q in c["queries"]])
    qx.add("## Expansions (other names for the idea)", "", "A model's proposal is used only after you accept it.", "")
    qx.table(["term", "proposed by", "status", "decided by", "your reason"], [(_expansion_class(e), [e["term"], e["source"], e["status"], e.get("decided_by") or "-", e.get("reason") or "-"])
                                                                           for e in c["expansions"]])
    not_read_rows = [["", g["representative"], next((v["title"] for v in g["versions"] if v["source_id"] == g["representative"]), ""), g["versions"][-1].get("tier"), g["stage"], g["reason"],
                      g["versions"][-1].get("document_type"), g["versions"][-1].get("review_status"), g["versions"][-1].get("first_public_date"), "possibly relevant, not read"]
                     for g in view["not_read_candidates"]]
    not_read_rows += [["acquisition request", "", r["title"], "", "R5", r["reason"], "", "", "", f"requested by {r['requested_by']}"] for r in view["acquisition_requests"]]
    files = {"reading_list.md": rl.text(), "coverage_report.md": cov.text(), "queries_and_expansions.md": qx.text(),
             "not_read_candidates.csv": _csv([r[1:] for r in not_read_rows], ["source_id", "title", "tier", "stage", "why", "document_type", "review_status", "first_public_date", "status"])}
    docs = {"reading_list.md": rl, "coverage_report.md": cov, "queries_and_expansions.md": qx}
    counts = {n: dict(d.counts) for n, d in docs.items()}
    counts["not_read_candidates.csv"] = {DATA_DERIVED: len(not_read_rows)}
    return Export(kind="retrieval", scope={"retrieval": run_id}, files=files, closure=closure, corpus_snapshot_id=snapshot, provenance_counts=counts,
                  not_available=[{"file": n, "reason": d.not_available} for n, d in docs.items() if d.not_available], sources=sources, runs=[])
