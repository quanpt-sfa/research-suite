"""The claim inventory (M5 WP6.2): the claims of a set of Source revisions, who holds each one, what it rests on, and what the researcher decided about it.

A `ClaimInventory` is built from the extraction of the reading protocol `RP_CLAIMS` (one verified quotation per claim, never a paraphrase alone). For every claim it keeps apart

* the **wording**: a verified span of the stored text at Source@revision. What the authors wrote, and the only part that is author-stated for certain;
* the **restatement** (`text`): a model's words, which ECA judges and the researcher confirms; until then it is `AUTHOR_REPORTED`, never verified;
* the **attribution**: who holds the claim. `AUTHOR_OWN_CLAIM`, `AUTHOR_REPORTS_PRIOR_WORK`, `AUTHOR_CRITIQUES_PRIOR_WORK` come from extraction; `RESEARCHER_INTERPRETATION` and
  `MODEL_PROPOSED_INTERPRETATION` are separate records with no wording. A sentence that reports earlier work stays `REPORTED_BY_CITING_AUTHOR`: the suite has not read the cited study and never gives
  it the content. Citation cues in the sentence are a *flag* for the researcher's review and never change an attribution (as ECA's qualifier cues never decide a verdict);
* the **role**: what kind of claim it is (a finding, a proposition, a definition, a contribution, an assumption ...), a separate axis from the attribution;
* **attributes** (scope, premises, mechanism, boundary conditions, constructs): each with a basis, `STATED_IN_QUOTE` only when its content words are in the claim's quotations (a textual check,
  not a reading of meaning) and `MODEL_INFERRED` otherwise. What a model does not give is absent, never defaulted;
* **evidence links**: the wording and any number of further verified spans, each with its role and why it is needed, so a claim is not forced onto one quotation.

Nothing here says a claim is true, established or new. Relations between claims and alignments across versions are in `claim_relations.py`.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from research_harness import taxonomy
from research_harness.core.canonical import canonical_json_hash
from research_harness.discovery import text_index as X
from research_harness.knowledge import corpus
from research_harness.knowledge.common import KnowledgeError, engine_origin, id_fragment, next_numbered_id
from research_harness.state.store import StateStore
from research_harness.workflows import eca, quotes
from research_harness.workflows import reading_protocols as rp

ROLES = ("owner", "author")
GENERATED_BY = "deterministic:claim_inventory@1"
PROTOCOL = "RP_CLAIMS"
EVIDENCE_PROTOCOL = "RP_CLAIM_EVIDENCE"
STRATEGIES = ("single_pass", "section_aware")
FIELD_ROLE = {"empirical_finding": "EMPIRICAL_FINDING", "interpretive_finding": "INTERPRETIVE_FINDING", "theoretical_proposition": "THEORETICAL_PROPOSITION",
              "methodological_claim": "METHODOLOGICAL_CLAIM", "definition": "DEFINITION", "claimed_contribution": "CLAIMED_CONTRIBUTION", "assumption": "ASSUMPTION",
              "boundary_condition": "BOUNDARY_CONDITION", "limitation": "LIMITATION", "gap_statement": "GAP_STATEMENT"}
EVIDENCE_FIELD_ROLE = {"qualifier": "QUALIFIER", "supporting_evidence": "SUPPORTING_EVIDENCE", "premise": "PREMISE", "context": "CONTEXT", "boundary": "BOUNDARY"}
HIGH_ROLES = frozenset({"CLAIMED_CONTRIBUTION", "GAP_STATEMENT"})        # they feed what a paper says it adds: ECA treats them as HIGH
AUTHOR_ATTRIBUTIONS = ("AUTHOR_OWN_CLAIM", "AUTHOR_REPORTS_PRIOR_WORK", "AUTHOR_CRITIQUES_PRIOR_WORK")
INTERPRETATIONS = ("RESEARCHER_INTERPRETATION", "MODEL_PROPOSED_INTERPRETATION")
CORRECTABLE = ("attribution", "role", "text", "granularity")
SECTION_GROUPS = (("introduction", ("preamble", "abstract", "introduction")), ("theory", ("theory_hypotheses",)), ("method", ("data_sample", "measurement", "method")),
                  ("results", ("results", "robustness")), ("discussion", ("limitations", "discussion_conclusion", "other")))
GROUP_NOTE = {"introduction": "an introduction: many sentences describe earlier work, so decide for each sentence who holds the claim",
              "theory": "a theory section: definitions, assumptions and propositions, some of them earlier work's",
              "method": "a method section: claims about methods, measures and data",
              "results": "a results section: usually the authors' own findings",
              "discussion": "a discussion and limitations section: implications, limits, and sometimes earlier work again"}
LIMITS = ["a claim is a model's reading of a sentence, kept with the verified wording: ECA decides whether the restatement is fair, and until the researcher confirms it the claim is only AUTHOR_REPORTED",
          "an attribution is a model's reading of who holds the claim; citation cues in the sentence are flags for review, never a verdict; a sentence that reports earlier work is not given to the cited study",
          "an attribute is STATED_IN_QUOTE only when its content words are in the claim's quotations (a textual check, not a reading of meaning); everything else is MODEL_INFERRED",
          "a count of claims is not evidence that the paper was read completely: the coverage lists the sections that were read, the ones that were not, and what was rejected",
          "relations between claims and alignments across versions exist only as proposals until the researcher decides; none is derived from similarity",
          "nothing here says a claim is true, established or new"]

_CITE = [re.compile(p) for p in (
    r"\b[A-Z][A-Za-z'’\-]+(?: et al\.?)?(?: (?:and|&) [A-Z][A-Za-z'’\-]+)?\s*\(\d{4}[a-z]?\)",
    r"\((?:[A-Z][A-Za-z'’\-]+(?: et al\.?)?(?:,| and| &)? ?)+,? ?\d{4}[a-z]?(?:\s*;[^)]*)?\)",
    r"\[\d+(?:\s*[,–-]\s*\d+)*\]",
    r"\b(?:previous|prior|earlier|existing|extant) (?:studies|work|research|scholarship|literature|accounts?)\b",
    r"\b(?:has|have) (?:been )?(?:shown|established|demonstrated|argued|documented|found)\b(?<!\bwe have found)",
    r"\b(?:according to|as argued by|as shown by|as documented by)\b")]
_OWN = [re.compile(p, re.I) for p in (
    r"\bwe (?:argue|propose|find|show|define|assume|contribute|claim|suggest|add|develop|introduce|report|present|conclude|posit|demonstrate)\b",
    r"\b(?:this|the present|our) (?:paper|study|article|research|work)\b", r"\bour (?:results|findings|analysis|argument|model|account|data|framework|evidence)\b", r"\bin this (?:paper|study|article)\b")]


# ------------------------------------------------------------------------------------------------ small checks

def cue_check(sentence: str, attribution: Optional[str]) -> dict[str, Any]:
    """Flags, never verdicts: citation cues and own-work cues found by fixed patterns in the claim's wording, set against the model's attribution."""
    cites = sorted({m.group(0).strip() for p in _CITE for m in p.finditer(sentence)})
    owns = sorted({m.group(0).strip().lower() for p in _OWN for m in p.finditer(sentence)})
    flag = None
    if attribution is None:
        flag = "ATTRIBUTION_NOT_ASSIGNED"
    elif attribution == "AUTHOR_OWN_CLAIM" and cites and not owns:
        flag = "CITATION_CUE_BUT_LABELLED_OWN"
    elif attribution in ("AUTHOR_REPORTS_PRIOR_WORK", "AUTHOR_CRITIQUES_PRIOR_WORK") and owns and not cites:
        flag = "OWN_CUE_BUT_LABELLED_REPORTED"
    elif attribution == "AUTHOR_REPORTS_PRIOR_WORK" and not cites and not owns:
        flag = "NO_CITATION_CUE_FOR_REPORTED"
    return {"flag": flag, "citation_cues": cites, "own_cues": owns, "meaning": "a pattern match on the wording: a reason to look at the attribution, never a decision about it"}


_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def content_words(text: str) -> list[str]:
    """Words that carry content, lower-cased and lightly stemmed, numbers kept (the retrieval tokenizer drops them, but "2005" is exactly what an attribute may state)."""
    return [X.stem(w) for w in _WORD.findall((text or "").casefold()) if (w.isdigit() or len(w) >= 2) and w not in X.STOPWORDS]


def stated_in(value: str, quoted: list[str]) -> bool:
    """The attribute's content words are all in the claim's quotations. A textual check, not a reading of meaning: a paraphrase reads as inferred, which is the safe error, and words that are
    all present but combined differently (a span of years built from two spans the text gives) read as stated, which is the unsafe one, so the exports say what the check is."""
    want = content_words(value)
    have = set(content_words(" ".join(quoted)))
    return bool(want) and all(t in have for t in want)


def _basis(value: str, quoted: list[str]) -> dict[str, str]:
    return {"value": value, "basis": "STATED_IN_QUOTE" if stated_in(value, quoted) else "MODEL_INFERRED"}


def _basis_text(value: str, quoted: list[str]) -> dict[str, str]:
    return {"text": value, "basis": "STATED_IN_QUOTE" if stated_in(value, quoted) else "MODEL_INFERRED"}


def _texts(values: Any) -> list[str]:
    return [str(v).strip() for v in values or [] if isinstance(v, (str, int, float)) and str(v).strip()]


def _need_text(value: Optional[str], what: str) -> str:
    if not value or not value.strip():
        raise KnowledgeError("REASON_REQUIRED", f"{what} is required")
    return value.strip()


# ------------------------------------------------------------------------------------------------ the object

def load_inventory(store: StateStore, inventory_id: str) -> dict[str, Any]:
    o = store.get_object(inventory_id)
    if not o or o["object_type"] != "ClaimInventory":
        raise KnowledgeError("INVENTORY_NOT_FOUND", f"{inventory_id} is not a ClaimInventory in this project")
    return o


def list_inventories(store: StateStore) -> list[dict[str, Any]]:
    out = []
    with store.read() as conn:
        for (oid,) in conn.execute("SELECT object_id FROM object_heads WHERE object_type='ClaimInventory' ORDER BY object_id").fetchall():
            o = store.get_object(oid, conn=conn)
            c = o["content"]
            out.append({"inventory_id": oid, "revision": o["revision"], "label": c.get("label"), "sources": len(c["sources"]), "claims": len(c["elements"]),
                        "relations": len(c["relations"]), "alignments": len(c["alignments"])})
    return out


def _edges_for(content: dict[str, Any]) -> list[tuple[str, str, int, dict[str, Any]]]:
    """derived_from every Source revision read and every passage a claim cites: so a library closure carries them and a revised Source flags the inventory."""
    edges: list[tuple[str, str, int, dict[str, Any]]] = []
    seen: set[tuple[str, int]] = set()
    for s in content["sources"]:
        k = (s["source_id"], s["source_revision"])
        if k not in seen:
            seen.add(k)
            edges.append(("derived_from", k[0], k[1], {"edge_kind": "read_for_claim_inventory"}))
    for c in content["elements"]:
        links = ([c["wording"]] if c.get("wording") else []) + list(c.get("evidence") or [])
        for ln in links:
            k = (ln["passage_id"], ln["passage_revision"])
            if k not in seen:
                seen.add(k)
                edges.append(("derived_from", k[0], k[1], {"edge_kind": "claim_passage"}))
    return edges


def _summary(content: dict[str, Any]) -> dict[str, Any]:
    by_attr: dict[str, int] = {}
    by_role: dict[str, int] = {}
    for c in content["elements"]:
        by_attr[(c["attribution"] or {}).get("value") or "NOT_ASSIGNED"] = by_attr.get((c["attribution"] or {}).get("value") or "NOT_ASSIGNED", 0) + 1
        by_role[c["role"]] = by_role.get(c["role"], 0) + 1
    usage = {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0}
    complete = 0
    for s in content["sources"]:
        ex = s["extraction"]
        for k in usage:
            usage[k] += (ex.get("usage") or {}).get(k) or 0
        complete += 1 if ex.get("chars_read") is not None and ex["chars_read"] >= ex["chars_total"] - ex.get("chars_excluded", 0) and not ex.get("context_partial") else 0
    cov = {"sources": len(content["sources"]), "claims": len(content["elements"]), "claims_by_attribution": dict(sorted(by_attr.items())), "claims_by_role": dict(sorted(by_role.items())),
           "sources_read_completely": complete, "model_calls": usage["llm_calls"], "input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"],
           "records_rejected": sum(len(s["extraction"].get("rejected") or []) for s in content["sources"]),
           "claims_needing_follow_up": sum(1 for c in content["elements"] if c.get("needs_followup")),
           "relations": len(content["relations"]), "alignments": len(content["alignments"])}
    return {**content, "coverage": cov, "limits": LIMITS}


def _write_with(store: StateStore, token: Optional[str], inventory_id: str, build: Any, operation: str) -> dict[str, Any]:
    """One transaction: the next revision, built from the content as it is inside the transaction (`build(content, tx) -> content`)."""
    with store.write_tx(token=token, require_roles=ROLES) as tx:
        head = tx.head(inventory_id)
        if head is None or head["object_type"] != "ClaimInventory":
            raise KnowledgeError("INVENTORY_NOT_FOUND", f"{inventory_id} is not a ClaimInventory in this project")
        cur = tx.get(inventory_id)
        content = build(cur["content"], tx)
        if content == cur["content"]:
            return {"inventory_id": inventory_id, "revision": cur["revision"], "changed": False}
        content = _summary(content)
        res = tx.add_revision(object_id=inventory_id, object_type="ClaimInventory", content=content, edges=_edges_for(content), expected_head=head["head_revision"],
                              origin=engine_origin(operation, tx.actor_id))
        tx.audit(operation, "RECORDED", {"inventory_id": inventory_id, "revision": res["revision"]})
    return {"inventory_id": inventory_id, "revision": res["revision"], "changed": True}


def _next_claim_id(elements: list[dict[str, Any]]) -> str:
    return f"C{max((int(e['id'][1:]) for e in elements), default=0) + 1:03d}"


# ------------------------------------------------------------------------------------------------ claims from extraction

def _claim_from_record(cid: str, rec: dict[str, Any], src: dict[str, Any], passage: dict[str, Any], res: dict[str, Any], model_tag: str) -> dict[str, Any]:
    attrs = rec.get("attributes") or {}
    role = FIELD_ROLE[rec["field"]]
    quoted = [rec["quote"]] + [e["quote"] for e in rec.get("extra_quotes") or []]
    attribution = rec.get("type") if rec.get("type") in AUTHOR_ATTRIBUTIONS else None
    pc = passage["content"]
    wording = {"passage_id": passage["object_id"], "passage_revision": passage["revision"], "quote": rec["quote"], "char_start": rec["char_start"], "char_end": rec["char_end"],
               "section": pc.get("section"), "status": "QUOTE_VERIFIED"}
    evidence = [{"passage_id": passage["object_id"], "passage_revision": passage["revision"], "link_role": "WORDING", "why_needed": "the authors' own words for the claim",
                 "derivation": "extraction", "quote": rec["quote"], "char_start": rec["char_start"], "char_end": rec["char_end"]}]
    valid_roles = set(taxonomy.vocabulary("claim_evidence_link_role")) - {"WORDING"}
    for e in rec.get("extra_quotes") or []:
        given = e.get("role")
        link = {"passage_id": e["passage_id"], "passage_revision": e["revision"], "link_role": given if given in valid_roles else "CONTEXT",
                "why_needed": e.get("why") or "", "derivation": "extraction", "quote": e["quote"], "char_start": e["char_start"], "char_end": e["char_end"]}
        if given not in valid_roles:
            link["role_as_given"] = given
        evidence.append(link)
    scope = {k: _basis(str(v).strip(), quoted) for k, v in (attrs.get("scope") or {}).items() if isinstance(v, str) and v.strip()}
    mech = attrs.get("mechanism")
    granularity = attrs.get("granularity") or "ATOMIC"
    components = [{"text": str(x["text"]).strip(), "conditions": _texts(x.get("conditions"))} for x in attrs.get("components") or [] if isinstance(x, dict) and str(x.get("text", "")).strip()]
    cited = [{"citation": cw, "state": "REPORTED_BY_CITING_AUTHOR", "source_id": None} for cw in _texts(attrs.get("cited_works"))]
    follow: list[str] = []
    if attrs.get("needs_deeper_reading"):
        follow.append("NEEDS_DEEPER_READING")
    if granularity == "COMPOUND":
        follow.append("COMPOUND_CLAIM")
    if role in ("EMPIRICAL_FINDING", "INTERPRETIVE_FINDING") and attribution == "AUTHOR_OWN_CLAIM" and len(evidence) == 1:
        follow.append("NO_EVIDENCE_BEYOND_WORDING")
    text = (rec.get("value") or rec["quote"]).strip()
    return {"id": cid, "category": role.lower(), "role": role, "text": text, "restated": text != rec["quote"].strip(), "label": rec.get("label"),
            "attribution": {"value": attribution, "assigned_by": model_tag, "cue": (attrs.get("attribution_cue") or None), "cue_check": cue_check(rec["quote"], attribution)},
            "cited_works": cited, "granularity": granularity if granularity in ("ATOMIC", "COMPOUND") else "ATOMIC", "components": components,
            "constructs": _texts(attrs.get("constructs")), "scope": scope, "premises": [_basis_text(p, quoted) for p in _texts(attrs.get("premises"))],
            "mechanism": _basis_text(mech.strip(), quoted) if isinstance(mech, str) and mech.strip() else None,
            "boundary_conditions": [_basis_text(b, quoted) for b in _texts(attrs.get("boundary_conditions"))], "direction": attrs.get("direction"),
            "needs_followup": follow, "work_id": src["work_id"], "version_id": src.get("version_id"), "source_id": src["source_id"], "source_revision": src["source_revision"],
            "wording": wording, "evidence": evidence, "evidence_basis": [{"kind": "source_passage", "ref": passage["object_id"]}], "author_stated": True,
            "protocol": res["protocol"], "extraction_key": res["extraction_key"], "run_id": res["run_id"], "generated_by": model_tag,
            "risk_tier": "HIGH" if role in HIGH_ROLES else "STANDARD", "about": [], "reviews": []}


def _section_runs(store: StateStore, source_id: str, revision: int) -> list[tuple[str, list[tuple[int, int]]]]:
    obj, text = corpus.source_text(store, source_id, revision)
    secs = corpus.source_sections(store, obj, text)
    out = []
    for name, labels in SECTION_GROUPS:
        spans = [(s["char_start"], s["char_end"]) for s in secs if s["label"] in labels and s["label"] != "references" and s["char_end"] > s["char_start"]]
        if spans:
            out.append((name, spans))
    return out


def _extract(store: StateStore, token: Optional[str], item: dict[str, Any], adapter: Any, strategy: str, max_input_tokens: int, reuse: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The records of one source (with the result each came from) and its `sources[]` entry. A failed run stops everything: nothing is written."""
    results = []
    if strategy == "single_pass":
        results.append(("all", rp.run_protocol(store, token, item["source_id"], PROTOCOL, adapter, source_revision=item["source_revision"], max_input_tokens=max_input_tokens, reuse=reuse)))
    else:
        for name, spans in _section_runs(store, item["source_id"], item["source_revision"]):
            results.append((name, rp.run_protocol(store, token, item["source_id"], PROTOCOL, adapter, source_revision=item["source_revision"], max_input_tokens=max_input_tokens,
                                                  reuse=reuse, spans=spans, context=f"Section group: {GROUP_NOTE[name]}.")))
    for name, res in results:
        if res["status"] == "FAILED":
            raise KnowledgeError("EXTRACTION_FAILED", f"{item['source_id']} ({PROTOCOL}, {name}): {res.get('reason')}: {res.get('message')}", source=item["source_id"])
    usage = {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0}
    run_ids, rejected, gaps, keys = [], [], [], []
    chars_read = chars_total = 0
    sections_read: list[str] = []
    not_read: list[str] = []
    partial = False
    for name, res in results:
        run = _run_usage(store, res)
        for k in usage:
            usage[k] += run.get(k) or 0
        run_ids.append(res["run_id"])
        keys.append(res["extraction_key"])
        rejected += [{"reason": r["reason"], "field": r.get("field"), "quote": (r.get("quote") or "")[:200], "group": name} for r in res.get("rejected") or []]
        gaps += res.get("gaps") or []
        chars_read += res.get("chars_read") or 0
        chars_total = max(chars_total, res.get("chars_total") or 0)
        sections_read += res.get("sections_read") or []
        partial = partial or res.get("context_status") == "PARTIAL"
    if strategy == "single_pass":
        not_read = results[0][1].get("sections_not_read") or []
    else:
        obj, text = corpus.source_text(store, item["source_id"], item["source_revision"])
        secs = corpus.source_sections(store, obj, text)
        read_starts = {s["char_start"] for _, spans in _section_runs(store, item["source_id"], item["source_revision"]) for s in secs if (s["char_start"], s["char_end"]) in spans}
        not_read = [s.get("heading") or s["label"] for s in secs if s["char_start"] not in read_starts]
    first = results[0][1]
    entry = {"source_id": item["source_id"], "source_revision": item["source_revision"], "work_id": item["work_id"], "version_id": item.get("version_id"),
             "text_sha256": item.get("text_sha256"), "tier": "FULL_TEXT",
             "extraction": {"protocol": first["protocol"], "protocol_digest": first["protocol_digest"], "strategy": strategy, "extraction_keys": keys, "run_ids": run_ids, "model": first["model"],
                            "chunks": sum(r.get("chunks") or 0 for _, r in results), "sections_read": sections_read, "sections_not_read": not_read, "chars_read": chars_read,
                            "chars_total": chars_total, "context_partial": partial, "usage": {**usage, "usage_source": _run_usage(store, results[0][1]).get("usage_source")},
                            "reused_runs": sum(1 for _, r in results if r.get("reused")), "rejected": rejected, "gaps": sorted(set(gaps))}}
    return [(rec, res) for _, res in results for rec in res["records"]], entry


