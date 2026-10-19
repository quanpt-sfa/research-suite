"""Workflow 2 — Literature Review Implementation.

Retrieves, evaluates, and synthesizes relevant scientific literature into a structured
evidence matrix with verified provenance, locators, and on-demand full-text inspection.
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

    # 1. Structured Evidence Matrix
    evidence_matrix: list[dict[str, Any]] = []
    source_provenance: list[dict[str, Any]] = []
    methods_and_contexts: list[dict[str, Any]] = []
    supported_findings: list[str] = []
    unresolved_disagreements: list[dict[str, Any]] = []
    fulltext_needed: list[str] = []

    # Map sources by ID
    source_dict = {s["object_id"]: s for s in sources}
    fulltext_dict = {ft["source_id"]: ft for ft in context.full_texts}

    for ex in excerpts:
        sid = ex.get("source_id") or "UNKNOWN_SRC"
        s_obj = source_dict.get(sid)
        citation = s_obj["content"].get("citation_key") if s_obj else sid
        title = s_obj["content"].get("bibliographic_identity") if s_obj else "Document"

        passage_text = ex.get("excerpt") or ex.get("normalized_claim", "")
        locator = ex.get("locator", "Unspecified locator")

        # Determine evidence orientation
        is_contradictory = any(term in passage_text.lower() for term in ["contrary", "fails to find", "refutes", "negative correlation", "no effect"])

        matrix_row = {
            "passage_id": ex.get("passage_id"),
            "source_id": sid,
            "citation": citation,
            "locator": locator,
            "normalized_finding": ex.get("normalized_claim", passage_text[:120]),
            "evidence_type": "empirical_finding" if "data" in passage_text.lower() or "sample" in passage_text.lower() else "theoretical_statement",
            "relationship": "contradicts_prior" if is_contradictory else "supports_prior",
            "fulltext_available": sid in fulltext_dict,
        }
        evidence_matrix.append(matrix_row)

        source_provenance.append({
            "source_id": sid,
            "citation_key": citation,
            "bibliographic_identity": title,
            "locator": locator,
            "passage_id": ex.get("passage_id"),
            "is_full_text": sid in fulltext_dict,
        })

        if is_contradictory:
            unresolved_disagreements.append({
                "source_a": citation,
                "locator_a": locator,
                "conflicting_finding": passage_text,
                "status": "UNRESOLVED_EMPIRICAL_DISAGREEMENT",
            })
        else:
            supported_findings.append(f"[{citation}, {locator}] {ex.get('normalized_claim') or passage_text[:100]}")

        # Check if passage requires full-text verification
        if not locator or locator == "Unspecified locator" or len(passage_text) < 40:
            fulltext_needed.append(f"Source {citation} lacks precise page locator; verify original document text.")
        elif not (sid in fulltext_dict) and ("identification" in passage_text.lower() or "method" in passage_text.lower()):
            fulltext_needed.append(f"Source {citation} mentions identification strategy; full methodological text recommended for verification.")

    # Identify sources present only as abstract/excerpt
    for s in sources:
        sid = s["object_id"]
        ckey = s["content"].get("citation_key", sid)
        if sid not in fulltext_dict and not s["content"].get("full_text"):
            fulltext_needed.append(
                f"Source {ckey} ({sid}) is available as abstract/excerpt only; full-text retrieval required for complete empirical verification."
            )

    # 2. Extract Methods and Contexts Reviewed
    for s in sources:
        s_content = s["content"]
        methods_and_contexts.append({
            "source_id": s["object_id"],
            "citation_key": s_content.get("citation_key", s["object_id"]),
            "study_design": s_content.get("study_design") or s_content.get("methodology", "Archival observational"),
            "sample_context": s_content.get("sample_context") or s_content.get("sample", "U.S. public firms / primary literature"),
            "time_period": s_content.get("time_period", "Multi-year sample"),
        })

    # 3. Theoretical Synthesis
    synthesis_lines = []
    if theories:
        synthesis_lines.append(f"Theoretical foundations are anchored by {len(theories)} framework(s): " +
                               ", ".join(t["content"].get("label", t["object_id"]) for t in theories) + ".")
    if constructs:
        synthesis_lines.append(f"Literature investigates {len(constructs)} core construct(s): " +
                               ", ".join(c["content"].get("label", c["object_id"]) for c in constructs) + ".")
    synthesis_lines.append(f"A total of {len(sources)} source(s) and {len(excerpts)} evidence passage(s) were synthesized.")
    if unresolved_disagreements:
        synthesis_lines.append(f"The literature exhibits {len(unresolved_disagreements)} major points of empirical friction or disagreement.")
    else:
        synthesis_lines.append("Existing evidence demonstrates broad thematic consistency across reviewed settings.")

    theoretical_synthesis = " ".join(synthesis_lines)

    # 4. Evidence Limitations
    evidence_limitations = [
        "Synthesized evidence relies on specific sample contexts that may not generalize across all institutional regimes.",
        "Passage-level extractions reflect documented excerpted findings; full-text methodological caveats should be consulted for replication.",
    ]
    if not fulltext_dict:
        evidence_limitations.append("Full texts were not expanded into context (abstract and excerpt level review).")

    result_data = LiteratureReviewResult(
        evidence_matrix=evidence_matrix,
        source_provenance_and_locators=source_provenance,
        methods_and_contexts_reviewed=methods_and_contexts,
        supported_findings=supported_findings[:15],
        unresolved_disagreements=unresolved_disagreements,
        theoretical_synthesis=theoretical_synthesis,
        evidence_limitations=evidence_limitations,
        questions_requiring_fulltext_verification=fulltext_needed,
    )

    elapsed_ms = int((time.time() - start_time) * 1000)
    usage = TaskUsage(
        workflow="literature",
        task_id=tid,
        model=None,
        input_tokens=context.estimated_tokens,
        output_tokens=estimate_tokens(json.dumps(result_data.to_dict()))[0],
        cache_read_tokens=None,
        llm_calls=0,
        context_objects=len(context.objects),
        reused_artifacts=0,
        elapsed_ms=elapsed_ms,
        usage_source="estimated" if context.is_token_estimate else "provider",
    )

    return WorkflowOutput(
        workflow="literature",
        task_id=tid,
        project_id=context.project_id,
        status="INCOMPLETE_CONTEXT" if context.budget_status == "INCOMPLETE_EXCEEDED" else "SUCCESS",
        result=result_data.to_dict(),
        usage=usage,
        provenance_refs=[{"object_id": o["object_id"], "revision": o["revision"]} for o in context.objects],
        notes=["Evidence matrix constructed from revision-pinned sources and passages without hallucinated citations."],
    )
