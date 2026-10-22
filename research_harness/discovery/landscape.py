"""The landscape of the literature on a problem (M5 WP6).

A landscape is built from a **current** retrieval run's reading list: each paper on it is read at one Source@revision with two reading protocols
(`RP_LANDSCAPE`, and the construct and mechanism fields of `RP_THEORY`), and every record that survives quote verification becomes an *entry* that points at its
EvidencePassage. Nothing here says which paper is right.

Three layers per entry, kept apart:

* `author_claim`: what the paper says. A verified quote, `AUTHOR_ASSERTED`; the entry's `text` is a model's restatement, checked against the quote only by ECA.
* `evidence` (A5): three separate dimensions, never summed into "established". `reading` is `AUTHOR_REPORTED`, or `READING_CONFIRMED` when ECA found the entry a fair reading
  of its passage and the researcher confirmed it. `inference` is `NOT_ASSESSED` until the researcher writes a method assessment (criteria, threats, basis from
  methodological literature); ECA never moves it. `reproduction` is `NOT_ATTEMPTED` until the researcher links a replication-package run or records a new-data replication;
  reproducing code says nothing about whether its design permits the inference.
* `inferences`: what the suite derives, listed apart and `PROVISIONAL` at most: a contradiction between findings of different works that name the same relationship
  with opposite direction, a difference between two versions of one work, constructs defined differently. A model derives none of them.

Versions: evidence stays at Source@revision. `work_id` only groups papers for reading; a claim extracted from one version is never attributed to another, and a
difference between versions of one work is a `version_difference`, never a contradiction between studies. For every work the landscape records which versions the
corpus holds, which was read, which carries entries (the evidence), and whether a later version than anything read is held (the latest is judged across the whole
Source Registry, not among the hits).
"""
from __future__ import annotations

import json
from typing import Any, Optional

from research_harness.core.canonical import canonical_json_hash
from research_harness.discovery import retrieval as RT
from research_harness.knowledge import corpus
from research_harness.knowledge.common import KnowledgeError, engine_origin, next_numbered_id
from research_harness.state.store import StateStore
from research_harness.workflows import eca
from research_harness.workflows import reading_protocols as rp

ROLES = ("owner", "author")
GENERATED_BY = "deterministic:landscape@1"
CONTRADICTIONS_BY = "deterministic:landscape.contradictions@1"
PROTOCOLS = ("RP_LANDSCAPE", "RP_THEORY")
FIELD_TO_TYPE = {("RP_LANDSCAPE", "finding"): "finding", ("RP_LANDSCAPE", "boundary_condition"): "boundary",
                 ("RP_LANDSCAPE", "identifying_assumption"): "identifying_assumption", ("RP_LANDSCAPE", "measurement"): "measurement",
                 ("RP_LANDSCAPE", "design"): "design", ("RP_LANDSCAPE", "stated_limitation"): "method_limitation",
                 ("RP_LANDSCAPE", "open_question"): "unresolved_question", ("RP_THEORY", "construct_definition"): "construct",
                 ("RP_THEORY", "assumed_mechanism"): "mechanism"}
TYPE_ORDER = ("construct", "mechanism", "design", "measurement", "identifying_assumption", "finding", "boundary", "method_limitation", "unresolved_question")
INFERENCE_KINDS = ("contradiction", "version_difference", "definitions_differ")
MAX_PAIRS_PER_RELATIONSHIP = 100
METHOD_VERDICTS = ("SUPPORTED_WITHIN_SCOPE", "LIMITED", "NOT_SUPPORTED")
REPRODUCTION_KINDS = ("package_run", "new_data")
VERSION_POLICIES = ("retrieved", "all")


def _norm(text: str) -> str:
    return " ".join((text or "").casefold().split())


# ------------------------------------------------------------------------------------------------ the object

def load_landscape(store: StateStore, landscape_id: str) -> dict[str, Any]:
    o = store.get_object(landscape_id)
    if not o or o["object_type"] != "Landscape":
        raise KnowledgeError("LANDSCAPE_NOT_FOUND", f"{landscape_id} is not a Landscape in this project")
    return o


def _put(store: StateStore, token: Optional[str], landscape_id: str, content: dict[str, Any], operation: str, *, expected_head: int,
         edges: Optional[list] = None) -> dict[str, Any]:
    with store.write_tx(token=token, require_roles=ROLES) as tx:
        if edges is None:
            head = tx.head(landscape_id)
            rows = tx.conn.execute("SELECT relation,target_id,target_revision,provenance FROM edges WHERE source_id=? AND source_revision=?",
                                   (landscape_id, head["head_revision"])).fetchall() if head else []
            edges = [(r[0], r[1], r[2], json.loads(r[3])) for r in rows]
        res = tx.add_revision(object_id=landscape_id, object_type="Landscape", content=content, edges=edges, expected_head=expected_head,
                              origin=engine_origin(operation, tx.actor_id))
        tx.audit(operation, "RECORDED", {"landscape_id": landscape_id, "revision": res["revision"]})
    return {"landscape_id": landscape_id, "revision": res["revision"]}