def _run_usage(store: StateStore, res: dict[str, Any]) -> dict[str, Any]:
    """The usage the registry recorded for this run (a reuse records zero calls: what it saved is not what it cost). The usage source of a reuse is that of the run it reused."""
    from research_harness.state import workflow_runs
    with store.read() as conn:
        row = workflow_runs.get_run(conn, res["run_id"])
        use = dict((row or {}).get("usage") or {})
        if row and row.get("reused_from_run_id") and use.get("usage_source") in (None, "not_available"):
            origin = workflow_runs.get_run(conn, row["reused_from_run_id"]) or {}
            use["usage_source"] = (origin.get("usage") or {}).get("usage_source") or use.get("usage_source")
    return use


def _source_item(store: StateStore, source_id: str) -> dict[str, Any]:
    obj = store.get_object(source_id)
    if not obj or obj["object_type"] != "Source":
        raise KnowledgeError("SOURCE_NOT_FOUND", f"{source_id} is not a Source in this project")
    c = obj["content"]
    if corpus.source_tier(c) != "FULL_TEXT":
        raise KnowledgeError("SOURCE_HAS_NO_FULL_TEXT", f"{source_id} is {corpus.source_tier(c)}: no claim can be read from it")
    return {"source_id": source_id, "source_revision": obj["revision"], "work_id": c.get("work_id") or source_id, "version_id": c.get("version_id"), "text_sha256": c.get("text_sha256")}


