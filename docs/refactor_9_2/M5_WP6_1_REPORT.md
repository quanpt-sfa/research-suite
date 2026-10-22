# WP6.1 — Research-design-aware scientific synthesis: audit report

> **Amended by the remediation of 2026-10-22 (`M5_WP6_1_REMEDIATION.md`).** Where this report describes `NOT_APPLICABLE` methods/results, the `*_REVIEWED_*` recognition statuses, or an assessment without a separate basis check, the remediation supersedes it. Kept as the record of what was reported then.

Scope amendment to M5 approved by the Research Owner on 2026-10-22 (first written as 2026-10-23 in error: the instruction was received and `95b49ef` and `b631b64` were committed on 2026-10-22). **This report does not declare M5 or Gate M complete. WP7–WP8 are not started.** WP6.1 is `TECHNICALLY_ACCEPTED`
only in the sense of §3 (synthetic acceptance); every scientific question in §8 is open.

## 1. Baselines

| | SHA | CI run | Result |
|---|---|---|---|
| Baseline checked by the owner | `5010732` (WP5 remediation `b9e152c`, WP6 `ac65f45`) | 39722231273 | four jobs green |
| WP6.1 | `95b49ef` | 39730603834 | four jobs green: 3.12 847 passed / 6 skipped; 3.12+tiktoken 847 / 6; 3.11 847 / 6; 3.10 846 / 7 |
| Local, same code | `95b49ef` | — | 851 passed, 2 skipped (3.12 with pypdf and tiktoken); the skipped tests are the two Gate K/L real-artefact tests |

Tests added by WP6.1: 29 in `tests/test_inquiry_cross_design.py`; changes to existing expectations are listed in §5.
The documents of this report and the plan status are committed after `95b49ef`; their commit does not change code.

## 2. Capability matrix before and after

Statuses as in `M5_WP6_1_DESIGN.md` §2. "After" says what a test now exercises; it never says a capability is scientifically validated.

| # | Capability | Before | After |
|---|---|---|---|
| 1 | Shared core (StateStore, CAS, Source@revision, EvidencePassage, ECA, ResearchProblem, ResearchTask, library, checkpoints) | IMPLEMENTED_AND_TESTED | unchanged; the three pilots use it with no Dataset or Analysis object |
| 2 | `DomainWorkflowSpec` engine | IMPLEMENTED_AND_TESTED | + `research_purposes`, predicate `research_purpose_in`; old specs keep dict, digest and pin (tested) |
| 3 | Built-in specs | 2 (generic, archival fraud) | + `LIB_DW_CRITICAL_SYNTHESIS`, `LIB_DW_CONCEPTUAL_ARGUMENT`, `LIB_DW_QUALITATIVE_INQUIRY`: IMPLEMENTED_AND_TESTED as execution contracts (`EXTRACTED`: order and conditions, no method content) |
| 4 | Qualitative spec | REPRESENTABLE_BUT_NOT_OPERATIONAL (test fixture) | built-in, loadable by id, discipline-independent |
| 5 | `study_design` vocabulary | REPRESENTABLE_BUT_NOT_OPERATIONAL except archival / predictive | conceptual, narrative_review, systematic_review, qualitative_interview, case_study, design_based now select a contract; vocabulary unchanged |
| 6 | `research_purpose` | NOT_SUPPORTED | recorded on a ResearchProblem (optional), carried to a question promoted from it, read by the engine (`domain next --problem`, `run_workflow` gate) |
| 7 | Evidence form / claim kind per landscape entry | NOT_SUPPORTED | `claim_kind` empirical / interpretive / conceptual with its inference route |
| 8 | Discipline context | accounting, finance, education, cross_domain | + management, information_systems |
| 9 | Static discipline profiles | quantitative only | unchanged; DOMAIN_WORKFLOWS.md now says so explicitly |
| 10 | Theory / assumption extraction in the landscape | **gap**: dropped by the WP6 mapping | theory, stated assumption, tested assumption, theoretical claim, interpretive finding stored with Source@revision and passage |
| 11 | Landscape relations | 3 rule-derived | + 5 proposal-only kinds, semantic empirical disagreement, implied assumption; `definitions_differ` stays an observation |
| 12 | Implied / critic-proposed assumptions | NOT_SUPPORTED | proposals with provenance `suite_proposed:model:<id>`, `researcher_proposed`, `critic_proposed:<name>`; never entries |
| 13 | Inference assessment | empirical findings only | per route; empirical rule unchanged |
| 14 | Reproduction dimension | findings | findings; `NOT_APPLICABLE` (linking refused) for other claims |
| 15 | Recognition of a tension | NOT_SUPPORTED | `ADDRESSED_IN_REVIEWED_LITERATURE` / `NOT_FOUND_IN_REVIEWED_SET`; `tension_candidates` (words only) |
| 16 | Currency of conclusions | landscape as a whole | + per inference, with the reason (revised / excluded source; corpus change for absence statements) |
| 17 | Generic workflow on a conceptual project | accidental (methods `REQUIRES_VERIFICATION`, results `INSUFFICIENT_EVIDENCE`) | the contracts say so explicitly: `NOT_APPLICABLE` with the reason, never BLOCKED; the fraud spec on a narrative review is `NOT_APPLICABLE` |
| 18 | Contribution skill identification rule | tier-based | unchanged |