def list_landscapes(store: StateStore) -> list[dict[str, Any]]:
    out = []
    with store.read() as conn:
        for (oid,) in conn.execute("SELECT object_id FROM object_heads WHERE object_type='Landscape' ORDER BY object_id").fetchall():
            o = store.get_object(oid, conn=conn)
            c = o["content"]
            out.append({"landscape_id": oid, "revision": o["revision"], "label": c["label"], "retrieval_id": c["retrieval_id"], "entries": len(c["elements"]),
                        "inferences": len(c["inferences"]), "works_read": sum(1 for w in c["works"].values() if w["read"])})
    return out


# ------------------------------------------------------------------------------------------------ versions of a work, judged from the whole registry

def held_versions(store: StateStore, work_id: str) -> list[dict[str, Any]]:
    """Every non-excluded Source of the work now in the registry (any tier), oldest public date first."""
    out = []
    with store.read() as conn:
        for (oid,) in conn.execute("SELECT object_id FROM object_heads WHERE object_type='Source' ORDER BY object_id").fetchall():
            o = store.get_object(oid, conn=conn)
            c = o["content"]
            if (c.get("work_id") or oid) != work_id or c.get("excluded"):
                continue
            out.append({"source_id": oid, "source_revision": o["revision"], "tier": corpus.source_tier(c), "title": c.get("title"), "document_type": c.get("document_type"),
                        "review_status": c.get("review_status"), "first_public_date": c.get("first_public_date"), "version_id": c.get("version_id")})
    return sorted(out, key=lambda v: (v["first_public_date"] or "", v["source_id"]))


def latest_version(versions: list[dict[str, Any]]) -> tuple[Optional[str], str]:
    """`(source_id, basis)`: the latest held version by public date; None, and why, when the dates cannot say."""
    dated = [v for v in versions if v.get("first_public_date")]
    if len(versions) == 1:
        return versions[0]["source_id"], "the only version held"
    if not dated:
        return None, "no public date recorded for any held version: the latest cannot be told"
    best = max(dated, key=lambda v: (v["first_public_date"], v["source_id"]))
    undated = len(versions) - len(dated)
    return best["source_id"], "latest first_public_date among all held versions" + (f" ({undated} held version(s) have no date, so they could be later)" if undated else "")


# ------------------------------------------------------------------------------------------------ what will be read

def _reading_plan(store: StateStore, view: dict[str, Any], versions: str, only_works: Optional[list[str]]) -> dict[str, Any]:
    """The Source@revision to read for each work on the run's reading list (after the researcher's review of it)."""
    if versions not in VERSION_POLICIES:
        raise KnowledgeError("VERSIONS_POLICY_UNKNOWN", f"versions must be one of {VERSION_POLICIES}")
    wanted = set(only_works) if only_works else None
    unknown = (wanted or set()) - {g["work_id"] for g in view["reading_list"]}
    if unknown:
        raise KnowledgeError("WORK_NOT_ON_READING_LIST", f"not on the reading list of {view['retrieval_id']}: {sorted(unknown)}")
    read, skipped = [], []
    for g in view["reading_list"]:
        if wanted is not None and g["work_id"] not in wanted:
            skipped.append({"work_id": g["work_id"], "why": "not selected for this landscape"})
            continue
        if versions == "retrieved":
            chosen = [v for v in g["versions"] if v["source_id"] == g["best_hit"]["source_id"]]
        else:
            chosen = [v for v in g["versions"] if v["tier"] == "FULL_TEXT"]
        for v in chosen:
            if v["tier"] != "FULL_TEXT":
                skipped.append({"work_id": g["work_id"], "source_id": v["source_id"], "why": f"no full text ({v['tier']})"})
                continue
            read.append({"work_id": g["work_id"], "source_id": v["source_id"], "source_revision": v["source_revision"], "version_id": v.get("version_id"),
                         "retrieved": bool(v.get("retrieved")), "stage": g["stage"]})
    return {"read": read, "skipped": skipped}


def _check_unchanged(store: StateStore, read: list[dict[str, Any]]) -> None:
    changed = []
    for r in read:
        head = store.get_object(r["source_id"])
        if not head or head["revision"] != r["source_revision"]:
            changed.append(f"{r['source_id']} (searched at r{r['source_revision']}, now r{head['revision'] if head else '-'})")
    if changed:
        raise KnowledgeError("LANDSCAPE_SOURCE_CHANGED", "revised since the search, so the search no longer describes them: " + "; ".join(changed) + ". Search again.")


def estimate(store: StateStore, retrieval_id: str, *, versions: str = "retrieved", works: Optional[list[str]] = None) -> dict[str, Any]:
    """Model calls a build would make: one per (source, protocol) not already extracted at that Source@revision."""
    from research_harness.workflows.instrumentation import estimate_tokens
    view = RT.run_view(store, retrieval_id)
    plan = _reading_plan(store, view, versions, works)
    calls = tin = 0
    for r in plan["read"]:
        for pid in PROTOCOLS:
            if rp.extraction_status(store, r["source_id"], pid)["cached"]:
                continue
            calls += 1
            tin += estimate_tokens(corpus.source_text(store, r["source_id"], r["source_revision"])[1])[0]
    return {"llm_calls": calls, "input_tokens": tin, "sources": len(plan["read"]), "skipped": len(plan["skipped"])}


# ------------------------------------------------------------------------------------------------ suite inferences

def _key(label: Optional[str], aliases: dict[str, str]) -> Optional[str]:
    n = _norm(label or "")
    return aliases.get(n, n) or None