def _claims_of(store: StateStore, extracted: list[tuple[dict[str, Any], dict[str, Any]]], item: dict[str, Any], first_number: int) -> list[dict[str, Any]]:
    rows = sorted(extracted, key=lambda x: (x[0]["char_start"], x[0]["char_end"], x[0]["field"]))
    out = []
    for n, (rec, res) in enumerate(rows, first_number):
        passage = store.get_object(rec["passage_id"], rec["revision"])
        model_tag = passage["content"].get("generated_by") or "model:unknown"
        out.append(_claim_from_record(f"C{n:03d}", rec, item, passage, res, model_tag))
    return out


def build(store: StateStore, token: Optional[str], sources: list[str], adapter: Any, *, label: Optional[str] = None, strategy: str = "single_pass",
          max_input_tokens: int = rp.DEFAULT_MAX_INPUT_TOKENS, reuse: bool = True) -> dict[str, Any]:
    """An inventory of the claims of these Source revisions (full text only). Extraction is cached per source, model and settings; a failed extraction writes nothing."""
    if strategy not in STRATEGIES:
        raise KnowledgeError("STRATEGY_UNKNOWN", f"strategy must be one of {STRATEGIES}")
    if not sources:
        raise KnowledgeError("NO_SOURCES", "name at least one Source")
    ids = list(dict.fromkeys(sources))
    items = [_source_item(store, s) for s in ids]
    elements: list[dict[str, Any]] = []
    entries = []
    for item in items:
        extracted, entry = _extract(store, token, item, adapter, strategy, max_input_tokens, reuse)
        elements += _claims_of(store, extracted, item, len(elements) + 1)
        entries.append(entry)
    content = _summary({"label": label or f"Claims of {len(items)} source(s)", "kind": "claim_inventory", "state": "BUILT", "sources": entries, "elements": elements, "relations": [], "alignments": [],
                        "corpus_snapshot_id": corpus.corpus_snapshot_id(store), "edits": [], "generated_by": GENERATED_BY})
    with store.write_tx(token=token, require_roles=ROLES) as tx:
        iid = next_numbered_id(tx.conn, "CINV_", "ClaimInventory")
        res = tx.add_revision(object_id=iid, object_type="ClaimInventory", content=content, edges=_edges_for(content), expected_head=0, origin=engine_origin("claims_build", tx.actor_id))
        tx.audit("claims_build", "RECORDED", {"inventory_id": iid, "revision": res["revision"], "claims": len(elements)})
    cov = content["coverage"]
    return {"inventory_id": iid, "revision": res["revision"], "claims": len(elements), "sources": len(entries), "calls": cov["model_calls"], "input_tokens": cov["input_tokens"],
            "output_tokens": cov["output_tokens"]}


