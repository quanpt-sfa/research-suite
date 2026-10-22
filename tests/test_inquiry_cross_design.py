"""M5 WP6.1: research-design-aware scientific synthesis. One Research Core, three independent kinds of inquiry (critical synthesis, education, archival/predictive modeling).

Synthetic acceptance only: invented papers, scripted readers and scripted proposers. Nothing here validates scientific quality on real literature (REAL_CORPUS_VALIDATION_PENDING).
"""
from __future__ import annotations

import json

import jsonschema
import pytest

from research_harness import taxonomy
from research_harness.cli import main
from research_harness.core.errors import AuthorizationError
from research_harness.discovery import landscape as L
from research_harness.discovery import problem as P
from research_harness.discovery import retrieval as RT
from research_harness.export.landscape import build_landscape_export
from research_harness.knowledge import corpus
from research_harness.knowledge.common import KnowledgeError
from research_harness.knowledge.replication import add_replication_package, record_reproduction
from research_harness.protocols import schema
from research_harness.state.actors import register_actor
from research_harness.tasks import engine as T
from research_harness.validation.integrity import audit_project_integrity
from research_harness.workflows import eca
from research_harness.workflows.adapters import ScriptedModelAdapter
from research_harness.workflows.dispatcher import run_workflow
from research_harness.workflows.domain_engine import (DomainWorkflowSpec, ProjectFacts, SpecError, evaluate_predicate, facts_from_store, list_builtin_specs, load_builtin_spec,
                                                       next_steps, record_step_run, stage_status)
from research_harness.workflows.status import Applicability
from tests import inquiry_fixtures as F
from tests import landscape_fixture as LF

ADJ_OK = {"passage_proposition": "As quoted.", "equivalence": "EQUIVALENT", "dropped_conditions": [], "cue_handling": [], "verdict": "SUPPORTED_AS_STATED",
          "required_qualification": [], "rationale": "ok"}


def synth(proposals):
    return ScriptedModelAdapter(model_id="scripted-synthesiser", default_response={"proposals": proposals})


def ent(v, source, field, label=None):
    return next(e for e in v["entries"] if e["source_id"] == source and e["field"] == field and (label is None or e.get("label") == label))


def inf(v, iid):
    return next(i for i in v["inferences"] if i["id"] == iid)


@pytest.fixture
def wa(tmp_path):
    w = F.build_world(tmp_path, name="PRJ_A", papers=F.A_PAPERS, statement=F.A_STATEMENT, terms=F.A_TERMS, inquiry=F.A_INQUIRY)
    w["lid"] = L.build(w["store"], w["token"], w["rid"], F.reader(F.A_RECORDS), versions="all")["landscape_id"]
    w["v"] = L.view(w["store"], w["lid"])
    return w


@pytest.fixture
def wb(tmp_path):
    w = F.build_world(tmp_path, name="PRJ_B", papers=F.B_PAPERS, statement=F.B_STATEMENT, terms=F.B_TERMS, inquiry=F.B_INQUIRY)
    w["lid"] = L.build(w["store"], w["token"], w["rid"], F.reader(F.B_RECORDS))["landscape_id"]
    w["v"] = L.view(w["store"], w["lid"])
    return w


@pytest.fixture
def wc(tmp_path):
    w = F.build_world(tmp_path, name="PRJ_C", papers=F.C_PAPERS, statement=F.C_STATEMENT, terms=F.C_TERMS, inquiry=F.C_INQUIRY)
    w["lid"] = L.build(w["store"], w["token"], w["rid"], F.reader(F.C_RECORDS))["landscape_id"]
    w["v"] = L.view(w["store"], w["lid"])
    return w


def add_rq(w, **content):
    with w["store"].write_tx(token=w["token"]) as tx:
        tx.add_revision(object_id="RQ_X", object_type="ResearchQuestion", content={"text": "A research question for the workflow tests.", **content}, origin={}, edges=[])


# ================================================================================================ the inquiry is recorded, optional and does not move retrieval

def test_inquiry_is_optional_validated_and_recorded_on_the_problem(tmp_path):
    w = F.build_world(tmp_path, name="PRJ_I", papers=F.A_PAPERS, statement=F.A_STATEMENT, terms=F.A_TERMS)
    assert "inquiry" not in w["store"].get_object(w["pid"])["content"]                              # a problem made without one is exactly as before
    bad = [{"research_purposes": ["win_a_prize"]}, {"research_design": "alchemy"}, {"evidence_forms": ["vibes"]}, {"discipline": {"domain": "astrology"}}, {"nothing": 1}]
    for inq in bad:
        with pytest.raises(Exception):
            P.new_problem(w["store"], w["token"], statement="A statement long enough to be accepted.", inquiry=inq)
    out = P.revise_problem(w["store"], w["token"], w["pid"], rationale="record what kind of inquiry this is", inquiry=F.A_INQUIRY)
    c = w["store"].get_object(w["pid"])["content"]
    assert out["revision"] == 2 and c["inquiry"]["research_purposes"] == ["critical_synthesis", "theory_development"] and P.problem_view(w["store"], w["pid"])["inquiry"] == c["inquiry"]
    jsonschema.validate(c, schema.content_schema("ResearchProblem"))
    # the purpose does not change what the queries were made from: the run stays current for the problem
    assert RT.run_view(w["store"], w["rid"])["currency"]["problem"]["status"] == "CURRENT"


def test_the_four_dimensions_are_separate_vocabularies():
    for name in ("research_purpose", "evidence_form", "claim_kind", "relation_kind", "basis_kind", "proposal_provenance"):
        assert taxonomy.vocabulary(name)
    assert {"critical_synthesis", "theory_development", "prediction", "interpretation"} <= set(taxonomy.vocabulary("research_purpose"))
    assert not set(taxonomy.vocabulary("research_purpose")) & set(taxonomy.vocabulary("study_design"))        # purpose is not design
    assert taxonomy.validate_domain("management", "organizational_communication") and taxonomy.validate_domain("information_systems", "human_ai_interaction")
    assert taxonomy.validate_domain("education", "design_based_research")


# ================================================================================================ 1-3: one core, execution contracts that differ in logic, empirical gates kept

def test_the_same_core_builds_a_landscape_for_all_three_inquiries(wa, wb, wc):
    for w in (wa, wb, wc):
        v = w["v"]
        assert v["entries"] and v["currency"]["all_current"] is True and v["inquiry"]["research_purposes"]
        types = {o["object_type"] for o in _all_objects(w["store"])}
        assert "Dataset" not in types and "Analysis" not in types                                      # no pilot needed a dataset or an analysis to get a landscape
        assert audit_project_integrity(w["store"])["status"] == "PASS"
    assert wa["v"]["inquiry"]["discipline"] == {"domain": "management", "subfield": "organizational_communication"}
    assert wc["v"]["inquiry"]["research_design"] == "predictive_modeling"


