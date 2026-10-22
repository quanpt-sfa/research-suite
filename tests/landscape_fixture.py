"""A small invented literature for the landscape tests (M5 WP6), and a scripted reader that returns verbatim records from it.

Planted structure:
  PAT   finds that detection models reduce earnings manipulation (negative);
  QUINN finds that they raise it (positive), naming the same relationship in different capitals, and defines the construct in other words -> a contradiction between
        two works, and definitions that differ;
  REN1/REN2 are two versions of one work (W_REN) whose tone finding reverses between versions -> a version difference, never a contradiction;
  SAM   studies a different relationship (audit fees) -> contradicts nothing;
  ABS   is held as an abstract only -> never read.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from research_harness.discovery import problem as P
from research_harness.discovery import retrieval as RT
from research_harness.knowledge import corpus
from research_harness.state.store import StateStore
from research_harness.workflows.adapters import ModelError, ScriptedModelAdapter

REL = "detection model use -> manipulation"
TONE = "disclosure after detection -> tone"
FEES = "detection model use -> audit fees"
STATEMENT = "Detection models may change earnings manipulation and disclosure by firms."
TERMS = ["detection models earnings manipulation disclosure audit fees"]

S = {
    "pat_def": "We define earnings manipulation as a managerial choice that moves reported earnings away from underlying performance.",
    "pat_mech": "Detection models raise the expected cost of manipulation, so managers manipulate less.",
    "pat_design": "We use a difference-in-differences design around the staggered adoption of a regulator's detection model.",
    "pat_meas": "Manipulation is measured with discretionary accruals from a modified Jones model.",
    "pat_assume": "The design relies on parallel pre-trends between adopting and non-adopting firms, which we test in an event study.",
    "pat_find": "Firms facing a detection model reduce discretionary accruals by 0.8 percentage points of assets.",
    "pat_bound": "The reduction is absent for firms with concentrated ownership.",
    "pat_limit": "Our sample is limited to listed US firms from 2010 to 2020.",
    "pat_open": "Whether managers move to other margins of manipulation remains open.",
    "q_def": "We define earnings manipulation as any action that changes reported earnings without changing cash flows.",
    "q_design": "We use a regression discontinuity around the regulator's size threshold for detection models.",
    "q_find": "Firms facing a detection model increase real earnings management by 1.2 percentage points of sales.",
    "q_bound": "The increase is concentrated among firms with high analyst coverage.",
    "ren1_find": "After detection models spread, disclosures become more optimistic in tone.",
    "ren2_find": "After detection models spread, disclosures become less optimistic in tone.",
    "sam_find": "Audit fees rise by four percent after the regulator adopts a detection model.",
}


def _text(title: str, abstract: str, intro: list[str], method: list[str], results: list[str], limits: list[str], refs: Optional[list[str]] = None) -> str:
    refs = refs or ["Synthetic Author. A Reference Paper on Accruals. Journal of Synthetic Studies."]
    return (f"{title}\n\nAbstract\n{abstract}\n\n1 Introduction\n{' '.join(intro)}\n\n2 Data and method\n{' '.join(method)}\n\n3 Results\n{' '.join(results)}\n\n"
            f"4 Limitations\n{' '.join(limits)}\n\nReferences\n" + "\n".join(f"[{n}] {r}" for n, r in enumerate(refs, 1)) + "\n")


PAPERS: dict[str, dict[str, Any]] = {
    "pat": {"title": "Detection Models Deter Earnings Manipulation", "year": 2023, "meta": {},
            "text": _text("Detection Models Deter Earnings Manipulation", "Detection models and earnings manipulation by firms.",
                          [S["pat_def"], S["pat_mech"]], [S["pat_design"], S["pat_meas"], S["pat_assume"]], [S["pat_find"], S["pat_bound"]], [S["pat_limit"], S["pat_open"]])},
    "quinn": {"title": "Detection Models and Displaced Manipulation", "year": 2024, "meta": {},
              "text": _text("Detection Models and Displaced Manipulation", "Detection models and earnings manipulation by firms.",
                            [S["q_def"]], [S["q_design"]], [S["q_find"], S["q_bound"]], ["Our estimates describe listed firms only."])},
    "ren1": {"title": "Disclosure After Detection Models", "year": 2024,
             "meta": dict(document_type="working_paper", review_status="NOT_REVIEWED", first_public_date="2024-02-01", date_evidence="repository posting record (synthetic)",
                          version_id="v1", work_id="W_REN"),
             "text": _text("Disclosure After Detection Models", "Disclosure by firms after detection models and earnings manipulation: disclosure disclosure disclosure detection models earnings.",
                           ["Disclosure tone is the focus of this paper."], ["We code the tone of annual disclosure."], [S["ren1_find"]], ["Tone coding is automatic."])},
    "ren2": {"title": "Disclosure After Detection Models", "year": 2025,
             "meta": dict(document_type="working_paper", review_status="NOT_REVIEWED", first_public_date="2025-01-15", date_evidence="repository posting record (synthetic)",
                          version_id="v2", work_id="W_REN"),
             "text": _text("Disclosure After Detection Models", "A revised study of tone.",
                           ["Disclosure tone is the focus of this paper."], ["We code the tone of annual disclosure with a new dictionary."], [S["ren2_find"]], ["Tone coding uses a dictionary."])},
    "sam": {"title": "Audit Fees After Detection Models", "year": 2022, "meta": {},
            "text": _text("Audit Fees After Detection Models", "Audit fees, detection models and earnings manipulation.",
                          ["Audit fees reflect audit risk."], ["We use a panel of audit fees."], [S["sam_find"]], ["Fees are observed for listed firms."])},
}

RECORDS: dict[str, dict[str, list[dict[str, Any]]]] = {
    "pat": {"RP_LANDSCAPE": [{"field": "design", "quote": S["pat_design"], "value": "A difference-in-differences design around staggered adoption of a detection model."},
                             {"field": "measurement", "quote": S["pat_meas"], "value": "Manipulation is measured by discretionary accruals (modified Jones model).", "label": "earnings manipulation"},
                             {"field": "identifying_assumption", "quote": S["pat_assume"], "value": "Parallel pre-trends, tested in an event study.", "type": "tested_in_source"},
                             {"field": "finding", "quote": S["pat_find"], "value": "Detection model use reduces discretionary accruals by 0.8 percentage points of assets.",
                              "label": REL, "type": "negative"},
                             {"field": "boundary_condition", "quote": S["pat_bound"], "value": "No reduction for firms with concentrated ownership.", "label": REL},
                             {"field": "stated_limitation", "quote": S["pat_limit"], "value": "Listed US firms, 2010 to 2020 only."},
                             {"field": "open_question", "quote": S["pat_open"], "value": "Whether managers move to other margins of manipulation is open."}],
            "RP_THEORY": [{"field": "construct_definition", "quote": S["pat_def"], "value": "Earnings manipulation: a managerial choice moving reported earnings from performance.",
                           "label": "earnings manipulation"},
                          {"field": "assumed_mechanism", "quote": S["pat_mech"], "value": "Detection raises the expected cost of manipulation, so managers manipulate less."}]},
    "quinn": {"RP_LANDSCAPE": [{"field": "design", "quote": S["q_design"], "value": "A regression discontinuity around the size threshold for detection models."},
                               {"field": "finding", "quote": S["q_find"], "value": "Detection model use raises real earnings management by 1.2 percentage points of sales.",
                                "label": REL.upper(), "type": "positive"},
                               {"field": "boundary_condition", "quote": S["q_bound"], "value": "The increase is concentrated among firms with high analyst coverage.", "label": REL}],
              "RP_THEORY": [{"field": "construct_definition", "quote": S["q_def"], "value": "Earnings manipulation: any action changing reported earnings but not cash flows.",
                             "label": "earnings manipulation"}]},
    "ren1": {"RP_LANDSCAPE": [{"field": "finding", "quote": S["ren1_find"], "value": "Disclosures become more optimistic in tone after detection models spread.",
                               "label": TONE, "type": "positive"}], "RP_THEORY": []},
    "ren2": {"RP_LANDSCAPE": [{"field": "finding", "quote": S["ren2_find"], "value": "Disclosures become less optimistic in tone after detection models spread.",
                               "label": TONE, "type": "negative"}], "RP_THEORY": []},
    "sam": {"RP_LANDSCAPE": [{"field": "finding", "quote": S["sam_find"], "value": "Audit fees rise by four percent after a detection model is adopted.",
                              "label": FEES, "type": "positive"}], "RP_THEORY": []},
}


def reader(*, fail_for: Optional[set[str]] = None, records: Optional[dict] = None, calls: Optional[list] = None, model_id: str = "scripted-landscape-reader") -> ScriptedModelAdapter:
    """A scripted model that answers each reading-protocol request with the planted records for the paper it is shown."""
    recs = records or RECORDS

    def handler(system: str, user: str) -> Any:
        key = re.search(r"Source: (\S+) \(", user).group(1)
        proto = re.search(r"\((RP_[A-Z_]+):", system).group(1)
        if calls is not None:
            calls.append((key, proto))
        if key in (fail_for or set()):
            raise ModelError("scripted failure")
        return {"records": recs.get(key, {}).get(proto, []), "gaps": []}

    return ScriptedModelAdapter(model_id=model_id, custom_handler=handler)


def build_world(tmp_path: Any, *, name: str = "PRJ_LAND", search: bool = True, make_run: bool = True, with_abstract_only: bool = True, **run_kw: Any) -> dict[str, Any]:
    store, token = StateStore.create(tmp_path / "home", name, owner_id="researcher")
    ids = {}
    for key, p in PAPERS.items():
        r = corpus.import_source(store, token, citation_key=key, title=p["title"], year=p["year"], text=p["text"], **p["meta"])
        ids[key] = r["object_id"]
    if with_abstract_only:
        corpus.import_metadata_only(store, token, citation_key="abs", title="Detection Models and Manipulation: An Abstract-Only Study", year=2021,
                                    abstract="We study detection models and earnings manipulation by firms and their disclosure.")
        ids["abs"] = "SRC_ABS"
    pid = P.new_problem(store, token, statement=STATEMENT)["problem_id"]
    run_kw.setdefault("terms", TERMS)
    run_kw.setdefault("top_n", 50)
    rid = RT.new_run(store, token, pid, **run_kw)["retrieval_id"] if make_run else None
    if search and rid:
        RT.search(store, token, rid)
    return {"store": store, "token": token, "ids": ids, "pid": pid, "rid": rid, "tmp": tmp_path, "home": tmp_path / "home"}
