"""Phase 3A.1: structured evidence fields and the hypothesis/result classification table."""
from __future__ import annotations

import itertools

import pytest

from research_harness.workflows.evidence_fields import (
    CLASSIFICATIONS,
    classify_against_hypothesis,
    lexical_cues,
    parse_hypothesis,
    parse_result,
    result_bears_on_claim,
    review_flag,
    significance,
)


def H(direction="negative", **kw):
    return parse_hypothesis("CLAIM_1", {"predicted_direction": direction, **kw})


def R(estimate=-0.12, p=0.003, alpha=0.05, **kw):
    content = {"estimate": estimate, "alpha": alpha, **kw}
    if p is not None:
        content["p_value"] = p
    return parse_result("RES_1", 1, content)


# ---- truth table: every cell ------------------------------------------------------------------------

TABLE = [
    # predicted, estimate, p,    expected
    ("positive", +0.2, 0.01, "CONSISTENT"), ("positive", -0.2, 0.01, "CHALLENGES"), ("positive", +0.2, 0.40, "INCONCLUSIVE"),
    ("positive", -0.2, 0.40, "INCONCLUSIVE"),
    ("negative", -0.2, 0.01, "CONSISTENT"), ("negative", +0.2, 0.01, "CHALLENGES"), ("negative", -0.2, 0.40, "INCONCLUSIVE"),
    ("negative", +0.2, 0.40, "INCONCLUSIVE"),
    ("no_effect", +0.2, 0.01, "CHALLENGES"), ("no_effect", -0.2, 0.01, "CHALLENGES"),
    ("no_effect", +0.2, 0.40, "COMPATIBLE_NOT_ESTABLISHED"), ("no_effect", -0.2, 0.40, "COMPATIBLE_NOT_ESTABLISHED"),
    ("nondirectional", +0.2, 0.01, "CONSISTENT"), ("nondirectional", -0.2, 0.01, "CONSISTENT"),
    ("nondirectional", +0.2, 0.40, "INCONCLUSIVE"), ("nondirectional", -0.2, 0.40, "INCONCLUSIVE"),
]


@pytest.mark.parametrize("direction,estimate,p,expected", TABLE)
def test_classification_truth_table(direction, estimate, p, expected):
    assert classify_against_hypothesis(H(direction), R(estimate, p)).label == expected


def test_negative_coefficient_that_the_theory_predicts_is_consistent_not_a_challenge():
    c = classify_against_hypothesis(H("negative"), R(-0.12, 0.003))
    assert c.label == "CONSISTENT" and "predicted" in c.note


def test_non_significance_never_refutes_a_directional_prediction_nor_establishes_a_null():
    assert classify_against_hypothesis(H("positive"), R(-0.01, 0.41)).label == "INCONCLUSIVE"
    assert classify_against_hypothesis(H("no_effect"), R(0.01, 0.41)).label == "COMPATIBLE_NOT_ESTABLISHED"


def test_zero_estimate_has_no_direction():
    assert classify_against_hypothesis(H("positive"), R(0.0, 0.001)).label == "INCONCLUSIVE"


def test_the_threshold_is_the_results_own_alpha():
    assert classify_against_hypothesis(H("negative"), R(-0.2, 0.04, alpha=0.05)).label == "CONSISTENT"
    assert classify_against_hypothesis(H("negative"), R(-0.2, 0.04, alpha=0.01)).label == "INCONCLUSIVE"
    assert classify_against_hypothesis(H("negative"), R(-0.2, 0.05, alpha=0.05)).label == "CONSISTENT"  # p <= alpha


# ---- missing / invalid data: NOT_ASSESSED, never defaulted -----------------------------------------------

def test_missing_fields_give_not_assessed_and_name_what_is_missing():
    assert classify_against_hypothesis(parse_hypothesis("C", {}), R()).missing == ("predicted_direction",)
    assert classify_against_hypothesis(H(), parse_result("R", 1, {"alpha": 0.05, "p_value": 0.01})).missing == ("estimate",)
    no_alpha = classify_against_hypothesis(H(), parse_result("R", 1, {"estimate": -1.0, "p_value": 0.01}))
    assert (no_alpha.label, no_alpha.missing) == ("NOT_ASSESSED", ("alpha",))  # there is no default alpha
    no_p = classify_against_hypothesis(H(), parse_result("R", 1, {"estimate": -1.0, "alpha": 0.05}))
    assert no_p.missing == ("p_value_or_ci",)


