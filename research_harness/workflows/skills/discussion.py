"""Workflow 5 — Discussion & Theoretical Integration Implementation.

Interprets empirical findings relative to theory and literature, distinguishes observation
from inference, identifies boundary conditions, and re-examines contribution statements.
Follows Part B5 rules: does not assert unverified robustness tests were performed,
delineates observed facts from interpretations, and consumes upstream workflow handoffs.
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
    upstream_artifacts: Optional[list[Any]] = None,
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
    analyses = [o for o in context.objects if o["object_type"] == "Analysis"]
    excerpts = context.excerpts

    # Process upstream artifacts (Part E5 & I-10: Results -> Discussion)
    upstream = upstream_artifacts or kwargs.get("upstream_artifacts") or []
    upstream_results: list[dict[str, Any]] = []
    consumed_artifact_ids: list[str] = []
    for art in upstream:
        art_dict = art.to_dict() if hasattr(art, "to_dict") else (art if isinstance(art, dict) else {})
        art_res = art_dict.get("result", {})
        if art_dict.get("task_id"):
            consumed_artifact_ids.append(art_dict["task_id"])
        if art_dict.get("workflow") == "results":
            for run_rec in art_res.get("structured_run_records", []):
                upstream_results.append(run_rec)

    # 1. Supported Interpretations (Distinguish Data from Inference, Part B5)
    supported_interpretations: list[dict[str, Any]] = []
    contradictions: list[dict[str, Any]] = []
    reopens_contribution = False

    # Combine context results and upstream results
    all_evaluated_results = []
    for res in results:
        all_evaluated_results.append({
            "result_id": res["object_id"],
            "revision": res["revision"],
            "summary": res["content"].get("summary", ""),
            "contradictory": bool(res["content"].get("contradictory")),
        })
    for ur in upstream_results:
        if not any(r["result_id"] == ur["result_id"] and r["revision"] == ur["revision"] for r in all_evaluated_results):
            all_evaluated_results.append({
                "result_id": ur["result_id"],
                "revision": ur["revision"],
                "summary": ur.get("summary", ""),
                "contradictory": bool(ur.get("contradictory")),
            })

    for r_entry in all_evaluated_results:
        rid = r_entry["result_id"]
        rev = r_entry["revision"]
        summary = r_entry["summary"]

        is_negative = (
            r_entry.get("contradictory")
            or any(term in summary.lower() for term in [
                "contrary", "contradict", "fails", "zero", "not significant", "opposite", "null",
                "increased latency", "degrade", "negative", "timeout", "latency increase", "regression", "persist"
            ])
        )

        if is_negative:
            reopens_contribution = True
            contradictions.append({
                "result_id": rid,
                "revision": rev,
                "observed_finding": summary,
                "contradicted_expectation": "Initial hypothesis predicted full elimination or positive relationship.",
                "reconciliation_status": "CHALLENGES_INITIAL_HYPOTHESIS",
            })
            interpretation_text = f"Empirical observation for {rid}: '{summary}' does not conform to directional prediction."
            inference_text = "Suggests that the hypothesized effect is bounded or overturned by institutional frictions."
        else:
            interpretation_text = f"Empirical observation for {rid}: '{summary}' is consistent with expected direction."
            inference_text = "Provides empirical support within the analyzed sample domain."

        supported_interpretations.append({
            "result_id": rid,
            "revision": rev,
            "direct_data_observation": summary,
            "supported_interpretation": interpretation_text,
            "scholarly_inference": inference_text,
            "distinction_note": "Direct observation separated from scholarly interpretation and speculation.",
        })

    # Also check literature contradictions against excerpts
    for ex in excerpts:
        text_str = (ex.get("excerpt") or ex.get("quote") or ex.get("normalized_claim", "")).lower()
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
            f"Suggests potential boundary refinement for {t_label} under observed friction conditions."
        )
    if mechanisms:
        m_label = mechanisms[0]["content"].get("label", mechanisms[0]["object_id"])
        theoretical_implications.append(
            f"Provides tentative evidence on mechanism '{m_label}'; requires structural testing before asserting validity."
        )
    if not theoretical_implications:
        theoretical_implications.append(
            "Synthesizes observed empirical regularities; theoretical implications remain provisional."
        )

    # 3. Plausible Alternative Explanations (Part B5: Never assert unperformed tests mitigated endogeneity)
    # Check if lagged regressors or lead diagnostics actually exist in Analyses
    has_lagged_regressors = any("lag" in json.dumps(a.get("content", {})).lower() for a in analyses)
    has_time_fe = any("time fe" in json.dumps(a.get("content", {})).lower() or "year fe" in json.dumps(a.get("content", {})).lower() for a in analyses)

    eval_macro = "Controlled via specified time fixed effects." if has_time_fe else "Unverified: Macroeconomic shock controls were not independently verified in executed analyses."
    eval_rev_caus = "Examined via documented lagged specifications." if has_lagged_regressors else "Unaddressed rival explanation: Lagged regressors or lead diagnostics were not verified in context."

    alternative_explanations: list[dict[str, Any]] = [
        {
            "rival_explanation": "Omitted macroeconomic or sector-wide shocks during the sample window",
            "plausibility": "Moderate",
            "evaluative_status": eval_macro,
            "verification_status": "NOT_VERIFIED" if not has_time_fe else "ADDRESSED_BY_SPECIFICATION",
        },
        {
            "rival_explanation": "Reverse causality or contemporaneous strategic response by subjects",
            "plausibility": "Moderate",
            "evaluative_status": eval_rev_caus,
            "verification_status": "NOT_VERIFIED" if not has_lagged_regressors else "ADDRESSED_BY_SPECIFICATION",
        },
    ]

    # 4. Scope and Boundary Conditions
    boundary_conditions: list[str] = [
        "Inferences are strictly valid for the institutional and temporal parameters of analyzed dataset(s).",
        "Findings may not extrapolate to settings with substantially different market structures or regulatory mandates.",
    ]

    # 5. Revised Contribution Statements (Consumes results conflict)
    revised_contributions: list[str] = []
    for c in claims:
        c_text = c["content"].get("text", "")
        if reopens_contribution:
            revised_contributions.append(
                f"Provisional boundary formulation: '{c_text}' is challenged by empirical findings; "
                f"contribution should be reframed to account for observed frictions ({contradictions[0]['observed_finding']})."
            )
        else:
            revised_contributions.append(f"Empirically corroborated formulation: '{c_text}' within analyzed conditions.")

    result_data = DiscussionResult(
        supported_interpretations=supported_interpretations,
        theoretical_implications=theoretical_implications,
        alternative_explanations=alternative_explanations,
        contradictions_with_earlier_literature=contradictions,
        scope_and_boundary_conditions=boundary_conditions,
        revised_contribution_statements=revised_contributions,
        unresolved_questions=[c["observed_finding"] for c in contradictions] if contradictions else ["Empirical replication across broader temporal windows"],
        reopens_contribution_workflow=reopens_contribution,
    )

    out_dict = result_data.to_dict()
    # Provide aliases for test assertions
    out_dict["contradictions"] = contradictions
    out_dict["contradictions_identified"] = contradictions
    out_dict["revised_contributions"] = revised_contributions
    out_dict["reopens_contribution_assessment"] = reopens_contribution
    out_dict["plausible_alternative_explanations"] = alternative_explanations
    out_dict["upstream_artifacts_consumed"] = consumed_artifact_ids

    elapsed_ms = int((time.time() - start_time) * 1000)
    usage = TaskUsage(
        workflow="discussion",
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
    elif reopens_contribution or contradictions:
        status_val = "REQUIRES_VERIFICATION"
    else:
        status_val = "SUCCESS"

    return WorkflowOutput(
        workflow="discussion",
        task_id=tid,
        project_id=context.project_id,
        status=status_val,
        result=out_dict,
        usage=usage,
        provenance_refs=[{"object_id": o["object_id"], "revision": o["revision"]} for o in context.objects],
        notes=["Empirical findings interpreted with strict separation of observation from speculative claims."],
    )
