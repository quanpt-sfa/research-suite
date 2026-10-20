# Research Suite 9.2 — Cumulative, Domain-Aware Research Workflows: Implementation Plan (v3)

**Status:** Draft v3 for owner approval
**Date:** 2026-10-20
**Supersedes:** v2 (`RESEARCH_SUITE_9_2_REFACTOR_PLAN_v2.md`) and v1 (`…_v1.md`). Both are kept for comparison.

**Inputs:**
- The independent audit of PR quanpt-sfa/research-suite#3 at `9728134`: NOT ACCEPTED.
- The owner's review of v1, from which v2 was built.
- The owner's review of v2. It asked for six acceptance-testable changes, two ordering fixes, stronger Gates K and L, legacy-cache retention, and a pilot domain. All are listed in Appendix A.

## What changed in v3

v3 is not a rewrite. The v2 architecture is unchanged: four layers, library store, playbooks, Journal Policy Registry, Introduction/Literature engines, knowledge lifecycle. v3 adds the following:

| Priority | Change | Acceptance condition | Where |
|---|---|---|---|
| **P0** | Split knowledge validation into three independent dimensions: **source fidelity**, **computational reproducibility** and **methodological validity**. Methodological validity is assessed per context. "Adopted" becomes a usage record, not a quality claim. | A playbook that is extracted correctly and reproduced correctly is still **not** methodologically valid by default, and is not recommended as "applicable". | §4.1, 3D, 4.3, Gate K |
| **P0** | **Evidence–Claim Adjudication (ECA)**: a semantic check of the claim against the passage it cites. The *generator* of a statement is recorded separately from its *evidence basis*. | A correct quote with a wrong interpretation is detected, or the statement stays unverified. | §3 I9/I16, §4, Phase 1, 3A.2 |
| **P0** | Four separate status axes: **execution**, **context**, **assessment** and **applicability**. These replace the single combined status. | A successful run does not mean the scientific conclusion is verified. | §4.2, all phases |
| **P1** | `DomainWorkflow` becomes an **execution contract**: steps, entry conditions, evidence requirements, branching and loop-back, and per-step applicability. A deterministic engine drives it. | Two study types follow different procedures and gating, not just different prompts. | 3A.1, Gate B |
| **P1** | Library → project import copies the full **dependency closure**: objects, CAS artifacts, fragments and replication assets. | A project with an imported playbook can be opened and reproduced while the library is unavailable. | 4.2, Gate G |
| **P1** | Gates K and L test scientific content: blind reference annotation, cold-versus-warm matched tasks, and **at least one real paper with code**. | Fidelity and reuse are shown by evidence, not by schema completeness or token savings. | Phase 6, §8 |
| — | Old 9.1.1 caches are **kept as legacy/unverified history**, not deleted. | Legacy outputs are retrievable, never reused, and linked to the runs that correct them. | 4.1, D3 |
| — | Ordering fixes: minimal reading protocols move ahead of M2's extraction acceptance. 3B's integration is gated on valid 3C output. Milestones close on integration tests, not unit tests. | No milestone can be reported complete while a key scientific link is unwired. | §7 |
| — | Pilot domain: **accounting archival / financial-reporting fraud detection**. It is the *first* pilot only, never the default template. | The generic default and a structurally different contrast spec remain. | 3A.1, D7 |

**Non-goals** (unchanged): enterprise security, multi-user authorization, distributed or cloud deployment, autonomous multi-agent orchestration, and new mandatory dependencies.

---

## 1. Mission and the decisive question

Research Suite serves one academic researcher in accounting, finance and education. A finished study should leave behind data, code, results and a manuscript. It should also leave **verified, reusable research knowledge**: a method playbook, an analysis procedure, a way to assess contributions, or a lesson from peer review. That knowledge is only valid within stated conditions.

The architecture is judged by whether, at the end of each project, Research Suite can answer this question with evidence:

> **"From the study we just completed, what have we verified about methods and research procedures that can be reused in the next study — and under what conditions?"**

v3 makes "verified" precise. The answer must state which dimension was verified (source fidelity, computational reproducibility, methodological validity) and in which context. Being used in a project does not count as being valid.

---

## 2. Target architecture: four layers (unchanged from v2)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ L4  Project Workflow Instance        (per project store)                     │
│     binds: domain workflow@ver + method playbook(s)@ver + journal policies   │
│     + imported dependency closure + project data                             │
│     → domain engine drives steps → AnalysisPlan → Execution DAG → Results    │
├──────────────────────────────────────────────────────────────────────────────┤
│ L3  Method Library + Journal Policy Registry   (library store)               │
├──────────────────────────────────────────────────────────────────────────────┤
│ L2  Domain Workflow Library (execution contracts) + corpus + KnowledgeItems  │
├──────────────────────────────────────────────────────────────────────────────┤
│ L1  Research Core (existing 9.0 engine, extended): objects, edges, CAS,      │
│     provenance, review authority, workflow_runs, token accounting, budgeted  │
│     context, model adapter, Evidence–Claim Adjudication records              │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 2.1 Storage decisions

| Concern | Decision | Reason |
|---|---|---|
| Library location | A dedicated Research Core store, `StateStore` at `<home>/LIBRARY` | Reuses revisions, CAS, edges, audit, review authority and integrity checks. |
| Library identifiers | All library object IDs carry the prefix `LIB_`. Projects reject creating `LIB_*` objects except through import. | Prevents ID collisions when the closure is imported. |
| Cross-store references | `LIB:<object_id>@<revision>#<content_digest[:16]>`, held in content fields | Edges cannot cross stores; the digest pin keeps references reproducible. |
| Using library items | `research library use` imports the item's **entire dependency closure** in one atomic transaction, together with a closure manifest (4.2). | The project must work offline from the library (P1 change). |
| New object types | Added in `core/model.py::OBJECT_TYPES`, `protocols/schema.py` and `protocols/snapshot.py` | `objects.object_type` has no SQL `CHECK`, so no table rebuild is needed. |
| New edge relations | **None.** Reuse the 10 existing relations. | `edges.relation` has a SQL `CHECK` (`state/schema.py:82`), and the table is append-only and trigger-protected. |
| Domain/playbook content | Data (JSON specs and markdown fragments with digests), not code | Revised through the knowledge lifecycle, not through code releases. |
| Shared corpus | Lives in the library; projects import pinned copies with their artifacts. | Each paper is read and extracted once. |

### 2.2 New object types

| Type | Store | Purpose |
|---|---|---|
| `DomainWorkflow` | library | Execution contract for a subfield and study design (3A.1). |
| `MethodPlaybook` | library | Literature-grounded procedure carrying three-dimensional validation (3D). |
| `JournalPolicy` | library | Versioned, sourced policy of one of four types. |
| `KnowledgeItem` | library / project | `verification_rule`, `evidence_comparison_pattern`, `analysis_script`, `review_lesson`, `checklist`, `reading_protocol`, `replication_package`, `failure_record`, `validity_assessment`, `library_import_manifest`. |
| `ContributionArgument` | project / library | Five-component reconstruction of an Introduction (3B). |
| `AnalysisPlan` | project | Project-specific instantiation of playbooks. |
| `Adjudication` (existing type, extended use) | project / library | Evidence–Claim Adjudication records (3A.2). Currently a server-only type, which is kept. |

---

## 3. Invariants

