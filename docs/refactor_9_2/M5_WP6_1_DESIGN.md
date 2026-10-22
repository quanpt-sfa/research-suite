# WP6.1 — Research-design-aware scientific synthesis: audit and change design

> **Amended by the remediation of 2026-10-22 (`M5_WP6_1_REMEDIATION.md`).** Superseded here: methods and results of the critical-synthesis and qualitative contracts are no longer `NOT_APPLICABLE` (the contracts define their method and findings); the recognition statuses `ADDRESSED_IN_REVIEWED_LITERATURE` and `NOT_FOUND_IN_REVIEWED_SET` are replaced by the inspection-based statuses; a question promoted from a problem reads the problem at the pinned revision; an assessment's verdict and the check of its basis are shown apart. The text below is kept as the record of what was decided then.

Scope amendment to M5 (owner decision 2026-10-22, after WP6 `ac65f45`; the date was first written as 2026-10-23 in error, the commits are timestamped 2026-10-22). WP6.1 does not complete M5 or Gate M. WP7–WP8 stay closed until WP14 is accepted.
Status when this document was written: baseline `5010732`, CI green on four jobs, WP6 accepted technically on a synthetic corpus and scripted adapters only.

## 1. What was audited

Read directly: `docs/DOMAIN_WORKFLOWS.md`, `workflows/domain_engine.py`, `workflows/domain_specs/{generic,acc_fraud_detection}.json`, `taxonomy/{study_designs,domains,scope,diagnostics}.json`,
`workflows/skills/*`, `workflows/profiles/*`, `knowledge/literature.py`, `discovery/landscape.py`, `reading_protocols.json`, `tasks/goals.py`, `docs/LANDSCAPE.md`,
`PLAN_M5_TASKS_AND_DISCOVERY.md`, `M5_BACKLOG.md`, `tests/test_domain_engine.py`, `tests/fixtures/dw_edu_qualitative_fixture.json`.

## 2. Capability matrix before WP6.1

`IMPLEMENTED_AND_TESTED` = code path exercised by a test that fails if it breaks. `REPRESENTABLE_BUT_NOT_OPERATIONAL` = the data can be stored or declared but nothing acts on it.
`NOT_SUPPORTED` = neither.

