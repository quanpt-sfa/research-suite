# WP6.1 remediation and scientific pilot preparation: audit report

Date: 2026-10-22. Author: the development assistant (Claude Sonnet 5.5). For: the Research Owner.
Status labels used below: `IMPLEMENTED`, `TECHNICALLY_TESTED`, `RESEARCHER_REVIEW_REQUIRED`, `REAL_CORPUS_VALIDATION_PENDING`. **No part of this work is `SCIENTIFICALLY_VALIDATED`**: that label needs
genuine independent reference judgements, and none exist. WP7 and WP8 were not started; WP14 was not approved.

## 1. Commits and CI

| Item | Value |
|---|---|
| Initial HEAD (Phase A, `git fetch` then compare) | `b631b64`, equal to `origin/main`, CI run 39730974356 green on four jobs |
| Remediation commit | `24517d8` (pushed to `refactor/9.2` and `main`) |
| CI run for `24517d8` | 39776983080, green on all four jobs (3.10, 3.11, 3.12, 3.12 + tiktoken) |
| Documentation commit (this report, backlog B6, plan status) | the commit that adds this file; code is unchanged since `24517d8` |
| Local tests, Python 3.12 + tiktoken | 912 passed, 2 skipped (full run); one test was added afterwards (pin of spec digests, §5); its file alone: 12 passed. CI ran the final tree |
| Local tests, Python 3.10 (no tiktoken) | 908 passed, 6 skipped (tiktoken tests skip); same note |

Python 3.11 was not run locally; it is covered by the CI matrix (3.10 / 3.11 / 3.12 / 3.12 + tiktoken).

## 2. The four problems: reproduced, root cause, fix

All four were reproducible. `tests/test_remediation_phase_a.py` holds one failing-first test per problem. I ran it against the sources of `b631b64` (a clean checkout with only the test file and its
fixtures copied in): **4 failed**. On `24517d8`: **4 passed**.

| # | Failure on `b631b64` | Root cause | Fix |
|---|---|---|---|
| P0-1 | `methods` stage of the critical-synthesis contract is `NOT_APPLICABLE` | The spec treated "no estimation" as "no methods or results", and a stage was both a manuscript section and a capability group; the three contracts were derived from the regression workflow by deleting what did not fit | Stages are capability groups with `activity` and `manuscript` labels. Each contract gained the steps its tradition needs (§3). `NOT_APPLICABLE` is used only where the function is not part of the design |
| P0-2 | A proposal whose model returned `addressed_by: []` got `NOT_FOUND_IN_REVIEWED_SET` | The status was derived from the model's list, so silence became a finding of absence | Recognition is derived only from a researcher's inspection record (`discovery/inspection.py`). Statuses: `RECOGNITION_NOT_ASSESSED`, `POTENTIAL_ADDRESSING_SOURCES`, `ADDRESSED_IN_INSPECTED_SET`, `NOT_FOUND_IN_INSPECTED_SET`, `INSPECTION_INCOMPLETE`. The record holds the tension digest and revision, corpus snapshot, queries, Source@revision and text inspected, relevant-but-unread, per-source results, limits, and the researcher's decision, rationale and time. Any change to corpus, source or tension marks it stale. `tension_candidates()` only proposes reading |
| P1-3 | A question promoted from a problem that declared `critical_synthesis` + `theory_development` (systematic review) read `evaluation` / `design_based` once the problem was revised | The inquiry was read from the head of the problem, not the revision the question came from; with nothing recorded the workflow defaulted to the quantitative one | `derived_from` edge pins the problem revision (`promoted_from`); `update_question_inquiry` is an explicit, rationale-bearing act that flags dependents; an undeclared purpose selects no workflow (`domain suggest` lists contracts that fit and chooses none) |
| P1-4 | The assessment dimension had no `assessed_by` / verification | The researcher's verdict and the evidence behind it were one field | Assessment carries `RESEARCHER_ASSESSED` and `INFERENCE_JUDGMENT` separately from a basis status computed by `discovery/basis.py` (§4). No composite score |

Smaller items: an implied assumption may carry premises, reasoning, evidence, missing information and alternatives (`missing_information` is no longer forced to `[]`); workflow selection considers
purpose and design together; the `2026-10-23` dates in six documents were corrected to `2026-10-22` with an explicit correction note (history kept).

## 3. Workflow contract changes (`workflows/domain_specs/*.json`, version 0.2.0)

