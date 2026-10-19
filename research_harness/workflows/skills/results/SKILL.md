---
name: results
title: Results Interpretation & Reproducibility Workflow
description: Organize reproducible empirical analyses, interpret computational outputs, and detect changed results affecting scientific claims.
version: 9.1.0
allowed_tools: ["search_objects", "get_object", "edges_from", "edges_to", "fetch_artifact"]
inputs:
  - dataset_references
  - analysis_specifications
  - script_or_run_metadata
  - tables_figures_and_diagnostics
  - previous_result_revisions
outputs:
  - structured_run_records
  - input_and_parameter_references
  - output_artifact_references
  - reproducibility_metadata
  - result_comparisons
  - robustness_summaries
  - findings_supported_by_available_outputs
  - changed_results_and_affected_claims
---

# Results Workflow (Workflow 4)

## Purpose & Scientific Objective
Organize reproducible empirical runs, document parameterizations, evaluate robustness, interpret computational outputs, and detect changed results that impact upstream or downstream claims.

## Mandatory Invariants
1. **Never Invent Numbers:** The workflow records and interprets computed results; it must never hallucinate statistical metrics, p-values, coefficients, or table entries.
2. **Context Offloading:** Do not dump large datasets or raw megabyte logs directly into the prompt context. Keep data artifacts in CAS storage and pass compact references, summaries, and excerpted tables.
3. **Execution Responsibility:** Python and R code remain responsible for numerical execution; the workflow manages interpretation, reproducibility metadata, and revision tracking.
4. **DAG Dependency & Invalidation Tracking:** Whenever a Result is revised, trace all dependent Claims using graph relations (`depends_on`, `supports`) and flag affected claims for re-assessment.

## Step-by-Step Task Instructions
1. **Record Run Metadata:** Document run scripts, seeds, library versions, dataset hashes, and execution environment.
2. **Extract Key Metrics & Diagnostics:** Record coefficients, standard errors, sample sizes, fit statistics, and robustness outputs.
3. **Compare Against Prior Revisions:** If prior revisions of the Result exist, compare parameters, magnitude, and statistical sign to identify discrepancies.
4. **Synthesize Findings Supported by Outputs:** State exactly what the data tables and diagnostic figures support, without over-generalizing.
5. **Trace Affected Claims:** Identify claims that depend on this Result and flag them if the result has changed.
