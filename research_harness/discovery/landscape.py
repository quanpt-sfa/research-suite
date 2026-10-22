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

from jsonschema import Draft202012Validator

from research_harness import taxonomy
from research_harness.core.canonical import canonical_json_hash
from research_harness.discovery import basis as BAS
from research_harness.discovery import inspection as INS
from research_harness.discovery import relations as REL
from research_harness.discovery import retrieval as RT
from research_harness.discovery import text_index as X
from research_harness.discovery.relations import PROPOSED_ONLY_KINDS  # noqa: F401  (re-exported)
from research_harness.knowledge import corpus
from research_harness.knowledge.common import KnowledgeError, engine_origin, id_fragment, next_numbered_id
from research_harness.state import workflow_runs
from research_harness.state.store import StateStore
from research_harness.workflows import eca, quotes
from research_harness.workflows import reading_protocols as rp
from research_harness.workflows.adapters import ModelError, adapter_identity
from research_harness.workflows.model_io import parse_model_output
from research_harness.workflows.status import Applicability, AssessmentStatus, ContextStatus, ExecutionStatus

ROLES = ("owner", "author")
GENERATED_BY = "deterministic:landscape@1"
CONTRADICTIONS_BY = "deterministic:landscape.contradictions@1"
PROTOCOLS = ("RP_LANDSCAPE", "RP_THEORY")
FIELD_TO_TYPE = {("RP_LANDSCAPE", "theoretical_claim"): "theoretical_claim", ("RP_LANDSCAPE", "interpretive_finding"): "interpretive_finding",
                 ("RP_THEORY", "theory_invoked"): "theory", ("RP_THEORY", "stated_assumption"): "stated_assumption", ("RP_THEORY", "assumption_tested"): "stated_assumption",
                 ("RP_LANDSCAPE", "finding"): "finding", ("RP_LANDSCAPE", "boundary_condition"): "boundary",
                 ("RP_LANDSCAPE", "identifying_assumption"): "identifying_assumption", ("RP_LANDSCAPE", "measurement"): "measurement",
                 ("RP_LANDSCAPE", "design"): "design", ("RP_LANDSCAPE", "stated_limitation"): "method_limitation",
                 ("RP_LANDSCAPE", "open_question"): "unresolved_question", ("RP_THEORY", "construct_definition"): "construct",
                 ("RP_THEORY", "assumed_mechanism"): "mechanism"}
ITEM_TYPE_OF_FIELD = {("RP_THEORY", "stated_assumption"): "stated", ("RP_THEORY", "assumption_tested"): "tested_in_source"}
TYPE_ORDER = ("theory", "construct", "mechanism", "stated_assumption", "design", "measurement", "identifying_assumption", "theoretical_claim", "finding", "interpretive_finding",
              "boundary", "method_limitation", "unresolved_question")
CLAIM_KIND_OF = {"finding": "empirical", "interpretive_finding": "interpretive", "theoretical_claim": "conceptual"}
INFERENCE_KINDS = ("contradiction", "version_difference", "definitions_differ") + REL.PROPOSABLE_KINDS
ROUTE_BASIS = {"empirical": ("methodological_literature",), "interpretive": ("methodological_literature", "textual_evidence"), "conceptual": ("theoretical_literature", "logical_analysis")}
CONSIDERATIONS = {
    "empirical": ["measurement", "design", "identifying assumptions", "diagnostics", "limits of inference"],
    "interpretive": ["transparency of interpretation", "context of the material", "alternative interpretations considered", "credibility", "reflexivity where it applies"],
    "conceptual": ["conceptual clarity", "logical coherence", "consistency of assumptions", "treatment of rival explanations", "correspondence with prior theory", "boundaries of applicability"],
    "critical_synthesis": ["faithful reconstruction of the prior literature", "evidence that an attributed assumption exists", "fair treatment of competing interpretations",
                           "the reasoning from the identified tension to the proposed reconceptualisation"],
}
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


def _with_summary(c: dict[str, Any]) -> dict[str, Any]:
    """Flat facts a workflow contract can ask about (`exists Landscape where ...`) without reading inside lists of records. Derived, rewritten on every revision."""
    infs, insp = c.get("inferences") or [], c.get("inspections") or []
    return {**c, "entry_categories": sorted({e["category"] for e in c["elements"]}), "has_proposals": any(i.get("derivation", "rule") != "rule" for i in infs),
            "has_reviewed_inference": any(i.get("reviews") for i in infs), "has_inspection": bool(insp),
            "has_completed_inspection": any(INS.status_of(x)[0] in ("ADDRESSED_IN_INSPECTED_SET", "NOT_FOUND_IN_INSPECTED_SET") for x in insp)}