Engine additions (`workflows/domain_engine.py`): `StageSpec.activity / manuscript / assessed_by`; `StepSpec.completed_by` with kinds `EXECUTED_BY_CAPABILITY`, `DECLARED_IN_RECORD`; the
`review_component` predicate; derived completion in `effective_facts`. A step the researcher marks with `record_step_run` is labelled `RECORDED_BY_RESEARCHER`; a stage that rests only on such steps reports
`REQUIRES_VERIFICATION`, never `SUCCESS`. A new executor (`workflows/skills/contract_stage.py`) reports a contract stage from its steps.

| Contract | Methods stage | Findings stage | Not default |
|---|---|---|---|
| Critical synthesis (`systematic_review`, `narrative_review`, `conceptual`) | search strategy, screening and selection, appraisal (when the protocol performs one), extraction protocol, synthesis protocol: each completed by a declared, *adopted* review protocol | conceptual reconstruction, identified tensions, inspected recognition, interpretive synthesis | statistical significance, regression robustness, identification |
| Conceptual argument | reconstruct the inference chain, rival explanations | propositions and framework, boundary conditions | an empirical Results section |
| Qualitative inquiry (`qualitative_interview`, `case_study`, `design_based`) | sampling rationale, data generation, coding and interpretation, alternative interpretations, credibility checks (reflexivity) | findings (themes, patterns), traceability of findings, design principles (only for design-based) | coefficients, identification |

The fraud-detection (archival) contract keeps every gate and gained no derived completion. Review protocols (`knowledge/review_protocol.py`, `docs/REVIEW_PROTOCOL.md`) are stored on the
`ResearchProblem`; a protocol is a `DRAFT` until the researcher adopts the exact plan (plan digest); a screening log and PRISMA-style counts are audited against the corpus.

## 4. Recognition and basis

* `BASIS_CHECKED` means a deterministic check passed (a quote-verified passage at the Source's current revision containing the excerpt, with the limit of what it supports stated; or an argument whose steps
  resolve to premises, with a conclusion and rivals). **It does not mean the basis supports the criterion or the argument is valid.** Assessments recorded before this change stay `BASIS_NOT_TRACEABLE`
  (tested).
* A recognition status is a statement about the inspected set in its corpus snapshot. The export says it makes no claim that a tension is new, and a test forbids "novel" outside that disclaimer.

## 5. Compatibility and migration

* **No migration is needed.** Specs that use none of the new fields keep their digest, so every stored pin still resolves. Verified with the engine of `b631b64`: `LIB_DW_GENERIC` `02fc4faa…` and
  `LIB_DW_ACC_FRAUD_DETECTION` `13aa49ba…` are identical before and after; a test pins both.
* The three rewritten contracts are version 0.2.0 with new digests. A run pinned to the 0.1.0 digests keeps resolving to them only if those spec versions are still held; they are not kept by this change.
  I judge this acceptable because the 0.1.0 contracts were the thing found to be wrong, but it is a decision for you if an existing project pinned them (no store under `sample/` references them, checked with grep; tests build their own).
* Old `Landscape` objects read with defaults; legacy assessments show `BASIS_NOT_TRACEABLE`; a model-listed `addressed_by` on an old inference is shown as an unverified hint (`POTENTIAL_ADDRESSING_SOURCES`, `verified: false`) and an empty one as `RECOGNITION_NOT_ASSESSED`; neither is read as a finding (`discovery/inspection.py`).
* Eight tests of `tests/test_inquiry_cross_design.py` and some of `tests/test_landscape.py` were changed *because the semantics changed* (for example `ENTRY_NOT_A_FINDING` → `ENTRY_NOT_A_CLAIM`;
  recognition statuses). Each change is a decision, not a silenced failure; they are visible in the diff.

## 6. Three independent designs (Phase C, synthetic)

`tests/test_remediation_cross_design.py` (2 tests, parametrised over three fixtures) and `tests/test_remediation_contracts.py`. All use scripted readers: **technical acceptance only**.