I1–I8 are kept from v1. I9 is revised. I10–I14 are kept from v2. I15–I18 are new.

| # | Invariant |
|---|---|
| I1 | **No verdict from keywords.** Keyword or lexical cues may only produce `review_flags`. |
| I2 | **"Unknown" is the default.** Missing input yields `NOT_ASSESSED`, `UNKNOWN` or `INSUFFICIENT`. |
| I3 | **No silent upgrade on any axis.** Caps propagate through cache, model step and handoffs, per axis (§4.2). |
| I4 | **Budget enforced.** The request is ≤ `max_input_tokens`, or it is not sent. |
| I5 | **Every run is registered.** |
| I6 | **Every token number states its basis.** |
| I7 | **Workflows emit proposals; they never write scientific objects.** |
| I8 | **Compatibility:** Python 3.10–3.12, `jsonschema` the only mandatory dependency, no agent framework. |
| **I9 (revised)** | **The generator of a statement is not its evidence.** Every statement records `generated_by`: `researcher`, `model:<descriptor>`, `deterministic:<module@ver>`, `import` or `legacy`. Separately it records `evidence_basis[]`: `source_passage`, `replication_code`, `project_run`, `journal_policy`, `reporting_standard`, `review_feedback` or `researcher_judgement`. A model-generated statement may carry strong evidence, but its assessment is capped at `PROVISIONAL` until adjudicated (I16) or confirmed by the researcher. Every playbook step, criterion and interpretation rule must have at least one evidence basis. |
| I10 | **Knowledge transfers only within validated scope.** An exclusion mismatch means the item is never offered as applicable. |
| I11 | **Author-asserted ≠ verified** (published Introductions, self-declared novelty). |
| I12 | **Precedent ≠ requirement.** Only a matching `official_requirement` is binding. |
| I13 | **Methods shape Results.** |
| I14 | **Promotion is explicit:** owner token, scope, exclusions, and evidence. |
| **I15** | **Validity is three-dimensional and contextual.** Source fidelity, computational reproducibility and methodological validity are recorded and evaluated separately. Methodological validity is asserted only for a stated context (estimand, measurement, assumptions, data). Usage in a project is recorded as a usage record and never raises validity. |
| **I16** | **High-impact statements require semantic adjudication.** A statement used in contribution, measurement, identification or interpretation decisions, or promoted to the library, cannot reach `VERIFIED_WITHIN_SCOPE` without an ECA record that is supported as stated or supported with its qualifiers applied, and confirmed by the researcher. |
| **I17** | **Statistical outcomes are not booleans.** Only execution checks yield pass/fail. Statistical diagnostics yield described signals with cited criteria. Scientific interpretation yields claim effects. A diagnostic concern never blocks the DAG and is never reported as "PASS". |
| **I18** | **Domain behaviour lives in execution contracts.** Two domain workflows must be able to differ in step order, entry conditions, evidence requirements, checks, loop-back conditions and non-applicable steps. Differing prompt fragments alone do not make a domain workflow. |

---

## 4. Shared vocabularies and status model (Phase 0 deliverable)

The vocabularies live in `research_harness/taxonomy/*.json` and are loaded and validated by `taxonomy/__init__.py`.

| Vocabulary | Values |
|---|---|
| `domain` / `subfield` | accounting: `archival_financial_reporting`, `auditing`, `management_accounting`, `accounting_information_systems`; finance: `asset_pricing`, `corporate_finance`, `banking`; education: `learning_analytics`, `design_based_research`, `qualitative_education`; cross-domain: `conceptual`, `provocation` |
| `study_design` | `archival`, `experimental_lab`, `field_experiment`, `quasi_experimental`, `survey`, `qualitative_interview`, `case_study`, `design_based`, `mixed_methods`, `analytical_model`, `predictive_modeling`, `conceptual`, `systematic_review`, `narrative_review` |
| **`generated_by`** | `researcher`, `model`, `deterministic`, `import`, `legacy` |
| **`evidence_basis.kind`** | `source_passage`, `replication_code`, `project_run`, `journal_policy`, `reporting_standard`, `review_feedback`, `researcher_judgement` |
| `journal_policy.type` | `official_requirement`, `reporting_standard`, `published_precedent`, `editorial_feedback` |
| `scope` dimensions | `domain`, `subfield`, `study_design`, `data_regime` (jurisdiction, data source, label source, outcome measure), `unit_of_analysis`, `period`, `journal`, `estimand` |
| `claim_change` | `STRENGTHENED`, `UNCHANGED`, `NARROWED`, `QUALIFIED`, `WEAKENED`, `REVERSED`, `NOT_SUPPORTED` |
| `intro_component_status` | `AUTHOR_ASSERTED`, `CORROBORATED`, `CONTESTED`, `UNSUPPORTED`, `NOT_ASSESSED` |
| `eca.equivalence` | `EQUIVALENT`, `NARROWER`, `BROADER`, `DIFFERENT`, `CONTRADICTS`, `UNRESOLVED` |
| `eca.verdict` | `SUPPORTED_AS_STATED`, `SUPPORTED_IF_QUALIFIED`, `NOT_SUPPORTED`, `UNRESOLVED` |
| `diagnostic.signal` | `NO_CONCERN_INDICATED`, `CONCERN_INDICATED`, `INCONCLUSIVE`, `NOT_COMPUTABLE` |
| `claim_effect` | `STRENGTHENS`, `LIMITS`, `CHANGES`, `NO_BEARING`, `NOT_ASSESSED` |

### 4.1 Knowledge validation: three dimensions plus lifecycle and usage

This replaces v2's linear `EXTRACTED → SOURCE_VERIFIED → TESTED → ADOPTED` chain. Every library item, and every critical element inside a playbook, carries all of the following:

| Dimension | Question | Values | Entry conditions (enforced in code) |
|---|---|---|---|
| **`source_fidelity`** | Did we understand and reconstruct correctly what the authors did? | `NOT_CHECKED` → `QUOTE_VERIFIED` → `MEANING_VERIFIED`; `DISPUTED` | `QUOTE_VERIFIED`: every source span exists verbatim in stored text. `MEANING_VERIFIED`: every critical element has an ECA verdict of `SUPPORTED_AS_STATED` or `SUPPORTED_IF_QUALIFIED` with qualifiers carried into the element, plus researcher confirmation. `DISPUTED`: any ECA verdict is `NOT_SUPPORTED`, or there is unresolved counter-evidence. |
| **`computational_reproducibility`** | Can we rerun the procedure and reproduce the results? | `NOT_ATTEMPTED`, `NO_ASSETS`, `RAN`, `REPRODUCED_WITHIN_TOLERANCE`, `FAILED_TO_REPRODUCE` | Computed from DAG and replication runs (5b, 3E) with declared tolerances. |
| **`methodological_validity`** | Is the procedure appropriate to *this* estimand, measurement, set of assumptions and data? | per context: `NOT_ASSESSED`, `CONTESTED`, `PROVISIONAL`, `SUPPORTED_WITHIN_SCOPE` | Stored as a list of `validity_assessments` (below). It is **never global**. `SUPPORTED_WITHIN_SCOPE` requires all of the following: ≥ 1 basis from *methodological* literature (not only application papers); every registered `known_criticism` addressed or explicitly scoped out; project diagnostics where applicable; researcher confirmation. |