def alias_map(groups: list[list[str]]) -> dict[str, str]:
    """`{normalized name: normalized first name of its group}`. A name may be in one group only."""
    out: dict[str, str] = {}
    for g in groups:
        names = [_norm(x) for x in g if _norm(x)]
        if len(names) < 2:
            raise KnowledgeError("ALIAS_GROUP_TOO_SMALL", "name at least two names for the same construct or relationship")
        for n in names:
            if out.get(n, names[0]) != names[0]:
                raise KnowledgeError("ALIAS_CONFLICT", f"{n!r} is already declared the same as {out[n]!r}")
            out[n] = names[0]
    return out


def derive_inferences(elements: list[dict[str, Any]], aliases: dict[str, str]) -> list[dict[str, Any]]:
    """Suite inferences from the entries alone (no model): same relationship named, opposite direction, different works -> contradiction; the same across versions of
    one work -> version_difference; one construct defined in different words by different works -> definitions_differ. Pairs are listed with the conditions each side
    states; whether the scopes overlap is left to the researcher (`scope_overlap: NOT_ASSESSED`)."""
    out: list[dict[str, Any]] = []
    findings = [e for e in elements if e["category"] == "finding" and e["direction"] in ("positive", "negative", "null") and _key(e["label"], aliases)]
    by_key: dict[str, list[dict[str, Any]]] = {}
    for e in findings:
        by_key.setdefault(_key(e["label"], aliases), []).append(e)    # type: ignore[arg-type]
    boundaries = [e for e in elements if e["category"] == "boundary"]

    def side(e: dict[str, Any]) -> dict[str, Any]:
        conds = [b["id"] for b in boundaries if b["source_id"] == e["source_id"] and b["source_revision"] == e["source_revision"]
                 and (_key(b["label"], aliases) in (None, _key(e["label"], aliases)))]
        return {"entry": e["id"], "source_id": e["source_id"], "source_revision": e["source_revision"], "work_id": e["work_id"], "version_id": e.get("version_id"),
                "direction": e["direction"], "passage_id": e["author_claim"]["passage_id"], "passage_revision": e["author_claim"]["passage_revision"], "conditions": conds}

    n = 0
    for key in sorted(by_key):
        es = sorted(by_key[key], key=lambda e: e["id"])
        pairs = 0
        for i, a in enumerate(es):
            for b in es[i + 1:]:
                if a["direction"] == b["direction"] or (a["source_id"], a["source_revision"]) == (b["source_id"], b["source_revision"]):
                    continue
                kind = "contradiction" if a["work_id"] != b["work_id"] else "version_difference"
                sub = "null_vs_effect" if "null" in (a["direction"], b["direction"]) else "opposite_direction"
                if pairs >= MAX_PAIRS_PER_RELATIONSHIP:
                    break
                pairs += 1
                n += 1
                out.append({"id": f"I{n:03d}", "kind": kind, "subkind": sub, "relationship": key, "sides": [side(a), side(b)], "generated_by": CONTRADICTIONS_BY,
                            "status": "PROVISIONAL", "scope_overlap": "NOT_ASSESSED", "reviews": [],
                            "note": ("two versions of one work report different directions: which version a later step relies on is the researcher's choice, and neither is "
                                     "a separate study" if kind == "version_difference" else
                                     "different works name the same relationship with different direction; whether their populations, periods and estimands overlap is not assessed")})
    by_construct: dict[str, list[dict[str, Any]]] = {}
    for e in elements:
        if e["category"] == "construct" and _key(e["label"], aliases):
            by_construct.setdefault(_key(e["label"], aliases), []).append(e)    # type: ignore[arg-type]
    for key in sorted(by_construct):
        es = sorted(by_construct[key], key=lambda e: e["id"])
        works = {e["work_id"] for e in es}
        if len(works) >= 2 and len({_norm(e["text"]) for e in es}) >= 2:
            n += 1
            out.append({"id": f"I{n:03d}", "kind": "definitions_differ", "subkind": "wording", "relationship": key,
                        "sides": [{"entry": e["id"], "source_id": e["source_id"], "source_revision": e["source_revision"], "work_id": e["work_id"],
                                   "version_id": e.get("version_id"), "passage_id": e["author_claim"]["passage_id"], "passage_revision": e["author_claim"]["passage_revision"]}
                                  for e in es],
                        "generated_by": CONTRADICTIONS_BY, "status": "PROVISIONAL", "scope_overlap": "NOT_ASSESSED", "reviews": [],
                        "note": "the wording differs; whether the meaning does is for the researcher to judge"})
    return out


# ------------------------------------------------------------------------------------------------ build

def _entry(eid: str, rec: dict[str, Any], category: str, protocol_pin: str, src: dict[str, Any], passage: dict[str, Any], model_tag: str) -> dict[str, Any]:
    pc = passage["content"]
    return {"id": eid, "category": category, "text": rec["value"], "label": rec.get("label"), "direction": rec.get("type") if category == "finding" else None,
            "item_type": rec.get("type") if category == "identifying_assumption" else None, "protocol": protocol_pin, "field": rec["field"],
            "work_id": src["work_id"], "version_id": src.get("version_id"), "source_id": src["source_id"], "source_revision": src["source_revision"],
            "author_claim": {"status": "AUTHOR_ASSERTED", "passage_id": passage["object_id"], "passage_revision": passage["revision"], "quote": rec["quote"],
                             "locator": pc.get("locator"), "section": pc.get("section"), "char_start": rec["char_start"], "char_end": rec["char_end"]},
            "evidence_basis": [{"kind": "source_passage", "ref": passage["object_id"]}], "generated_by": model_tag, "risk_tier": "STANDARD"}


