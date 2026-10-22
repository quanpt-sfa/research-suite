"""Readable export of a claim inventory (M5 WP6.2): claim_inventory.csv, claim_evidence_links.csv, claim_argument_relations.csv, claim_version_history.csv, claim_coverage_report.md,
claim_attribution_audit.md, claim_graph.md, claim_benchmark_results.json, token_cost_report.csv.

Every row names its Source@revision, its provenance class and its confirmation status. A relation or an alignment is exported as what it is (a proposal, confirmed or not by the researcher), never
as an established fact; a claim with no verified wording is exported with `author_stated = false` and no quotation; a quotation leaves the store at most `QUOTE_MAX` characters long. The files
are rebuilt from the store, so `research export verify` can say whether they are still what the store produces.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from research_harness.benchmarks import claims as BM
from research_harness.export import render as R
from research_harness.export.contracts import DATA_DERIVED, EXPORTER_VERSION, MODEL_SYNTHESISED, NOT_AVAILABLE, RESEARCHER_CONFIRMED
from research_harness.export.manifest import Export, digest_of
from research_harness.export.retrieval import _csv
from research_harness.knowledge import claims as CL
from research_harness.knowledge import corpus
from research_harness.state import workflow_runs
from research_harness.state.store import StateStore


def claim_class(c: dict[str, Any]) -> str:
    """What kind of statement the claim record is: the researcher's own, one the researcher confirmed or corrected, or a model's reading of a verified quotation."""
    if c["attribution"].get("value") == "RESEARCHER_INTERPRETATION" or c["status"] in ("CONFIRMED_BY_RESEARCHER", "CORRECTED_BY_RESEARCHER"):
        return RESEARCHER_CONFIRMED
    return MODEL_SYNTHESISED


def _tokenizer_note(usage_source: Any) -> str:
    return ("ESTIMATED: scripted adapter; tokens are counted from the prompt text, not billed" if usage_source == "scripted" else
            "as reported by the provider" if usage_source == "provider" else f"usage source {usage_source or 'not recorded'}")


def _run_costs(store: StateStore, s: dict[str, Any]) -> dict[str, Any]:
    """Calls, tokens and what reuse saved, from the run registry rows of one source's extraction runs."""
    made = {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0}
    saved = {"input_tokens": 0, "output_tokens": 0}
    reused = 0
    with store.read() as conn:
        for rid in s["extraction"]["run_ids"]:
            row = workflow_runs.get_run(conn, rid) or {}
            u = row.get("usage") or {}
            for k in made:
                made[k] += u.get(k) or 0
            if row.get("reused_from_run_id"):
                reused += 1
                src = workflow_runs.get_run(conn, row["reused_from_run_id"]) or {}
                for k in saved:
                    saved[k] += (src.get("usage") or {}).get(k) or 0
    return {**made, "reused_runs": reused, "tokens_saved_by_reuse": saved["input_tokens"] + saved["output_tokens"], "usage_source": (s["extraction"]["usage"] or {}).get("usage_source")}