def _all_objects(store):
    with store.read() as conn:
        return [{"object_type": r[0]} for r in conn.execute("SELECT object_type FROM object_heads").fetchall()]


def test_builtin_specs_include_non_empirical_execution_contracts_and_old_pins_are_unchanged():
    assert list_builtin_specs() == ["LIB_DW_ACC_FRAUD_DETECTION", "LIB_DW_CONCEPTUAL_ARGUMENT", "LIB_DW_CRITICAL_SYNTHESIS", "LIB_DW_GENERIC", "LIB_DW_QUALITATIVE_INQUIRY"]
    assert load_builtin_spec("LIB_DW_GENERIC").pin == "LIB_DW_GENERIC@1.0.0#02fc4faaefb4f388"
    assert load_builtin_spec("LIB_DW_ACC_FRAUD_DETECTION").pin == "LIB_DW_ACC_FRAUD_DETECTION@0.1.0#13aa49ba3271f63b"
    assert "research_purposes" not in load_builtin_spec("LIB_DW_GENERIC").to_dict()
    for sid in ("LIB_DW_CRITICAL_SYNTHESIS", "LIB_DW_CONCEPTUAL_ARGUMENT", "LIB_DW_QUALITATIVE_INQUIRY"):
        s = load_builtin_spec(sid)
        assert s.validation_status == "EXTRACTED" and s.domain == "cross_domain" and DomainWorkflowSpec.from_dict(s.to_dict()) == s


def test_research_purpose_predicate_and_filter():
    f = ProjectFacts(study_design="narrative_review")
    f.research_purpose = "critical_synthesis"
    assert evaluate_predicate({"research_purpose_in": ["critical_synthesis"]}, f).ok
    assert not evaluate_predicate({"research_purpose_in": ["prediction"]}, f).ok
    with pytest.raises(SpecError):
        DomainWorkflowSpec.from_dict({**load_builtin_spec("LIB_DW_GENERIC").to_dict(), "steps": [{"id": "x", "stage": "contribution", "kind": "assess",
                                                                                                  "entry_conditions": [{"research_purpose_in": ["win_a_prize"]}]}]})
    spec = load_builtin_spec("LIB_DW_CRITICAL_SYNTHESIS")
    other = ProjectFacts(study_design="narrative_review")
    other.research_purpose = "prediction"
    assert stage_status(spec, other, "literature").applicability is Applicability.NOT_APPLICABLE and "research purposes" in stage_status(spec, other, "literature").justification
    assert stage_status(spec, f, "literature").applicability is Applicability.APPLICABLE


def test_a_conceptual_critical_review_is_not_blocked_by_the_absence_of_data(wa):
    spec = load_builtin_spec("LIB_DW_CRITICAL_SYNTHESIS")
    add_rq(wa, study_design="narrative_review", research_purpose="critical_synthesis")
    facts = facts_from_store(wa["store"], research_question_id="RQ_X")
    assert facts.study_design == "narrative_review" and facts.research_purpose == "critical_synthesis"
    for stage in ("methods", "results"):
        assert stage_status(spec, facts, stage).applicability is Applicability.NOT_APPLICABLE
        out = run_workflow(stage, wa["store"], research_question_id="RQ_X", domain_workflow=spec, reuse_cached=False)
        assert out.status == "NOT_APPLICABLE" and "critical synthesis" in out.result["justification"] and out.status != "BLOCKED"
    assert {"contribution_assessment", "reconstruct_theories"} <= set(next_steps(spec, facts))              # the landscape exists, so the theories can be reconstructed
    empty = ProjectFacts(study_design="narrative_review")
    empty.research_purpose = "critical_synthesis"
    assert "reconstruct_theories" not in next_steps(spec, empty)                                           # without a landscape there is nothing to reconstruct them from
    # the steps are the logic of the inquiry, in order, with a loop-back when the tension turns out to be addressed already
    record_step_run(wa["store"], spec, "contribution_assessment")
    facts = facts_from_store(wa["store"], research_question_id="RQ_X")
    assert "reconstruct_theories" in next_steps(spec, facts)
    for step in ("reconstruct_theories", "reconstruct_constructs_assumptions", "compare_theories_and_scopes", "identify_tensions"):
        record_step_run(wa["store"], spec, step)
    record_step_run(wa["store"], spec, "search_recognition", diagnostic_signals={"tension_addressed": "CONCERN_INDICATED"})
    facts = facts_from_store(wa["store"], research_question_id="RQ_X")
    assert next_steps(spec, facts)[0] == "identify_tensions"                                              # sent back: a reviewed paper already addresses it
    record_step_run(wa["store"], spec, "identify_tensions")
    facts = facts_from_store(wa["store"], research_question_id="RQ_X")
    assert "identify_tensions" not in next_steps(spec, facts)                                              # the loop is closed once the step was redone


def test_the_regression_workflow_is_not_applied_to_a_critical_review_and_its_gates_hold_for_archival_work(wa, wc):
    fraud = load_builtin_spec("LIB_DW_ACC_FRAUD_DETECTION")
    add_rq(wa, study_design="narrative_review")
    blocked_or_na = run_workflow("results", wa["store"], research_question_id="RQ_X", domain_workflow=fraud, reuse_cached=False)
    assert blocked_or_na.status == "NOT_APPLICABLE" and "narrative_review" in blocked_or_na.result["justification"]          # never "needs a regression dataset"
    add_rq(wc, study_design="predictive_modeling", research_purpose="prediction")
    gate = run_workflow("results", wc["store"], research_question_id="RQ_X", domain_workflow=fraud, reuse_cached=False)
    assert gate.status == "BLOCKED" and "model_comparison_protocol" in gate.result["unmet_entry_conditions"][0]
    facts = facts_from_store(wc["store"], research_question_id="RQ_X")
    assert next_steps(fraud, facts) == ["contribution_assessment", "literature_assessment"]                                  # label provenance still needs its dataset and passage
    crit = load_builtin_spec("LIB_DW_CRITICAL_SYNTHESIS")
    assert stage_status(crit, facts, "literature").applicability is Applicability.NOT_APPLICABLE            # and the critical-synthesis contract does not claim an archival project