def build(store: StateStore, token: Optional[str], retrieval_id: str, adapter: Any, *, label: Optional[str] = None, versions: str = "retrieved",
          works: Optional[list[str]] = None, aliases: Optional[list[list[str]]] = None, carry_from: Optional[str] = None, allow_stale_corpus: bool = False,
          max_input_tokens: int = rp.DEFAULT_MAX_INPUT_TOKENS, reuse: bool = True) -> dict[str, Any]:
    """A landscape from a current retrieval run. Refused when the run was not searched, its problem or accepted terms changed since, or (unless the researcher
    says to build on the old snapshot) its corpus changed; a failed extraction stops the build and nothing is written."""
    view = RT.require_current(store, retrieval_id, allow_stale_corpus=allow_stale_corpus)
    run = RT.load_run(store, retrieval_id)
    rc = run["content"]
    amap = alias_map(aliases or [])
    plan = _reading_plan(store, view, versions, works)
    if not plan["read"]:
        raise KnowledgeError("LANDSCAPE_NOTHING_TO_READ", "no full text on the reading list to read")
    _check_unchanged(store, plan["read"])
    extracted: list[tuple[dict[str, Any], str, dict[str, Any]]] = []
    for item in plan["read"]:
        for pid in PROTOCOLS:
            res = rp.run_protocol(store, token, item["source_id"], pid, adapter, source_revision=item["source_revision"], max_input_tokens=max_input_tokens, reuse=reuse)
            if res["status"] == "FAILED":
                raise KnowledgeError("EXTRACTION_FAILED", f"{item['source_id']} ({pid}): {res.get('reason')}: {res.get('message')}", source=item["source_id"])
            extracted.append((item, pid, res))
    rows: list[tuple[tuple, dict[str, Any], str, str, dict[str, Any], dict[str, Any], str]] = []
    for item, pid, res in extracted:
        pin = res["protocol"]
        for rec in res["records"]:
            cat = FIELD_TO_TYPE.get((pid, rec["field"]))
            if cat is None:
                continue
            passage = store.get_object(rec["passage_id"], rec["revision"])
            model_tag = passage["content"].get("generated_by") or "model:unknown"
            rows.append(((TYPE_ORDER.index(cat), item["work_id"], item["source_id"], rec["char_start"]), rec, cat, pin, item, passage, model_tag))
    rows.sort(key=lambda r: r[0])
    elements = [_entry(f"E{n:03d}", r[1], r[2], r[3], r[4], r[5], r[6]) for n, r in enumerate(rows, 1)]
    inferences = derive_inferences(elements, amap)

    by_source: dict[str, dict[str, Any]] = {}
    for item, pid, res in extracted:
        s = by_source.setdefault(item["source_id"], {"source_id": item["source_id"], "source_revision": item["source_revision"], "work_id": item["work_id"],
                                                     "version_id": item.get("version_id"), "protocols": [], "extraction_runs": [], "reused": 0, "rejected_records": 0,
                                                     "text_sha256": (store.get_object(item["source_id"])["content"].get("text_sha256"))})
        s["protocols"].append(res["protocol"])
        s["extraction_runs"].append(res["run_id"])
        s["reused"] += 1 if res.get("reused") else 0
        s["rejected_records"] += len(res.get("rejected") or [])
    sources_read = sorted(by_source.values(), key=lambda s: s["source_id"])
    used = {e["source_id"] for e in elements}
    works_info: dict[str, Any] = {}
    for wid in sorted({i["work_id"] for i in plan["read"]}):
        held = held_versions(store, wid)
        read_ids = {s["source_id"] for s in sources_read if s["work_id"] == wid}
        retrieved_ids = {v["source_id"] for g in view["reading_list"] if g["work_id"] == wid for v in g["versions"] if v.get("retrieved")}
        latest, basis = latest_version(held)
        dates = {v["source_id"]: v["first_public_date"] for v in held}
        later_unread: Any
        if latest is None:
            later_unread = "UNKNOWN"
        else:
            later_unread = bool(latest not in read_ids and dates.get(latest) and any(dates.get(r) and dates[latest] > dates[r] for r in read_ids))
        works_info[wid] = {"work_id": wid, "latest_held": latest, "latest_basis": basis, "later_version_not_read": later_unread, "read": sorted(read_ids),
                           "used_as_evidence": sorted(read_ids & used),
                           "versions": [{**v, "retrieved": v["source_id"] in retrieved_ids, "read": v["source_id"] in read_ids,
                                         "entries": sum(1 for e in elements if e["source_id"] == v["source_id"])} for v in held],
                           "note": "evidence is cited at Source@revision (each entry's author_claim); the work groups papers for reading only"}
    unlabelled = sum(1 for e in elements if e["category"] == "finding" and (not e["label"] or e["direction"] not in ("positive", "negative", "null", "mixed", "descriptive")))
    unlabelled_cmp = sum(1 for e in elements if e["category"] == "finding" and not _key(e["label"], amap))
    counts: dict[str, int] = {}
    for e in elements:
        counts[e["category"]] = counts.get(e["category"], 0) + 1
    coverage = {"works_on_reading_list": len(view["reading_list"]), "works_read": len(works_info), "sources_read": len(sources_read), "entries": len(elements),
                "entries_by_type": counts, "inferences": {k: sum(1 for i in inferences if i["kind"] == k) for k in INFERENCE_KINDS},
                "findings_without_a_relationship_label": unlabelled_cmp, "records_rejected_in_extraction": sum(s["rejected_records"] for s in sources_read),
                "model_calls_reused": sum(s["reused"] for s in sources_read), "not_read": plan["skipped"], "possibly_relevant_not_read": len(view["not_read_candidates"]),
                "works_with_a_later_version_not_read": sorted(w for w, i in works_info.items() if i["later_version_not_read"] is True),
                "works_where_the_latest_version_cannot_be_told": sorted(w for w, i in works_info.items() if i["later_version_not_read"] == "UNKNOWN")}
    limits = ["a landscape lists what the papers on the reading list state; a topic or a competitor that was not retrieved is not shown to be absent (see the retrieval's coverage)",
              "entries are a model's restatement of a verified quote: ECA decides whether each is a fair reading, and until the researcher confirms it the entry is only AUTHOR_REPORTED",
              "contradictions are derived only between findings whose relationship the extraction named in the same words (or words you declared the same); a contradiction stated in different words is missed",
              "whether two contradicting findings concern overlapping populations, periods and estimands is not assessed; it is left to you, with the conditions each paper states",
              "no entry says a result is established: `reading`, `inference` and `reproduction` are shown separately and `inference` stays NOT_ASSESSED until you write a method assessment",
              "the introduction argument and contribution claims of the papers are not part of this landscape"]
    carried = _carry(store, carry_from, elements, inferences) if carry_from else {"assessments": [], "reproduction_links": [], "reviews": {}}
    for i in inferences:
        i["reviews"] = carried["reviews"].get(_inference_key(i), [])
    content = {"label": label or f"Landscape for {rc['problem_id']}", "kind": "landscape", "state": "BUILT", "retrieval_id": retrieval_id, "retrieval_revision": run["revision"],
               "problem_id": rc["problem_id"], "problem_revision": rc["problem_revision"], "corpus_snapshot_id": rc.get("corpus_snapshot_id"), "versions_policy": versions,
               "protocols": [rp.get_protocol(p).pin for p in PROTOCOLS], "sources_read": sources_read, "works": works_info, "elements": elements, "inferences": inferences,
               "construct_aliases": aliases or [], "assessments": carried["assessments"], "reproduction_links": carried["reproduction_links"], "carried_from": carry_from,
               "coverage": coverage, "limits": limits, "edits": [], "generated_by": GENERATED_BY}
    edges = [("depends_on", retrieval_id, run["revision"], {"edge_kind": "landscape_from"})]
    edges += [("derived_from", s["source_id"], s["source_revision"], {"edge_kind": "read_for_landscape"}) for s in sources_read]
    with store.read() as conn:
        lid = next_numbered_id(conn, "LAND_", "Landscape")
    out = _put(store, token, lid, content, "landscape_build", expected_head=0, edges=edges)
    return {**out, "entries": len(elements), "inferences": coverage["inferences"], "works_read": len(works_info), "sources_read": len(sources_read),
            "model_calls": sum(len(s["protocols"]) - s["reused"] for s in sources_read)}