def add_sources(store: StateStore, token: Optional[str], inventory_id: str, sources: list[str], adapter: Any, *, strategy: str = "single_pass",
                max_input_tokens: int = rp.DEFAULT_MAX_INPUT_TOKENS, reuse: bool = True) -> dict[str, Any]:
    """More Source revisions into an inventory: the claims already there are kept (ids are never reused); only the new sources are read."""
    cur = load_inventory(store, inventory_id)["content"]
    have = {s["source_id"] for s in cur["sources"]}
    items = [_source_item(store, s) for s in dict.fromkeys(sources) if s not in have]
    if not items:
        return {"inventory_id": inventory_id, "revision": store.get_object(inventory_id)["revision"], "changed": False, "claims_added": 0}
    new = []
    for it in items:
        extracted, entry = _extract(store, token, it, adapter, strategy, max_input_tokens, reuse)
        new.append((it, extracted, entry))
    added = {"n": 0}

    def build_(c: dict[str, Any], tx: Any) -> dict[str, Any]:
        elements = list(c["elements"])
        n = max((int(e["id"][1:]) for e in elements), default=0) + 1
        for it, extracted, _entry in new:
            made = _claims_of(store, extracted, it, n)
            elements += made
            n += len(made)
            added["n"] += len(made)
        return {**c, "sources": [*c["sources"], *[e for _, _, e in new]], "elements": elements}

    out = _write_with(store, token, inventory_id, build_, "claims_add_sources")
    return {**out, "claims_added": added["n"]}