| # | Capability | Status | Evidence / gap |
|---|---|---|---|
| 1 | Shared core: StateStore, CAS, Source@revision, EvidencePassage, ECA, ResearchProblem, ResearchTask, library, checkpoints, provenance | IMPLEMENTED_AND_TESTED | independent of design; used by WP4–WP6 |
| 2 | `DomainWorkflowSpec` engine: stage applicability with justification, entry conditions, evidence requirements, loop-backs, closed predicate language | IMPLEMENTED_AND_TESTED | `tests/test_domain_engine.py` |
| 3 | Built-in specs | IMPLEMENTED_AND_TESTED for two: `LIB_DW_GENERIC` (any design, no guidance) and `LIB_DW_ACC_FRAUD_DETECTION` (archival / predictive / quasi-experimental) | no spec for a conceptual, critical-synthesis or qualitative design |
| 4 | Qualitative-education spec | REPRESENTABLE_BUT_NOT_OPERATIONAL | `tests/fixtures/dw_edu_qualitative_fixture.json` is a test fixture that says it is "not an education workflow"; not loadable by id |
| 5 | `study_design` vocabulary (14 values incl. conceptual, narrative_review, systematic_review, qualitative_interview, case_study, design_based) | REPRESENTABLE_BUT_NOT_OPERATIONAL for all but archival/predictive_modeling | declared in `study_designs.json`; only the fraud spec filters on it |
| 6 | `research_purpose` as a recorded fact | NOT_SUPPORTED | nearest: `answer_kind` on a ResearchProblem interpretation (a property of one reading; nothing acts on it) |
| 7 | Evidence form / claim kind of a landscape entry | NOT_SUPPORTED | WP6 has one claim type (`finding`, empirical); `Claim.claim_tier` (incl. `conceptual`) exists for claims, not for landscape entries |
| 8 | `discipline_context` | IMPLEMENTED for accounting, finance, education, cross_domain; NOT_SUPPORTED for management, information systems | `domains.json`; a closed hierarchy |
| 9 | Static discipline profiles (accounting, finance, education) | IMPLEMENTED_AND_TESTED, but all three describe quantitative traditions | the education profile is policy evaluation / VAM / IRT: it must not be read as "education research standards" |
| 10 | Theory / assumption extraction | IMPLEMENTED_AND_TESTED in the literature engine (`RP_THEORY`: theory_invoked, assumed_mechanism, construct_definition, stated_assumption, assumption_tested; `literature._theory`) | **WP6 gap**: `landscape.FIELD_TO_TYPE` maps only construct_definition and assumed_mechanism; theory, stated assumptions and tested assumptions are extracted by the protocol and dropped by the landscape |
| 11 | Landscape relations | IMPLEMENTED_AND_TESTED: `contradiction` (rule), `version_difference` (rule), `definitions_differ` (rule flag) | conceptual_divergence, theoretical_incompatibility, explanatory_incompleteness, boundary_tension, complementarity: NOT_SUPPORTED; no way to hold a semantic relation proposal |
| 12 | Implied / critic-proposed assumptions | NOT_SUPPORTED | nothing separates "the author wrote it" from "the suite or a reviewer thinks it is needed" |
| 13 | Inference assessment | IMPLEMENTED_AND_TESTED for empirical findings only (`landscape.assess_method`: methodological-literature basis, threats, cap) | refuses every non-finding entry (`ENTRY_NOT_A_FINDING`); no route for interpretive, conceptual or critical claims |
| 14 | Reproduction dimension | IMPLEMENTED_AND_TESTED for findings | shown as `NOT_ATTEMPTED` only on findings; meaningless (not "pending") for a proposition |
| 15 | Recognition of a tension in the literature | NOT_SUPPORTED | nothing links a proposed tension to papers that already address it; the suite could claim a tension "unrecognised" |
| 16 | Currency of conclusions drawn from absence | IMPLEMENTED for the landscape as a whole (WP6) | not per inference: a confirmed relation or an "absent from the reviewed set" statement is not flagged individually when the corpus moves |
| 17 | Generic workflow on a conceptual project | IMPLEMENTED but unsuited: probed on a project with `study_design: conceptual`, `run_workflow` under `LIB_DW_GENERIC` gives methods `REQUIRES_VERIFICATION` and results `INSUFFICIENT_EVIDENCE` (a conceptual project is read as a deficient empirical one); under the fraud spec every stage is `NOT_APPLICABLE` | behaviour is accidental, not a contract: no spec says that a critical synthesis has no results stage and what its steps are |
| 18 | Contribution skill: identification required only for causal-tier claims | IMPLEMENTED_AND_TESTED | does not force identification on a `conceptual` claim; no conceptual-claim assessment either |

## 3. Why `study_design` and `DomainWorkflow` are not enough, and what is the smallest change

* `study_design` says how a **project** is done. A literature landscape reads papers of every design for any project: whether a *claim* is empirical, interpretive or conceptual is a property of the
  claim in the source paper, not of the project's design. So the standard of inference has to be chosen per claim.
* A project's **purpose** (explain, predict, interpret, develop theory, critically synthesise, evaluate, improve a method) is independent of its design (a systematic review can be descriptive or critical).
  The engine has no fact called purpose, so a step cannot apply or not apply depending on it.
* `DomainWorkflowSpec` already expresses an execution contract (stage not applicable with justification, entry conditions, loop-backs) and needs no replacement. What it lacks is a way to branch on purpose and
  specs for non-empirical logics.

Therefore **no new object type, no `research_genre` enum, no parallel store**. The "inquiry contract" is the combination of four things that already exist or are one field away:

| Dimension | Where it lives | Change |
|---|---|---|
| `research_purpose` | new optional `ResearchProblem.inquiry.research_purposes` (vocabulary `research_purpose`) | added field; absent on existing problems |
| `research_design` | `study_design` vocabulary, recorded in `ResearchProblem.inquiry.research_design` and on the ResearchQuestion as today | none to the vocabulary |
| `evidence_and_inference` | per landscape entry: `claim_kind` (empirical / interpretive / conceptual) → inference route; project-level `inquiry.evidence_forms` (vocabulary `evidence_form`) | new, in the landscape |
| `discipline_context` | `domain` / `subfield` taxonomy, `inquiry.discipline` | two domains added (management, information_systems) |