def test_summary_text_alone_is_not_assessable():
    r = parse_result("R", 1, {"summary": "Coefficient is -0.12 (p<0.01), significant, as predicted."})
    assert classify_against_hypothesis(H(), r).label == "NOT_ASSESSED"


def test_invalid_values_are_reported_not_coerced():
    r = parse_result("R", 1, {"estimate": "-0.12", "p_value": "p<0.01", "alpha": 5, "n": -3, "ci": [2, 1], "provenance": "TRUST_ME"})
    assert set(r.invalid) == {"estimate", "p_value", "alpha", "n", "ci", "provenance"}
    assert r.estimate is None and r.p_value is None and r.provenance is None
    assert parse_result("R", 1, {"p_value": 1.5}).invalid == ("p_value",)
    assert parse_result("R", 1, {"estimate": True}).invalid == ("estimate",)  # a bool is not a number
    bad_direction = parse_hypothesis("C", {"predicted_direction": "up"})
    assert bad_direction.invalid == ("predicted_direction",) and bad_direction.predicted_direction is None


def test_confidence_interval_counts_only_with_a_matching_declared_level():
    ci_only = lambda lo, hi, level: parse_result("R", 1, {"estimate": -0.3, "alpha": 0.05, "ci": [lo, hi], **({"ci_level": level} if level else {})})  # noqa: E731
    assert significance(ci_only(-0.5, -0.1, 0.95)) is True
    assert significance(ci_only(-0.5, 0.1, 0.95)) is False
    assert significance(ci_only(-0.5, -0.1, None)) is None   # level unknown
    assert significance(ci_only(-0.5, -0.1, 0.90)) is None   # level does not match alpha
    c = classify_against_hypothesis(H(), ci_only(-0.5, -0.1, None))
    assert (c.label, c.missing) == ("NOT_ASSESSED", ("ci_level_matching_alpha",))
    assert classify_against_hypothesis(H(), ci_only(-0.5, -0.1, 0.95)).label == "CONSISTENT"


def test_p_value_takes_precedence_over_interval():
    r = parse_result("R", 1, {"estimate": -0.3, "alpha": 0.05, "p_value": 0.2, "ci": [-0.5, -0.1], "ci_level": 0.95})
    assert significance(r) is False


def test_every_cell_returns_a_known_label():
    for d, e, p in itertools.product(["positive", "negative", "no_effect", "nondirectional"], [-1.0, 0.0, 1.0], [0.001, 0.9]):
        assert classify_against_hypothesis(H(d), R(e, p)).label in CLASSIFICATIONS


# ---- linking ------------------------------------------------------------------------------------------

def test_result_bears_on_claim_by_explicit_id_or_relation_in_either_direction():
    r = parse_result("RES_1", 1, {"hypothesis_ids": ["CLAIM_1"]})
    assert result_bears_on_claim(r, "CLAIM_1", []) == "hypothesis_ids"
    plain = parse_result("RES_1", 1, {})
    assert result_bears_on_claim(plain, "CLAIM_1", []) is None
    rel = lambda s, t, k: [{"source_id": s, "target_id": t, "relation": k, "source_revision": 1, "target_revision": 1}]  # noqa: E731
    assert result_bears_on_claim(plain, "CLAIM_1", rel("RES_1", "CLAIM_1", "tests")) == "relation:tests"
    assert result_bears_on_claim(plain, "CLAIM_1", rel("CLAIM_1", "RES_1", "depends_on")) == "relation:depends_on"
    assert result_bears_on_claim(plain, "CLAIM_1", rel("RES_1", "RQ_A", "depends_on")) is None
    assert result_bears_on_claim(plain, "CLAIM_1", rel("RES_1", "CLAIM_1", "derived_from")) is None


# ---- lexical cues are flags only --------------------------------------------------------------------------

def test_lexical_cues_find_words_but_decide_nothing():
    assert "negative" in lexical_cues("A negative coefficient") and "regression" in lexical_cues("OLS regression")
    assert lexical_cues("Independence is associated with fewer restatements.") == []
    assert lexical_cues(None) == [] and lexical_cues(5) == []
    flag = review_flag("EP_1", 2, "This contradicts earlier work; however, only when X.")
    assert flag["kind"] == "lexical_cue" and flag["effect"] == "none" and {"contradict", "however", "only when"} <= set(flag["cues"])
    assert review_flag("EP_1", 1, "Plain sentence.") is None
