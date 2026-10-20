"""Literature review engine (plan v3, 3C): three purposes, one extraction per paper, structure not verdicts.

`run_literature(purpose, sources)` runs the reading protocol of the purpose on every source (theory: `RP_THEORY`,
method: `RP_METHOD`, contribution: `RP_CONTRIBUTION`). Each protocol run reads only the sections of its dimension,
verifies every quote, and is cached by (source revision, protocol@version): a source whose extraction arrived with a
library import costs no model call.

What the engine adds on top is **structure**, never a judgement of meaning:

* theory: constructs and mechanisms grouped by the label the extraction gave them; a construct defined in different
  words by different sources is *flagged* (the texts differ), not declared to mean something different;
  assumptions that are stated but never recorded as tested, by label;
* method: one `MethodRecord` per source (fields to passages, each record `QUOTE_VERIFIED`, never more: whether the
  reconstruction is a correct reading is Evidence-Claim Adjudication), plus which sources report which field;
* contribution: the records of each paper's own contribution statement, ready for 3B's comparison with the focus study.

Synthesised statements ("A and B define X in different words") are `Statement`s with a deterministic generator and the
passages as their basis. Those that feed Methods or the contribution argument are HIGH risk.

Modes: `narrative` (default; the search protocol is recorded as `NOT_RECORDED` or as described) and `systematic`
(needs a search protocol and a screening log; the PRISMA 2020 flow counts are computed from the log). PRISMA applies
only in systematic mode, and only the flow counts are produced here, not the 27-item checklist.
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from research_harness.core.canonical import canonical_json_hash
from research_harness.knowledge.common import KnowledgeError, register_run
from research_harness.state.store import StateStore
from research_harness.workflows import quotes
from research_harness.workflows import reading_protocols as rp
from research_harness.workflows.provenance import Basis, Statement

PURPOSES = {"theory": "RP_THEORY", "method": "RP_METHOD", "contribution": "RP_CONTRIBUTION"}
MODES = ("narrative", "systematic")
EXECUTOR = "literature_engine@1"
_FEEDS = {"theory": "contribution", "method": "methods", "contribution": "contribution"}


def _norm(label: str) -> str:
    return quotes.normalize_whitespace(label).lower()


def _basis(rec: dict[str, Any], source_id: str) -> Basis:
    return Basis("source_passage", rec["passage_id"], rec.get("char_start"), rec.get("char_end"), quote_verified=True)


def _statement(text: str, recs: Sequence[dict[str, Any]], source_ids: Sequence[str], feeds: str) -> dict[str, Any]:
    st = Statement(text=text, generated_by="deterministic:literature_engine@1",
                   evidence_basis=[_basis(r, s) for r, s in zip(recs, source_ids)])
    return {**st.to_dict(), "risk_tier": "HIGH", "feeds": feeds}


def search_protocol_problems(sp: Optional[dict[str, Any]]) -> list[str]:
    problems = []
    for key in ("databases", "queries"):
        if not (sp or {}).get(key):
            problems.append(f"search protocol needs {key}")
    for key in ("date_from", "date_to"):
        if not (sp or {}).get(key):
            problems.append(f"search protocol needs {key}")
    return problems


def prisma_counts(log: Sequence[dict[str, Any]], search_protocol: dict[str, Any]) -> dict[str, Any]:
    """PRISMA 2020 flow counts from a screening log. Entries: `{record, stage: dedup|screening|eligibility, decision:
    include|exclude, reason}`. Every number is computed; none is typed in except the per-database identified counts."""
    identified = sum(int(n) for n in (search_protocol.get("records_identified") or {}).values())
    by_stage: dict[str, list[dict[str, Any]]] = {"dedup": [], "screening": [], "eligibility": []}
    for e in log:
        if e.get("stage") not in by_stage or e.get("decision") not in {"include", "exclude"}:
            raise KnowledgeError("SCREENING_LOG_INVALID", f"a screening entry needs stage in {list(by_stage)} and decision include/exclude: {e}")
        if e["decision"] == "exclude" and not e.get("reason"):
            raise KnowledgeError("SCREENING_LOG_INVALID", f"an exclusion needs a reason: {e.get('record')}")
        by_stage[e["stage"]].append(e)
    excluded_elig: dict[str, int] = {}
    for e in by_stage["eligibility"]:
        if e["decision"] == "exclude":
            excluded_elig[e["reason"]] = excluded_elig.get(e["reason"], 0) + 1
    included = sorted(e["record"] for e in by_stage["eligibility"] if e["decision"] == "include")
    return {"identified": identified, "duplicates_removed": sum(1 for e in by_stage["dedup"] if e["decision"] == "exclude"),
            "screened": len(by_stage["screening"]), "excluded_at_screening": sum(1 for e in by_stage["screening"] if e["decision"] == "exclude"),
            "assessed_for_eligibility": len(by_stage["eligibility"]), "excluded_at_eligibility": excluded_elig, "included": included,
            "included_count": len(included)}


def _theory(per_source: dict[str, dict[str, Any]]) -> dict[str, Any]:
    constructs: dict[str, list[dict[str, Any]]] = {}
    mechanisms: dict[str, list[dict[str, Any]]] = {}
    stated: dict[str, list[dict[str, Any]]] = {}
    tested: set[tuple[str, str]] = set()
    for sid, res in per_source.items():
        for r in res["records"]:
            entry = {"source_id": sid, "passage_id": r["passage_id"], "quote": r["quote"], "value": r["value"], "label": r.get("label")}
            lab = _norm(r["label"]) if r.get("label") else None
            if r["field"] == "construct_definition" and lab:
                constructs.setdefault(lab, []).append(entry)
            elif r["field"] == "assumed_mechanism" and lab:
                mechanisms.setdefault(lab, []).append(entry)
            elif r["field"] == "stated_assumption":
                stated.setdefault(lab or _norm(r["value"]), []).append(entry)
            elif r["field"] == "assumption_tested":
                tested.add((sid, lab or _norm(r["value"])))
    statements, flags = [], []
    divergent = {}
    for lab, defs in sorted(constructs.items()):
        sources = sorted({d["source_id"] for d in defs})
        texts = {quotes.normalize_whitespace(d["quote"]).lower() for d in defs}
        if len(sources) >= 2 and len(texts) >= 2:
            divergent[lab] = defs
            flags.append({"flag": "CONSTRUCT_DEFINITION_TEXT_DIFFERS", "construct": lab, "sources": sources,
                          "note": "the defining texts differ; whether the meanings differ is for the researcher (or an adjudication) to say"})
            statements.append(_statement(f"Sources {', '.join(sources)} define '{lab}' in different words.", defs, [d["source_id"] for d in defs], "contribution"))
    shared = {lab: sorted({m["source_id"] for m in ms}) for lab, ms in mechanisms.items() if len({m["source_id"] for m in ms}) >= 2}
    for lab, srcs in sorted(shared.items()):
        ms = mechanisms[lab]
        statements.append(_statement(f"Sources {', '.join(srcs)} name the same mechanism label '{lab}' (the labels match; the mechanisms may still differ).",
                                     ms, [m["source_id"] for m in ms], "contribution"))
    untested = [{"assumption": lab, "source_id": e["source_id"], "passage_id": e["passage_id"], "quote": e["quote"]}
                for lab, es in sorted(stated.items()) for e in es if (e["source_id"], lab) not in tested]
    return {"constructs": {k: v for k, v in sorted(constructs.items())}, "divergent_constructs": divergent, "mechanisms": dict(sorted(mechanisms.items())),
            "shared_mechanism_labels": shared, "assumptions_stated_not_recorded_as_tested": untested, "review_flags": flags, "statements": statements}


def _method(per_source: dict[str, dict[str, Any]], protocol: rp.ReadingProtocol) -> dict[str, Any]:
    records = []
    coverage: dict[str, dict[str, bool]] = {f["name"]: {} for f in protocol.fields}
    for sid, res in per_source.items():
        fields: dict[str, list[dict[str, Any]]] = {}
        for r in res["records"]:
            fields.setdefault(r["field"], []).append({"passage_id": r["passage_id"], "quote": r["quote"], "value": r["value"],
                                                      "char_start": r["char_start"], "char_end": r["char_end"]})
        for name in coverage:
            coverage[name][sid] = name in fields
        records.append({"source_id": sid, "source_revision": res["source_revision"], "fields": fields,
                        "source_fidelity": "QUOTE_VERIFIED" if res["records"] else "NOT_CHECKED", "gaps": res.get("gaps", []),
                        "protocol": res["protocol"], "model": res.get("model")})
    return {"method_records": records, "field_coverage": coverage,
            "note": "source_fidelity is QUOTE_VERIFIED at most: that the quotes exist is checked, that the reconstruction reads them correctly is not"}


def _contribution(per_source: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"contribution_records": [{"source_id": sid, "source_revision": res["source_revision"], "records": res["records"],
                                      "author_asserted": True, "protocol": res["protocol"]} for sid, res in per_source.items()]}


def run_literature(store: StateStore, token: Optional[str], purpose: str, sources: Sequence[str], adapter: Any, *,
                   mode: str = "narrative", search_protocol: Optional[dict[str, Any]] = None,
                   screening_log: Optional[Sequence[dict[str, Any]]] = None, max_input_tokens: int = rp.DEFAULT_MAX_INPUT_TOKENS,
                   reuse: bool = True) -> dict[str, Any]:
    """Run one purpose of the literature engine over `sources` (Source ids). See the module docstring."""
    if purpose not in PURPOSES:
        raise KnowledgeError("PURPOSE_UNKNOWN", f"purpose must be one of {sorted(PURPOSES)}")
    if mode not in MODES:
        raise KnowledgeError("MODE_UNKNOWN", f"mode must be one of {MODES}")
    if not sources:
        raise KnowledgeError("NO_SOURCES", "give at least one source")
    prisma = None
    if mode == "systematic":
        problems = search_protocol_problems(search_protocol)
        if not screening_log:
            problems.append("a systematic review needs a screening log")
        if problems:
            raise KnowledgeError("SYSTEMATIC_REQUIRES_PROTOCOL", "; ".join(problems), problems=problems)
        prisma = prisma_counts(screening_log, search_protocol)  # type: ignore[arg-type]
        if set(prisma["included"]) != set(sources):
            raise KnowledgeError("SCREENING_LOG_MISMATCH", "the sources reviewed are not the sources the screening log includes",
                                 reviewed=sorted(sources), included=prisma["included"])
    protocol = rp.get_protocol(PURPOSES[purpose])
    per_source: dict[str, dict[str, Any]] = {}
    calls = reused = 0
    run_ids: list[str] = []
    for sid in sources:
        res = rp.run_protocol(store, token, sid, protocol.id, adapter, max_input_tokens=max_input_tokens, reuse=reuse)
        if res["status"] == "FAILED":
            raise KnowledgeError("EXTRACTION_FAILED", f"{sid}: {res.get('reason')}: {res.get('message')}", source=sid)
        per_source[sid] = res
        reused += 1 if res.get("reused") else 0
        calls += 0 if res.get("reused") else res.get("chunks", 0)
        run_ids.append(res["run_id"])
    body = {"theory": lambda: _theory(per_source), "method": lambda: _method(per_source, protocol),
            "contribution": lambda: _contribution(per_source)}[purpose]()
    review: dict[str, Any] = {
        "purpose": purpose, "protocol": protocol.pin, "mode": mode, "sources": list(sources), "feeds": _FEEDS[purpose],
        "search_protocol": (search_protocol if search_protocol else "NOT_RECORDED") if mode == "narrative" else search_protocol,
        "extraction": {"runs": run_ids, "reused": reused, "model_calls": calls}, **body}
    if prisma is not None:
        review["prisma"] = prisma
    run = register_run(store, workflow=f"literature_review:{purpose}", fingerprint=canonical_json_hash(
        {"purpose": purpose, "sources": sorted(sources), "protocol": protocol.digest, "runs": sorted(run_ids), "mode": mode}),
        executor=EXECUTOR, result=review, assessment="PROVISIONAL", upstream_run_ids=run_ids,
        input_revisions=[{"object_id": sid, "revision": per_source[sid]["source_revision"]} for sid in sources])
    review["run_id"] = run
    return review