`DomainWorkflowSpec` gains optional `research_purposes` and a predicate `research_purpose_in`. A spec that does not use them keeps the **same dict and digest** (`to_dict` omits empty `research_purposes`), so
every stored pin `ID@version#digest` stays valid. Three new built-in specs, all `cross_domain` and `EXTRACTED` (they fix the order and conditions of work, no method content, like the fraud skeleton):
`LIB_DW_CRITICAL_SYNTHESIS`, `LIB_DW_CONCEPTUAL_ARGUMENT`, `LIB_DW_QUALITATIVE_INQUIRY`.

## 4. Inference routes (what makes a claim check different, without a score)

A claim kind selects the **considerations** a reviewer usually raises and the **kinds of basis** that can support `SUPPORTED_WITHIN_SCOPE`. Considerations are prompts shown in the export, never a mandatory checklist;
no combined scientific score is produced (there is no rubric or validation for one).

| Route | Claim | Considerations (examples) | Basis kinds accepted for `SUPPORTED_WITHIN_SCOPE` |
|---|---|---|---|
| `empirical` | finding | measurement, design, identifying assumptions, diagnostics, inference limits | `methodological_literature` (unchanged from WP6) |
| `interpretive` | interpretive finding | transparency of interpretation, context, alternative interpretations, credibility, reflexivity where it applies | `methodological_literature`, `textual_evidence` |
| `conceptual` | theoretical claim | conceptual clarity, logical coherence, consistency of assumptions, rival explanations, correspondence with prior theory, boundaries of applicability | `theoretical_literature`, `logical_analysis` |
| (critical synthesis) | a proposed relation or implied assumption | faithful reconstruction of prior literature, evidence that an attributed assumption exists, fair treatment of rival interpretations, reasoning from the tension to the reconceptualisation | **considerations only**: no verdict is computed; the researcher's confirmation (with reason, acknowledging missing information) is the decision |

The cap rule keeps its shape: `SUPPORTED_WITHIN_SCOPE` needs a criterion on an accepted basis kind, every criterion met, at least one threat/rival/alternative checked and none open; otherwise it is stored as `LIMITED`
with the blockers named. ECA never moves it; reproduction does not move it; a model never writes it.

## 5. Landscape changes

1. `RP_LANDSCAPE@2` (data): adds `theoretical_claim` (types proposition, argument_conclusion, critique, reconceptualization) and `interpretive_finding`; reads the theory dimension as well. Findings keep their fields.
2. Theory and assumptions: `theory_invoked` → category `theory`; `stated_assumption` and `assumption_tested` → category `stated_assumption` (item_type `stated` / `tested_in_source`). All keep Source@revision and EvidencePassage.
   Provenance of an entry is `author_stated`. A **suite- or critic-proposed** assumption is never an entry: it is a proposal of kind `implied_assumption` with provenance `suite_proposed` / `researcher_proposed`, its reasoning,
   the entries it rests on, status `PROPOSED`.
3. Relations: `RELATION_KINDS` = empirical_disagreement (the existing rule-derived `contradiction`, now also labelled with this relation), version_difference (rule), conceptual_divergence, theoretical_incompatibility,
   explanatory_incompleteness, boundary_tension, complementarity (model- or researcher-proposed only). `definitions_differ` stays an observation and is never converted into `conceptual_divergence`.
   A proposal must give: the claims (entry ids → Source@revision, passage, place), what they share (object, scope, unit), how they differ (definition, assumption, mechanism, reasoning), why they are incompatible / only
   differ in scope / complement, missing information, the level of analysis of each side (required for `theoretical_incompatibility`, where **differing levels reject the proposal** when a model makes it; optional and shown for the others), for conceptual divergence the
   difference in meaning apart from wording, and optionally the papers that already address the tension. A deterministic gate checks each of these before anything is stored; failures are listed with the reason.
4. Recognition: the suite never states that a tension is "unrecognised". A proposal records `addressed_by` entries; the status is `ADDRESSED_IN_REVIEWED_LITERATURE` or `NOT_FOUND_IN_REVIEWED_SET` (a statement about the reviewed set, flagged for re-assessment when the corpus changes).
   `tension_candidates` lists held full texts that may already address a proposal (lexical, no model).
