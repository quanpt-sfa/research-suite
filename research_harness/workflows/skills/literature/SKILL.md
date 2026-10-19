---
name: literature
title: Literature Review Workflow
description: Retrieve, assess, and synthesize relevant scientific literature into a structured evidence matrix with verified provenance.
version: 9.1.0
allowed_tools: ["search_objects", "get_object", "edges_from", "edges_to", "fetch_artifact"]
inputs:
  - research_question
  - source_collection
  - screening_criteria
  - relevant_claims
  - target_concepts_and_theories
outputs:
  - evidence_matrix
  - source_provenance_and_locators
  - methods_and_contexts_reviewed
  - supported_findings
  - unresolved_disagreements
  - theoretical_synthesis
  - evidence_limitations
  - questions_requiring_fulltext_verification
---

# Literature Review Workflow (Workflow 2)

## Purpose & Scientific Objective
Systematically map, evaluate, and synthesize published scientific evidence relevant to a research question. Construct a structured evidence matrix linking specific propositions to documented passages, preserving provenance, locators, and methodological context.

## Mandatory Invariants
1. **Zero Citation or Evidence Fabrication:** Never invent papers, authors, DOIs, page locators, or excerpt text.
2. **Abstract vs Full-Text Distinction:** Never claim to have read or evaluated an entire study when only an abstract, summary, or excerpt was examined.
3. **Selective Full-Text on Demand:** Full text is fetched from storage only when required to resolve specific methodological details or contradictory findings (`fulltext_policy="on_demand"`).
4. **Preserve Exact Provenance:** Every evidence row must record the source ID, exact locator (page/paragraph/table), and normalized finding.

## Step-by-Step Task Instructions
1. **Screen Sources against Criteria:** Filter candidate sources using defined inclusion/exclusion criteria.
2. **Build Evidence Matrix:** For each included source:
   - Extract primary claims and findings.
   - Record exact locator (`p. X`, `Table Y`).
   - Classify empirical method, sample context, and strength of evidence.
3. **Synthesize Findings and Disagreements:**
   - Group findings by theme or construct.
   - Explicitly highlight unresolved disagreements or contradictory findings.
4. **Theoretical Integration:** Synthesize how reviewed evidence informs underlying theory.
5. **Flag Full-Text Verification Gaps:** Identify any ambiguous findings requiring full-text inspection.
