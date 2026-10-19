---
name: methods
title: Methods Design & Evaluation Workflow
description: Design, formalize, and evaluate research methods, identification strategies, and operationalizations appropriate to the research question.
version: 9.1.0
allowed_tools: ["search_objects", "get_object", "edges_from", "edges_to"]
inputs:
  - research_question
  - constructs_and_proxies
  - dataset_descriptions
  - identification_assumptions
  - candidate_methods
  - methodological_literature
outputs:
  - candidate_research_designs
  - assumptions
  - identification_or_measurement_requirements
  - specification_plans
  - alternative_methods
  - diagnostic_tests
  - limitations_and_unresolved_choices
---

# Methods Workflow (Workflow 3)

## Purpose & Scientific Objective
Design and rigorously evaluate research methods, empirical specifications, construct operationalizations, and identification strategies tailored to the target research question.

## Mandatory Invariants
1. **Methodological Pluralism:** Do not force fixed-effects regression, causal inference, or any single methodology onto all research questions. Match the method to the epistemic claim tier (conceptual, descriptive, predictive, associational, mechanism-consistent, or causal).
2. **Explicate Identifying Assumptions:** Clearly state assumptions required for the estimator or method to be valid (e.g., parallel trends, exogeneity, measurement invariance, sample representativeness).
3. **Diagnostic & Falsification Planning:** Propose concrete falsification, placebo, or diagnostic tests.
4. **Separate Generic Mechanics from Discipline Profiles:** Generic methodological logic remains discipline-neutral, while discipline profiles provide domain-specific checklists and traditions.

## Step-by-Step Task Instructions
1. **Map Constructs to Empirical Proxies:** Operationalize conceptual constructs into measurable variables or operational proxies.
2. **Formulate Candidate Research Designs:** Detail the structural or observational design (e.g., randomized experiment, staggered DID, IV, cross-sectional survey, content analysis).
3. **Specify Models & Estimation Plans:** Define dependent variables, primary independent variables, controls, fixed effects, clustering, and estimation algorithms.
4. **Detail Identification & Measurement Requirements:** Document required identification conditions and tests of assumptions.
5. **Develop Diagnostic & Placebo Tests:** Plan sensitivity analyses, placebo checks, and balance diagnostics.
6. **Integrate Discipline Profile Checklists:** If a discipline profile (e.g., accounting, finance, education) is active, incorporate its specialized validity criteria.
