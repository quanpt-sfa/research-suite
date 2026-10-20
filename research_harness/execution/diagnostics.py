"""Statistical diagnostics, robustness divergence and non-significance readings (plan v3, 3E, invariant I17).

Only *execution checks* are pass/fail (that is `dag.py`). Everything here is **evidence with a stated basis**:

* a diagnostic yields a `DiagnosticRecord`: the values, every reference criterion with the source it comes from and what
  that criterion alone indicates, a plain reading, and a signal. Criteria may disagree; then the signal is `INCONCLUSIVE`
  and all of them are shown. A concern never stops a run and a lack of concern is never called "PASS".
* a robustness result that differs from the baseline is a `Divergence`: a measured magnitude, and, only if the plan declares
  a tolerance with its basis, whether it is within it. It is evidence about the claim, not a failed step.
* a non-significant result gets readings (`UNDERPOWERED`, `IMPRECISE`, `NEGLIGIBLE_EFFECT_SUPPORTED`, `MEASUREMENT_CONCERN`)
  computed from a smallest effect of interest and an alpha that the plan declared with a basis. Nothing is defaulted: with
  either missing the reading is `INCONCLUSIVE_REASON_NOT_ASSESSED`, naming what is missing. None of them says "refutes".
"""
from __future__ import annotations

import operator
from typing import Any, Optional

from research_harness import taxonomy

OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le, "==": operator.eq, "!=": operator.ne}


class DiagnosticSpecError(ValueError):
    """A diagnostic or criterion that is not well-formed (for example a threshold with no source)."""


def dig(data: dict[str, Any], path: str) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _has_basis(basis: Any) -> bool:
    return bool(basis) and (not isinstance(basis, (list, tuple)) or all(bool(b) for b in basis))


