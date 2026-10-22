"""Domain views of a claim inventory: the records an older reading protocol would have produced, derived from the stored claims with no model call (M5 WP6.2, candidate B).

One reading of a paper yields claims with their wording, attribution and role. A view re-labels them for one purpose (landscape entries, introduction-argument components, theory records,
contribution records, method records). It never invents: a protocol field that a claim record cannot fill is listed as `NOT_DERIVABLE` (the paper must be read for it) or `PARTIAL` (a claim
supplies a candidate, and a person or a further reading must decide the field). Merging protocols into one reading therefore costs something real, which `DERIVABILITY` states field by field.

A view also *improves* on the older protocols in one respect: it separates what the authors claim from what they report of earlier work, which `RP_LANDSCAPE` records do not.
"""
from __future__ import annotations

from typing import Any

# older protocol -> field -> (status, how the field is filled or why it cannot be)
DERIVABILITY: dict[str, dict[str, tuple[str, str]]] = {
    "RP_LANDSCAPE": {
        "finding": ("DERIVED", "EMPIRICAL_FINDING held by the authors"), "theoretical_claim": ("DERIVED", "THEORETICAL_PROPOSITION held by the authors"),
        "interpretive_finding": ("DERIVED", "INTERPRETIVE_FINDING held by the authors"), "boundary_condition": ("DERIVED", "BOUNDARY_CONDITION"),
        "stated_limitation": ("DERIVED", "LIMITATION"), "open_question": ("DERIVED", "GAP_STATEMENT"),
        "identifying_assumption": ("PARTIAL", "an ASSUMPTION is a candidate; whether it is one the design relies on, and whether the paper tests it, needs a reading of the design"),
        "measurement": ("PARTIAL", "a METHODOLOGICAL_CLAIM is a candidate; the construct it measures must be named"),
        "design": ("PARTIAL", "a METHODOLOGICAL_CLAIM is a candidate; the design and data regime must be stated apart")},
    "RP_THEORY": {
        "construct_definition": ("DERIVED", "DEFINITION held by the authors"), "stated_assumption": ("DERIVED", "ASSUMPTION held by the authors"),
        "assumed_mechanism": ("PARTIAL", "a claim's mechanism, where the model gave one"), "theory_invoked": ("NOT_DERIVABLE", "naming the theory a paper relies on is not a claim of the paper"),
        "assumption_tested": ("NOT_DERIVABLE", "whether the paper tests an assumption needs a reading of its design")},
    "RP_INTRO_ARGUMENT": {
        "established_knowledge": ("DERIVED", "AUTHOR_REPORTS_PRIOR_WORK claims in introductory sections"), "unresolved_tension": ("DERIVED", "GAP_STATEMENT and AUTHOR_CRITIQUES_PRIOR_WORK claims"),
        "proposed_contribution": ("DERIVED", "CLAIMED_CONTRIBUTION"), "phenomenon": ("NOT_DERIVABLE", "the phenomenon a paper says needs explaining is framing, not a claim"),
        "evidence_needed": ("NOT_DERIVABLE", "the tests the authors say are needed are not extracted as claims")},
    "RP_CONTRIBUTION": {
        "claimed_contribution": ("DERIVED", "CLAIMED_CONTRIBUTION"), "setting": ("PARTIAL", "a claim's stated setting, where there is one"),
        "mechanism": ("PARTIAL", "a claim's mechanism, where the model gave one"), "research_question": ("NOT_DERIVABLE", "a question is not a claim"),
        "design": ("NOT_DERIVABLE", "the design is described, not claimed")},
    "RP_METHOD": {f: ("PARTIAL", "a METHODOLOGICAL_CLAIM is a candidate; which of the method fields it fills is a decision a further reading must make, and the decisive fields need their own fidelity check")
                  for f in ("study_design", "sample_selection", "label_construction", "variable_measurement", "model_or_estimator", "validation_or_tests", "interpretation_rule", "limitation_handling")},
}
_ROLE_FIELD = {"RP_LANDSCAPE": {"EMPIRICAL_FINDING": "finding", "THEORETICAL_PROPOSITION": "theoretical_claim", "INTERPRETIVE_FINDING": "interpretive_finding", "BOUNDARY_CONDITION": "boundary_condition",
                                "LIMITATION": "stated_limitation", "GAP_STATEMENT": "open_question"},
               "RP_THEORY": {"DEFINITION": "construct_definition", "ASSUMPTION": "stated_assumption"},
               "RP_CONTRIBUTION": {"CLAIMED_CONTRIBUTION": "claimed_contribution"}}
VIEWS = tuple(DERIVABILITY)


def derive(view: dict[str, Any], protocol: str) -> dict[str, Any]:
    """The records `protocol` would hold, from the claims of an inventory view. Records carry the claim, Source@revision and passage they come from; `unfilled` lists the fields no claim can fill."""
    if protocol not in DERIVABILITY:
        raise KeyError(f"no view for {protocol}: {sorted(DERIVABILITY)}")
    records: list[dict[str, Any]] = []
    for c in view["claims"]:
        if not c.get("wording") or c["status"] == "REJECTED_BY_RESEARCHER":
            continue
        att, role = c["attribution"].get("value"), c["role"]
        field = None
        if protocol in _ROLE_FIELD:
            field = _ROLE_FIELD[protocol].get(role)
            if field and protocol in ("RP_LANDSCAPE", "RP_THEORY") and att != "AUTHOR_OWN_CLAIM":
                field = None                                   # what the paper reports of earlier work is not what the paper finds, defines or assumes
        elif protocol == "RP_INTRO_ARGUMENT":
            intro = (c["wording"].get("section") or "").lower().startswith(("1", "intro", "abstract", "preamble")) or "introduction" in (c["wording"].get("section") or "").lower()
            if att == "AUTHOR_REPORTS_PRIOR_WORK" and intro:
                field = "established_knowledge"
            elif role == "GAP_STATEMENT" or att == "AUTHOR_CRITIQUES_PRIOR_WORK":
                field = "unresolved_tension"
            elif role == "CLAIMED_CONTRIBUTION":
                field = "proposed_contribution"
        elif protocol == "RP_METHOD" and role == "METHODOLOGICAL_CLAIM" and att == "AUTHOR_OWN_CLAIM":
            field = "method_claim_unclassified"
        if protocol == "RP_LANDSCAPE" and role == "ASSUMPTION" and att == "AUTHOR_OWN_CLAIM":
            field = "identifying_assumption"
        if field:
            status = DERIVABILITY[protocol].get(field, ("PARTIAL", "needs a decision"))[0]
            records.append({"field": field, "value": c["text"], "quote": c["wording"]["quote"], "claim": c["id"], "source_id": c["source_id"], "source_revision": c["source_revision"],
                            "passage_id": c["wording"]["passage_id"], "attribution": att, "derivability": status})
    filled = {r["field"] for r in records}
    return {"protocol": protocol, "records": records, "no_model_call": True,
            "unfilled": {f: {"status": st, "why": why} for f, (st, why) in DERIVABILITY[protocol].items() if f not in filled},
            "fields_not_derivable": sorted(f for f, (st, _) in DERIVABILITY[protocol].items() if st == "NOT_DERIVABLE"),
            "fields_partial": sorted(f for f, (st, _) in DERIVABILITY[protocol].items() if st == "PARTIAL")}