def _inference_key(i: dict[str, Any]) -> str:
    return canonical_json_hash([i["kind"], i["relationship"], sorted((s["passage_id"], s["passage_revision"]) for s in i["sides"])])


def _carry(store: StateStore, carry_from: str, elements: list[dict[str, Any]], inferences: list[dict[str, Any]]) -> dict[str, Any]:
    """The researcher's own records from an earlier landscape, kept where the passage and wording they were about are unchanged."""
    old = load_landscape(store, carry_from)["content"]
    now = {(e["author_claim"]["passage_id"], e["author_claim"]["passage_revision"]): e for e in elements}
    prev = {e["id"]: e for e in old["elements"]}
    out: dict[str, Any] = {"assessments": [], "reproduction_links": [], "reviews": {}}
    for kind in ("assessments", "reproduction_links"):
        for rec in old[kind]:
            e0 = prev.get(rec["entry"])
            e1 = now.get((e0["author_claim"]["passage_id"], e0["author_claim"]["passage_revision"])) if e0 else None
            if e1 and _norm(e1["text"]) == _norm(e0["text"]):
                out[kind].append({**rec, "entry": e1["id"], "carried_from": carry_from})
    for i in old["inferences"]:
        if i["reviews"]:
            out["reviews"][_inference_key(i)] = [{**r, "carried_from": carry_from} for r in i["reviews"]]
    return out


# ------------------------------------------------------------------------------------------------ the researcher's records

def _need_text(value: Optional[str], what: str) -> str:
    if not value or not value.strip():
        raise KnowledgeError("REASON_REQUIRED", f"{what} is required")
    return value.strip()


