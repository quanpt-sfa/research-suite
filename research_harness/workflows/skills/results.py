"""Workflow 4 - Results Interpretation & Reproducibility (deterministic evidence gate).

Organizes the recorded results, separates what is verified from what is merely reported, and shows what changed
between revisions and which claims that touches.

* A result is `VERIFIED_RUN` only if its `run_id` names an execution run that really succeeded in the run registry
  and its `artifact_sha256` equals that run's recorded output digest. Writing `provenance: VERIFIED_RUN` on the
  result, or passing `run_metadata={"verified_computation": True}`, verifies nothing.
* Everything else is reported as it is: `AUTHOR_REPORTED`, `IMPORTED_FROM_PAPER`, or unrecorded. Unknown seeds,
  scripts and estimators stay `None`.
* Revision comparison is field-level over the structured values, and re-classifies the result against each
  dependent hypothesis, so it shows which claims' status actually changed.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from research_harness.workflows.contracts import ContextPackage, ResultsInterpretationResult, WorkflowOutput
from research_harness.workflows.evidence_fields import (
    classify_against_hypothesis,
    parse_hypothesis,
    parse_result,
    result_bears_on_claim,
    review_flag,
)
from research_harness.workflows.runs import diff_json
from research_harness.workflows.skills._common import (
    INSUFFICIENT,
    NEEDS_VERIFICATION,
    SUCCESS,
    budget_gate,
    finish,
    head_objects,
)


def _verification(fields: Any, verified_runs: dict[str, str]) -> str:
    """VERIFIED_RUN only against a real execution record; a self-declared one is called out."""
    recorded = verified_runs.get(fields.run_id) if fields.run_id else None
    if recorded is not None and fields.artifact_sha256 is not None and recorded == fields.artifact_sha256:
        return "VERIFIED_RUN"
    if fields.provenance == "VERIFIED_RUN":
        return "CLAIMED_VERIFIED_RUN_NOT_FOUND"
    return fields.provenance or "UNVERIFIED"


def execute_results_workflow(
    context: ContextPackage,
    *,
    task_id: Optional[str] = None,
    run_metadata: Optional[dict[str, Any]] = None,
    **kwargs: Any,
) -> WorkflowOutput:
    """Execute the Results evidence gate over structured research context."""
    start_time = time.time()
    tid = task_id or f"TASK_RES_{uuid.uuid4().hex[:8].upper()}"

    results = head_objects(context, "Result")
    datasets = head_objects(context, "Dataset")
    analyses = head_objects(context, "Analysis")
    claims = head_objects(context, "Claim")
    relations = context.relations
    hypotheses = {c["object_id"]: parse_hypothesis(c["object_id"], c["content"]) for c in claims}

    run_records: list[dict[str, Any]] = []
    artifact_refs: list[dict[str, Any]] = []
    verified, reported = [], []
    review_flags: list[dict[str, Any]] = []
    fields_by_id: dict[str, Any] = {}

    for res in results:
        rid, rev, c = res["object_id"], res["revision"], res["content"]
        fields = parse_result(rid, rev, c)
        fields_by_id[rid] = fields
        summary = c.get("summary", "")
        verification = _verification(fields, context.verified_runs)
        run_records.append({
            "result_id": rid, "revision": rev, "label": c.get("label", rid), "summary": summary,
            "metrics": c.get("metrics") or c.get("statistics") or {"summary": summary},
            "analysis_id": c.get("analysis_id"), "contradictory": fields.contradictory_flag,
            "structured": fields.structured(), "verification": verification,
            "missing_structured_fields": list(fields.missing), "invalid_structured_fields": list(fields.invalid),
        })
        if c.get("artifact_sha256"):
            artifact_refs.append({"result_id": rid, "revision": rev, "artifact_sha256": c["artifact_sha256"],
                                  "artifact_type": c.get("artifact_type", "table")})
        if summary:
            (verified if verification == "VERIFIED_RUN" else reported).append(
                {"result_id": rid, "revision": rev, "summary": summary, "verification": verification})
        flag = review_flag(rid, rev, summary, "summary")
        if flag:
            review_flags.append(flag)

    input_params = [{"analysis_id": a["object_id"], "script_path": a["content"].get("script_path"),
                     "estimator": a["content"].get("estimator"), "parameters": a["content"].get("parameters", {}),
                     "status": a["content"].get("status"), "purpose": a["content"].get("purpose")} for a in analyses]

    # Revision comparison: field-level, and which dependent claims' classification moved.
    by_oid: dict[str, list[dict[str, Any]]] = {}
    for r in context.objects:
        if r["object_type"] == "Result":
            by_oid.setdefault(r["object_id"], []).append(r)
    comparisons: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    for oid in sorted(by_oid):
        revs = sorted(by_oid[oid], key=lambda x: x["revision"])
        if len(revs) < 2:
            continue
        prev, curr = revs[-2], revs[-1]
        f_prev, f_curr = parse_result(oid, prev["revision"], prev["content"]), parse_result(oid, curr["revision"], curr["content"])
        comparisons.append({
            "result_id": oid, "prior_revision": prev["revision"], "head_revision": curr["revision"],
            "prior_summary": prev["content"].get("summary"), "head_summary": curr["content"].get("summary"),
            "difference": f"Revision {prev['revision']} -> {curr['revision']}",
            "structured_changes": diff_json(f_prev.structured(), f_curr.structured(), "$.structured")})
        affected, status_moves = [], []
        for cl in claims:
            cid = cl["object_id"]
            bears = result_bears_on_claim(f_curr, cid, relations) or result_bears_on_claim(f_prev, cid, relations) \
                or (oid in (cl["content"].get("depends_on") or []))
            if not bears:
                continue
            affected.append(cid)
            before = classify_against_hypothesis(hypotheses[cid], f_prev).label
            after = classify_against_hypothesis(hypotheses[cid], f_curr).label
            status_moves.append({"claim_id": cid, "classification_before": before, "classification_after": after,
                                 "classification_changed": before != after})
        changed.append({
            "changed_result_id": oid, "prior_revision": prev["revision"], "head_revision": curr["revision"],
            "affected_claims": affected, "claim_status_changes": status_moves,
            "status": "REQUIRES_REASSESSMENT" if affected else "RESOLVED_ISOLATED",
            "recommended_action": (f"Re-evaluate claims {affected} against revised result {oid}:rev{curr['revision']}"
                                   if affected else "No claims depend on this result.")})

    robustness = [{"analysis_id": a["object_id"], "purpose": a["content"].get("purpose"), "status": a["content"].get("status"),
                   "results": [r["object_id"] for r in results if r["content"].get("analysis_id") == a["object_id"]]}
                  for a in analyses if a["content"].get("purpose") in {"robustness", "placebo", "heterogeneity"}]

    meta = run_metadata or {}
    all_verified = bool(results) and len(verified) == len(results)
    reproducibility = {
        # Author-supplied, shown as such. None stays None.
        "runtime_environment": meta.get("environment"), "random_seed": meta.get("random_seed"),
        "script_path": meta.get("script_path"), "estimator": meta.get("estimator"),
        "metadata_source": "author_reported_run_metadata" if meta else "not_provided",
        "dataset_hashes": [{"dataset_id": d["object_id"],
                            "data_sha256": d["content"].get("data_sha256") or d["content"].get("artifact_sha256"),
                            "object_content_digest": d.get("content_digest")} for d in datasets],
        "verified_results": [v["result_id"] for v in verified],
        "unverified_results": [r["object_id"] for r in results if r["object_id"] not in {v["result_id"] for v in verified}],
        "deterministic_verification": all_verified,
        "reproducibility_status": "VERIFIED_COMPUTATIONAL_RUN" if all_verified else "COMPUTATIONAL_REPRODUCTION_NOT_VERIFIED",
    }
    ignored = [f"run_metadata.{k}: asserted by the caller but not accepted without an execution record"
               for k in ("verified_computation", "execution_verified") if meta.get(k)]

    result_data = ResultsInterpretationResult(
        structured_run_records=run_records, input_and_parameter_references=input_params,
        output_artifact_references=artifact_refs, reproducibility_metadata=reproducibility,
        result_comparisons=comparisons, robustness_summaries=robustness,
        findings_supported_by_available_outputs=[f"[{v['result_id']}:rev{v['revision']}] {v['summary']}" for v in verified],
        changed_results_and_affected_claims=changed)
    out = result_data.to_dict()
    out.update({
        "changed_results_affecting_claims": changed, "author_reported_findings": reported, "review_flags": review_flags,
        "ignored_assertions": ignored,
    })

    status = budget_gate(context, INSUFFICIENT if not results else (SUCCESS if all_verified else NEEDS_VERIFICATION))
    return finish("results", tid, context, out, status, start_time,
                  ["Results organized with their verification state; only results backed by an execution record are "
                   "listed as supported by available outputs."])
