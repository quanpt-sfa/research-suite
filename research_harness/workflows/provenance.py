"""Provenance envelope: who generated a statement is NOT what supports it (plan v3, invariant I9).

`generated_by` records the producer of a statement (researcher, a model, a deterministic module, an import or
legacy output). `evidence_basis` records what supports it (a verified source passage, replication code, a
project run, a journal policy, ...). The two are separate attributes: a model can generate a statement that
rests on strong evidence, but the statement's assessment stays capped until it is adjudicated and confirmed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from research_harness import taxonomy
from research_harness.workflows.status import AssessmentStatus, apply_caps, is_model_generated


class MissingEvidenceBasis(ValueError):
    """Raised when a statement that must carry evidence has none (I9)."""




def validate_generated_by(value: str) -> str:
    """Accept `kind` or `kind:<descriptor>` where `kind` is in the `generated_by` vocabulary."""
    kind, _, descriptor = value.partition(":")
    taxonomy.validate_term("generated_by", kind)
    if kind in {"model", "deterministic"} and ":" in value and not descriptor:
        raise taxonomy.TaxonomyError(f"generated_by '{value}' has an empty descriptor")
    return value


@dataclass(frozen=True)
class Basis:
    """One piece of evidence supporting a statement."""
    kind: str
    ref: str
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    quote_verified: Optional[bool] = None
    retrieved_at: Optional[str] = None
    note: Optional[str] = None

    def __post_init__(self) -> None:
        taxonomy.validate_term("evidence_basis_kind", self.kind)
        if not self.ref:
            raise ValueError("Basis.ref must identify the evidence (object id + revision, run id, policy pin, ...)")
        if (self.char_start is None) != (self.char_end is None):
            raise ValueError("Basis.char_start and Basis.char_end must be given together")
        if self.char_start is not None and not (0 <= self.char_start <= self.char_end):  # type: ignore[operator]
            raise ValueError("Basis character span must satisfy 0 <= char_start <= char_end")

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in {
            "kind": self.kind, "ref": self.ref, "char_start": self.char_start, "char_end": self.char_end,
            "quote_verified": self.quote_verified, "retrieved_at": self.retrieved_at, "note": self.note,
        }.items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Basis":
        return cls(**{k: data.get(k) for k in (
            "kind", "ref", "char_start", "char_end", "quote_verified", "retrieved_at", "note")})


@dataclass
class Statement:
    """A claim-bearing statement with its producer and its evidence kept apart."""
    text: str
    generated_by: str
    evidence_basis: list[Basis] = field(default_factory=list)
    eca_ref: Optional[str] = None
    eca_verdict: Optional[str] = None
    requested_assessment: AssessmentStatus = AssessmentStatus.PROVISIONAL
    researcher_confirmed: bool = False

    def __post_init__(self) -> None:
        validate_generated_by(self.generated_by)
        if self.eca_verdict is not None:
            taxonomy.validate_term("eca_verdict", self.eca_verdict)

    @property
    def assessment(self) -> AssessmentStatus:
        """Effective assessment after the cap rules (never VERIFIED from generation alone)."""
        if not self.evidence_basis:
            return AssessmentStatus.NOT_ASSESSED
        return apply_caps(
            self.requested_assessment,
            generated_by=self.generated_by,
            eca_verdict=self.eca_verdict,
            researcher_confirmed=self.researcher_confirmed,
        )

    @property
    def model_generated(self) -> bool:
        return is_model_generated(self.generated_by)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "generated_by": self.generated_by,
            "evidence_basis": [b.to_dict() for b in self.evidence_basis],
            "eca_ref": self.eca_ref,
            "eca_verdict": self.eca_verdict,
            "assessment": self.assessment.value,
            "researcher_confirmed": self.researcher_confirmed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Statement":
        return cls(
            text=data["text"],
            generated_by=data["generated_by"],
            evidence_basis=[Basis.from_dict(b) for b in data.get("evidence_basis") or []],
            eca_ref=data.get("eca_ref"),
            eca_verdict=data.get("eca_verdict"),
            requested_assessment=AssessmentStatus(data.get("requested_assessment") or AssessmentStatus.PROVISIONAL.value),
            researcher_confirmed=bool(data.get("researcher_confirmed")),
        )


def require_evidence_basis(statement: Statement, *, what: str = "statement") -> Statement:
    """Enforce I9: playbook steps, criteria and interpretation rules must have at least one evidence basis."""
    if not statement.evidence_basis:
        raise MissingEvidenceBasis(
            f"{what} '{statement.text[:60]}' has no evidence basis; generated_by='{statement.generated_by}' is "
            f"the producer, not evidence. Attach a source passage, replication code, project run, policy or an "
            f"explicit researcher_judgement."
        )
    return statement