# ------------------------------------------------------------------------------------------------ selective follow-up

_SENT_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z\[0-9])")
FOLLOWUP_REASONS = ("NEEDS_DEEPER_READING", "COMPOUND_CLAIM")        # what a follow-up call is made for by default; NO_EVIDENCE_BEYOND_WORDING is a flag for the report, not a reason to spend a call
SPAN_CHARS = 2500


def _sentence_spans(text: str, lo: int, hi: int) -> list[tuple[int, int]]:
    """Character spans of the sentences of text[lo:hi]; a line that is not a sentence (a heading) is skipped."""
    out = []
    pos = lo
    for line in text[lo:hi].splitlines(keepends=True):
        stripped = line.strip()
        if len(stripped) >= 20 and stripped[-1] in ".!?)":
            start = pos + (len(line) - len(line.lstrip()))
            cut = start
            for m in _SENT_BREAK.finditer(text, start, pos + len(line.rstrip())):
                out.append((cut, m.start()))
                cut = m.end()
            out.append((cut, pos + len(line.rstrip())))
        pos += len(line)
    return [(a, b) for a, b in out if b - a >= 20]


def _candidate_spans(store: StateStore, claim: dict[str, Any], limit: int = 6, max_chars: int = SPAN_CHARS) -> list[tuple[int, int]]:
    """Sentences of the same source (never the claim's own) ranked by BM25 against the claim's words, each with the sentence before and after it, up to `max_chars` of text: a reason to
    read them, not evidence. Selecting sentences and not paragraphs keeps the second reading short."""
    obj, text = corpus.source_text(store, claim["source_id"], claim["source_revision"])
    spans: list[tuple[int, int]] = []
    for sec in corpus.source_sections(store, obj, text):
        if sec["label"] != "references":
            spans += _sentence_spans(text, sec["char_start"], sec["char_end"])
    if not spans:
        return []
    own = (claim["wording"]["char_start"], claim["wording"]["char_end"])
    is_own = [a < own[1] and own[0] < b for a, b in spans]
    bm = X.Bm25({str(i): X.tokenize(text[a:b]) for i, (a, b) in enumerate(spans) if not is_own[i]})
    scores = bm.score(X.tokenize(claim["text"] + " " + claim["wording"]["quote"]))
    ranked = sorted(scores, key=lambda i: (-round(scores[i], 9), int(i)))[:limit]
    keep: set[int] = set()
    used = 0
    for r in ranked:
        i = int(r)
        window = [j for j in (i - 1, i, i + 1) if 0 <= j < len(spans) and not is_own[j] and j not in keep]
        cost = sum(spans[j][1] - spans[j][0] for j in window)
        if used and used + cost > max_chars:
            continue
        keep.update(window)
        used += cost
    out: list[tuple[int, int]] = []
    for j in sorted(keep):
        if out and out[-1][1] <= spans[j][0] and j - 1 in keep and not is_own[j - 1] and text[out[-1][1]:spans[j][0]].strip() == "":
            out[-1] = (out[-1][0], spans[j][1])
        else:
            out.append(spans[j])
    return out


