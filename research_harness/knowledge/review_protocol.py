"""The method of a literature review, as a record (M5 WP6.1 remediation).

A critical or systematic review has a method: how the literature was searched, what was screened in and out and why, whether and how studies were appraised, what was extracted and
how the extractions were synthesised. That method is the "methods" of such an inquiry; it needs no dataset and no estimator, and it can be checked. This module represents it on the
ResearchProblem (`review_protocol`, revised with a reason like any other change to the problem) and checks it. Nothing here searches a database or decides what to include: the
researcher does, and the record says who adopted the plan and who made each screening decision.

Components and their statuses (`component_statuses`):

* `NOT_DECLARED`; `INCOMPLETE` (declared, something required is missing); `DRAFT_COMPLETE` (nothing is missing, but the researcher has not adopted this plan);
  `COMPLETE` (nothing is missing and the researcher adopted exactly this plan); `NOT_PERFORMED_JUSTIFIED` (appraisal only: the plan says it is not performed and why, adopted).

Adoption (`adopt_review_protocol`) is bound to the digest of the plan (everything except the screening log); any edit of the plan makes the adoption void. A plan drafted by an
assistant is a draft until the researcher adopts it. The screening log is evidence of decisions, not part of the plan: appending to it does not void the adoption.

`audit_review` compares the record with the corpus (is each included record held, with a full text? is anything in the corpus absent from the screening log?). It states what the
record and the corpus show; it does not say the search was adequate or the review is complete.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from research_harness.core.canonical import canonical_json_hash
from research_harness.knowledge.common import KnowledgeError
from research_harness.knowledge.literature import prisma_counts
from research_harness.state.store import StateStore

REVIEW_TYPES = ("critical_systematic", "systematic", "narrative", "scoping", "conceptual")
SEARCH_KINDS = ("database", "citation_chasing", "expert", "hand_search", "registry", "preprint_server", "corpus")
SEARCH_STATUS = ("planned", "executed")
SCREEN_STAGES = ("dedup", "screening", "eligibility")
COMPONENTS = ("search", "screening", "appraisal", "extraction", "synthesis")
TOP_KEYS = {"review_type", "streams", "search", "screening", "appraisal", "extraction", "synthesis", "limitations", "drafted_by", "status", "adopted_by", "adopted_at", "adopted_digest"}
_NOT_PLAN = {"status", "adopted_by", "adopted_at", "adopted_digest", "drafted_by"}


def _s(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def plan_digest(protocol: dict[str, Any]) -> str:
    """The digest of the plan: everything but the adoption fields and the screening log."""
    plan = {k: v for k, v in protocol.items() if k not in _NOT_PLAN}
    if isinstance(plan.get("screening"), dict):
        plan["screening"] = {k: v for k, v in plan["screening"].items() if k != "log"}
    return canonical_json_hash(plan)


def is_adopted(protocol: dict[str, Any]) -> bool:
    return bool(protocol.get("adopted_digest")) and protocol["adopted_digest"] == plan_digest(protocol)


def validate_shape(protocol: Any) -> None:
    """Refuse what is not a review protocol at all (wrong types, unknown keys). Missing parts are not an error here: they are reported as INCOMPLETE."""
    if not isinstance(protocol, dict) or not protocol:
        raise KnowledgeError("REVIEW_PROTOCOL_INVALID", "a review protocol is a non-empty object")
    unknown = set(protocol) - TOP_KEYS
    if unknown:
        raise KnowledgeError("REVIEW_PROTOCOL_INVALID", f"unknown keys {sorted(unknown)}; allowed: {sorted(TOP_KEYS)}")
    if protocol.get("review_type") is not None and protocol["review_type"] not in REVIEW_TYPES:
        raise KnowledgeError("REVIEW_PROTOCOL_INVALID", f"review_type must be one of {REVIEW_TYPES}")
    if not isinstance(protocol.get("streams", []), list) or not all(isinstance(x, dict) for x in protocol.get("streams", [])):
        raise KnowledgeError("REVIEW_PROTOCOL_INVALID", "streams is a list of objects")
    for key in ("search", "screening", "appraisal", "extraction", "synthesis"):
        if protocol.get(key) is not None and not isinstance(protocol[key], dict):
            raise KnowledgeError("REVIEW_PROTOCOL_INVALID", f"{key} is an object")
    for src in (protocol.get("search") or {}).get("sources", []) or []:
        if not isinstance(src, dict) or src.get("kind") not in SEARCH_KINDS or src.get("status") not in SEARCH_STATUS:
            raise KnowledgeError("REVIEW_PROTOCOL_INVALID", f"a search source needs kind in {SEARCH_KINDS} and status in {SEARCH_STATUS}: {src}")
    for e in (protocol.get("screening") or {}).get("log", []) or []:
        _check_log_entry(e)


def _check_log_entry(e: Any) -> None:
    if not isinstance(e, dict) or not _s(e.get("record")) or e.get("stage") not in SCREEN_STAGES or e.get("decision") not in ("include", "exclude"):
        raise KnowledgeError("SCREENING_LOG_INVALID", f"a screening entry needs a record, a stage in {SCREEN_STAGES} and a decision include/exclude: {e}")
    if e["decision"] == "exclude" and not _s(e.get("reason")):
        raise KnowledgeError("SCREENING_LOG_INVALID", f"an exclusion needs a reason: {e.get('record')}")


def component_statuses(protocol: Optional[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """`{component: {"status", "problems"}}`. Pure; reads only the protocol."""
    out: dict[str, dict[str, Any]] = {}
    protocol = protocol or {}
    adopted = is_adopted(protocol) if protocol else False

    def put(name: str, declared: bool, problems: list[str], *, justified_not_performed: bool = False) -> None:
        if not declared:
            out[name] = {"status": "NOT_DECLARED", "problems": ["not declared"]}
        elif problems:
            out[name] = {"status": "INCOMPLETE", "problems": problems}
        elif not adopted:
            out[name] = {"status": "DRAFT_COMPLETE", "problems": ["the researcher has not adopted this plan"]}
        else:
            out[name] = {"status": "NOT_PERFORMED_JUSTIFIED" if justified_not_performed else "COMPLETE", "problems": []}

    streams = protocol.get("streams") or []
    sp = protocol.get("search") or {}
    problems = []
    if not streams:
        problems.append("name at least one stream (a theoretical stream or question the search serves)")
    for st in streams:
        if not (_s(st.get("id")) and _s(st.get("name")) and _s(st.get("question")) and st.get("terms")):
            problems.append(f"stream {st.get('id') or st.get('name') or '?'} needs id, name, question and search terms")
    sources = sp.get("sources") or []
    if not sources:
        problems.append("name at least one search source")
    for src in sources:
        if not _s(src.get("name")):
            problems.append("a search source needs a name")
        if src.get("status") == "executed" and not (src.get("queries") and isinstance(src.get("records_identified"), int)):
            problems.append(f"{src.get('name')}: an executed search records its queries and the number of records identified")
    for k in ("date_from", "date_to"):
        if not _s(sp.get(k)):
            problems.append(f"the search needs {k}")
    if not sp.get("inclusion_scope"):
        problems.append("state the scope the search covers (fields, languages, document types)")
    put("search", bool(streams or sp), problems)

    sc = protocol.get("screening") or {}
    problems = []
    crit = sc.get("criteria") or {}
    if not _s(sc.get("procedure")):
        problems.append("describe the screening procedure (who screens, in which stages, how disagreements are settled)")
    if not crit.get("include"):
        problems.append("state inclusion criteria")
    if not crit.get("exclude"):
        problems.append("state exclusion criteria")
    log = sc.get("log") or []
    if not log:
        problems.append("the screening log is empty: nothing has been screened and recorded")
    else:
        try:
            ident = {"records_identified": {s.get("name"): s.get("records_identified") for s in sources if s.get("status") == "executed" and isinstance(s.get("records_identified"), int)}}
            counts = prisma_counts(log, ident)
            if not counts["identified"]:
                problems.append("no executed search source gives a number of records identified, so the flow counts cannot start")
            elif counts["included_count"] < 1:
                problems.append("no record is included at the eligibility stage")
        except KnowledgeError as exc:
            problems.append(exc.message)
    put("screening", bool(sc), problems)

    ap = protocol.get("appraisal") or {}
    problems = []
    performed = ap.get("performed")
    if performed is True:
        if not _s(ap.get("approach")):
            problems.append("describe the appraisal approach")
        if not ap.get("criteria"):
            problems.append("state the appraisal criteria")
    elif performed is False:
        if not _s(ap.get("reason_if_not")):
            problems.append("say why no appraisal is performed")
    else:
        problems.append("say whether studies are appraised (performed: true or false)")
    put("appraisal", bool(ap), problems, justified_not_performed=performed is False)

    ex = protocol.get("extraction") or {}
    problems = []
    if not ex.get("fields"):
        problems.append("list what is extracted from each study")
    if not (ex.get("protocols") or _s(ex.get("manual_procedure"))):
        problems.append("name the reading protocols used (for example RP_LANDSCAPE@2, RP_THEORY@3) or describe the manual procedure")
    put("extraction", bool(ex), problems)

    sy = protocol.get("synthesis") or {}
    problems = []
    if not _s(sy.get("method")):
        problems.append("state the synthesis method")
    if not _s(sy.get("how_tensions_are_judged")):
        problems.append("state how a theoretical tension is judged to exist, and how a claim that the literature already addresses it is judged")
    put("synthesis", bool(sy), problems)
    return out


# ------------------------------------------------------------------------------------------------ writing the record

def _problem(store: StateStore, problem_id: str) -> dict[str, Any]:
    from research_harness.discovery.problem import load_problem
    return load_problem(store, problem_id)


def _write(store: StateStore, token: Optional[str], problem_id: str, build: Any, operation: str) -> dict[str, Any]:
    from research_harness.discovery import problem as P
    p = P.load_problem(store, problem_id)
    return P._write(store, token, problem_id, build, operation, expected_head=p["revision"], invalidate=False)


def set_review_protocol(store: StateStore, token: Optional[str], problem_id: str, protocol: dict[str, Any], rationale: str, *, drafted_by: Optional[str] = None) -> dict[str, Any]:
    """Record the plan of the review on the problem (a new problem revision, with your reason). The screening log already recorded is kept; the plan is a DRAFT until adopted."""
    from research_harness.discovery import problem as P
    if not _s(rationale):
        raise KnowledgeError("RATIONALE_REQUIRED", "say why the protocol is recorded or changed")
    validate_shape(protocol)

    def build(c: dict, tx: Any) -> dict:
        old = c.get("review_protocol") or {}
        new = {k: v for k, v in protocol.items() if k not in _NOT_PLAN}
        if (old.get("screening") or {}).get("log") and "log" not in (new.get("screening") or {}):
            new["screening"] = {**(new.get("screening") or {}), "log": old["screening"]["log"]}
        new["status"] = "DRAFT"
        new["drafted_by"] = drafted_by or protocol.get("drafted_by") or tx.actor_id
        return {**c, "review_protocol": new, "decisions": P._log(c, tx.at, tx.actor_id, "review_protocol_set", rationale.strip(), plan_digest=plan_digest(new))}

    return _write(store, token, problem_id, build, "problem_review_protocol")


def adopt_review_protocol(store: StateStore, token: Optional[str], problem_id: str, rationale: str) -> dict[str, Any]:
    """The researcher adopts this exact plan (author or owner). Adoption is bound to the plan's digest: any later edit of the plan voids it."""
    from research_harness.discovery import problem as P
    if not _s(rationale):
        raise KnowledgeError("RATIONALE_REQUIRED", "say why you adopt this plan")

    def build(c: dict, tx: Any) -> dict:
        rp = c.get("review_protocol")
        if not rp:
            raise KnowledgeError("REVIEW_PROTOCOL_NOT_FOUND", f"{problem_id} records no review protocol")
        new = {**rp, "status": "ADOPTED_BY_RESEARCHER", "adopted_by": tx.actor_id, "adopted_at": tx.at, "adopted_digest": plan_digest(rp)}
        return {**c, "review_protocol": new, "decisions": P._log(c, tx.at, tx.actor_id, "review_protocol_adopted", rationale.strip(), plan_digest=new["adopted_digest"])}

    return _write(store, token, problem_id, build, "problem_review_protocol_adopt")


