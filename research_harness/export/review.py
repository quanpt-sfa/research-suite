"""Readable export of a review protocol and its audit: search_protocol.md, screening_log.csv, corpus_inventory.csv (M5 WP6.1 remediation).

States what the record on the ResearchProblem says about how the literature was searched, screened, selected, appraised, extracted and synthesised, who adopted the plan, and what the
corpus holds against it. It does not say the search was adequate. A plan that is only a draft (for example one prepared by an assistant) is shown as a draft.
"""
from __future__ import annotations

from typing import Any

from research_harness.export import render as R
from research_harness.export.contracts import DATA_DERIVED, EXPORTER_VERSION, MODEL_SYNTHESISED, RESEARCHER_CONFIRMED
from research_harness.export.manifest import Export, digest_of
from research_harness.export.retrieval import _csv
from research_harness.knowledge import corpus
from research_harness.knowledge import review_protocol as RP
from research_harness.state.store import StateStore


def build_review_export(store: StateStore, problem_id: str) -> Export:
    from research_harness.discovery.problem import load_problem
    prob = load_problem(store, problem_id)
    rp = prob["content"].get("review_protocol") or {}
    audit = RP.audit_review(store, problem_id)
    snapshot = corpus.corpus_snapshot_id(store)
    closure: list[list[Any]] = [[problem_id, prob["revision"], prob["revision_digest"]]]
    sources = []
    lookup = RP._corpus_lookup(store)
    seen = set()
    for row in sorted(lookup.values(), key=lambda r: r["source_id"]):
        if row["source_id"] in seen:
            continue
        seen.add(row["source_id"])
        o = store.get_object(row["source_id"])
        closure.append([row["source_id"], o["revision"], o["revision_digest"]])
        if row["tier"] == "FULL_TEXT":
            sources.append({"source_id": row["source_id"], "revision": o["revision"], "text_sha256": o["content"].get("text_sha256"), "fulltext_status": "FULL_TEXT"})
    header = (f"<!-- research-suite export · review protocol · {problem_id}@{prob['revision']} · closure {digest_of(closure)[:12]} · corpus {snapshot[:12]} · exporter {EXPORTER_VERSION} -->")
    doc = R.Doc(f"Review protocol of {problem_id}", header)
    if not rp:
        doc.unavailable("no review protocol is recorded on the problem (research review-protocol set)")
        files = {"search_protocol.md": doc.text()}
        return Export(kind="review", scope={"problem": problem_id}, files=files, closure=closure, corpus_snapshot_id=snapshot, provenance_counts={"search_protocol.md": dict(doc.counts)},
                      not_available=[{"file": "search_protocol.md", "reason": doc.not_available or ""}], sources=sources, runs=[])
    adopted = RP.is_adopted(rp)
    drafted = str(rp.get("drafted_by") or "")
    cls = RESEARCHER_CONFIRMED if adopted else (MODEL_SYNTHESISED if ("assistant" in drafted or drafted.startswith("model")) else RESEARCHER_CONFIRMED)
    inq = prob["content"].get("inquiry") or {}
    doc.add(f"Protocol status: **{audit['protocol_status']}**" + (f" (adopted by {audit['adopted_by']} at {audit['adopted_at']})" if adopted else f" — drafted by {drafted or 'unknown'}; the researcher has not adopted this plan"),
            "", f"Review type: {rp.get('review_type') or 'not stated'}. Inquiry: purpose {', '.join(inq.get('research_purposes') or []) or '-'}; design {inq.get('research_design') or '-'}; "
            f"evidence {', '.join(inq.get('evidence_forms') or []) or '-'}; discipline {(inq.get('discipline') or {}).get('domain') or '-'}/{(inq.get('discipline') or {}).get('subfield') or '-'}.", "",
            "A review protocol is the method of a literature review. This file states what is recorded and what the corpus holds against it; it does not say that the search was adequate, "
            "the criteria sound or the review complete.", "")
    doc.add("## Components", "")
    doc.table(["component", "status", "what is missing"], [(DATA_DERIVED, [k, v["status"], "; ".join(v["problems"]) or "-"]) for k, v in audit["components"].items()])
    doc.add("## Streams", "")
    doc.table(["id", "name", "question", "search terms", "included", "included with full text"],
              [(cls, [s["id"], s["name"], s["question"], ", ".join(s["terms"] or []), s["included_records"], s["included_with_full_text"]]) for s in audit["streams"]])
    sp = rp.get("search") or {}
    doc.add("## Search", "", f"Date range: {sp.get('date_from') or '-'} to {sp.get('date_to') or '-'}. Scope: {sp.get('inclusion_scope') or '-'}.", "")
    doc.table(["source", "kind", "status", "queries", "records identified", "date run"],
              [(cls, [x.get("name"), x.get("kind"), x.get("status"), " || ".join(f"[{q.get('stream', '-')}] {q.get('string')}" if isinstance(q, dict) else str(q) for q in x.get("queries") or []) or "-",
                      x.get("records_identified") if x.get("records_identified") is not None else "-", x.get("date_run") or "-"]) for x in sp.get("sources", [])])
    sc = rp.get("screening") or {}
    crit = sc.get("criteria") or {}
    doc.add("## Screening and selection", "", f"Procedure: {sc.get('procedure') or 'not stated'}", "", "Inclusion criteria: " + ("; ".join(crit.get("include") or []) or "not stated"),
            "Exclusion criteria: " + ("; ".join(crit.get("exclude") or []) or "not stated"), "")
    pr = audit["prisma"]
    doc.add("Flow counts (computed from the log): " + (", ".join(f"{k} {v}" for k, v in pr.items() if k != "included") if pr else "not available (no valid log, or no executed search with a record count)"), "")
    doc.add("## Included records and the corpus", "")
    doc.table(["record", "stream", "in corpus", "tier", "Source@revision", "full text"],
              [(DATA_DERIVED, [r["record"], r["stream"] or "-", r["in_corpus"], r["tier"] or "-", f"{r['source_id']}@{r['source_revision']}" if r["source_id"] else "-", r["full_text"]]) for r in audit["included"]])
    doc.add(f"Held in the corpus but never mentioned in the screening log: {len(audit['held_but_never_screened'])}"
            + (f" ({', '.join(audit['held_but_never_screened'])})" if audit["held_but_never_screened"] else "") + ".", "")
    ap, ex, sy = rp.get("appraisal") or {}, rp.get("extraction") or {}, rp.get("synthesis") or {}
    doc.add("## Appraisal, extraction, synthesis", "", f"Appraisal: " + ("performed — " + str(ap.get("approach")) + "; criteria: " + "; ".join(ap.get("criteria") or []) if ap.get("performed") else
                                                                       f"not performed — {ap.get('reason_if_not') or 'no reason given'}" if ap.get("performed") is False else "not declared"),
            f"Extraction: fields {', '.join(ex.get('fields') or []) or '-'}; reading protocols {', '.join(ex.get('protocols') or []) or '-'}; {ex.get('manual_procedure') or ''}",
            f"Synthesis: {sy.get('method') or 'not stated'}. How a tension is judged: {sy.get('how_tensions_are_judged') or 'not stated'}", "")
    doc.add("## Readiness to build a landscape", "", "Yes." if audit["readiness"]["can_build_landscape"] else "**Not ready.**", *[f"- {b}" for b in audit["readiness"]["blockers"]], "")
    doc.add("## Limits", "", *[f"- {x}" for x in audit["limits"]])
    log_rows = [[e["record"], e["stage"], e["decision"], e.get("reason"), e.get("stream"), e.get("by"), e.get("at")] for e in (sc.get("log") or [])]
    log_by_rec = {}
    for e in sc.get("log") or []:
        log_by_rec[e["record"].casefold()] = e
    inv_rows = []
    done = set()
    for row in sorted(lookup.values(), key=lambda r: r["source_id"]):
        if row["source_id"] in done:
            continue
        done.add(row["source_id"])
        e = next((log_by_rec[k] for k in (row["source_id"].casefold(), (row["citation_key"] or "").casefold(), (row["doi"] or "").casefold()) if k and k in log_by_rec), None)
        inv_rows.append([row["source_id"], row["revision"], row["citation_key"], row["doi"], row["title"], row["year"], row["tier"], row["document_type"], row["review_status"], row["excluded"],
                         (e or {}).get("decision"), (e or {}).get("stage"), (e or {}).get("stream"), row["tier"] == "FULL_TEXT"])
    files = {"search_protocol.md": doc.text(),
             "screening_log.csv": _csv(log_rows, ["record", "stage", "decision", "reason", "stream", "by", "at"]),
             "corpus_inventory.csv": _csv(inv_rows, ["source_id", "revision", "citation_key", "doi", "title", "year", "tier", "document_type", "review_status", "excluded",
                                                     "screening_decision", "screening_stage", "stream", "full_text"])}
    counts = {"search_protocol.md": dict(doc.counts), "screening_log.csv": {RESEARCHER_CONFIRMED: len(log_rows)}, "corpus_inventory.csv": {DATA_DERIVED: len(inv_rows)}}
    return Export(kind="review", scope={"problem": problem_id}, files=files, closure=closure, corpus_snapshot_id=snapshot, provenance_counts=counts, sources=sources, runs=[])