def followup(store: StateStore, token: Optional[str], inventory_id: str, adapter: Any, *, claim_ids: Optional[list[str]] = None, reasons: tuple[str, ...] = FOLLOWUP_REASONS,
             max_claims: Optional[int] = None, max_input_tokens: int = rp.DEFAULT_MAX_INPUT_TOKENS, reuse: bool = True) -> dict[str, Any]:
    """The selective second look. One `RP_CLAIM_EVIDENCE` call for each claim that is named, or flagged for one of `reasons` (by default: the model asked for deeper reading, or the claim is
    compound), over a few sentences chosen by words alone. Links it finds are added with `derivation: followup`, each verified. `max_claims` is a budget: the claims it leaves out are
    listed, never silently dropped. A claim that needs nothing costs nothing."""
    cur = load_inventory(store, inventory_id)["content"]
    pool = [c for c in cur["elements"] if c.get("wording") and ((claim_ids and c["id"] in claim_ids) or (not claim_ids and set(c.get("needs_followup") or []) & set(reasons)))]
    wanted = pool[:max_claims] if max_claims is not None else pool
    deferred = [c["id"] for c in pool[len(wanted):]]
    found: dict[str, list[dict[str, Any]]] = {}
    calls = 0
    for c in wanted:
        spans = _candidate_spans(store, c)
        if not spans:
            continue
        res = rp.run_protocol(store, token, c["source_id"], EVIDENCE_PROTOCOL, adapter, source_revision=c["source_revision"], max_input_tokens=max_input_tokens, reuse=reuse, spans=spans,
                              context=f"Claim: {c['text']} (wording: \"{c['wording']['quote']}\")")
        if res["status"] == "FAILED":
            raise KnowledgeError("EXTRACTION_FAILED", f"{c['id']} ({EVIDENCE_PROTOCOL}): {res.get('reason')}: {res.get('message')}", claim=c["id"])
        calls += 0 if res.get("reused") else 1
        found[c["id"]] = [{"passage_id": r["passage_id"], "passage_revision": r["revision"], "link_role": EVIDENCE_FIELD_ROLE[r["field"]], "why_needed": r["value"], "derivation": "followup",
                           "quote": r["quote"], "char_start": r["char_start"], "char_end": r["char_end"], "run_id": res["run_id"]} for r in res["records"] if r["field"] in EVIDENCE_FIELD_ROLE]

    def build_(content: dict[str, Any], tx: Any) -> dict[str, Any]:
        elements = []
        for c in content["elements"]:
            if c["id"] in found:
                have = {(e["passage_id"], e["passage_revision"]) for e in c["evidence"]}
                add = [e for e in found[c["id"]] if (e["passage_id"], e["passage_revision"]) not in have and (e["char_start"], e["char_end"]) != (c["wording"]["char_start"], c["wording"]["char_end"])]
                left = [f for f in c["needs_followup"] if f not in reasons and not (f == "NO_EVIDENCE_BEYOND_WORDING" and add)]
                c = {**c, "evidence": [*c["evidence"], *add], "needs_followup": left, "followed_up": True, **({} if add else {"followup_found_nothing": True})}
            elements.append(c)
        return {**content, "elements": elements}

    out = _write_with(store, token, inventory_id, build_, "claims_followup") if found else {"inventory_id": inventory_id, "changed": False}
    return {**out, "claims_followed_up": len(found), "model_calls": calls, "links_added": sum(len(v) for v in found.values()), "claims_left_for_budget": deferred}


# ------------------------------------------------------------------------------------------------ the researcher's records

def _claim_in(c: dict[str, Any], claim_id: str) -> dict[str, Any]:
    e = next((x for x in c["elements"] if x["id"] == claim_id), None)
    if e is None:
        raise KnowledgeError("CLAIM_UNKNOWN", f"no claim {claim_id} in this inventory")
    return e


def _replace(content: dict[str, Any], claim: dict[str, Any]) -> dict[str, Any]:
    return {**content, "elements": [claim if e["id"] == claim["id"] else e for e in content["elements"]]}