def review_inference(store: StateStore, token: Optional[str], landscape_id: str, inference_id: str, decision: str, reason: str) -> dict[str, Any]:
    """The researcher's decision on a suite inference: CONFIRMED (the studies do conflict on overlapping scope), DISMISSED (they do not), or NEEDS_SCOPE. Recorded with
    who and when; the inference itself stays PROVISIONAL and generated by the suite."""
    if decision not in ("CONFIRMED", "DISMISSED", "NEEDS_SCOPE"):
        raise KnowledgeError("DECISION_UNKNOWN", "decision must be CONFIRMED, DISMISSED or NEEDS_SCOPE")
    reason = _need_text(reason, "your reason")
    cur = load_landscape(store, landscape_id)
    c = cur["content"]
    inf = next((i for i in c["inferences"] if i["id"] == inference_id), None)
    if inf is None:
        raise KnowledgeError("INFERENCE_UNKNOWN", f"{landscape_id} has no inference {inference_id}")
    with store.write_tx(token=token, require_roles=ROLES) as tx:
        head = tx.get(landscape_id)
        if head["revision"] != cur["revision"]:
            raise KnowledgeError("STALE_REVISION", "the landscape changed while you were deciding")
        rec = {"at": tx.at, "by": tx.actor_id, "decision": decision, "reason": reason}
        new = [{**i, "reviews": [*i["reviews"], rec]} if i["id"] == inference_id else i for i in c["inferences"]]
        rows = tx.conn.execute("SELECT relation,target_id,target_revision,provenance FROM edges WHERE source_id=? AND source_revision=?",
                               (landscape_id, head["revision"])).fetchall()
        res = tx.add_revision(object_id=landscape_id, object_type="Landscape", content={**c, "inferences": new}, expected_head=cur["revision"],
                              edges=[(r[0], r[1], r[2], json.loads(r[3])) for r in rows], origin=engine_origin("landscape_review_inference", tx.actor_id))
        tx.audit("landscape_review_inference", "RECORDED", {"landscape_id": landscape_id, "inference": inference_id, "decision": decision})
    return {"landscape_id": landscape_id, "revision": res["revision"], "inference": inference_id, "decision": decision}


def _finding(c: dict[str, Any], entry_id: str) -> dict[str, Any]:
    e = next((x for x in c["elements"] if x["id"] == entry_id), None)
    if e is None:
        raise KnowledgeError("ENTRY_UNKNOWN", f"no entry {entry_id} in this landscape")
    if e["category"] != "finding":
        raise KnowledgeError("ENTRY_NOT_A_FINDING", f"{entry_id} is a {e['category']}: method assessments and reproduction links are about findings")
    return e


def _append(store: StateStore, token: Optional[str], landscape_id: str, key: str, record: dict[str, Any], operation: str) -> dict[str, Any]:
    cur = load_landscape(store, landscape_id)
    c = cur["content"]
    with store.write_tx(token=token, require_roles=ROLES) as tx:
        head = tx.get(landscape_id)
        if head["revision"] != cur["revision"]:
            raise KnowledgeError("STALE_REVISION", "the landscape changed while you were writing")
        rec = {**record, "at": tx.at, "by": tx.actor_id}
        rows = tx.conn.execute("SELECT relation,target_id,target_revision,provenance FROM edges WHERE source_id=? AND source_revision=?",
                               (landscape_id, head["revision"])).fetchall()
        res = tx.add_revision(object_id=landscape_id, object_type="Landscape", content={**c, key: [*c[key], rec]}, expected_head=cur["revision"],
                              edges=[(r[0], r[1], r[2], json.loads(r[3])) for r in rows], origin=engine_origin(operation, tx.actor_id))
        tx.audit(operation, "RECORDED", {"landscape_id": landscape_id, "entry": record["entry"]})
    return {"landscape_id": landscape_id, "revision": res["revision"], "entry": record["entry"], "record": rec}