## 3. What changed

**Object schemas.** `ResearchProblem`: optional `inquiry`. `Landscape`: `inquiry`; inference records gain `relation`, `derivation`, `provenance`, `missing_information`, `levels`, `addressed_by`, `text`, `reasoning`, `proposed_by/at`,
`corpus_snapshot_at_proposal`, `carried_from`; entries gain `claim_kind`, `claim_type`, `provenance`; assessments gain `route`. No new object type, no enum that duplicates one; objects stored before WP6.1 are read with defaults.

**Protocols (data).** `RP_LANDSCAPE@2` (+ `theoretical_claim`, `interpretive_finding`, reads the theory dimension); `RP_THEORY@3` (reads sections whose heading matches no label: conceptual papers name sections after their ideas).
Caches under the old versions are not reused. `tests/test_reading_protocols.py` changed accordingly.

**Workflow contracts.** `research_purposes` and `research_purpose_in` in `domain_engine.py`; facts from a ResearchProblem; the three specs; `domain next --problem|--research-purpose`.

**Export.** `research_landscape.md`: Inquiry; theories, assumptions, theoretical claims, interpretive findings; inferences derived by rule; proposed relations; assumptions not stated by the authors; considerations by kind of claim; route on assessments.
New `relations.csv`. `evidence_table.csv` and `works_and_versions.csv` are unchanged.

**CLI.** `problem new|revise --purpose --design --evidence-form --domain --subfield`; `landscape propose|add-proposal|candidates`; `landscape review --acknowledge-missing`; goal `build_landscape` with optional `propose_relations`.

**Explicit changes to earlier tests/behaviour** (none silent): error code `ENTRY_NOT_A_FINDING` → `ENTRY_NOT_A_CLAIM`; `landscape show` prefixes a claim line with its kind; the WP6 export file set gains `relations.csv`; built-in spec list in two domain-engine tests;
type order in `test_profile_knowledge.py` was updated in WP6.

## 4. Tests that show one core serving three kinds of inquiry (`tests/test_inquiry_cross_design.py`, three independent corpora in `tests/inquiry_fixtures.py`)