```json
"validity_assessments": [{
  "context": {"subfield": "...", "study_design": "...", "estimand": "...", "measurement": "...",
              "data_regime": {...}, "assumptions": ["..."]},
  "verdict": "PROVISIONAL",
  "evidence_basis": [...], "known_criticisms": [{"ref": "LIB:..", "status": "addressed|scoped_out|open"}],
  "diagnostics_runs": ["RUN_..."], "generated_by": "researcher|model", "confirmed_by_researcher": false,
  "assessed_at": "..."
}]
```

- **Lifecycle** (separate axis): `ACTIVE`, `RESTRICTED`, `SUPERSEDED`, `WITHDRAWN`.
- **Usage records** replace v2's "ADOPTED" status. They record that an item was used in project P, in context X, with outcome Y (diagnostics concerns, deviations, failures). They are descriptive only and never change validity.
- **Negative knowledge is first-class.** `KnowledgeItem{kind:"failure_record"}` and `known_criticisms` are stored and shown at reuse, so a flawed method that was used once is not silently recommended again.

**Recommendation rule** (used by `library match`, 4.3):

| Status | Condition |
|---|---|
| "applicable" | `methodological_validity = SUPPORTED_WITHIN_SCOPE` for a context whose scope matches, and `source_fidelity ≥ QUOTE_VERIFIED` (MEANING_VERIFIED for critical elements), and no exclusion mismatch. |
| "candidate, validity not established here" | Fidelity or reproducibility is good but validity is not assessed for this context. The output lists the assessment questions to answer. |
| "related, not applicable because …" | An exclusion mismatch, or the item is `CONTESTED` or `WITHDRAWN`. |

### 4.2 Status model: four independent axes

This replaces v1/v2's single status enum, including `NOT_APPLICABLE`. Every run, plan step and assessed item carries `Status{execution, context, assessment, applicability}`.

| Axis | Values | Meaning |
|---|---|---|
| `execution` | `SUCCEEDED`, `FAILED`, `NOT_RUN`, `BLOCKED` (+ `reason`: `exception`, `invalid_output`, `budget_refused`, `missing_runtime`, `upstream_failed`, `not_applicable`) | Did the computation or model call happen technically? |
| `context` | `COMPLETE`, `PARTIAL`, `INSUFFICIENT` (+ omissions list) | Was the needed evidence available and within budget? |
| `assessment` | `NOT_ASSESSED`, `PROVISIONAL`, `VERIFIED_WITHIN_SCOPE`, `CONTESTED` (+ scope) | How far is the scientific conclusion established? |
| `applicability` | `APPLICABLE`, `NOT_APPLICABLE`, `UNKNOWN` (+ justification) | Does this step or stage apply to this study design? |

**Cap rules** live in `contracts.apply_caps()` and are table-tested. There is no "combined status".

| Condition | Cap |
|---|---|
| `execution ≠ SUCCEEDED` | `assessment = NOT_ASSESSED` |
| `context = INSUFFICIENT` | affected items' `assessment = NOT_ASSESSED` |
| `context = PARTIAL` | `assessment ≤ PROVISIONAL` |
| `generated_by = model` without ECA plus researcher confirmation | `assessment ≤ PROVISIONAL` |
| unresolved counter-evidence, or an ECA verdict of `NOT_SUPPORTED` | `assessment = CONTESTED` |
| `applicability = NOT_APPLICABLE` | `execution = NOT_RUN(reason=not_applicable)`; no output is expected or required |
| downstream item that depends on an upstream item | downstream `assessment ≤` upstream `assessment` |
| reuse from cache | all four axes returned exactly as stored |

`VERIFIED_WITHIN_SCOPE` can be reached only through researcher confirmation of evidence-backed items. No code path produces it from a successful run alone. The test `test_execution_success_does_not_imply_verified` enforces this.

---

## 5. Branch and PR strategy (unchanged)

- Convert PR #3 to draft.
- Create the integration branch `refactor/9.2` from `9728134`.
- Stack one PR per (sub)phase.
- Flip strict xfails only in the PR that fixes them.
- Add a CI leg with `tiktoken` installed.
- Merge to `main` at the end and close PR #3 as superseded.
- Domain content enters the library as data, at fidelity `NOT_CHECKED` and validity `NOT_ASSESSED`. It is never committed as code fixtures posing as verified.

---

## 6. Phases

### Phase 0 — Truth baseline, taxonomy, status axes (2–3 days)

| Task | Files | Detail |
|---|---|---|
| 0.1 Audit regressions as strict xfails | `tests/test_audit_9_1_1_regressions.py` | One test per audit check. |
| 0.2 Correct false documentation | `docs/DEEPAGENTS_REFERENCE_ALIGNMENT.md`, `docs/WORKFLOW_9_1_1_ACCEPTANCE.md` | Mark the docs "SUPERSEDED BY AUDIT". |
| **0.3 Four-axis status** | `workflows/contracts.py` | `ExecutionStatus`, `ContextStatus`, `AssessmentStatus`, `Applicability`, `Status` dataclass, `apply_caps()`. The old strings map onto the axes through a one-release compatibility adapter. |
| 0.4 Taxonomy | `research_harness/taxonomy/*.json`, loader | Includes the vocabularies listed in §4. |
| **0.5 Provenance envelope** | `workflows/contracts.py` | `Statement{text, generated_by, evidence_basis: list[Basis], eca_ref?, assessment}`. `Basis{kind, ref, char_start?, char_end?, quote_verified?, retrieved_at?}`. `require_evidence_basis()` enforces I9. |
| 0.6 CI tokenizer leg; plan and audit copied to `docs/refactor_9_2/` | ci, docs | — |

**Tests:** `test_apply_caps_table` (every cap rule) and `test_execution_success_does_not_imply_verified`.

### Phase 1 — Working model execution path (2–3 days)

This phase is kept from v1:
- the adapter contract, plus `describe()` and `count_tokens()`;
- an optional Anthropic adapter behind the `[model]` extra;
- a validated model step;
- no call when `context = INSUFFICIENT` (`execution = NOT_RUN(budget_refused)`);
- adapter-only usage figures.

**Corrected in v3 (generator vs evidence).** Extraction outputs (Introduction components, literature extractions, playbook drafts) must return `evidence_basis` spans of the form `(object_id, revision, char_start, char_end)`. These spans are quote-verified, and unverifiable spans give `execution = FAILED(invalid_output)`. Each statement is stored with `generated_by="model:<descriptor>"` **and** its verified `evidence_basis`. These are two separate attributes. The assessment of each statement is capped at `PROVISIONAL` (§4.2) until ECA and researcher confirmation.

### Phase 2 — Context selection, budget, full-text scientific comparison (4–5 days)

Kept from v2, with these parts:
- table-driven expansion and recorded `relations`;
- RQ scoping;
- no keyword-based priority;
- an enforced tiered budget;
- evidence rendered once;
- split prompts;
- full-text paging;
- section detection (`sections.py`);
- `compare_sources(dimension, source_ids, budget)`.

`context` status is computed here: `COMPLETE` when nothing is omitted; `PARTIAL` when Tier 1 or Tier 2 items are omitted; `INSUFFICIENT` when a Tier 0 item is missing or does not fit.

### Phase 3 — Domain-aware scientific workflows

#### 3A.1 — Domain workflow engine as an execution contract, plus the structured-verdict foundation (8–9 days)