def build_claims_export(store: StateStore, inventory_id: str) -> Export:
    inv = CL.load_inventory(store, inventory_id)
    v = CL.view(store, inventory_id)
    c = inv["content"]
    snapshot = corpus.corpus_snapshot_id(store)
    closure: list[list[Any]] = [[inventory_id, inv["revision"], inv["revision_digest"]]]
    seen: set[tuple] = {(inventory_id, inv["revision"])}

    def pin(oid: str, rev: Any = None) -> None:
        o = store.get_object(oid, rev)
        if o and (oid, o["revision"]) not in seen:
            seen.add((oid, o["revision"]))
            closure.append([oid, o["revision"], o["revision_digest"]])

    sources = []
    for s in c["sources"]:
        pin(s["source_id"], s["source_revision"])
        sources.append({"source_id": s["source_id"], "revision": s["source_revision"], "text_sha256": s["text_sha256"], "fulltext_status": "FULL_TEXT"})
    for e in c["elements"]:
        for ln in ([e["wording"]] if e.get("wording") else []) + list(e.get("evidence") or []):
            pin(ln["passage_id"], ln["passage_revision"])
    for cl in v["claims"]:
        for a in (cl["layers"]["restatement"].get("adjudications") or []):
            pin(a)
    runs = []
    with store.read() as conn:
        for s in c["sources"]:
            for rid in s["extraction"]["run_ids"]:
                row = workflow_runs.get_run(conn, rid)
                if row:
                    closure.append(["run", rid, row.get("artifact_sha256") or ""])
                    u = row.get("usage") or {}
                    runs.append({"run_id": rid, "workflow": row["workflow"], "execution_status": row["execution_status"], "model": (row.get("model_descriptor") or {}).get("model_id"),
                                 "llm_calls": u.get("llm_calls"), "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"), "usage_source": u.get("usage_source")})
        bench = [r for r in workflow_runs.list_runs(conn, workflow=BM.BENCHMARK_WORKFLOW) if r["execution_status"] == "SUCCEEDED" and r.get("artifact_sha256")]
    bench_results = []
    for r in sorted(bench, key=lambda r: r["run_id"]):
        closure.append(["run", r["run_id"], r["artifact_sha256"]])
        bench_results.append({"run_id": r["run_id"], **json.loads(store.artifacts.get_bytes(r["artifact_sha256"]).decode("utf-8"))})
        runs.append({"run_id": r["run_id"], "workflow": r["workflow"], "execution_status": r["execution_status"], "model": None, "llm_calls": 0, "input_tokens": 0, "output_tokens": 0,
                     "usage_source": "not_applicable"})
    header = f"<!-- research-suite export · claim inventory · {inventory_id}@{inv['revision']} · closure {digest_of(closure)[:12]} · corpus {snapshot[:12]} · exporter {EXPORTER_VERSION} -->"
    by_id = {x["id"]: x for x in v["claims"]}

    # ---- claim_inventory.csv
    rows, counts = [], Counter()
    for x in v["claims"]:
        cls = claim_class(x)
        counts[cls] += 1
        chk = (x["attribution"].get("cue_check") or {}).get("flag")
        w = x.get("wording")
        rows.append([x["id"], x.get("source_id"), x.get("source_revision"), x.get("work_id"), x.get("version_id"), x["role"], x["attribution"].get("value"), x["attribution_status"], chk,
                     R.value(x["text"]), R.value(x.get("model_text") or ""), R.quote(w["quote"]) if w else "", x["author_stated"], (w or {}).get("passage_id"), (w or {}).get("passage_revision"),
                     (w or {}).get("char_start"), (w or {}).get("char_end"), (w or {}).get("section"), x["granularity"], "; ".join(x["constructs"]), json.dumps(x["scope"], sort_keys=True),
                     json.dumps(x["premises"], sort_keys=True), json.dumps(x["boundary_conditions"], sort_keys=True), json.dumps(x["mechanism"], sort_keys=True) if x["mechanism"] else "", x.get("direction"),
                     json.dumps(x["cited_works"], sort_keys=True), len(x["evidence"]), ";".join(x["needs_followup"]), x["status"], x["layers"]["restatement"].get("value"), x["currency"]["status"],
                     cls, x["generated_by"], x.get("protocol"), x.get("run_id")])
    inv_head = ["claim_id", "source_id", "source_revision", "work_id", "version_id", "role", "attribution", "attribution_status", "attribution_cue_flag", "restatement", "model_restatement_before_correction", "wording_quote", "author_stated",
                "wording_passage_id", "wording_passage_revision", "char_start", "char_end", "section", "granularity", "constructs", "scope", "premises", "boundary_conditions", "mechanism", "direction",
                "cited_works", "evidence_links", "needs_followup", "review_status", "restatement_reading", "currency", "provenance_class", "generated_by", "protocol", "run_id"]

    # ---- claim_evidence_links.csv
    link_rows, link_counts = [], Counter()
    for x in v["claims"]:
        for ln in x["evidence"]:
            check = corpus.verify_passage(store, ln["passage_id"])["source_match"]
            cls = DATA_DERIVED if ln["derivation"] == "extraction" and ln["link_role"] == "WORDING" else (RESEARCHER_CONFIRMED if ln["derivation"] == "researcher" else MODEL_SYNTHESISED)
            link_counts[cls] += 1
            link_rows.append([x["id"], x["source_id"], x["source_revision"], ln["passage_id"], ln["passage_revision"], ln["link_role"], ln["derivation"], R.value(ln["why_needed"]), ln["char_start"],
                              ln["char_end"], R.quote(ln["quote"]), check, cls])
    link_head = ["claim_id", "source_id", "source_revision", "passage_id", "passage_revision", "link_role", "derivation", "why_needed", "char_start", "char_end", "quote", "source_match_at_export",
                 "provenance_class"]

    # ---- claim_argument_relations.csv
    rel_rows, rel_counts = [], Counter()
    for r in v["relations"]:
        last = r["reviews"][-1] if r["reviews"] else {}
        cls = r["provenance_class"]                      # researcher_confirmed, model_synthesised, or researcher_proposed_unconfirmed (the researcher's own proposal, not yet decided)
        rel_counts[cls] += 1
        to = r["to"]
        to_label = to.get("claim") or (to.get("passage") or {}).get("passage_id")
        to_src = f"{to.get('source_id')}@{to.get('source_revision')}" if to.get("claim") else f"{(to.get('passage') or {}).get('source_id')}@{(to.get('passage') or {}).get('source_revision')}"
        rel_rows.append([r["id"], r["kind"], r["from"]["claim"], f"{r['from']['source_id']}@{r['from']['source_revision']}", to_label, to_src, R.value(r["rationale"]),
                         json.dumps(r["scope"]["levels"], sort_keys=True), r["scope"]["compatibility"], R.value(r.get("meaning_comparison") or ""), R.value(r.get("shared_scope") or ""),
                         "; ".join(r["missing_information"]), r["derivation"], r["provenance"], r["status"], r["confirmation"]["state"], last.get("by"), last.get("at"), R.value(last.get("reason") or ""),
                         "; ".join(r["needs_reassessment"]), cls])
    rel_head = ["relation_id", "kind", "from_claim", "from_source_revision", "to_claim_or_passage", "to_source_revision", "rationale", "levels", "scope_compatibility", "meaning_comparison",
                "shared_scope", "missing_information", "derivation", "provenance", "status", "confirmation", "decided_by", "decided_at", "decision_reason", "needs_reassessment", "provenance_class"]

    # ---- claim_version_history.csv
    ver_rows, ver_counts = [], Counter()
    for a in v["alignments"]:
        last = a["reviews"][-1] if a["reviews"] else {}
        e, l = a["earlier"], a["later"]
        cls = a["provenance_class"]
        ver_counts[cls] += 1
        cav = a.get("coverage_caveat") or {}
        ver_rows.append([a["id"], a["work_id"], a["change"], (e or {}).get("claim"), f"{e['source_id']}@{e['source_revision']}" if e else "", (e or {}).get("version_id"),
                         (l or {}).get("claim"), f"{l['source_id']}@{l['source_revision']}" if l else "", (l or {}).get("version_id"), a.get("other_version_source"), R.value(a["what_changed"]),
                         "; ".join(a["conditions_added"]), R.value(a["rationale"]), "; ".join(a["missing_information"]), ("sections not read: " + ", ".join(cav.get("sections_not_read") or []) if cav else ""),
                         a.get("order_basis"), a["derivation"], a["status"], a["confirmation"]["state"], last.get("by"), "; ".join(a["needs_reassessment"]), cls])
    ver_head = ["alignment_id", "work_id", "change", "earlier_claim", "earlier_source_revision", "earlier_version", "later_claim", "later_source_revision", "later_version", "other_version_searched",
                "what_changed", "conditions_added", "rationale", "missing_information", "coverage_caveat", "order_basis", "derivation", "status", "confirmation", "decided_by", "needs_reassessment",
                "provenance_class"]

    # ---- token_cost_report.csv
    cost_rows, cost_total = [], Counter()
    for s in c["sources"]:
        k = _run_costs(store, s)
        n = sum(1 for x in v["claims"] if x.get("source_id") == s["source_id"])
        ok = sum(1 for x in v["claims"] if x.get("source_id") == s["source_id"] and x["status"] in ("CONFIRMED_BY_RESEARCHER", "CORRECTED_BY_RESEARCHER"))
        tot = k["input_tokens"] + k["output_tokens"]
        cost_total.update({"calls": k["llm_calls"], "in": k["input_tokens"], "out": k["output_tokens"], "claims": n, "confirmed": ok, "saved": k["tokens_saved_by_reuse"]})
        ex = s["extraction"]
        cost_rows.append([s["source_id"], s["source_revision"], ex["strategy"], ex["protocol"], (ex["model"] or {}).get("model_id"), k["llm_calls"], k["input_tokens"], k["output_tokens"], tot,
                          k["reused_runs"], k["tokens_saved_by_reuse"], n, ok, round(tot / n, 1) if n and tot else "", round(tot / ok, 1) if ok and tot else "",
                          ex["chars_read"], ex["chars_total"], _tokenizer_note(k["usage_source"])])
    tot = cost_total["in"] + cost_total["out"]
    cost_rows.append(["(all)", "", "", "", "", cost_total["calls"], cost_total["in"], cost_total["out"], tot, "", cost_total["saved"], cost_total["claims"], cost_total["confirmed"],
                      round(tot / cost_total["claims"], 1) if cost_total["claims"] and tot else "", round(tot / cost_total["confirmed"], 1) if cost_total["confirmed"] and tot else "", "", "",
                      "tokens per confirmed claim is empty until the researcher has confirmed claims"])
    cost_head = ["source_id", "source_revision", "strategy", "protocol", "model", "llm_calls", "input_tokens", "output_tokens", "total_tokens", "reused_runs", "tokens_saved_by_reuse", "claims",
                 "claims_confirmed_by_researcher", "tokens_per_claim", "tokens_per_confirmed_claim", "chars_read", "chars_total", "token_basis"]

    # ---- claim_coverage_report.md
    cov = v["coverage"]
    d = R.Doc(f"Claim coverage of {inventory_id}", header)
    cur = v["currency"]
    d.add(f"Sources read: {cov['sources']}; claims: {cov['claims']}; model calls: {cov['model_calls']}; currency: **{cur['sources']['status']}**"
          + (" — the inventory was built from Source revisions that have since changed" if not cur["all_current"] else "") + ".", "",
          "**A count of claims is not evidence that a paper was read completely.** The table says what was read, what was not, and what the extraction rejected. Claim counts differ by model and strategy "
          "and are not a measure of quality.", "")
    d.add("## What was read", "")
    d.table(["source", "revision", "strategy", "model", "chunks", "chars read / total", "sections not read", "records rejected", "gaps the model named"],
            [(DATA_DERIVED, [s["source_id"], s["source_revision"], s["extraction"]["strategy"], (s["extraction"]["model"] or {}).get("model_id"), s["extraction"]["chunks"],
                             f"{s['extraction']['chars_read']} / {s['extraction']['chars_total']}", ", ".join(s["extraction"]["sections_not_read"]) or "none", len(s["extraction"]["rejected"]),
                             "; ".join(s["extraction"]["gaps"]) or "none"]) for s in v["sources"]])
    reasons: Counter = Counter()
    for s in v["sources"]:
        reasons.update(r["reason"] for r in s["extraction"]["rejected"])
    d.add("## What the extraction rejected", "")
    d.table(["reason", "records"], [(DATA_DERIVED, [k, n]) for k, n in sorted(reasons.items())])
    d.add("## Claims", "")
    d.table(["by attribution", "claims"], [(DATA_DERIVED, [k, n]) for k, n in cov["claims_by_attribution"].items()])
    d.table(["by role", "claims"], [(DATA_DERIVED, [k, n]) for k, n in cov["claims_by_role"].items()])
    d.add(f"Claims flagged for a follow-up reading (compound, needs deeper reading, a finding with nothing beyond its wording): {cov['claims_needing_follow_up']}.", "",
          f"Relations proposed: {cov['relations']}; alignments proposed: {cov['alignments']}. Every one is a proposal until the researcher decides.", "")
    d.add("## Limits", "", *[f"- {x}" for x in v["limits"]])

    # ---- claim_attribution_audit.md
    a = R.Doc(f"Attribution audit of {inventory_id}", header)
    a.add("Who holds each claim, according to a model's reading of the sentence. Citation and own-work cues found in the wording are listed as flags: **a flag is a reason to look at the attribution, "
          "never a decision about it.** A claim that reports earlier work is the citing authors' report; the suite has not read the cited study and has not given it the content.", "")
    a.table(["attribution", "claims", "set or confirmed by the researcher"],
            [(DATA_DERIVED, [k, n, sum(1 for x in v["claims"] if (x["attribution"].get("value") or "NOT_ASSIGNED") == k and x["attribution_status"] in ("RESEARCHER_SET", "RESEARCHER_CONFIRMED"))])
             for k, n in cov["claims_by_attribution"].items()])
    flags = CL.attribution_audit(v)
    a.add("## Claims whose attribution deserves a second look", "")
    a.table(["claim", "source@revision", "flag", "attribution", "citation cues", "own-work cues", "researcher decided", "wording"],
            [(DATA_DERIVED, [f["claim"], f"{f['source_id']}@{f['source_revision']}", f["flag"], f["attribution"], "; ".join(f["citation_cues"] or []), "; ".join(f["own_cues"] or []),
                             f["researcher_has_decided"], R.quote(f["wording"])]) for f in flags])
    a.add("## Reported works", "")
    a.table(["claim", "citation as the authors give it", "state", "linked source", "checked against the cited source"],
            [(DATA_DERIVED, [x["id"], w["citation"], w["state"], w.get("source_id") or "-", w["checked_against_cited_source"]]) for x in v["claims"] for w in x["cited_works"]])
    a.add("A cited work is `CHECKED` only when the researcher has confirmed a relation between the claim and a claim read from that very Source; linking a source records where to check, nothing more.", "")
    a.add("## Interpretations that are not the authors'", "")
    a.table(["claim", "attribution", "about", "text", "missing information"],
            [(RESEARCHER_CONFIRMED if x["attribution"]["value"] == "RESEARCHER_INTERPRETATION" else MODEL_SYNTHESISED,
              [x["id"], x["attribution"]["value"], ", ".join(x.get("about") or []), R.value(x["text"]), "; ".join(x.get("missing_information") or [])])
             for x in v["claims"] if not x["author_stated"]])

    # ---- claim_graph.md
    g = R.Doc(f"Claim graph of {inventory_id}", header)
    g.add("Claims joined by the relations that were **proposed**. A dashed arrow is a proposal the researcher has not confirmed; a solid arrow is one the researcher confirmed; dismissed relations are "
          "not drawn. Nothing here says a claim is true or that two papers say the same thing.", "")
    live = [r for r in v["relations"] if r["confirmation"]["state"] != "DISMISSED_BY_RESEARCHER"]
    nodes = sorted({r["from"]["claim"] for r in live} | {r["to"]["claim"] for r in live if r["to"].get("claim")})
    g.add("```mermaid", "flowchart LR")
    for n in nodes:
        x = by_id[n]
        label = R.bounded(f"{n} · {x['attribution'].get('value') or 'NOT_ASSIGNED'} · {x['text']}", 70).replace('"', "'")
        g.add(f'  {n}["{label}"]')
    for r in live:
        arrow = "-->" if r["confirmation"]["state"] == "CONFIRMED_BY_RESEARCHER" else "-.->"
        target = r["to"]["claim"] if r["to"].get("claim") else "P_" + (r["to"].get("passage") or {}).get("passage_id", "X").replace("-", "_")
        if not r["to"].get("claim"):
            g.add(f'  {target}(["passage {(r["to"].get("passage") or {}).get("passage_id")}"])')
        g.add(f"  {r['from']['claim']} {arrow}|{r['kind']}| {target}")
    g.add("```", "", "## The same, as a list", "")
    g.table(["from", "kind", "to", "confirmation"], [(RESEARCHER_CONFIRMED if r["confirmation"]["state"] == "CONFIRMED_BY_RESEARCHER" else MODEL_SYNTHESISED,
                                                      [r["from"]["claim"], r["kind"], r["to"].get("claim") or (r["to"].get("passage") or {}).get("passage_id"), r["confirmation"]["state"]])
                                                     for r in v["relations"]])

    # ---- claim_benchmark_results.json
    if bench_results:
        bench_doc = {"status": "RESULTS_RECORDED", "runs": bench_results,
                     "note": "metrics are reported per task; none is combined into a composite score. A result on synthetic fixtures tests the metric code, not a model."}
        bench_na = None
    else:
        bench_doc = {"status": NOT_AVAILABLE, "reason": "no benchmark run is recorded in this project for this inventory (research claims benchmark ...)", "runs": []}
        bench_na = bench_doc["reason"]
    bench_bytes = (json.dumps(bench_doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")

    files = {"claim_inventory.csv": _csv(rows, inv_head), "claim_evidence_links.csv": _csv(link_rows, link_head), "claim_argument_relations.csv": _csv(rel_rows, rel_head),
             "claim_version_history.csv": _csv(ver_rows, ver_head), "claim_coverage_report.md": d.text(), "claim_attribution_audit.md": a.text(), "claim_graph.md": g.text(),
             "claim_benchmark_results.json": bench_bytes, "token_cost_report.csv": _csv(cost_rows, cost_head)}
    pc = {"claim_inventory.csv": dict(counts), "claim_evidence_links.csv": dict(link_counts), "claim_argument_relations.csv": dict(rel_counts), "claim_version_history.csv": dict(ver_counts),
          "claim_coverage_report.md": dict(d.counts), "claim_attribution_audit.md": dict(a.counts), "claim_graph.md": dict(g.counts), "claim_benchmark_results.json": {DATA_DERIVED: len(bench_results)},
          "token_cost_report.csv": {DATA_DERIVED: len(cost_rows)}}
    na = [{"file": "claim_benchmark_results.json", "reason": bench_na}] if bench_na else []
    return Export(kind="claims", scope={"inventory": inventory_id}, files=files, closure=closure, corpus_snapshot_id=snapshot, provenance_counts=pc, not_available=na, sources=sources, runs=runs)