| Requirement | Test |
|---|---|
| 1 same core, three inquiries | `test_the_same_core_builds_a_landscape_for_all_three_inquiries` (no Dataset or Analysis object in any store; integrity audit PASS) |
| 2 conceptual critical review not BLOCKED | `test_a_conceptual_critical_review_is_not_blocked_by_the_absence_of_data` (methods and results `NOT_APPLICABLE` with reasons; steps and loop-back) |
| 3 empirical gates kept | `test_the_regression_workflow_is_not_applied_to_a_critical_review_and_its_gates_hold_for_archival_work`, `test_archival_prediction_keeps_empirical_disagreement_and_the_empirical_inference_gate` |
| 4 theory and assumptions quoted from full text | `test_theory_and_assumptions_are_stored_and_quoted_from_the_full_text`, `test_a_conceptual_paper_with_its_own_section_headings_is_read` |
| 5 no conceptual divergence from wording | `test_definitions_that_differ_in_wording_are_flagged_but_never_become_conceptual_divergence` |
| 6 levels of analysis | `test_two_theories_at_different_levels_are_not_called_incompatible` |
| 7 tension already addressed | `test_a_tension_the_literature_already_addresses_is_never_called_unrecognised` |
| 8 model interpretation not established | `test_a_model_interpretation_is_never_an_established_theoretical_fact`, `test_researcher_and_critic_proposals_are_marked_as_theirs_and_follow_the_same_gate` |
| 9 versions not merged | `test_a_relation_between_two_versions_of_one_work_is_not_a_relation_between_studies` (+ WP6's version tests) |
| 10 corpus change flags conclusions | `test_a_changed_corpus_marks_the_conclusions_that_rested_on_it_for_reassessment` |
| 11 WP6 on its fixtures unchanged | `test_wp6_results_on_the_fraud_style_fixtures_are_unchanged` (entry ids, categories, inferences pinned) and the 26 unchanged-in-intent WP6 tests |
| 12 export shows provenance and confirmation | `test_every_inference_in_the_export_shows_where_it_came_from_and_whether_you_confirmed_it` |
| education | `test_education_has_two_different_contracts_and_neither_borrows_regression_logic`, `test_education_landscape_does_not_turn_artifact_completion_into_evidence_of_learning` |
| routes, no score | `test_each_claim_kind_is_assessed_on_its_own_terms_and_nothing_follows_from_reading_or_reproduction`, `test_consideration_prompts_are_not_a_mandatory_checklist_and_there_is_no_composite_score` |

## 5. One worked example of each inference supported (output in `docs/refactor_9_2/demos/wp6_1_inquiry_demo_output.txt`, invented papers, scripted models)

* **Empirical** (Pilot C): finding E007 "average precision falls from 0.31 to 0.12 under out-of-time splits"; an assessment that cites only the paper's own description with a threat still open is stored as `LIMITED` (no methodological literature; a threat open); three rule-derived disagreements on one relationship (opposite direction, two null-versus-effect).
* **Interpretive** (Pilot B): theme E006 "students checked agent output by whether it ran"; assessed on textual evidence, a considered alternative reading and reflexivity → `SUPPORTED_WITHIN_SCOPE` on the interpretive route, reproduction `NOT_APPLICABLE`.
* **Conceptual** (Pilot A): proposition E015 "more participation in a shared channel dilutes collective attention per message"; a methods text as basis → `LIMITED`; logical analysis with a rival considered → `SUPPORTED_WITHIN_SCOPE`; reading still `AUTHOR_REPORTED`.
* **Critical synthesis** (Pilot A): I003 `conceptual_divergence` ("what participation presupposes") between a stated assumption of attention theory and one of engagement theory, with the meaning difference, missing information, and the paper that already separates the two ideas (`ADDRESSED_IN_REVIEWED_LITERATURE: SRC_PART@1`); confirmed by the researcher only after acknowledging the missing information.
* **Implied assumption** (Pilot A): I004 "recipients can tell how much effort a message took", `suite_proposed:model:...`, `UNCONFIRMED`, listed apart "not stated by the authors".

## 6. Where the system refuses to go beyond the evidence (all in the demo and tests)

`LEVELS_DIFFER` (theoretical incompatibility across levels of analysis) · `LEVELS_REQUIRED` · `MEANING_DIFFERENCE_REQUIRED` (wording is not meaning) · `SAME_WORK` (versions are not studies) · `PROPOSAL_KIND_NOT_ALLOWED`
(a rule-only kind cannot be proposed) · `ADDRESSED_BY_UNKNOWN` · `MISSING_INFORMATION_NOT_ACKNOWLEDGED` (no confirmation without reading what is missing) · `REPRODUCTION_NOT_APPLICABLE` (a conceptual claim is not reproduced from data) ·
verdict capped at `LIMITED` (wrong kind of basis for the route, a threat or rival still open) · a model proposal never becomes an entry and never confirms itself · "unrecognised" and "novel" appear nowhere in the export (tested) ·
`NOT_FOUND_IN_REVIEWED_SET` says it is a statement about the reviewed set and is flagged when the corpus changes · no composite score.

## 7. Why no new object type or `research_genre` enum

`study_design` describes a project; the standard a claim is held to depends on the claim, and a landscape reads papers of every design for any project. The engine had no fact for purpose. The smallest change: an optional `inquiry` on the
problem (four separate facts), a purpose filter and predicate on the existing spec, three specs, and a route chosen by the claim kind. The contracts control which steps exist, their order, entry conditions and loop-backs; the routes control which kind of basis
can support a claim. Both are independent of discipline. Pins: unchanged for existing specs (tested); `RP_*` version bumps invalidate caches only.

## 8. What cannot be accepted yet

* **REAL_CORPUS_VALIDATION_PENDING**: nothing was run on real full texts or with a real model; every paper is invented and every model scripted. Whether the extraction fields read real conceptual or qualitative papers, whether a real model proposes sensible relations,
  and whether the levels-of-analysis guard agrees with researchers' judgement are unknown.
* The three execution contracts are `EXTRACTED` skeletons; whether they match how researchers in those traditions work needs a researcher in each.
* The critical-synthesis considerations are prompts without a rubric: no scientific score is justified until a rubric exists and is validated against expert judgement.
* The education profile remains a quantitative checklist; no qualitative or conceptual education profile exists.
* WP14 (the researcher's 20 expert cases) is still open and is written for empirical claims; conceptual pre-emption cases need the researcher's own cases.
* Backlog B1/B2 (WP3: partial provider usage, fencing at the result-write boundary) unchanged. A noticed engine limitation: redoing an upstream `decide` step does not reopen downstream steps without a diagnostic (B5).

## 9. Changes WP7 needs (not started; see `M5_BACKLOG.md` B5)

Candidates declare the kind of contribution and the claim kinds they rest on; rest on confirmed relations or show their state; conceptual pre-emption rules from the researcher's cases; decide whether landscape entries a candidate rests on become HIGH ECA statements;
`discover_contribution` picks its execution contract from the problem's inquiry (and says so when none is recorded); `needs_reassessment` propagates into candidates.
