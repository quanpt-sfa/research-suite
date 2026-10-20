"""Shared Context Builder for modular research workflows.

Inspired by Deep Agents isolated contexts and progressive disclosure. What it guarantees (plan v3, Phase 2):

* **Scoping.** The research question fixes the scope. Without explicit focus claims only claims connected to that
  question are selected (directly, or through a Result / Analysis / Claim chain, at most two hops); a source or
  theory that two questions share never pulls the other question's claims in. Several research questions and no
  explicit one is an error, not a guess. Unknown explicit ids raise.
* **Table-driven expansion.** Which neighbours a workflow reads is data (`_EXPANSION`). Every edge between the
  included objects is recorded in `ContextPackage.relations`, so executors read relations instead of inferring them
  from wording. `supports` / `challenges` are not direction-enforced anywhere in the store, so a link in either
  direction between a claim and an object is treated as a link.
* **Enforced budget.** The budget covers the whole request: system prompt (skill + output contract), header and
  evidence. Material is admitted by tier (T0 must-have, T1 relevant, T2 background) until the budget is spent;
  anything left out is recorded with its tier and reason, and the request is trimmed until it fits. If a T0 item
  cannot fit the context is INSUFFICIENT, never silently "OK".
* **Evidence is rendered once** (an evidence passage appears in the excerpts, not also as an object line).
* **Large full texts are paged, not dropped.** A Source whose body does not fit stays in the context with its
  abstract and metadata, status `FULLTEXT_DEFERRED`, size, hash and detected sections; read it with
  `get_fulltext_page`. An abstract is never presented as full text.
* **Section-aligned comparison** across sources on request (`comparison_dimension`).

Deliberately not done here: deciding what any evidence *means*. No wording of a passage affects selection or
priority; only structure (edges, explicit structured flags) does.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Sequence

from research_harness.state.store import StateStore
from research_harness.workflows import registry
from research_harness.workflows.comparison import compare_sources
from research_harness.workflows.contracts import (
    ContextPackage,
    render_comparison_block,
    render_excerpt_block,
    render_fulltext_block,
    render_object_line,
)
from research_harness.workflows.fulltext import body_of, text_sha256
from research_harness.workflows.instrumentation import estimate_tokens
from research_harness.workflows.model_io import build_system_prompt
from research_harness.workflows.profiles.registry import get_profile
from research_harness.workflows.sections import detect_sections


class AmbiguousResearchQuestionError(ValueError):
    """Several research questions exist and none was named."""


# Neighbours read per workflow, one hop from the seeds (the research question and the claims in scope).
# `out`: targets of the seed's outgoing edges; `in`: sources of its incoming edges.
_EXPANSION: dict[str, dict[str, frozenset]] = {
    "contribution": {
        "out": frozenset({"Claim", "Theory", "Mechanism", "EvidencePassage", "Result", "ResearchQuestion", "Source"}),
        "in": frozenset({"Result", "Adjudication", "Claim", "EvidencePassage", "Theory", "Mechanism", "Source"}),
    },
    "literature": {
        "out": frozenset({"Source", "EvidencePassage", "Construct", "Theory", "Claim", "ResearchQuestion"}),
        "in": frozenset({"Source", "EvidencePassage", "Construct", "Theory", "Claim"}),
    },
    "methods": {
        "out": frozenset({"Construct", "Proxy", "Dataset", "Analysis", "Result", "ResearchQuestion"}),
        "in": frozenset({"Construct", "Proxy", "Dataset", "Analysis", "Result"}),
    },
    "results": {
        "out": frozenset({"Result", "Dataset", "Analysis", "Claim"}),
        "in": frozenset({"Result", "Analysis", "Dataset", "Claim"}),
    },
    "discussion": {
        "out": frozenset({"Result", "Claim", "EvidencePassage", "Mechanism", "Theory"}),
        "in": frozenset({"Result", "Adjudication", "Claim", "EvidencePassage", "Theory", "Mechanism"}),
    },
}

# T1 (relevant) object types per workflow. T0 is structural (see `_tier`); everything else is T2 (background).
_TIER1_TYPES: dict[str, frozenset] = {
    "contribution": frozenset({"EvidencePassage", "Result", "Source", "Mechanism", "Theory"}),
    "literature": frozenset({"Source", "EvidencePassage", "Construct", "Theory"}),
    "methods": frozenset({"Construct", "Proxy", "Dataset", "Analysis", "Result"}),
    "results": frozenset({"Result", "Dataset", "Analysis"}),
    "discussion": frozenset({"Result", "EvidencePassage", "Mechanism", "Theory"}),
}

# A claim belongs to a question's scope if it is linked to it directly or through one of these intermediates.
_SCOPE_CHAIN_TYPES = frozenset({"Result", "Analysis", "Claim"})
_SCOPE_MAX_HOPS = 2
# Relations from a claim whose targets are must-have context for assessing that claim.
_CLAIM_CORE_RELATIONS = frozenset({"supports", "challenges", "depends_on", "tests", "estimated_with", "derived_from"})
_TYPE_RANK = {t: i for i, t in enumerate(
    ["ResearchQuestion", "Claim", "Adjudication", "Result", "EvidencePassage", "Source", "Mechanism", "Theory",
     "Analysis", "Dataset", "Construct", "Proxy"])}
_MAX_SECTIONS_LISTED = 40
_SAFETY_MARGIN = 0.95  # comparison blocks are sized against 95% of the room left, for estimator drift


def _tokens(text: str) -> int:
    return estimate_tokens(text)[0]


@dataclass
class _Entry:
    oid: str
    rev: int
    otype: str
    is_head: bool
    obj: dict[str, Any]
    tier: int = 2
    excerpt: Optional[dict[str, Any]] = None
    ft: Optional[dict[str, Any]] = None  # full-text record (Sources, head revision)
    body: Optional[str] = None           # full text available to load (kept out of `obj["content"]`)
    cost: int = 0
    body_cost: int = 0
    included: bool = False

    @property
    def key(self) -> tuple[str, int]:
        return self.oid, self.rev

    def sort_key(self) -> tuple:
        return (self.tier, 0 if self.is_head else 1, _TYPE_RANK.get(self.otype, 99), self.oid, -self.rev)


def _claims_in_scope(conn: Any, rq_id: str, head_dict: dict[str, dict]) -> list[str]:
    """Claims linked to the research question, directly or through a Result/Analysis/Claim chain (<= 2 hops)."""
    adjacency: dict[str, set[str]] = defaultdict(set)
    for src, tgt in conn.execute("SELECT DISTINCT source_id, target_id FROM edges"):
        adjacency[src].add(tgt)
        adjacency[tgt].add(src)
    reached: set[str] = set()
    frontier = {rq_id}
    for hop in range(_SCOPE_MAX_HOPS):
        nxt: set[str] = set()
        for node in frontier:
            # Only the question itself and chain-type intermediates are walked through.
            if node != rq_id and head_dict.get(node, {}).get("object_type") not in _SCOPE_CHAIN_TYPES:
                continue
            for nb in adjacency.get(node, ()):
                if nb in head_dict and nb not in reached and nb != rq_id:
                    reached.add(nb)
                    nxt.add(nb)
        frontier = nxt
    return sorted(o for o in reached if head_dict[o]["object_type"] == "Claim")


def build_context(
    store: StateStore,
    workflow: str,
    *,
    research_question_id: Optional[str] = None,
    focus_claims: Optional[Sequence[str]] = None,
    max_input_tokens: int = 12000,
    fulltext_policy: Literal["on_demand", "always", "never"] = "on_demand",
    fulltext_sources: Optional[Sequence[str]] = None,
    history_policy: Literal["head_only", "relevant_changes_only", "full"] = "relevant_changes_only",
    discipline_profile: Optional[str] = None,
    comparison_dimension: Optional[str] = None,
    comparison_sources: Optional[Sequence[str]] = None,
) -> ContextPackage:
    """Build a selective, token-budgeted, deduplicated context package for a specific workflow."""
    canonical_wf = workflow.strip().lower()
    focus_claim_set: set[str] = set(focus_claims or [])
    fulltext_source_set: set[str] = set(fulltext_sources or [])

    # Progressive disclosure: only the selected workflow's skill is loaded.
    skill_instructions = registry.load_skill_instructions(canonical_wf)
    skill_digest = hashlib.sha256(skill_instructions.encode("utf-8")).hexdigest()  # of the text actually sent
    if canonical_wf not in _EXPANSION:
        raise KeyError(f"Unknown workflow '{workflow}'. Available: {sorted(_EXPANSION)}")
    spec = _EXPANSION[canonical_wf]

    entries: list[_Entry] = []
    relations: list[dict[str, Any]] = []
    contradictory_ids: set[str] = set()
    claim_ids_in_scope: list[str] = []
    with store.read() as conn:
        head_dict = {r[0]: {"object_type": r[1], "head_revision": r[2]}
                     for r in conn.execute("SELECT object_id, object_type, head_revision FROM object_heads")}

        # ---- 1. seeds: research question and claims in scope --------------------------------------------
        rq_ids = sorted(o for o, h in head_dict.items() if h["object_type"] == "ResearchQuestion")
        research_question_resolved: Optional[str] = research_question_id
        if research_question_id is not None:
            if research_question_id not in head_dict or head_dict[research_question_id]["object_type"] != "ResearchQuestion":
                raise KeyError(f"RESEARCH_QUESTION_NOT_FOUND: {research_question_id}")
            strategy = f"EXPLICIT_RESEARCH_QUESTION: {research_question_id}"
        elif len(rq_ids) == 1:
            research_question_resolved = rq_ids[0]
            strategy = f"PRIMARY_PROJECT_RESEARCH_QUESTION: {rq_ids[0]}"
        elif len(rq_ids) > 1:
            raise AmbiguousResearchQuestionError(
                f"AMBIGUOUS_RESEARCH_QUESTION: the project has {len(rq_ids)} research questions {rq_ids}; "
                f"name one with research_question_id")
        else:
            strategy = "NO_RESEARCH_QUESTION_FOUND"

        all_claims = sorted(o for o, h in head_dict.items() if h["object_type"] == "Claim")
        if focus_claim_set:
            for cid in sorted(focus_claim_set):
                if cid not in head_dict or head_dict[cid]["object_type"] != "Claim":
                    raise KeyError(f"CLAIM_NOT_FOUND: {cid}")
            claim_ids_in_scope = sorted(focus_claim_set)
            strategy += "; CLAIMS: EXPLICIT_FOCUS"
        elif research_question_resolved is not None:
            claim_ids_in_scope = _claims_in_scope(conn, research_question_resolved, head_dict)
            strategy += f"; CLAIMS: RQ_SCOPED ({len(claim_ids_in_scope)} of {len(all_claims)} project claims)"
        else:
            claim_ids_in_scope = all_claims
            strategy += "; CLAIMS: ALL_PROJECT_CLAIMS"

        seeds: list[str] = ([research_question_resolved] if research_question_resolved else []) + claim_ids_in_scope

        # ---- 2. expansion by the workflow's table ------------------------------------------------------
        expanded: set[str] = set(seeds)
        for oid in seeds:
            rev = head_dict[oid]["head_revision"]
            for e in store.edges_from(conn, oid, rev):
                if head_dict.get(e["target_id"], {}).get("object_type") in spec["out"]:
                    expanded.add(e["target_id"])
            for e in store.edges_to(conn, oid, rev):
                if head_dict.get(e["source_id"], {}).get("object_type") in spec["in"]:
                    expanded.add(e["source_id"])
        # Passages derived from an included source are part of reading that source.
        for oid in sorted(expanded):
            if head_dict.get(oid, {}).get("object_type") == "Source":
                for e in store.edges_to(conn, oid, head_dict[oid]["head_revision"]):
                    if head_dict.get(e["source_id"], {}).get("object_type") == "EvidencePassage":
                        expanded.add(e["source_id"])
        # Adjudications about the claims in scope.
        scope_set = set(claim_ids_in_scope)
        for oid, h in head_dict.items():
            if h["object_type"] == "Adjudication":
                subject = (store.get_object(oid, conn=conn) or {}).get("content", {}).get("subject", {})
                if subject.get("object_id") in scope_set:
                    expanded.add(oid)

        # ---- 3. revisions to read, entries ---------------------------------------------------------------
        for oid in sorted(expanded):
            head_rev = head_dict[oid]["head_revision"]
            otype = head_dict[oid]["object_type"]
            revisions = [head_rev]
            if history_policy == "full":
                revisions = list(range(1, head_rev + 1))
            elif history_policy == "relevant_changes_only" and otype in {"Result", "Claim"} and head_rev > 1:
                revisions = [head_rev - 1, head_rev]
            for r in revisions:
                obj = store.get_object(oid, revision=r, conn=conn)
                if not obj:
                    continue
                original = obj.get("content", {}) or {}
                content = dict(original)
                entry = _Entry(oid=oid, rev=r, otype=otype, is_head=(r == head_rev), obj={})
                if otype == "Source":
                    content.pop("full_text", None)
                    content.pop("fulltext", None)
                    if entry.is_head:
                        entry.ft, entry.body = _fulltext_record(store, oid, original, content, fulltext_policy,
                                                                fulltext_source_set)
                entry.obj = {"object_id": oid, "object_type": otype, "revision": r,
                             "content_digest": obj.get("content_digest"), "content": content,
                             "lifecycle_status": obj.get("lifecycle_status", "ACTIVE")}
                if otype == "EvidencePassage":
                    entry.excerpt = _excerpt(store, conn, oid, r, content)
                if _structured_contradiction_flag(content):
                    contradictory_ids.add(oid)
                entries.append(entry)
                # edges of this revision towards other objects of the package
                for e in store.edges_from(conn, oid, r):
                    if e["target_id"] in expanded:
                        relations.append({"source_id": oid, "source_revision": r, "relation": e.get("relation"),
                                          "target_id": e["target_id"], "target_revision": e.get("target_revision")})

    relations = _dedupe_relations(relations)
    # A `challenges` link between a claim in scope and another object marks that object as challenge-linked
    # (either direction: the relation's direction is a convention, not enforced anywhere).
    for rel in relations:
        if rel["relation"] == "challenges":
            if rel["source_id"] in scope_set and rel["target_id"] not in scope_set:
                contradictory_ids.add(rel["target_id"])
            elif rel["target_id"] in scope_set and rel["source_id"] not in scope_set:
                contradictory_ids.add(rel["source_id"])

    # ---- 4. tiers -------------------------------------------------------------------------------------
    core_targets = {r["target_id"] for r in relations
                    if r["source_id"] in scope_set and r["relation"] in _CLAIM_CORE_RELATIONS}
    core_targets |= {r["source_id"] for r in relations
                     if r["target_id"] in scope_set and r["relation"] in {"supports", "challenges"}}
    adjudication_ids = {e.oid for e in entries if e.otype == "Adjudication"}
    t0_ids = ({research_question_resolved} if research_question_resolved else set()) | scope_set | core_targets \
        | contradictory_ids | adjudication_ids
    t0_ids = {o for o in t0_ids if o}
    # The source of a must-have passage is must-have (metadata and abstract only; bodies are paged separately).
    passage_sources = {r["target_id"] for r in relations
                       if r["relation"] == "derived_from" and r["source_id"] in t0_ids}
    t0_ids |= passage_sources
    tier1_types = _TIER1_TYPES[canonical_wf]
    for e in entries:
        e.tier = 0 if e.oid in t0_ids else (1 if e.otype in tier1_types else 2)
    entries.sort(key=_Entry.sort_key)

    # ---- 5. profile (recorded in the manifest; not rendered into the prompt) ---------------------------
    profile_data: Optional[dict[str, Any]] = None
    if discipline_profile:
        try:
            prof = get_profile(discipline_profile)
            profile_data = {"profile_id": prof.identifier, "title": prof.title, "traditions": prof.research_traditions,
                            "methodological_checklist": prof.methodological_checklist,
                            "validity_questions": prof.validity_questions}
        except KeyError:
            pass

    # ---- 6. budget -------------------------------------------------------------------------------------
    system_prompt = build_system_prompt(canonical_wf, skill_instructions)
    skeleton = _package(store, canonical_wf, rq=research_question_resolved, focus=sorted(focus_claim_set),
                        strategy=strategy, skill_instructions=skill_instructions, skill_digest=skill_digest,
                        profile=profile_data, budget=max_input_tokens)
    reserved = _tokens(system_prompt) + _tokens(skeleton.formatted_user_prompt)
    used = reserved

    for e in entries:
        e.cost = _tokens(render_excerpt_block(e.excerpt) if e.excerpt is not None else render_object_line(e.obj))
    omitted: list[dict[str, Any]] = []
    for e in entries:
        anchored = e.otype == "ResearchQuestion" and e.is_head
        if anchored or used + e.cost <= max_input_tokens:
            e.included, used = True, used + e.cost
        else:
            omitted.append(_omission(e, "HIGH_PRIORITY_BUDGET_EXCEEDED" if e.tier == 0 else "TOKEN_BUDGET_EXCEEDED"))

    # section-aligned comparison (explicitly requested, so it is read before optional full-text bodies)
    comparisons: list[dict[str, Any]] = []
    if comparison_dimension:
        sources = list(comparison_sources) if comparison_sources else [
            e.oid for e in entries if e.included and e.otype == "Source" and e.is_head]
        room = int((max_input_tokens - used) * _SAFETY_MARGIN) - 60 * len(sources)
        if sources:
            with store.read() as conn:
                comparisons = compare_sources(store, sources, comparison_dimension, budget_tokens=max(0, room), conn=conn)
            used += _tokens(render_comparison_block(comparisons))

    # full-text bodies: loaded in tier order while they fit, otherwise deferred (never drop the source)
    deferred: list[dict[str, Any]] = []
    for e in entries:
        if not (e.included and e.ft is not None and e.ft["fulltext_status"] == "FULLTEXT_LOADED"):
            continue
        loaded = dict(e.ft, full_text=e.body)
        e.body_cost = _tokens(render_fulltext_block(loaded))
        if used + e.body_cost <= max_input_tokens:
            e.ft, used = loaded, used + e.body_cost
        else:
            e.ft = _defer(e.ft, e.body)
            deferred.append({"object_id": e.oid, "chars": len(e.body or ""), "tokens_estimate": e.body_cost})

    package, request_tokens = _assemble_and_trim(
        store, canonical_wf, rq=research_question_resolved, focus=sorted(focus_claim_set), strategy=strategy,
        entries=entries, relations=relations, comparisons=comparisons, skill_instructions=skill_instructions,
        skill_digest=skill_digest, profile=profile_data, budget=max_input_tokens, system_prompt=system_prompt,
        omitted=omitted, deferred=deferred)

    coverage = _coverage(package, omitted, deferred, fulltext_policy, fulltext_source_set, request_tokens, max_input_tokens)
    package.coverage = coverage
    package.context_status = {"FULL": "COMPLETE", "PARTIAL_T2": "PARTIAL", "PARTIAL_T1": "PARTIAL",
                              "INSUFFICIENT": "INSUFFICIENT"}[coverage]
    package.budget_status = "INCOMPLETE_EXCEEDED" if coverage in {"INSUFFICIENT", "PARTIAL_T1"} else "OK"
    # Final figure, for the package exactly as returned (never larger than the measurement above).
    request_tokens = _tokens(system_prompt) + _tokens(package.formatted_user_prompt)
    package.omitted_items = omitted
    package.continuation_plan = _continuation_plan(omitted, deferred, entries, request_tokens, coverage)
    package.contradictory_evidence_count = len([e for e in entries if e.included and e.oid in contradictory_ids])
    package.manifest = _manifest(package, entries, omitted, deferred, contradictory_ids, max_input_tokens, request_tokens,
                                 reserved, profile_data, system_prompt)
    package.scope_claims = list(claim_ids_in_scope)
    package.estimated_tokens = request_tokens
    package.context_bytes = len((system_prompt + "\n" + package.formatted_user_prompt).encode("utf-8"))
    package.manifest["context_bytes"] = package.context_bytes
    package.is_token_estimate = estimate_tokens("x")[1]
    return package


# ---------------------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------------------

def _structured_contradiction_flag(content: dict[str, Any]) -> bool:
    """Explicit, structured author flags only. Wording of any text field is never inspected."""
    if content.get("contradictory") is True:
        return True
    assessment = content.get("assessment")
    return isinstance(assessment, dict) and assessment.get("relation") == "challenges"


def _dedupe_relations(relations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for r in relations:
        key = (r["source_id"], r["source_revision"], r["relation"], r["target_id"], r["target_revision"])
        if key not in seen:
            seen.add(key)
            out.append(r)
    out.sort(key=lambda r: (r["source_id"], r["source_revision"], r["relation"], r["target_id"], r["target_revision"] or 0))
    return out


def _excerpt(store: StateStore, conn: Any, oid: str, rev: int, content: dict[str, Any]) -> dict[str, Any]:
    src_id = content.get("source_id")
    if not src_id:
        for e in store.edges_from(conn, oid, rev):
            if e.get("relation") in ("derived_from", "depends_on", "measures", "cited_by"):
                target = store.get_object(e["target_id"], revision=e.get("target_revision"), conn=conn)
                if target and target.get("object_type") == "Source":
                    src_id = e["target_id"]
                    break
    shown = {"locator", "quote", "excerpt", "normalized_claim", "source_id"}
    other = {k: v for k, v in content.items() if k not in shown}
    return {
        "passage_id": oid, "revision": rev,
        "locator": content.get("locator", "") or "UNSPECIFIED",
        "normalized_claim": content.get("normalized_claim", ""),
        "excerpt": content.get("excerpt") or content.get("quote") or content.get("normalized_claim", ""),
        "quote": content.get("quote") or content.get("excerpt") or content.get("normalized_claim", ""),
        "source_id": src_id or "UNKNOWN",
        "other_fields": other,
    }


def _fulltext_record(store: StateStore, oid: str, original: dict[str, Any], content: dict[str, Any], policy: str,
                     requested: set[str]) -> tuple[dict[str, Any], Optional[str]]:
    body, body_status = body_of(store, original)
    wanted = policy == "always" or (policy == "on_demand" and oid in requested)
    abstract = content.get("abstract")
    if body is not None:
        status = "FULLTEXT_LOADED" if wanted else "FULLTEXT_AVAILABLE"
    elif body_status == "RETRIEVAL_FAILED":
        status = "RETRIEVAL_FAILED"
    else:
        status = "ABSTRACT_ONLY" if abstract else "FULLTEXT_MISSING"
    record = {
        "object_id": oid, "source_id": oid, "citation_key": content.get("citation_key", oid),
        "fulltext_status": status, "full_text": None, "abstract": abstract, "source_policy": policy,
        "is_abstract_fallback": False,
    }
    if body is not None:
        record["chars"], record["sha256"] = len(body), text_sha256(body)
    return record, body


def _defer(ft: dict[str, Any], body: Optional[str]) -> dict[str, Any]:
    sections = [s.to_dict() for s in detect_sections(body or "")][:_MAX_SECTIONS_LISTED]
    return dict(ft, fulltext_status="FULLTEXT_DEFERRED", full_text=None, sections=sections)


def _omission(e: _Entry, reason: str) -> dict[str, Any]:
    return {"object_id": e.oid, "revision": e.rev, "reason": reason, "type": e.otype, "tier": e.tier}


def _package(store: StateStore, workflow: str, *, rq: Optional[str], focus: list[str], strategy: str,
             skill_instructions: str, skill_digest: str, profile: Optional[dict], budget: int,
             objects: Sequence[dict] = (), excerpts: Sequence[dict] = (), full_texts: Sequence[dict] = (),
             relations: Sequence[dict] = (), comparisons: Sequence[dict] = ()) -> ContextPackage:
    return ContextPackage(
        workflow=workflow, project_id=store.project_id, research_question_id=rq, focus_claims=focus,
        objects=list(objects), excerpts=list(excerpts), full_texts=list(full_texts), omitted_items=[],
        continuation_plan=None, contradictory_evidence_count=0, context_bytes=0, estimated_tokens=0,
        is_token_estimate=True, manifest={"budget_limit": budget, "profile": profile}, budget_status="OK",
        skill_instructions=skill_instructions, skill_digest=skill_digest, selection_strategy=strategy,
        relations=list(relations), comparisons=list(comparisons))


def _materialize(store: StateStore, workflow: str, *, rq, focus, strategy, entries, relations, comparisons,
                 skill_instructions, skill_digest, profile, budget) -> ContextPackage:
    kept = [e for e in entries if e.included]
    ids = {e.oid for e in kept}
    return _package(
        store, workflow, rq=rq, focus=focus, strategy=strategy, skill_instructions=skill_instructions,
        skill_digest=skill_digest, profile=profile, budget=budget,
        objects=[e.obj for e in kept], excerpts=[e.excerpt for e in kept if e.excerpt is not None],
        full_texts=[e.ft for e in kept if e.ft is not None],
        relations=[r for r in relations if r["source_id"] in ids and r["target_id"] in ids], comparisons=comparisons)


def _assemble_and_trim(store: StateStore, workflow: str, *, rq, focus, strategy, entries, relations, comparisons,
                       skill_instructions, skill_digest, profile, budget, system_prompt, omitted, deferred):
    """Assemble the package and shrink it until the whole request fits (I4). Order of sacrifice: loaded bodies of
    the least important sources (they become paged), then included entries from the lowest tier up. T0 is never
    trimmed; if it does not fit, the caller marks the context INSUFFICIENT."""
    def measure() -> tuple[ContextPackage, int]:
        pkg = _materialize(store, workflow, rq=rq, focus=focus, strategy=strategy, entries=entries,
                           relations=relations, comparisons=comparisons, skill_instructions=skill_instructions,
                           skill_digest=skill_digest, profile=profile, budget=budget)
        # The "budget exceeded" warning is part of the header whenever the package is incomplete. Measure with it
        # whenever anything was left out or deferred (a superset of the cases that show it), so the final request
        # can never be larger than what was measured here.
        if omitted or deferred:
            pkg.budget_status = "INCOMPLETE_EXCEEDED"
        return pkg, _tokens(system_prompt) + _tokens(pkg.formatted_user_prompt)

    pkg, total = measure()
    while total > budget:
        loaded = [e for e in entries if e.included and e.ft is not None and e.ft["fulltext_status"] == "FULLTEXT_LOADED"]
        if loaded:
            victim = sorted(loaded, key=_Entry.sort_key)[-1]
            victim.ft = _defer(victim.ft, victim.body)
            deferred.append({"object_id": victim.oid, "chars": len(victim.body or ""), "tokens_estimate": victim.body_cost})
        else:
            droppable = [e for e in entries if e.included and e.tier > 0]
            if not droppable:
                break
            victim = sorted(droppable, key=_Entry.sort_key)[-1]
            victim.included = False
            omitted.append(_omission(victim, "FINAL_BUDGET_TRIM"))
        pkg, total = measure()
    return pkg, total


def _coverage(pkg: ContextPackage, omitted: list[dict], deferred: list[dict], policy: str, requested: set[str],
              request_tokens: int, budget: int) -> str:
    if request_tokens > budget or any(o["tier"] == 0 for o in omitted):
        return "INSUFFICIENT"
    asked = {d["object_id"] for d in deferred if policy == "always" or d["object_id"] in requested}
    if any(o["tier"] == 1 for o in omitted) or asked:
        return "PARTIAL_T1"
    if any(o["tier"] == 2 for o in omitted):
        return "PARTIAL_T2"
    return "FULL"


def _continuation_plan(omitted: list[dict], deferred: list[dict], entries: list[_Entry], request_tokens: int,
                       coverage: str) -> Optional[dict[str, Any]]:
    if not omitted and not deferred and coverage == "FULL":
        return None
    cost = {e.key: e.cost for e in entries}
    missing = sum(cost.get((o["object_id"], o["revision"]), 0) for o in omitted) + sum(d["tokens_estimate"] for d in deferred)
    return {
        "status": "INCOMPLETE_CONTEXT",
        "coverage": coverage,
        "omitted_count": len(omitted),
        "omitted_objects_count": len(omitted),
        "estimated_missing_tokens": missing,
        "recommended_budget": request_tokens + missing + 500,
        "recommendation": "Increase the context budget, narrow the focus claims, or read deferred full texts in pages.",
        "omitted_object_ids": [o["object_id"] for o in omitted],
        "deferred_fulltexts": [d["object_id"] for d in deferred],
        "retrieval_plan": "Re-run with explicit focal object ids; use get_fulltext_page(source_id, offset, limit) for deferred texts.",
    }


def _manifest(pkg: ContextPackage, entries: list[_Entry], omitted: list[dict], deferred: list[dict],
              contradictory_ids: set[str], budget: int, request_tokens: int, reserved: int,
              profile: Optional[dict], system_prompt: str) -> dict[str, Any]:
    by_type: dict[str, int] = {}
    for o in pkg.objects:
        by_type[o["object_type"]] = by_type.get(o["object_type"], 0) + 1
    return {
        "workflow": pkg.workflow,
        "included_objects_count": len(pkg.objects),
        "included_by_type": by_type,
        "omitted_objects_count": len(omitted),
        "contradictory_evidence_count": len([e for e in entries if e.included and e.oid in contradictory_ids]),
        "deduped_excerpts_count": len(pkg.excerpts),
        "fulltext_expanded_count": len([f for f in pkg.full_texts if f.get("fulltext_status") == "FULLTEXT_LOADED"]),
        "fulltext_deferred_count": len(deferred),
        "relations_count": len(pkg.relations),
        "budget_limit": budget,
        "budget_status": pkg.budget_status,
        "coverage": pkg.coverage,
        "context_status": pkg.context_status,
        "reserved_tokens": reserved,
        "system_prompt_tokens": _tokens(system_prompt),
        "request_tokens": request_tokens,
        "context_bytes": 0,
        "estimated_tokens": request_tokens,
        "is_estimate": estimate_tokens("x")[1],
        "token_count_basis": "heuristic" if estimate_tokens("x")[1] else "tiktoken",
        "profile": profile,
    }