def test_education_has_two_different_contracts_and_neither_borrows_regression_logic(wb):
    conceptual = load_builtin_spec("LIB_DW_CONCEPTUAL_ARGUMENT")
    qual = load_builtin_spec("LIB_DW_QUALITATIVE_INQUIRY")
    add_rq(wb, study_design="conceptual", research_purpose="methodological_improvement")
    fc = facts_from_store(wb["store"], research_question_id="RQ_X")
    assert stage_status(conceptual, fc, "results").applicability is Applicability.NOT_APPLICABLE
    assert stage_status(qual, fc, "methods").applicability is Applicability.NOT_APPLICABLE and "study designs" in stage_status(qual, fc, "methods").justification
    fq = ProjectFacts(study_design="design_based")
    fq.research_purpose = "interpretation"
    assert stage_status(qual, fq, "methods").applicability is Applicability.APPLICABLE and stage_status(qual, fq, "results").applicability is Applicability.NOT_APPLICABLE
    steps_c = {s.id for s in conceptual.steps}
    steps_q = {s.id for s in qual.steps}
    assert {"state_argument", "rival_explanations", "boundary_conditions"} <= steps_c and {"coding_and_interpretation", "credibility_checks", "alternative_interpretations"} <= steps_q
    assert not any("estimat" in s or "identif" in s or "robust" in s or "regress" in s for s in steps_c | steps_q)
    assert steps_c.isdisjoint({"coding_and_interpretation"}) and steps_q.isdisjoint({"rival_explanations"})        # different logics, not renamed copies


# ================================================================================================ 4: theory and assumptions, from the full text

def test_theory_and_assumptions_are_stored_and_quoted_from_the_full_text(wa):
    v = wa["v"]
    cats = {}
    for e in v["entries"]:
        cats.setdefault(e["category"], []).append(e)
    assert {"theory", "stated_assumption", "construct", "mechanism", "theoretical_claim", "finding", "boundary", "design", "method_limitation"} <= set(cats)
    for e in cats["theory"] + cats["stated_assumption"] + cats["theoretical_claim"]:
        ac = e["author_claim"]
        text = corpus.source_text(wa["store"], e["source_id"], e["source_revision"])[1]
        assert ac["quote"] in text and text[ac["char_start"]:ac["char_end"]] == ac["quote"] and e["provenance"] == "author_stated" and ac["status"] == "AUTHOR_ASSERTED"
        assert wa["store"].get_object(ac["passage_id"], ac["passage_revision"])["content"]["quote_verified"] is True
    att = ent(v, "SRC_ATT", "stated_assumption")
    assert att["item_type"] == "stated" and att["author_claim"]["quote"] == F.A["att_assume"] and att["label"] == "attention scarcity"
    assert ent(v, "SRC_ATT", "theory_invoked")["category"] == "theory" and ent(v, "SRC_ATT", "theoretical_claim")["claim_kind"] == "conceptual"
    assert ent(v, "SRC_AI", "finding")["claim_kind"] == "empirical" and ent(v, "SRC_AI", "finding")["direction"] == "negative"
    assert L.FIELD_TO_TYPE[("RP_THEORY", "assumption_tested")] == "stated_assumption" and L.ITEM_TYPE_OF_FIELD[("RP_THEORY", "assumption_tested")] == "tested_in_source"


def test_a_conceptual_paper_with_its_own_section_headings_is_read(wa):
    """None of the theory papers has a Method or Results section, and their sections are named after their ideas."""
    text = corpus.source_text(wa["store"], "SRC_ATT")[1]
    assert "Results" not in text and "2 A framework of group attention" in text
    assert {e["field"] for e in wa["v"]["entries"] if e["source_id"] == "SRC_ATT"} >= {"theory_invoked", "construct_definition", "stated_assumption", "assumed_mechanism", "theoretical_claim"}


# ================================================================================================ 5-8: relations are proposals, with evidence and the researcher's decision

def prop_div(v):
    return {"kind": "conceptual_divergence", "topic": "what participation presupposes", "entries": [ent(v, "SRC_ATT", "stated_assumption")["id"], ent(v, "SRC_ENG1", "stated_assumption")["id"]],
            "shared": "Both concern what communicating in a shared channel presupposes about the sender.",
            "differences": "Attention theory treats participation as drawing on a shared pool whatever its origin; engagement theory treats communication as effortful and attributable.",
            "rationale": "If messages can be produced without effort, the second assumption no longer follows from participation, while the first still does.",
            "missing_information": ["whether either theory addresses low-effort messages"],
            "levels": {}, "meaning_difference": "participation is attention use for one, an expression of personal investment for the other",
            "already_addressed_by": [], "search_terms": ["participation", "engagement", "effort"]}


def with_levels(p, v, a, b, la, lb):
    p = json.loads(json.dumps(p))
    p["entries"] = [a, b]
    p["levels"] = {a: la, b: lb}
    return p


def test_definitions_that_differ_in_wording_are_flagged_but_never_become_conceptual_divergence(wa):
    v = wa["v"]
    d = [i for i in v["inferences"] if i["kind"] == "definitions_differ"]
    assert [i["relationship"] for i in d] == ["communicative participation"] and d[0]["relation"] == "observation" and d[0]["derivation"] == "rule"
    assert not [i for i in v["inferences"] if i["kind"] in L.PROPOSED_ONLY_KINDS]                                    # no semantic relation is ever derived by rule
    a, b = ent(v, "SRC_PART", "construct_definition")["id"], ent(v, "SRC_AI", "construct_definition", "communicative participation")["id"]
    base = {"kind": "conceptual_divergence", "topic": "participation", "entries": [a, b], "shared": "Both define communicative participation.", "differences": "Different wording.",
            "rationale": "The words differ.", "missing_information": [], "levels": {a: "individual message", b: "individual message"}, "already_addressed_by": [], "search_terms": []}
    out = L.propose_relations(wa["store"], wa["token"], wa["lid"], synth([base]))
    assert out["accepted"] == [] and out["rejected"][0]["code"] == "MEANING_DIFFERENCE_REQUIRED"                     # a different wording is not a difference in meaning
    out = L.propose_relations(wa["store"], wa["token"], wa["lid"], synth([{**base, "meaning_difference": "for one, any message counts; for the other, only a message sent in the channel"}]))
    v2 = L.view(wa["store"], wa["lid"])
    i = inf(v2, out["accepted"][0])
    assert i["kind"] == "conceptual_divergence" and i["status"] == "PROPOSED" and i["confirmation"]["state"] == "UNCONFIRMED" and i["derivation"] == "model_proposed"
    assert any(x["kind"] == "definitions_differ" for x in v2["inferences"])                                         # the observation stays what it was


