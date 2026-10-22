"""Fixtures for the WP6.1 remediation tests: a review protocol for the invented critical-review corpus, and extra invented papers for the recognition cases.

Everything here is invented. The papers are not from the literature; the search sources named are placeholders for a test.
"""
from __future__ import annotations

import copy
from typing import Any

from research_harness.discovery import landscape as L
from research_harness.knowledge import corpus
from research_harness.knowledge import review_protocol as RP
from tests import inquiry_fixtures as F

PROTOCOL_A: dict[str, Any] = {
    "review_type": "critical_systematic",
    "streams": [
        {"id": "S1", "name": "Collective attention", "question": "How is collective attention conceptualised, and what does participation do to it?", "terms": ["collective attention", "attention allocation"]},
        {"id": "S2", "name": "Communicative participation", "question": "How is participation in a shared channel conceptualised?", "terms": ["communicative participation", "voice", "visibility"]},
        {"id": "S3", "name": "Relational engagement", "question": "How is relational engagement conceptualised and what is assumed to express it?", "terms": ["relational engagement", "social exchange"]},
        {"id": "S4", "name": "AI-mediated communication", "question": "What is known about messages produced with machine assistance?", "terms": ["AI-mediated communication", "machine-assisted messages"]}],
    "search": {"sources": [
        {"name": "Database X (placeholder)", "kind": "database", "status": "executed", "records_identified": 150,
         "queries": [{"stream": "S1", "string": "\"collective attention\" AND (channel OR team)", "date_run": "2026-10-01"}, {"stream": "S3", "string": "\"relational engagement\" AND work", "date_run": "2026-10-01"}]},
        {"name": "Citation chasing from the seed studies", "kind": "citation_chasing", "status": "executed", "records_identified": 30, "queries": [{"stream": "S2", "string": "backward and forward from seeds"}]}],
        "date_from": "2000-01-01", "date_to": "2026-09-30", "languages": ["English"], "inclusion_scope": "peer-reviewed articles and working papers in management, communication and information systems"},
    "screening": {"procedure": "one researcher screens titles and abstracts, then full texts; borderline records are re-read after a week", "stages": ["dedup", "screening", "eligibility"],
                  "criteria": {"include": ["theorises or tests a relation among participation, attention or engagement in organisations", "full text available"],
                               "exclude": ["no theoretical or empirical treatment of the constructs", "not about work or organisations"]}},
    "appraisal": {"performed": False, "reason_if_not": "a critical synthesis interrogates concepts and assumptions across designs; no common quality instrument applies to theory papers and experiments alike"},
    "extraction": {"fields": ["theory invoked", "construct definitions", "stated assumptions", "mechanisms", "theoretical claims", "boundary conditions"], "protocols": ["RP_LANDSCAPE@2", "RP_THEORY@3"]},
    "synthesis": {"method": "reconstruct each theory and its assumptions, compare scopes and levels of analysis, propose tensions, inspect whether the reviewed literature already addresses them",
                  "how_tensions_are_judged": "a tension is proposed from quoted passages, kept unconfirmed until the researcher decides, and recognised as addressed only on an inspection record with verbatim evidence"},
    "limitations": ["the database named here is a placeholder in this test"],
}


