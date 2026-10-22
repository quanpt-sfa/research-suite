"""Independent synthetic fixtures for the WP6.2 claim-inventory tests. Every paper, author and number is invented; nothing is from the literature.

Four papers carry the planted structure the tests look for:

* `cr` (critical review): sentences that REPORT an earlier work, CRITIQUE it, and argue the authors' OWN position, in one paragraph; a claim whose condition sits in another sentence;
  a compound claim; a definition; a stated assumption; a gap statement; a stated limitation.
* `cr_pre` / `cr_journal` (two versions of one work): one claim unchanged, one qualified, one weakened, one reversed, one only in the preprint, one only in the journal version.
* `edu` (education): an interpretive finding, and a completion claim next to the limitation that completion does not show learning.
* `fraud` (archival): a predictive claim, label provenance, a temporal split, and the limitation that a ranking of restatement labels is not an identification of fraud.

The scripted reader answers from a script keyed by the source's citation key; it records every call so a test can count them.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from research_harness.knowledge import corpus
from research_harness.state.store import StateStore
from research_harness.workflows.adapters import ModelError, ScriptedModelAdapter


def paper(title: str, abstract: str, sections: list[tuple[str, list[str]]]) -> str:
    body = "\n\n".join(f"{h}\n{' '.join(ss)}" for h, ss in sections)
    return f"{title}\n\nAbstract\n{abstract}\n\n{body}\n\nReferences\n[1] Invented Author. An Invented Reference. Journal of Invented Studies.\n"


# ------------------------------------------------------------------------------------------------ cr: a critical review
CR = {
    "rep1": "Alder (2018) argues that collective attention is a finite resource that group members divide among shared topics.",
    "rep2": "Previous studies have established that frequent messaging increases perceived responsiveness (Brandt, 2016; Corvo, 2019).",
    "crit1": "However, Alder's account treats every message as drawing equally on that pool, which does not hold where messages cost almost nothing to produce.",
    "own1": "We argue that communicative participation should be separated from relational engagement.",
    "own1_q": "This separation holds when the cost of producing a message is low and the audience cannot observe the sender's effort.",
    "def1": "We define communicative participation as contributing a message to a shared channel, whatever the effort behind it.",
    "assume1": "We assume that recipients cannot tell effortful messages from effortless ones without further cues.",
    "gap1": "No account yet explains how engagement can persist when most messages are produced with machine assistance.",
    "contr1": "This paper contributes a distinction between participation and engagement that organises both literatures.",
    "comp1": "We propose that participation raises visibility and that, in small teams, visibility raises attention to the sender, but only when messages are scarce.",
    "lim1": "The argument is conceptual and has not been tested with data.",
    "evid1": "In the shared-channel cases described above, senders who wrote less were thanked as often as senders who wrote more.",
}
CR_TEXT = paper("Participation Without Engagement", "A conceptual distinction for work communication.", [
    ("1 Introduction", [CR["rep1"], CR["rep2"], CR["crit1"], CR["own1"], CR["own1_q"], CR["gap1"]]),
    ("2 A framework", [CR["def1"], CR["assume1"], CR["comp1"], CR["evid1"]]),
    ("3 Contribution", [CR["contr1"]]),
    ("4 Limitations", [CR["lim1"]])])

# ------------------------------------------------------------------------------------------------ two versions of one work
V = {
    "same": "We argue that engagement is an investment of the self in particular working relationships.",
    "q_pre": "Participation raises visibility.",
    "q_post": "Participation raises visibility in small teams.",
    "w_pre": "Participation always dilutes attention.",
    "w_post": "Participation can dilute attention.",
    "r_pre": "Machine assistance increases engagement.",
    "r_post": "Machine assistance reduces engagement.",
    "only_pre": "We also claim that channel size is irrelevant.",
    "only_post": "We add that tone moderates the effect.",
}
PRE_TEXT = paper("Engagement and Visibility at Work", "Engagement, visibility and attention.", [
    ("1 Introduction", [V["same"], V["q_pre"], V["w_pre"]]), ("2 Propositions", [V["r_pre"], V["only_pre"]])])
JOURNAL_TEXT = paper("Engagement and Visibility at Work", "Engagement, visibility and attention.", [
    ("1 Introduction", [V["same"], V["q_post"], V["w_post"]]), ("2 Propositions", [V["r_post"], V["only_post"]])])
PRE_META = dict(document_type="working_paper", review_status="NOT_REVIEWED", first_public_date="2023-02-01", date_evidence="repository posting record (invented)", version_id="preprint", work_id="W_ENG")
JOURNAL_META = dict(document_type="journal_article", review_status="PUBLISHED", first_public_date="2024-05-10", date_evidence="DOI registration (invented)", version_id="journal", work_id="W_ENG")

# ------------------------------------------------------------------------------------------------ education
EDU = {
    "interp": "Students described checking the output as reading it once and asking whether it looked right.",
    "done": "All twelve students produced a complete dashboard within the session.",
    "lim": "Completing a dashboard does not show that the student can judge whether it is correct.",
    "design": "We interviewed twelve undergraduate students after a three-hour agentic-development session.",
}
EDU_TEXT = paper("Checking What Agents Build", "Students, agentic tools and evaluative judgement.", [
    ("1 Introduction", ["Educators ask what students learn when tools write the code."]), ("2 Method", [EDU["design"]]),
    ("3 Findings", [EDU["interp"], EDU["done"]]), ("4 Limitations", [EDU["lim"]])])

# ------------------------------------------------------------------------------------------------ archival fraud detection
FR = {
    "pred": "The model reaches an average precision of 0.41 on out-of-time filings.",
    "label": "Fraud labels derive from enforcement releases, which appear years after the misreporting.",
    "split": "Training used filings from 2005 to 2012 and testing used filings from 2013 to 2016.",
    "lim": "These scores do not show that the model identifies fraud, only that it ranks restatement labels.",
}
FR_TEXT = paper("Ranking Restatements Out of Time", "Predictive models of reporting problems.", [
    ("1 Introduction", ["Detection models are usually evaluated within the same years."]), ("2 Data and method", [FR["label"], FR["split"]]),
    ("3 Results", [FR["pred"]]), ("4 Limitations", [FR["lim"]])])

PAPERS: dict[str, dict[str, Any]] = {
    "cr": {"title": "Participation Without Engagement", "year": 2024, "meta": {}, "text": CR_TEXT},
    "pre": {"title": "Engagement and Visibility at Work", "year": 2023, "meta": PRE_META, "text": PRE_TEXT},
    "jour": {"title": "Engagement and Visibility at Work", "year": 2024, "meta": JOURNAL_META, "text": JOURNAL_TEXT},
    "edu": {"title": "Checking What Agents Build", "year": 2025, "meta": {}, "text": EDU_TEXT},
    "fraud": {"title": "Ranking Restatements Out of Time", "year": 2025, "meta": {}, "text": FR_TEXT},
}
OWN, REP, CRIT = "AUTHOR_OWN_CLAIM", "AUTHOR_REPORTS_PRIOR_WORK", "AUTHOR_CRITIQUES_PRIOR_WORK"


def rec(field: str, quote: str, attribution: Optional[str] = OWN, value: Optional[str] = None, *, label: Optional[str] = None, extra: Optional[list[dict]] = None, **attributes: Any) -> dict[str, Any]:
    """One scripted record, shaped as a model's answer to RP_CLAIMS."""
    r: dict[str, Any] = {"field": field, "value": value or quote, "quote": quote}
    if attribution:
        r["type"] = attribution
    if label:
        r["label"] = label
    if attributes:
        r["attributes"] = attributes
    if extra:
        r["extra_quotes"] = extra
    return r