def test_two_theories_at_different_levels_are_not_called_incompatible(wa):
    v = wa["v"]
    a, b = ent(v, "SRC_ATT", "theoretical_claim")["id"], ent(v, "SRC_ENG1", "theoretical_claim")["id"]
    base = {**prop_div(v), "kind": "theoretical_incompatibility", "topic": "participation and engagement", "entries": [a, b], "levels": {a: "group", b: "dyad"},
            "differences": "One explains attention per message in a group, the other explains investment between two colleagues."}
    out = L.propose_relations(wa["store"], wa["token"], wa["lid"], synth([base, {**base, "levels": {}}, {**base, "levels": {a: "group", b: "group"}, "topic": "same level"}]))
    codes = [r["code"] for r in out["rejected"]]
    assert codes == ["LEVELS_DIFFER", "LEVELS_REQUIRED"] and len(out["accepted"]) == 1                              # the same-level claim is allowed through, to be judged by the researcher
    ok = L.propose_relations(wa["store"], wa["token"], wa["lid"], synth([{**base, "kind": "complementarity", "topic": "levels", "rationale": "They explain different levels and complement each other.",
                                                                          "missing_information": ["whether effects cross levels"]}]))
    assert len(ok["accepted"]) == 1 and inf(L.view(wa["store"], wa["lid"]), ok["accepted"][0])["kind"] == "complementarity"


def test_a_tension_the_literature_already_addresses_is_never_called_unrecognised(wa):
    v = wa["v"]
    addressing = ent(v, "SRC_PART", "theoretical_claim")["id"]
    p = {**prop_div(v), "already_addressed_by": [{"entry": addressing, "how": "it separates participation from relational engagement"}]}
    open_p = {"kind": "explanatory_incompleteness", "topic": "perceived effort", "entries": [ent(v, "SRC_ATT", "assumed_mechanism")["id"], ent(v, "SRC_AI", "finding")["id"]],
              "shared": "Both concern what happens to messages that are cheap to produce.", "differences": "Dilution says nothing about how recipients read the sender's effort.",
              "rationale": "The mechanism explains attention per message, not the recipient's inference about effort.", "missing_information": ["a theory of recipients' inferences about effort"],
              "levels": {}, "already_addressed_by": [], "search_terms": ["perceived effort", "message"]}
    out = L.propose_relations(wa["store"], wa["token"], wa["lid"], synth([p, open_p]))
    assert len(out["accepted"]) == 2
    v2 = L.view(wa["store"], wa["lid"])
    a, b = (inf(v2, i) for i in out["accepted"])
    assert a["recognition"]["status"] == "ADDRESSED_IN_REVIEWED_LITERATURE" and a["recognition"]["addressed_by"][0]["source_id"] == "SRC_PART"
    assert b["recognition"]["status"] == "NOT_FOUND_IN_REVIEWED_SET" and "not a statement that the idea is new" in b["recognition"]["note"]
    with pytest.raises(KnowledgeError, match="ADDRESSED_BY_UNKNOWN"):
        L.add_proposal(wa["store"], wa["token"], wa["lid"], {**p, "already_addressed_by": [{"entry": "E999", "how": "x"}]})
    md = build_landscape_export(wa["store"], wa["lid"]).files["research_landscape.md"].decode("utf-8").lower()
    assert "addressed_in_reviewed_literature" in md and "not_found_in_reviewed_set" in md and "separates participation from relational engagement" in md
    assert "unrecognised" not in md and "unrecognized" not in md and "not been recognised" not in md and "novel" not in md
    cands = L.tension_candidates(wa["store"], wa["lid"], a["id"])
    assert "SRC_PART" in [c["source_id"] for c in cands["candidates"]] and next(c for c in cands["candidates"] if c["source_id"] == "SRC_PART")["read_in_landscape"] is True
    assert "participation" in cands["terms"] and cands["note"].startswith("held full texts that match")


def test_a_model_interpretation_is_never_an_established_theoretical_fact(wa):
    v = wa["v"]
    implied = {"kind": "implied_assumption", "topic": "observable effort", "entries": [ent(v, "SRC_ENG1", "stated_assumption")["id"]],
               "text": "Recipients can tell how much effort a message took.", "reasoning": "If effortful communication signals engagement, recipients must be able to perceive effort."}
    out = L.propose_relations(wa["store"], wa["token"], wa["lid"], synth([prop_div(v), implied]))
    assert len(out["accepted"]) == 2
    v2 = L.view(wa["store"], wa["lid"])
    assert [e["id"] for e in v2["entries"]] == [e["id"] for e in v["entries"]]                                       # a proposal never becomes an author's entry
    i_div, i_imp = (inf(v2, x) for x in out["accepted"])
    assert i_imp["provenance"] == "suite_proposed:model:scripted-synthesiser" and i_imp["status"] == "PROPOSED" and i_imp["text"].startswith("Recipients")
    assert i_imp["confirmation"]["state"] == "UNCONFIRMED" and i_imp["reasoning"] and i_imp["sides"][0]["entry"] == implied["entries"][0]
    with pytest.raises(KnowledgeError, match="REASON_REQUIRED"):
        L.review_inference(wa["store"], wa["token"], wa["lid"], i_div["id"], "CONFIRMED", " ")
    with pytest.raises(KnowledgeError, match="MISSING_INFORMATION_NOT_ACKNOWLEDGED"):
        L.review_inference(wa["store"], wa["token"], wa["lid"], i_div["id"], "CONFIRMED", "I checked the papers.")
    L.review_inference(wa["store"], wa["token"], wa["lid"], i_div["id"], "CONFIRMED", "I checked both papers; neither addresses low-effort messages.", acknowledge_missing=True)
    i2 = inf(L.view(wa["store"], wa["lid"]), i_div["id"])
    assert i2["confirmation"]["state"] == "CONFIRMED_BY_RESEARCHER" and i2["confirmation"]["by"] and i2["derivation"] == "model_proposed" and i2["status"] == "PROPOSED"
    md = build_landscape_export(wa["store"], wa["lid"]).files["research_landscape.md"].decode("utf-8")
    assert "not stated by the authors" in md and "suite_proposed:model:scripted-synthesiser" in md and "UNCONFIRMED" in md and "CONFIRMED_BY_RESEARCHER" in md
    viewer = register_actor(wa["store"], wa["token"], actor_id="viewer_a", display_name="viewer", roles=["importer"])
    with pytest.raises(AuthorizationError):
        L.review_inference(wa["store"], viewer["token"], wa["lid"], i_imp["id"], "CONFIRMED", "x", acknowledge_missing=True)