**Foundation, kept from v1/v2.** Structured evidence fields, the hypothesis/result truth table, removal of all keyword verdicts and templates, and `review_flags`.

**`DomainWorkflow` is now an executable contract, not a prompt bundle:**

```json
{
  "id": "LIB_DW_ACC_FRAUD_DETECTION", "domain": "accounting", "subfield": "archival_financial_reporting",
  "study_designs": ["archival", "predictive_modeling"], "version": "0.1.0",
  "steps": [
    {"id": "label_provenance", "stage": "methods", "kind": "decide",
     "entry_conditions": [{"exists": "Dataset", "where": {"role": "label_source"}}],
     "evidence_requirements": [{"kind": "source_passage", "about": "label definition and timing", "min": 1}],
     "produces": ["KnowledgeItem:label_definition"],
     "applicability": {"when": {"study_design_in": ["archival", "predictive_modeling"]}}},
    {"id": "temporal_sample_split", "stage": "methods", "kind": "plan",
     "entry_conditions": [{"step_assessment": {"label_provenance": ">=PROVISIONAL"}}], "...": "..."},
    {"id": "leakage_check", "stage": "results", "kind": "execute",
     "execution_checks": ["..."], "diagnostic_criteria": ["..."],
     "on_condition": [{"when": {"diagnostic_signal": {"leakage_check.feature_timing": "CONCERN_INDICATED"}},
                       "goto": "feature_construction", "reason": "post-period information in features"}]}
  ],
  "stage_instructions": {"methods": "LIB:..@..#.."},
  "scope": {...}, "exclusions": [...],
  "source_fidelity": "...", "methodological_validity": [...]
}
```

- **Engine** (`workflows/domain_engine.py`). `evaluate(project, spec)` returns, for each step: `applicability`, `entry_conditions_met` (with any unmet predicates), `evidence_requirements_met`, `runnable`, and any `loop_back` triggered by a recorded `on_condition`.
  - `next_steps(project)` returns the steps that can run now. `explain(step)` returns the reasons a step is blocked.
  - **Predicates** come from a small, closed set (no `eval`): `exists`, `field_equals`, `field_in`, `step_execution`, `step_assessment` (with comparators), `diagnostic_signal`, `study_design_in`, `scope_matches`, `count_ge`. Each predicate has unit tests.
- **The dispatcher always runs through the engine.** The five generic workflows are re-expressed as the default spec `LIB_DW_GENERIC`. Stage-level and step-level `applicability = NOT_APPLICABLE` gives `execution = NOT_RUN(not_applicable)` with a justification, so a conceptual study has no Results stage.
- **Step runs** are recorded in `workflow_runs` with `domain_step_id`. A loop-back creates a new step run whose `parent_run_id` points at the earlier one, so the history of returns to a step is preserved.
- **Pilot spec (D7):** `LIB_DW_ACC_FRAUD_DETECTION` covers accounting archival / financial-reporting fraud detection. Its steps include:
  - label provenance and timing;
  - sample construction;
  - feature construction with an information-timing check;
  - temporal validation design;
  - class-imbalance-aware evaluation;
  - model comparison protocol;
  - sensitivity to assumptions;
  - interpretation limits;
  - transferability assessment.

  Step content is filled from real method papers through the lifecycle. The spec starts with structure and `evidence_requirements` only.
- **Contrast spec (structural test fixture only):** `LIB_DW_EDU_QUALITATIVE_FIXTURE`. Its steps are: sampling rationale, data-collection protocol, coding-scheme development, intercoder/reflexivity procedure, thematic analysis, trustworthiness checks and interpretation. Statistical steps are `NOT_APPLICABLE`. It loops back to data collection when saturation is not reached. It is labelled as test-only; its content is **not** claimed to be verified and it is not shipped as an education workflow.
- **Profile migration.** As in v2, the profiles become scoped `KnowledgeItem{kind:"checklist"}` objects.

**Tests:**
- v1/v2 verdict tests;
- predicate unit tests;
- **`test_domain_workflows_differ_in_execution_not_prompt`**: give the pilot and contrast specs **identical stage instructions**. Assert that `next_steps`, the step order, entry-condition blocking, evidence requirements, `NOT_APPLICABLE` steps and loop-back behaviour all differ for the same project state. This shows the behaviour comes from the contract.
- `test_loop_back_preserves_history`;
- `test_conceptual_results_not_applicable_axes`.

#### 3A.2 — Evidence–Claim Adjudication (ECA) (4–5 days)

An ECA record is an `Adjudication` object, with `content.kind="evidence_claim"`, attached to a `(statement, passage)` pair:

| Field | Question | Produced by |
|---|---|---|
| `source_match` | Is the passage from the cited source and revision, verbatim? | deterministic (quote verification) |
| `passage_proposition` | What does the passage actually say, including population, conditions, hedges and direction? | model (separate call) or researcher |
| `qualifier_cues` | Conditional language in the passage ("only when", "unless", "conditional on", "in samples where", "we caution") | deterministic cue detector. **Cues are flags, never verdicts (I1).** |
| `equivalence` | Is the statement EQUIVALENT, NARROWER, BROADER, DIFFERENT or CONTRADICTS relative to the passage? | model or researcher |
| `dropped_conditions[]` | Which qualifiers in the passage are missing from the statement? | model or researcher, cross-checked against `qualifier_cues` |
| `counter_evidence[]` | Do other corpus passages (`challenges` edges, plus retrieval over the same construct or method) challenge this reading? | deterministic retrieval + model reading |
| `verdict` | `SUPPORTED_AS_STATED` / `SUPPORTED_IF_QUALIFIED{required_qualification}` / `NOT_SUPPORTED` / `UNRESOLVED` | model proposes; researcher confirms for high-impact statements |

**Process:**
1. **Risk tiering.** `HIGH` covers statements that feed contribution or novelty (3B), measurement or proxy validity, identification assumptions, interpretation or claim change (5a), and any element being promoted to the library. Everything else is `STANDARD`.
2. **Coverage policy.** Every `HIGH` statement is adjudicated. `STANDARD` statements are adjudicated on demand or sampled at a configurable rate. There is one adjudication model call per statement, using a separate adversarial prompt ("state what the passage claims, list any conditions the statement omits, decide whether the statement is broader"). It is **not** a multi-agent loop.
3. **Consistency guard.** If the deterministic `qualifier_cues` are non-empty and the model says `EQUIVALENT` without listing how each cue is handled, the record becomes `UNRESOLVED`, which forces researcher review.
4. **Effect on status.** Through §4.2, an adjudicated statement can reach at most `PROVISIONAL` without researcher confirmation. A `NOT_SUPPORTED` verdict sets it to `CONTESTED`. A `SUPPORTED_IF_QUALIFIED` verdict requires the qualification to be written into the statement (a new revision) before it can be verified.
5. **CLI:**
   - `research adjudicate --statement ID [--passage ID]`
   - `research adjudicate queue --project P` (high-impact items awaiting a decision)
   - `research adjudicate confirm ID --verdict … --token-file`

**Tests:**
- **`test_correct_quote_wrong_interpretation_detected_or_unverified`**: the passage says "Model X performs well only when the panel is balanced"; the statement says "X is suitable for panel data".
  - (a) The scripted adjudicator returns `BROADER` with dropped condition "balanced", so the statement is `CONTESTED` or not verified.
  - (b) The scripted adjudicator wrongly returns `EQUIVALENT`; the cue guard turns this into `UNRESOLVED`, so the statement is never `VERIFIED_WITHIN_SCOPE`.