def review_claim(store: StateStore, token: Optional[str], inventory_id: str, claim_id: str, decision: str, reason: str, *, corrections: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """The researcher's decision on a claim: CONFIRMED (the restatement, attribution and role are a fair reading), CORRECTED (with the corrected fields; the model's own value is kept) or REJECTED
    (not a claim of this paper, or misread). Recorded with who and when."""
    if decision not in taxonomy.vocabulary("claim_review_decision"):
        raise KnowledgeError("DECISION_UNKNOWN", f"decision must be one of {taxonomy.vocabulary('claim_review_decision')}")
    reason = _need_text(reason, "your reason")
    corrections = dict(corrections or {})
    if decision != "CORRECTED" and corrections:
        raise KnowledgeError("CORRECTION_NOT_ALLOWED", "corrections go with the decision CORRECTED")
    if decision == "CORRECTED" and not corrections:
        raise KnowledgeError("CORRECTION_NOT_ALLOWED", "say what is corrected")
    bad = set(corrections) - set(CORRECTABLE)
    if bad:
        raise KnowledgeError("CORRECTION_NOT_ALLOWED", f"only {CORRECTABLE} can be corrected, not {sorted(bad)}")

    def build_(c: dict[str, Any], tx: Any) -> dict[str, Any]:
        claim = _claim_in(c, claim_id)
        allowed = AUTHOR_ATTRIBUTIONS if claim.get("wording") else INTERPRETATIONS
        if "attribution" in corrections and corrections["attribution"] not in allowed:
            raise KnowledgeError("CORRECTION_NOT_ALLOWED", f"attribution of this claim can be corrected to {allowed}, not {corrections['attribution']!r}")
        if "role" in corrections and corrections["role"] not in taxonomy.vocabulary("claim_role"):
            raise KnowledgeError("CORRECTION_NOT_ALLOWED", f"role must be one of {taxonomy.vocabulary('claim_role')}")
        if "granularity" in corrections and corrections["granularity"] not in taxonomy.vocabulary("claim_granularity"):
            raise KnowledgeError("CORRECTION_NOT_ALLOWED", f"granularity must be one of {taxonomy.vocabulary('claim_granularity')}")
        if "text" in corrections:
            corrections["text"] = _need_text(corrections["text"], "the corrected restatement")
        rec = {"at": tx.at, "by": tx.actor_id, "decision": decision, "reason": reason, **({"corrections": corrections} if corrections else {})}
        return _replace(c, {**claim, "reviews": [*claim["reviews"], rec]})

    out = _write_with(store, token, inventory_id, build_, "claims_review_claim")
    return {**out, "claim": claim_id, "decision": decision}


def link_cited_source(store: StateStore, token: Optional[str], inventory_id: str, claim_id: str, citation: str, source_id: str) -> dict[str, Any]:
    """The researcher says which held Source a claim's citation refers to. The claim is still the citing authors' report: the link says where to check it, and nothing is checked."""
    src = store.get_object(source_id)
    if not src or src["object_type"] != "Source":
        raise KnowledgeError("SOURCE_NOT_FOUND", f"{source_id} is not a Source in this project")

    def build_(c: dict[str, Any], tx: Any) -> dict[str, Any]:
        claim = _claim_in(c, claim_id)
        if citation not in [w["citation"] for w in claim["cited_works"]]:
            raise KnowledgeError("CITATION_NOT_IN_CLAIM", f"{claim_id} does not cite {citation!r} (it cites {[w['citation'] for w in claim['cited_works']]})")
        works = [{**w, "state": "CITED_SOURCE_LINKED", "source_id": source_id, "source_revision": src["revision"], "linked_by": tx.actor_id, "linked_at": tx.at}
                 if w["citation"] == citation else w for w in claim["cited_works"]]
        return _replace(c, {**claim, "cited_works": works})

    return {**_write_with(store, token, inventory_id, build_, "claims_link_cited_source"), "claim": claim_id}


def add_interpretation(store: StateStore, token: Optional[str], inventory_id: str, *, text: str, attribution: str, about: list[str], rationale: str, role: str = "ASSUMPTION",
                       premises: Optional[list[str]] = None, missing_information: Optional[list[str]] = None, generated_by: Optional[str] = None) -> dict[str, Any]:
    """A researcher's interpretation, or a model's proposed one, of what the claims imply. It has no wording and is never author-stated; a model's must say what is missing."""
    if attribution not in INTERPRETATIONS:
        raise KnowledgeError("ATTRIBUTION_NOT_ALLOWED_FOR_INTERPRETATION", f"an interpretation is {INTERPRETATIONS}, not {attribution!r}")
    if role not in taxonomy.vocabulary("claim_role"):
        raise KnowledgeError("ROLE_UNKNOWN", f"role must be one of {taxonomy.vocabulary('claim_role')}")
    text = _need_text(text, "the interpretation")
    rationale = _need_text(rationale, "the rationale")
    if not about:
        raise KnowledgeError("ABOUT_REQUIRED", "say which claims the interpretation is about")
    if attribution == "MODEL_PROPOSED_INTERPRETATION":
        if not (generated_by or "").startswith("model:"):
            raise KnowledgeError("GENERATED_BY_REQUIRED", "a model's interpretation names the model: generated_by='model:<id>'")
        if missing_information is None:
            raise KnowledgeError("MISSING_INFORMATION_REQUIRED", "a model's proposed interpretation says what is missing (a list; empty only if nothing is missing)")
    made: dict[str, str] = {}

    def build_(c: dict[str, Any], tx: Any) -> dict[str, Any]:
        for a in about:
            _claim_in(c, a)
        cid = _next_claim_id(c["elements"])
        made["id"] = cid
        who = generated_by if attribution == "MODEL_PROPOSED_INTERPRETATION" else "researcher"
        el = {"id": cid, "category": role.lower(), "role": role, "text": text, "restated": False, "label": None,
              "attribution": {"value": attribution, "assigned_by": who, "cue": None, "cue_check": None}, "cited_works": [], "granularity": "ATOMIC", "components": [], "constructs": [],
              "scope": {}, "premises": [{"text": p, "basis": "MODEL_INFERRED"} for p in _texts(premises)], "mechanism": None, "boundary_conditions": [], "direction": None, "needs_followup": [],
              "work_id": None, "version_id": None, "source_id": None, "source_revision": None, "wording": None, "evidence": [], "evidence_basis": [], "author_stated": False,
              "protocol": None, "extraction_key": None, "run_id": None, "generated_by": who, "risk_tier": "STANDARD", "about": list(about), "rationale": rationale,
              "missing_information": _texts(missing_information), "reviews": [], "proposed_by": tx.actor_id, "proposed_at": tx.at}
        return {**c, "elements": [*c["elements"], el]}

    out = _write_with(store, token, inventory_id, build_, "claims_add_interpretation")
    return {**out, "claim": made["id"]}


def add_evidence_link(store: StateStore, token: Optional[str], inventory_id: str, claim_id: str, *, quote: str, role: str, why: str) -> dict[str, Any]:
    """The researcher adds a further passage to a claim. The quote must be in the stored text of the claim's Source revision; its span is computed here."""
    if role not in taxonomy.vocabulary("claim_evidence_link_role"):
        raise KnowledgeError("ROLE_UNKNOWN", f"link role must be one of {taxonomy.vocabulary('claim_evidence_link_role')}")
    if role == "WORDING":
        raise KnowledgeError("WORDING_LINK_IS_FIXED", "the wording link is the claim's own sentence from extraction and is not added or replaced")
    why = _need_text(why, "why the passage is needed")
    cur = load_inventory(store, inventory_id)["content"]
    claim = _claim_in(cur, claim_id)
    if not claim.get("wording"):
        raise KnowledgeError("CLAIM_HAS_NO_SOURCE", f"{claim_id} is an interpretation: it has no source text to cite")
    obj, text = corpus.source_text(store, claim["source_id"], claim["source_revision"])
    found = quotes.find_quote(text, quote)
    if not found:
        raise KnowledgeError("QUOTE_NOT_IN_SOURCE", f"the quote is not in the stored text of {claim['source_id']} revision {claim['source_revision']}")
    start, end = found
    pid = f"EP_{id_fragment(claim['source_id'], 28)}_CLAIMLINK_{start}_{end}"
    head = store.get_object(pid)
    if head is None:
        corpus.add_passage(store, token, source_id=claim["source_id"], quote=text[start:end], passage_id=pid, generated_by="researcher", item_type="extra_quote",
                           attributes={"extra_of": claim["wording"]["passage_id"], "role": role, "why": why})
        head = store.get_object(pid)

    def build_(c: dict[str, Any], tx: Any) -> dict[str, Any]:
        cl = _claim_in(c, claim_id)
        if any(e["passage_id"] == pid for e in cl["evidence"]):
            raise KnowledgeError("LINK_EXISTS", f"{claim_id} already cites that passage")
        link = {"passage_id": pid, "passage_revision": head["revision"], "link_role": role, "why_needed": why, "derivation": "researcher", "quote": text[start:end], "char_start": start,
                "char_end": end, "added_by": tx.actor_id, "added_at": tx.at}
        return _replace(c, {**cl, "evidence": [*cl["evidence"], link]})

    return {**_write_with(store, token, inventory_id, build_, "claims_add_evidence_link"), "claim": claim_id, "passage": pid}


# ------------------------------------------------------------------------------------------------ what is shown

def _effective(c: dict[str, Any]) -> dict[str, Any]:
    """The claim as the researcher's decisions leave it: corrections override the model's value, which is kept beside the corrected one."""
    att = dict(c["attribution"] or {})
    out = {**c, "attribution": att}
    status = "NOT_ASSIGNED" if att.get("value") is None else "MODEL_ASSIGNED"
    if c["attribution"] and c["attribution"].get("value") in INTERPRETATIONS:
        status = "RESEARCHER_SET" if c["attribution"]["value"] == "RESEARCHER_INTERPRETATION" else "MODEL_PROPOSED"
    model_attr = att.get("value")
    out["status"] = "UNREVIEWED"
    for r in c.get("reviews") or []:
        if r["decision"] == "CONFIRMED":
            if c["author_stated"] or att.get("value") in INTERPRETATIONS:
                status = "RESEARCHER_CONFIRMED" if att.get("value") else status
            out["status"] = "CONFIRMED_BY_RESEARCHER"
        elif r["decision"] == "CORRECTED":
            for k, v in (r.get("corrections") or {}).items():
                if k == "attribution":
                    att["model_value"] = model_attr
                    att["value"] = v
                    status = "RESEARCHER_SET"
                elif k == "text":
                    out["model_text"] = c["text"]
                    out["text"] = v
                    out["restated"] = True
                else:
                    out[f"model_{k}"] = out.get(k)
                    out[k] = v
            out["status"] = "CORRECTED_BY_RESEARCHER"
        elif r["decision"] == "REJECTED":
            out["status"] = "REJECTED_BY_RESEARCHER"
    out["attribution_status"] = status
    return out


def _currency(store: StateStore, c: dict[str, Any]) -> dict[str, Any]:
    if not c.get("source_id"):
        return {"status": "NOT_APPLICABLE", "note": "an interpretation has no source text"}
    head = store.get_object(c["source_id"])
    if not head or head["revision"] != c["source_revision"]:
        return {"status": "SOURCE_REVISED", "read_at": c["source_revision"], "now": head["revision"] if head else None}
    for ln in ([c["wording"]] if c.get("wording") else []) + list(c.get("evidence") or []):
        p = store.get_object(ln["passage_id"])
        if not p or p["revision"] != ln["passage_revision"]:
            return {"status": "PASSAGE_REVISED", "passage": ln["passage_id"]}
    return {"status": "CURRENT", "read_at": c["source_revision"]}


def _reading(store: StateStore, inventory_id: str, claim_id: str) -> dict[str, Any]:
    st = eca.statement_assessment(store, eca.resolve_statement(store, inventory_id, element=claim_id))
    confirmed = st["researcher_confirmed"] and st["eca_verdict"] in ("SUPPORTED_AS_STATED", "SUPPORTED_IF_QUALIFIED")
    return {"value": "READING_CONFIRMED" if confirmed else "AUTHOR_REPORTED", "eca_verdict": st["eca_verdict"], "disputed": st["eca_verdict"] == "NOT_SUPPORTED", "adjudications": st["adjudications"]}


def view(store: StateStore, inventory_id: str) -> dict[str, Any]:
    from research_harness.knowledge import claim_relations as CR
    inv = load_inventory(store, inventory_id)
    c = inv["content"]
    claims = []
    for e in c["elements"]:
        eff = _effective(e)
        cur = _currency(store, e)
        reading = _reading(store, inventory_id, e["id"]) if e.get("wording") else {"value": "NOT_APPLICABLE", "note": "no author wording to read"}
        claims.append({**eff, "currency": cur, "layers": {"wording": e.get("wording"), "restatement": reading}})
    by_id = {x["id"]: x for x in claims}
    for x in claims:
        for w in x["cited_works"]:
            w["checked_against_cited_source"] = bool(w.get("source_id")) and CR.cited_source_checked(c, by_id, x["id"], w["source_id"])
    snapshot_now = corpus.corpus_snapshot_id(store)
    changed = [s["source_id"] for s in c["sources"] if (store.get_object(s["source_id"]) or {}).get("revision") != s["source_revision"]]
    return {"inventory_id": inventory_id, "revision": inv["revision"], "label": c["label"], "corpus_snapshot_id": c.get("corpus_snapshot_id"), "corpus_snapshot_now": snapshot_now,
            "sources": c["sources"], "claims": claims, "relations": CR.enrich_relations(store, c, claims), "alignments": CR.enrich_alignments(store, c, claims),
            "currency": {"sources": {"status": "CURRENT" if not changed else "STALE", "changed": changed}, "all_current": not changed}, "coverage": c["coverage"], "limits": c["limits"]}


def attribution_audit(v: dict[str, Any]) -> list[dict[str, Any]]:
    """Claims whose attribution deserves a second look: not assigned, or at odds with the citation and own-work cues in the wording. A flag is a reason to look, never a decision."""
    out = []
    for c in v["claims"]:
        chk = (c["attribution"] or {}).get("cue_check") or {}
        if not c.get("author_stated") or not chk.get("flag"):
            continue
        reviewed = c["attribution_status"] in ("RESEARCHER_SET", "RESEARCHER_CONFIRMED")
        out.append({"claim": c["id"], "source_id": c["source_id"], "source_revision": c["source_revision"], "flag": chk["flag"], "attribution": c["attribution"].get("value"),
                    "citation_cues": chk.get("citation_cues"), "own_cues": chk.get("own_cues"), "wording": c["wording"]["quote"], "researcher_has_decided": reviewed})
    return out


def candidates(store: StateStore, inventory_id: str, claim_id: str, *, limit: int = 10) -> dict[str, Any]:
    """Claims of other works that may relate to this one, found by the words of the claim and its constructs (BM25; no model). **A candidate is a claim to read next to this one, not a
    relation, and not evidence that the two say the same thing.**"""
    c = load_inventory(store, inventory_id)["content"]
    me = _claim_in(c, claim_id)
    pool = [e for e in c["elements"] if e["id"] != claim_id and e.get("work_id") and e["work_id"] != me.get("work_id")]
    if not pool:
        return {"claim": claim_id, "candidates": [], "note": "no claim of another work in this inventory"}
    bm = X.Bm25({e["id"]: X.tokenize(e["text"] + " " + " ".join(e["constructs"])) for e in pool})
    q = X.tokenize(me["text"] + " " + " ".join(me["constructs"]))
    scores = bm.score(q)
    by = {e["id"]: e for e in pool}
    rows = sorted(scores, key=lambda i: (-round(scores[i], 9), i))[:limit]
    return {"claim": claim_id, "candidates": [{"claim": i, "score": round(scores[i], 4), "matched_terms": sorted(bm.matched(i, q)), "text": by[i]["text"], "source_id": by[i]["source_id"],
                                              "source_revision": by[i]["source_revision"], "work_id": by[i]["work_id"]} for i in rows],
            "note": "found by words alone; a candidate is a claim to read, not a relation and not evidence of equivalence"}