def screening_log(extra: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    log = [{"record": "dup-1", "stage": "dedup", "decision": "exclude", "reason": "duplicate record"}, {"record": "dup-2", "stage": "dedup", "decision": "exclude", "reason": "duplicate record"},
           {"record": "off-topic-1", "stage": "screening", "decision": "exclude", "reason": "not about work or organisations"}]
    for key, stream in (("att", "S1"), ("part", "S2"), ("eng1", "S3"), ("eng2", "S3"), ("ai", "S4")):
        log.append({"record": key, "stage": "screening", "decision": "include", "stream": stream})
        log.append({"record": key, "stage": "eligibility", "decision": "include", "stream": stream})
    return log + list(extra or [])


def adopt_protocol(w: dict[str, Any], *, adopt: bool = True, drafted_by: str = "assistant:test", log: list[dict[str, Any]] | None = None) -> None:
    RP.set_review_protocol(w["store"], w["token"], w["pid"], copy.deepcopy(PROTOCOL_A), "record the plan", drafted_by=drafted_by)
    RP.record_screening(w["store"], w["token"], w["pid"], screening_log() if log is None else log, "first pass")
    if adopt:
        RP.adopt_review_protocol(w["store"], w["token"], w["pid"], "this is the plan I will follow")


# ------------------------------------------------------------------------------------------------ extra invented papers for the recognition cases
R = {
    "mention_a": "Message volume in team channels has grown, and participation rates differ across teams.",
    "mention_b": "We describe how often employees post but take no position on what posting presupposes.",
    "wp_arg": "We propose that participation in a channel should be separated from relational engagement, because a message can be sent without personal investment.",
    "j_withdraw": "An earlier version proposed separating participation from relational engagement; we do not defend that separation here and treat sending a message as an expression of engagement.",
    "j_scope": "Our evidence concerns face-to-face teams only.",
}
EXTRA_PAPERS: dict[str, dict[str, Any]] = {
    "mention": {"title": "Notes on Message Volume in Team Channels", "year": 2021, "meta": {},
                "text": F._paper("Notes on Message Volume in Team Channels", "Participation counts in team channels and relational engagement.",
                                 [("1 Introduction", [R["mention_a"]]), ("2 Counts", [R["mention_b"]])])},
    "wpx": {"title": "Sending Is Not Investing", "year": 2022,
            "meta": dict(document_type="working_paper", review_status="NOT_REVIEWED", first_public_date="2022-05-01", date_evidence="repository posting record (invented)", version_id="v1", work_id="W_SEND"),
            "text": F._paper("Sending Is Not Investing", "Participation and relational engagement in channels.", [("1 Introduction", ["Channels lower the cost of speaking."]), ("2 Argument", [R["wp_arg"]])])},
    "jx": {"title": "Sending Is Not Investing", "year": 2024,
           "meta": dict(document_type="journal_article", review_status="PUBLISHED", first_public_date="2024-03-01", date_evidence="DOI registration (invented)", version_id="journal", work_id="W_SEND"),
           "text": F._paper("Sending Is Not Investing", "Participation and relational engagement in channels.", [("1 Introduction", ["Channels lower the cost of speaking."]), ("2 Position", [R["j_withdraw"], R["j_scope"]])])},
}


def add_extra_papers(w: dict[str, Any]) -> None:
    for key, p in EXTRA_PAPERS.items():
        corpus.import_source(w["store"], w["token"], citation_key=key, title=p["title"], year=p["year"], text=p["text"], **p["meta"])


def add_abstract_only(w: dict[str, Any]) -> None:
    corpus.import_metadata_only(w["store"], w["token"], citation_key="absonly", title="Participation Without Engagement in Shared Channels: A Conceptual Account", year=2023,
                                abstract="We distinguish participation from relational engagement where messages are cheap to produce.")


def div_proposal(v: dict[str, Any], **kw: Any) -> dict[str, Any]:
    """A conceptual divergence between the stated assumptions of attention theory and engagement theory, with no addressed_by hint."""
    ent = lambda src, fld: next(e["id"] for e in v["entries"] if e["source_id"] == src and e["field"] == fld)   # noqa: E731
    base = {"kind": "conceptual_divergence", "topic": "what participation presupposes", "entries": [ent("SRC_ATT", "stated_assumption"), ent("SRC_ENG1", "stated_assumption")],
            "shared": "Both concern what communicating in a shared channel presupposes about the sender.",
            "differences": "Attention theory treats participation as drawing on a shared pool whatever its origin; engagement theory treats communication as effortful and attributable.",
            "rationale": "If messages can be produced without effort, the second assumption no longer follows from participation, while the first still does.",
            "missing_information": ["whether either theory addresses low-effort messages"], "levels": {}, "meaning_difference": "participation is attention use for one, an expression of personal investment for the other",
            "already_addressed_by": [], "search_terms": ["participation", "engagement", "effort"]}
    return {**base, **kw}


def build_landscape(w: dict[str, Any]) -> str:
    return L.build(w["store"], w["token"], w["rid"], F.reader(F.A_RECORDS), versions="all")["landscape_id"]