def record_screening(store: StateStore, token: Optional[str], problem_id: str, entries: list[dict[str, Any]], rationale: str) -> dict[str, Any]:
    """Append the researcher's screening decisions to the log. Each is stamped with who and when; an exclusion needs its reason. Appending does not void the adoption of the plan."""
    if not _s(rationale):
        raise KnowledgeError("RATIONALE_REQUIRED", "say what these decisions are (for example: title and abstract screening, first pass)")
    if not entries:
        raise KnowledgeError("SCREENING_LOG_INVALID", "give at least one entry")
    for e in entries:
        _check_log_entry(e)
    from research_harness.discovery import problem as P

    def build(c: dict, tx: Any) -> dict:
        rp = c.get("review_protocol")
        if not rp:
            raise KnowledgeError("REVIEW_PROTOCOL_NOT_FOUND", f"{problem_id} records no review protocol: record the plan before the decisions")
        sc = dict(rp.get("screening") or {})
        stamped = [{**e, "by": tx.actor_id, "at": tx.at} for e in entries]
        sc["log"] = [*(sc.get("log") or []), *stamped]
        return {**c, "review_protocol": {**rp, "screening": sc}, "decisions": P._log(c, tx.at, tx.actor_id, "review_screening_recorded", rationale.strip(), entries=len(entries))}

    return _write(store, token, problem_id, build, "problem_review_screening")


