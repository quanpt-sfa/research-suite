"""Workflow 4 — Results Interpretation & Reproducibility Implementation.

Organizes reproducible empirical analyses, interprets computational outputs, and detects
changed results affecting dependent scientific claims via graph traversal.
Follows Part B4 rules: unknown random seeds, scripts, and estimators remain None;
unexecuted results are flagged COMPUTATIONAL_REPRODUCTION_NOT_VERIFIED.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional

from research_harness.workflows.contracts import (
    ContextPackage,
    ResultsInterpretationResult,
    TaskUsage,
    WorkflowOutput,
)
from research_harness.workflows.instrumentation import estimate_tokens


def execute_results_workflow(
    context: ContextPackage,
    *,
    task_id: Optional[str] = None,
    run_metadata: Optional[dict[str, Any]] = None,
    **kwargs: Any,
) -> WorkflowOutput:
    """Execute Results workflow deterministically over structured research context."""
    start_time = time.time()
    tid = task_id or f"TASK_RES_{uuid.uuid4().hex[:8].upper()}"

    results = [o for o in context.objects if o["object_type"] == "Result"]
    datasets = [o for o in context.objects if o["object_type"] == "Dataset"]
    analyses = [o for o in context.objects if o["object_type"] == "Analysis"]
    claims = [o for o in context.objects if o["object_type"] == "Claim"]

    # 1. Structured Run Records
    run_records: list[dict[str, Any]] = []
    input_params: list[dict[str, Any]] = []
    artifact_refs: list[dict[str, Any]] = []
    supported_findings: list[str] = []

    for res in results:
        rid = res["object_id"]
        rev = res["revision"]
        c = res["content"]
        summary = c.get("summary", "")
        metrics = c.get("metrics") or c.get("statistics") or {"summary": summary}

        run_records.append({
            "result_id": rid,
            "revision": rev,
            "label": c.get("label", rid),
            "summary": summary,
            "metrics": metrics,
            "analysis_id": c.get("analysis_id"),
            "contradictory": bool(c.get("contradictory")),
        })

        if c.get("artifact_sha256"):
            artifact_refs.append({
                "result_id": rid,
                "revision": rev,
                "artifact_sha256": c.get("artifact_sha256"),
                "artifact_type": c.get("artifact_type", "table"),
            })

        if summary:
            supported_findings.append(f"[{rid}:rev{rev}] {summary}")

    # 2. Extract Input and Parameter References from Analyses and Datasets (Part B4: No fabricated paths/estimators)
    for a in analyses:
        c_a = a.get("content", {})
        input_params.append({
            "analysis_id": a["object_id"],
            "script_path": c_a.get("script_path"),  # None if unspecified
            "estimator": c_a.get("estimator"),      # None if unspecified
            "parameters": c_a.get("parameters", {}),
        })

    # 3. Detect Changed Results and Affected Claims
    by_oid: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        by_oid.setdefault(r["object_id"], []).append(r)

    changed_results_affected: list[dict[str, Any]] = []
    result_comparisons: list[dict[str, Any]] = []

    for oid, revs in by_oid.items():
        sorted_revs = sorted(revs, key=lambda x: x["revision"])
        if len(sorted_revs) > 1:
            prev = sorted_revs[-2]
            curr = sorted_revs[-1]
            diff_note = f"Revision {prev['revision']} -> {curr['revision']}: '{prev['content'].get('summary', '')}' changed to '{curr['content'].get('summary', '')}'"
            result_comparisons.append({
                "result_id": oid,
                "prior_revision": prev["revision"],
                "head_revision": curr["revision"],
                "prior_summary": prev["content"].get("summary"),
                "head_summary": curr["content"].get("summary"),
                "difference": diff_note,
            })

            # Find claims that depend on this result
            dependent_claim_ids = []
            for c in claims:
                c_deps = c["content"].get("depends_on", [])
                if oid in c_deps:
                    dependent_claim_ids.append(c["object_id"])

            changed_results_affected.append({
                "changed_result_id": oid,
                "prior_revision": prev["revision"],
                "head_revision": curr["revision"],
                "affected_claims": dependent_claim_ids,
                "status": "REQUIRES_REASSESSMENT" if dependent_claim_ids else "RESOLVED_ISOLATED",
                "recommended_action": f"Re-evaluate claims {dependent_claim_ids} against revised result {oid}:rev{curr['revision']}" if dependent_claim_ids else "No claims dependent on this result.",
            })

    # 4. Robustness Summaries
    robustness_summaries = [
        {
            "category": "Baseline Specification",
            "observation_count": len(results),
            "findings_summary": "; ".join(supported_findings[:3]) if supported_findings else "No empirical findings recorded.",
        }
    ]

    # 5. Reproducibility Metadata (Part B4: No fabricated seeds or assumed verification)
    meta = run_metadata or {}
    has_verified_computation = bool(meta.get("verified_computation") or meta.get("execution_verified"))
    has_dataset_hashes = any(d.get("content_digest") for d in datasets)

    if not has_verified_computation:
        rep_status = "COMPUTATIONAL_REPRODUCTION_NOT_VERIFIED"
    elif not has_dataset_hashes:
        rep_status = "INPUT_PROVENANCE_INCOMPLETE"
    else:
        rep_status = "VERIFIED_COMPUTATIONAL_RUN"

    reproducibility = {
        "runtime_environment": meta.get("environment"),
        "random_seed": meta.get("random_seed"),  # None if not provided
        "script_path": meta.get("script_path"),
        "estimator": meta.get("estimator"),
        "dataset_hashes": [{d["object_id"]: d.get("content_digest")} for d in datasets],
        "deterministic_verification": False if not has_verified_computation else True,
        "reproducibility_status": rep_status,
    }

    result_data = ResultsInterpretationResult(
        structured_run_records=run_records,
        input_and_parameter_references=input_params,
        output_artifact_references=artifact_refs,
        reproducibility_metadata=reproducibility,
        result_comparisons=result_comparisons,
        robustness_summaries=robustness_summaries,
        findings_supported_by_available_outputs=supported_findings,
        changed_results_and_affected_claims=changed_results_affected,
    )

    out_dict = result_data.to_dict()
    out_dict["changed_results_affecting_claims"] = changed_results_affected

    elapsed_ms = int((time.time() - start_time) * 1000)
    usage = TaskUsage(
        workflow="results",
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
    elif rep_status != "VERIFIED_COMPUTATIONAL_RUN":
        status_val = "REQUIRES_VERIFICATION"
    else:
        status_val = "SUCCESS"

    return WorkflowOutput(
        workflow="results",
        task_id=tid,
        project_id=context.project_id,
        status=status_val,
        result=out_dict,
        usage=usage,
        provenance_refs=[{"object_id": o["object_id"], "revision": o["revision"]} for o in context.objects],
        notes=["Results organized with exact parameter references, without fabricated execution metadata."],
    )
