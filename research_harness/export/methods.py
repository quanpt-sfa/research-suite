"""Readable export of the Methods workflow: playbook, extraction, adjudication sheet, fidelity report, cost (WP2).

Pure with respect to the store: reading only, no model call, no clock. The same closure gives the same bytes. Every table row carries a
provenance class (data_derived / model_synthesised / researcher_confirmed); a missing upstream output gives a file headed NOT_AVAILABLE.
Quotes are re-verified against the stored text at export time and bounded; no full text is ever written.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any, Optional

from research_harness.export import render as R
from research_harness.export.contracts import (
    DATA_DERIVED, EXPORTER_VERSION, MODEL_SYNTHESISED, NOT_AVAILABLE, RESEARCHER_CONFIRMED)
from research_harness.export.manifest import Export, digest_of
from research_harness.knowledge import corpus, lifecycle, playbooks
from research_harness.knowledge.common import KnowledgeError
from research_harness.state import workflow_runs
from research_harness.state.store import StateStore
from research_harness.workflows import eca

FILES = ("playbook.md", "extraction.md", "adjudication_sheet.md", "fidelity_report.md", "cost_report.md")


def _js(x: Any) -> str:
    return json.dumps(x, sort_keys=True, ensure_ascii=False, default=str)


def _class_of_generator(generated_by: Optional[str], confirmed: bool) -> str:
    if confirmed or generated_by == "researcher":
        return RESEARCHER_CONFIRMED
    return MODEL_SYNTHESISED if str(generated_by or "").startswith("model:") else DATA_DERIVED


def _model_label(descriptor: Optional[dict]) -> str:
    d = descriptor or {}
    return str(d.get("model_id") or d.get("provider") or "-")


def _usage(row: dict) -> tuple[int, int, int, str]:
    u = row.get("usage") or {}
    return int(u.get("llm_calls") or 0), int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0), str(u.get("usage_source") or "-")


class _Collected:
    """Everything read from the store, before any rendering."""

    def __init__(self, store: StateStore, playbook_id: str, plan_id: Optional[str]):
        pb = store.get_object(playbook_id)
        if not pb or pb["object_type"] != "MethodPlaybook":
            raise KnowledgeError("PLAYBOOK_NOT_FOUND", f"{playbook_id} is not a MethodPlaybook")
        self.store, self.pb, self.plan_id = store, pb, plan_id
        self.content = pb["content"]
        self.closure: list[list[Any]] = [[playbook_id, pb["revision"], pb["revision_digest"]]]
        self.report = {r["element"]: r for r in playbooks.element_report(store, playbook_id)}
        self.statements: dict[str, tuple[Any, dict]] = {}
        self.adjudications: dict[str, list[dict]] = {}
        seen_adj: set[str] = set()
        for el in self.content["elements"]:
            st = eca.resolve_statement(store, playbook_id, element=el["id"])
            sa = eca.statement_assessment(store, st)
            self.statements[el["id"]] = (st, sa)
            self.adjudications[el["id"]] = [store.get_object(a) for a in sa["adjudications"]]
            for a in sa["adjudications"]:
                if a not in seen_adj:
                    seen_adj.add(a)
                    o = store.get_object(a)
                    self.closure.append([a, o["revision"], o["revision_digest"]])
        self.passages: dict[str, dict] = {}
        for el in self.content["elements"]:
            for b in el.get("evidence_basis") or []:
                if b.get("kind") == "source_passage" and b.get("ref"):
                    pid = lifecycle._passage_id(b["ref"])
                    o = store.get_object(pid)
                    if o and pid not in self.passages:
                        self.passages[pid] = o
                        self.closure.append([pid, o["revision"], o["revision_digest"]])
        self.sources: dict[str, dict] = {}
        for p in self.passages.values():
            sid = p["content"].get("source_id")
            if sid and sid not in self.sources:
                o = store.get_object(sid, p["content"].get("source_revision"))
                if o:
                    self.sources[sid] = o
                    self.closure.append([sid, o["revision"], o["revision_digest"]])
        self.verification = {pid: corpus.verify_passage(store, pid)["source_match"] for pid in self.passages}
        with store.read() as conn:
            all_runs = workflow_runs.list_runs(conn)
        self.extraction_runs = [r for r in all_runs if r["workflow"].startswith("reading_protocol:")
                                and any(i.get("object_id") in self.sources for i in r.get("input_revisions") or [])]
        self.eca_runs = [r for r in all_runs if r["workflow"] == "eca_adjudication"
                         and (r.get("input_revisions") or [{}])[0].get("object_id") == playbook_id]
        for r in self.extraction_runs + self.eca_runs:
            self.closure.append(["run", r["run_id"], r.get("artifact_sha256") or ""])
        self.fidelity = None
        if plan_id:
            plan = store.get_object(plan_id)
            if not plan or plan["object_type"] != "AnalysisPlan":
                raise KnowledgeError("PLAN_NOT_FOUND", f"{plan_id} is not an AnalysisPlan")
            if not any(p.split("@")[0].replace("LIB:", "") == playbook_id for p in plan["content"].get("playbook_pins") or []):
                raise KnowledgeError("PLAN_NOT_FOR_PLAYBOOK", f"{plan_id} does not rest on {playbook_id}")
            self.closure.append([plan_id, plan["revision"], plan["revision_digest"]])
            self.fidelity = playbooks.fidelity_report(store, plan_id)


def _header(c: _Collected, digest: str, snapshot: str) -> str:
    return (f"<!-- research-suite export · methods · {c.pb['object_id']}@{c.pb['revision']} · closure {digest[:12]} · corpus {snapshot[:12]} "
            f"· exporter {EXPORTER_VERSION} -->")


def _adjudication_state(sa: dict, adjs: list[dict]) -> str:
    if sa["researcher_confirmed"]:
        return f"confirmed: {sa['eca_verdict']}"
    pending = [a for a in adjs if not a["content"].get("confirms")]
    if pending:
        return f"proposal, not confirmed: {pending[-1]['content']['eca_verdict']}"
    return "none"


def _check_label(match: str) -> str:
    return "VERIFIED" if match == "VERIFIED" else ("QUOTE_NO_LONGER_VERIFIES" if match == "FAILED" else match)


def _playbook_doc(c: _Collected, header: str) -> R.Doc:
    d = R.Doc(f"Playbook {c.pb['object_id']} (revision {c.pb['revision']}): {c.content.get('label', '')}", header)
    v = c.content.get("validation") or {}
    d.add(f"- validation status: `{c.content.get('validation_status', '-')}` · lifecycle: `{c.content.get('lifecycle', '-')}`",
          f"- scope: `{_js(c.content.get('scope') or {})}` · exclusions: `{_js(c.content.get('exclusions') or [])}`",
          f"- sources: {', '.join(sorted(c.sources)) or '-'} · corpus passages cited: {len(c.passages)}",
          "- Statements below were written by the model that read the sources unless the class says otherwise; a statement is `researcher_confirmed` "
          "only when a researcher confirmation exists for its exact text. Fidelity, reproducibility and validity are three separate columns.", "")
    d.add("## Elements", "")
    rows = []
    for el in c.content["elements"]:
        st, sa = c.statements[el["id"]]
        rep = c.report[el["id"]]
        role = "supporting" if el.get("supporting") else ("decisive" if el.get("critical") else "other")
        validity = "; ".join(f"{a['verdict']} ({_js(a['context'])})" for a in rep["validity_assessments"]) or "NOT_ASSESSED"
        basis = ", ".join(lifecycle._passage_id(b["ref"]) for b in el.get("evidence_basis") or [] if b.get("ref")) or "none"
        cls = _class_of_generator(el.get("generated_by"), sa["researcher_confirmed"])
        rows.append((cls, [el["id"], el["category"], el.get("risk_tier", "-"), role, R.value(el["text"]), el.get("source_field", "-"),
                           rep["source_fidelity"], rep["computational_reproducibility"], validity,
                           _adjudication_state(sa, c.adjudications[el["id"]]), basis]))
    d.table(["element", "category", "tier", "role", "statement", "extracted as", "source fidelity", "reproducibility", "validity",
             "adjudication", "passages"], rows)
    conds = c.content.get("conditions") or []
    d.add("## Conditions for the procedure to make sense", "")
    d.table(["condition", "text", "requires"], [(DATA_DERIVED, [x.get("id", "-"), R.value(x.get("text")), _js(x.get("requires"))]) for x in conds])
    d.add("## Validity assessments (per context)", "")
    d.table(["context", "verdict", "elements"],
            [(DATA_DERIVED, [_js(a.get("context")), a.get("verdict"), ", ".join(a.get("elements") or []) or "all"]) for a in v.get("validity_assessments", [])])
    d.add("## Evidence passages (quote checked against the stored text at export time)", "")
    rows = []
    for pid, p in sorted(c.passages.items()):
        pc = p["content"]
        rows.append((DATA_DERIVED, [f"{pid}@{p['revision']}", pc.get("source_id"), pc.get("locator"), R.quote(pc.get("quote")), _check_label(c.verification[pid])]))
    d.table(["passage", "source", "locator", "quote", "check"], rows)
    return d


def _extraction_doc(c: _Collected, header: str) -> R.Doc:
    d = R.Doc("Extraction runs behind the playbook", header)
    done = [r for r in c.extraction_runs if r["execution_status"] == "SUCCEEDED" and r.get("artifact_sha256") and not r.get("reused_from_run_id")
            and c.store.artifacts.has(r["artifact_sha256"])]
    if not done:
        return d.unavailable("no successful reading-protocol run on record for the sources of this playbook")
    latest: dict[tuple, dict] = {}
    for r in sorted(done, key=lambda r: (r["created_at"], r["run_id"])):
        latest[(r["input_revisions"][0]["object_id"], r["workflow"])] = r
    for (sid, wf), r in sorted(latest.items()):
        res = json.loads(c.store.artifacts.get_bytes(r["artifact_sha256"]).decode("utf-8"))
        rej = Counter(str(x.get("reason")) for x in res.get("rejected", []))
        d.add(f"## {res.get('protocol', wf)} on {sid} (source revision {res.get('source_revision')})", "",
              f"- run `{r['run_id']}` ({'re-derived from the stored model answer, no model call' if r['status_reason'] == 'reconsidered' else 'extracted by a model call'}) · model `{_model_label(r.get('model_descriptor'))}` · status `{res.get('status')}`"
              f" · chunks {res.get('chunks')} · sections read: {', '.join(res.get('sections_read') or []) or '-'}",
              f"- kept {len(res.get('records', []))} · rejected {sum(rej.values())} ({', '.join(f'{k}: {n}' for k, n in sorted(rej.items())) or 'none'})"
              f" · gaps: {', '.join(res.get('gaps') or []) or 'none'}", "")
        rows = []
        for rec in sorted(res.get("records", []), key=lambda x: (x["char_start"], x["field"])):
            check = corpus.verify_passage(c.store, rec["passage_id"])["source_match"]
            rows.append((MODEL_SYNTHESISED, [f"{rec['passage_id']}@{rec['revision']}", rec["field"], rec.get("label") or "-", R.value(rec["value"]),
                                             R.quote(rec["quote"]), _check_label(check)]))
        d.table(["passage", "field", "item", "value (model)", "quote (verbatim)", "check"], rows)
        d.add("Rejected or set aside (not stored):", "")
        d.table(["reason", "field", "quote (as proposed)"],
                [(MODEL_SYNTHESISED, [str(x.get("reason")), x.get("field", "-"), R.bounded(x.get("quote"), 120)])
                 for x in sorted(res.get("rejected", []), key=lambda x: (str(x.get("reason")), str(x.get("field")), str(x.get("quote"))))])
    return d


def _sheet_doc(c: _Collected, header: str) -> R.Doc:
    d = R.Doc("Evidence–claim adjudications for the playbook's statements", header)
    if not any(c.adjudications.values()):
        return d.unavailable("no adjudication recorded for this playbook's statements (run `research adjudicate run-pending`)")
    d.add("A confirmation is the researcher's own decision with its own rationale; a proposal is the model's reading until then.", "")
    rows = []
    for el in c.content["elements"]:
        st, _sa = c.statements[el["id"]]
        for a in c.adjudications[el["id"]]:
            x = a["content"]
            if x.get("confirms"):
                rows.append((RESEARCHER_CONFIRMED, [el["id"], st.risk_tier, "confirmation", x["eca_verdict"], x.get("guard") or "-", R.value(x.get("rationale")),
                                                    x.get("passage", {}).get("object_id")]))
            else:
                rows.append((MODEL_SYNTHESISED, [el["id"], st.risk_tier, "proposal", x["eca_verdict"], x.get("guard") or "-",
                                                 R.value(x.get("passage_proposition")), x.get("passage", {}).get("object_id")]))
    d.table(["element", "tier", "record", "verdict", "guard", "rationale / model reading", "passage"], rows)
    waiting = [r for r in eca.review_sheet(c.store) if r["statement"]["object_id"] == c.pb["object_id"]]
    d.add("## Awaiting the researcher's decision", "",
          "Decide with `research adjudicate sheet` and `confirm-batch`; this file records state and does not take decisions.", "")
    d.table(["adjudication", "element", "needs attention", "model verdict", "dropped conditions", "flagged cues", "passage quote"],
            [(MODEL_SYNTHESISED, [r["adjudication_id"], r["statement"]["element"], "yes" if r["needs_attention"] else "no", r["model"]["verdict"],
                                  "; ".join(r["model"]["dropped_conditions"]) or "-", ", ".join(r["qualifier_cues"]) or "-", R.quote(r["passage"]["quote"])])
             for r in waiting])
    return d


def _fidelity_doc(c: _Collected, header: str) -> R.Doc:
    d = R.Doc(f"Fidelity of plan {c.plan_id} to its playbook", header)
    fr = c.fidelity
    if fr is None:
        return d.unavailable("no analysis plan given (use --plan)")
    d.add("The plan is a proposal as stored; this export does not confirm it. Execution results are included only if the plan was run.", "")
    d.add(f"- rests on: {', '.join(r['playbook'] for r in fr['rests_on'])}", f"- inherited steps: {', '.join(fr['inherited']) or 'none'}", "")
    d.add("## Adapted", "")
    d.table(["step", "changes", "rationale"], [(DATA_DERIVED, [x["step"], _js(x["changes"]), R.value(x["rationale"])]) for x in fr["adapted"]])
    d.add("## Added", "")
    d.table(["step", "rationale", "basis"], [(DATA_DERIVED, [x["step"], R.value(x["rationale"]), _js(x["basis"])]) for x in fr["added"]])
    d.add("## Omitted", "")
    d.table(["step", "element", "reason", "replaced by"],
            [(DATA_DERIVED, [x["step"], x.get("element") or "-", R.value(x["reason"]), x.get("replaced_by") or "-"]) for x in fr["omitted"]])
    d.add("## Conditions", "")
    d.table(["condition"], [(DATA_DERIVED, [_js(x)]) for x in fr["conditions"]])
    d.add("## Execution (latest run of each step)", "")
    ex = fr.get("execution") or {}
    d.table(["step", "execution", "failed checks", "signals"],
            [(DATA_DERIVED, [sid, s["execution"], len(s.get("failed_checks") or []), _js(s.get("signals") or s.get("diagnostics") or {})]) for sid, s in sorted(ex.items())])
    return d


def _cost_doc(c: _Collected, header: str) -> R.Doc:
    d = R.Doc("Model calls, tokens and researcher decisions behind this playbook", header)
    rows = []
    by: dict[str, list[dict]] = {}
    for r in c.extraction_runs + c.eca_runs:
        by.setdefault(r["workflow"], []).append(r)
    for wf, rs in sorted(by.items()):
        rows.append((DATA_DERIVED, [wf, len(rs), sum(r["execution_status"] == "SUCCEEDED" for r in rs), sum(r["execution_status"] == "FAILED" for r in rs),
                                    sum(bool(r.get("reused_from_run_id")) for r in rs), sum(_usage(r)[0] for r in rs), sum(_usage(r)[1] for r in rs),
                                    sum(_usage(r)[2] for r in rs), ", ".join(sorted({_usage(r)[3] for r in rs}))]))
    d.table(["workflow", "runs", "succeeded", "failed", "reused", "model calls", "input tokens", "output tokens", "usage source"], rows)
    confirmations = sum(1 for adjs in c.adjudications.values() for a in adjs if a["content"].get("confirms"))
    d.add(f"- researcher confirmations recorded for this playbook's statements: {confirmations}",
          f"- money: {NOT_AVAILABLE} (no price table supplied)",
          f"- estimate versus actual: {NOT_AVAILABLE} (estimates come with task plans)", "",
          "Token counts are as recorded in the run registry; the usage source column says whether they were reported by the provider, scripted or estimated.")
    return d


def build_methods_export(store: StateStore, playbook_id: str, plan_id: Optional[str] = None) -> Export:
    c = _Collected(store, playbook_id, plan_id)
    snapshot = corpus.corpus_snapshot_id(store)
    header = _header(c, digest_of(c.closure), snapshot)
    docs = {"playbook.md": _playbook_doc(c, header), "extraction.md": _extraction_doc(c, header), "adjudication_sheet.md": _sheet_doc(c, header),
            "fidelity_report.md": _fidelity_doc(c, header), "cost_report.md": _cost_doc(c, header)}
    runs = [{"run_id": r["run_id"], "workflow": r["workflow"], "execution_status": r["execution_status"], "model": _model_label(r.get("model_descriptor")),
             "llm_calls": _usage(r)[0], "input_tokens": _usage(r)[1], "output_tokens": _usage(r)[2], "usage_source": _usage(r)[3]}
            for r in c.extraction_runs + c.eca_runs]
    return Export(kind="methods", scope={"playbook": playbook_id, "plan": plan_id}, files={n: d.text() for n, d in docs.items()}, closure=c.closure,
                  corpus_snapshot_id=snapshot, provenance_counts={n: dict(d.counts) for n, d in docs.items()},
                  not_available=[{"file": n, "reason": d.not_available} for n, d in docs.items() if d.not_available],
                  sources=[{"source_id": sid, "revision": o["revision"], "text_sha256": o["content"].get("text_sha256"),
                            "fulltext_status": o["content"].get("fulltext_status")} for sid, o in c.sources.items()], runs=runs)
