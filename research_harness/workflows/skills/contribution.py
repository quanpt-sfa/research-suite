"""Workflow 1 — Contribution Finding Implementation.

Identifies, challenges, and refines potential scientific contributions against prior literature
and empirical findings, preserving exact provenance and evidence bindings.
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

    # Differentiate focus claims from baseline literature claims
    focus_claims = [c for c in claims if c["object_id"] in context.focus_claims]
    if not focus_claims and claims:
        focus_claims = [claims[0]]  # Default to primary claim if no focus set

    competing_literature = [c for c in claims if c["object_id"] not in {fc["object_id"] for fc in focus_claims}]

    # 1. Candidate Contributions
    candidates: list[dict[str, Any]] = []
    hypothesis_list = contribution_hypotheses or []

    for fc in focus_claims:
        cid = fc["object_id"]
        c_text = fc["content"].get("text", "")
        tier = fc["content"].get("claim_tier", "descriptive")

        # Determine differentiation basis
        diff_points = []
        if mechanisms:
            diff_points.append(f"Articulates concrete mechanism: {mechanisms[0]['content'].get('label', mechanisms[0]['object_id'])}")
        if tier == "causal":
            diff_points.append("Establishes counterfactual identification beyond associational literature")
        elif tier == "predictive":
            diff_points.append("Demonstrates out-of-sample predictive performance on unseen partitions")
        elif tier == "mechanism_consistent":
            diff_points.append("Demonstrates empirical consistency with hypothesized structural routing")
        else:
            diff_points.append(f"Provides structured empirical operationalization ({tier} tier)")

        candidates.append({
            "claim_id": cid,
            "revision": fc["revision"],
            "proposed_contribution": c_text,
            "claim_tier": tier,
            "differentiation_factors": diff_points,
            "stated_hypotheses": hypothesis_list,
        })

    # 2. Closest Competing Contributions from Literature
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

    # 3. Supporting & Contradicting Evidence
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
        if "fail" in res_summary.lower() or "not significant" in res_summary.lower() or "zero" in res_summary.lower():
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

    # 4. Necessary Assumptions
    assumptions = [
        "Construct operationalizations accurately reflect the theoretical phenomena.",
        "Observed empirical relationships are not artifacts of selection or survivorship.",
    ]
    if any(fc["content"].get("claim_tier") == "causal" for fc in focus_claims):
        assumptions.append("Identifying assumptions (exogeneity, unconfoundedness, or parallel trends) hold.")
    if theories:
        assumptions.append(f"Theoretical framing from '{theories[0]['content'].get('label', 'Theory')}' applies to the sample domain.")

    # 5. Missing Verification
    missing: list[str] = []
    if not results:
        missing.append("Empirical results verifying the candidate claim have not yet been recorded.")
    if not adjudications:
        missing.append("Independent scholarly adjudication verifying the claim's support is pending.")
    if contradicting_evidence:
        missing.append(f"{len(contradicting_evidence)} potentially contradictory evidence item(s) require explicit reconciliation.")

    # 6. Alternative Formulations
    primary_text = focus_claims[0]["content"].get("text", "") if focus_claims else "Focal claim"
    alternatives = [
        f"Conservative formulation: Limit claim scope strictly to documented sample contexts.",
        f"Mechanistic formulation: Frame contribution around '{mechanisms[0]['content'].get('label', 'mechanism')}' rather than broad universality." if mechanisms else f"Boundary formulation: Specify institutional and temporal boundary conditions.",
    ]

    # 7. Confidence Explanation Grounded in Evidence
    if contradicting_evidence:
        confidence = (
            f"Preliminary with substantial unresolved tension: {len(supporting_evidence)} supporting evidence items versus "
            f"{len(contradicting_evidence)} contradictory findings. Novelty cannot be established without reconciling discrepancies."
        )
    elif results and adjudications:
        confidence = (
            f"Moderately substantiated: Grounded in {len(results)} empirical result(s) and {len(adjudications)} adjudication(s), "
            f"differentiated from {len(closest_competing)} baseline literature items."
        )
    else:
        confidence = (
            f"Formulative hypothesis stage: Candidate contributions are structured and differentiated, but await "
            f"formal empirical verification and adjudication."
        )

    result_data = ContributionFindingResult(
        candidate_contributions=candidates,
        closest_competing_contributions=closest_competing,
        exact_point_of_differentiation="; ".join(candidates[0]["differentiation_factors"]) if candidates else "Novel inquiry specification",
        supporting_evidence=supporting_evidence,
        contradicting_evidence=contradicting_evidence,
        necessary_assumptions=assumptions,
        missing_verification=missing,
        alternative_contribution_formulations=alternatives,
        confidence_explanation=confidence,
        is_preliminary=bool(contradicting_evidence or not results or not adjudications),
    )

    elapsed_ms = int((time.time() - start_time) * 1000)
    usage = TaskUsage(
        workflow="contribution",
        task_id=tid,
        model=None,
        input_tokens=context.estimated_tokens,
        output_tokens=estimate_tokens(json.dumps(result_data.to_dict()))[0],
        cache_read_tokens=None,
        llm_calls=0,
        context_objects=len(context.objects),
        reused_artifacts=0,
        elapsed_ms=elapsed_ms,
        usage_source="estimated" if context.is_token_estimate else "provider",
    )

    return WorkflowOutput(
        workflow="contribution",
        task_id=tid,
        project_id=context.project_id,
        status="INCOMPLETE_CONTEXT" if context.budget_status == "INCOMPLETE_EXCEEDED" else "SUCCESS",
        result=result_data.to_dict(),
        usage=usage,
        provenance_refs=[{"object_id": o["object_id"], "revision": o["revision"]} for o in context.objects],
        notes=["Offline deterministic contribution synthesis grounded in revision-pinned state."],
    )