# the answers a careful model could give for the four papers (the tests change them to plant mistakes)
def cr_records() -> list[dict[str, Any]]:
    return [
        rec("theoretical_proposition", CR["rep1"], REP, "Alder holds that attention is a finite resource shared among group members.", cited_works=["Alder (2018)"], constructs=["collective attention"],
            attribution_cue="Alder (2018) argues that"),
        rec("empirical_finding", CR["rep2"], REP, "Earlier studies found that frequent messaging raises perceived responsiveness.", cited_works=["Brandt, 2016", "Corvo, 2019"],
            constructs=["messaging frequency", "perceived responsiveness"], direction="positive"),
        rec("theoretical_proposition", CR["crit1"], CRIT, "Alder's account fails where messages cost almost nothing to produce.", cited_works=["Alder's account"],
            boundary_conditions=["messages cost almost nothing to produce"], premises=["Alder treats every message as drawing equally on the pool"]),
        rec("theoretical_proposition", CR["own1"], OWN, "Participation and engagement should be treated as different things.", constructs=["communicative participation", "relational engagement"],
            granularity="ATOMIC", extra=[{"quote": CR["own1_q"], "role": "QUALIFIER", "why": "states when the separation holds"}]),
        rec("gap_statement", CR["gap1"], OWN, "No account explains how engagement persists under machine assistance.", constructs=["relational engagement", "machine assistance"]),
        rec("definition", CR["def1"], OWN, "Participation is contributing a message to a shared channel.", label="communicative participation", constructs=["communicative participation"]),
        rec("assumption", CR["assume1"], OWN, "Recipients cannot tell effort from its absence without cues.", premises=["no further cues are available"]),
        rec("theoretical_proposition", CR["comp1"], OWN, "Participation raises visibility; in small teams visibility raises attention to the sender when messages are scarce.", granularity="COMPOUND",
            components=[{"text": "participation raises visibility", "conditions": []},
                        {"text": "visibility raises attention to the sender", "conditions": ["small teams", "messages are scarce"]}], scope={"setting": "small teams"}),
        rec("claimed_contribution", CR["contr1"], OWN, "The paper distinguishes participation from engagement."),
        rec("limitation", CR["lim1"], OWN, "The argument has not been tested with data."),
    ]


