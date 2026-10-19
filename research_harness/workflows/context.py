"""Shared Context Builder for modular research workflows.

Inspired by Deep Agents isolated contexts and progressive disclosure:
- Selects only scientific objects relevant to the active workflow task.
- Expands graph relationships selectively via DAG edges.
- Deduplicates repeated evidence across claims.
- Excerpts evidence by default; supports on-demand full-text expansion.
- Enforces configurable token budgets without silently dropping contradictory evidence.
- Flags incomplete context when essential evidence exceeds the budget.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Literal, Optional, Sequence, Set, Tuple

from research_harness.state.store import StateStore
from research_harness.workflows.contracts import ContextPackage, TokenBudget
from research_harness.workflows.instrumentation import estimate_tokens
from research_harness.workflows.profiles.registry import get_profile


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
) -> ContextPackage:
    """Build a selective, token-budgeted, deduplicated context package for a specific workflow."""
    canonical_wf = workflow.strip().lower()
    focus_claim_set: set[str] = set(focus_claims or [])
    fulltext_source_set: set[str] = set(fulltext_sources or [])

    with store.read() as conn:
        all_heads = conn.execute("SELECT object_id, object_type, head_revision FROM object_heads").fetchall()
        head_dict = {r[0]: {"object_type": r[1], "head_revision": r[2]} for r in all_heads}

        # 1. Identify relevant candidate objects based on workflow and focus
        selected_ids: set[str] = set()

        # Research Question
        if research_question_id and research_question_id in head_dict:
            selected_ids.add(research_question_id)
        else:
            # Add primary RQ if none specified
            for oid, h in head_dict.items():
                if h["object_type"] == "ResearchQuestion":
                    selected_ids.add(oid)
                    if not research_question_id:
                        research_question_id = oid
                    break

        # Focus Claims or relevant claims
        if focus_claim_set:
            selected_ids.update(focus_claim_set.intersection(head_dict.keys()))
        else:
            for oid, h in head_dict.items():
                if h["object_type"] == "Claim":
                    selected_ids.add(oid)

        # 2. Selective Graph Expansion based on Workflow Type
        expanded_ids: set[str] = set(selected_ids)
        for oid in list(selected_ids):
            rev = head_dict[oid]["head_revision"]
            edges_out = store.edges_from(conn, oid, rev)
            for e in edges_out:
                target = e["target_id"]
                if target in head_dict:
                    t_type = head_dict[target]["object_type"]
                    if canonical_wf == "contribution":
                        if t_type in {"Claim", "Theory", "Mechanism", "EvidencePassage", "Result", "ResearchQuestion"}:
                            expanded_ids.add(target)
                    elif canonical_wf == "literature":
                        if t_type in {"Source", "EvidencePassage", "Construct", "Theory", "Claim", "ResearchQuestion"}:
                            expanded_ids.add(target)
                    elif canonical_wf == "methods":
                        if t_type in {"Construct", "Proxy", "Dataset", "Analysis", "Result", "ResearchQuestion"}:
                            expanded_ids.add(target)
                    elif canonical_wf == "results":
                        if t_type in {"Result", "Dataset", "Analysis", "Claim"}:
                            expanded_ids.add(target)
                    elif canonical_wf == "discussion":
                        if t_type in {"Result", "Claim", "EvidencePassage", "Mechanism", "Theory"}:
                            expanded_ids.add(target)

            # Also check edges pointing in
            edges_in = store.edges_to(conn, oid, rev)
            for e in edges_in:
                src = e["source_id"]
                if src in head_dict:
                    s_type = head_dict[src]["object_type"]
                    if canonical_wf == "literature":
                        if s_type in {"Source", "EvidencePassage", "Construct", "Theory", "Claim"}:
                            expanded_ids.add(src)
                    elif canonical_wf in {"contribution", "discussion"}:
                        if s_type in {"Result", "Adjudication", "Claim", "EvidencePassage", "Theory", "Mechanism"}:
                            expanded_ids.add(src)
                    elif canonical_wf == "methods":
                        if s_type in {"Construct", "Proxy", "Dataset", "Analysis", "Result"}:
                            expanded_ids.add(src)
                    elif canonical_wf == "results":
                        if s_type in {"Result", "Analysis", "Dataset", "Claim"}:
                            expanded_ids.add(src)

        # For literature and contribution: expand evidence passages derived from sources in expanded_ids
        for oid in list(expanded_ids):
            if head_dict.get(oid, {}).get("object_type") == "Source":
                for e in store.edges_to(conn, oid, head_dict[oid]["head_revision"]):
                    src = e["source_id"]
                    if head_dict.get(src, {}).get("object_type") == "EvidencePassage":
                        expanded_ids.add(src)

        # Always include active Adjudications for focus claims
        for cid in focus_claim_set:
            if cid in head_dict:
                for oid, h in head_dict.items():
                    if h["object_type"] == "Adjudication":
                        adj_obj = store.get_object(oid, conn=conn)
                        subj = adj_obj.get("content", {}).get("subject", {})
                        if subj.get("object_id") == cid:
                            expanded_ids.add(oid)

        # 3. Retrieve raw object revisions according to history_policy
        raw_objects: list[dict[str, Any]] = []
        contradictory_items: list[dict[str, Any]] = []
        excerpts_map: dict[Tuple[str, int], dict[str, Any]] = {}
        full_texts_map: dict[str, dict[str, Any]] = {}

        for oid in sorted(expanded_ids):
            h = head_dict[oid]
            head_rev = h["head_revision"]
            otype = h["object_type"]

            revisions_to_fetch = [head_rev]
            if history_policy == "full":
                revisions_to_fetch = list(range(1, head_rev + 1))
            elif history_policy == "relevant_changes_only" and otype in {"Result", "Claim"} and head_rev > 1:
                # include immediate predecessor for change detection
                revisions_to_fetch = [head_rev - 1, head_rev]

            for r in revisions_to_fetch:
                obj = store.get_object(oid, revision=r, conn=conn)
                if not obj:
                    continue

                content = dict(obj.get("content", {}))

                # Handle on-demand full text for sources (Context Offloading)
                if otype == "Source":
                    should_fetch_fulltext = False
                    if fulltext_policy == "always":
                        should_fetch_fulltext = True
                    elif fulltext_policy == "on_demand" and (oid in fulltext_source_set):
                        should_fetch_fulltext = True

                    fulltext_raw = content.pop("full_text", None) or content.pop("fulltext", None)
                    if should_fetch_fulltext and oid not in full_texts_map:
                        fulltext_str = fulltext_raw or content.get("abstract") or content.get("bibliographic_identity", "")
                        artifact_sha = content.get("artifact_sha256")
                        if artifact_sha and store.artifacts.has(artifact_sha):
                            try:
                                fulltext_str = store.artifacts.get_bytes(artifact_sha).decode("utf-8", errors="replace")
                            except Exception:
                                pass
                        full_texts_map[oid] = {
                            "object_id": oid,
                            "source_id": oid,
                            "citation_key": content.get("citation_key", oid),
                            "full_text": fulltext_str,
                            "source_policy": fulltext_policy,
                        }

                obj_entry = {
                    "object_id": oid,
                    "object_type": otype,
                    "revision": r,
                    "content_digest": obj.get("content_digest"),
                    "content": content,
                    "lifecycle_status": obj.get("lifecycle_status", "ACTIVE"),
                }

                # Check for contradictory flags in content / text
                is_contradictory = False
                text_repr = json.dumps(content).lower()
                if "contradict" in text_repr or "not_supported" in text_repr or "refute" in text_repr:
                    is_contradictory = True
                    contradictory_items.append(obj_entry)

                raw_objects.append(obj_entry)

                # Extract deduplicated evidence passages
                if otype == "EvidencePassage":
                    passage_key = (oid, r)
                    if passage_key not in excerpts_map:
                        src_id = content.get("source_id")
                        if not src_id:
                            rev_edges = store.edges_from(conn, oid, r)
                            for e in rev_edges:
                                rel = e.get("relation")
                                to_id = e.get("target_id")
                                to_rev = e.get("target_revision")
                                if rel in ("derived_from", "depends_on", "measures", "cited_by"):
                                    to_meta = store.get_object(to_id, revision=to_rev, conn=conn)
                                    if to_meta and to_meta.get("object_type") == "Source":
                                        src_id = to_id
                                        break
                        excerpts_map[passage_key] = {
                            "passage_id": oid,
                            "revision": r,
                            "locator": content.get("locator", ""),
                            "normalized_claim": content.get("normalized_claim", ""),
                            "excerpt": content.get("excerpt") or content.get("quote") or content.get("normalized_claim", ""),
                            "source_id": src_id,
                        }

    # 4. Integrate Discipline Profile if specified
    profile_data: Optional[dict[str, Any]] = None
    if discipline_profile:
        try:
            prof = get_profile(discipline_profile)
            profile_data = {
                "profile_id": prof.identifier,
                "title": prof.title,
                "traditions": prof.research_traditions,
                "methodological_checklist": prof.methodological_checklist,
                "validity_questions": prof.validity_questions,
            }
        except KeyError:
            pass

    # 5. Prioritization & Budget Enforcement
    # Priority order:
    # High: ResearchQuestion, Focus Claims, Contradictory Evidence, Discipline Profile Checklist
    # Medium: Supporting Evidence Passages, Results, Mechanisms, Constructs
    # Low: Background Sources, Prior Revisions
    high_priority: list[dict[str, Any]] = []
    medium_priority: list[dict[str, Any]] = []
    low_priority: list[dict[str, Any]] = []

    for obj in raw_objects:
        oid = obj["object_id"]
        otype = obj["object_type"]
        is_contra = any(c["object_id"] == oid and c["revision"] == obj["revision"] for c in contradictory_items)

        if otype == "ResearchQuestion" or oid in focus_claim_set or is_contra:
            high_priority.append(obj)
        elif otype in {"Claim", "EvidencePassage", "Result", "Mechanism", "Construct", "Proxy"}:
            medium_priority.append(obj)
        else:
            low_priority.append(obj)

    final_objects: list[dict[str, Any]] = []
    omitted_items: list[dict[str, Any]] = []
    budget_status: Literal["OK", "INCOMPLETE_EXCEEDED"] = "OK"
    continuation_plan: Optional[dict[str, Any]] = None

    # Track budget incrementally
    running_token_estimate = 0
    if profile_data:
        prof_tokens, _ = estimate_tokens(json.dumps(profile_data))
        running_token_estimate += prof_tokens

    # Include High Priority (ALWAYS included; if high priority alone exceeds budget, flag incomplete context)
    for obj in high_priority:
        obj_tokens, _ = estimate_tokens(json.dumps(obj))
        if running_token_estimate + obj_tokens > max_input_tokens:
            budget_status = "INCOMPLETE_EXCEEDED"
            omitted_items.append({"object_id": obj["object_id"], "revision": obj["revision"], "reason": "HIGH_PRIORITY_BUDGET_EXCEEDED", "type": obj["object_type"]})
        else:
            running_token_estimate += obj_tokens
            final_objects.append(obj)

    # Include Medium Priority if space permits
    for obj in medium_priority:
        obj_tokens, _ = estimate_tokens(json.dumps(obj))
        if running_token_estimate + obj_tokens > max_input_tokens:
            budget_status = "INCOMPLETE_EXCEEDED"
            omitted_items.append({"object_id": obj["object_id"], "revision": obj["revision"], "reason": "TOKEN_BUDGET_EXCEEDED", "type": obj["object_type"]})
        else:
            running_token_estimate += obj_tokens
            final_objects.append(obj)

    # Include Low Priority if space permits
    for obj in low_priority:
        obj_tokens, _ = estimate_tokens(json.dumps(obj))
        if running_token_estimate + obj_tokens > max_input_tokens:
            omitted_items.append({"object_id": obj["object_id"], "revision": obj["revision"], "reason": "TOKEN_BUDGET_EXCEEDED", "type": obj["object_type"]})
        else:
            running_token_estimate += obj_tokens
            final_objects.append(obj)

    # If items were omitted, provide explicit continuation / retrieval plan
    if omitted_items:
        omitted_tokens = sum(estimate_tokens(json.dumps(item))[0] for item in omitted_items)
        continuation_plan = {
            "status": "INCOMPLETE_CONTEXT",
            "omitted_count": len(omitted_items),
            "omitted_objects_count": len(omitted_items),
            "estimated_missing_tokens": omitted_tokens,
            "recommended_budget": running_token_estimate + omitted_tokens + 500,
            "recommendation": "Increase context budget or split workflow evaluation across specific focal claim subsets.",
            "omitted_object_ids": [x["object_id"] for x in omitted_items],
            "retrieval_plan": "Execute incremental retrieval for omitted objects using explicit focal object IDs.",
        }

    # Filter excerpts to those belonging to retained objects
    retained_ids = {o["object_id"] for o in final_objects}
    deduped_excerpts = [v for k, v in excerpts_map.items() if k[0] in retained_ids]
    deduped_full_texts = [v for k, v in full_texts_map.items() if k in retained_ids]

    # Calculate overall context size and tokens
    context_dict = {
        "workflow": canonical_wf,
        "project_id": store.project_id,
        "research_question_id": research_question_id,
        "focus_claims": list(focus_claim_set),
        "profile": profile_data,
        "objects": final_objects,
        "excerpts": deduped_excerpts,
        "full_texts": deduped_full_texts,
    }
    raw_json = json.dumps(context_dict, sort_keys=True, ensure_ascii=False)
    context_bytes = len(raw_json.encode("utf-8"))
    total_tokens, is_estimate = estimate_tokens(raw_json)

    manifest = {
        "workflow": canonical_wf,
        "included_objects_count": len(final_objects),
        "included_by_type": {},
        "omitted_objects_count": len(omitted_items),
        "contradictory_evidence_count": len(contradictory_items),
        "deduped_excerpts_count": len(deduped_excerpts),
        "fulltext_expanded_count": len(deduped_full_texts),
        "budget_limit": max_input_tokens,
        "budget_status": budget_status,
        "context_bytes": context_bytes,
        "estimated_tokens": total_tokens,
        "is_estimate": is_estimate,
        "profile": profile_data,
    }
    for o in final_objects:
        t = o["object_type"]
        manifest["included_by_type"][t] = manifest["included_by_type"].get(t, 0) + 1

    return ContextPackage(
        workflow=canonical_wf,
        project_id=store.project_id,
        research_question_id=research_question_id,
        focus_claims=list(focus_claim_set),
        objects=final_objects,
        excerpts=deduped_excerpts,
        full_texts=deduped_full_texts,
        omitted_items=omitted_items,
        continuation_plan=continuation_plan,
        contradictory_evidence_count=len(contradictory_items),
        context_bytes=context_bytes,
        estimated_tokens=total_tokens,
        is_token_estimate=is_estimate,
        manifest=manifest,
        budget_status=budget_status,
    )
