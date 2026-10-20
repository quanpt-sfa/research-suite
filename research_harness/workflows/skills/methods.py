"""Workflow 3 — Methods Design and Evaluation Implementation.

Designs, formalizes, and evaluates research methods, empirical specifications, construct
operationalizations, and diagnostic checks, integrating optional discipline profiles.
Follows Part B3 rules: methods remain conditional candidate designs (CANDIDATE_METHOD)
rather than justified designs, and checklist items are not marked ADDRESSED_IN_SPECIFICATION
merely for being displayed.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional

from research_harness.workflows.contracts import (
    ContextPackage,
    MethodsEvaluationResult,
    TaskUsage,
    WorkflowOutput,
)
from research_harness.workflows.instrumentation import estimate_tokens
from research_harness.workflows.profiles.registry import get_profile


def execute_methods_workflow(
    context: ContextPackage,
    *,
    task_id: Optional[str] = None,
    candidate_methods: Optional[list[str]] = None,
    discipline_profile: Optional[str] = None,
    **kwargs: Any,
) -> WorkflowOutput:
    """Execute Methods workflow deterministically over structured research context."""
    start_time = time.time()
    tid = task_id or f"TASK_METH_{uuid.uuid4().hex[:8].upper()}"

    rq_objs = [o for o in context.objects if o["object_type"] == "ResearchQuestion"]
    rq_text = rq_objs[0]["content"].get("text", "") if rq_objs else "Primary research inquiry"

    constructs = [o for o in context.objects if o["object_type"] == "Construct"]
    proxies = [o for o in context.objects if o["object_type"] == "Proxy"]
    datasets = [o for o in context.objects if o["object_type"] == "Dataset"]
    claims = [o for o in context.objects if o["object_type"] == "Claim"]

    # 1. Candidate Research Designs (Part B3: Conditional candidates, not justified methods)
    candidate_designs: list[dict[str, Any]] = []
    methods_requested = candidate_methods or []

    claim_tiers = {c["content"].get("claim_tier", "descriptive") for c in claims}

    if "causal" in claim_tiers or "causal" in [m.lower() for m in methods_requested]:
        candidate_designs.append({
            "design_name": "Quasi-Experimental Difference-in-Differences",
            "type": "quasi_experimental",
            "status": "CANDIDATE_METHOD",
            "justified": False,
            "required_information": [
                "Explicit exogenous intervention event with known timing",
                "Observable unexposed comparison group",
                "Pre-treatment period data for parallel trends evaluation",
            ],
            "target_claim_tier": "causal",
            "rationale": "Candidate method for mitigating time-invariant confounders if parallel trends hold.",
        })
        candidate_designs.append({
            "design_name": "Instrumental Variables (2SLS)",
            "type": "instrumental_variable",
            "status": "CANDIDATE_METHOD",
            "justified": False,
            "required_information": [
                "Exogenous instrument satisfying relevance (F > 10) and exclusion restriction",
            ],
            "target_claim_tier": "causal",
            "rationale": "Candidate method if valid exogenous instrument is identified and defensible.",
        })
    elif "predictive" in claim_tiers:
        candidate_designs.append({
            "design_name": "Cross-Validated Predictive Model (Train/Validation/Test)",
            "type": "machine_learning_predictive",
            "status": "CANDIDATE_METHOD",
            "justified": False,
            "required_information": ["Temporal or clustered partition scheme", "Pre-specified performance metrics"],
            "target_claim_tier": "predictive",
            "evaluation_metrics": ["RMSE", "AUC-ROC", "Out-of-sample R2"],
            "rationale": "Evaluates out-of-sample generalization without in-sample overfitting.",
        })
    else:
        candidate_designs.append({
            "design_name": "Panel Regression with Multi-Way Fixed Effects",
            "type": "panel_econometric",
            "status": "CANDIDATE_METHOD",
            "justified": False,
            "required_information": ["Panel unit identifier", "Time index", "Attrition diagnostics"],
            "target_claim_tier": "associational",
            "rationale": "Candidate design to control for time-invariant unit heterogeneity.",
        })

    # 2. Key Assumptions
    assumptions: list[str] = [
        "Construct-to-proxy mapping preserves construct validity and minimizes measurement error.",
        "Panel observation units are not subject to differential attrition across sample windows.",
    ]
    if "causal" in claim_tiers:
        assumptions.extend([
            "Parallel trends assumption: Treated and control units would follow parallel paths in the absence of treatment.",
            "Stable Unit Treatment Value Assumption (SUTVA): No unmodeled interference or spillovers between units.",
        ])

    # 3. Identification & Measurement Requirements
    id_requirements: list[str] = [
        f"Operationalize {len(constructs)} construct(s) using {len(proxies)} validated empirical proxy metric(s).",
    ]
    for c in constructs:
        c_label = c["content"].get("label", c["object_id"])
        matching_proxies = [p for p in proxies if p["content"].get("construct_id") == c["object_id"]]
        if matching_proxies:
            p_labels = ", ".join(p["content"].get("label", p["object_id"]) for p in matching_proxies)
            id_requirements.append(f"Construct '{c_label}' operationalized by proxy: {p_labels}")
        else:
            id_requirements.append(f"Construct '{c_label}' lacks explicit linked Proxy object.")

    # 4. Specification Plans
    specification_plans: list[dict[str, Any]] = [
        {
            "specification_name": "Proposed Baseline Model",
            "dependent_variable": proxies[0]["content"].get("label", "DependentVariable") if proxies else "Y",
            "primary_regressors": [proxies[1]["content"].get("label", "TreatmentRegressor")] if len(proxies) > 1 else ["X"],
            "controls": ["Size", "Leverage", "PriorPerformance", "MacroTrend"],
            "fixed_effects": ["Entity/Firm FE", "Year/Period FE"],
            "clustering": "Two-way clustered by entity and time period",
            "datasets_used": [d["content"].get("label", d["object_id"]) for d in datasets] if datasets else ["Primary dataset"],
            "status": "PROPOSED",
        }
    ]

    # 5. Diagnostic and Falsification Tests
    diagnostics = [
        {
            "test_name": "Placebo Timing / Lead-Lag Test",
            "purpose": "Verify lack of pre-treatment anticipatory trends",
            "success_criterion": "Coefficients on pre-treatment leads are statistically indistinguishable from zero",
            "status": "REQUIRES_INFORMATION",
        },
        {
            "test_name": "Alternative Proxy Sensitivity",
            "purpose": "Check sensitivity to proxy measurement choices",
            "success_criterion": "Core coefficients maintain direction and significance under secondary proxy definitions",
            "status": "REQUIRES_INFORMATION",
        },
        {
            "test_name": "Cluster-Robust Variance Check",
            "purpose": "Rule out artificial significance from correlated residuals",
            "success_criterion": "Standard errors remain robust under alternative clustering structures",
            "status": "REQUIRES_INFORMATION",
        },
    ]

    # 6. Discipline Profile Integration (Part B3: Never ADDRESSED_IN_SPECIFICATION merely because displayed)
    prof_id = discipline_profile or kwargs.get("discipline") or (context.manifest.get("profile") or {}).get("profile_id")
    profile_results = None
    if prof_id:
        try:
            prof = get_profile(prof_id)
            profile_results = {
                "profile": prof.identifier,
                "traditions": prof.research_traditions,
                "checklist_evaluations": [
                    {
                        "item": item["item"],
                        "prompt": item["prompt"],
                        "compliance_status": "REQUIRES_INFORMATION",
                    }
                    for item in prof.methodological_checklist
                ],
                "recommended_validity_questions": prof.validity_questions,
            }
        except KeyError:
            pass

    # 7. Limitations and Unresolved Choices
    limitations = [
        "Candidate econometric methods require verified institutional events; causality cannot be claimed from specification structure alone.",
        "External validity is constrained by sample composition of underlying datasets.",
    ]

    result_data = MethodsEvaluationResult(
        candidate_research_designs=candidate_designs,
        assumptions=assumptions,
        identification_or_measurement_requirements=id_requirements,
        specification_plans=specification_plans,
        alternative_methods=[{"alternative": "Matched sample using propensity score or entropy balancing", "tradeoff": "Reduces sample size while improving covariate balance."}],
        diagnostic_tests=diagnostics,
        limitations_and_unresolved_choices=limitations,
        profile_checklist_results=profile_results,
    )

    out_dict = result_data.to_dict()

    elapsed_ms = int((time.time() - start_time) * 1000)
    usage = TaskUsage(
        workflow="methods",
        task_id=tid,
        model=None,
        input_tokens=context.estimated_tokens,
        output_tokens=estimate_tokens(json.dumps(out_dict))[0],
        cache_read_tokens=None,
        llm_calls=0,
        context_objects=len(context.objects),
        reused_artifacts=0,
        elapsed_ms=elapsed_ms,
        usage_source="estimated",
    )

    if context.budget_status == "INCOMPLETE_EXCEEDED":
        status_val = "INCOMPLETE_CONTEXT"
    elif any(d.get("status") == "CANDIDATE_METHOD" for d in candidate_designs):
        status_val = "REQUIRES_VERIFICATION"
    else:
        status_val = "SUCCESS"

    return WorkflowOutput(
        workflow="methods",
        task_id=tid,
        project_id=context.project_id,
        status=status_val,
        result=out_dict,
        usage=usage,
        provenance_refs=[{"object_id": o["object_id"], "revision": o["revision"]} for o in context.objects],
        notes=["Candidate research designs evaluated conditionally without premature methodological justification."],
    )
