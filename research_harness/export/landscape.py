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

SECTIONS = (("theory", "Theories the papers invoke"), ("construct", "Constructs and how each paper defines them"), ("mechanism", "Mechanisms the papers state"),
            ("stated_assumption", "Assumptions the authors state"), ("design", "Designs and data"),
            ("measurement", "Measurement"), ("identifying_assumption", "Identifying assumptions"), ("boundary", "Where claims are stated not to hold"),
            ("method_limitation", "Limitations the authors state"), ("unresolved_question", "Questions the authors leave open"))
CLAIM_SECTIONS = (("theoretical_claim", "Theoretical claims"), ("interpretive_finding", "Interpretive findings"))
RELATION_CLASS = {"rule": DATA_DERIVED, "model_proposed": MODEL_SYNTHESISED, "researcher_proposed": RESEARCHER_CONFIRMED}


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
    inq = v.get("inquiry")
    doc.add(cur_line + (" — **do not build on this landscape until it is rebuilt from a current run**" if not cur["all_current"] else ""), "",
            f"Built from {c['retrieval_id']} for {c['problem_id']} with {', '.join(c['protocols'])}; versions read: {c['versions_policy']}. "
            f"{k['works_read']} of {k['works_on_reading_list']} works on the reading list were read ({k['sources_read']} sources), {k['entries']} entries; "
            f"{k['possibly_relevant_not_read']} possibly relevant papers were **not read** (no full text).", "",
            "Each entry shows the verbatim quote and where it is. A finding carries three separate dimensions — reading, inference, reproduction — and none of them says "
            "the finding is established. What the authors say is author-asserted; what the suite derives or proposes is listed apart, with its provenance and whether you confirmed it.", "")
    d_ = (inq or {}).get("discipline") or {}
    doc.add("## Inquiry", "", (f"Research purpose: {', '.join(inq.get('research_purposes') or []) or 'not recorded'}; design: {inq.get('research_design') or 'not recorded'}; evidence: "
                              f"{', '.join(inq.get('evidence_forms') or []) or 'not recorded'}; discipline: {d_.get('domain') or 'not recorded'}/{d_.get('subfield') or '-'}. "
                              "These are four separate facts; none was inferred from another." if inq else "The problem records no inquiry (purpose, design, evidence, discipline)."), "")
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
    for cat, title in CLAIM_SECTIONS:
        es = [e for e in v["entries"] if e["category"] == cat]
        doc.add(f"## {title}", "")
        doc.table(["entry", "paper (Source@revision)", "name", "type", "what it says", "quote", "where", "reading", "inference", "reproduction"],
                  [(_entry_class(e), [e["id"], f"{e['source_id']}@{e['source_revision']}", e.get("label") or "-", e.get("claim_type") or "-", e["text"], R.quote(e["author_claim"]["quote"]),
                                      e["author_claim"]["locator"], *_dims(e)]) for e in es])
    for cat, title in SECTIONS:
        es = [e for e in v["entries"] if e["category"] == cat]
        doc.add(f"## {title}", "")
        doc.table(["entry", "paper (Source@revision)", "name", "what it says", "quote", "where", "reading"],
                  [(_entry_class(e), [e["id"], f"{e['source_id']}@{e['source_revision']}", e.get("label") or e.get("item_type") or "-", e["text"], R.quote(e["author_claim"]["quote"]),
                                      e["author_claim"]["locator"], _dims(e)[0]]) for e in es])
    infs = v["inferences"]
    flagged = [i for i in infs if i["needs_reassessment"]]

    def rel_row(i: dict[str, Any]) -> tuple[str, list[Any]]:
        conf = i["confirmation"]
        rec = i["recognition"]
        why = "; ".join(f"{s['entry']}: {', '.join(s['conditions']) or 'none stated'}" for s in i["sides"] if "conditions" in s)
        return (RESEARCHER_CONFIRMED if conf["state"] == "CONFIRMED_BY_RESEARCHER" else RELATION_CLASS.get(i["derivation"], MODEL_SYNTHESISED),
                [i["id"], f"{i['kind']} ({i['subkind']})", i["derivation"], i["provenance"], i["relationship"],
                 "; ".join(f"{s['entry']} {s['source_id']}@{s['source_revision']}" + (f" {s['direction']}" if s.get("direction") else "") + (f" [{i['levels'][s['entry']]}]" if i.get("levels", {}).get(s["entry"]) else "")
                           for s in i["sides"]),
                 i["status"], conf["state"] + (f" by {conf['by']}: {conf['reason']}" if conf.get("by") else " (not reviewed by you)"),
                 (rec["status"] + ": " + "; ".join(f"{a['source_id']}@{a['source_revision']} ({a['how']}; \"{R.quote(a['quote'])}\" at {a['locator']})" for a in rec["addressed_by"]) if rec and rec["addressed_by"]
                  else (rec["status"] if rec else "-")),
                 why or "-", "; ".join(i["missing_information"]) or "-", "; ".join(i["needs_reassessment"]) or "-"])

    rule_inf = [i for i in infs if i["derivation"] == "rule"]
    prop = [i for i in infs if i["derivation"] != "rule" and i["kind"] != "implied_assumption"]
    implied = [i for i in infs if i["kind"] == "implied_assumption"]
    head = ["id", "kind", "derivation", "provenance", "relationship", "sides (entry, Source@revision, direction, level)", "status", "confirmation", "recognition in the reviewed set",
            "conditions each side states", "missing information", "needs reassessment"]
    doc.add("## Suite inferences derived by rule", "", "Derived by the suite from the entries above, by a stated rule and without a model; at most PROVISIONAL. Whether the studies overlap in population, "
            "period and estimand is not assessed: the conditions each paper states are listed so that you can judge. `definitions_differ` observes wording only and says nothing about meaning.", "")
    if flagged:
        doc.add(f"**{len(flagged)} inference(s) need reassessment: something they rest on changed after they were made.**", "")
    doc.table(head, [rel_row(i) for i in rule_inf])
    doc.add("## Proposed relations between studies (not derived by rule; not confirmed)", "",
            "Each is a proposal by a model, by you or by a critic, checked by a gate (the claims it concerns, what they share, how they differ, why, what is missing). It stays UNCONFIRMED until you decide, "
            "with a reason. The suite makes no claim that a tension is new: it lists the reviewed papers that address it, or says that none was found in the reviewed set.", "")
    doc.table(head, [rel_row(i) for i in prop])
    doc.add("## Assumptions proposed by the suite or a critic (not stated by the authors)", "",
            "These are not entries: no author wrote them. Each rests on the entries named, with the reasoning given, and is UNCONFIRMED until you decide.", "")
    doc.table(["id", "derivation", "provenance", "rests on (entry, Source@revision)", "proposed assumption", "reasoning", "status", "confirmation", "needs reassessment"],
              [(RELATION_CLASS.get(i["derivation"], MODEL_SYNTHESISED), [i["id"], i["derivation"], i["provenance"], "; ".join(f"{s['entry']} {s['source_id']}@{s['source_revision']}" for s in i["sides"]),
                                                                            i["text"], i["reasoning"], i["status"], i["confirmation"]["state"], "; ".join(i["needs_reassessment"]) or "-"]) for i in implied])
    doc.add("## Method assessments you recorded", "", "Written by an author or owner; a model writes none. A verdict above LIMITED needs a basis of a kind the claim's route accepts "
            "(empirical: methodological literature; interpretive: methodological literature or textual evidence; conceptual: theoretical literature or logical analysis), every criterion met and "
            "no open threat or rival.", "")
    doc.table(["entry", "route", "verdict", "declared", "blockers", "criteria", "threats", "by", "rationale"],
              [(RESEARCHER_CONFIRMED, [a["entry"], a.get("route", "empirical"), a["verdict"], a["declared_verdict"], "; ".join(a["blockers"]) or "-",
                                       "; ".join(f"{x['criterion']} [{x['met']}; basis: {x['basis']}" + (f", {x['basis_kind']}" if x.get("basis_kind") else "") + "]" for x in a["criteria"]),
                                       "; ".join(f"{t['threat']} [{t['status']}]" for t in a["threats"]) or "none checked", a["by"], a["rationale"]]) for a in c["assessments"]])
    doc.add("## Reproduction links you recorded", "")
    doc.table(["entry", "kind", "reference", "status now", "by", "reason"],
              [(RESEARCHER_CONFIRMED, [x["entry"], x["kind"], (x.get("package") and f"{x['package']} outputs {', '.join(x['outputs'])}") or x.get("reference"),
                                       next(e["layers"]["evidence"]["reproduction"]["value"] for e in findings if e["id"] == x["entry"]), x["by"], x["reason"]])
               for x in c["reproduction_links"]])
    doc.add("## Considerations by kind of claim", "", "Questions a reviewer of each kind of claim usually raises. They are prompts, not a checklist: no consideration is mandatory, and no composite score is "
            "computed from them.", "")
    for kind_, items in v["considerations"].items():
        doc.add(f"- **{kind_}**: " + "; ".join(items))
    doc.add("", "## Limits", "", *[f"- {x}" for x in c["limits"]])
    doc.add(f"- findings without a relationship label (cannot be compared): {k['findings_without_a_relationship_label']}; records rejected in extraction (quote not verified): "
            f"{k['records_rejected_in_extraction']}")
    evidence_rows = [[e["id"], e["category"], e["source_id"], e["source_revision"], e["work_id"], e.get("version_id"), e.get("label"), e.get("direction"), e["text"],
                      e["author_claim"]["quote"], e["author_claim"]["locator"], *_dims(e),
                      e["generated_by"]] for e in v["entries"]]
    work_rows = [[w["work_id"], x["source_id"], x["source_revision"], x.get("version_id"), x.get("document_type"), x.get("review_status"), x.get("first_public_date"),
                  x["tier"], x["retrieved"], x["read"], x["entries"], w["latest_held"], w["later_version_not_read"]] for w in c["works"].values() for x in w["versions"]]
    rel_rows = [[i["id"], i["kind"], i["relation"], i["derivation"], i["provenance"], i["relationship"], ";".join(s["entry"] for s in i["sides"]),
                 ";".join(f"{s['source_id']}@{s['source_revision']}" for s in i["sides"]), i["status"], i["confirmation"]["state"], (i["recognition"] or {}).get("status"),
                 "; ".join(i.get("missing_information") or []), "; ".join(i["needs_reassessment"])] for i in infs]
    files = {"research_landscape.md": doc.text(),
             "relations.csv": _csv(rel_rows, ["id", "kind", "relation", "derivation", "provenance", "relationship", "entries", "sources", "status", "confirmation", "recognition", "missing_information",
                                              "needs_reassessment"]),
             "evidence_table.csv": _csv(evidence_rows, ["entry", "type", "source_id", "source_revision", "work_id", "version_id", "relationship", "direction", "text", "quote", "where",
                                                        "reading", "inference", "reproduction", "generated_by"]),
             "works_and_versions.csv": _csv(work_rows, ["work_id", "source_id", "source_revision", "version_id", "document_type", "review_status", "first_public_date", "tier",
                                                        "retrieved", "read", "entries", "latest_held", "later_version_not_read"])}
    counts = {"research_landscape.md": dict(doc.counts), "evidence_table.csv": {MODEL_SYNTHESISED: len(evidence_rows)}, "works_and_versions.csv": {DATA_DERIVED: len(work_rows)},
              "relations.csv": {DATA_DERIVED: len(rel_rows)}}
    return Export(kind="landscape", scope={"landscape": landscape_id}, files=files, closure=closure, corpus_snapshot_id=snapshot, provenance_counts=counts, sources=sources, runs=[])
