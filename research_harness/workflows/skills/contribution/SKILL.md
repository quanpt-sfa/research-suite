---
name: contribution
title: Contribution Finding Workflow
description: Identify, challenge, and refine potential scientific contributions against prior literature and empirical findings.
version: 9.1.0
allowed_tools: ["search_objects", "get_object", "edges_from", "edges_to"]
inputs:
  - research_question
  - literature_claims
  - theory_mechanisms
  - contribution_hypotheses
  - available_findings
outputs:
  - candidate_contributions
  - closest_competing_contributions
  - exact_point_of_differentiation
  - supporting_evidence
  - contradicting_evidence
  - necessary_assumptions
  - missing_verification
  - alternative_contribution_formulations
  - confidence_explanation
---

# Contribution Finding Workflow (Workflow 1)

## Purpose & Scientific Objective
Identify, challenge, and refine potential scientific contributions. Formulate what this research adds to scholarly knowledge, delineate exact points of differentiation against competing literature, and rigorously document supporting versus contradicting evidence.

## Mandatory Invariants
1. **Never equate an unexplored topic with a contribution:** An unstudied question is only an opportunity, not proof of scholarly value or scientific significance.
2. **Never declare automatic novelty:** Novelty is a relational judgment relative to prior published claims, mechanisms, and findings.
3. **Evidence-grounded differentiation:** Every candidate contribution must explicitly identify the closest competing literature claims and specify the exact boundary of differentiation.
4. **Re-executable over time:** Contribution Finding must be re-callable whenever new empirical Results or Discussion findings become available.

## Step-by-Step Task Instructions
1. **Analyze Research Question & Scope:** Parse the target research question and extract core phenomena, constructs, and theoretical anchors.
2. **Survey Existing Hypotheses & Literature Baseline:** Review active literature claims and competing viewpoints already cataloged in the project state.
3. **Formulate Candidate Contributions:** For each candidate contribution:
   - State the focal claim or mechanism.
   - Contrast against the closest competing literature.
   - Identify the exact point of differentiation (e.g., conceptual extension, methodological identification, new mechanism, empirical setting).
4. **Catalog Supporting and Contradicting Evidence:**
   - Attach exact revision-pinned object references (`object_id`, `revision`).
   - Highlight any conflicting literature claims or contradictory empirical findings.
5. **Identify Assumptions & Missing Verifications:**
   - What theoretical or identifying assumptions must hold for the contribution to stand?
   - What empirical tests or robustness checks remain unperformed?
6. **Synthesize Confidence & Alternative Formulations:**
   - Provide an evidence-grounded confidence evaluation.
   - Propose alternative formulations if the primary formulation is challenged.