def method_verdict(declared: str, criteria: list[dict[str, Any]], threats: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """The verdict a method assessment may carry: `SUPPORTED_WITHIN_SCOPE` only with a basis from methodological literature, every criterion met and no threat open
    (the rule `assess_validity` applies to a method's validity); otherwise it is capped at LIMITED, with the blockers named. NOT_SUPPORTED needs a criterion that
    is not met, with its basis."""
    blockers: list[str] = []
    if declared == "NOT_SUPPORTED":
        failing = [c for c in criteria if c["met"] == "no"]
        if not failing:
            raise KnowledgeError("NOT_SUPPORTED_NEEDS_A_FAILED_CRITERION", "NOT_SUPPORTED needs a criterion marked met=no, with its basis")
        return "NOT_SUPPORTED", []
    if declared == "SUPPORTED_WITHIN_SCOPE":
        if not any(c.get("methodological_literature") for c in criteria):
            blockers.append("no criterion rests on methodological literature (application papers alone do not show a design permits an inference)")
        if any(c["met"] != "yes" for c in criteria):
            blockers.append("not every criterion is met")
        if any(t["status"] == "open" for t in threats):
            blockers.append("a threat is still open")
        if not threats:
            blockers.append("no threat was checked")
        if not blockers:
            return "SUPPORTED_WITHIN_SCOPE", []
    return "LIMITED", blockers


def assess_method(store: StateStore, token: Optional[str], landscape_id: str, entry_id: str, *, verdict: str, criteria: list[dict[str, Any]], threats: list[dict[str, Any]],
                  rationale: str, code_evidence: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    """The researcher's method assessment of one finding (A5 `inference`): does the paper's design permit the inference its finding draws? Criteria each name their
    basis; threats each say whether addressed, scoped out or open. Written only by an author or owner; a model never writes one. The verdict stored is capped by
    `method_verdict`."""
    if verdict not in METHOD_VERDICTS:
        raise KnowledgeError("VERDICT_UNKNOWN", f"verdict must be one of {METHOD_VERDICTS}")
    rationale = _need_text(rationale, "your rationale")
    if not criteria:
        raise KnowledgeError("CRITERIA_REQUIRED", "name at least one criterion, with its basis")
    for c in criteria:
        if not (c.get("criterion") or "").strip() or not (c.get("basis") or "").strip():
            raise KnowledgeError("CRITERION_BASIS_REQUIRED", "each criterion needs its wording and the basis it rests on")
        if c.get("met") not in ("yes", "no", "unclear"):
            raise KnowledgeError("CRITERION_MET_INVALID", "met is yes, no or unclear")
    for t in threats:
        if not (t.get("threat") or "").strip() or t.get("status") not in ("addressed", "scoped_out", "open"):
            raise KnowledgeError("THREAT_INVALID", "a threat has its wording and a status of addressed, scoped_out or open")
    for ev in code_evidence or []:
        if not ev.get("file_sha256") or not ev.get("lines"):
            raise KnowledgeError("CODE_EVIDENCE_INCOMPLETE", "code evidence names the file's sha256 and a line range")
    cur = load_landscape(store, landscape_id)
    entry = _finding(cur["content"], entry_id)
    capped, blockers = method_verdict(verdict, criteria, threats)
    rec = {"entry": entry_id, "passage": [entry["author_claim"]["passage_id"], entry["author_claim"]["passage_revision"]], "declared_verdict": verdict, "verdict": capped,
           "blockers": blockers, "criteria": criteria, "threats": threats, "code_evidence": code_evidence or [], "rationale": rationale, "generated_by": "researcher"}
    out = _append(store, token, landscape_id, "assessments", rec, "landscape_assess_method")
    return {**out, "verdict": capped, "blockers": blockers}


def link_reproduction(store: StateStore, token: Optional[str], landscape_id: str, entry_id: str, *, kind: str, reason: str, package_id: Optional[str] = None,
                      outputs: Optional[list[str]] = None, reference: Optional[str] = None) -> dict[str, Any]:
    """Tie a finding to a reproduction (A5 `reproduction`). `package_run`: a replication package with a recorded reproduction (`research ... record-reproduction`) and the
    outputs of it that concern this finding; the status is read from the package each time it is shown. `new_data`: the researcher records a replication on new
    data, by a reference they name; the suite did not check it."""
    if kind not in REPRODUCTION_KINDS:
        raise KnowledgeError("REPRODUCTION_KIND_UNKNOWN", f"kind must be one of {REPRODUCTION_KINDS}")
    reason = _need_text(reason, "your reason")
    cur = load_landscape(store, landscape_id)
    _finding(cur["content"], entry_id)
    rec: dict[str, Any] = {"entry": entry_id, "kind": kind, "reason": reason}
    if kind == "package_run":
        pkg = store.get_object(package_id or "")
        if not pkg or pkg["object_type"] != "KnowledgeItem" or pkg["content"].get("kind") != "replication_package":
            raise KnowledgeError("NOT_A_REPLICATION_PACKAGE", f"{package_id} is not a replication package")
        stamp = pkg["content"].get("reproduction")
        if not stamp:
            raise KnowledgeError("REPRODUCTION_NOT_RECORDED", f"{package_id} has no recorded reproduction: run the package first")
        known = {o["id"] for o in stamp["outputs"]}
        if not outputs or set(outputs) - known:
            raise KnowledgeError("OUTPUTS_UNKNOWN", f"name the outputs of {package_id} that concern this finding; it recorded {sorted(known)}")
        rec.update(package=package_id, package_revision=pkg["revision"], outputs=sorted(outputs))
    else:
        rec["reference"] = _need_text(reference, "the reference of the new-data replication")
        rec["checked_by_suite"] = False
    return _append(store, token, landscape_id, "reproduction_links", rec, "landscape_link_reproduction")


# ------------------------------------------------------------------------------------------------ reading a landscape

def landscape_currency(store: StateStore, c: dict[str, Any]) -> dict[str, Any]:
    """Is what the landscape was built from still what the project holds? Four inputs, each its own status: the retrieval run's own currency (corpus, problem and accepted
    terms), the run's revision (a review of its reading list after the build), and the Source revisions read."""
    run = store.get_object(c["retrieval_id"])
    view = RT.run_view(store, c["retrieval_id"]) if run else None
    changed = []
    for s in c["sources_read"]:
        head = store.get_object(s["source_id"])
        if not head or head["revision"] != s["source_revision"]:
            changed.append(s["source_id"])
    cur = (view or {}).get("currency", {})
    retrieval = {"status": "MISSING"} if run is None else (
        {"status": "CURRENT", "revision": run["revision"]} if run["revision"] == c["retrieval_revision"] else
        {"status": "REVISED", "recorded_revision": c["retrieval_revision"], "current_revision": run["revision"],
         "note": "the run was edited or searched again after this landscape was built (a paper set aside, restored or added, or a new search)"})
    out = {"retrieval": retrieval, "corpus": cur.get("corpus", {"status": "UNKNOWN"}), "problem": cur.get("problem", {"status": "UNKNOWN"}),
           "search_config": cur.get("search_config", {"status": "UNKNOWN"}), "sources_read": {"status": "CURRENT" if not changed else "STALE", "changed": changed}}
    out["all_current"] = (retrieval["status"] == "CURRENT" and out["corpus"]["status"] == "CURRENT" and out["problem"]["status"] == "CURRENT"
                          and out["search_config"]["status"] == "CURRENT" and not changed)
    return out


def _reproduction(store: StateStore, links: list[dict[str, Any]]) -> dict[str, Any]:
    if not links:
        return {"value": "NOT_ATTEMPTED"}
    link = links[-1]
    if link["kind"] == "new_data":
        return {"value": "REPLICATED_NEW_DATA", "reference": link["reference"], "checked_by_suite": False, "by": link["by"], "at": link["at"]}
    pkg = store.get_object(link["package"])
    stamp = (pkg or {}).get("content", {}).get("reproduction") if pkg else None
    if not stamp:
        return {"value": "NOT_ATTEMPTED", "note": f"{link['package']} no longer has a recorded reproduction"}
    sts = [o["status"] for o in stamp["outputs"] if o["id"] in link["outputs"]]
    value = ("FAILED_TO_REPRODUCE" if "FAILED" in sts else "REPRODUCED_WITHIN_TOLERANCE" if sts and all(s == "REPRODUCED" for s in sts) else "NOT_ATTEMPTED")
    return {"value": value, "package": link["package"], "outputs": link["outputs"], "package_revision_linked": link["package_revision"],
            "package_revision_now": pkg["revision"], "checked_at": stamp.get("checked_at"),
            "note": "" if value != "NOT_ATTEMPTED" else "the linked outputs were not all computed against the paper's value"}


def _inference_dimension(assessments: list[dict[str, Any]], entry: dict[str, Any]) -> dict[str, Any]:
    mine = [a for a in assessments if a["entry"] == entry["id"] and a["passage"] == [entry["author_claim"]["passage_id"], entry["author_claim"]["passage_revision"]]]
    if not mine:
        return {"value": "NOT_ASSESSED"}
    a = mine[-1]
    return {"value": "METHOD_CRITICALLY_ASSESSED", "verdict": a["verdict"], "blockers": a["blockers"], "by": a["by"], "at": a["at"], "rationale": a["rationale"]}


def _reading_dimension(store: StateStore, landscape_id: str, entry_id: str) -> dict[str, Any]:
    st = eca.statement_assessment(store, eca.resolve_statement(store, landscape_id, element=entry_id))
    confirmed = st["researcher_confirmed"] and st["eca_verdict"] in ("SUPPORTED_AS_STATED", "SUPPORTED_IF_QUALIFIED")
    return {"value": "READING_CONFIRMED" if confirmed else "AUTHOR_REPORTED", "eca_verdict": st["eca_verdict"], "disputed": st["eca_verdict"] == "NOT_SUPPORTED",
            "adjudications": st["adjudications"], "reasons": st["reasons"]}


def view(store: StateStore, landscape_id: str) -> dict[str, Any]:
    land = load_landscape(store, landscape_id)
    c = land["content"]
    entries = []
    for e in c["elements"]:
        evid: dict[str, Any] = {"reading": _reading_dimension(store, landscape_id, e["id"])}
        if e["category"] == "finding":
            evid["inference"] = _inference_dimension(c["assessments"], e)
            evid["reproduction"] = _reproduction(store, [x for x in c["reproduction_links"] if x["entry"] == e["id"]])
        inf = [i["id"] for i in c["inferences"] if any(s["entry"] == e["id"] for s in i["sides"])]
        entries.append({**e, "layers": {"author_claim": e["author_claim"], "evidence": evid, "suite_inference": inf}})
    return {"landscape_id": landscape_id, "revision": land["revision"], "label": c["label"], "retrieval_id": c["retrieval_id"], "problem_id": c["problem_id"],
            "problem_revision": c["problem_revision"], "corpus_snapshot_id": c["corpus_snapshot_id"], "versions_policy": c["versions_policy"], "protocols": c["protocols"],
            "currency": landscape_currency(store, c), "works": c["works"], "entries": entries, "inferences": c["inferences"], "construct_aliases": c["construct_aliases"],
            "sources_read": c["sources_read"], "coverage": c["coverage"], "limits": c["limits"], "carried_from": c["carried_from"]}


def require_current(store: StateStore, landscape_id: str, *, allow_stale_corpus: bool = False) -> dict[str, Any]:
    """The landscape's view, or an error naming the input that is out of date. A later step (candidate generation) calls this."""
    v = view(store, landscape_id)
    cur = v["currency"]
    for key, code in (("problem", "LANDSCAPE_PROBLEM_STALE"), ("search_config", "LANDSCAPE_CONFIG_STALE"), ("sources_read", "LANDSCAPE_SOURCE_CHANGED"),
                      ("retrieval", "LANDSCAPE_RETRIEVAL_REVISED")):
        if cur[key]["status"] != "CURRENT":
            raise KnowledgeError(code, f"{landscape_id}: {key} is {cur[key]['status']}; build the landscape again from a current run")
    if cur["corpus"]["status"] != "CURRENT" and not allow_stale_corpus:
        raise KnowledgeError("LANDSCAPE_CORPUS_STALE", f"{landscape_id}: the corpus changed since it was built; rebuild, or build on the old snapshot explicitly")
    return v
