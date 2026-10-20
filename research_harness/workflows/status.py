"""Four-axis status model for Research Suite 9.2 (plan v3, section 4.2).

A single combined status conflated "did it run" with "is the scientific conclusion established".
Those are independent facts: an analysis can run successfully while its conclusion stays unverified.
Each axis is recorded separately, and `apply_caps` is the only place that decides how far an
assessment may go.

Contract:
* No code path reaches VERIFIED_WITHIN_SCOPE from a successful run alone. It needs researcher
  confirmation of an evidence-backed item (invariant I3/I16).
* A not-applicable step is NOT_RUN with nothing assessed; it is not an error and not "complete".
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Sequence


class ExecutionStatus(str, Enum):
    """Did the computation or model call technically happen?"""
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    NOT_RUN = "NOT_RUN"
    BLOCKED = "BLOCKED"


class ContextStatus(str, Enum):
    """Was the evidence needed available and within budget?"""
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"


class AssessmentStatus(str, Enum):
    """How far is the scientific conclusion established?"""
    NOT_ASSESSED = "NOT_ASSESSED"
    PROVISIONAL = "PROVISIONAL"
    VERIFIED_WITHIN_SCOPE = "VERIFIED_WITHIN_SCOPE"
    CONTESTED = "CONTESTED"


class Applicability(str, Enum):
    """Does this stage or step apply to this study design?"""
    APPLICABLE = "APPLICABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


EXECUTION_REASONS = frozenset({
    "exception", "invalid_output", "budget_refused", "missing_runtime", "upstream_failed", "not_applicable", "refusal",
    "entry_conditions_unmet",
})

# Rank used for "may not exceed" comparisons. CONTESTED is a state, not a rank.
_RANK = {
    AssessmentStatus.NOT_ASSESSED: 0,
    AssessmentStatus.PROVISIONAL: 1,
    AssessmentStatus.VERIFIED_WITHIN_SCOPE: 2,
}

_SUPPORTING_ECA = frozenset({"SUPPORTED_AS_STATED", "SUPPORTED_IF_QUALIFIED"})


@dataclass(frozen=True)
class Status:
    """Four independent status axes plus the reason and scope that qualify them."""
    execution: ExecutionStatus = ExecutionStatus.NOT_RUN
    context: ContextStatus = ContextStatus.INSUFFICIENT
    assessment: AssessmentStatus = AssessmentStatus.NOT_ASSESSED
    applicability: Applicability = Applicability.APPLICABLE
    reason: Optional[str] = None
    scope: Optional[dict[str, Any]] = None
    omissions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution": self.execution.value,
            "context": self.context.value,
            "assessment": self.assessment.value,
            "applicability": self.applicability.value,
            "reason": self.reason,
            "scope": self.scope,
            "omissions": list(self.omissions),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Status":
        return cls(
            execution=ExecutionStatus(data["execution"]),
            context=ContextStatus(data["context"]),
            assessment=AssessmentStatus(data["assessment"]),
            applicability=Applicability(data.get("applicability", "APPLICABLE")),
            reason=data.get("reason"),
            scope=data.get("scope"),
            omissions=tuple(data.get("omissions") or ()),
        )


def is_model_generated(generated_by: Optional[str]) -> bool:
    return bool(generated_by) and (generated_by == "model" or generated_by.startswith("model:"))


def _min_assessment(a: AssessmentStatus, b: AssessmentStatus) -> AssessmentStatus:
    return a if _RANK[a] <= _RANK[b] else b


def apply_caps(
    requested: AssessmentStatus,
    *,
    execution: ExecutionStatus = ExecutionStatus.SUCCEEDED,
    context: ContextStatus = ContextStatus.COMPLETE,
    applicability: Applicability = Applicability.APPLICABLE,
    generated_by: Optional[str] = None,
    eca_verdict: Optional[str] = None,
    counter_evidence: bool = False,
    researcher_confirmed: bool = False,
    upstream: Sequence[AssessmentStatus] = (),
) -> AssessmentStatus:
    """Return the highest assessment the evidence allows.

    Rules, evaluated in order:
      1. not applicable, or execution did not succeed          -> NOT_ASSESSED
      2. context INSUFFICIENT                                  -> NOT_ASSESSED
      3. unresolved counter-evidence, or ECA NOT_SUPPORTED     -> CONTESTED
      4. nothing requested above NOT_ASSESSED                  -> NOT_ASSESSED
      5. context PARTIAL                                       -> at most PROVISIONAL
      6. VERIFIED needs researcher confirmation                -> otherwise PROVISIONAL
      7. a model-generated statement also needs a supporting ECA verdict for VERIFIED;
         an UNRESOLVED ECA verdict also keeps it PROVISIONAL
      8. a CONTESTED upstream item caps the result at PROVISIONAL; otherwise the result may not
         exceed the weakest upstream assessment
    """
    if applicability is Applicability.NOT_APPLICABLE or execution is not ExecutionStatus.SUCCEEDED:
        return AssessmentStatus.NOT_ASSESSED
    if context is ContextStatus.INSUFFICIENT:
        return AssessmentStatus.NOT_ASSESSED
    if counter_evidence or eca_verdict == "NOT_SUPPORTED" or requested is AssessmentStatus.CONTESTED:
        return AssessmentStatus.CONTESTED
    if requested is AssessmentStatus.NOT_ASSESSED:
        return AssessmentStatus.NOT_ASSESSED

    result = requested
    if context is ContextStatus.PARTIAL:
        result = _min_assessment(result, AssessmentStatus.PROVISIONAL)
    if result is AssessmentStatus.VERIFIED_WITHIN_SCOPE:
        if not researcher_confirmed:
            result = AssessmentStatus.PROVISIONAL
        elif is_model_generated(generated_by) and eca_verdict not in _SUPPORTING_ECA:
            result = AssessmentStatus.PROVISIONAL
        elif eca_verdict == "UNRESOLVED":
            result = AssessmentStatus.PROVISIONAL
    for up in upstream:
        if up is AssessmentStatus.CONTESTED:
            result = _min_assessment(result, AssessmentStatus.PROVISIONAL)
        else:
            result = _min_assessment(result, up)
    return result


def normalize_status(status: Status) -> Status:
    """Enforce cross-axis consistency.

    A not-applicable step is NOT_RUN with nothing assessed; nothing is assessed when execution did not
    succeed.
    """
    if status.applicability is Applicability.NOT_APPLICABLE:
        return Status(
            execution=ExecutionStatus.NOT_RUN, context=status.context, assessment=AssessmentStatus.NOT_ASSESSED,
            applicability=Applicability.NOT_APPLICABLE, reason="not_applicable", scope=status.scope,
            omissions=status.omissions,
        )
    if status.execution is not ExecutionStatus.SUCCEEDED and status.assessment is not AssessmentStatus.NOT_ASSESSED:
        return Status(
            execution=status.execution, context=status.context, assessment=AssessmentStatus.NOT_ASSESSED,
            applicability=status.applicability, reason=status.reason, scope=status.scope, omissions=status.omissions,
        )
    return status


def axes_from_legacy(legacy_status: str) -> Status:
    """Map a 9.1.1 combined status string onto the four axes (one-release compatibility adapter).

    Deliberately conservative: a legacy `SUCCESS` says only that code ran, so it maps to NOT_ASSESSED,
    never to a verified conclusion.
    """
    s = (legacy_status or "").upper()
    ok = ExecutionStatus.SUCCEEDED
    if s in {"SUCCESS", "REUSED"}:
        return Status(ok, ContextStatus.COMPLETE, AssessmentStatus.NOT_ASSESSED, Applicability.APPLICABLE,
                      reason="legacy_status_" + s.lower())
    if s == "REQUIRES_VERIFICATION":
        return Status(ok, ContextStatus.COMPLETE, AssessmentStatus.PROVISIONAL, Applicability.APPLICABLE,
                      reason="legacy_requires_verification")
    if s == "INCOMPLETE_CONTEXT":
        return Status(ok, ContextStatus.PARTIAL, AssessmentStatus.NOT_ASSESSED, Applicability.APPLICABLE,
                      reason="legacy_incomplete_context")
    if s == "INSUFFICIENT_EVIDENCE":
        return Status(ok, ContextStatus.INSUFFICIENT, AssessmentStatus.NOT_ASSESSED, Applicability.APPLICABLE,
                      reason="legacy_insufficient_evidence")
    if s == "INVALID_OUTPUT":
        return Status(ExecutionStatus.FAILED, ContextStatus.COMPLETE, AssessmentStatus.NOT_ASSESSED,
                      Applicability.APPLICABLE, reason="invalid_output")
    if s == "NOT_APPLICABLE":
        return Status(ExecutionStatus.NOT_RUN, ContextStatus.COMPLETE, AssessmentStatus.NOT_ASSESSED,
                      Applicability.NOT_APPLICABLE, reason="not_applicable")
    if s == "BLOCKED":
        return Status(ExecutionStatus.BLOCKED, ContextStatus.INSUFFICIENT, AssessmentStatus.NOT_ASSESSED,
                      Applicability.APPLICABLE, reason="entry_conditions_unmet")
    if s == "FAILED":
        return Status(ExecutionStatus.FAILED, ContextStatus.INSUFFICIENT, AssessmentStatus.NOT_ASSESSED,
                      Applicability.APPLICABLE, reason="exception")
    raise ValueError(f"Unknown legacy workflow status: {legacy_status!r}")