def test_researcher_and_critic_proposals_are_marked_as_theirs_and_follow_the_same_gate(wa):
    v = wa["v"]
    implied = {"kind": "implied_assumption", "topic": "attributability", "entries": [ent(v, "SRC_ENG1", "stated_assumption")["id"]], "text": "Messages are attributed to a single sender.",
               "reasoning": "A critic reading the paper notes that personal attribution presupposes a single identifiable sender."}
    out = L.add_proposal(wa["store"], wa["token"], wa["lid"], implied, provenance="critic_proposed", critic="Reviewer 2")
    i = inf(L.view(wa["store"], wa["lid"]), out["inference"])
    assert i["provenance"] == "critic_proposed:Reviewer 2" and i["derivation"] == "researcher_proposed" and i["confirmation"]["state"] == "UNCONFIRMED"
    with pytest.raises(KnowledgeError, match="PROPOSAL_KIND_NOT_ALLOWED"):
        L.add_proposal(wa["store"], wa["token"], wa["lid"], {**prop_div(v), "kind": "version_difference"})
    with pytest.raises(KnowledgeError, match="TOO_FEW_ENTRIES"):
        L.add_proposal(wa["store"], wa["token"], wa["lid"], {**prop_div(v), "entries": [prop_div(v)["entries"][0]]})
    with pytest.raises(KnowledgeError, match="FIELD_REQUIRED"):
        L.add_proposal(wa["store"], wa["token"], wa["lid"], {**prop_div(v), "differences": " "})
    with pytest.raises(KnowledgeError, match="PROVENANCE_UNKNOWN"):
        L.add_proposal(wa["store"], wa["token"], wa["lid"], prop_div(v), provenance="oracle")


def test_a_relation_between_two_versions_of_one_work_is_not_a_relation_between_studies(wa):
    v = wa["v"]
    a, b = ent(v, "SRC_ENG1", "stated_assumption")["id"], ent(v, "SRC_ENG2", "stated_assumption")["id"]
    assert ent(v, "SRC_ENG1", "stated_assumption")["author_claim"]["quote"] != ent(v, "SRC_ENG2", "stated_assumption")["author_claim"]["quote"]
    assert {ent(v, "SRC_ENG1", "stated_assumption")["source_id"], ent(v, "SRC_ENG2", "stated_assumption")["source_id"]} == {"SRC_ENG1", "SRC_ENG2"}      # each version keeps its own claim
    out = L.propose_relations(wa["store"], wa["token"], wa["lid"], synth([{**prop_div(v), "entries": [a, b]}]))
    assert out["accepted"] == [] and out["rejected"][0]["code"] == "SAME_WORK"
    assert not [i for i in v["inferences"] if {s["entry"] for s in i["sides"]} == {a, b}]


def test_a_malformed_proposer_answer_is_a_failed_run_and_writes_nothing(wa):
    out = L.propose_relations(wa["store"], wa["token"], wa["lid"], ScriptedModelAdapter(model_id="bad", default_response={"proposals": [{"kind": 7}]}))
    assert out["status"] == "FAILED" and out["reason"] == "invalid_output" and L.view(wa["store"], wa["lid"])["inferences"] == wa["v"]["inferences"]


# ================================================================================================ 10: what a conclusion depends on

def test_a_changed_corpus_marks_the_conclusions_that_rested_on_it_for_reassessment(wa):
    v = wa["v"]
    addressing = ent(v, "SRC_PART", "theoretical_claim")["id"]
    p_found = {**prop_div(v), "already_addressed_by": [{"entry": addressing, "how": "it separates the two ideas"}]}
    p_absent = {"kind": "explanatory_incompleteness", "topic": "perceived effort", "entries": [ent(v, "SRC_ATT", "assumed_mechanism")["id"], ent(v, "SRC_AI", "finding")["id"]],
                "shared": "Both concern cheap messages.", "differences": "Dilution does not cover recipients' inferences.", "rationale": "No mechanism links the two.",
                "missing_information": [], "levels": {}, "already_addressed_by": [], "search_terms": ["effort"]}
    out = L.propose_relations(wa["store"], wa["token"], wa["lid"], synth([p_found, p_absent]))
    found, absent = out["accepted"]
    L.review_inference(wa["store"], wa["token"], wa["lid"], absent, "CONFIRMED", "No reviewed paper covers it.")
    v1 = L.view(wa["store"], wa["lid"])
    assert all(i["needs_reassessment"] == [] for i in v1["inferences"])
    corpus.import_source(wa["store"], wa["token"], citation_key="late", title="A Late Paper On Perceived Effort", text="A Late Paper\n\nAbstract\nEffort.\n\n1 Introduction\nPerceived effort of messages.\n")
    v2 = L.view(wa["store"], wa["lid"])
    assert v2["currency"]["corpus"]["status"] == "STALE_FOR_CORPUS"
    assert any("corpus" in r for r in inf(v2, absent)["needs_reassessment"]) and inf(v2, absent)["confirmation"]["state"] == "CONFIRMED_BY_RESEARCHER"
    assert inf(v2, found)["needs_reassessment"] == []                                                                # it rests on named sources, which did not change
    corpus.set_excluded(wa["store"], wa["token"], "SRC_PART", "withdrawn")
    v3 = L.view(wa["store"], wa["lid"])
    assert any("SRC_PART" in r for r in inf(v3, found)["needs_reassessment"])
    md = build_landscape_export(wa["store"], wa["lid"]).files["research_landscape.md"].decode("utf-8")
    assert "inference(s) need reassessment" in md and "the corpus changed since this absence statement was made" in md


# ================================================================================================ inference routes by claim kind

CONCEPT_OK = [{"criterion": "The inference from participation to dilution is logically valid given the stated premises", "basis": "the paper's own derivation", "basis_kind": "logical_analysis", "met": "yes"}]


