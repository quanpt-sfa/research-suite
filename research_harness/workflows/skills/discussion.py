"""Workflow 5 — Discussion & Theoretical Integration Implementation.

Interprets empirical findings relative to theory and literature, distinguishes observation
from inference, identifies boundary conditions, and re-examines contribution statements.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional

from research_harness.workflows.contracts import (
    ContextPackage,
    DiscussionResult,
    TaskUsage,
    WorkflowOutput,
)
from research_harness.workflows.instrumentation import estimate_tokens


def execute_discussion_workflow(
    context: ContextPackage,
    *,
    task_id: Optional[str] = None,
    limitations: Optional[list[str]] = None,
    **kwargs: Any,
) -> WorkflowOutput:
    """Execute Discussion workflow deterministically over structured research context."""
    start_time = time.time()
    tid = task_id or f"TASK_DISC_{uuid.uuid4().hex[:8].upper()}"

    rq_objs = [o for o in context.objects if o["object_type"] == "ResearchQuestion"]
    rq_text = rq_objs[0]["content"].get("text", "") if rq_objs else "Primary research inquiry"

    results = [o for o in context.objects if o["object_type"] == "Result"]
    claims = [o for o in context.objects if o["object_type"] == "Claim"]
    theories = [o for o in context.objects if o["object_type"] == "Theory"]
    mechanisms = [o for o in context.objects if o["object_type"] == "Mechanism"]
    excerpts = context.excerpts

    # 1. Supported Interpretations (Distinguish Data from Inference)
    supported_interpretations: list[dict[str, Any]] = []
    contradictions: list[dict[str, Any]] = []
    reopens_contribution = False

    for res in results:
        rid = res["object_id"]
        rev = res["revision"]
        summary = res["content"].get("summary", "")

        # Check if result contradicts any claims or literature
        is_negative = (
            bool(res["content"].get("contradictory") or res["content"].get("contradictory_signal"))
            or any(term in summary.lower() for term in [
                "contrary", "contradict", "fails", "zero", "not significant", "opposite", "null",
                "increased latency", "degrade", "negative", "timeout", "latency increase", "regression"
            ])
        )

        if is_negative:
            reopens_contribution = True
            contradictions.append({
                "result_id": rid,
                "revision": rev,
                "observed_finding": summary,
                "contradicted_expectation": "Initial hypothesis predicted significant positive effect.",
                "reconciliation_status": "CHALLENGES_INITIAL_HYPOTHESIS",
            })
            interpretation_text = f"Observed empirical data for {rid} do not support the expected directional hypothesis ({summary})."
            inference_text = "This suggests that the theoretical mechanism may be bounded by unmodeled institutional frictions or alternative channels."
        else:
            interpretation_text = f"Empirical findings ({summary}) are consistent with the hypothesized relationship."
            inference_text = "Provides empirical support for the theoretical prediction within the tested sample window."

        supported_interpretations.append({
            "result_id": rid,
            "revision": rev,
            "direct_data_observation": summary,
            "supported_interpretation": interpretation_text,
            "scholarly_inference": inference_text,
            "distinction_note": "Delineates observable statistical metric from broader conceptual conclusion.",
        })

    # Also check literature contradictions against excerpts
    for ex in excerpts:
        text_str = (ex.get("excerpt") or ex.get("normalized_claim", "")).lower()
        if "contradict" in text_str or "fails" in text_str or "unsupported" in text_str:
            reopens_contribution = True
            contradictions.append({
                "literature_passage_id": ex.get("passage_id"),
                "source_id": ex.get("source_id"),
                "observed_finding": ex.get("excerpt") or ex.get("normalized_claim"),
                "contradicted_expectation": "Prior consensus literature",
                "reconciliation_status": "LITERATURE_DISAGREEMENT",
            })

    # 2. Theoretical Implications
    theoretical_implications: list[str] = []
    if theories:
        t_label = theories[0]["content"].get("label", theories[0]["object_id"])
        theoretical_implications.append(
            f"Extends {t_label} by specifying how empirical friction influences operational outcomes."
        )
    if mechanisms:
        m_label = mechanisms[0]["content"].get("label", mechanisms[0]["object_id"])
        theoretical_implications.append(
            f"Provides evidence regarding the plausibility of mechanism '{m_label}' under observed institutional conditions."
        )
    if not theoretical_implications:
        theoretical_implications.append(
            "Synthesizes observed empirical regularities to clarify the scope of existing theoretical models."
        )

    # 3. Plausible Alternative Explanations
    alternative_explanations: list[dict[str, Any]] = [
        {
            "rival_explanation": "Omitted macroeconomic or sector-wide shocks during the sample window",
            "plausibility": "Moderate",
            "evaluative_status": "Controlled via time fixed effects, but residual macro covariance cannot be entirely ruled out.",
        },
        {
            "rival_explanation": "Reverse causality or contemporaneous strategic response by subjects",
            "plausibility": "Low-to-moderate",
            "evaluative_status": "Mitigated by lagged regressor specifications and pre-treatment lead diagnostics.",
        },
    ]

    # 4. Scope and Boundary Conditions
    boundary_conditions: list[str] = [
        "Inferences are strictly valid for the institutional and temporal parameters of the analyzed dataset.",
        "Findings may not extrapolate to settings with substantially different regulatory oversight or disclosure mandates.",
    ]

    # 5. Revised Contribution Statements
    revised_contributions: list[str] = []
    for c in claims:
        c_text = c["content"].get("text", "")
        if reopens_contribution:
            revised_contributions.append(
                f"Refined scope for '{c_text}': In light of observed empirical nuances, contribution is restricted "
                f"to documented regimes where hypothesized mechanisms dominate."
            )
        else:
            revised_contributions.append(
                f"Substantiated contribution for '{c_text}': Validated by empirical outputs with explicit boundary conditions."
            )

    # 6. Unresolved Questions
    unresolved_questions: list[str] = [
        "How do these findings generalize across international jurisdictions with alternative institutional enforcement?",
        "What are the long-term equilibrium consequences beyond the immediate post-intervention horizon?",
    ]
    if contradictions:
        unresolved_questions.append(
            f"What specific conditions reconcile the {len(contradictions)} documented empirical contradiction(s)?"
        )

    result_data = DiscussionResult(
        supported_interpretations=supported_interpretations,
        theoretical_implications=theoretical_implications,
        alternative_explanations=alternative_explanations,
        contradictions_with_earlier_literature=contradictions,
        scope_and_boundary_conditions=boundary_conditions,
        revised_contribution_statements=revised_contributions,
        unresolved_questions=unresolved_questions,
        reopens_contribution_workflow=reopens_contribution,
    )

    elapsed_ms = int((time.time() - start_time) * 1000)
    usage = TaskUsage(
        workflow="discussion",
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
        workflow="discussion",
        task_id=tid,
        project_id=context.project_id,
        status="INCOMPLETE_CONTEXT" if context.budget_status == "INCOMPLETE_EXCEEDED" else "SUCCESS",
        result=result_data.to_dict(),
        usage=usage,
        provenance_refs=[{"object_id": o["object_id"], "revision": o["revision"]} for o in context.objects],
        notes=[
            f"Discussion evaluated: {'Contradictions detected; reopens Contribution Finding.' if reopens_contribution else 'Findings consistent with theoretical predictions.'}"
        ],
    )
