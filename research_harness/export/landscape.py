"""Readable export of a landscape: research_landscape.md, evidence_table.csv, works_and_versions.csv (M5 WP6).

The landscape lists what the papers on a reading list state, entry by entry, with the verbatim quote and where it is. For a finding the three evidence dimensions
(reading, inference, reproduction) are shown side by side and never summed; suite inferences (a contradiction, a version difference) are listed apart with the
Source@revision of each side and what you decided about them. Nothing here says a result is established, and a paper that was not retrieved is not shown to be absent.
"""
from __future__ import annotations

from typing import Any

from research_harness.discovery import landscape as L
from research_harness.export import render as R
from research_harness.export.contracts import DATA_DERIVED, EXPORTER_VERSION, MODEL_SYNTHESISED, RESEARCHER_CONFIRMED
from research_harness.export.manifest import Export, digest_of
from research_harness.export.retrieval import _csv
from research_harness.knowledge import corpus
from research_harness.state.store import StateStore

SECTIONS = (("construct", "Constructs and how each paper defines them"), ("mechanism", "Mechanisms the papers state"), ("design", "Designs and data"),
            ("measurement", "Measurement"), ("identifying_assumption", "Identifying assumptions"), ("boundary", "Where findings are stated not to hold"),
            ("method_limitation", "Limitations the authors state"), ("unresolved_question", "Questions the authors leave open"))


def _dims(e: dict[str, Any]) -> tuple[str, str, str]:
    ev = e["layers"]["evidence"]
    reading = ev["reading"]["value"] + (" (ECA found it not supported)" if ev["reading"]["disputed"] else "")
    if "inference" not in ev:                                                  # the other two dimensions are about findings
        return reading, "", ""
    inf = ev["inference"]["value"] + (f": {ev['inference']['verdict']}" if ev["inference"].get("verdict") else "")
    rep = ev["reproduction"]["value"]
    return reading, inf, rep


def _entry_class(e: dict[str, Any]) -> str:
    return RESEARCHER_CONFIRMED if e["layers"]["evidence"]["reading"]["value"] == "READING_CONFIRMED" else MODEL_SYNTHESISED


