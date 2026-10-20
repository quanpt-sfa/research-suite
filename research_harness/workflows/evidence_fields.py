"""Structured evidence fields and the hypothesis-versus-result classification (plan v3, Phase 3A.1).

Scientific verdicts in the deterministic executors come from here, from graph relations, or from nowhere. Wording
never decides anything (invariant I1): a lexical cue may raise a `review_flag`, which changes no status and no
classification.

The fields are optional conventions inside the `content` of existing object types (no schema change). A missing
field is reported as missing and the dependent assessment is `NOT_ASSESSED`; nothing is defaulted. Numbers must be
numbers: a string such as "p<0.01" is not a p-value and is reported as invalid.

    Claim   : predicted_direction (positive|negative|no_effect|nondirectional), focal_variable, outcome_variable,
              hypothesis_label
    Result  : hypothesis_ids[], estimate, std_error, p_value, ci [lo, hi], ci_level, alpha, n, estimator, run_id,
              artifact_sha256, provenance (VERIFIED_RUN | AUTHOR_REPORTED | IMPORTED_FROM_PAPER)
    Analysis: status (PLANNED | EXECUTED), purpose (main | robustness | placebo | heterogeneity),
              identification_strategy, assumptions[], specification {controls, fixed_effects, clustering}
    Dataset : structure (panel | cross_sectional | time_series | experimental | qualitative | mixed), unit, time_index
    Adjudication: scope (claim_support | identification | measurement | novelty), verdict
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

PREDICTED_DIRECTIONS = ("positive", "negative", "no_effect", "nondirectional")
RESULT_PROVENANCE = ("VERIFIED_RUN", "AUTHOR_REPORTED", "IMPORTED_FROM_PAPER")
DATASET_STRUCTURES = ("panel", "cross_sectional", "time_series", "experimental", "qualitative", "mixed")

CLASSIFICATIONS = ("CONSISTENT", "CHALLENGES", "INCONCLUSIVE", "COMPATIBLE_NOT_ESTABLISHED", "NOT_ASSESSED")


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


# ---------------------------------------------------------------------------------------------------
# parsed views
# ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class HypothesisFields:
    claim_id: str
    predicted_direction: Optional[str] = None
    focal_variable: Optional[str] = None
    outcome_variable: Optional[str] = None
    hypothesis_label: Optional[str] = None
    missing: tuple[str, ...] = ()
    invalid: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResultFields:
    result_id: str
    revision: int
    hypothesis_ids: tuple[str, ...] = ()
    estimate: Optional[float] = None
    std_error: Optional[float] = None
    p_value: Optional[float] = None
    ci: Optional[tuple[float, float]] = None
    ci_level: Optional[float] = None
    alpha: Optional[float] = None
    n: Optional[int] = None
    estimator: Optional[str] = None
    run_id: Optional[str] = None
    artifact_sha256: Optional[str] = None
    provenance: Optional[str] = None
    contradictory_flag: bool = False
    missing: tuple[str, ...] = ()
    invalid: tuple[str, ...] = ()

    def structured(self) -> dict[str, Any]:
        """The structured values present, for output records."""
        keys = ("hypothesis_ids", "estimate", "std_error", "p_value", "ci", "ci_level", "alpha", "n", "estimator",
                "run_id", "artifact_sha256", "provenance")
        return {k: getattr(self, k) for k in keys if getattr(self, k) not in (None, ())}


def parse_hypothesis(claim_id: str, content: dict[str, Any]) -> HypothesisFields:
    missing, invalid = [], []
    direction = content.get("predicted_direction")
    if direction is None:
        missing.append("predicted_direction")
    elif direction not in PREDICTED_DIRECTIONS:
        invalid.append("predicted_direction")
        direction = None
    return HypothesisFields(
        claim_id=claim_id, predicted_direction=direction, focal_variable=content.get("focal_variable"),
        outcome_variable=content.get("outcome_variable"), hypothesis_label=content.get("hypothesis_label"),
        missing=tuple(missing), invalid=tuple(invalid))


def parse_result(result_id: str, revision: int, content: dict[str, Any]) -> ResultFields:
    missing, invalid = [], []

    def num(name: str) -> Optional[float]:
        raw = content.get(name)
        if raw is None:
            return None
        value = _number(raw)
        if value is None:
            invalid.append(name)
        return value

    estimate, std_error, p_value, alpha, ci_level = num("estimate"), num("std_error"), num("p_value"), num("alpha"), num("ci_level")
    if p_value is not None and not 0.0 <= p_value <= 1.0:
        invalid.append("p_value")
        p_value = None
    if alpha is not None and not 0.0 < alpha < 1.0:
        invalid.append("alpha")
        alpha = None
    ci = None
    raw_ci = content.get("ci")
    if raw_ci is not None:
        pair = [_number(x) for x in raw_ci] if isinstance(raw_ci, (list, tuple)) and len(raw_ci) == 2 else None
        if pair and None not in pair and pair[0] <= pair[1]:
            ci = (pair[0], pair[1])
        else:
            invalid.append("ci")
    n_raw = content.get("n")
    n = int(n_raw) if isinstance(n_raw, int) and not isinstance(n_raw, bool) and n_raw >= 0 else None
    if n_raw is not None and n is None:
        invalid.append("n")
    provenance = content.get("provenance")
    if provenance is not None and provenance not in RESULT_PROVENANCE:
        invalid.append("provenance")
        provenance = None
    ids = content.get("hypothesis_ids")
    hypothesis_ids = tuple(i for i in ids if isinstance(i, str)) if isinstance(ids, (list, tuple)) else ()
    if estimate is None:
        missing.append("estimate")
    if p_value is None and ci is None:
        missing.append("p_value_or_ci")
    if alpha is None:
        missing.append("alpha")
    return ResultFields(
        result_id=result_id, revision=revision, hypothesis_ids=hypothesis_ids, estimate=estimate, std_error=std_error,
        p_value=p_value, ci=ci, ci_level=ci_level, alpha=alpha, n=n, estimator=content.get("estimator"),
        run_id=content.get("run_id"), artifact_sha256=content.get("artifact_sha256"), provenance=provenance,
        contradictory_flag=content.get("contradictory") is True, missing=tuple(missing), invalid=tuple(invalid))


# ---------------------------------------------------------------------------------------------------
# classification of a result against a hypothesis
# ---------------------------------------------------------------------------------------------------

def significance(result: ResultFields) -> Optional[bool]:
    """Is the estimate significant at the result's own declared `alpha`? None if that cannot be determined.

    Uses the p-value (`p <= alpha`) when given. A confidence interval is used only if its level is declared and
    equals `1 - alpha`; otherwise it says nothing about this alpha. There is no default alpha (decision D6).
    """
    if result.alpha is None:
        return None
    if result.p_value is not None:
        return result.p_value <= result.alpha
    if result.ci is not None and result.ci_level is not None and abs(result.ci_level - (1.0 - result.alpha)) < 1e-9:
        lo, hi = result.ci
        return not (lo <= 0.0 <= hi)
    return None


@dataclass(frozen=True)
class Classification:
    label: str
    missing: tuple[str, ...] = ()
    basis: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"classification": self.label, "missing": list(self.missing), "basis": self.basis, "note": self.note}


def classify_against_hypothesis(h: HypothesisFields, r: ResultFields) -> Classification:
    """How one result bears on one hypothesis, from structured fields only.

    | predicted           | significant, same sign | significant, opposite sign | not significant            |
    | positive / negative | CONSISTENT             | CHALLENGES                 | INCONCLUSIVE               |
    | no_effect           | CHALLENGES             | CHALLENGES                 | COMPATIBLE_NOT_ESTABLISHED |
    | nondirectional      | CONSISTENT             | CONSISTENT                 | INCONCLUSIVE               |

    A non-significant estimate never refutes a directional hypothesis (INCONCLUSIVE), and never establishes a null
    (COMPATIBLE_NOT_ESTABLISHED): absence of significance is not evidence of absence.
    """
    sig = significance(r)
    needed: list[str] = []
    if h.predicted_direction is None:
        needed.append("predicted_direction")
    if r.estimate is None:
        needed.append("estimate")
    if sig is None:
        if r.alpha is None:
            needed.append("alpha")
        elif r.p_value is None and r.ci is None:
            needed.append("p_value_or_ci")
        else:
            needed.append("ci_level_matching_alpha")  # an interval without a declared level equal to 1 - alpha
    if needed:
        return Classification("NOT_ASSESSED", tuple(needed),
                              note="structured fields needed to classify this result are missing or invalid")
    basis = {"predicted_direction": h.predicted_direction, "estimate": r.estimate, "p_value": r.p_value,
             "ci": r.ci, "alpha": r.alpha, "significant": sig}
    sign = (r.estimate > 0) - (r.estimate < 0)
    d = h.predicted_direction
    if d in ("positive", "negative"):
        if not sig:
            return Classification("INCONCLUSIVE", basis=basis,
                                  note="not significant at the declared alpha: neither supports nor refutes the prediction")
        expected = 1 if d == "positive" else -1
        if sign == 0:
            return Classification("INCONCLUSIVE", basis=basis, note="a zero estimate has no direction")
        return Classification("CONSISTENT" if sign == expected else "CHALLENGES", basis=basis,
                              note=f"significant estimate with {'the predicted' if sign == expected else 'the opposite'} sign")
    if d == "no_effect":
        if sig:
            return Classification("CHALLENGES", basis=basis, note="a significant effect was found where none was predicted")
        return Classification("COMPATIBLE_NOT_ESTABLISHED", basis=basis,
                              note="not significant: compatible with no effect, which is not established by non-significance")
    return Classification("CONSISTENT" if sig else "INCONCLUSIVE", basis=basis,
                          note="nondirectional prediction: a significant effect of either sign is consistent")


# ---------------------------------------------------------------------------------------------------
# linking results to claims
# ---------------------------------------------------------------------------------------------------

def result_bears_on_claim(result: ResultFields, claim_id: str, relations: Iterable[dict[str, Any]]) -> Optional[str]:
    """Why a result is considered to bear on a claim: an explicit `hypothesis_ids` entry, or a relation between the
    two (in either direction: the direction of these relations is a convention, not enforced). None if unrelated."""
    if claim_id in result.hypothesis_ids:
        return "hypothesis_ids"
    for rel in relations:
        pair = {rel["source_id"], rel["target_id"]}
        if pair == {result.result_id, claim_id} and rel["relation"] in {"tests", "supports", "challenges", "depends_on"}:
            return f"relation:{rel['relation']}"
    return None


def passage_relationship(passage_id: str, content: dict[str, Any], claim_ids: Iterable[str],
                         relations: Iterable[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """How a passage relates to the claims under assessment: `SUPPORTS_CLAIM`, `CHALLENGES_CLAIM`, `CONFLICTING_LINKS`
    or `NOT_ASSESSED`, from `supports` / `challenges` relations (either direction), an explicit structured
    `assessment.relation`, or the author's `contradictory: true` flag. The passage's wording is never consulted."""
    claim_ids, relations = list(claim_ids), list(relations)
    links: list[dict[str, Any]] = []
    for cid in claim_ids:
        for rel in relations:
            if {rel["source_id"], rel["target_id"]} == {passage_id, cid} and rel["relation"] in {"supports", "challenges"}:
                links.append({"claim_id": cid, "relation": rel["relation"], "basis": f"relation:{rel['relation']}"})
    assessment = content.get("assessment")
    if isinstance(assessment, dict) and assessment.get("relation") in {"supports", "challenges"}:
        links.extend({"claim_id": cid, "relation": assessment["relation"], "basis": "structured_field:assessment.relation"}
                     for cid in claim_ids)
    if content.get("contradictory") is True:
        links.extend({"claim_id": cid, "relation": "challenges", "basis": "author_flag:contradictory"} for cid in claim_ids)
    kinds = {link["relation"] for link in links}
    if kinds == {"supports"}:
        return "SUPPORTS_CLAIM", links
    if kinds == {"challenges"}:
        return "CHALLENGES_CLAIM", links
    if kinds:
        return "CONFLICTING_LINKS", links
    return "NOT_ASSESSED", links


# ---------------------------------------------------------------------------------------------------
# lexical cues: flags for a human, never verdicts
# ---------------------------------------------------------------------------------------------------

_CUES = ("contrary", "contradict", "fails", "fail to", "refute", "zero", "not significant", "insignificant", "opposite",
         "null", "negative", "regression", "persist", "no effect", "conflict", "opposing", "reverse", "limitation",
         "however", "caution", "only when", "unless", "timeout", "degrade")


def lexical_cues(text: Any) -> list[str]:
    """Words in a text that may deserve a reader's attention. Used only to build `review_flags`; the presence or
    absence of a cue never changes a status, a classification, or what is reopened."""
    if not isinstance(text, str):
        return []
    low = text.lower()
    return [c for c in _CUES if re.search(r"\b" + re.escape(c), low)]


def review_flag(object_id: str, revision: int, text: Any, field_name: str = "text") -> Optional[dict[str, Any]]:
    cues = lexical_cues(text)
    if not cues:
        return None
    return {"object_id": object_id, "revision": revision, "field": field_name, "cues": cues, "kind": "lexical_cue",
            "effect": "none"}
