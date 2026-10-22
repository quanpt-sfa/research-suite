"""WP6.1 remediation, Phase A: one regression test per reported problem, written against the code as it stood at b631b64. Each fails there; the fix makes it pass.
The fuller acceptance tests are in tests/test_remediation_contracts.py, test_remediation_recognition.py, test_remediation_pinning.py and test_remediation_basis.py.
"""
from __future__ import annotations

import pytest

from research_harness.discovery import landscape as L
from research_harness.discovery import problem as P
from research_harness.workflows.adapters import ScriptedModelAdapter
from research_harness.workflows.domain_engine import ProjectFacts, facts_from_store, load_builtin_spec, stage_status
from research_harness.workflows.status import Applicability
from tests import inquiry_fixtures as F


def synth(proposals):
    return ScriptedModelAdapter(model_id="scripted-synthesiser", default_response={"proposals": proposals})


@pytest.fixture
def wa(tmp_path):
    w = F.build_world(tmp_path, name="PRJ_A", papers=F.A_PAPERS, statement=F.A_STATEMENT, terms=F.A_TERMS, inquiry=F.A_INQUIRY)
    w["lid"] = L.build(w["store"], w["token"], w["rid"], F.reader(F.A_RECORDS), versions="all")["landscape_id"]
    w["v"] = L.view(w["store"], w["lid"])
    return w


def test_problem_1_methods_and_results_are_not_declared_not_applicable_for_a_critical_review():
    """Not applying a regression is not the same as having no method or no findings."""
    spec = load_builtin_spec("LIB_DW_CRITICAL_SYNTHESIS")
    f = ProjectFacts(study_design="systematic_review")
    f.research_purpose = "critical_synthesis"
    for stage in ("methods", "results"):
        assert stage_status(spec, f, stage).applicability is Applicability.APPLICABLE
    qual = load_builtin_spec("LIB_DW_QUALITATIVE_INQUIRY")
    fq = ProjectFacts(study_design="design_based")
    fq.research_purpose = "interpretation"
    assert stage_status(qual, fq, "results").applicability is Applicability.APPLICABLE


def test_problem_2_a_proposal_without_addressed_by_is_not_a_finding_of_absence(wa):
    v = wa["v"]
    e = {x["field"]: x["id"] for x in v["entries"] if x["source_id"] == "SRC_ATT"}
    other = next(x["id"] for x in v["entries"] if x["source_id"] == "SRC_ENG1" and x["field"] == "stated_assumption")
    p = {"kind": "conceptual_divergence", "topic": "t", "entries": [e["stated_assumption"], other], "shared": "s", "differences": "d", "rationale": "r", "missing_information": ["m"],
         "levels": {}, "meaning_difference": "md", "already_addressed_by": [], "search_terms": ["participation"]}
    out = L.propose_relations(wa["store"], wa["token"], wa["lid"], synth([p]))
    rec = next(i for i in L.view(wa["store"], wa["lid"])["inferences"] if i["id"] == out["accepted"][0])["recognition"]
    assert rec["status"] == "RECOGNITION_NOT_ASSESSED"


def test_problem_3_a_promoted_question_keeps_the_inquiry_of_the_problem_revision_it_was_promoted_from(tmp_path):
    w = F.build_world(tmp_path, name="PRJ_P", papers=F.A_PAPERS, statement=F.A_STATEMENT, terms=F.A_TERMS, inquiry=F.A_INQUIRY, search=False)
    s, t, pid = w["store"], w["token"], w["pid"]
    P.add_interpretation(s, t, pid, "AI assistance separates producing a message from investing in a relationship.")
    P.choose(s, t, pid, ["I1"], "the reading pursued")
    rq = P.promote_to_rq(s, t, pid, "I1", "How does AI-assisted communication separate participation from engagement at work?", "my wording")["research_question"]
    P.revise_problem(s, t, pid, rationale="the study became an evaluation", inquiry={"research_purposes": ["evaluation"], "research_design": "design_based"})
    facts = facts_from_store(s, research_question_id=rq)
    assert facts.purposes() == {"critical_synthesis", "theory_development"} and facts.study_design == "narrative_review"


def test_problem_4_the_researcher_verdict_and_the_verification_of_its_basis_are_shown_apart(wa):
    claim = next(e["id"] for e in wa["v"]["entries"] if e["source_id"] == "SRC_ATT" and e["field"] == "theoretical_claim")
    L.assess_method(wa["store"], wa["token"], wa["lid"], claim, verdict="SUPPORTED_WITHIN_SCOPE", threats=[{"threat": "rival", "status": "addressed"}], rationale="r",
                    criteria=[{"criterion": "valid derivation", "basis": "the paper's derivation", "basis_kind": "logical_analysis", "met": "yes"}])
    inf = next(e for e in L.view(wa["store"], wa["lid"])["entries"] if e["id"] == claim)["layers"]["evidence"]["inference"]
    assert inf["verdict"] == "SUPPORTED_WITHIN_SCOPE" and inf["assessed_by"] == "RESEARCHER_ASSESSED"
    assert inf["verification"]["status"] == "BASIS_NOT_TRACEABLE"                      # a basis_kind and a string are not a traceable basis