5. Confirmation: a model-proposed item is `UNCONFIRMED` until the researcher decides, with a reason; confirming an item with missing information needs the researcher to acknowledge it. The export shows provenance and confirmation of every
   theoretical inference; nothing proposed by a model is shown as established.
6. Currency per inference: each inference lists what it depends on (its Source@revisions; the corpus snapshot for absence statements) and is flagged `needs_reassessment` with the reason when one of them moved.
7. Inquiry snapshot in the landscape (purpose, design, evidence forms, discipline) and in the export header.

## 6. Files and functions

| File | Change |
|---|---|
| `taxonomy/inquiry.json` (new) | vocabularies `research_purpose`, `evidence_form`, `claim_kind`, `relation_kind`, `basis_kind`, `proposal_provenance` |
| `taxonomy/domains.json` | + management (organizational_behavior, organizational_communication), information_systems (human_ai_interaction) |
| `discovery/problem.py`, `protocols/schema.py` | optional `inquiry` on ResearchProblem: `new_problem(inquiry=)`, `revise_problem(inquiry=)`; shown in `problem_view`; CLI `--purpose --design --evidence-form --domain --subfield` |
| `workflows/domain_engine.py` | `research_purposes` on the spec (omitted from `to_dict` when empty), predicate `research_purpose_in`, `ProjectFacts.research_purpose`, `facts_from_store(research_purpose=)`, filter in `stage_status`; CLI `domain next --research-purpose` |
| `workflows/domain_specs/{critical_synthesis,conceptual_argument,qualitative_inquiry}.json` (new) | execution contracts for non-empirical logics |
| `workflows/protocols_data/reading_protocols.json` | `RP_LANDSCAPE@2`, `RP_THEORY@3` |
| `discovery/relations.py` (new) | the proposal gate, prompt and schema, confirmation / recognition / dependency helpers |
| `discovery/landscape.py` | new categories and mapping, `claim_kind`, routes, `assess_method` generalised (empirical behaviour unchanged), `propose_relations`, `add_proposal`, `tension_candidates`, `review_inference` (acknowledge_missing, proposals), per-inference dependencies, carry-over of proposals, inquiry snapshot; error code `ENTRY_NOT_A_FINDING` became `ENTRY_NOT_A_CLAIM` |
| `discovery/cli_landscape.py` | `landscape propose`, `landscape add-proposal`, `landscape candidates`, `--acknowledge-missing`, `--route` display |
| `export/landscape.py` | theory, assumptions, theoretical claims and interpretive findings; relations table with provenance, confirmation, recognition, missing information, dependencies; implied assumptions apart; route considerations; inquiry header; a new `relations.csv` (the WP6 files are otherwise unchanged) |
| `tasks/goals.py`, `tasks/capabilities.py` | optional `propose_relations` step in `build_landscape` (default off: plan unchanged) |
| tests | `tests/inquiry_fixtures.py` (three independent corpora), `tests/test_inquiry_cross_design.py`, regression additions to `test_domain_engine.py` / `test_landscape.py` |

## 7. Compatibility

* Objects already stored are unchanged: `inquiry` is optional; `Landscape` objects built before WP6.1 lack the new keys and are read with defaults (`proposals` empty, no `claim_kind`).
* `RP_LANDSCAPE@1` → `@2` and `RP_THEORY@2` → `@3` (which now also reads sections whose heading matches no label, because conceptual papers name their sections after their ideas) change the protocol digests, so extractions cached under the old versions are not reused (a cost, not a correctness issue; the literature engine's theory review reads more text per paper). `tests/test_reading_protocols.py` was changed to say so explicitly.
* Spec pins: specs that do not set `research_purposes` keep their digest (tested).
* WP6 behaviour on the fraud-style fixtures is a regression target: entry counts, inference kinds and ids, dimensions and exports for `tests/landscape_fixture.py` must be unchanged except for the explicit additions.

## 8. Not done here

No WP7–WP8; no run on real full texts or with a real model (`REAL_CORPUS_VALIDATION_PENDING`); no rubric or composite score; no auto-confirmation of theory, novelty or contribution; no change to the Research Core, to
`GATEL_REAL` or to any store under `sample/`.