def build_landscape_export(store: StateStore, landscape_id: str) -> Export:
    land = L.load_landscape(store, landscape_id)
    v = L.view(store, landscape_id)
    c = land["content"]
    snapshot = corpus.corpus_snapshot_id(store)
    closure: list[list[Any]] = [[landscape_id, land["revision"], land["revision_digest"]]]
    seen = {landscape_id}

    def pin(oid: str, rev: Any = None) -> None:
        o = store.get_object(oid, rev)
        if o and (oid, o["revision"]) not in seen:
            seen.add((oid, o["revision"]))                                                  # type: ignore[arg-type]
            closure.append([oid, o["revision"], o["revision_digest"]])

    pin(c["retrieval_id"], c["retrieval_revision"])
    pin(c["problem_id"], c["problem_revision"])
    sources = []
    for s in c["sources_read"]:
        pin(s["source_id"], s["source_revision"])
        sources.append({"source_id": s["source_id"], "revision": s["source_revision"], "text_sha256": s["text_sha256"], "fulltext_status": "FULL_TEXT"})
    for e in c["elements"]:
        pin(e["author_claim"]["passage_id"], e["author_claim"]["passage_revision"])
    for e in v["entries"]:
        for a in e["layers"]["evidence"]["reading"]["adjudications"]:
            pin(a)
    for link in c["reproduction_links"]:
        if link["kind"] == "package_run":
            pin(link["package"])
    header = (f"<!-- research-suite export · landscape · {landscape_id}@{land['revision']} · closure {digest_of(closure)[:12]} · corpus {snapshot[:12]} "
              f"· exporter {EXPORTER_VERSION} -->")
    cur = v["currency"]
    cur_line = (f"retrieval run {c['retrieval_id']}: **{cur['retrieval']['status']}**; corpus snapshot {c.get('corpus_snapshot_id') or '-'}: **{cur['corpus']['status']}**; "
                f"problem input (r{c['problem_revision']}): **{cur['problem']['status']}**; search configuration: **{cur['search_config']['status']}**; "
                f"sources read: **{cur['sources_read']['status']}**")
    doc = R.Doc(f"Landscape {landscape_id}: {c['label']}", header)
    k = c["coverage"]
    doc.add(cur_line + (" — **do not build on this landscape until it is rebuilt from a current run**" if not cur["all_current"] else ""), "",
            f"Built from {c['retrieval_id']} for {c['problem_id']} with {', '.join(c['protocols'])}; versions read: {c['versions_policy']}. "
            f"{k['works_read']} of {k['works_on_reading_list']} works on the reading list were read ({k['sources_read']} sources), {k['entries']} entries; "
            f"{k['possibly_relevant_not_read']} possibly relevant papers were **not read** (no full text).", "",
            "Each entry shows the verbatim quote and where it is. A finding carries three separate dimensions — reading, inference, reproduction — and none of them says "
            "the finding is established. What the authors say is author-asserted; what the suite derives is listed under *Suite inferences*.", "")
    if k["works_with_a_later_version_not_read"] or k["works_where_the_latest_version_cannot_be_told"]:
        doc.add("## Versions to know about", "")
        for wid in k["works_with_a_later_version_not_read"]:
            w = c["works"][wid]
            doc.add(f"- **{wid}**: {w['latest_held']} is held and later than what was read ({', '.join(w['read'])}); its claims are not in this landscape.")
        for wid in k["works_where_the_latest_version_cannot_be_told"]:
            doc.add(f"- **{wid}**: several versions are held and no public date says which is latest.")
        doc.add("")
    doc.add("## Works and versions", "", "Evidence is cited at Source@revision; the work only groups papers for reading.", "")
    doc.table(["work", "versions held", "read", "carry entries (evidence)", "latest held", "later version not read"],
              [(DATA_DERIVED, [w["work_id"], "; ".join(f"{x['source_id']}@{x['source_revision']} ({x.get('version_id') or '-'}, {x.get('document_type') or '?'}, public {x.get('first_public_date') or 'unknown'}"
                                                         f"{'' if x['retrieved'] else ', held, not retrieved'})" for x in w["versions"]),
                               ", ".join(w["read"]) or "-", ", ".join(w["used_as_evidence"]) or "-", f"{w['latest_held'] or 'cannot be told'} ({w['latest_basis']})",
                               w["later_version_not_read"]]) for w in c["works"].values()])
    findings = [e for e in v["entries"] if e["category"] == "finding"]
    doc.add("## Findings", "")
    doc.table(["entry", "paper (Source@revision)", "relationship", "direction", "what it says", "quote", "where", "reading", "inference", "reproduction"],
              [(_entry_class(e), [e["id"], f"{e['source_id']}@{e['source_revision']}", e.get("label") or "-", e.get("direction") or "-", e["text"], R.quote(e["author_claim"]["quote"]),
                                  e["author_claim"]["locator"], *_dims(e)]) for e in findings])
    for cat, title in SECTIONS:
        es = [e for e in v["entries"] if e["category"] == cat]
        doc.add(f"## {title}", "")
        doc.table(["entry", "paper (Source@revision)", "name", "what it says", "quote", "where", "reading"],
                  [(_entry_class(e), [e["id"], f"{e['source_id']}@{e['source_revision']}", e.get("label") or e.get("item_type") or "-", e["text"], R.quote(e["author_claim"]["quote"]),
                                      e["author_claim"]["locator"], _dims(e)[0]]) for e in es])
    doc.add("## Suite inferences", "", "Derived by the suite from the entries above, by a stated rule and without a model; at most PROVISIONAL. Whether the studies overlap in population, "
            "period and estimand is not assessed: the conditions each paper states are listed so that you can judge.", "")
    doc.table(["id", "kind", "relationship", "sides (entry, Source@revision, direction)", "conditions each side states", "your decision"],
              [(DATA_DERIVED, [i["id"], f"{i['kind']} ({i['subkind']})", i["relationship"],
                               "; ".join(f"{s['entry']} {s['source_id']}@{s['source_revision']}" + (f" {s['direction']}" if s.get("direction") else "") for s in i["sides"]),
                               "; ".join(f"{s['entry']}: {', '.join(s['conditions']) or 'none stated'}" for s in i["sides"] if "conditions" in s) or "-",
                               (f"{i['reviews'][-1]['decision']} by {i['reviews'][-1]['by']}: {i['reviews'][-1]['reason']}" if i["reviews"] else "not reviewed")]) for i in v["inferences"]])
    doc.add("## Method assessments you recorded", "", "Written by an author or owner; a model writes none. A verdict above LIMITED needs a basis from methodological literature, "
            "every criterion met and no open threat.", "")
    doc.table(["entry", "verdict", "declared", "blockers", "criteria", "threats", "by", "rationale"],
              [(RESEARCHER_CONFIRMED, [a["entry"], a["verdict"], a["declared_verdict"], "; ".join(a["blockers"]) or "-",
                                       "; ".join(f"{x['criterion']} [{x['met']}; basis: {x['basis']}]" for x in a["criteria"]),
                                       "; ".join(f"{t['threat']} [{t['status']}]" for t in a["threats"]) or "none checked", a["by"], a["rationale"]]) for a in c["assessments"]])
    doc.add("## Reproduction links you recorded", "")
    doc.table(["entry", "kind", "reference", "status now", "by", "reason"],
              [(RESEARCHER_CONFIRMED, [x["entry"], x["kind"], (x.get("package") and f"{x['package']} outputs {', '.join(x['outputs'])}") or x.get("reference"),
                                       next(e["layers"]["evidence"]["reproduction"]["value"] for e in findings if e["id"] == x["entry"]), x["by"], x["reason"]])
               for x in c["reproduction_links"]])
    doc.add("## Limits", "", *[f"- {x}" for x in c["limits"]])
    doc.add(f"- findings without a relationship label (cannot be compared): {k['findings_without_a_relationship_label']}; records rejected in extraction (quote not verified): "
            f"{k['records_rejected_in_extraction']}")
    evidence_rows = [[e["id"], e["category"], e["source_id"], e["source_revision"], e["work_id"], e.get("version_id"), e.get("label"), e.get("direction"), e["text"],
                      e["author_claim"]["quote"], e["author_claim"]["locator"], *_dims(e),
                      e["generated_by"]] for e in v["entries"]]
    work_rows = [[w["work_id"], x["source_id"], x["source_revision"], x.get("version_id"), x.get("document_type"), x.get("review_status"), x.get("first_public_date"),
                  x["tier"], x["retrieved"], x["read"], x["entries"], w["latest_held"], w["later_version_not_read"]] for w in c["works"].values() for x in w["versions"]]
    files = {"research_landscape.md": doc.text(),
             "evidence_table.csv": _csv(evidence_rows, ["entry", "type", "source_id", "source_revision", "work_id", "version_id", "relationship", "direction", "text", "quote", "where",
                                                        "reading", "inference", "reproduction", "generated_by"]),
             "works_and_versions.csv": _csv(work_rows, ["work_id", "source_id", "source_revision", "version_id", "document_type", "review_status", "first_public_date", "tier",
                                                        "retrieved", "read", "entries", "latest_held", "later_version_not_read"])}
    counts = {"research_landscape.md": dict(doc.counts), "evidence_table.csv": {MODEL_SYNTHESISED: len(evidence_rows)}, "works_and_versions.csv": {DATA_DERIVED: len(work_rows)}}
    return Export(kind="landscape", scope={"landscape": landscape_id}, files=files, closure=closure, corpus_snapshot_id=snapshot, provenance_counts=counts, sources=sources, runs=[])