def test_each_claim_kind_is_assessed_on_its_own_terms_and_nothing_follows_from_reading_or_reproduction(wa, wb):
    v = wa["v"]
    claim = ent(v, "SRC_ATT", "theoretical_claim")["id"]
    finding = ent(v, "SRC_AI", "finding")["id"]
    assert ent(v, "SRC_ATT", "theoretical_claim")["layers"]["evidence"]["reproduction"] == {"value": "NOT_APPLICABLE", "note": "a conceptual claim is not reproduced from data"}
    assert ent(v, "SRC_AI", "finding")["layers"]["evidence"]["reproduction"]["value"] == "NOT_ATTEMPTED"
    with pytest.raises(KnowledgeError, match="REPRODUCTION_NOT_APPLICABLE"):
        L.link_reproduction(wa["store"], wa["token"], wa["lid"], claim, kind="new_data", reference="x", reason="x")
    # a conceptual claim: methodological literature alone is not a basis for a conceptual claim; logical analysis with a rival considered is
    weak = L.assess_method(wa["store"], wa["token"], wa["lid"], claim, verdict="SUPPORTED_WITHIN_SCOPE", criteria=[{**CONCEPT_OK[0], "basis_kind": "methodological_literature"}],
                           threats=[{"threat": "rival: attention is not a single pool", "status": "addressed"}], rationale="Cited a methods text.")
    assert weak["verdict"] == "LIMITED" and any("conceptual" in b for b in weak["blockers"]) and weak["route"] == "conceptual"
    no_rival = L.assess_method(wa["store"], wa["token"], wa["lid"], claim, verdict="SUPPORTED_WITHIN_SCOPE", criteria=CONCEPT_OK, threats=[], rationale="No rival considered.")
    assert no_rival["verdict"] == "LIMITED" and any("rival explanation" in b for b in no_rival["blockers"])
    ok = L.assess_method(wa["store"], wa["token"], wa["lid"], claim, verdict="SUPPORTED_WITHIN_SCOPE", criteria=CONCEPT_OK,
                         threats=[{"threat": "rival: attention is not a single pool", "status": "addressed"}], rationale="Derivation holds within group channels.")
    assert ok["verdict"] == "SUPPORTED_WITHIN_SCOPE"
    # an empirical finding keeps its WP6 rule: a logical argument is not a basis for a design to permit an inference
    emp = L.assess_method(wa["store"], wa["token"], wa["lid"], finding, verdict="SUPPORTED_WITHIN_SCOPE", criteria=CONCEPT_OK, threats=[{"threat": "demand effects", "status": "addressed"}], rationale="r")
    assert emp["verdict"] == "LIMITED" and emp["route"] == "empirical" and any("methodological literature" in b for b in emp["blockers"])
    ev = ent(L.view(wa["store"], wa["lid"]), "SRC_ATT", "theoretical_claim")["layers"]["evidence"]
    assert ev["inference"]["verdict"] == "SUPPORTED_WITHIN_SCOPE" and ev["reading"]["value"] == "AUTHOR_REPORTED"          # the assessment did not confirm the reading
    # the reading, confirmed, does not confirm the inference
    st = eca.resolve_statement(wa["store"], wa["lid"], element=finding)
    r = eca.adjudicate(wa["store"], wa["token"], st, eca.basis_passage(wa["store"], wa["lid"], finding), ScriptedModelAdapter(model_id="adj", default_response=dict(ADJ_OK)))
    eca.confirm(wa["store"], wa["token"], r["adjudication_id"], verdict="SUPPORTED_AS_STATED", rationale="A fair reading.")
    ev2 = ent(L.view(wa["store"], wa["lid"]), "SRC_AI", "finding")["layers"]["evidence"]
    assert ev2["reading"]["value"] == "READING_CONFIRMED" and ev2["inference"]["verdict"] == "LIMITED"
    # interpretive claims (education B): credibility and reflexivity, not identification
    vb = wb["v"]
    theme = ent(vb, "SRC_MON", "interpretive_finding", "checking by execution")
    assert theme["claim_kind"] == "interpretive" and theme["layers"]["evidence"]["reproduction"]["value"] == "NOT_APPLICABLE"
    q = L.assess_method(wb["store"], wb["token"], wb["lid"], theme["id"], verdict="SUPPORTED_WITHIN_SCOPE", threats=[{"threat": "alternative reading: checking by habit rather than by design", "status": "addressed"}],
                        criteria=[{"criterion": "Excerpts show the participants' own words and context", "basis": "the paper's reported excerpts", "basis_kind": "textual_evidence", "met": "yes"},
                                  {"criterion": "Reflexivity about the first author's teaching role", "basis": "the paper's reflexivity statement", "basis_kind": "textual_evidence", "met": "yes"}],
                        rationale="Transparent, with reflexivity; one institution only.")
    assert q["route"] == "interpretive" and q["verdict"] == "SUPPORTED_WITHIN_SCOPE"
    assert "considerations" in L.view(wb["store"], wb["lid"]) and "alternative interpretations" in " ".join(L.view(wb["store"], wb["lid"])["considerations"]["interpretive"])


def test_consideration_prompts_are_not_a_mandatory_checklist_and_there_is_no_composite_score(wb):
    v = wb["v"]
    assert set(v["considerations"]) == {"empirical", "interpretive", "conceptual", "critical_synthesis"}
    assert "score" not in json.dumps(v).lower().replace("scored", "")
    out = L.assess_method(wb["store"], wb["token"], wb["lid"], ent(v, "SRC_VAL", "theoretical_claim", "artifact completion -> evaluative judgement")["id"], verdict="LIMITED",
                          criteria=[{"criterion": "Boundaries of applicability are stated", "basis": "the paper's own boundary", "basis_kind": "textual_evidence", "met": "yes"}], threats=[], rationale="Boundaries stated; no rival treated.")
    assert out["verdict"] == "LIMITED" and out["blockers"] == []                                                      # one consideration is enough to record; nothing demands all of them


# ================================================================================================ education: nothing is concluded from a completed artifact

def test_education_landscape_does_not_turn_artifact_completion_into_evidence_of_learning(wb):
    v = wb["v"]
    claim = ent(v, "SRC_VAL", "theoretical_claim", "artifact completion -> evaluative judgement")
    assert claim["claim_kind"] == "conceptual" and claim["layers"]["evidence"]["inference"] == {"value": "NOT_ASSESSED"}
    assert ent(v, "SRC_MON", "interpretive_finding", "checking by execution")["layers"]["evidence"]["reading"]["value"] == "AUTHOR_REPORTED"
    p = {"kind": "complementarity", "topic": "checking behaviour and validity", "entries": [claim["id"], ent(v, "SRC_MON", "interpretive_finding", "checking by execution")["id"]],
         "shared": "Both concern what traces of checking tell about the learner.", "differences": "One is an argument about inference, the other an interpretation of interview accounts.",
         "rationale": "The interview theme illustrates the gap the argument describes; it does not show it generally.", "missing_information": ["whether execution-checking predicts evaluative judgement"],
         "levels": {}, "already_addressed_by": [], "search_terms": []}
    out = L.propose_relations(wb["store"], wb["token"], wb["lid"], synth([p]))
    v2 = L.view(wb["store"], wb["lid"])
    assert inf(v2, out["accepted"][0])["confirmation"]["state"] == "UNCONFIRMED"
    md = build_landscape_export(wb["store"], wb["lid"]).files["research_landscape.md"].decode("utf-8")
    assert "established" not in md.lower().replace("none of them says the finding is established", "").replace("none of them says a finding is established", "").replace(
        "no entry says a result is established", "")
    assert "does not by itself support" in md and "by whether it ran" in md
    assert "Interpretive findings" in md and "Theoretical claims" in md


# ================================================================================================ archival / predictive modeling keeps its gates

