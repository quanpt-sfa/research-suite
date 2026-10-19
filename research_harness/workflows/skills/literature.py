"""Workflow 2 — Literature Review Implementation.

Retrieves, evaluates, and synthesizes relevant scientific literature into a structured
evidence matrix with verified provenance, locators, and on-demand full-text inspection.
Follows Part B2 rules: unknown designs remain UNKNOWN, unreported samples remain
NOT_REPORTED, and lack of contradiction keywords does not imply consensus.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional

from research_harness.workflows.contracts import (
    ContextPackage,
    LiteratureReviewResult,
    TaskUsage,
    WorkflowOutput,
)
from research_harness.workflows.instrumentation import estimate_tokens


def execute_literature_workflow(
    context: ContextPackage,
    *,
    task_id: Optional[str] = None,
    screening_criteria: Optional[dict[str, Any]] = None,
    **kwargs: Any,
) -> WorkflowOutput:
    """Execute Literature Review workflow deterministically over structured research context."""
    start_time = time.time()
    tid = task_id or f"TASK_LIT_{uuid.uuid4().hex[:8].upper()}"

    sources = [o for o in context.objects if o["object_type"] == "Source"]
    excerpts = context.excerpts
    claims = [o for o in context.objects if o["object_type"] == "Claim"]
    theories = [o for o in context.objects if o["object_type"] == "Theory"]
    constructs = [o for o in context.objects if o["object_type"] == "Construct"]

    evidence_matrix: list[dict[str, Any]] = []
    source_provenance: list[dict[str, Any]] = []
    methods_and_contexts: list[dict[str, Any]] = []
    supported_findings: list[str] = []
    unresolved_disagreements: list[dict[str, Any]] = []
    fulltext_needed: list[str] = []

    source_dict = {s["object_id"]: s for s in sources}
    fulltext_dict = {ft["source_id"]: ft for ft in context.full_texts}

    for ex in excerpts:
        sid = ex.get("source_id") or "UNKNOWN_SRC"
        s_obj = source_dict.get(sid)
        s_rev = s_obj["revision"] if s_obj else 1
        citation = s_obj["content"].get("citation_key") if s_obj else sid
        title = s_obj["content"].get("bibliographic_identity") if s_obj else "Document"

        passage_text = ex.get("excerpt") or ex.get("quote") or ex.get("normalized_claim", "")
        locator = ex.get("locator", "UNSPECIFIED")

        # Determine evidence orientation without assuming support simply due to lack of keywords
        is_contradictory = any(term in passage_text.lower() for term in ["contrary", "fails to find", "refutes", "negative correlation", "no effect", "conflict", "opposing"])
        is_explicitly_supporting = any(term in passage_text.lower() for term in ["confirms", "supports", "consistent with", "replicates", "validates"])

        if is_contradictory:
            rel = "contradicts_prior"
        elif is_explicitly_supporting:
            rel = "supports_prior"
        else:
            rel = "NOT_ASSESSED"

        ft_entry = fulltext_dict.get(sid, {})
        ft_status = ft_entry.get("fulltext_status", "NOT_VERIFIED")

        matrix_row = {
            "passage_id": ex.get("passage_id"),
            "source_id": sid,
            "source_revision": s_rev,
            "citation": citation,
            "locator": locator,
            "extracted_claim": ex.get("normalized_claim", passage_text[:120]),
            "normalized_finding": ex.get("normalized_claim", passage_text[:120]),
            "evidence_type": "empirical_finding" if "data" in passage_text.lower() or "sample" in passage_text.lower() else ("theoretical_statement" if "theory" in passage_text.lower() else "UNKNOWN"),
            "relationship": rel,
            "evidence_conflict": is_contradictory,
            "fulltext_inspection_status": ft_status,
            "fulltext_available": sid in fulltext_dict and ft_status == "FULLTEXT_LOADED",
            "assessment_provenance": f"Passage {ex.get('passage_id')} from Source {sid}:rev{s_rev}",
        }
        evidence_matrix.append(matrix_row)

        source_provenance.append({
            "source_id": sid,
            "revision": s_rev,
            "citation_key": citation,
            "bibliographic_identity": title,
            "locator": locator,
            "passage_id": ex.get("passage_id"),
            "is_full_text": ft_status == "FULLTEXT_LOADED",
            "fulltext_status": ft_status,
        })

        if is_contradictory:
            unresolved_disagreements.append({
                "source_a": citation,
                "locator_a": locator,
                "conflicting_finding": passage_text,
                "status": "UNRESOLVED_EMPIRICAL_DISAGREEMENT",
            })
        elif is_explicitly_supporting:
            supported_findings.append(f"[{citation}, {locator}] {ex.get('normalized_claim') or passage_text[:100]}")

        # Check if passage requires full-text verification
        if not locator or locator in {"UNSPECIFIED", "Unspecified locator"} or len(passage_text) < 40:
            fulltext_needed.append(f"Source {citation} lacks precise page locator; verify original document text.")
        elif ft_status != "FULLTEXT_LOADED":
            fulltext_needed.append(f"Source {citation} lacks inspected full text (status: {ft_status}); verification plan required.")

    # Identify sources present only as abstract/excerpt
    for s in sources:
        sid = s["object_id"]
        ckey = s["content"].get("citation_key", sid)
        ft_info = fulltext_dict.get(sid, {})
        if ft_info.get("fulltext_status") != "FULLTEXT_LOADED":
            fulltext_needed.append(
                f"Source {ckey} ({sid}) is available as {ft_info.get('fulltext_status', 'abstract/excerpt')}; full-text retrieval required for complete empirical verification."
            )

    # 2. Extract Methods and Contexts Reviewed (Part B2: Missing -> UNKNOWN / NOT_REPORTED)
    for s in sources:
        s_content = s["content"]
        study_design = s_content.get("study_design") or s_content.get("methodology") or "UNKNOWN"
        sample_ctx = s_content.get("sample_context") or s_content.get("sample") or "NOT_REPORTED"
        time_per = s_content.get("time_period") or "NOT_REPORTED"

        methods_and_contexts.append({
            "source_id": s["object_id"],
            "revision": s["revision"],
            "citation_key": s_content.get("citation_key", s["object_id"]),
            "study_design": study_design,
            "sample_context": sample_ctx,
            "time_period": time_per,
        })

    # 3. Theoretical Synthesis (Part B2: No fabricated thematic consensus)
    synthesis_lines = []
    if theories:
        synthesis_lines.append(f"Theoretical foundations are anchored by {len(theories)} framework(s): " +
                               ", ".join(t["content"].get("label", t["object_id"]) for t in theories) + ".")
    if constructs:
        synthesis_lines.append(f"Literature investigates {len(constructs)} core construct(s): " +
                               ", ".join(c["content"].get("label", c["object_id"]) for c in constructs) + ".")
    synthesis_lines.append(f"A total of {len(sources)} source(s) and {len(excerpts)} evidence passage(s) were analyzed.")
    if unresolved_disagreements:
        synthesis_lines.append(f"The literature exhibits {len(unresolved_disagreements)} documented point(s) of empirical divergence requiring resolution.")
    else:
        synthesis_lines.append("Reviewed sources do not report explicit lexical contradictions; empirical agreement remains provisional pending full-text verification.")

    theoretical_synthesis = " ".join(synthesis_lines)

    # 4. Evidence Limitations
    evidence_limitations = [
        "Synthesized evidence relies on reported sample contexts; generalizability remains bounded.",
        "Passage-level extractions reflect documented excerpted statements; full-text methodological review is required for replication.",
    ]

    result_data = LiteratureReviewResult(
        evidence_matrix=evidence_matrix,
        source_provenance_and_locators=source_provenance,
        methods_and_contexts_reviewed=methods_and_contexts,
        supported_findings=supported_findings,
        unresolved_disagreements=unresolved_disagreements,
        theoretical_synthesis=theoretical_synthesis,
        evidence_limitations=evidence_limitations,
        questions_requiring_fulltext_verification=fulltext_needed,
    )

    out_dict = result_data.to_dict()
    # Provide aliases for backwards and test compatibility
    out_dict["source_provenance"] = source_provenance
    out_dict["methods_and_contexts"] = methods_and_contexts
    out_dict["fulltext_retrieval_needed"] = fulltext_needed

    elapsed_ms = int((time.time() - start_time) * 1000)
    usage = TaskUsage(
        workflow="literature",
        task_id=tid,
        model=None,
        input_tokens=context.estimated_tokens,
        output_tokens=estimate_tokens(json.dumps(out_dict))[0],
        cache_read_tokens=None,
        llm_calls=0,
        context_objects=len(context.objects),
        reused_artifacts=0,
        elapsed_ms=elapsed_ms,
        usage_source="estimated" if context.is_token_estimate else "provider",
    )

    if context.budget_status == "INCOMPLETE_EXCEEDED":
        status_val = "INCOMPLETE_CONTEXT"
    elif any(row.get("fulltext_inspection_status") != "FULLTEXT_LOADED" for row in evidence_matrix):
        status_val = "REQUIRES_VERIFICATION"
    else:
        status_val = "SUCCESS"

    return WorkflowOutput(
        workflow="literature",
        task_id=tid,
        project_id=context.project_id,
        status=status_val,
        result=out_dict,
        usage=usage,
        provenance_refs=[{"object_id": o["object_id"], "revision": o["revision"]} for o in context.objects],
        notes=["Evidence synthesized without fabricated sample contexts or assumed literature consensus."],
    )
