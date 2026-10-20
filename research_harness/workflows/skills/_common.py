"""Shared plumbing of the deterministic executors.

The deterministic executors apply no scientific judgement of their own. They report what the structured data and the
graph relations show, name what is missing, and set a status that never claims more than that:

* `INCOMPLETE_CONTEXT`: the context builder could not fit the evidence;
* `INSUFFICIENT_EVIDENCE`: the evidence the workflow needs is absent from the project;
* `REQUIRES_VERIFICATION`: there is something to report, and interpretation / verification remains;
* `SUCCESS`: every gate the workflow checks is satisfied (still not a verified conclusion).
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from research_harness.workflows.contracts import ContextPackage, TaskUsage, WorkflowOutput
from research_harness.workflows.instrumentation import estimate_tokens
from research_harness.workflows.status import (
    Applicability,
    AssessmentStatus,
    ContextStatus,
    ExecutionStatus,
    Status,
    apply_caps,
)

INCOMPLETE = "INCOMPLETE_CONTEXT"
INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
NEEDS_VERIFICATION = "REQUIRES_VERIFICATION"
SUCCESS = "SUCCESS"


def head_objects(context: ContextPackage, object_type: str) -> list[dict[str, Any]]:
    """Objects of a type at their highest revision present in the context."""
    best: dict[str, dict[str, Any]] = {}
    for o in context.objects:
        if o["object_type"] == object_type and (o["object_id"] not in best or o["revision"] > best[o["object_id"]]["revision"]):
            best[o["object_id"]] = o
    return [best[k] for k in sorted(best)]


def assessed_claims(context: ContextPackage) -> list[dict[str, Any]]:
    """The claims under assessment: explicit focus claims, else every claim scoped to the research question."""
    wanted = set(context.focus_claims or context.scope_claims)
    return [c for c in head_objects(context, "Claim") if c["object_id"] in wanted]


def finish(workflow: str, task_id: str, context: ContextPackage, result: dict[str, Any], status: str, start_time: float,
           notes: list[str], reason: Optional[str] = None) -> WorkflowOutput:
    """Wrap an executor's result: estimated usage (never `provider`: no model was called) and explicit status axes."""
    usage = TaskUsage(
        workflow=workflow, task_id=task_id, model=None, input_tokens=context.estimated_tokens,
        output_tokens=estimate_tokens(json.dumps(result, default=str))[0], cache_read_tokens=None, llm_calls=0,
        context_objects=len(context.objects), reused_artifacts=0, elapsed_ms=int((time.time() - start_time) * 1000),
        usage_source="estimated")
    ctx = ContextStatus(context.context_status)
    if status == INSUFFICIENT:
        ctx = ContextStatus.INSUFFICIENT
    assessment = AssessmentStatus.NOT_ASSESSED if status in {INCOMPLETE, INSUFFICIENT} else apply_caps(
        AssessmentStatus.PROVISIONAL, context=ctx, generated_by="deterministic")
    axes = Status(ExecutionStatus.SUCCEEDED, ctx, assessment, Applicability.APPLICABLE, reason=reason,
                  omissions=tuple(f"{o.get('object_id')}@{o.get('revision')}" for o in context.omitted_items))
    return WorkflowOutput(
        workflow=workflow, task_id=task_id, project_id=context.project_id, status=status, result=result, usage=usage,
        provenance_refs=[{"object_id": o["object_id"], "revision": o["revision"]} for o in context.objects],
        notes=notes, status_axes=axes)


def budget_gate(context: ContextPackage, status: str) -> str:
    """Incomplete evidence in the context outranks any other status."""
    return INCOMPLETE if context.budget_status == "INCOMPLETE_EXCEEDED" else status