- `test_generator_and_basis_are_separate_fields`;
- `test_high_impact_requires_confirmation`;
- `test_counter_evidence_marks_contested`;
- `test_qualified_verdict_requires_revised_statement`.

#### 3B — Introduction Argument Analysis (4–5 days)

The five components, the two directions and I11 are as in v2.

**Changes in v3:**
- Every component statement is a `Statement` (generator + basis).
- `established_knowledge`, `unresolved_tension` and `proposed_contribution` are `HIGH` risk, so ECA applies.
- `CORROBORATED` requires an ECA verdict of `SUPPORTED_*` against a passage *outside* the paper.
- Whether a difference is "substantive" is expressed as `assessment`: model-only means `PROVISIONAL`; researcher-confirmed means `VERIFIED_WITHIN_SCOPE`.

**Integration dependency.** The contribution comparison consumes 3C's `RP_CONTRIBUTION` output. 3B can be developed in parallel with 3C, but 3B is **not done** until `test_3b_contribution_comparison_integrates_3c_output` passes against real 3C output. A stub does not count.

#### 3C.0 — Reading-protocol core (2–3 days; moved into M2)

- Defines the schemas for `RP_INTRO_ARGUMENT`, `RP_THEORY`, `RP_METHOD` and `RP_CONTRIBUTION`, with a minimal `run_protocol(protocol, source, sections)` that uses section-aware retrieval and the Phase 1 extraction rules.
- Extraction caching is keyed by `(source rev, protocol@version)`.
- M2's extraction acceptance runs on these real protocols, not on placeholders.

#### 3C — Literature Review Engine (4–5 days, after 3C.0)

Kept from v2:
- three purposes (theoretical, methodological and contribution-oriented synthesis);
- construct-definition divergence;
- MethodRecords;
- per-dimension closeness;
- narrative versus systematic modes, with PRISMA 2020 for systematic mode only;
- cross-project reuse of extractions.

**v3 additions:**
- Synthesised statements (for example "studies A and B assume the same mechanism") are `Statement`s.
- `HIGH`-risk synthesis items feeding Methods or Contribution go through ECA.
- MethodRecords carry `source_fidelity`.

#### 3D — Literature-grounded Methods: Playbooks + Journal alignment (7–9 days)

The playbook fields are as in v2, extended so that **every critical element** (sample construction, estimand or prediction target, specification or feature set, standard errors or validation scheme, diagnostics, interpretation) carries its own `source_fidelity`, `computational_reproducibility` and `validity_assessments`.

- `known_criticisms[]` are registered from methodological literature, for example a critique of a proxy's construct validity. Each one must be `addressed` or `scoped_out` before the validity for that context can be `SUPPORTED_WITHIN_SCOPE`.
- **Selection** (`select_playbooks`) uses the recommendation rule in §4.1. A playbook that is merely extracted and reproduced appears as "candidate, validity not established here", together with the questions needed to assess it.
- **AnalysisPlan fidelity** (`inherited`, `adapted`, `added`, `omitted` with reasons) is as in v2. Each `adapted` element records what changed relative to the reference and why.
- The Journal Policy Registry is as in v2: four types, snapshots, `retrieved_at`, staleness, and binding by article type and effective date.

**Tests:**
- v2 3D tests, plus **`test_extracted_and_reproduced_playbook_not_valid_by_default`**: the fixture is a discretionary-accruals proxy whose formula is quote-verified and whose replication reproduces, but which has no validity assessment for the new context, so `select_playbooks` labels it "candidate, validity not established".
- `test_open_known_criticism_blocks_supported_within_scope`;
- `test_validity_requires_methodological_basis_not_only_application`;
- `test_usage_record_does_not_raise_validity`.

#### 3E — Method-linked Results with Execution DAG: three layers (7–9 days)

The DAG runner is as in v2: stdlib only, topological order, hashes, logs, step runs in `workflow_runs`, and optional R support. v3 **replaces** the single `expected_checks` list with three layers:

| Layer | Content | Output | Effect |
|---|---|---|---|
| **Execution checks** | code ran (exit 0), files exist with the expected schema or format, input/output hashes recorded, replication outputs within declared numeric tolerance | boolean per check → `execution` axis | The only layer that can block downstream steps (`BLOCKED(upstream_failed)`). |
| **Statistical diagnostics** | what the data, model and tests show; whether inference conditions show signs of violation | `DiagnosticRecord{diagnostic, values, reference_criteria:[{criterion, source basis}], reading, signal}` | Never pass/fail. Several sourced criteria may disagree, and all are shown. Signals feed interpretation and the domain engine's `on_condition` branching (for example, a decision point asking the researcher to choose an alternative). They never stop execution. |
| **Scientific interpretation** | how the diagnostics and results strengthen, limit or change each claim | `ClaimEffect{claim_id, effect, reasoning: Statement, depends_on:[runs, diagnostics]}` | `assessment` is `PROVISIONAL` unless the researcher confirms it. It feeds 5a. |