def test_archival_prediction_keeps_empirical_disagreement_and_the_empirical_inference_gate(wc, tmp_path):
    v = wc["v"]
    kinds = sorted((i["kind"], i["subkind"], i["relation"]) for i in v["inferences"] if i["relationship"] == F.C_REL)
    assert kinds == [("contradiction", "null_vs_effect", "empirical_disagreement"), ("contradiction", "null_vs_effect", "empirical_disagreement"),
                     ("contradiction", "opposite_direction", "empirical_disagreement")]
    assert all(i["derivation"] == "rule" and i["status"] == "PROVISIONAL" for i in v["inferences"])
    fid = ent(v, "SRC_OTS", "finding")["id"]
    assert ent(v, "SRC_OTS", "finding")["claim_kind"] == "empirical" and ent(v, "SRC_OTS", "measurement")["label"] == "fraud label"
    weak = L.assess_method(wc["store"], wc["token"], wc["lid"], fid, verdict="SUPPORTED_WITHIN_SCOPE", threats=[{"threat": "label timing leakage", "status": "addressed"}],
                           criteria=[{"criterion": "Labels are dated at release, not at the fiscal year", "basis": "the paper's own description", "methodological_literature": False, "met": "yes"}], rationale="r")
    assert weak["verdict"] == "LIMITED" and any("methodological literature" in b for b in weak["blockers"]) and weak["route"] == "empirical"
    open_threat = L.assess_method(wc["store"], wc["token"], wc["lid"], fid, verdict="SUPPORTED_WITHIN_SCOPE", threats=[{"threat": "undetected fraud labelled as non-fraud", "status": "open"}],
                                  criteria=[{"criterion": "Label provenance and timing are documented", "basis": "survey of label construction in fraud research", "methodological_literature": True, "met": "yes"}], rationale="r")
    assert open_threat["verdict"] == "LIMITED" and any("open" in b for b in open_threat["blockers"])
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "panel.csv").write_bytes(b"firm,label\na,1\nb,0\nc,0\nd,0\n")
    add_replication_package(wc["store"], wc["token"], package_id="PKG_OTS", label="package", directory=pkg, files=["panel.csv"], entitlement={"status": "confirmed", "license": "invented", "basis": "test"},
                            expected_outputs=[{"id": "pos", "file": "panel.csv", "column": "label", "stat": "positive_rate", "expected": 0.25, "tolerance": 1e-9}])
    record_reproduction(wc["store"], wc["token"], "PKG_OTS")
    L.link_reproduction(wc["store"], wc["token"], wc["lid"], fid, kind="package_run", package_id="PKG_OTS", outputs=["pos"], reason="The package reproduces the label rate.")
    ev = ent(L.view(wc["store"], wc["lid"]), "SRC_OTS", "finding")["layers"]["evidence"]
    assert ev["reproduction"]["value"] == "REPRODUCED_WITHIN_TOLERANCE" and ev["inference"]["verdict"] == "LIMITED"          # reproducing the code did not validate the inference


# ================================================================================================ 11: WP6 behaviour on its own fixtures is pinned

def test_wp6_results_on_the_fraud_style_fixtures_are_unchanged(tmp_path):
    w = LF.build_world(tmp_path)
    calls = []
    lid = L.build(w["store"], w["token"], w["rid"], LF.reader(calls=calls))["landscape_id"]
    v = L.view(w["store"], lid)
    assert len(calls) == 8 and [(e["id"], e["category"], e["source_id"]) for e in v["entries"]] == [
        ("E001", "construct", "SRC_PAT"), ("E002", "construct", "SRC_QUINN"), ("E003", "mechanism", "SRC_PAT"), ("E004", "design", "SRC_PAT"), ("E005", "design", "SRC_QUINN"),
        ("E006", "measurement", "SRC_PAT"), ("E007", "identifying_assumption", "SRC_PAT"), ("E008", "finding", "SRC_PAT"), ("E009", "finding", "SRC_QUINN"), ("E010", "finding", "SRC_SAM"),
        ("E011", "finding", "SRC_REN1"), ("E012", "boundary", "SRC_PAT"), ("E013", "boundary", "SRC_QUINN"), ("E014", "method_limitation", "SRC_PAT"), ("E015", "unresolved_question", "SRC_PAT")]
    assert [(i["id"], i["kind"], i["subkind"], i["relationship"]) for i in v["inferences"]] == [("I001", "contradiction", "opposite_direction", LF.REL),
                                                                                               ("I002", "definitions_differ", "wording", "earnings manipulation")]
    assert all(e["claim_kind"] == "empirical" for e in v["entries"] if e["category"] == "finding") and all(e["provenance"] == "author_stated" for e in v["entries"])
    assert v["entries"][7]["layers"]["evidence"]["reproduction"] == {"value": "NOT_ATTEMPTED"}


# ================================================================================================ 12: export and task

def test_every_inference_in_the_export_shows_where_it_came_from_and_whether_you_confirmed_it(wa):
    v = wa["v"]
    out = L.propose_relations(wa["store"], wa["token"], wa["lid"], synth([prop_div(v)]))
    L.review_inference(wa["store"], wa["token"], wa["lid"], "I001", "DISMISSED", "These were two versions of a thought, not a conflict.")
    v2 = L.view(wa["store"], wa["lid"])
    exp = build_landscape_export(wa["store"], wa["lid"])
    md = exp.files["research_landscape.md"].decode("utf-8")
    rows = {line.split("|")[1].strip(): line for line in md.splitlines() if line.startswith("| I")}
    assert set(rows) == {i["id"] for i in v2["inferences"]}
    for i in v2["inferences"]:
        assert i["derivation"] in rows[i["id"]] and i["confirmation"]["state"] in rows[i["id"]], i["id"]
    assert "rule" in rows["I001"] and "DISMISSED_BY_RESEARCHER" in rows["I001"]
    assert out["accepted"][0] in rows and "model_proposed" in rows[out["accepted"][0]] and "scripted-synthesiser" in rows[out["accepted"][0]]
    assert "Inquiry" in md and "critical_synthesis" in md and "management" in md
    for kind in ("empirical", "interpretive", "conceptual", "critical_synthesis"):
        assert kind in md                                                                                         # considerations appear as prompts
    assert "Considerations" in md and "not a checklist" in md


def test_the_build_landscape_goal_can_include_a_proposal_step_and_stays_the_same_by_default(wa, tmp_path):
    from research_harness.tasks import goals
    base = goals.plan_for("build_landscape", {"retrieval_id": wa["rid"]})
    assert [s["step_id"] for s in base["steps"]] == ["S01", "S02", "S03", "S04"]
    plus = goals.plan_for("build_landscape", {"retrieval_id": wa["rid"], "propose_relations": True})
    assert [s["capability"] for s in plus["steps"] if s["kind"] == "run"] == ["landscape_build", "landscape_propose", "export_landscape"]
    assert plus["steps"][3]["kind"] == "decide" and plus["mandatory_checkpoints"] == ["S01", "S04"]