| | Critical literature review | Education: conceptual / qualitative | Accounting: archival fraud detection |
|---|---|---|---|
| Suggested contract | critical synthesis | conceptual argument (qualitative contract tested separately) | fraud detection |
| Claim kinds in the landscape | conceptual, empirical | conceptual, interpretive | empirical |
| Basis accepted for its claims | logical analysis; methodological literature for empirical ones | logical analysis; textual evidence | methodological literature |
| Another design's basis | verdict `LIMITED` | verdict `LIMITED` | verdict `LIMITED` |
| Dataset / Analysis objects needed | none | none | none (the paper's reproduction is separate) |
| Methods / findings steps | review protocol / tensions, synthesis | argument chain / propositions; sampling, coding, credibility / themes | all empirical gates kept |
| Other contracts | each reports at least one stage as not covering this inquiry | same | same |

The three demos (`scripts/demos/wp6_1_inquiry_demo.py`, `wp6_landscape_demo.py`; outputs in `docs/refactor_9_2/demos/`) were regenerated and read by me for stale wording.

## 7. Scientific pilot (Phase D): `pilots/ai_communication_critical_review/`

**Result: set up, not run. Status `REAL_CORPUS_VALIDATION_PENDING`.** The blockers are real, not procedural:

* **No lawful full text is held.** Nothing was downloaded. The corpus is 26 metadata-only sources; the reading protocols have nothing to read, so conceptual map, theoretical assumptions and tension
  inspection are `NOT_AVAILABLE` files that say so (`conceptual_map.md`, `theoretical_assumptions.md`, `tension_inspection_report.md`). I did not substitute synthetic results.
* **No model run**: WP3 backlog B1 (partial provider usage) is open and no paid run was approved. Copyrighted full text must not go to a third-party model without permission.
* **Protocol not adopted**: the review protocol is an assistant-drafted `DRAFT`; screening is empty and appraisal is `NOT_DECLARED`; `can_build_landscape: false`.
* **Candidate seeds are the assistant's proposal** (27, from its own knowledge), *not* a search result and *not* a screening decision. `scripts/pilot/verify_seeds.py` matched 26 to Crossref by title,
  first author and year together; DOIs and metadata are what Crossref returned (no DOI from memory). `simon1971` (a book chapter) did not match and is left out; 2 matched records list a correction or erratum
  (`weng2012`, `hohenstein2023`). `seeds_verified.json` records the query URLs and nearest candidates.

Deliverables and their origin (also in `manifest.json`): `search_protocol.md`, `corpus_inventory.csv` — production exporter (`research export review`); `problem_framing.md`, `citation_evidence_audit.md`,
`seed_verification.csv`, the three `NOT_AVAILABLE` files — generated by `scripts/pilot/build_ai_communication_pilot.py` from the store and the verified seeds; `limitations.md` — written by the assistant. No
author is quoted, no full-text content is described, no human adjudication is invented. The scientific store (`_store/`) is git-ignored. `tests/test_pilot_setup.py` (4 tests) checks that the setup rebuilds
offline and that no DOI appears that Crossref did not return.

To run it for real, the Research Owner must: rotate the exposed API key; decide the protocol (databases, criteria, appraisal, which seeds to screen in); supply lawfully accessible full texts; approve a bounded
model run with the cost known.

## 8. Not changed, on purpose

Research Core, `GATEL_REAL`, Gate K/L material and `sample/` stores; ECA and human checkpoints; no new store, no new `research_genre` abstraction, no new object type. WP3 backlog B1 and B2 remain open
(`M5_BACKLOG.md`); new items are in B6.

## 9. Decisions for the Research Owner (methodological)

1. Do the three contracts (§3) describe how researchers in those traditions work? Especially: is `inspect_recognition` correctly a step of the findings, should appraisal ever be mandatory for a critical review, and is "declared in an adopted record" an acceptable completion for method steps?
2. Is `BASIS_CHECKED` (structural, §4) a useful label, or should it be renamed so it cannot be read as validation?
3. Pilot: which databases and criteria, which seeds to screen in, whether a bounded model run on lawful full text is approved, and who the independent judge of the tensions will be (without that the pilot cannot become `SCIENTIFICALLY_VALIDATED`).
4. Whether the 0.1.0 contract versions need to remain loadable for projects outside this repository.
5. WP14: the 20 expert cases are yours to write; not requested or invented here.

## 10. Readiness for WP7–WP8 (not started)

**Technically ready for design review, not for approval.** What WP7 can rely on: claim kinds with their own standards, relations that are proposals, recognition that is an inspection record with explicit
limits, per-inference `needs_reassessment`. What blocks it: WP14 (expert cases and precedence rules written for empirical claims; pre-emption for conceptual candidates is undefined, `M5_BACKLOG.md` B5), the
real-corpus validation above, and the open B1/B2. Candidates must not become "novel" on a `NOT_FOUND_IN_INSPECTED_SET` (B5 item 2). I stop here and await a new decision.

## 11. Limits of this report

Everything tested here ran on invented papers and scripted models, plus metadata from Crossref. The tests show the rules behave as designed; they do not show the rules are scientifically right.