def evaluate_diagnostic(spec: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    """One `DiagnosticRecord` from a spec `{diagnostic, value_key, criteria: [{name, concern_if: {op, threshold}, basis}]}`."""
    name = spec.get("diagnostic")
    if not name or not spec.get("value_key"):
        raise DiagnosticSpecError("a diagnostic needs a name and the metrics key of its value")
    if not spec.get("criteria"):
        raise DiagnosticSpecError(f"diagnostic {name!r} has no reference criteria: thresholds are never defaulted")
    value = dig(metrics, spec["value_key"])
    criteria, signals = [], []
    for c in spec["criteria"]:
        cond = c.get("concern_if") or {}
        if c.get("name") is None or cond.get("op") not in OPS or "threshold" not in cond:
            raise DiagnosticSpecError(f"diagnostic {name!r}: a criterion needs a name and concern_if {{op in {sorted(OPS)}, threshold}}")
        if not _has_basis(c.get("basis")):
            raise DiagnosticSpecError(f"diagnostic {name!r}, criterion {c['name']!r}: a criterion needs the source it comes from (basis)")
        if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
            sig = "NOT_COMPUTABLE"
        else:
            sig = "CONCERN_INDICATED" if OPS[cond["op"]](value, cond["threshold"]) else "NO_CONCERN_INDICATED"
        signals.append(sig)
        criteria.append({"criterion": c["name"], "concern_if": cond, "basis": c["basis"], "signal": sig})
    computable = [s for s in signals if s != "NOT_COMPUTABLE"]
    if not computable:
        overall, disagree = "NOT_COMPUTABLE", False
    elif len(set(computable)) == 1:
        overall, disagree = computable[0], False
    else:
        overall, disagree = "INCONCLUSIVE", True
    taxonomy.validate_term("diagnostic_signal", overall)
    reading = (f"{name} = {value}: " + "; ".join(
        f"{c['criterion']} (concern if {c['concern_if']['op']} {c['concern_if']['threshold']}) indicates "
        f"{'a concern' if c['signal'] == 'CONCERN_INDICATED' else 'no concern' if c['signal'] == 'NO_CONCERN_INDICATED' else 'nothing: not computable'}"
        for c in criteria) + (". The criteria disagree; all are shown." if disagree else "."))
    return {"diagnostic": name, "values": {spec["value_key"]: value}, "reference_criteria": criteria, "reading": reading,
            "signal": overall, "criteria_disagree": disagree}


def divergence(baseline: dict[str, Any], robust: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """A robustness divergence: how far a robustness result is from the baseline, as evidence.

    `spec = {dimension, metric, baseline_step, tolerance?, tolerance_basis?, interpretation_if_holds?, interpretation_if_fails?}`.
    Without a declared tolerance and basis no outcome is claimed and no interpretation is chosen.
    """
    dim = spec.get("dimension")
    if dim not in ("measurement", "sample", "specification", "period"):
        raise DiagnosticSpecError("a divergence has a dimension: measurement, sample, specification or period")
    base, val = dig(baseline, spec["metric"]), dig(robust, spec["metric"])
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (base, val)):
        return {"dimension": dim, "metric": spec["metric"], "baseline_step": spec.get("baseline_step"), "magnitude": None,
                "outcome": "NOT_COMPUTABLE", "interpretation": None}
    mag = abs(val - base)
    out: dict[str, Any] = {"dimension": dim, "metric": spec["metric"], "baseline_step": spec.get("baseline_step"), "baseline_value": base,
                           "value": val, "magnitude": mag, "relative_magnitude": (mag / abs(base)) if base else None,
                           "sign_changed": (base > 0) != (val > 0) if base and val else False}
    tol = spec.get("tolerance")
    if tol is None or not _has_basis(spec.get("tolerance_basis")):
        out.update(outcome="NO_RULE_DECLARED", interpretation=None,
                   note="no tolerance with a basis was declared for this check, so no reading is chosen")
    else:
        holds = mag <= tol
        out.update(outcome="WITHIN_DECLARED_TOLERANCE" if holds else "EXCEEDS_DECLARED_TOLERANCE", tolerance=tol, tolerance_basis=spec["tolerance_basis"],
                   interpretation=spec.get("interpretation_if_holds") if holds else spec.get("interpretation_if_fails"))
    return out


def nonsignificance_reading(values: dict[str, Any], declared: dict[str, Any], *, measurement_concern: bool = False) -> dict[str, Any]:
    """Readings of a result that is not significant at the declared alpha. `values`: estimate, p_value, ci_low, ci_high, mde,
    equivalence_p. `declared`: sesoi, alpha, basis. Returns the readings that hold, each with its basis, or the reason none can be given."""
    missing = [k for k in ("sesoi", "alpha") if declared.get(k) is None]
    if missing or not _has_basis(declared.get("basis")):
        need = missing + ([] if _has_basis(declared.get("basis")) else ["basis"])
        return {"significant": None, "readings": [{"reading": "INCONCLUSIVE_REASON_NOT_ASSESSED",
                                                   "why": "the plan has not declared " + ", ".join(need) + " (no defaults are used)"}]}
    sesoi, alpha = declared["sesoi"], declared["alpha"]
    lo, hi, p = values.get("ci_low"), values.get("ci_high"), values.get("p_value")
    contains_zero = lo is not None and hi is not None and lo <= 0 <= hi
    significant = (p < alpha) if p is not None else (not contains_zero if lo is not None and hi is not None else None)
    if significant is None:
        return {"significant": None, "readings": [{"reading": "INCONCLUSIVE_REASON_NOT_ASSESSED", "why": "neither a p-value nor a confidence interval is available"}]}
    if significant:
        return {"significant": True, "readings": []}
    basis = declared["basis"]
    readings: list[dict[str, Any]] = []
    mde = values.get("mde")
    if mde is not None and mde > sesoi:
        readings.append({"reading": "UNDERPOWERED", "because": f"the minimum detectable effect {mde} exceeds the smallest effect of interest {sesoi}", "basis": basis})
    if contains_zero and lo is not None and (lo <= sesoi <= hi or lo <= -sesoi <= hi):
        readings.append({"reading": "IMPRECISE", "because": f"the interval [{lo}, {hi}] contains both zero and the smallest effect of interest {sesoi}", "basis": basis})
    eq = values.get("equivalence_p")
    if eq is not None and eq < alpha:
        readings.append({"reading": "NEGLIGIBLE_EFFECT_SUPPORTED", "because": f"the equivalence test (p = {eq}) is below alpha {alpha}", "basis": basis})
    if measurement_concern:
        readings.append({"reading": "MEASUREMENT_CONCERN", "because": "a measurement diagnostic indicated a concern", "basis": basis})
    if not readings:
        need = [k for k in ("mde", "ci_low", "ci_high", "equivalence_p") if values.get(k) is None]
        readings.append({"reading": "INCONCLUSIVE_REASON_NOT_ASSESSED", "why": "no reading follows from the values given; missing: " + ", ".join(need or ["none"])})
    return {"significant": False, "readings": readings}