- **Robustness divergence is evidence, not failure.** A robustness result that differs from the baseline is recorded as `Divergence{dimension: measurement|sample|specification|period, magnitude}`. Its interpretation (from the playbook's `interpretation_if_holds` / `_if_fails`) informs `NARROWED` or `QUALIFIED` claim changes.
- **Non-significance refinements** (`UNDERPOWERED`, `IMPRECISE`, `NEGLIGIBLE_EFFECT_SUPPORTED`, `MEASUREMENT_CONCERN`, `INCONCLUSIVE_REASON_NOT_ASSESSED`) are diagnostic readings with a basis, not verdicts. SESOI and alpha are declared in the plan with a basis.
- **A Result depending on a failed or unexecuted step:** `execution` is inherited, and `assessment = NOT_ASSESSED` with the dependency named. This replaces v2's `CONDITIONAL`.
- **Verification of a result:** `VERIFIED_RUN` provenance requires the output hash to match. That is an *execution-level* fact. It never sets `assessment` by itself.

**Tests:**
- `test_dag_blocks_only_on_execution_failure`;
- `test_diagnostic_concern_never_blocks_or_passes`;
- `test_conflicting_criteria_both_reported`;
- `test_robustness_divergence_recorded_as_evidence`;
- `test_result_on_failed_step_not_assessed`;
- `test_hash_verified_result_still_provisional`;
- `test_on_condition_branch_from_diagnostic_signal`.

### Phase 4 — Run registry, Knowledge Library, closure import, promotion (13–16 days)

#### 4.1 `workflow_runs` and legacy history (3–4 days)

Kept from v1/v2: schema 9.0.3, append-only, outside the state digest, reuse through the registry, stable fingerprint including pins, and `recover()` awareness.

**v3 changes:**
- **Columns.** The single `status` column is replaced by `execution_status`, `context_status`, `assessment_status` and `applicability`. New columns: `domain_step_id`, `closure_digest` and `legacy` (boolean).
- **Reuse eligibility:** `execution = SUCCEEDED`, `context ≠ INSUFFICIENT`, `legacy = false`. A reuse returns all four axes unchanged.
- **Legacy retention (D3 revised).** `upgrade_schema()` to 9.0.3 does not delete `workflow_cache/*.json`. It:
  1. stores each file in CAS;
  2. registers it with `executor="legacy_9_1_1"`, `legacy=true`, `assessment=NOT_ASSESSED`, and `known_defects` taken from the audit (e.g. `["lexical_verdicts", "unscoped_claims", "status_loss_on_reuse"]` per workflow);
  3. moves the original files to `workflow_cache_legacy/`, leaving them unchanged.
  - Legacy runs are never reuse-eligible.
  - A later 9.2 run for the same workflow, RQ and focus records `parent_run_id = <legacy run>`, so `research workflow diff` shows what was corrected.
  - `research workflow runs --legacy` lists them.
  - `workflow_usage.jsonl` is kept and imported the same way.

#### 4.2 Library store, pinning and dependency closure (5–6 days)

- `open_library(home)` and `pin()` are as in v2. Library IDs are `LIB_`-prefixed.
- **`dependency_closure(pin) -> ClosureManifest`** collects:
  1. objects reachable by outgoing edges (`derived_from`, `depends_on`, `cited_by`, `estimated_with`, `tests`, `measures`, `operationalizes`, `defines`, `supports`, `challenges`);
  2. objects referenced by content-field pins (playbook step refs, reading protocols, journal policies, instruction fragments, validity assessments, known criticisms, failure records);
  3. CAS artifacts referenced anywhere in the closure: full texts, section sidecars, policy snapshots, replication files, expected outputs, fragments.

  Each entry carries its digest, and the manifest has an overall `closure_digest`.
- **`use_in_project(project, pin, token)`** imports the whole closure in **one write transaction**:
  - objects get `origin.kind="LIBRARY_IMPORTED"` plus their pin;
  - internal edges are recreated between the imported copies, which is possible because the IDs are identical;
  - CAS blobs are copied and their digests verified;
  - the manifest is stored as a `KnowledgeItem{kind:"library_import_manifest"}`.
  - Any missing or corrupt dependency aborts the whole import, so nothing is half-imported.
- **`research library verify-closure --project P`** checks that every manifest entry is present and matches its digest, without touching the library.
- **Library unavailable:** `check_updates()` returns `LIBRARY_UNAVAILABLE` (not an error), and every project operation still works.

#### 4.3 Lifecycle and promotion (4–5 days)

| Operation | Command | Code-enforced conditions |
|---|---|---|
| Acquire | `research library acquire --from source\|run\|policy\|review\|retrospective` | Creates an item with `source_fidelity=NOT_CHECKED` and `methodological_validity=[]`. |
| Formalize | `research library formalize ITEM --as …` | The schema for that kind validates. |
| Validate | `research library validate ITEM [--context context.json]` | Computes each dimension separately (§4.1). A validity assessment is created **per context**, and starts at `PROVISIONAL` unless the researcher confirms it with a methodological basis. |
| Promote | `research library promote ITEM --scope --exclusions --token-file` | Requires: the owner token; non-empty scope; exclusions (or a recorded "none known"); `source_fidelity ≥ MEANING_VERIFIED` for critical elements; at least one validity assessment for the promoted scope (`PROVISIONAL` or higher, with methodological basis); and **all `HIGH` ECA records resolved**. |
| Reuse | `research library match --project P` | Applies the §4.1 recommendation rule. Each dimension is shown separately, together with known criticisms and failure records. |
| Revise | `research library revise ITEM --restrict\|--extend\|--supersede-by\|--record-failure` | Creates a new revision with history kept. Projects pinned to the old revision get a notice. |

**Project retrospective** is as in v2, but it now reports separately what was verified in each dimension, *within which context*, and which failures or criticisms emerged. Every candidate starts unpromoted.

#### 4.4 Journal Policy Registry storage (1 day)

As in v2.

**Phase 4 tests:**
- v2 tests;
- `test_legacy_cache_registered_not_reusable`;
- `test_legacy_parent_link_and_diff`;
- **`test_project_reproduces_with_library_unavailable`**:
  1. Build a library playbook whose closure includes a full-text Source, section sidecar, passages, ECA records, replication package and expected outputs.
  2. Import it into a project.
  3. In a new process, **rename the library directory**.
  4. Open the project and run `verify-closure`.
  5. Run the domain-engine steps and the replication DAG.
  6. Assert that the outputs reproduce within tolerance and that the library was never accessed.
- `test_closure_import_atomic_on_missing_artifact`;
- `test_check_updates_library_unavailable`;
- `test_promotion_requires_three_dimensions_and_resolved_eca`.

### Phase 5a — Results → Discussion → Contribution loop (3–4 days)

Kept from v2: typed handoffs, upstream passed by run id, the revision proposal, claim-change assessment, the Introduction promise check (direction 2) and the discussion layers.

**v3 changes:**
- Propagation is **per axis** (§4.2).
- `assess_claim_change` reads the 3E **scientific-interpretation** layer (`ClaimEffect`s) and **divergences**, never raw diagnostic signals.
- Every claim-change statement is `HIGH` risk, so ECA applies; the change is `PROVISIONAL` until the researcher confirms it.
- `NOT_SUPPORTED` is never produced from non-significance alone.
- A promise-check change on the Introduction produces a manuscript revision proposal. Each suggestion carries its basis and is never applied automatically.

### Phase 5b — Corpus, verified extraction, replication intake, Playbook builder (6–8 days)

As in v2: `corpus import`, `passage add`, `extract` (now running on 3C.0 protocols), `replication add`, `replication reproduce`, `playbook draft`, `run capture` and `result add`.

**v3 additions:**
- **`replication reproduce`** writes `computational_reproducibility` into the affected playbook elements. That is the only effect: it never changes validity.
- **Licensing.** The researcher supplies papers and replication packages they are entitled to use. They are stored locally only. The corpus never publishes their content.

### Phase 6 — Benchmarks: correctness, method fidelity, cumulative reuse (6–8 days + researcher time)

Kept from v1/v2: the `usage_source` invariant, computed-only metrics, prompt-versus-prompt token comparison, perturbation tests against hard-coded values, and an optional live mode.

#### 6.1 Synthetic logic benchmark (CI)

This benchmark confirms that the software follows the designed logic. It covers:
- ECA cases: correct quote with wrong interpretation, dropped condition, counter-evidence;
- status-axis caps;
- a domain-engine difference between the pilot and contrast specs;
- closure offline reproduction;
- the scope-mismatch non-transfer case.

**It is not evidence of scientific usefulness.**

#### 6.2 Method-fidelity evaluation (Gate K)

1. **Blind reference annotation.** *Before* seeing system output, the researcher (or a designated second reader) annotates the critical elements of the pilot's foundational method paper(s) and replication package. The elements are: label source and timing, sample construction, estimand or prediction target, specification or feature set, standard errors or validation scheme, diagnostics, and interpretation limits. The annotation is stored as `reference_annotation` (CAS, hashed, timestamped before the system run).
2. **Element-by-element comparison** against the system's playbook and fidelity report. Each element is scored `MATCH`, `JUSTIFIED_ADAPTATION` (adaptation recorded with rationale), `PARTIAL` or `MISMATCH`. Disagreements are adjudicated and logged.
3. **Computational check:** the key reported table or metrics are reproduced from the replication package within the declared tolerance.
4. **Validity check:** every validity condition used for the pilot context has an assessment with a methodological basis, and open known criticisms are listed.

#### 6.3 Cumulative reuse evaluation (Gate L): matched, cold versus warm

Comparing Study A with Study B is replaced by a **counterfactual on the same study**:
- **Study B is run twice.** The **cold** run uses an empty library. The **warm** run uses the library after Study A plus its retrospective and promotions. Because it is the same study, difficulty and rubric are identical.
- **Task families**, each with a rubric declared before the runs:
  - **T1 evidence retrieval** (precision and recall against gold passages);
  - **T2 extraction correctness** (field-level against gold annotation);
  - **T3 method selection** (choice and conditions against a gold justified set);
  - **T4 fidelity** (6.2 scoring);
  - **T5 inference errors** (count of overgeneralisations, dropped conditions and unsupported claims, judged blind by the researcher and/or through ECA outcomes).
- **Effort metrics:** manual steps, commands, model calls, tokens (with basis) and elapsed time.
- **Study C** is in the same subfield but with an incompatible scope (legal regime, label source or outcome measure). It measures false transfer.
- **Real-world requirement.** Study A **must** use at least one **real published paper with its real replication code** in the pilot subfield, chosen by the researcher (D14). Study B is preferably real too: the researcher's own follow-up study, or a second real paper with code. The synthetic version of A/B/C runs in CI, and the real version is recorded as an evaluation artefact.

### Phase 7 — Publish domain workflow versions and the knowledge update procedure (3 days)

As in v2 (`library export/import`, `docs/KNOWLEDGE_UPDATE_PROCEDURE.md`, generated acceptance report, 9.2.0, migration notes). v3 additions:
- The published version notes must state each dimension's status per context.
- The migration notes must document the legacy-history import and the four status axes.

---

## 7. Execution order, milestones, effort

**Milestone rule (new).** Each milestone lists the **integration tests** that must pass. A milestone cannot be reported complete on unit tests or on stubs of another module's output. The generated acceptance report (Phase 7 tooling, introduced as a script in Phase 0) checks this list.

```
M1 Trustworthy core           : 0 → 1 → 2 → 3A.1 ∥ 4.1
M2 Knowledge infrastructure   : 3C.0 → 5b.corpus/passage/extract → 3A.2 (ECA) ; 4.2 → 4.3 → 4.4
M3 Pilot domain end-to-end    : 3C ; 3B (integration after 3C) → 3D (+5b.replication, playbook) → 3E → 5a
M4 Evidence of value          : 6.1 → 6.2 (Gate K, real pilot) → 6.3 (Gate L, cold/warm + C) → 7
```

| Milestone | Phases | Effort (dev-days) | Required integration tests (exit criterion) |
|---|---|---|---|
| M1 | 0, 1, 2, 3A.1, 4.1 | 20–24 | All audit xfails flipped; `test_execution_success_does_not_imply_verified`; one scripted model run per generic step; `test_domain_workflows_differ_in_execution_not_prompt`; `test_legacy_cache_registered_not_reusable` |
| M2 | 3C.0, 5b (corpus/extract), 3A.2, 4.2–4.4 | 20–25 | Extraction on real 3C.0 protocols with quote verification; `test_correct_quote_wrong_interpretation_detected_or_unverified`; `test_project_reproduces_with_library_unavailable`; `test_promotion_requires_three_dimensions_and_resolved_eca` |
| M3 | 3C, 3B, 3D, 3E, 5a, 5b (replication/playbook) | 30–38 | `test_3b_contribution_comparison_integrates_3c_output`; `test_extracted_and_reproduced_playbook_not_valid_by_default`; `test_diagnostic_concern_never_blocks_or_passes`; full pilot chain RQ → Introduction → literature → playbook → plan → DAG → three-layer results → claim change → promise check, on the synthetic pilot fixture |
| M4 | 6, 7 | 9–12 dev + researcher annotation time | Gate K on the **real** pilot paper and code; Gate L cold/warm on matched task families + Study C; generated acceptance report |
| **Total** | | **≈ 79–99 dev-days** | Researcher time for literature selection, reference annotation, ECA confirmation and the real pilot is **not** included and should be planned separately. |

**Pilot policy.** Accounting archival / financial-reporting fraud detection is the first pilot (D7). It exercises label provenance, sample construction, measurement, temporal validation, model comparison, assumption-sensitive results and method transfer. It is **not** the default template. `LIB_DW_GENERIC` remains the default, and education and conceptual workflows will be built from their own studies through the lifecycle. The education spec in 3A.1 exists only to prove structural difference.

---

## 8. Acceptance gates

| Gate | Criterion | Proving tests / evidence |
|---|---|---|
| A | Deep Agents patterns adapted (progressive disclosure across stage, domain and playbook fragments; offload and paging; isolated runs; enforced budget) | v1/v2 Gate A tests |
| B | Domain-aware workflows perform meaningful scientific tasks **and differ in execution** | 3B–3E suites; `test_domain_workflows_differ_in_execution_not_prompt`; pilot chain |
| C | SKILL.md and domain/playbook fragments participate in model execution and caching | B1, E1, fragment-invalidation test |
| D | Context completeness and budgets enforced | v1/v2 D tests; context-axis tests |
| E | No keyword-based inference; author-asserted ≠ verified; precedent ≠ requirement; **semantic claim–evidence adjudication** | review-flag fuzz; I11/I12 tests; `test_correct_quote_wrong_interpretation_detected_or_unverified` |
| F | Cache reuse and invalidation correct; all four axes preserved; legacy never reused | Phase 4.1 tests |
| G | Provenance and persistence, **including offline reproducibility from the imported closure** | `test_project_reproduces_with_library_unavailable`, closure atomicity |
| H | Honest token and benchmark measurement | Phase 6 invariants |
| I | Cross-workflow reasoning with claim change and promise check, per-axis propagation | Phase 5a tests |
| J | Research Core compatibility | existing suite + upgrade test on 3.10–3.12 and the tiktoken leg |
| **K** | **Scientific method fidelity: content correctness, not only report completeness.** (K1) fidelity report complete for every plan step. (K2) On the **real** pilot, every critical element scores `MATCH` or `JUSTIFIED_ADAPTATION` against the **blind reference annotation**, with **zero unflagged `MISMATCH`**. (K3) Key results reproduced from the replication package within tolerance. (K4) No playbook element is `SUPPORTED_WITHIN_SCOPE` without a methodological basis, and open criticisms are listed. | 6.2 artefacts; `test_extracted_and_reproduced_playbook_not_valid_by_default`; `test_validity_requires_methodological_basis_not_only_application` |
| **L** | **Cumulative research productivity on matched tasks.** For Study B, warm vs cold: (L1) effort is lower on the task families where library items were reused; (L2) quality is no worse on **every** task family T1–T4, within a tolerance declared in advance; (L3) T5 inference errors warm ≤ cold; (L4) Study C false transfer = 0, with each excluded item shown with its reason; (L5) at least one real paper with real code is in Study A. | 6.3 artefacts (synthetic in CI, real recorded) |

**Release criterion for 9.2.0:**
- all gates A–L pass in the generated acceptance report;
- the pilot domain workflow has `source_fidelity ≥ MEANING_VERIFIED` and `computational_reproducibility = REPRODUCED_WITHIN_TOLERANCE` for its critical elements, plus at least one researcher-confirmed `validity_assessment` for the pilot context.

Nothing is claimed beyond that context.

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| Researcher time becomes the bottleneck (reference annotation, ECA confirmations, validity assessments). | Risk tiering limits confirmations to `HIGH` statements; the adjudication queue is batched; the effort is planned separately from development (§7). |
| ECA model errors. | The deterministic cue guard; model-only adjudication is capped at `PROVISIONAL`; researcher confirmation for `HIGH`; tests that include a deliberately wrong adjudicator. |
| Flawed methods accumulate. | Three-dimensional validity; per-context assessments; `known_criticisms`; `failure_record`; the recommendation rule never treats usage as validity. |
| The domain-engine predicate language grows into a programming language. | A closed predicate set with unit tests; new predicates need a plan amendment; complex logic goes into steps marked `decide`, which require a researcher decision. |
| Closure imports bloat projects. | Content-addressed dedup in CAS; the closure is computed per pin; `verify-closure` reports size. |
| Legacy history is mistaken for current output. | `legacy=true`, `assessment=NOT_ASSESSED`, never reuse-eligible, `known_defects` shown at display time. |
| Real pilot licensing. | The researcher supplies material they are entitled to use; it is stored locally; nothing is published. |
| Content curation, model extraction errors, false generalisation, journal staleness, R execution, systematic-review scope creep, snapshot/proposal paths | As in v2 §9. |

---

## 10. Decisions

| # | Decision | Status / recommendation |
|---|---|---|
| D1 | Optional live provider | Anthropic adapter behind `[model]`; model id configurable; no default network calls. |
| D2 | `workflow_runs` writes | Unauthenticated, derived, append-only; library promotion and ECA confirmation require the owner token. |
| **D3 (revised)** | Old `workflow_cache` JSON | **Keep as legacy/unverified history**: CAS + `workflow_runs(legacy=true)` + `workflow_cache_legacy/`; never reused; linked to correcting runs. |
| D4 | PR #3 | Draft; stack on it; close on merge. |
| D5 | PDF ingestion | Optional `[pdf]` extra. |
| D6 | Default alpha or SESOI | None; declared in the plan with a basis. |
| **D7 (decided)** | First pilot | **Accounting archival / financial-reporting fraud detection** (first pilot only, not the default template). |
| D8 | Library location | `<home>/LIBRARY`, one per researcher. |
| D9 | Shared corpus | In the library; projects import closures. |
| D10 | Seed content | Structure only; no developer- or model-authored content marked as verified. |
| D11 | R support | Optional; requires `Rscript` on PATH. |
| D12 | Systematic review depth | Protocol + screening log + PRISMA flow counts + checklist export. |
| **D13 (new)** | Who prepares the blind reference annotation for Gate K | Recommended: the researcher, before the system run, timestamped in CAS; optionally a second reader for the critical elements. |
| **D14 (new)** | The real pilot paper(s) and replication package | Owner to select a published fraud-detection study with publicly available code and data access the researcher is entitled to use; ideally a second real study for warm/cold Study B. |
| **D15 (new)** | ECA sampling rate for `STANDARD` statements | Recommended 20% plus on-demand; `HIGH` is always 100%. |

---

## Appendix A — Owner's review of v2 and where v3 addresses it

| Owner's point | v3 location |
|---|---|
| Issue 1: source verification ≠ scientific validation; split source fidelity, computational reproducibility and methodological validity; ADOPTED ≠ scientifically endorsed | §4.1, I15, 3D, 4.3, Gate K (K4) |
| Issue 2: Evidence–Claim Adjudication (right source, actual meaning, semantic equivalence, dropped conditions, challenging sources), prioritised by impact; separate `model_generated` from `source_passage` | 3A.2, I9 (revised), I16, Phase 1 correction, §4 vocabularies |
| Issue 3: DomainWorkflow must differ in execution (sequence, evidence, entry conditions, checks, loop-back, non-applicable steps); the pilot must prove it | 3A.1 execution contract and engine, I18, `test_domain_workflows_differ_in_execution_not_prompt`, Gate B |
| Issue 4: Results must separate execution checks, statistical diagnostics and scientific interpretation; no boolean science | 3E three layers, I17 |
| Issue 5: status inconsistency (`NOT_APPLICABLE` absent from enum); split execution / context / assessment / applicability | §4.2, Phase 0.3, all phases |
| Issue 6: Library → project dependency closure; reproducible with the library offline; keep legacy caches | 4.2, 4.1 legacy, D3, Gate G |
| Ordering: reading protocols before M2 extraction acceptance; 3B integration gated on 3C output | 3C.0 in M2; 3B integration rule; milestone rule in §7 |
| Gate K: completeness ≠ correctness; check critical elements against the method paper and replication package | Gate K (K1–K4), 6.2 blind reference annotation |
| Gate L: B ≥ A not comparable; matched tasks with clear rubrics; at least one real paper with code | Gate L (L1–L5), 6.3 cold/warm counterfactual, D14 |
| Pilot: accounting archival / fraud detection, first pilot only, not the default | D7, §7 pilot policy, 3A.1 |

## Appendix B — Changes across versions

| Item | v1 | v2 | v3 |
|---|---|---|---|
| Status | single enum | single enum + `NOT_APPLICABLE` (inconsistent) | four axes with cap rules |
| Knowledge validation | — | linear lifecycle ending in ADOPTED | three dimensions + lifecycle + usage records + negative knowledge |
| Provenance | `Basis` | `basis.kind` mixes generator and evidence | `generated_by` separate from `evidence_basis`; ECA |
| Domain workflow | discipline profile | stage instructions + protocols + playbook links | executable contract with predicates, branching and loop-back |
| Results checks | metadata | `expected_checks` (boolean) | execution checks / statistical diagnostics / scientific interpretation |
| Library import | — | pinned object copy | atomic dependency closure, offline-reproducible |
| Old caches | delete | delete | legacy/unverified history, linked to corrections |
| Gate K | — | report completeness 100% | blind reference annotation on a real pilot + reproduction + validity basis |
| Gate L | — | B ≥ A accuracy | cold/warm on the same study, matched task families, real paper + code |
| Effort | 22–26 days | 60–76 days | 79–99 days (+ researcher time) |

## Appendix C — Audit defect traceability

| Audit defect | Phase |
|---|---|
| P0-1 adapter call broken | 1 |
| P0-2 model output accepted as SUCCESS | 1, 0.3 (axes) |
| P0-3 keyword verdicts | 2, 3A.1 |
| P0-4 competitor Sources invisible | 2 |
| P0-5 cross-RQ claim leakage | 2 |
| P0-6 incomplete reused | 4.1 (axes preserved) |
| P0-7 Methods template | 3A.1, 3D |
| P0-8 self-attested verification | 3A.1, 3E |
| P1-1 no run registry | 4.1 |
| P1-2 budget not enforced | 2 |
| P1-3 usage mislabelled | 1, 6 |
| P1-4 fingerprint instability | 4.1 |
| P1-5 hard-coded benchmark | 6 |
| P1-6 full text unread / empty run reported as SUCCESS | 2, 3A.1, 3C |
| P1-7 no output contract sent to the model | 1 |
| P1-8 author summaries listed as supported | 3E |
| P2-1 to P2-5 | 0, 5b, 3A.1, 3E, 7 |