def pre_records() -> list[dict[str, Any]]:
    return [rec("theoretical_proposition", V["same"], OWN, constructs=["relational engagement"]), rec("theoretical_proposition", V["q_pre"], OWN, constructs=["visibility"]),
            rec("theoretical_proposition", V["w_pre"], OWN, constructs=["attention"]), rec("theoretical_proposition", V["r_pre"], OWN, constructs=["machine assistance", "engagement"]),
            rec("theoretical_proposition", V["only_pre"], OWN, constructs=["channel size"])]


def journal_records() -> list[dict[str, Any]]:
    return [rec("theoretical_proposition", V["same"], OWN, constructs=["relational engagement"]), rec("theoretical_proposition", V["q_post"], OWN, constructs=["visibility"], scope={"setting": "small teams"}),
            rec("theoretical_proposition", V["w_post"], OWN, constructs=["attention"]), rec("theoretical_proposition", V["r_post"], OWN, constructs=["machine assistance", "engagement"]),
            rec("theoretical_proposition", V["only_post"], OWN, constructs=["tone"])]


def edu_records() -> list[dict[str, Any]]:
    return [rec("interpretive_finding", EDU["interp"], OWN, "Students treated checking as a quick plausibility reading.", constructs=["checking", "evaluative judgement"]),
            rec("empirical_finding", EDU["done"], OWN, "Every student produced a complete dashboard.", direction="descriptive", scope={"population": "twelve undergraduate students"}),
            rec("limitation", EDU["lim"], OWN, "Completion does not show the student can judge correctness."), rec("methodological_claim", EDU["design"], OWN, "Twelve students were interviewed after a session.")]


def fraud_records() -> list[dict[str, Any]]:
    return [rec("empirical_finding", FR["pred"], OWN, "Average precision was 0.41 out of time.", direction="descriptive", constructs=["average precision"]),
            rec("methodological_claim", FR["label"], OWN, "Labels come from enforcement releases that lag the misreporting.", constructs=["label validity"]),
            rec("methodological_claim", FR["split"], OWN, "Training 2005-2012, testing 2013-2016.", constructs=["temporal split"], scope={"period": "2013 to 2016"}),
            rec("limitation", FR["lim"], OWN, "The scores rank restatement labels; they do not identify fraud.", constructs=["identification"])]


SCRIPT: dict[str, dict[str, Any]] = {"cr": {"RP_CLAIMS": cr_records}, "pre": {"RP_CLAIMS": pre_records}, "jour": {"RP_CLAIMS": journal_records}, "edu": {"RP_CLAIMS": edu_records},
                                     "fraud": {"RP_CLAIMS": fraud_records}}


def reader(script: Optional[dict[str, dict[str, Any]]] = None, *, model_id: str = "scripted-claims-reader", calls: Optional[list] = None, fail_for: Optional[set[str]] = None,
           evidence: Optional[dict[str, list[dict[str, Any]]]] = None) -> ScriptedModelAdapter:
    """`script[source key][protocol]` is a list of records or a callable returning one. `evidence[claim text fragment]` answers `RP_CLAIM_EVIDENCE` calls."""
    script = SCRIPT if script is None else script

    def handler(system: str, user: str) -> Any:
        key = re.search(r"Source: (\S+) \(", user).group(1)
        proto = re.search(r"\((RP_[A-Z_]+):", system).group(1)
        if calls is not None:
            calls.append((key, proto))
        if key in (fail_for or set()):
            raise ModelError("scripted failure")
        if proto == "RP_CLAIM_EVIDENCE":
            claim_line = next((ln for ln in user.splitlines() if ln.startswith("Claim:")), "")
            for frag, recs in (evidence or {}).items():
                if frag in claim_line:
                    return {"records": recs, "gaps": []}
            return {"records": [], "gaps": ["nothing bears on this claim in the text shown"]}
        recs = script.get(key, {}).get(proto, [])
        return {"records": recs() if callable(recs) else recs, "gaps": []}
    return ScriptedModelAdapter(model_id=model_id, custom_handler=handler)


def make_store(tmp_path: Any, papers: Optional[dict[str, dict[str, Any]]] = None, name: str = "PRJ_CLAIMS") -> dict[str, Any]:
    store, token = StateStore.create(tmp_path / "home", name, owner_id="researcher")
    ids = {}
    for key, p in (PAPERS if papers is None else papers).items():
        r = corpus.import_source(store, token, citation_key=key, title=p["title"], year=p["year"], text=p["text"], **p["meta"])
        ids[key] = r["object_id"]
    return {"store": store, "token": token, "ids": ids, "home": tmp_path / "home"}
