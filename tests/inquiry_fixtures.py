"""Three independent invented corpora for the WP6.1 cross-design tests. Not one corpus with the domain renamed: each has its own papers, section structure, kinds of claims and planted structure.

A  critical synthesis (AI-assisted communication at work). Papers are theoretical or conceptual, with their own section headings and no Method or Results section, plus one empirical
   paper. Planted: two theories at different levels of analysis that look incompatible but explain different levels; a paper that already separates participation from engagement
   (so the tension is not unrecognised); two versions of one work whose stated assumption differs; construct definitions that differ in wording only.
B  education (agentic AI). A conceptual paper on assessment validity (propositions, no data) and a qualitative design-research paper (interpretive findings from interviews).
C  archival / predictive modeling (financial-reporting fraud detection). Papers with label provenance, temporal validation and leakage; planted: two findings of opposite direction.

All names and results are invented; nothing here is from the literature.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from research_harness.discovery import problem as P
from research_harness.discovery import retrieval as RT
from research_harness.knowledge import corpus
from research_harness.state.store import StateStore
from research_harness.workflows.adapters import ModelError, ScriptedModelAdapter


def _paper(title: str, abstract: str, sections: list[tuple[str, list[str]]]) -> str:
    body = "\n\n".join(f"{h}\n{' '.join(ss)}" for h, ss in sections)
    return f"{title}\n\nAbstract\n{abstract}\n\n{body}\n\nReferences\n[1] Invented Author. An Invented Reference. Journal of Invented Studies.\n"


# ------------------------------------------------------------------------------------------------ A: critical synthesis
A_STATEMENT = ("Does the increasing capacity to produce AI-assisted communication challenge how existing scholarship understands the relationship between communicative "
               "participation, collective attention, and relational engagement at work?")
A_TERMS = ["AI-assisted communication participation collective attention relational engagement work"]
A = {
    "att_theory": "Collective attention theory holds that a group has a limited pool of attention that its members allocate to shared topics.",
    "att_def": "We define collective attention as the shared allocation of a group's limited attention to a common set of topics.",
    "att_assume": "We assume that attention is scarce and that participation in a shared channel draws on the same pool.",
    "att_mech": "Attentional dilution: each additional message in a shared channel lowers the attention available per message.",
    "att_claim": "Greater participation in a shared channel dilutes collective attention per message.",
    "eng_theory": "Relational engagement is developed here within social exchange theory.",
    "eng_def": "We define relational engagement as an employee's felt investment in particular working relationships.",
    "eng_assume1": "We assume that engagement is expressed through effortful, personally attributable communication.",
    "eng_assume2": "We assume that engagement is expressed through personally attributable communication.",
    "eng_claim": "Effortful personal communication builds relational engagement between colleagues.",
    "part_def": "We define communicative participation as contributing a message to a shared channel, regardless of the effort or intent behind it.",
    "part_claim": "We argue that communicative participation and relational engagement should be distinguished, because participation can occur without personal investment.",
    "part_boundary": "This distinction matters most where the cost of producing a message is low.",
    "ai_find": "Recipients rate machine-assisted messages as lower in sender effort than unassisted messages.",
    "ai_design": "We ran a vignette experiment with 240 employees, randomising whether a message was described as machine-assisted.",
    "ai_limit": "The vignettes describe single messages, not ongoing working relationships.",
    "ai_def": "We define sender effort as the perceived time and thought invested by the sender.",
    "ai_part_def": "Participation here means sending a message in the shared channel.",
}
A_PAPERS: dict[str, dict[str, Any]] = {
    "att": {"title": "Collective Attention as a Group Resource", "year": 2019, "meta": {},
            "text": _paper("Collective Attention as a Group Resource", "Participation, attention and shared channels at work.",
                           [("1 Introduction", ["Teams rely on shared channels for coordination."]), ("2 A framework of group attention", [A["att_theory"], A["att_def"], A["att_assume"], A["att_mech"]]),
                            ("3 Propositions", [A["att_claim"]])])},
    "eng1": {"title": "Relational Engagement at Work", "year": 2022,
             "meta": dict(document_type="working_paper", review_status="NOT_REVIEWED", first_public_date="2022-03-01", date_evidence="repository posting record (invented)", version_id="v1", work_id="W_ENG"),
             "text": _paper("Relational Engagement at Work", "Engagement, communication and working relationships.",
                            [("1 Introduction", ["Working relationships shape how people experience work."]), ("2 Engagement as investment", [A["eng_theory"], A["eng_def"], A["eng_assume1"]]),
                             ("3 Propositions", [A["eng_claim"]])])},
    "eng2": {"title": "Relational Engagement at Work", "year": 2024,
             "meta": dict(document_type="journal_article", review_status="PUBLISHED", first_public_date="2024-01-10", date_evidence="DOI registration (invented)", version_id="journal", work_id="W_ENG"),
             "text": _paper("Relational Engagement at Work", "Engagement, communication and working relationships.",
                            [("1 Introduction", ["Working relationships shape how people experience work."]), ("2 Engagement as investment", [A["eng_theory"], A["eng_def"], A["eng_assume2"]]),
                             ("3 Propositions", [A["eng_claim"]])])},
    "part": {"title": "Participation Is Not Engagement", "year": 2023, "meta": {},
             "text": _paper("Participation Is Not Engagement", "Distinguishing participation from relational engagement where messages are cheap.",
                            [("1 Introduction", ["Channels make it easy to contribute."]), ("2 Two ideas that are often merged", [A["part_def"], A["part_claim"], A["part_boundary"]])])},
    "ai": {"title": "Machine-Assisted Messages and Perceived Effort", "year": 2024, "meta": {},
           "text": _paper("Machine-Assisted Messages and Perceived Effort", "Participation and perceived effort when messages are machine-assisted.",
                          [("1 Introduction", ["Machine assistance changes how messages are produced.", A["ai_def"], A["ai_part_def"]]), ("2 Data and method", [A["ai_design"]]), ("3 Results", [A["ai_find"]]),
                           ("4 Limitations", [A["ai_limit"]])])},
}
REL_ATT = "participation -> collective attention"
A_RECORDS: dict[str, dict[str, list[dict[str, Any]]]] = {
    "att": {"RP_LANDSCAPE": [{"field": "theoretical_claim", "quote": A["att_claim"], "value": "More participation in a shared channel dilutes collective attention per message.",
                              "label": REL_ATT, "type": "proposition"}],
            "RP_THEORY": [{"field": "theory_invoked", "quote": A["att_theory"], "value": "Collective attention theory: a group's limited attention pool.", "label": "collective attention theory"},
                          {"field": "construct_definition", "quote": A["att_def"], "value": "Collective attention: shared allocation of a group's limited attention.", "label": "collective attention"},
                          {"field": "stated_assumption", "quote": A["att_assume"], "value": "Attention is scarce and participation draws on one shared pool.", "label": "attention scarcity"},
                          {"field": "assumed_mechanism", "quote": A["att_mech"], "value": "Attentional dilution lowers attention per message.", "label": "attentional dilution"}]},
    "eng1": {"RP_LANDSCAPE": [{"field": "theoretical_claim", "quote": A["eng_claim"], "value": "Effortful personal communication builds relational engagement.", "label": "communication -> engagement", "type": "proposition"}],
             "RP_THEORY": [{"field": "theory_invoked", "quote": A["eng_theory"], "value": "Social exchange theory.", "label": "social exchange theory"},
                           {"field": "construct_definition", "quote": A["eng_def"], "value": "Relational engagement: felt investment in particular working relationships.", "label": "relational engagement"},
                           {"field": "stated_assumption", "quote": A["eng_assume1"], "value": "Engagement is expressed through effortful, attributable communication.", "label": "engagement is expressed through effortful communication"}]},
    "eng2": {"RP_LANDSCAPE": [{"field": "theoretical_claim", "quote": A["eng_claim"], "value": "Effortful personal communication builds relational engagement.", "label": "communication -> engagement", "type": "proposition"}],
             "RP_THEORY": [{"field": "theory_invoked", "quote": A["eng_theory"], "value": "Social exchange theory.", "label": "social exchange theory"},
                           {"field": "construct_definition", "quote": A["eng_def"], "value": "Relational engagement: felt investment in particular working relationships.", "label": "relational engagement"},
                           {"field": "stated_assumption", "quote": A["eng_assume2"], "value": "Engagement is expressed through attributable communication.", "label": "engagement is expressed through communication"}]},
    "part": {"RP_LANDSCAPE": [{"field": "theoretical_claim", "quote": A["part_claim"], "value": "Participation and relational engagement are distinct: participation can occur without personal investment.",
                               "label": "participation vs engagement", "type": "critique"},
                              {"field": "boundary_condition", "quote": A["part_boundary"], "value": "The distinction matters most where producing a message is cheap.", "label": "participation vs engagement"}],
             "RP_THEORY": [{"field": "construct_definition", "quote": A["part_def"], "value": "Communicative participation: contributing a message to a shared channel, whatever the effort.", "label": "communicative participation"}]},
    "ai": {"RP_LANDSCAPE": [{"field": "design", "quote": A["ai_design"], "value": "A vignette experiment with 240 employees randomising machine assistance."},
                            {"field": "finding", "quote": A["ai_find"], "value": "Machine-assisted messages are rated lower in sender effort.", "label": "machine assistance -> perceived sender effort", "type": "negative"},
                            {"field": "stated_limitation", "quote": A["ai_limit"], "value": "Vignettes describe single messages, not ongoing relationships."}],
           "RP_THEORY": [{"field": "construct_definition", "quote": A["ai_def"], "value": "Sender effort: perceived time and thought invested by the sender.", "label": "sender effort"},
                         {"field": "construct_definition", "quote": A["ai_part_def"], "value": "Participation: sending a message in the shared channel.", "label": "communicative participation"}]},
}

# ------------------------------------------------------------------------------------------------ B: education
B_STATEMENT = ("Students use agentic development environments to produce complete dashboards and reports, but the traces of their checking and their ability to judge the work "
               "may not correspond to the quality of the product.")
B_TERMS = ["agentic AI students dashboards reports verification evaluative judgement assessment validity"]
B = {
    "c_def": "We define evaluative judgement as the capability to make decisions about the quality of work, of oneself and others.",
    "c_assume": "We assume that assessment validity concerns inferences about learners, not statements about artifacts.",
    "c_claim1": "A completed artifact produced with an agentic tool does not by itself support an inference about the learner's evaluative judgement.",
    "c_claim2": "Assessment of agentic work needs evidence of how the learner checked and revised delegated output.",
    "c_boundary": "The argument does not apply to tasks where the tool's contribution is fixed and recorded in advance.",
    "c_rival": "A rival view holds that sustained use of the tool is itself a sign of competence.",
    "q_design": "We conducted a design-based study in two iterations of a course, with twelve semi-structured interviews.",
    "q_theme1": "Students described checking the agent's output by whether it ran, not by whether it was correct.",
    "q_theme2": "Most students rarely revised the task specification after the first delegation.",
    "q_context": "All participants were second-year students in one programme at one institution.",
    "q_limit": "Interview accounts may differ from what students did while working.",
    "q_refl": "The first author taught the course, which may have shaped what students said.",
}
B_PAPERS: dict[str, dict[str, Any]] = {
    "val": {"title": "Artifact Completion Is Not Evidence of Learning", "year": 2025, "meta": {},
            "text": _paper("Artifact Completion Is Not Evidence of Learning", "Assessment validity and evaluative judgement when learners delegate work to agentic AI.",
                           [("1 Introduction", ["Learners now delegate whole tasks to agentic tools."]), ("2 Evaluative judgement and validity", [B["c_def"], B["c_assume"]]),
                            ("3 Propositions", [B["c_claim1"], B["c_claim2"], B["c_boundary"]]), ("4 A rival reading", [B["c_rival"]])])},
    "mon": {"title": "How Students Monitor Delegated AI Work", "year": 2025, "meta": {},
            "text": _paper("How Students Monitor Delegated AI Work", "A design-based study of how students supervise agentic AI on dashboards and reports.",
                           [("1 Introduction", ["Students delegate dashboards and reports to an agentic environment."]), ("2 Design and participants", [B["q_design"], B["q_context"]]),
                            ("3 Findings", [B["q_theme1"], B["q_theme2"]]), ("4 Reflexivity and limits", [B["q_refl"], B["q_limit"]])])},
}
B_RECORDS: dict[str, dict[str, list[dict[str, Any]]]] = {
    "val": {"RP_LANDSCAPE": [{"field": "theoretical_claim", "quote": B["c_claim1"], "value": "Artifact completion alone does not support an inference about evaluative judgement.",
                              "label": "artifact completion -> evaluative judgement", "type": "proposition"},
                             {"field": "theoretical_claim", "quote": B["c_claim2"], "value": "Assessing agentic work needs evidence of how learners checked and revised delegated output.",
                              "label": "assessment of agentic work", "type": "proposition"},
                             {"field": "boundary_condition", "quote": B["c_boundary"], "value": "Not applicable where the tool's contribution is fixed and recorded in advance.", "label": "artifact completion -> evaluative judgement"}],
            "RP_THEORY": [{"field": "construct_definition", "quote": B["c_def"], "value": "Evaluative judgement: the capability to decide on the quality of work.", "label": "evaluative judgement"},
                          {"field": "stated_assumption", "quote": B["c_assume"], "value": "Assessment validity concerns inferences about learners, not artifacts.", "label": "validity concerns inferences about learners"}]},
    "mon": {"RP_LANDSCAPE": [{"field": "design", "quote": B["q_design"], "value": "Design-based study, two course iterations, twelve interviews."},
                             {"field": "interpretive_finding", "quote": B["q_theme1"], "value": "Students checked agent output by whether it ran rather than whether it was correct.", "label": "checking by execution"},
                             {"field": "interpretive_finding", "quote": B["q_theme2"], "value": "Most students rarely revised the task specification after first delegation.", "label": "specification stability"},
                             {"field": "boundary_condition", "quote": B["q_context"], "value": "Participants were second-year students in one programme at one institution.", "label": "checking by execution"},
                             {"field": "stated_limitation", "quote": B["q_limit"], "value": "Interview accounts may differ from what students did."}],
            "RP_THEORY": []},
}

# ------------------------------------------------------------------------------------------------ C: archival / predictive (financial-reporting fraud detection)
C_STATEMENT = "Financial reporting fraud is hard to detect with models whose evaluation may depend on how fraud labels are dated and how the data are split in time."
C_TERMS = ["financial reporting fraud detection model evaluation labels temporal split leakage"]
C = {
    "p1_design": "We train gradient boosting models on firm-year financial ratios and evaluate them on later years only.",
    "p1_label": "The fraud label comes from enforcement releases and is dated by the release, not by the fiscal year of the misstatement.",
    "p1_find": "Average precision falls from 0.31 under random splits to 0.12 under out-of-time splits.",
    "p1_assume": "We assume that firms never flagged by an enforcement release are not fraudulent.",
    "p1_limit": "Enforcement releases cover only detected fraud, so undetected fraud is labelled as non-fraud.",
    "p2_design": "We compare detection models using stratified random splits of the same firm-year panel.",
    "p2_find": "Out-of-time splits raise average precision by 0.04 relative to random splits of the same panel.",
    "p2_leak": "Features computed from restated filings include information that was unavailable at the prediction date.",
    "p3_find": "Detection accuracy is unaffected by the choice between random and out-of-time splits.",
    "p3_design": "We evaluate a regularised logistic model on a panel of listed firms.",
}
C_PAPERS: dict[str, dict[str, Any]] = {
    "ots": {"title": "Out-of-Time Evaluation of Fraud Detection Models", "year": 2022, "meta": {},
            "text": _paper("Out-of-Time Evaluation of Fraud Detection Models", "Financial reporting fraud detection models, labels and temporal splits.",
                           [("1 Introduction", ["Fraud detection models are usually evaluated on random splits."]), ("2 Data and method", [C["p1_design"], C["p1_label"], C["p1_assume"]]),
                            ("3 Results", [C["p1_find"]]), ("4 Limitations", [C["p1_limit"]])])},
    "rnd": {"title": "Random Splits in Fraud Detection Evaluation", "year": 2021, "meta": {},
            "text": _paper("Random Splits in Fraud Detection Evaluation", "Financial reporting fraud detection, evaluation and leakage.",
                           [("1 Introduction", ["Evaluation design affects reported fraud detection performance."]), ("2 Data and method", [C["p2_design"], C["p2_leak"]]), ("3 Results", [C["p2_find"]])])},
    "flat": {"title": "Split Choice and Detection Accuracy", "year": 2023, "meta": {},
             "text": _paper("Split Choice and Detection Accuracy", "Financial reporting fraud detection model evaluation and split choice.",
                            [("1 Introduction", ["Does split choice matter for fraud detection models?"]), ("2 Data and method", [C["p3_design"]]), ("3 Results", [C["p3_find"]])])},
}
C_REL = "out-of-time evaluation -> average precision"
C_RECORDS: dict[str, dict[str, list[dict[str, Any]]]] = {
    "ots": {"RP_LANDSCAPE": [{"field": "design", "quote": C["p1_design"], "value": "Gradient boosting on firm-year ratios, evaluated on later years only."},
                             {"field": "measurement", "quote": C["p1_label"], "value": "Fraud label from enforcement releases, dated by release.", "label": "fraud label"},
                             {"field": "identifying_assumption", "quote": C["p1_assume"], "value": "Firms never flagged are not fraudulent.", "type": "testing_not_stated"},
                             {"field": "finding", "quote": C["p1_find"], "value": "Average precision falls from 0.31 (random splits) to 0.12 (out-of-time splits).",
                              "label": C_REL, "type": "negative"},
                             {"field": "stated_limitation", "quote": C["p1_limit"], "value": "Only detected fraud is labelled; undetected fraud counts as non-fraud."}], "RP_THEORY": []},
    "rnd": {"RP_LANDSCAPE": [{"field": "design", "quote": C["p2_design"], "value": "Stratified random splits of one firm-year panel."},
                             {"field": "finding", "quote": C["p2_find"], "value": "Out-of-time splits raise average precision by 0.04 relative to random splits.", "label": C_REL, "type": "positive"},
                             {"field": "stated_limitation", "quote": C["p2_leak"], "value": "Restated-filing features contain information unavailable at the prediction date."}], "RP_THEORY": []},
    "flat": {"RP_LANDSCAPE": [{"field": "design", "quote": C["p3_design"], "value": "Regularised logistic model on a panel of listed firms."},
                              {"field": "finding", "quote": C["p3_find"], "value": "Detection accuracy does not depend on the split choice.", "label": C_REL, "type": "null"}], "RP_THEORY": []},
}


# ------------------------------------------------------------------------------------------------ helpers
def reader(records: dict, *, fail_for: Optional[set[str]] = None, calls: Optional[list] = None, model_id: str = "scripted-inquiry-reader") -> ScriptedModelAdapter:
    def handler(system: str, user: str) -> Any:
        key = re.search(r"Source: (\S+) \(", user).group(1)
        proto = re.search(r"\((RP_[A-Z_]+):", system).group(1)
        if calls is not None:
            calls.append((key, proto))
        if key in (fail_for or set()):
            raise ModelError("scripted failure")
        return {"records": records.get(key, {}).get(proto, []), "gaps": []}
    return ScriptedModelAdapter(model_id=model_id, custom_handler=handler)


def build_world(tmp_path: Any, *, name: str, papers: dict, statement: str, terms: list[str], inquiry: Optional[dict] = None, search: bool = True) -> dict[str, Any]:
    store, token = StateStore.create(tmp_path / "home", name, owner_id="researcher")
    ids = {}
    for key, p in papers.items():
        r = corpus.import_source(store, token, citation_key=key, title=p["title"], year=p["year"], text=p["text"], **p["meta"])
        ids[key] = r["object_id"]
    pid = P.new_problem(store, token, statement=statement, **({"inquiry": inquiry} if inquiry else {}))["problem_id"]
    rid = RT.new_run(store, token, pid, terms=terms, top_n=50)["retrieval_id"]
    if search:
        RT.search(store, token, rid)
    return {"store": store, "token": token, "ids": ids, "pid": pid, "rid": rid, "tmp": tmp_path, "home": tmp_path / "home", "name": name}


A_INQUIRY = {"research_purposes": ["critical_synthesis", "theory_development"], "research_design": "narrative_review", "evidence_forms": ["conceptual_argument", "textual_evidence"],
             "discipline": {"domain": "management", "subfield": "organizational_communication"}}
B_INQUIRY = {"research_purposes": ["evaluation", "methodological_improvement"], "research_design": "conceptual", "evidence_forms": ["conceptual_argument", "qualitative_interpretation"],
             "discipline": {"domain": "education", "subfield": "design_based_research"}}
C_INQUIRY = {"research_purposes": ["prediction"], "research_design": "predictive_modeling", "evidence_forms": ["empirical_results"],
             "discipline": {"domain": "accounting", "subfield": "archival_financial_reporting"}}
