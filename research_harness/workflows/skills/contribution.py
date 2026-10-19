"""Workflow 1 — Contribution Finding Implementation.

Identifies, challenges, and refines potential scientific contributions against prior literature
and empirical findings, preserving exact provenance and evidence bindings.
Follows strict scientific non-fabrication rules: claim tiers indicate assertion types,
never established causal identification or verified novelty without evidence.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional

from research_harness.workflows.contracts import (
    ContextPackage,
    ContributionFindingResult,
    TaskUsage,
    WorkflowOutput,
)
from research_harness.workflows.instrumentation import estimate_tokens


def execute_contribution_workflow(
    context: ContextPackage,
    *,
    task_id: Optional[str] = None,
    contribution_hypotheses: Optional[list[str]] = None,
    upstream_artifacts: Optional[list[Any]] = None,
    **kwargs: Any,
) -> WorkflowOutput:
    """Execute Contribution Finding workflow deterministically over structured research context."""
    start_time = time.time()
    tid = task_id or f"TASK_CONTRIB_{uuid.uuid4().hex[:8].upper()}"

    # Extract relevant entities from context package
    rq_objs = [o for o in context.objects if o["object_type"] == "ResearchQuestion"]
    rq_text = rq_objs[0]["content"].get("text", "") if rq_objs else "Unspecified research inquiry"

    claims = [o for o in context.objects if o["object_type"] == "Claim"]
    theories = [o for o in context.objects if o["object_type"] == "Theory"]
    mechanisms = [o for o in context.objects if o["object_type"] == "Mechanism"]
    results = [o for o in context.objects if o["object_type"] == "Result"]
    adjudications = [o for o in context.objects if o["object_type"] == "Adjudication"]

    # Consume upstream artifacts if provided (e.g. Discussion or Results)
    upstream = upstream_artifacts or kwargs.get("upstream_artifacts") or []
    discussion_tensions: list[str] = []
    consumed_artifact_ids: list[str] = []
    for art in upstream:
        art_dict = art.to_dict() if hasattr(art, "to_dict") else (art if isinstance(art, dict) else {})
        art_res = art_dict.get("result", {})
        art_wf = art_dict.get("workflow")
        if art_dict.get("task_id"):
            consumed_artifact_ids.append(art_dict["task_id"])
        if art_wf == "discussion":
            unresolved = art_res.get("contradictions") or art_res.get("unresolved_disagreements") or []
            for item in unresolved:
                finding = item.get("observed_finding") or item.get("conflicting_finding") or str(item)
                discussion_tensions.append(finding)
            for r_text in (art_res.get("revised_contributions") or []):
                discussion_tensions.append(r_text)
        elif art_wf == "results":
            for res_item in art_res.get("structured_run_records", []):
                s = res_item.get("summary", "")
                if "fail" in s.lower() or "not significant" in s.lower() or "persist" in s.lower():
                    discussion_tensions.append(s)

    # Differentiate focus claims from baseline literature claims
    focus_claims = [c for c in claims if c["object_id"] in context.focus_claims]
    if not focus_claims and claims:
        focus_claims = [claims[0]]  # Default to primary claim if no focus set

    competing_literature = [c for c in claims if c["object_id"] not in {fc["object_id"] for fc in focus_claims}]

    # 1. Identification and Novelty Verification Checks (Part B1)
    has_verified_causal_design = False
    for adj in adjudications:
        if adj["content"].get("verdict") == "SUPPORTED" and "identification" in adj["content"].get("rationale", "").lower():
            has_verified_causal_design = True

    identification_status = "IDENTIFIED_BY_VERIFIED_DESIGN" if has_verified_causal_design else "IDENTIFICATION_NOT_VERIFIED"

    # Novelty Verification Check
    missing_fulltext_competitors: list[str] = []
    for ft in context.full_texts:
        if ft.get("fulltext_status") != "FULLTEXT_LOADED":
            missing_fulltext_competitors.append(ft.get("citation_key") or ft.get("source_id"))
    for s in [o for o in context.objects if o["object_type"] == "Source"]:
        sid = s["object_id"]
        ft_entry = next((ft for ft in context.full_texts if ft.get("source_id") == sid), None)
        if not ft_entry or ft_entry.get("fulltext_status") != "FULLTEXT_LOADED":
            ckey = s["content"].get("citation_key", sid)
            if ckey not in missing_fulltext_competitors:
                missing_fulltext_competitors.append(ckey)

    if missing_fulltext_competitors or not context.full_texts:
        novelty_status = "NOVELTY_NOT_VERIFIED"
        novelty_verification_plan = (
            f"Retrieve and inspect complete full texts for competing studies ({', '.join(missing_fulltext_competitors[:3]) if missing_fulltext_competitors else 'prior literature'}) "
            "to establish verified empirical differentiation."
        )
    else:
        novelty_status = "NOVELTY_HYPOTHESIS_PENDING_ADJUDICATION"
        novelty_verification_plan = "Perform pairwise claim matrix comparison against loaded competitor full texts."

    # 2. Candidate Contributions (Preserve original candidates; distinguish proposed from established)
    candidates: list[dict[str, Any]] = []
    hypothesis_list = contribution_hypotheses or []

    for fc in focus_claims:
        cid = fc["object_id"]
        c_text = fc["content"].get("text", "")
        tier = fc["content"].get("claim_tier", "descriptive")

        # Determine differentiation basis without unsupported conclusions (Part B1)
        diff_points = []
        if mechanisms:
            diff_points.append(f"Articulates concrete mechanism: {mechanisms[0]['content'].get('label', mechanisms[0]['object_id'])}")
        if tier == "causal":
            diff_points.append("Asserts causal inference tier (requires verified identification design)")
        elif tier == "predictive":
            diff_points.append("Asserts predictive inference tier (requires out-of-sample partition testing)")
        elif tier == "mechanism_consistent":
            diff_points.append("Asserts structural mechanism routing (requires mediation testing)")
        else:
            diff_points.append(f"Provides empirical inquiry ({tier} tier)")

        candidates.append({
            "claim_id": cid,
            "revision": fc["revision"],
            "proposed_contribution": c_text,
            "claim_tier": tier,
            "differentiation_factors": diff_points,
            "stated_hypotheses": hypothesis_list,
            "identification_status": identification_status,
            "novelty_status": novelty_status,
        })

    # 3. Closest Competing Contributions from Literature
    closest_competing: list[dict[str, Any]] = []
    for comp in competing_literature:
        closest_competing.append({
            "object_id": comp["object_id"],
            "revision": comp["revision"],
            "competing_claim": comp["content"].get("text", ""),
            "tier": comp["content"].get("claim_tier", "descriptive"),
        })
    if not closest_competing and context.excerpts:
        for ex in context.excerpts[:3]:
            closest_competing.append({
                "source_id": ex.get("source_id", "LIT_PRIOR"),
                "competing_claim": ex.get("normalized_claim") or ex.get("excerpt", ""),
                "locator": ex.get("locator", ""),
            })

    # 4. Supporting & Contradicting Evidence
    supporting_evidence: list[dict[str, Any]] = []
    contradicting_evidence: list[dict[str, Any]] = []

    for ex in context.excerpts:
        text_str = (ex.get("excerpt") or ex.get("normalized_claim", "")).lower()
        if "contradict" in text_str or "fails" in text_str or "unsupported" in text_str:
            contradicting_evidence.append(ex)
        else:
            supporting_evidence.append(ex)

    for res in results:
        res_summary = res["content"].get("summary", "")
        res_entry = {"result_id": res["object_id"], "revision": res["revision"], "summary": res_summary}
        if "fail" in res_summary.lower() or "not significant" in res_summary.lower() or "zero" in res_summary.lower() or res["content"].get("contradictory"):
            contradicting_evidence.append(res_entry)
        else:
            supporting_evidence.append(res_entry)

    for adj in adjudications:
        verdict = adj["content"].get("verdict")
        adj_entry = {"adjudication_id": adj["object_id"], "verdict": verdict, "rationale": adj["content"].get("rationale", "")}
        if verdict in {"NOT_SUPPORTED", "UNCLEAR"}:
            contradicting_evidence.append(adj_entry)
        else:
            supporting_evidence.append(adj_entry)

    # 5. Necessary Assumptions
    assumptions = [
        "Construct operationalizations accurately reflect the theoretical phenomena.",
        "Observed empirical relationships are not artifacts of selection or survivorship.",
    ]
    if any(fc["content"].get("claim_tier") == "causal" for fc in focus_claims):
        assumptions.append("Identifying assumptions (exogeneity, unconfoundedness, or parallel trends) hold.")
    if theories:
        assumptions.append(f"Theoretical framing from '{theories[0]['content'].get('label', 'Theory')}' applies to the sample domain.")

    # 6. Missing Verification
    missing: list[str] = []
    if not results:
        missing.append("Empirical results verifying the candidate claim have not yet been recorded.")
    if not adjudications:
        missing.append("Independent scholarly adjudication verifying the claim's support is pending.")
    if contradicting_evidence:
        missing.append(f"{len(contradicting_evidence)} potentially contradictory evidence item(s) require explicit reconciliation.")
    if novelty_status == "NOVELTY_NOT_VERIFIED":
        missing.append(f"Novelty verification incomplete: full texts missing for {len(missing_fulltext_competitors)} competing source(s).")

    # 7. Alternative Formulations (Includes provisional formulations from upstream handoff)
    primary_text = focus_claims[0]["content"].get("text", "") if focus_claims else "Focal claim"
    alternatives = [
        "Conservative formulation: Limit claim scope strictly to documented sample contexts.",
        f"Mechanistic formulation: Frame contribution around '{mechanisms[0]['content'].get('label', 'mechanism')}' rather than broad universality." if mechanisms else "Boundary formulation: Specify institutional and temporal boundary conditions.",
    ]
    if discussion_tensions:
        for dt in discussion_tensions[:2]:
            alternatives.append(f"Provisional revised formulation: Reconcile candidate claim with contradictory finding ({dt}).")

    # 8. Grounded Confidence Explanation
    if contradicting_evidence or discussion_tensions:
        confidence = (
            f"Preliminary with unresolved tension: {len(supporting_evidence)} supporting evidence items versus "
            f"{len(contradicting_evidence) + len(discussion_tensions)} contradictory findings/tensions. "
            "Novelty and validity cannot be established without reconciling discrepancies."
        )
    elif results and adjudications:
        confidence = (
            f"Moderately substantiated: Grounded in {len(results)} empirical result(s) and {len(adjudications)} adjudication(s), "
            f"differentiated from {len(closest_competing)} baseline literature items."
        )
    else:
        confidence = (
            "Formulative hypothesis stage: Candidate contributions are structured, but await "
            "formal empirical verification and independent adjudication."
        )

    result_data = ContributionFindingResult(
        candidate_contributions=candidates,
        closest_competing_contributions=closest_competing,
        exact_point_of_differentiation="; ".join(candidates[0]["differentiation_factors"]) if candidates else "Inquiry specification",
        supporting_evidence=supporting_evidence,
        contradicting_evidence=contradicting_evidence,
        necessary_assumptions=assumptions,
        missing_verification=missing,
        alternative_contribution_formulations=alternatives,
        confidence_explanation=confidence,
        is_preliminary=bool(contradicting_evidence or not results or not adjudications or novelty_status == "NOVELTY_NOT_VERIFIED"),
    )

    out_dict = result_data.to_dict()
    out_dict["identification_status"] = identification_status
    out_dict["novelty_status"] = novelty_status
    out_dict["novelty_verification_plan"] = novelty_verification_plan
    out_dict["missing_fulltext_competitors"] = missing_fulltext_competitors
    out_dict["upstream_artifacts_consumed"] = consumed_artifact_ids

    elapsed_ms = int((time.time() - start_time) * 1000)
    usage = TaskUsage(
        workflow="contribution",
        task_id=tid,
        model=None,
        input_tokens=context.estimated_tokens,
        output_tokens=estimate_tokens(json.dumps(out_dict))[0],
        cache_read_tokens=None,
        llm_calls=0,
        context_objects=len(context.objects),
        reused_artifacts=0,
        elapsed_ms=elapsed_ms,
        usage_source="estimated" if context.is_token_estimate else "provider",
    )

    if context.budget_status == "INCOMPLETE_EXCEEDED":
        status_val = "INCOMPLETE_CONTEXT"
    elif identification_status == "IDENTIFICATION_NOT_VERIFIED" or novelty_status == "NOVELTY_NOT_VERIFIED":
        status_val = "REQUIRES_VERIFICATION"
    else:
        status_val = "SUCCESS"

    return WorkflowOutput(
        workflow="contribution",
        task_id=tid,
        project_id=context.project_id,
        status=status_val,
        result=out_dict,
        usage=usage,
        provenance_refs=[{"object_id": o["object_id"], "revision": o["revision"]} for o in context.objects],
        notes=["Offline deterministic contribution synthesis grounded in revision-pinned state without fabricated novelty."],
    )