def test_cli_proposals_candidates_and_acknowledgement(wa, monkeypatch, capsys, tmp_path):
    from research_harness import cli_knowledge
    v = wa["v"]
    tf = tmp_path / "token"
    tf.write_text(wa["token"], encoding="utf-8")
    monkeypatch.setattr(cli_knowledge, "_adapter", lambda args: synth([prop_div(v)]))
    base = ["--project", "PRJ_A", "--home", str(wa["home"])]
    auth = [*base, "--token-file", str(tf)]

    def j(*argv, rc=0):
        assert main([*map(str, argv), "--json"]) == rc, capsys.readouterr().err
        return json.loads(capsys.readouterr().out)

    out = j("landscape", "propose", wa["lid"], *auth)
    assert len(out["accepted"]) == 1
    iid = out["accepted"][0]
    assert main(["landscape", "show", wa["lid"], *base]) == 0
    shown = capsys.readouterr().out
    assert "conceptual_divergence" in shown and "UNCONFIRMED" in shown and "model_proposed" in shown
    cands = j("landscape", "candidates", wa["lid"], iid, *base)
    assert cands["candidates"]
    assert main(["landscape", "review", wa["lid"], iid, "CONFIRMED", "--reason", "checked", *auth]) == 1
    assert "MISSING_INFORMATION_NOT_ACKNOWLEDGED" in capsys.readouterr().err
    assert j("landscape", "review", wa["lid"], iid, "CONFIRMED", "--reason", "checked", "--acknowledge-missing", *auth)["decision"] == "CONFIRMED"
    pf = tmp_path / "proposal.json"
    pf.write_text(json.dumps({"kind": "implied_assumption", "topic": "t", "entries": [ent(v, "SRC_ENG1", "stated_assumption")["id"]], "text": "Messages have one sender.",
                              "reasoning": "A reviewer's note."}), encoding="utf-8")
    added = j("landscape", "add-proposal", wa["lid"], "--proposal", str(pf), "--provenance", "critic_proposed", "--critic", "Reviewer 2", *auth)
    assert added["inference"].startswith("I")
    out_dir = tmp_path / "exp"
    j("export", "landscape", wa["lid"], "--out", out_dir, *base)
    assert main(["export", "verify", str(out_dir), *base]) == 0 and capsys.readouterr().out.strip() == "VERIFIED"
    assert main(["problem", "revise", wa["pid"], "--rationale", "record the inquiry", "--purpose", "critical_synthesis", "--design", "narrative_review", "--evidence-form", "conceptual_argument",
                 "--domain", "management", "--subfield", "organizational_communication", *auth]) == 0
    assert main(["domain", "next", "--project", "PRJ_A", "--home", str(wa["home"]), "--spec", "LIB_DW_CRITICAL_SYNTHESIS", "--study-design", "narrative_review",
                 "--research-purpose", "critical_synthesis"]) == 0
    assert "reconstruct_theories" in capsys.readouterr().out


# ================================================================================================ what follows from the recorded inquiry, and what a rebuild keeps

def test_a_promoted_question_and_a_problem_give_the_engine_the_recorded_inquiry(wa):
    spec = load_builtin_spec("LIB_DW_CRITICAL_SYNTHESIS")
    facts = facts_from_store(wa["store"], problem_id=wa["pid"])
    assert facts.study_design == "narrative_review" and facts.purposes() == {"critical_synthesis", "theory_development"}
    assert stage_status(spec, facts, "literature").applicability is Applicability.APPLICABLE
    with pytest.raises(KeyError, match="PROBLEM_NOT_FOUND"):
        facts_from_store(wa["store"], problem_id="PROB_999999")
    P.add_interpretation(wa["store"], wa["token"], wa["pid"], "AI assistance separates producing a message from investing in a relationship.")
    P.choose(wa["store"], wa["token"], wa["pid"], ["I1"], "the reading this synthesis pursues")
    rq = P.promote_to_rq(wa["store"], wa["token"], wa["pid"], "I1", "How does AI-assisted communication separate participation from relational engagement at work?", "my own wording")
    rq_id = rq["research_question"]
    q = facts_from_store(wa["store"], research_question_id=rq_id)
    assert q.study_design == "narrative_review" and q.purposes() == {"critical_synthesis", "theory_development"}             # the promoted question carries the problem's inquiry
    out = run_workflow("results", wa["store"], research_question_id=rq_id, domain_workflow=spec, reuse_cached=False)
    assert out.status == "NOT_APPLICABLE"


def test_a_rebuild_carries_proposals_and_decisions_only_where_the_claims_they_concern_are_unchanged(wa):
    v = wa["v"]
    out = L.propose_relations(wa["store"], wa["token"], wa["lid"], synth([prop_div(v)]))
    L.review_inference(wa["store"], wa["token"], wa["lid"], out["accepted"][0], "CONFIRMED", "Checked.", acknowledge_missing=True)
    same = L.build(wa["store"], wa["token"], wa["rid"], F.reader(F.A_RECORDS), versions="all", carry_from=wa["lid"])
    v2 = L.view(wa["store"], same["landscape_id"])
    carried = [i for i in v2["inferences"] if i["derivation"] == "model_proposed"]
    assert len(carried) == 1 and carried[0]["confirmation"]["state"] == "CONFIRMED_BY_RESEARCHER" and carried[0]["carried_from"] == wa["lid"] and carried[0]["provenance"].startswith("suite_proposed:")
    assert [s["entry"] for s in carried[0]["sides"]] == prop_div(v)["entries"]                                                    # same claims, same entry ids after an unchanged rebuild
    changed = json.loads(json.dumps(F.A_RECORDS))
    changed["att"]["RP_THEORY"][2]["value"] = "Attention is scarce, and any participation draws on the same pool (restated)."
    again = L.build(wa["store"], wa["token"], wa["rid"], F.reader(changed), versions="all", carry_from=wa["lid"], reuse=False)
    assert [i for i in L.view(wa["store"], again["landscape_id"])["inferences"] if i["derivation"] == "model_proposed"] == []        # the assumption it was about changed


def test_a_proposal_run_that_adds_nothing_does_not_write_a_new_landscape_revision(wa):
    before = wa["store"].get_object(wa["lid"])["revision"]
    bad = {**prop_div(wa["v"]), "kind": "version_difference"}
    out = L.propose_relations(wa["store"], wa["token"], wa["lid"], synth([bad]))
    assert out["accepted"] == [] and out["rejected"][0]["code"] == "PROPOSAL_KIND_NOT_ALLOWED" and out["revision"] == before
    assert wa["store"].get_object(wa["lid"])["revision"] == before