# ------------------------------------------------------------------------------------------------ the audit

def _corpus_lookup(store: StateStore) -> dict[str, dict[str, Any]]:
    from research_harness.knowledge import corpus
    out: dict[str, dict[str, Any]] = {}
    with store.read() as conn:
        for (oid,) in conn.execute("SELECT object_id FROM object_heads WHERE object_type='Source' ORDER BY object_id").fetchall():
            o = store.get_object(oid, conn=conn)
            c = o["content"]
            row = {"source_id": oid, "revision": o["revision"], "tier": corpus.source_tier(c), "citation_key": c.get("citation_key"), "doi": c.get("doi"), "title": c.get("title"),
                   "year": c.get("year"), "excluded": bool(c.get("excluded")), "document_type": c.get("document_type"), "review_status": c.get("review_status")}
            for key in (oid, c.get("citation_key"), c.get("doi")):
                if key:
                    out[str(key).casefold()] = row
    return out


def audit_review(store: StateStore, problem_id: str) -> dict[str, Any]:
    """What the record and the corpus show about the review: component statuses, flow counts, which included records are held and readable, what the corpus holds that
    the screening log never mentions. It does not say the search was adequate."""
    p = _problem(store, problem_id)
    rp = p["content"].get("review_protocol")
    if not rp:
        return {"problem_id": problem_id, "problem_revision": p["revision"], "protocol_status": "NOT_RECORDED", "components": {}, "readiness": {"can_build_landscape": False,
                "blockers": ["no review protocol is recorded on the problem"]}, "limits": []}
    comps = component_statuses(rp)
    sp, sc = rp.get("search") or {}, rp.get("screening") or {}
    log = sc.get("log") or []
    lookup = _corpus_lookup(store)
    prisma = None
    try:
        ident = {"records_identified": {s.get("name"): s.get("records_identified") for s in sp.get("sources", []) if s.get("status") == "executed" and isinstance(s.get("records_identified"), int)}}
        prisma = prisma_counts(log, ident) if log else None
    except KnowledgeError:
        prisma = None
    final: dict[str, dict[str, Any]] = {}
    for e in log:                       # the last decision per record at the deepest stage reached
        final[e["record"]] = e
    included = [r for r, e in final.items() if e["decision"] == "include" and e["stage"] == "eligibility"]
    inc_rows = []
    for r in sorted(included):
        hit = lookup.get(r.casefold())
        e = final[r]
        inc_rows.append({"record": r, "stream": e.get("stream"), "source_id": hit["source_id"] if hit else None, "source_revision": hit["revision"] if hit else None,
                         "tier": hit["tier"] if hit else None, "title": hit["title"] if hit else None, "in_corpus": bool(hit), "full_text": bool(hit and hit["tier"] == "FULL_TEXT"),
                         "excluded_from_corpus": bool(hit and hit["excluded"])})
    mentioned = {k.casefold() for k in final}
    screened_ids = {lookup[k]["source_id"] for k in mentioned if k in lookup}
    unscreened = sorted({row["source_id"] for row in lookup.values() if row["source_id"] not in screened_ids and not row["excluded"]})
    streams = []
    for st in rp.get("streams") or []:
        mine = [r for r in inc_rows if r["stream"] == st.get("id")]
        streams.append({"id": st.get("id"), "name": st.get("name"), "question": st.get("question"), "terms": st.get("terms"), "included_records": len(mine),
                        "included_with_full_text": sum(1 for r in mine if r["full_text"])})
    blockers = []
    if not is_adopted(rp):
        blockers.append("the researcher has not adopted the current plan")
    for name, c in comps.items():
        if c["status"] not in ("COMPLETE", "NOT_PERFORMED_JUSTIFIED"):
            blockers.append(f"{name}: {c['status']} — " + "; ".join(c["problems"]))
    if not inc_rows:
        blockers.append("no record is included at the eligibility stage")
    missing = [r["record"] for r in inc_rows if not r["in_corpus"]]
    unreadable = [r["record"] for r in inc_rows if r["in_corpus"] and not r["full_text"]]
    if missing:
        blockers.append(f"{len(missing)} included record(s) are not in the corpus")
    if unreadable:
        blockers.append(f"{len(unreadable)} included record(s) have no full text (they cannot support or challenge a claim)")
    return {"problem_id": problem_id, "problem_revision": p["revision"], "protocol_status": "ADOPTED_BY_RESEARCHER" if is_adopted(rp) else "DRAFT", "drafted_by": rp.get("drafted_by"),
            "adopted_by": rp.get("adopted_by") if is_adopted(rp) else None, "adopted_at": rp.get("adopted_at") if is_adopted(rp) else None, "plan_digest": plan_digest(rp),
            "review_type": rp.get("review_type"), "components": comps, "streams": streams, "search": {"date_from": sp.get("date_from"), "date_to": sp.get("date_to"),
            "sources": sp.get("sources") or [], "inclusion_scope": sp.get("inclusion_scope")}, "prisma": prisma, "included": inc_rows, "included_not_in_corpus": missing,
            "included_without_full_text": unreadable, "held_but_never_screened": unscreened, "screening_decisions": len(log),
            "readiness": {"can_build_landscape": not blockers, "blockers": blockers},
            "limits": ["this audit compares the record with the corpus; it does not say the search was adequate, the criteria sound or the review complete",
                       *[str(x) for x in rp.get("limitations") or []]]}