def _put(store: StateStore, token: Optional[str], landscape_id: str, content: dict[str, Any], operation: str, *, expected_head: int,
         edges: Optional[list] = None, run_row: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    content = _with_summary(content)
    with store.write_tx(token=token, require_roles=ROLES) as tx:
        if edges is None:
            head = tx.head(landscape_id)
            rows = tx.conn.execute("SELECT relation,target_id,target_revision,provenance FROM edges WHERE source_id=? AND source_revision=?",
                                   (landscape_id, head["head_revision"])).fetchall() if head else []
            edges = [(r[0], r[1], r[2], json.loads(r[3])) for r in rows]
        res = tx.add_revision(object_id=landscape_id, object_type="Landscape", content=content, edges=edges, expected_head=expected_head,
                              origin=engine_origin(operation, tx.actor_id))
        tx.audit(operation, "RECORDED", {"landscape_id": landscape_id, "revision": res["revision"]})
        rid = workflow_runs.insert_run_tx(tx, run_row) if run_row is not None else None
    return {"landscape_id": landscape_id, "revision": res["revision"], "run_id": rid}


def _write_with(store: StateStore, token: Optional[str], landscape_id: str, build: Any, operation: str, run_row: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """One transaction: the next revision, built from the content as it is inside the transaction (`build(content, tx) -> content`), its audit row and optionally the run."""
    with store.write_tx(token=token, require_roles=ROLES) as tx:
        head = tx.head(landscape_id)
        cur = tx.get(landscape_id)
        content = build(cur["content"], tx)
        if content == cur["content"]:                  # nothing to record (for example every proposal was rejected): no new revision, but the run is still registered
            rid = workflow_runs.insert_run_tx(tx, run_row) if run_row is not None else None
            return {"landscape_id": landscape_id, "revision": cur["revision"], "run_id": rid}
        content = _with_summary(content)
        rows = tx.conn.execute("SELECT relation,target_id,target_revision,provenance FROM edges WHERE source_id=? AND source_revision=?", (landscape_id, head["head_revision"])).fetchall()
        res = tx.add_revision(object_id=landscape_id, object_type="Landscape", content=content, expected_head=head["head_revision"],
                              edges=[(r[0], r[1], r[2], json.loads(r[3])) for r in rows], origin=engine_origin(operation, tx.actor_id))
        tx.audit(operation, "RECORDED", {"landscape_id": landscape_id, "revision": res["revision"]})
        rid = workflow_runs.insert_run_tx(tx, run_row) if run_row is not None else None
    return {"landscape_id": landscape_id, "revision": res["revision"], "run_id": rid}


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
                out.append({"id": f"I{n:03d}", "kind": kind, "subkind": sub, "relation": "empirical_disagreement" if kind == "contradiction" else "version_difference", "derivation": "rule",
                            "provenance": "rule:" + CONTRADICTIONS_BY, "relationship": key, "sides": [side(a), side(b)], "generated_by": CONTRADICTIONS_BY,
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
            out.append({"id": f"I{n:03d}", "kind": "definitions_differ", "subkind": "wording", "relation": "observation", "derivation": "rule",
                        "provenance": "rule:" + CONTRADICTIONS_BY, "relationship": key,
                        "sides": [{"entry": e["id"], "source_id": e["source_id"], "source_revision": e["source_revision"], "work_id": e["work_id"],
                                   "version_id": e.get("version_id"), "passage_id": e["author_claim"]["passage_id"], "passage_revision": e["author_claim"]["passage_revision"]}
                                  for e in es],
                        "generated_by": CONTRADICTIONS_BY, "status": "PROVISIONAL", "scope_overlap": "NOT_ASSESSED", "reviews": [],
                        "note": "the wording differs; whether the meaning does is for the researcher to judge"})
    return out


# ------------------------------------------------------------------------------------------------ build

def _entry(eid: str, rec: dict[str, Any], category: str, protocol_pin: str, src: dict[str, Any], passage: dict[str, Any], model_tag: str, pid: str = "") -> dict[str, Any]:
    pc = passage["content"]
    item_type = ITEM_TYPE_OF_FIELD.get((pid, rec["field"])) or (rec.get("type") if category == "identifying_assumption" else None)
    return {"id": eid, "category": category, "text": rec["value"], "label": rec.get("label"), "direction": rec.get("type") if category == "finding" else None,
            "claim_kind": CLAIM_KIND_OF.get(category), "claim_type": rec.get("type") if category == "theoretical_claim" else None, "provenance": "author_stated",
            "item_type": item_type, "protocol": protocol_pin, "field": rec["field"],
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
    rows: list[tuple[tuple, dict[str, Any], str, str, dict[str, Any], dict[str, Any], str, str]] = []
    for item, pid, res in extracted:
        pin = res["protocol"]
        for rec in res["records"]:
            cat = FIELD_TO_TYPE.get((pid, rec["field"]))
            if cat is None:
                continue
            passage = store.get_object(rec["passage_id"], rec["revision"])
            model_tag = passage["content"].get("generated_by") or "model:unknown"
            rows.append(((TYPE_ORDER.index(cat), item["work_id"], item["source_id"], rec["char_start"]), rec, cat, pin, item, passage, model_tag, pid))
    rows.sort(key=lambda r: r[0])
    elements = [_entry(f"E{n:03d}", r[1], r[2], r[3], r[4], r[5], r[6], r[7]) for n, r in enumerate(rows, 1)]
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
                "entries_by_type": counts, "inferences": {k: sum(1 for i in inferences if i["kind"] == k) for k in INFERENCE_KINDS[:3]},
                "findings_without_a_relationship_label": unlabelled_cmp, "records_rejected_in_extraction": sum(s["rejected_records"] for s in sources_read),
                "model_calls_reused": sum(s["reused"] for s in sources_read), "not_read": plan["skipped"], "possibly_relevant_not_read": len(view["not_read_candidates"]),
                "works_with_a_later_version_not_read": sorted(w for w, i in works_info.items() if i["later_version_not_read"] is True),
                "works_where_the_latest_version_cannot_be_told": sorted(w for w, i in works_info.items() if i["later_version_not_read"] == "UNKNOWN")}
    limits = ["a landscape lists what the papers on the reading list state; a topic or a competitor that was not retrieved is not shown to be absent (see the retrieval's coverage)",
              "entries are a model's restatement of a verified quote: ECA decides whether each is a fair reading, and until the researcher confirms it the entry is only AUTHOR_REPORTED",
              "contradictions are derived only between findings whose relationship the extraction named in the same words (or words you declared the same); a contradiction stated in different words is missed",
              "whether two contradicting findings concern overlapping populations, periods and estimands is not assessed; it is left to you, with the conditions each paper states",
              "no entry says a result is established: `reading`, `inference` and `reproduction` are shown separately and `inference` stays NOT_ASSESSED until you write a method assessment",
              "the introduction argument and contribution claims of the papers are not part of this landscape",
              "relations that need a reading of meaning (conceptual divergence, theoretical incompatibility, explanatory incompleteness, boundary tension, complementarity) and implied assumptions "
              "exist only as proposals, unconfirmed until you decide; `definitions_differ` is an observation about wording and never becomes a divergence of meaning"]
    carried = _carry(store, carry_from, elements, inferences) if carry_from else {"assessments": [], "reproduction_links": [], "reviews": {}, "proposals": []}
    for i in inferences:
        i["reviews"] = carried["reviews"].get(_inference_key(i), [])
    for pr in carried["proposals"]:
        inferences.append({**pr, "id": f"I{len(inferences) + 1:03d}"})
    problem_obj = store.get_object(rc["problem_id"], rc["problem_revision"])
    content = {"label": label or f"Landscape for {rc['problem_id']}", "kind": "landscape", "state": "BUILT", "retrieval_id": retrieval_id, "retrieval_revision": run["revision"],
               "problem_id": rc["problem_id"], "problem_revision": rc["problem_revision"], "corpus_snapshot_id": rc.get("corpus_snapshot_id"), "versions_policy": versions,
               "protocols": [rp.get_protocol(p).pin for p in PROTOCOLS], "sources_read": sources_read, "works": works_info, "elements": elements, "inferences": inferences,
               "construct_aliases": aliases or [], "assessments": carried["assessments"], "reproduction_links": carried["reproduction_links"], "carried_from": carry_from, "inspections": [],
               "inquiry": (problem_obj["content"].get("inquiry") if problem_obj else None),
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
    out: dict[str, Any] = {"assessments": [], "reproduction_links": [], "reviews": {}, "proposals": []}
    for kind in ("assessments", "reproduction_links"):
        for rec in old[kind]:
            e0 = prev.get(rec["entry"])
            e1 = now.get((e0["author_claim"]["passage_id"], e0["author_claim"]["passage_revision"])) if e0 else None
            if e1 and _norm(e1["text"]) == _norm(e0["text"]):
                out[kind].append({**rec, "entry": e1["id"], "carried_from": carry_from})
    remap = {oid: now[(e["author_claim"]["passage_id"], e["author_claim"]["passage_revision"])] for oid, e in prev.items()
             if (e["author_claim"]["passage_id"], e["author_claim"]["passage_revision"]) in now and _norm(now[(e["author_claim"]["passage_id"], e["author_claim"]["passage_revision"])]["text"]) == _norm(e["text"])}
    for i in old["inferences"]:
        if i.get("derivation", "rule") == "rule":
            if i["reviews"]:
                out["reviews"][_inference_key(i)] = [{**r, "carried_from": carry_from} for r in i["reviews"]]
            continue
        used = [s["entry"] for s in i["sides"]] + [a["entry"] for a in i.get("addressed_by") or []]
        if not all(u in remap for u in used):
            continue                                       # something it was about changed: it is not carried
        m = lambda eid: remap[eid]["id"]                   # noqa: E731
        moved = {**i, "sides": [{**sd, "entry": m(sd["entry"])} for sd in i["sides"]], "addressed_by": [{**a, "entry": m(a["entry"])} for a in i.get("addressed_by") or []],
                 "levels": {m(k): v for k, v in (i.get("levels") or {}).items() if k in remap}, "carried_from": carry_from}
        out["proposals"].append(moved)
    return out


# ------------------------------------------------------------------------------------------------ the researcher's records

def _need_text(value: Optional[str], what: str) -> str:
    if not value or not value.strip():
        raise KnowledgeError("REASON_REQUIRED", f"{what} is required")
    return value.strip()


def review_inference(store: StateStore, token: Optional[str], landscape_id: str, inference_id: str, decision: str, reason: str, *, acknowledge_missing: bool = False) -> dict[str, Any]:
    """The researcher's decision on an inference or proposal: CONFIRMED (the studies do conflict on overlapping scope; the assumption is needed; the relation holds), DISMISSED,
    or NEEDS_SCOPE. Recorded with who and when. The inference keeps what it is (rule-derived, or proposed by whom): confirming it does not change its provenance."""
    if decision not in ("CONFIRMED", "DISMISSED", "NEEDS_SCOPE"):
        raise KnowledgeError("DECISION_UNKNOWN", "decision must be CONFIRMED, DISMISSED or NEEDS_SCOPE")
    reason = _need_text(reason, "your reason")

    def build(c: dict[str, Any], tx: Any) -> dict[str, Any]:
        inf = next((i for i in c["inferences"] if i["id"] == inference_id), None)
        if inf is None:
            raise KnowledgeError("INFERENCE_UNKNOWN", f"{landscape_id} has no inference {inference_id}")
        proposed = inf.get("derivation", "rule") != "rule"
        if decision == "CONFIRMED" and proposed and inf.get("missing_information") and not acknowledge_missing:
            raise KnowledgeError("MISSING_INFORMATION_NOT_ACKNOWLEDGED", "this proposal lists information that is missing (" + "; ".join(inf["missing_information"])
                                 + "): confirm only after reading it, and say so with acknowledge_missing / --acknowledge-missing")
        rec = {"at": tx.at, "by": tx.actor_id, "decision": decision, "reason": reason, **({"acknowledged_missing": True} if acknowledge_missing and proposed else {})}
        return {**c, "inferences": [{**i, "reviews": [*i["reviews"], rec]} if i["id"] == inference_id else i for i in c["inferences"]]}

    out = _write_with(store, token, landscape_id, build, "landscape_review_inference")
    return {"landscape_id": landscape_id, "revision": out["revision"], "inference": inference_id, "decision": decision}


def _claim(c: dict[str, Any], entry_id: str) -> dict[str, Any]:
    e = next((x for x in c["elements"] if x["id"] == entry_id), None)
    if e is None:
        raise KnowledgeError("ENTRY_UNKNOWN", f"no entry {entry_id} in this landscape")
    if e["category"] not in CLAIM_KIND_OF:
        raise KnowledgeError("ENTRY_NOT_A_CLAIM", f"{entry_id} is a {e['category']}: assessments are about claims (a finding, an interpretive finding or a theoretical claim)")
    return e


def _append(store: StateStore, token: Optional[str], landscape_id: str, key: str, record: dict[str, Any], operation: str) -> dict[str, Any]:
    made: dict[str, Any] = {}

    def build(c: dict[str, Any], tx: Any) -> dict[str, Any]:
        made["rec"] = {**record, "at": tx.at, "by": tx.actor_id}
        return {**c, key: [*c.get(key, []), made["rec"]]}

    out = _write_with(store, token, landscape_id, build, operation)
    return {"landscape_id": landscape_id, "revision": out["revision"], "entry": record.get("entry"), "record": made["rec"]}


def _basis_kind(c: dict[str, Any]) -> Optional[str]:
    return c.get("basis_kind") or ("methodological_literature" if c.get("methodological_literature") else None)


def method_verdict(declared: str, criteria: list[dict[str, Any]], threats: list[dict[str, Any]], route: str = "empirical", failed: Optional[dict[int, str]] = None) -> tuple[str, list[str]]:
    """The verdict an assessment may carry. `SUPPORTED_WITHIN_SCOPE` needs a criterion on a kind of basis the claim's route accepts (an empirical claim: methodological
    literature, as for `assess_validity`; a conceptual claim: theoretical literature or logical analysis; an interpretive claim: methodological literature or the textual
    evidence), every criterion met, at least one threat (for a conceptual or interpretive claim: rival explanation or alternative interpretation) checked and none open;
    otherwise it is stored as LIMITED with the blockers named. NOT_SUPPORTED needs a criterion marked met=no."""
    blockers: list[str] = []
    failed = failed or {}
    if declared == "NOT_SUPPORTED":
        failing = [c for c in criteria if c["met"] == "no"]
        if not failing:
            raise KnowledgeError("NOT_SUPPORTED_NEEDS_A_FAILED_CRITERION", "NOT_SUPPORTED needs a criterion marked met=no, with its basis")
        return "NOT_SUPPORTED", []
    if declared == "SUPPORTED_WITHIN_SCOPE":
        accepted = ROUTE_BASIS[route]
        if failed:
            blockers.append("a cited basis failed its checks (" + "; ".join(failed.values()) + "): it does not count")
        if not any(_basis_kind(c) in accepted and n not in failed for n, c in enumerate(criteria)):
            blockers.append("no criterion rests on methodological literature (application papers alone do not show a design permits an inference)" if route == "empirical" else
                            f"no criterion rests on an accepted basis for a {route} claim ({', '.join(accepted)})")
        if any(c["met"] != "yes" for c in criteria):
            blockers.append("not every criterion is met")
        if any(t["status"] == "open" for t in threats):
            blockers.append("a threat is still open" if route == "empirical" else "a rival explanation or threat is still open")
        if not threats:
            blockers.append("no threat was checked" if route == "empirical" else "no rival explanation or threat was checked")
        if not blockers:
            return "SUPPORTED_WITHIN_SCOPE", []
    return "LIMITED", blockers


def assess_method(store: StateStore, token: Optional[str], landscape_id: str, entry_id: str, *, verdict: str, criteria: list[dict[str, Any]], threats: list[dict[str, Any]],
                  rationale: str, code_evidence: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    """The researcher's assessment of one claim (A5 `inference`): does the design, argument or interpretation permit what the claim draws from it? Criteria each name their
    basis, in words and (optionally) as `basis_refs` ([{kind: source|passage, ref, revision, excerpt, supports_only}]) or an `argument` record ({premises, steps, conclusion,
    rival_assumptions}); threats each say whether addressed, scoped out or open. Written only by an author or owner; a model never writes one.

    Two things are kept apart. The **verdict** is the researcher's judgement (`RESEARCHER_ASSESSED`, `INFERENCE_JUDGMENT`), capped by `method_verdict`. The **verification** of the
    cited basis is the suite's (`basis.py`) and is recomputed whenever the assessment is shown. A basis that failed its checks does not count toward `SUPPORTED_WITHIN_SCOPE`; a
    basis that is only a sentence still counts, as before, and is shown as `BASIS_NOT_TRACEABLE`."""
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
        if c.get("basis_kind") is not None and c["basis_kind"] not in taxonomy.vocabulary("basis_kind"):
            raise KnowledgeError("CRITERION_BASIS_KIND_INVALID", f"basis_kind must be one of {taxonomy.vocabulary('basis_kind')}")
        bad = BAS.check_shape(c)
        if bad:
            raise KnowledgeError("BASIS_REFERENCE_INVALID", "; ".join(bad))
    for t in threats:
        if not (t.get("threat") or "").strip() or t.get("status") not in ("addressed", "scoped_out", "open"):
            raise KnowledgeError("THREAT_INVALID", "a threat has its wording and a status of addressed, scoped_out or open")
    for ev in code_evidence or []:
        if not ev.get("file_sha256") or not ev.get("lines"):
            raise KnowledgeError("CODE_EVIDENCE_INCOMPLETE", "code evidence names the file's sha256 and a line range")
    cur = load_landscape(store, landscape_id)
    entry = _claim(cur["content"], entry_id)
    route = CLAIM_KIND_OF[entry["category"]]
    per = BAS.verify_assessment(store, criteria, ROUTE_BASIS[route])["criteria"]
    failed = {n: "; ".join(p["problems"]) for n, p in enumerate(per) if p["status"] == "BASIS_CHECK_FAILED"}
    capped, blockers = method_verdict(verdict, criteria, threats, route, failed)
    rec = {"entry": entry_id, "route": route, "passage": [entry["author_claim"]["passage_id"], entry["author_claim"]["passage_revision"]], "declared_verdict": verdict, "verdict": capped,
           "blockers": blockers, "criteria": criteria, "threats": threats, "code_evidence": code_evidence or [], "rationale": rationale, "generated_by": "researcher"}
    out = _append(store, token, landscape_id, "assessments", rec, "landscape_assess_method")
    return {**out, "verdict": capped, "blockers": blockers, "route": route, "verification": BAS.verify_assessment(store, criteria, ROUTE_BASIS[route])}


def link_reproduction(store: StateStore, token: Optional[str], landscape_id: str, entry_id: str, *, kind: str, reason: str, package_id: Optional[str] = None,
                      outputs: Optional[list[str]] = None, reference: Optional[str] = None) -> dict[str, Any]:
    """Tie a finding to a reproduction (A5 `reproduction`). `package_run`: a replication package with a recorded reproduction (`research ... record-reproduction`) and the
    outputs of it that concern this finding; the status is read from the package each time it is shown. `new_data`: the researcher records a replication on new
    data, by a reference they name; the suite did not check it."""
    if kind not in REPRODUCTION_KINDS:
        raise KnowledgeError("REPRODUCTION_KIND_UNKNOWN", f"kind must be one of {REPRODUCTION_KINDS}")
    reason = _need_text(reason, "your reason")
    cur = load_landscape(store, landscape_id)
    claim = _claim(cur["content"], entry_id)
    if claim["category"] != "finding":
        raise KnowledgeError("REPRODUCTION_NOT_APPLICABLE", f"{entry_id} is a {CLAIM_KIND_OF[claim['category']]} claim: it is not reproduced from data")
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


def _inference_dimension(store: StateStore, assessments: list[dict[str, Any]], entry: dict[str, Any]) -> dict[str, Any]:
    mine = [a for a in assessments if a["entry"] == entry["id"] and a["passage"] == [entry["author_claim"]["passage_id"], entry["author_claim"]["passage_revision"]]]
    if not mine:
        return {"value": "NOT_ASSESSED"}
    a = mine[-1]
    route = a.get("route", "empirical")
    ver = BAS.verify_assessment(store, a["criteria"], ROUTE_BASIS[route])
    legacy = not any(c.get("basis_refs") or c.get("argument") for c in a["criteria"])
    return {"value": "METHOD_CRITICALLY_ASSESSED", "route": route, "verdict": a["verdict"], "blockers": a["blockers"], "by": a["by"], "at": a["at"], "rationale": a["rationale"],
            "assessed_by": "RESEARCHER_ASSESSED", "judgment": "INFERENCE_JUDGMENT", "verification": ver,
            "verification_note": ("recorded without a traceable basis: shown as not traceable, not upgraded" if legacy else ver["meaning"])}


def _reading_dimension(store: StateStore, landscape_id: str, entry_id: str) -> dict[str, Any]:
    st = eca.statement_assessment(store, eca.resolve_statement(store, landscape_id, element=entry_id))
    confirmed = st["researcher_confirmed"] and st["eca_verdict"] in ("SUPPORTED_AS_STATED", "SUPPORTED_IF_QUALIFIED")
    return {"value": "READING_CONFIRMED" if confirmed else "AUTHOR_REPORTED", "eca_verdict": st["eca_verdict"], "disputed": st["eca_verdict"] == "NOT_SUPPORTED",
            "adjudications": st["adjudications"], "reasons": st["reasons"]}


def _next_inference_id(inferences: list[dict[str, Any]]) -> str:
    return f"I{max((int(i['id'][1:]) for i in inferences), default=0) + 1:03d}"


def _enrich_inference(store: StateStore, i: dict[str, Any], inspections: list[dict[str, Any]], snapshot_now: str) -> dict[str, Any]:
    i = {"derivation": "rule", "relation": i["kind"], "provenance": "rule:" + CONTRADICTIONS_BY, "missing_information": [], "levels": {}, "addressed_by": [], "text": None, "reasoning": None,
         "premises": [], "alternative_interpretations": [], **i}

    def head(sid: str) -> Optional[int]:
        o = store.get_object(sid)
        return o["revision"] if o else None

    rec = INS.recognition_view(i, inspections, snapshot_now, head)
    deps, why = INS.dependencies(store, i, rec)
    return {**i, "confirmation": REL.confirmation(i), "recognition": rec, "dependencies": {"sources": deps}, "needs_reassessment": why}


def view(store: StateStore, landscape_id: str) -> dict[str, Any]:
    land = load_landscape(store, landscape_id)
    c = land["content"]
    entries = []
    for e in c["elements"]:
        e = {"claim_kind": CLAIM_KIND_OF.get(e["category"]), "provenance": "author_stated", **e}
        evid: dict[str, Any] = {"reading": _reading_dimension(store, landscape_id, e["id"])}
        if e["category"] in CLAIM_KIND_OF:
            evid["inference"] = _inference_dimension(store, c["assessments"], e)
            evid["reproduction"] = (_reproduction(store, [x for x in c["reproduction_links"] if x["entry"] == e["id"]]) if e["category"] == "finding"
                                    else {"value": "NOT_APPLICABLE", "note": f"a {e['claim_kind']} claim is not reproduced from data"})
        inf = [i["id"] for i in c["inferences"] if any(s["entry"] == e["id"] for s in i["sides"])]
        entries.append({**e, "layers": {"author_claim": e["author_claim"], "evidence": evid, "suite_inference": inf}})
    inspections = c.get("inspections") or []
    snapshot_now = corpus.corpus_snapshot_id(store)
    return {"landscape_id": landscape_id, "revision": land["revision"], "label": c["label"], "retrieval_id": c["retrieval_id"], "problem_id": c["problem_id"],
            "problem_revision": c["problem_revision"], "corpus_snapshot_id": c["corpus_snapshot_id"], "versions_policy": c["versions_policy"], "protocols": c["protocols"],
            "inquiry": c.get("inquiry"), "considerations": CONSIDERATIONS,
            "currency": landscape_currency(store, c), "works": c["works"], "entries": entries, "construct_aliases": c["construct_aliases"],
            "inferences": [_enrich_inference(store, i, inspections, snapshot_now) for i in c["inferences"]], "inspections": inspections,
            "sources_read": c["sources_read"], "coverage": c["coverage"], "limits": c["limits"], "carried_from": c["carried_from"]}


# ------------------------------------------------------------------------------------------------ proposals: relations between studies and implied assumptions

def _proposal_record(norm: dict[str, Any], iid: str, *, derivation: str, provenance: str, generated_by: str, by: Optional[str], at: Optional[str], snapshot: str) -> dict[str, Any]:
    return {"id": iid, **norm, "subkind": "proposal", "derivation": derivation, "provenance": provenance, "generated_by": generated_by, "status": "PROPOSED",
            "scope_overlap": "NOT_ASSESSED", "reviews": [], "proposed_by": by, "proposed_at": at, "corpus_snapshot_at_proposal": snapshot}


def propose_relations(store: StateStore, token: Optional[str], landscape_id: str, adapter: Any, *, kinds: Optional[list[str]] = None) -> dict[str, Any]:
    """One model call: relations between the studies of the landscape and assumptions its entries imply but do not state. Every item goes through the gate in `relations.py`
    before it is stored, as a PROPOSED, UNCONFIRMED inference with the provenance `suite_proposed:model:<id>`; rejected items are listed with the reason. A model proposal never
    becomes an entry, never confirms itself and never says a tension is unrecognised."""
    bad = [k for k in kinds or [] if k not in REL.PROPOSABLE_KINDS]
    if bad:
        raise KnowledgeError("PROPOSAL_KIND_NOT_ALLOWED", f"{bad} cannot be requested (proposable: {REL.PROPOSABLE_KINDS})")
    cur = load_landscape(store, landscape_id)
    c = cur["content"]
    model = adapter_identity(adapter)
    who = f"model:{model.get('model_id') or model.get('provider')}"
    fp = canonical_json_hash({"landscape": [landscape_id, cur["revision"], cur["revision_digest"]], "kinds": kinds, "skill": REL.SKILL_DIGEST, "model": model})
    base = {"workflow": "landscape_relations", "input_fingerprint": fp, "executor": "landscape_relations@1", "applicability": Applicability.APPLICABLE.value, "model_descriptor": model,
            "skill_digest": REL.SKILL_DIGEST, "input_revisions": [{"object_id": landscape_id, "revision": cur["revision"], "content_digest": cur["content_digest"]}]}

    def failed(reason: str, usage: dict) -> str:
        return workflow_runs.append_run(store, {**base, "execution_status": ExecutionStatus.FAILED.value, "context_status": ContextStatus.COMPLETE.value,
                                                "assessment_status": AssessmentStatus.NOT_ASSESSED.value, "legacy_status": "FAILED", "status_reason": reason, "usage": usage})

    try:
        raw, usage = adapter.generate(REL.SYSTEM_PROMPT, REL.build_prompt(c, kinds), expected_schema=REL.PROPOSAL_SCHEMA, temperature=0.0)
    except ModelError as exc:
        return {"status": "FAILED", "reason": type(exc).__name__, "message": str(exc), "run_id": failed(type(exc).__name__, {"llm_calls": 1}), "accepted": [], "rejected": []}
    use = {"llm_calls": 1, "input_tokens": getattr(usage, "input_tokens", None), "output_tokens": getattr(usage, "output_tokens", None), "usage_source": getattr(usage, "usage_source", None)}
    parsed, err = parse_model_output(raw)
    problems = [] if parsed is None else sorted(e.message[:120] for e in Draft202012Validator(REL.PROPOSAL_SCHEMA).iter_errors(parsed))
    if parsed is None or problems:
        return {"status": "FAILED", "reason": "invalid_output", "message": err or "; ".join(problems[:3]), "run_id": failed("invalid_output", use), "accepted": [], "rejected": []}
    row = {**base, "execution_status": ExecutionStatus.SUCCEEDED.value, "context_status": ContextStatus.COMPLETE.value, "assessment_status": AssessmentStatus.PROVISIONAL.value,
           "legacy_status": "SUCCESS", "status_reason": None, "usage": use}
    snapshot = corpus.corpus_snapshot_id(store)
    accepted: list[str] = []
    rejected: list[dict[str, Any]] = []

    def build(content: dict[str, Any], tx: Any) -> dict[str, Any]:
        infs = list(content["inferences"])
        for p in parsed["proposals"]:
            try:
                norm = REL.validate_proposal({**content, "inferences": infs}, p, enforce_levels=True)
            except KnowledgeError as exc:
                rejected.append({"proposal": {k: p.get(k) for k in ("kind", "topic", "entries")}, "code": exc.code, "reason": exc.message})
                continue
            rec = _proposal_record(norm, _next_inference_id(infs), derivation="model_proposed", provenance=f"suite_proposed:{who}", generated_by=who, by=who, at=tx.at, snapshot=snapshot)
            infs.append(rec)
            accepted.append(rec["id"])
        return {**content, "inferences": infs}

    try:
        out = _write_with(store, token, landscape_id, build, "landscape_propose_relations", run_row=row) if parsed["proposals"] else {"run_id": workflow_runs.append_run(store, row), "revision": cur["revision"]}
    except Exception as exc:
        try:
            failed(f"write_refused:{getattr(exc, 'code', type(exc).__name__)}", use)
        except Exception:  # noqa: BLE001
            pass
        raise
    return {"status": "PROPOSED", "landscape_id": landscape_id, "revision": out["revision"], "accepted": accepted, "rejected": rejected, "run_id": out["run_id"], "model": who}


def add_proposal(store: StateStore, token: Optional[str], landscape_id: str, proposal: dict[str, Any], *, provenance: str = "researcher_proposed", critic: Optional[str] = None) -> dict[str, Any]:
    """The researcher's own proposal, or one a critic made that the researcher records (for example a reviewer's implied assumption), through the same gate. Recorded as
    `researcher_proposed` / `critic_proposed:<name>`, PROPOSED and UNCONFIRMED until decided. The levels-of-analysis check applies only to a model's proposal."""
    if provenance not in REL.PROVENANCES:
        raise KnowledgeError("PROVENANCE_UNKNOWN", f"provenance must be one of {REL.PROVENANCES}")
    if provenance == "critic_proposed" and not (critic and critic.strip()):
        raise KnowledgeError("CRITIC_REQUIRED", "name the critic whose proposal this is")
    snapshot = corpus.corpus_snapshot_id(store)
    made: dict[str, Any] = {}

    def build(content: dict[str, Any], tx: Any) -> dict[str, Any]:
        norm = REL.validate_proposal(content, proposal, enforce_levels=False)
        rec = _proposal_record(norm, _next_inference_id(content["inferences"]), derivation="researcher_proposed",
                               provenance=f"{provenance}:{critic.strip()}" if provenance == "critic_proposed" else provenance, generated_by="researcher", by=tx.actor_id, at=tx.at, snapshot=snapshot)
        made["id"] = rec["id"]
        return {**content, "inferences": [*content["inferences"], rec]}

    out = _write_with(store, token, landscape_id, build, "landscape_add_proposal")
    return {**out, "inference": made["id"]}


def tension_candidates(store: StateStore, landscape_id: str, inference_id: str, *, limit: int = 10, terms: Optional[list[str]] = None) -> dict[str, Any]:
    """Held sources that may already address a proposed relation, found by words alone (BM25 over the stored sections of full texts, and over title and abstract of the
    others; no model): the proposal's search terms and topic, or the `terms` given. Each says whether it can be read at all, whether it was read for this landscape, and whether
    a model hinted at it. **A match is a paper to read, not evidence that it addresses the tension, and a search that finds nothing is not evidence that nothing does.**"""
    c = load_landscape(store, landscape_id)["content"]
    i = next((x for x in c["inferences"] if x["id"] == inference_id), None)
    if i is None:
        raise KnowledgeError("INFERENCE_UNKNOWN", f"{landscape_id} has no inference {inference_id}")
    use_terms = list(terms) if terms else list(i.get("search_terms") or []) + ([i["relationship"]] if i.get("relationship") else [])
    tokens = sorted(set(X.phrase_tokens(use_terms)))
    index = RT.Index(store)
    read = {s["source_id"] for s in c["sources_read"]}
    hinted = {a["source_id"] for a in i.get("addressed_by") or []}
    cands: list[dict[str, Any]] = []
    n = max(1, int(limit))
    for tier in ("FULL_TEXT", "ABSTRACT_ONLY", "METADATA_ONLY"):
        for rank, h in enumerate(index.rank(tier, tokens, n), 1):
            src = index.sources[h["source_id"]]
            cands.append({"source_id": h["source_id"], "source_revision": src["revision"], "title": src["content"].get("title"), "tier": tier, "readable": tier == "FULL_TEXT",
                          "rank": rank, "score": h["score"], "matched_terms": h["matched"], "locator": h["locator"], "work_id": src["work_id"], "version_id": src["content"].get("version_id"),
                          "read_in_landscape": h["source_id"] in read, "model_hint": h["source_id"] in hinted, "origin": "bm25"})
    seen = {x["source_id"] for x in cands}
    for sid in sorted(hinted - seen):                       # a source a model named that the words did not find is still a candidate to inspect
        src = index.sources.get(sid)
        if src and not src["excluded"]:
            cands.append({"source_id": sid, "source_revision": src["revision"], "title": src["content"].get("title"), "tier": src["tier"], "readable": src["tier"] == "FULL_TEXT",
                          "rank": None, "score": 0, "matched_terms": [], "locator": None, "work_id": src["work_id"], "version_id": src["content"].get("version_id"),
                          "read_in_landscape": sid in read, "model_hint": True, "origin": "model_hint"})
    return {"inference": inference_id, "terms": use_terms, "tokens": tokens, "corpus_snapshot_id": corpus.corpus_snapshot_id(store),
            "note": "held sources that match these words, best first, then weaker tiers; a match is a paper to read, not evidence that it addresses the tension, and finding none is not evidence that none does",
            "candidates": cands}


def inspect_tension(store: StateStore, token: Optional[str], landscape_id: str, inference_id: str, *, results: list[dict[str, Any]], rationale: str,
                    terms: Optional[list[str]] = None, extra_candidates: Optional[list[str]] = None, candidate_limit: int = 10, limitations: Optional[list[str]] = None) -> dict[str, Any]:
    """Record an inspection of the sources that may already address a proposed tension (author or owner). `results` is the researcher's reading, one entry per source:
    `{source_id, result: ADDRESSES|MENTIONS_PHENOMENON_ONLY|DOES_NOT_ADDRESS, inspected: "whole text" or the sections read, note, quotes: [...]}`. A result of ADDRESSES or
    MENTIONS_PHENOMENON_ONLY needs verbatim quotes, each of which becomes a quote-verified EvidencePassage; a source held only as abstract or metadata cannot be inspected and
    is recorded as NOT_READABLE. The candidate set is generated by `tension_candidates` (and model hints and `extra_candidates`) and recorded with the corpus snapshot; a candidate
    without a result makes the inspection incomplete. The status that follows is stated by `inspection.status_of`: a statement about this inspected set only."""
    reason = _need_text(rationale, "your rationale")
    if not results:
        raise KnowledgeError("INSPECTION_RESULTS_REQUIRED", "record what you found in at least one source")
    cur = load_landscape(store, landscape_id)
    c = cur["content"]
    inf = next((x for x in c["inferences"] if x["id"] == inference_id), None)
    if inf is None:
        raise KnowledgeError("INFERENCE_UNKNOWN", f"{landscape_id} has no inference {inference_id}")
    if inf.get("derivation", "rule") == "rule" or inf["kind"] == "implied_assumption":
        raise KnowledgeError("INSPECTION_NOT_APPLICABLE", "recognition is inspected for a proposed relation between studies, not for a rule-derived inference or an implied assumption")
    cand = tension_candidates(store, landscape_id, inference_id, limit=candidate_limit, terms=terms)
    by_id = {x["source_id"]: x for x in cand["candidates"]}
    for sid in extra_candidates or []:
        if sid in by_id:
            continue
        o = store.get_object(sid)
        if not o or o["object_type"] != "Source":
            raise KnowledgeError("SOURCE_NOT_HELD", f"{sid} is not a Source in this project")
        from research_harness.knowledge.corpus import source_tier
        t = source_tier(o["content"])
        by_id[sid] = {"source_id": sid, "source_revision": o["revision"], "title": o["content"].get("title"), "tier": t, "readable": t == "FULL_TEXT", "rank": None, "score": 0,
                      "matched_terms": [], "locator": None, "work_id": o["content"].get("work_id") or sid, "version_id": o["content"].get("version_id"), "read_in_landscape": False,
                      "model_hint": False, "origin": "researcher_added"}
    recorded: list[dict[str, Any]] = []
    seen_src: set[str] = set()
    for r in results:
        sid = r.get("source_id")
        if sid in seen_src:
            raise KnowledgeError("INSPECTION_DUPLICATE_SOURCE", f"{sid} has two results")
        seen_src.add(sid)
        o = store.get_object(sid or "")
        if not o or o["object_type"] != "Source":
            raise KnowledgeError("SOURCE_NOT_HELD", f"{sid} is not a Source in this project")
        from research_harness.knowledge.corpus import source_tier
        tier = source_tier(o["content"])
        res = r.get("result")
        if res not in INS.RESULTS:
            raise KnowledgeError("INSPECTION_RESULT_UNKNOWN", f"result must be one of {INS.RESULTS}")
        if tier != "FULL_TEXT":
            if res != "NOT_READABLE":
                raise KnowledgeError("SOURCE_NOT_READABLE", f"{sid} is held as {tier}: no text was inspected, so the only honest result is NOT_READABLE")
        elif res == "NOT_READABLE":
            raise KnowledgeError("SOURCE_IS_READABLE", f"{sid} has a full text; inspect it")
        scope_text = r.get("inspected") if isinstance(r.get("inspected"), str) else ", ".join(r.get("inspected") or [])
        if res != "NOT_READABLE" and not (scope_text or "").strip():
            raise KnowledgeError("INSPECTED_SCOPE_REQUIRED", f"say what you inspected in {sid} (the whole text, or which sections)")
        passages = []
        if res in ("ADDRESSES", "MENTIONS_PHENOMENON_ONLY"):
            if not r.get("quotes"):
                raise KnowledgeError("EVIDENCE_REQUIRED", f"{res} for {sid} needs the verbatim words that show it")
            _, text = corpus.source_text(store, sid)
            for q in r["quotes"]:
                found = quotes.find_quote(text, q)
                if not found:
                    raise KnowledgeError("QUOTE_NOT_IN_SOURCE", f"the quote is not in the stored text of {sid}: {q[:80]!r}")
                pid = f"EP_INSP_{id_fragment(sid, 26)}_{found[0]}"
                existing = store.get_object(pid)
                if existing is None:
                    corpus.add_passage(store, token, source_id=sid, quote=q, passage_id=pid, topic="tension_inspection", inference=inference_id)
                    existing = store.get_object(pid)
                passages.append({"passage_id": pid, "passage_revision": existing["revision"], "quote": existing["content"]["quote"], "locator": existing["content"]["locator"]})
        recorded.append({"source_id": sid, "source_revision": o["revision"], "tier": tier, "work_id": o["content"].get("work_id") or sid, "version_id": o["content"].get("version_id"),
                         "result": res, "inspected": r.get("inspected") if res != "NOT_READABLE" else "nothing: no text is held", "note": (r.get("note") or "").strip(), "passages": passages})
    by_id_src = {x["source_id"] for x in recorded}
    for x in recorded:
        by_id.setdefault(x["source_id"], {"source_id": x["source_id"], "source_revision": x["source_revision"], "title": None, "tier": x["tier"], "readable": x["tier"] == "FULL_TEXT",
                                          "rank": None, "score": 0, "matched_terms": [], "locator": None, "work_id": x["work_id"], "version_id": x["version_id"], "read_in_landscape": False,
                                          "model_hint": False, "origin": "researcher_added"})
    uninspected = sorted(sid for sid, x in by_id.items() if sid not in by_id_src and x["readable"])
    unreadable = sorted(sid for sid, x in by_id.items() if not x["readable"])
    insp = {"inference": inference_id, "tension_digest": INS.tension_digest(inf), "landscape_revision": cur["revision"], "corpus_snapshot_id": cand["corpus_snapshot_id"],
            "search": {"terms": cand["terms"], "tokens": cand["tokens"], "method": "BM25 over stored sections (full texts) and title and abstract (other tiers); model hints and researcher-added sources",
                       "limit": candidate_limit}, "candidates": list(by_id.values()), "results": recorded, "uninspected": uninspected, "unreadable": unreadable,
            "limitations": [*INS.STANDARD_LIMITATIONS, *[str(x) for x in limitations or []]]}
    insp["status_at_record"], insp["qualifications_at_record"] = INS.status_of(insp)

    def build(content: dict[str, Any], tx: Any) -> dict[str, Any]:
        olds = content.get("inspections") or []
        rec = {**insp, "id": f"N{len(olds) + 1:03d}", "decision": {"by": tx.actor_id, "at": tx.at, "rationale": reason}}
        return {**content, "inspections": [*olds, rec]}

    out = _write_with(store, token, landscape_id, build, "landscape_inspect_tension")
    return {"landscape_id": landscape_id, "revision": out["revision"], "inspection": f"N{len(c.get('inspections') or []) + 1:03d}", "inference": inference_id,
            "status": insp["status_at_record"], "qualifications": insp["qualifications_at_record"], "candidates": len(insp["candidates"]), "inspected": len(recorded),
            "uninspected": uninspected, "unreadable": unreadable}


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
