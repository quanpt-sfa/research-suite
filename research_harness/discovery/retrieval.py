"""Internal retrieval over the stored corpus (M5 WP5): R1 lexical, R2 expansion, R3a citations held in the corpus, R5 the researcher's review.

What it produces is a **reading list** and a **coverage report**, never a verdict. A retrieval score ranks what to read; a source that was not retrieved
is not shown to be irrelevant, and a source that was retrieved is not shown to say anything.

* R1: BM25 over the stored sections of full-text sources and over the title and abstract of every source, with query terms from the problem (or the chosen
  interpretation) and from the researcher.
* R2: other names for the same idea. A model may *propose* them; they enter the search only after the researcher accepts them (or writes them).
  A source found only through an accepted expansion is labelled R2, with the expansion that found it.
* R3a: held papers that cite, or are cited by, a paper on the reading list, from their stored reference lists (`citations.py`). Listed apart, as neighbours.
* R5: the researcher marks hits irrelevant (with a reason), adds a held paper they know, or records a paper they want to acquire. Edits are recorded.
* Tiers: only FULL_TEXT sources go on the reading list. ABSTRACT_ONLY and METADATA_ONLY sources are listed as possibly relevant, not read; they have no
  text, so no passage can be made from them and they can never support or challenge a claim.
* Every conclusion drawn from a run is bound to the corpus snapshot it was made on (invariant I20): when the corpus changes, the run is shown STALE_FOR_CORPUS
  with what changed.
* No network, no external service. External metadata sources are not part of this version.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any, Optional

from jsonschema import Draft202012Validator

from research_harness.core.canonical import canonical_json_hash
from research_harness.discovery import citations as C
from research_harness.discovery import text_index as X
from research_harness.discovery.problem import load_problem
from research_harness.knowledge import corpus
from research_harness.knowledge.common import KnowledgeError, engine_origin, next_numbered_id
from research_harness.state import workflow_runs
from research_harness.state.store import StateStore
from research_harness.workflows.adapters import ModelError, adapter_identity
from research_harness.workflows.model_io import parse_model_output
from research_harness.workflows.status import Applicability, AssessmentStatus, ContextStatus, ExecutionStatus

ROLES = ("owner", "author")
GENERATED_BY = "deterministic:retrieval@1"
DEFAULT_CONFIG: dict[str, Any] = {"bm25": {"k1": 1.2, "b": 0.75}, "top_n": 20, "k": 10, "tokenizer": X.TOKENIZER_VERSION, "min_fulltext_sources": 3,
                                  "neighbours_from": 10, "min_unit_chars": 30, "stages": ["R1", "R2", "R3a"]}
TIER_ORDER = ("FULL_TEXT", "ABSTRACT_ONLY", "METADATA_ONLY")

EXPANSION_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "search_term_expansion", "type": "object",
    "properties": {"terms": {"type": "array", "items": {"type": "object", "properties": {"term": {"type": "string", "minLength": 3},
                                                                                          "rationale": {"type": "string"}}, "required": ["term"]}}},
    "required": ["terms"], "additionalProperties": True}
EXPANSION_PROMPT = (
    "You help a researcher search a library. Given a research problem and the words already used to search for it, list OTHER NAMES for the same idea: "
    "synonyms, alternative technical terms, older or field-specific wording, and names of datasets or labels the literature uses for it. Do not broaden the topic, "
    "do not repeat a term already listed, and do not say anything about what the literature found.\n"
    "Respond with exactly one JSON object and nothing else. It must validate against this JSON Schema:\n" + json.dumps(EXPANSION_SCHEMA, indent=2, sort_keys=True))
EXPANSION_SKILL_DIGEST = canonical_json_hash(EXPANSION_PROMPT)


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


# ------------------------------------------------------------------------------------------------ the corpus as the search sees it

def current_members(store: StateStore) -> list[list[Any]]:
    """`[source_id, revision, tier, excluded]` for every Source, without reading any text."""
    out = []
    with store.read() as conn:
        for (oid,) in conn.execute("SELECT object_id FROM object_heads WHERE object_type='Source' ORDER BY object_id").fetchall():
            o = store.get_object(oid, conn=conn)
            out.append([oid, o["revision"], corpus.source_tier(o["content"]), bool(o["content"].get("excluded"))])
    return out


class Index:
    """The searchable corpus: units per tier (sections and title-and-abstract for full texts, title-and-abstract for the rest), built once per search."""

    def __init__(self, store: StateStore, config: Optional[dict[str, Any]] = None):
        self.cfg = {**DEFAULT_CONFIG, **(config or {})}
        self.members: list[list[Any]] = []
        self.sources: dict[str, dict[str, Any]] = {}
        self.unreadable: list[str] = []
        self.unit_source: dict[str, dict[str, str]] = {t: {} for t in TIER_ORDER}
        self.unit_meta: dict[str, dict[str, Any]] = {}
        docs: dict[str, dict[str, list[str]]] = {t: {} for t in TIER_ORDER}
        papers: dict[str, dict[str, Any]] = {}
        with store.read() as conn:
            heads = [r[0] for r in conn.execute("SELECT object_id FROM object_heads WHERE object_type='Source' ORDER BY object_id").fetchall()]
            objs = [store.get_object(h, conn=conn) for h in heads]
        for o in objs:
            c, sid = o["content"], o["object_id"]
            tier, excluded = corpus.source_tier(c), bool(c.get("excluded"))
            self.members.append([sid, o["revision"], tier, excluded])
            self.sources[sid] = {"revision": o["revision"], "tier": tier, "excluded": excluded, "content": c, "work_id": c.get("work_id") or sid}
            if excluded:
                continue
            meta_text = " ".join(str(x) for x in [c.get("title"), " ".join(c.get("authors") or []), c.get("venue"), c.get("abstract")] if x)
            n = 0
            if meta_text.strip():
                uid = f"{sid}#{n:04d}"
                n += 1
                docs[tier][uid] = X.tokenize(meta_text)
                self.unit_source[tier][uid] = sid
                self.unit_meta[uid] = {"section": "title and abstract", "locator": f"{sid} (title and abstract)"}
            if tier == "FULL_TEXT":
                try:
                    obj, text = corpus.source_text(store, sid)
                except KnowledgeError:
                    self.unreadable.append(sid)
                    continue
                sections = corpus.source_sections(store, obj, text)
                papers[sid] = {"text": text, "sections": sections, "title": c.get("title"), "doi": c.get("doi")}
                for s in sections:
                    if s.get("label") == "references":
                        continue
                    body = text[s["char_start"]:s["char_end"]]
                    if len(body.strip()) < self.cfg["min_unit_chars"]:
                        continue
                    uid = f"{sid}#{n:04d}"
                    n += 1
                    docs[tier][uid] = X.tokenize(f"{s.get('heading') or ''} {body}")
                    self.unit_source[tier][uid] = sid
                    self.unit_meta[uid] = {"section": s.get("heading") or s.get("label") or "text", "locator": f"{sid} chars {s['char_start']}-{s['char_end']}"}
        bm = self.cfg["bm25"]
        self.bm25 = {t: X.Bm25(docs[t], bm["k1"], bm["b"]) for t in TIER_ORDER if docs[t]}
        # papers for citation matching: held, readable, not excluded; targets may be of any tier (they need a title or a DOI, not a text)
        targets = {sid: {"title": s["content"].get("title"), "doi": s["content"].get("doi"), "text": papers.get(sid, {}).get("text"),
                         "sections": papers.get(sid, {}).get("sections")} for sid, s in self.sources.items() if not s["excluded"]}
        self.links = C.find_links(targets)
        self.references_parsed = C.references_parsed({k: v for k, v in targets.items() if v["text"]})

    def rank(self, tier: str, tokens: list[str], top_n: int) -> list[dict[str, Any]]:
        bm = self.bm25.get(tier)
        if bm is None or not tokens:
            return []
        out = []
        for sid, score, unit in X.best_per_source(bm.score(tokens), self.unit_source[tier])[:top_n]:
            out.append({"source_id": sid, "score": round(score, 6), "unit": unit, "matched": bm.matched(unit, tokens),
                        "section": self.unit_meta[unit]["section"], "locator": self.unit_meta[unit]["locator"]})
        return out

    def unit_terms(self, tier: str, unit: str) -> set[str]:
        return set(self.bm25[tier].tf[unit])


# ------------------------------------------------------------------------------------------------ the run object

def load_run(store: StateStore, run_id: str) -> dict[str, Any]:
    o = store.get_object(run_id)
    if not o or o["object_type"] != "RetrievalRun":
        raise KnowledgeError("RETRIEVAL_NOT_FOUND", f"{run_id} is not a RetrievalRun in this project")
    return o


def _put(store: StateStore, token: Optional[str], run_id: str, content: dict[str, Any], operation: str, *, expected_head: int,
         edges: Optional[list] = None, run_row: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    with store.write_tx(token=token, require_roles=ROLES) as tx:
        if edges is None:
            head = tx.head(run_id)
            rows = tx.conn.execute("SELECT relation,target_id,target_revision,provenance FROM edges WHERE source_id=? AND source_revision=?",
                                   (run_id, head["head_revision"])).fetchall() if head else []
            edges = [(r[0], r[1], r[2], json.loads(r[3])) for r in rows]
        res = tx.add_revision(object_id=run_id, object_type="RetrievalRun", content=content, edges=edges, expected_head=expected_head,
                              origin=engine_origin(operation, tx.actor_id))
        tx.audit(operation, "RECORDED", {"retrieval_id": run_id, "revision": res["revision"], "state": content["state"]})
        rid = workflow_runs.insert_run_tx(tx, run_row) if run_row is not None else None
    return {"retrieval_id": run_id, "revision": res["revision"], "state": content["state"], "run_id": rid}


def _log(c: dict[str, Any], tx_like: dict[str, Any]) -> list[dict[str, Any]]:
    return [*c["edits"], tx_like]


def _accepted(c: dict[str, Any]) -> list[str]:
    return sorted({e["term"] for e in c["expansions"] if e["status"] == "ACCEPTED"}, key=_norm)


def new_run(store: StateStore, token: Optional[str], problem_id: str, *, interpretation_ids: Optional[list[str]] = None, terms: Optional[list[str]] = None,
            label: Optional[str] = None, parent: Optional[str] = None, **config: Any) -> dict[str, Any]:
    """A retrieval run for a problem, in DRAFT: its queries are fixed and recorded; nothing has been searched. One query per chosen interpretation (or per
    those named); with none chosen, one query from the statement. The search terms a model proposed for a chosen interpretation are offered as expansions
    (PROPOSED): they enter the search only when the researcher accepts them."""
    unknown = set(config) - set(DEFAULT_CONFIG)
    if unknown:
        raise KnowledgeError("CONFIG_UNKNOWN", f"unknown retrieval settings: {sorted(unknown)}")
    pr = load_problem(store, problem_id)
    pc = pr["content"]
    ids = list(interpretation_ids) if interpretation_ids else list(pc["chosen_interpretations"])
    by_id = {i["id"]: i for i in pc["interpretations"]}
    bad = [i for i in ids if i not in by_id]
    if bad:
        raise KnowledgeError("INTERPRETATION_UNKNOWN", f"{', '.join(bad)} is not an interpretation of {problem_id}")
    researcher_terms = [t.strip() for t in (terms or []) if t and t.strip()]
    extra = X.phrase_tokens(researcher_terms)
    queries = []
    for n, iid in enumerate(ids or [None], 1):
        text = by_id[iid]["text"] if iid else pc["statement"]
        base = sorted(set(X.tokenize(text)) | set(extra))
        queries.append({"id": f"Q{n}", "interpretation": iid or "P0", "text": text, "tokens": base,
                        "from": (["the interpretation" if iid else "the problem statement"] + (["the researcher's terms"] if researcher_terms else []))})
    if not any(q["tokens"] for q in queries):
        raise KnowledgeError("QUERY_EMPTY", "the problem and terms give no searchable words")
    expansions, seen = [], set()
    for iid in ids:
        for t in by_id[iid].get("search_terms") or []:
            if _norm(t) not in seen:
                seen.add(_norm(t))
                expansions.append({"term": t, "source": by_id[iid]["generated_by"], "interpretation": iid, "status": "PROPOSED", "origin": "interpretation search terms"})
    cfg = {**DEFAULT_CONFIG, **config}
    with store.read() as conn:
        rid = next_numbered_id(conn, "RET_", "RetrievalRun")
    content = {"label": label or f"Retrieval for {problem_id}", "problem_id": problem_id, "problem_revision": pr["revision"], "interpretations": ids, "queries": queries,
               "terms": researcher_terms, "expansions": expansions, "config": cfg, "state": "DRAFT", "corpus_snapshot_id": None, "corpus_members": [], "hits": [],
               "neighbours": [], "coverage": {}, "edits": [], "parent_run": parent, "generated_by": GENERATED_BY}
    edges = [("depends_on", problem_id, pr["revision"], {"edge_kind": "retrieved_for"})]
    if parent:
        p = load_run(store, parent)
        edges.append(("derived_from", parent, p["revision"], {"edge_kind": "refines"}))
    out = _put(store, token, rid, content, "retrieval_new", expected_head=0, edges=edges)
    return {**out, "queries": [q["id"] for q in queries], "expansions_offered": len(expansions)}


def propose_expansions(store: StateStore, token: Optional[str], run_id: str, adapter: Any, *, k: int = 12) -> dict[str, Any]:
    """Ask a model for other names for the idea (one call). They are stored PROPOSED and used only after the researcher accepts them."""
    k = max(1, min(int(k), 30))
    run = load_run(store, run_id)
    c = run["content"]
    pr = load_problem(store, c["problem_id"])
    model = adapter_identity(adapter)
    fp = canonical_json_hash({"run": [run_id, run["revision"], run["revision_digest"]], "k": k, "skill": EXPANSION_SKILL_DIGEST, "model": model})
    base = {"workflow": "retrieval_expansion", "input_fingerprint": fp, "executor": "retrieval_expansion@1", "applicability": Applicability.APPLICABLE.value,
            "model_descriptor": model, "skill_digest": EXPANSION_SKILL_DIGEST,
            "input_revisions": [{"object_id": run_id, "revision": run["revision"], "content_digest": run["content_digest"]}]}

    def fail(reason: str, usage: dict) -> str:
        return workflow_runs.append_run(store, {**base, "execution_status": ExecutionStatus.FAILED.value, "context_status": ContextStatus.COMPLETE.value,
                                                "assessment_status": AssessmentStatus.NOT_ASSESSED.value, "legacy_status": "FAILED", "status_reason": reason,
                                                "usage": usage})

    listed = ", ".join(sorted({t for q in c["queries"] for t in q["tokens"]} | {e["term"] for e in c["expansions"]})) or "(none)"
    prompt = (f"PROBLEM:\n{pr['content']['statement']}\n\nREADINGS BEING SEARCHED:\n" + "\n".join(f"- {q['text']}" for q in c["queries"])
              + f"\n\nWORDS ALREADY USED (do not repeat): {listed}\n\nList up to {k} other names for the same idea.")
    try:
        raw, usage = adapter.generate(EXPANSION_PROMPT, prompt, expected_schema=EXPANSION_SCHEMA, temperature=0.0)
    except ModelError as exc:
        return {"status": "FAILED", "reason": type(exc).__name__, "message": str(exc), "run_id": fail(type(exc).__name__, {"llm_calls": 1}), "proposed": []}
    use = {"llm_calls": 1, "input_tokens": getattr(usage, "input_tokens", None), "output_tokens": getattr(usage, "output_tokens", None),
           "usage_source": getattr(usage, "usage_source", None)}
    parsed, err = parse_model_output(raw)
    problems = [] if parsed is None else sorted(e.message[:120] for e in Draft202012Validator(EXPANSION_SCHEMA).iter_errors(parsed))
    if parsed is None or problems:
        return {"status": "FAILED", "reason": "invalid_output", "message": err or "; ".join(problems[:3]), "run_id": fail("invalid_output", use), "proposed": []}
    who = f"model:{model.get('model_id') or model.get('provider')}"
    row = {**base, "execution_status": ExecutionStatus.SUCCEEDED.value, "context_status": ContextStatus.COMPLETE.value, "assessment_status": AssessmentStatus.PROVISIONAL.value,
           "legacy_status": "SUCCESS", "status_reason": None, "usage": use}
    added: list[str] = []
    seen = {_norm(e["term"]) for e in c["expansions"]} | {_norm(t) for t in c["terms"]}
    exps = list(c["expansions"])
    for item in parsed["terms"][:k]:
        if _norm(item["term"]) in seen:
            continue
        seen.add(_norm(item["term"]))
        exps.append({"term": item["term"].strip(), "source": who, "interpretation": None, "status": "PROPOSED", "origin": "model expansion", "rationale": (item.get("rationale") or "")[:300]})
        added.append(item["term"].strip())
    try:
        out = _put(store, token, run_id, {**c, "expansions": exps}, "retrieval_expansions_proposed", expected_head=run["revision"], run_row=row)
    except Exception as exc:
        try:
            fail(f"write_refused:{getattr(exc, 'code', type(exc).__name__)}", use)
        except Exception:  # noqa: BLE001
            pass
        raise
    return {"status": "PROPOSED", "retrieval_id": run_id, "revision": out["revision"], "proposed": added, "run_id": out["run_id"], "model": who}


def decide_expansions(store: StateStore, token: Optional[str], run_id: str, *, rationale: str, accept: tuple = (), reject: tuple = (), add: tuple = ()) -> dict[str, Any]:
    """The researcher accepts or rejects proposed terms, or adds terms of their own (accepted by definition). Needs a reason; a model term is never accepted
    by being proposed."""
    if not rationale or not rationale.strip():
        raise KnowledgeError("RATIONALE_REQUIRED", "say why these terms are accepted or rejected")
    if not (accept or reject or add):
        raise KnowledgeError("NOTHING_TO_DECIDE", "give terms to accept, reject or add")
    run = load_run(store, run_id)
    with store.write_tx(token=token, require_roles=ROLES) as tx:
        c = tx.get(run_id)["content"]
        exps = [dict(e) for e in c["expansions"]]
        for verb, status, items in (("accept", "ACCEPTED", accept), ("reject", "REJECTED", reject)):
            for t in items:
                hit = next((e for e in exps if _norm(e["term"]) == _norm(t)), None)
                if hit is None:
                    raise KnowledgeError("TERM_UNKNOWN", f"{t!r} is not among the proposed terms of {run_id}")
                hit.update(status=status, decided_by=tx.actor_id, decided_at=tx.at, reason=rationale.strip())
        for t in add:
            if len(t.strip()) < 3:
                raise KnowledgeError("TERM_TOO_SHORT", f"{t!r} is too short to search for")
            if any(_norm(e["term"]) == _norm(t) for e in exps):
                hit = next(e for e in exps if _norm(e["term"]) == _norm(t))
                hit.update(status="ACCEPTED", decided_by=tx.actor_id, decided_at=tx.at, reason=rationale.strip())
            else:
                exps.append({"term": t.strip(), "source": "researcher", "interpretation": None, "status": "ACCEPTED", "origin": "researcher",
                             "decided_by": tx.actor_id, "decided_at": tx.at, "reason": rationale.strip()})
        edit = {"at": tx.at, "by": tx.actor_id, "action": "expansions", "accepted": list(accept) + list(add), "rejected": list(reject), "reason": rationale.strip()}
        head = tx.head(run_id)
        rows = tx.conn.execute("SELECT relation,target_id,target_revision,provenance FROM edges WHERE source_id=? AND source_revision=?", (run_id, head["head_revision"])).fetchall()
        res = tx.add_revision(object_id=run_id, object_type="RetrievalRun", content={**c, "expansions": exps, "edits": _log(c, edit)}, expected_head=run["revision"],
                              edges=[(r[0], r[1], r[2], json.loads(r[3])) for r in rows], origin=engine_origin("retrieval_expansions_decided", tx.actor_id))
        tx.audit("retrieval_expansions_decided", "RECORDED", {"retrieval_id": run_id, "revision": res["revision"]})
    return {"retrieval_id": run_id, "revision": res["revision"], "accepted": sorted(set(_accepted({"expansions": exps})), key=_norm)}


# ------------------------------------------------------------------------------------------------ the search

def _hit_record(index: Index, sid: str, tier: str, per_query: list[dict[str, Any]], accepted: list[str]) -> dict[str, Any]:
    best = min(per_query, key=lambda q: (q["rank"], -q["score"], q["query"]))
    src = index.sources[sid]
    c = src["content"]
    stage = "R1" if any(q["stage"] == "R1" for q in per_query) else "R2"
    terms_in_unit = index.unit_terms(tier, best["unit"])
    exp_found = [t for t in accepted if set(X.tokenize(t)) and set(X.tokenize(t)) <= terms_in_unit]
    matched = best["base_matched"] if best["stage"] == "R1" and best["base_matched"] else best["matched"]       # an R1 hit is explained by the terms of the query itself
    shown = ", ".join(f"{t} ({n})" for t, n in sorted(matched.items(), key=lambda kv: (-kv[1], kv[0]))[:8])
    reason = (f"{stage}: matched {shown} in {best['section']}"
              + (f"; found only through the accepted expansion {', '.join(exp_found)}" if stage == "R2" and exp_found
                 else f"; it also contains the accepted expansion {', '.join(exp_found)}" if exp_found else ""))
    return {"source_id": sid, "source_revision": src["revision"], "work_id": src["work_id"], "tier": tier, "title": c.get("title"), "rank": 0, "score": best["score"], "stage": stage,
            "queries": [{k: q[k] for k in ("query", "interpretation", "rank", "score", "stage")} for q in sorted(per_query, key=lambda q: q["query"])],
            "matched_terms": matched, "expansion_terms": exp_found, "section": best["section"], "locator": best["locator"], "reason": reason,
            "document_type": c.get("document_type"), "review_status": c.get("review_status"), "first_public_date": c.get("first_public_date")}


def search(store: StateStore, token: Optional[str], run_id: str) -> dict[str, Any]:
    """Execute R1 (and R2 with the accepted expansions) and R3a, record hits with their provenance and the coverage, and bind the result to the corpus snapshot."""
    run = load_run(store, run_id)
    c = run["content"]
    cfg = c["config"]
    index = Index(store, cfg)
    accepted = _accepted(c)
    exp_tokens = X.phrase_tokens(accepted)
    top_n = cfg["top_n"]
    per_source: dict[tuple, list[dict]] = {}
    for q in c["queries"]:
        for tier in TIER_ORDER:
            r1 = index.rank(tier, q["tokens"], top_n)
            r2 = index.rank(tier, sorted(set(q["tokens"]) | set(exp_tokens)), top_n) if exp_tokens else r1
            r1_ids = {h["source_id"] for h in r1}
            for rank, h in enumerate(r2, 1):
                per_source.setdefault((tier, h["source_id"]), []).append({**h, "query": q["id"], "interpretation": q["interpretation"], "rank": rank,
                                                                           "stage": "R1" if h["source_id"] in r1_ids else "R2",
                                                                           "base_matched": index.bm25[tier].matched(h["unit"], q["tokens"])})
    hits: list[dict[str, Any]] = []
    for tier in TIER_ORDER:
        tier_hits = [_hit_record(index, sid, t, pq, accepted) for (t, sid), pq in per_source.items() if t == tier]
        tier_hits.sort(key=lambda h: (min(q["rank"] for q in h["queries"]), -h["score"], h["source_id"]))
        for n, h in enumerate(tier_hits, 1):
            h["rank"] = n
        hits.extend(tier_hits)
    hit_ids = {h["source_id"] for h in hits}
    seeds = [h["source_id"] for h in hits if h["tier"] == "FULL_TEXT"][:cfg["neighbours_from"]]
    nb: dict[str, dict[str, Any]] = {}
    for link in index.links:
        for seed, other, relation in ((link["citing"], link["cited"], "cited_by_a_reading_list_paper"), (link["cited"], link["citing"], "cites_a_reading_list_paper")):
            if seed in seeds and other not in hit_ids:
                src = index.sources[other]
                e = nb.setdefault(other, {"source_id": other, "source_revision": src["revision"], "work_id": src["work_id"], "tier": src["tier"], "title": src["content"].get("title"),
                                          "stage": "R3a", "relations": [], "document_type": src["content"].get("document_type"),
                                          "review_status": src["content"].get("review_status"), "first_public_date": src["content"].get("first_public_date")})
                e["relations"].append({"relation": relation, "via": seed, "match": link["match"], "locator": link["locator"]})
    neighbours = sorted(nb.values(), key=lambda e: e["source_id"])
    for e in neighbours:
        r = e["relations"][0]
        e["reason"] = f"R3a: {r['relation'].replace('_', ' ')} {r['via']} ({r['match']} match at {r['locator']})"
    members = index.members
    tiers = Counter(m[2] for m in members if not m[3])
    full_hits = [h for h in hits if h["tier"] == "FULL_TEXT"]
    years = [s["content"].get("year") for s in index.sources.values() if s["content"].get("year") and not s["excluded"]]
    works = {s["work_id"] for s in index.sources.values() if not s["excluded"]}
    coverage = {
        "corpus": {"sources_total": len(members), "excluded": sum(1 for m in members if m[3]), "full_text": tiers.get("FULL_TEXT", 0), "abstract_only": tiers.get("ABSTRACT_ONLY", 0),
                   "metadata_only": tiers.get("METADATA_ONLY", 0), "works": len(works), "unreadable_full_texts": sorted(index.unreadable)},
        "assessed": {"retrieved_sources": len(hit_ids) + len(neighbours), "retrieved_full_text": len(full_hits) + sum(1 for e in neighbours if e["tier"] == "FULL_TEXT"),
                     "of_full_text_in_corpus": tiers.get("FULL_TEXT", 0), "note": "'searched corpus' means the sources retrieved here, not every source held"},
        "stages": {"R1": sum(1 for h in hits if h["stage"] == "R1"), "R2_only": sum(1 for h in hits if h["stage"] == "R2"), "R3a_neighbours": len(neighbours),
                   "R2_enabled": bool(exp_tokens), "accepted_expansions": accepted,
                   "proposed_not_decided": sorted(e["term"] for e in c["expansions"] if e["status"] == "PROPOSED"),
                   "rejected_expansions": sorted(e["term"] for e in c["expansions"] if e["status"] == "REJECTED")},
        "queries": [{"id": q["id"], "interpretation": q["interpretation"], "tokens": q["tokens"]} for q in c["queries"]],
        "exclusions": [{"source_id": sid, "reason": s["content"]["excluded"]["reason"]} for sid, s in sorted(index.sources.items()) if s["excluded"]],
        "external_calls": {"metadata_sources": "not used in this version", "calls": 0},
        "known_gaps": {"not_read_candidates": sum(1 for h in hits if h["tier"] != "FULL_TEXT") + sum(1 for e in neighbours if e["tier"] != "FULL_TEXT"),
                       "year_range": [min(years), max(years)] if years else None, "languages": "not determined (no language detection in this version)",
                       "reference_lists_parsed": sorted(k for k, v in index.references_parsed.items() if v),
                       "reference_lists_not_found": sorted(k for k, v in index.references_parsed.items() if not v)},
        "floor": {"min_fulltext_sources": cfg["min_fulltext_sources"], "full_text_retrieved": len(full_hits),
                  "below_floor": len(full_hits) < cfg["min_fulltext_sources"],
                  "note": "counts full texts retrieved by the query; being retrieved is not a judgement that a paper is relevant: your review decides that"},
        "limits": ["a retrieval score ranks what to read; it says nothing about what a paper claims", "a source that was not retrieved is not shown to be irrelevant",
                   f"each query lists at most {cfg['top_n']} sources per tier: a source ranked lower is not shown",
                   "metadata-only and abstract-only sources were not read and can support or challenge nothing"]}
    snapshot = corpus.corpus_snapshot_id(store)
    content = {**c, "state": "SEARCHED", "corpus_snapshot_id": snapshot, "corpus_members": members, "hits": hits, "neighbours": neighbours, "coverage": coverage,
               "config": {**cfg, "searched_with_expansions": accepted, "searched_with_terms": c["terms"]}}
    out = _put(store, token, run_id, content, "retrieval_search", expected_head=run["revision"])
    return {**out, "corpus_snapshot_id": snapshot, "reading_list": len(full_hits), "not_read_candidates": coverage["known_gaps"]["not_read_candidates"],
            "neighbours": len(neighbours), "below_floor": coverage["floor"]["below_floor"]}


# ------------------------------------------------------------------------------------------------ R5: the researcher's review

def review(store: StateStore, token: Optional[str], run_id: str, action: str, *, reason: str, source_id: Optional[str] = None, title: Optional[str] = None,
           doi: Optional[str] = None) -> dict[str, Any]:
    """`irrelevant` (take a hit off the reading list, for this run only), `restore`, `add` (a held paper the researcher knows), `acquire` (a paper wanted and
    not held: recorded as a request, nothing is created). Each needs a reason and is recorded with who and when."""
    if action not in ("irrelevant", "restore", "add", "acquire"):
        raise KnowledgeError("ACTION_UNKNOWN", "action must be irrelevant, restore, add or acquire")
    if not reason or not reason.strip():
        raise KnowledgeError("REASON_REQUIRED", "say why")
    run = load_run(store, run_id)
    if run["content"]["state"] != "SEARCHED":
        raise KnowledgeError("RETRIEVAL_NOT_SEARCHED", "search first: there is no reading list to review yet")
    with store.write_tx(token=token, require_roles=ROLES) as tx:
        c = tx.get(run_id)["content"]
        known = {h["source_id"] for h in c["hits"]} | {e["source_id"] for e in c["neighbours"]}
        edit: dict[str, Any] = {"at": tx.at, "by": tx.actor_id, "action": action, "reason": reason.strip()}
        if action in ("irrelevant", "restore"):
            if source_id not in known:
                raise KnowledgeError("SOURCE_NOT_IN_RUN", f"{source_id} was not retrieved by {run_id}")
            edit["source_id"] = source_id
        elif action == "add":
            head = tx.head(source_id or "")
            if not head or head["object_type"] != "Source":
                raise KnowledgeError("SOURCE_NOT_FOUND", f"{source_id} is not a Source in this store")
            if (tx.get(source_id)["content"].get("excluded")):
                raise KnowledgeError("SOURCE_EXCLUDED", f"{source_id} is excluded from the corpus; include it first")
            edit["source_id"] = source_id
        else:
            if not title or not title.strip():
                raise KnowledgeError("TITLE_REQUIRED", "say which paper you want to acquire")
            edit.update(title=title.strip(), doi=(doi or None))
        head = tx.head(run_id)
        rows = tx.conn.execute("SELECT relation,target_id,target_revision,provenance FROM edges WHERE source_id=? AND source_revision=?", (run_id, head["head_revision"])).fetchall()
        res = tx.add_revision(object_id=run_id, object_type="RetrievalRun", content={**c, "edits": _log(c, edit)}, expected_head=run["revision"],
                              edges=[(r[0], r[1], r[2], json.loads(r[3])) for r in rows], origin=engine_origin(f"retrieval_{action}", tx.actor_id))
        tx.audit(f"retrieval_{action}", "RECORDED", {"retrieval_id": run_id, "revision": res["revision"]})
    return {"retrieval_id": run_id, "revision": res["revision"], "action": action}


# ------------------------------------------------------------------------------------------------ reading a run

def corpus_currency(store: StateStore, c: dict[str, Any]) -> dict[str, Any]:
    if c["state"] != "SEARCHED":
        return {"status": "NOT_SEARCHED"}
    now = corpus.corpus_snapshot_id(store)
    if now == c["corpus_snapshot_id"]:
        return {"status": "CURRENT", "snapshot": now}
    old = {m[0]: m for m in c["corpus_members"]}
    cur = {m[0]: m for m in current_members(store)}
    return {"status": "STALE_FOR_CORPUS", "from": c["corpus_snapshot_id"], "to": now,
            "added": sorted(set(cur) - set(old)), "removed": sorted(set(old) - set(cur)),
            "revised": sorted(s for s in set(old) & set(cur) if old[s][1] != cur[s][1]),
            "note": "conclusions drawn from the absence of a source rest on the old snapshot until the search is run again"}


def _group_by_work(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict]] = {}
    for e in entries:
        groups.setdefault(e["work_id"], []).append(e)
    out = []
    for wid, es in groups.items():
        es = sorted(es, key=lambda e: (e.get("first_public_date") or "", e["source_id"]))
        rep = es[-1]
        best = min(es, key=lambda e: e["rank"] if e.get("rank") is not None else 10 ** 9)
        out.append({"work_id": wid, "representative": rep["source_id"], "rank": best.get("rank"), "score": max((e.get("score") or 0) for e in es), "stage": best["stage"],
                    "reason": best["reason"], "locator": best.get("locator"), "section": best.get("section"), "matched_terms": best.get("matched_terms", {}),
                    "expansion_terms": best.get("expansion_terms", []), "queries": best.get("queries", []),
                    "versions": [{k: e.get(k) for k in ("source_id", "source_revision", "title", "document_type", "review_status", "first_public_date", "tier")} for e in es]})
    out.sort(key=lambda g: (g["rank"] if g["rank"] is not None else 10 ** 9, g["work_id"]))
    for n, g in enumerate(out, 1):
        g["position"] = n
    return out


def run_view(store: StateStore, run_id: str) -> dict[str, Any]:
    run = load_run(store, run_id)
    c = run["content"]
    members = {m[0]: m for m in current_members(store)}
    irrelevant: dict[str, str] = {}
    added: dict[str, str] = {}
    acquire: list[dict[str, Any]] = []
    for e in c["edits"]:
        if e["action"] == "irrelevant":
            irrelevant[e["source_id"]] = e["reason"]
        elif e["action"] == "restore":
            irrelevant.pop(e["source_id"], None)
        elif e["action"] == "add":
            added[e["source_id"]] = e["reason"]
        elif e["action"] == "acquire":
            acquire.append({"title": e["title"], "doi": e.get("doi"), "reason": e["reason"], "requested_by": e["by"], "at": e["at"]})
    full = [h for h in c["hits"] if h["tier"] == "FULL_TEXT" and h["source_id"] not in irrelevant]
    full += [{**n, "rank": None, "score": 0, "queries": [], "matched_terms": {}, "section": None, "locator": n["relations"][0]["locator"]} for n in c["neighbours"]
             if n["tier"] == "FULL_TEXT" and n["source_id"] not in irrelevant and n["source_id"] not in added]
    have = {h["source_id"] for h in full}
    for sid, why in sorted(added.items()):
        m = members.get(sid)
        if m and m[2] == "FULL_TEXT" and sid not in have and sid not in irrelevant:
            s = store.get_object(sid)["content"]
            full.append({"source_id": sid, "source_revision": m[1], "work_id": s.get("work_id") or sid, "tier": "FULL_TEXT", "title": s.get("title"), "rank": None, "score": 0,
                         "stage": "R5_added", "reason": f"R5: added by the researcher ({why})", "document_type": s.get("document_type"),
                         "review_status": s.get("review_status"), "first_public_date": s.get("first_public_date"), "queries": [], "matched_terms": {}})
    not_read = [h for h in c["hits"] if h["tier"] != "FULL_TEXT" and h["source_id"] not in irrelevant] + [n for n in c["neighbours"] if n["tier"] != "FULL_TEXT"]
    for sid, why in sorted(added.items()):
        m = members.get(sid)
        if m and m[2] != "FULL_TEXT":
            s = store.get_object(sid)["content"]
            not_read.append({"source_id": sid, "source_revision": m[1], "work_id": s.get("work_id") or sid, "tier": m[2], "title": s.get("title"), "stage": "R5_added",
                             "reason": f"R5: added by the researcher ({why}); not read", "document_type": s.get("document_type"), "review_status": s.get("review_status"),
                             "first_public_date": s.get("first_public_date")})
    cur = corpus_currency(store, c)
    outdated = c["state"] == "SEARCHED" and (c["config"].get("searched_with_expansions") != _accepted(c) or c["config"].get("searched_with_terms") != c["terms"])
    after_review = len({h["source_id"] for h in full if h["stage"] in ("R1", "R2", "R5_added")})        # citation neighbours were not retrieved by the query
    return {"retrieval_id": run_id, "revision": run["revision"], "state": c["state"], "problem_id": c["problem_id"], "problem_revision": c["problem_revision"], "queries": c["queries"],
            "terms": c["terms"], "expansions": c["expansions"], "config": c["config"], "corpus_currency": cur, "search_outdated": outdated,
            "reading_list": _group_by_work(full), "not_read_candidates": _group_by_work(not_read), "acquisition_requests": acquire,
            "set_aside": [{"source_id": s, "reason": r} for s, r in sorted(irrelevant.items())], "coverage": c["coverage"], "edits": c["edits"],
            "n_sources_in_reading_list": len(full), "floor_after_your_review": {"full_texts_on_the_list": after_review, "min_fulltext_sources": c["config"]["min_fulltext_sources"],
                                                                                 "below_floor": after_review < c["config"]["min_fulltext_sources"]}}
