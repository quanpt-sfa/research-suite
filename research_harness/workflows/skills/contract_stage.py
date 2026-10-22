"""A stage run under an inquiry contract: report where the stage stands, apply no empirical standard (M5 WP6.1 remediation).

For an empirical project a stage run applies the stage's skill to the recorded Analysis, Dataset and Result objects. For an inquiry whose contract says `assessed_by: steps`
(a critical synthesis, a conceptual argument, a qualitative inquiry), the same stage names a different scientific activity (the review method; the construction of an argument;
interpretation), and reading it against Result objects and coefficients would call a complete review "insufficient evidence". So the run reports the state of the steps the
contract defines for the stage: which apply, which are done and how (`EXECUTED_BY_CAPABILITY`, `DECLARED_IN_RECORD`, `RECORDED_BY_RESEARCHER`), what blocks the rest.

Status: `SUCCESS` when every applicable step is done and none rests only on the researcher's assertion; `REQUIRES_VERIFICATION` when all are done but some only
asserted; `INSUFFICIENT_EVIDENCE` while a step is not done. No model is called and no judgement of the work's quality is made.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from research_harness.workflows.contracts import ContextPackage, WorkflowOutput
from research_harness.workflows.domain_engine import DomainWorkflowSpec, ProjectFacts, evaluate
from research_harness.workflows.skills._common import INSUFFICIENT, NEEDS_VERIFICATION, SUCCESS, finish
from research_harness.workflows.status import Applicability


def execute_contract_stage(context: ContextPackage, *, spec: DomainWorkflowSpec, facts: ProjectFacts, stage: str, task_id: Optional[str] = None) -> WorkflowOutput:
    start = time.time()
    tid = task_id or f"TASK_{stage.upper()}_CONTRACT"
    st = spec.stages[stage]
    rows = [e for e in evaluate(spec, facts) if e.stage == stage]
    steps = []
    for e in rows:
        steps.append({"step": e.step_id, "kind": e.kind, "applies": e.applicability is Applicability.APPLICABLE, "applicability_reason": e.applicability_reason, "done": e.done,
                      "completion": e.completion, "runnable": e.runnable, "blocked_by": e.blocked_by, "reopened_by": e.reopened_by})
    applicable = [x for x in steps if x["applies"]]
    pending = [x["step"] for x in applicable if not x["done"]]
    asserted = [x["step"] for x in applicable if x["done"] and x["completion"] == "RECORDED_BY_RESEARCHER"]
    status = INSUFFICIENT if pending else (NEEDS_VERIFICATION if asserted else SUCCESS)
    result: dict[str, Any] = {"stage": stage, "contract": spec.pin, "activity": st.activity, "manuscript": st.manuscript, "steps": steps, "pending": pending,
                              "done_by_assertion_only": asserted,
                              "completion_kinds": {k: sum(1 for x in applicable if x["completion"] == k) for k in ("EXECUTED_BY_CAPABILITY", "DECLARED_IN_RECORD", "RECORDED_BY_RESEARCHER")},
                              "note": "the state of this stage's steps under the contract; no empirical standard is applied, no quality judgement is made, and a manuscript need not have a section of this name"}
    notes = [f"Stage '{stage}' under {spec.id}: {len(applicable) - len(pending)} of {len(applicable)} step(s) done"
             + (f"; pending: {', '.join(pending)}" if pending else "") + (f"; only asserted by the researcher: {', '.join(asserted)}" if asserted else "")]
    return finish(stage, tid, context, result, status, start, notes, reason="contract_stage")
