"""Workflow 2 - Literature Review (deterministic evidence gate).

Registers what the project holds about the literature, with exact provenance, and says what has not been done:

* the relationship of a passage to a claim comes from a `supports` / `challenges` relation (either direction) or an
  explicit structured field, otherwise `NOT_ASSESSED`. The wording of a passage is never read as agreement or
  disagreement; lexical cues are listed under `review_flags` and change nothing;
* unknown study designs stay `UNKNOWN`, unreported samples `NOT_REPORTED`;
* a loaded full text is not interpreted here, but the sections that usually qualify a finding (limitations,
  robustness, discussion) are located so that they are read, not skipped;
* a run with no sources and no passages is `INSUFFICIENT_EVIDENCE`, never a success.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from research_harness.workflows.contracts import ContextPackage, LiteratureReviewResult, WorkflowOutput
from research_harness.workflows.evidence_fields import passage_relationship, review_flag
from research_harness.workflows.sections import detect_sections
from research_harness.workflows.skills._common import (
    INSUFFICIENT,
    NEEDS_VERIFICATION,
    SUCCESS,
    assessed_claims,
    budget_gate,
    finish,
    head_objects,
)

_QUALIFYING_SECTIONS = ("limitations", "robustness", "discussion_conclusion")
_METHOD_FIELDS = (("study_design", ("study_design", "methodology")), ("sample_context", ("sample_context", "sample")),
                  ("time_period", ("time_period",)), ("unit", ("unit", "unit_of_analysis")))


def execute_literature_workflow(
    context: ContextPackage,
    *,
    task_id: Optional[str] = None,
    screening_criteria: Optional[dict[str, Any]] = None,
    **kwargs: Any,
) -> WorkflowOutput:
    """Execute the Literature Review evidence gate over structured research context."""
    start_time = time.time()
    tid = task_id or f"TASK_LIT_{uuid.uuid4().hex[:8].upper()}"

    sources = head_objects(context, "Source")
    excerpts = context.excerpts
    theories = head_objects(context, "Theory")
    constructs = head_objects(context, "Construct")
    claim_ids = [c["object_id"] for c in assessed_claims(context)]
    relations = context.relations
    source_dict = {s["object_id"]: s for s in sources}
    fulltext_dict = {ft["source_id"]: ft for ft in context.full_texts}
    passage_content = {(o["object_id"], o["revision"]): o["content"] for o in context.objects if o["object_type"] == "EvidencePassage"}

    evidence_matrix: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    supported: list[str] = []
    disagreements: list[dict[str, Any]] = []
    fulltext_needed: list[str] = []
    review_flags: list[dict[str, Any]] = []

    for ex in excerpts:
        pid, prev = ex.get("passage_id"), ex.get("revision")
        sid = ex.get("source_id") or "UNKNOWN_SRC"
        s_obj = source_dict.get(sid)
        s_rev = s_obj["revision"] if s_obj else None
        citation = s_obj["content"].get("citation_key", sid) if s_obj else sid
        title = s_obj["content"].get("bibliographic_identity") or s_obj["content"].get("title") if s_obj else None
        text = ex.get("excerpt") or ex.get("quote") or ex.get("normalized_claim", "")
        locator = ex.get("locator", "UNSPECIFIED")
        content = passage_content.get((pid, prev), {})

        relationship, links = passage_relationship(pid, content, claim_ids, relations)
        ft_status = fulltext_dict.get(sid, {}).get("fulltext_status", "NOT_VERIFIED")
        evidence_matrix.append({
            "passage_id": pid, "passage_revision": prev, "source_id": sid, "source_revision": s_rev, "citation": citation,
            "locator": locator, "extracted_claim": ex.get("normalized_claim") or text[:120],
            "normalized_finding": ex.get("normalized_claim") or text[:120],
            "evidence_type": content.get("evidence_type") or "UNKNOWN",
            "relationship": relationship, "linked_claims": links, "evidence_conflict": relationship == "CHALLENGES_CLAIM",
            "fulltext_inspection_status": ft_status, "fulltext_available": ft_status == "FULLTEXT_LOADED",
            "assessment_provenance": f"Passage {pid}:rev{prev} from Source {sid}" + (f":rev{s_rev}" if s_rev else ""),
        })
        provenance.append({"source_id": sid, "revision": s_rev, "citation_key": citation, "bibliographic_identity": title,
                           "locator": locator, "passage_id": pid, "is_full_text": ft_status == "FULLTEXT_LOADED",
                           "fulltext_status": ft_status})
        if relationship == "SUPPORTS_CLAIM":
            supported.append(f"[{citation}, {locator}] {ex.get('normalized_claim') or text[:100]}")
        elif relationship in {"CHALLENGES_CLAIM", "CONFLICTING_LINKS"}:
            disagreements.append({"source_a": citation, "locator_a": locator, "conflicting_finding": text,
                                  "status": "UNRESOLVED_EMPIRICAL_DISAGREEMENT", "linked_claims": links})
        flag = review_flag(pid, prev, text, "excerpt")
        if flag:
            review_flags.append(flag)
        if not locator or locator in {"UNSPECIFIED", "Unspecified locator"}:
            fulltext_needed.append(f"Source {citation} lacks a precise locator for {pid}; verify the original document text.")
        elif ft_status != "FULLTEXT_LOADED":
            fulltext_needed.append(f"Source {citation} lacks inspected full text (status: {ft_status}); verification plan required.")

    for s in sources:
        ft = fulltext_dict.get(s["object_id"], {})
        if ft.get("fulltext_status") != "FULLTEXT_LOADED":
            fulltext_needed.append(
                f"Source {s['content'].get('citation_key', s['object_id'])} ({s['object_id']}) is available as "
                f"{ft.get('fulltext_status', 'abstract/excerpt')}; full-text retrieval required for complete empirical verification.")

    # Methods and contexts reviewed (missing => UNKNOWN / NOT_REPORTED) and where studies differ.
    methods: list[dict[str, Any]] = []
    for s in sources:
        c = s["content"]
        pick = lambda keys, default: next((c[k] for k in keys if c.get(k)), default)  # noqa: E731
        methods.append({"source_id": s["object_id"], "revision": s["revision"], "citation_key": c.get("citation_key", s["object_id"]),
                        "study_design": pick(_METHOD_FIELDS[0][1], "UNKNOWN"), "sample_context": pick(_METHOD_FIELDS[1][1], "NOT_REPORTED"),
                        "time_period": pick(_METHOD_FIELDS[2][1], "NOT_REPORTED"), "unit": pick(_METHOD_FIELDS[3][1], "NOT_REPORTED")})
    unknown = {"UNKNOWN", "NOT_REPORTED"}
    difference_matrix = []
    for name, _keys in _METHOD_FIELDS:
        values = {m["citation_key"]: m[name] for m in methods}
        reported = {v for v in values.values() if v not in unknown}
        difference_matrix.append({"field": name, "values": values, "differs": len(reported) > 1,
                                  "not_reported_in": sorted(k for k, v in values.items() if v in unknown)})

    # Sections of loaded / deferred full texts that usually qualify a finding.
    sections_to_review: list[dict[str, Any]] = []
    for sid, ft in sorted(fulltext_dict.items()):
        cite = ft.get("citation_key", sid)
        if ft.get("full_text"):
            secs = [s.to_dict() for s in detect_sections(ft["full_text"])]
            state = "LOADED"
        else:
            secs, state = ft.get("sections") or [], "NOT_LOADED"
        sections_to_review.extend(
            {"source_id": sid, "citation_key": cite, "label": s["label"], "heading": s["heading"], "char_start": s["char_start"],
             "char_end": s["char_end"], "locator": f"{sid}[{s['char_start']}:{s['char_end']}]", "text_state": state}
            for s in secs if s["label"] in _QUALIFYING_SECTIONS)

    n_challenge = len([r for r in evidence_matrix if r["relationship"] == "CHALLENGES_CLAIM"])
    n_unassessed = len([r for r in evidence_matrix if r["relationship"] == "NOT_ASSESSED"])
    synthesis = [f"{len(sources)} source(s) and {len(excerpts)} evidence passage(s) are registered."]
    if theories:
        synthesis.append("Theories recorded: " + ", ".join(t["content"].get("label", t["object_id"]) for t in theories) + ".")
    if constructs:
        synthesis.append("Constructs recorded: " + ", ".join(c["content"].get("label", c["object_id"]) for c in constructs) + ".")
    synthesis.append(f"{len(supported)} passage(s) are linked as supporting and {n_challenge} as challenging a claim under "
                     f"assessment; {n_unassessed} are not linked to any claim and are NOT_ASSESSED.")
    synthesis.append("Agreement or disagreement across sources has not been assessed: wording is not read as evidence of "
                     "either, and the sections listed under sections_requiring_review have not been interpreted.")

    result_data = LiteratureReviewResult(
        evidence_matrix=evidence_matrix, source_provenance_and_locators=provenance, methods_and_contexts_reviewed=methods,
        supported_findings=supported, unresolved_disagreements=disagreements, theoretical_synthesis=" ".join(synthesis),
        evidence_limitations=[
            "Passage-level extractions are the recorded statements; they are not a reading of each paper's design.",
            "No search protocol is recorded: this run registers and compares the corpus it was given and is not a systematic review.",
        ],
        questions_requiring_fulltext_verification=fulltext_needed)
    out = result_data.to_dict()
    out.update({"source_provenance": provenance, "methods_and_contexts": methods, "fulltext_retrieval_needed": fulltext_needed,
                "method_difference_matrix": difference_matrix, "sections_requiring_review": sections_to_review,
                "review_flags": review_flags})

    has_gap = bool(fulltext_needed) or n_unassessed > 0
    status = budget_gate(context, INSUFFICIENT if not sources and not excerpts else (NEEDS_VERIFICATION if has_gap else SUCCESS))
    return finish("literature", tid, context, out, status, start_time,
                  ["Evidence registered with provenance; relationships come from relations and structured fields, never from wording."])
