# Domain workflows (9.2, Phase 3A.1b)

A domain workflow is an **execution contract**, not a bundle of prompts. Two workflows may share word-for-word stage
instructions and still behave differently on the same project, because they differ in *what can run, in what order,
under what conditions, and where results send the work back*. The engine (`workflows/domain_engine.py`) is a
deterministic function of the spec and the project's recorded facts.

## A spec

| Part | Meaning |
|---|---|
| `stages` | for each of `contribution`, `literature`, `methods`, `results`, `discussion`: `applicable` (bool or predicate; `false` requires a `justification`), `entry_conditions`, and `instructions` (a fragment added to the model's system prompt) |
| `steps` | `id`, `stage`, `kind` (`assess` \| `decide` \| `plan` \| `execute` \| `review`), `entry_conditions`, `evidence_requirements` (`{type, where, min, about}`), `applicability` (`{when, justification}`), `on_condition` (`{when, goto, reason}`) |
| `study_designs` | the designs the workflow covers; empty = any. A recorded design outside the list makes every stage `NOT_APPLICABLE`; an unrecorded design claims nothing either way |
| `scope`, `exclusions`, `validation_status` | where it is known to apply; new content starts `EXTRACTED` |

Conditions use a **closed predicate language** (no code, no `eval`). Anything else is rejected when the spec is loaded:
`exists`, `count_ge`, `field_in`, `study_design_in`, `step_execution`, `step_assessment`, `diagnostic_signal`,
`scope_matches`, `all`, `any`, `not`. A predicate that names an unknown step, or a `goto` to one, is rejected too.

## What the engine reports

For every step: whether it applies (and why not), which entry conditions and evidence requirements are unmet (in words),
whether it is done, whether it is **reopened** by a loop-back rule, and whether it is runnable now.
`research domain next --project P --spec LIB_DW_ACC_FRAUD_DETECTION` prints this; `--json` gives it structured.

A loop-back rule (`on_condition`) fires while its evidence is **newer than the last run of the step it sends work to**: once
that step has been redone the loop is closed, and a new concerning diagnostic reopens it again. Every run of a step stays in
the registry (`parent_run_id`), so each return to a step is part of the history.

## In `run_workflow`

`run_workflow(..., domain_workflow=spec_or_id)` (default: the research question's `domain_workflow` field, else the generic
workflow) decides, **before any reuse**, whether the stage runs:

* stage not applicable → status `NOT_APPLICABLE` (`execution=NOT_RUN`, `applicability=NOT_APPLICABLE`, with the spec's
  justification): not run and not an error;
* entry conditions unmet → status `BLOCKED` (`entry_conditions_unmet`), listing what is unmet;
* otherwise it runs; the stage's instruction fragment is part of the system prompt and the stage's digest is part of the
  cache fingerprint (editing another stage's rules does not invalidate this one).

Both gate outcomes are recorded in the run registry with the spec's pin (`ID@version#digest`) and are never reused. A stage run
of a stage with a single `assess` step completes that step (`domain_step_id`).

## Purpose, and contracts for non-empirical inquiry (WP6.1)

Design does not say why a study is done, and a landscape of the literature reads papers of every design for any project. A spec may therefore also list `research_purposes` (vocabulary `research_purpose`; empty = any);
a recorded purpose outside the list makes every stage `NOT_APPLICABLE`, like a design outside `study_designs`. The predicate `research_purpose_in` joins the closed language, and `ProjectFacts` carries the purpose
(`research_purpose` on the ResearchQuestion, `domain next --research-purpose`, or the inquiry recorded on a ResearchProblem: `domain next --problem PROB_x`; a question promoted from a problem takes the problem's recorded inquiry for the gate of `run_workflow`; a ResearchProblem may record several purposes and a spec applies if one of them is covered). A spec that does not use purposes has exactly the same dict and digest as before, so every stored pin stays valid.

### Activity, stage and manuscript section are three different things (remediation, 2026-10-22)

* The **scientific activity** is what researchers actually do: search, screen, reconstruct, interpret, build an argument, estimate.
* A **stage** (`contribution`, `literature`, `methods`, `results`, `discussion`) is a capability group of the suite that supports an activity. It is not a manuscript section. A contract says in `activity` what the stage
  means for this kind of inquiry and, in `manuscript`, how a paper typically presents it; the second is descriptive only, and no contract requires a heading called Methods or Results.
* A **manuscript section** is the author's and the genre's choice.

A stage is `NOT_APPLICABLE` only where the function it names is not part of the inquiry at all. Not estimating anything is not having no method, and a qualitative theme or a conceptual proposition is a finding. The three
contracts below therefore apply every stage and define what each carries. (`NOT_APPLICABLE` still appears for a step whose function the design lacks, with the reason: an appraisal the adopted protocol says is not performed;
design principles outside design-based research; any stage of a contract whose declared designs or purposes do not include the project's.)

A stage with `assessed_by: steps` is reported by `run_workflow` as the state of its steps under the contract (`workflows/skills/contract_stage.py`): no empirical skill is applied, so a complete review is not read as
"insufficient evidence" for lack of coefficients. Status: `SUCCESS` (every applicable step done and none only asserted), `REQUIRES_VERIFICATION` (done, but some only asserted), `INSUFFICIENT_EVIDENCE` (a step is not done).

### How a step is done: three kinds, kept apart

| Completion | Means |
|---|---|
| `EXECUTED_BY_CAPABILITY` | an object that only a capability writes exists (`completed_by` a RetrievalRun, a Landscape): the suite did the work |
| `DECLARED_IN_RECORD` | a structured record of the researcher exists and passed the suite's completeness checks (an adopted review protocol component; a Result of kind theme, proposition, conceptual_map; a reviewed inference; a completed inspection) |
| `RECORDED_BY_RESEARCHER` | `record_step_run`: an assertion that the work was done; **nothing was checked** |

A step with a `completed_by` predicate is done when its predicate holds, whatever else was recorded, and is labelled by that stronger kind; a step whose work leaves no object (a researcher's reading, a credibility
judgement) can only be asserted, and says so. A step done before its entry conditions were met is reported `out_of_order`. `domain next` shows the kind next to each done step.

### Built-in contracts for inquiry that is not an estimation pipeline

All `cross_domain`, `EXTRACTED` (order and conditions, no method content, not yet checked against how researchers in the tradition work), independent of discipline.

| Spec | Method (`methods`) | Findings (`results`) | Loop-backs |
|---|---|---|---|
| `LIB_DW_CRITICAL_SYNTHESIS` (designs conceptual, narrative_review, systematic_review; purposes critical_synthesis, theory_development) | search strategy → screening and selection → appraisal (when the protocol performs one) → extraction protocol → synthesis protocol: each a component of the review protocol (`docs/REVIEW_PROTOCOL.md`) that the researcher adopted. `literature`: reconstruct theories and constructs/assumptions (a landscape, executed by a capability) → compare theories and scopes | decide which proposed tensions stand → inspect whether the literature already addresses each (inspection record) → the interpretive synthesis (a Result of kind conceptual_map, tension or interpretive_synthesis) | an inspected paper already addresses the tension → back to identifying tensions |
| `LIB_DW_CONCEPTUAL_ARGUMENT` (design conceptual) | construct and reconstruct the argument: reconstruct the inference chain → treat rival explanations | the conceptual products: propositions, argument conclusions, framework (conceptual Claims or Results of kind proposition, framework, argument_conclusion) → boundary conditions | an unanswered rival → back to the inference chain |
| `LIB_DW_QUALITATIVE_INQUIRY` (designs qualitative_interview, case_study, design_based) | sampling rationale → data generation → coding and interpretation → alternative interpretations → credibility checks including reflexivity | findings: themes, patterns, interpretations, explanations (Results of those kinds) → a check that each traces to the material → design principles (design-based only) | interpretation still moving → back to data generation |

None has an estimator, an identification or a robustness step; the regression workflow is not applied to them. `LIB_DW_ACC_FRAUD_DETECTION` is unchanged (same pin) and still blocks a predictive project until its label,
sample and validation evidence are recorded. `domain suggest` lists which contracts fit the recorded design and purposes and chooses none; the generic contract is never offered as the appropriate one. The facts the engine uses
come from the question, or, for a question promoted from a ResearchProblem, from the problem at the revision it was promoted from (`problem update-question` is the explicit act that brings it up to date). The review
protocol is read at the problem's head, since it is the working record of the method. `workflows/profiles/*` (accounting, finance, education) remain checklists for the quantitative traditions they name; the education
profile is policy evaluation, VAM and IRT, not a statement of what education research requires.

## Recording steps

`record_step_run(store, spec, step_id, execution=..., assessment=..., diagnostic_signals={name: signal})` registers that a step
ran (diagnostic signals use the `diagnostic_signal` vocabulary). Execution plans (Phase 3E) will call it for each executed step.

## Built in

* `LIB_DW_GENERIC`: the five stages in any order, one assessment step each, no conditions. The default.
* `LIB_DW_ACC_FRAUD_DETECTION`: the **pilot skeleton** for archival financial-reporting fraud detection: label provenance and
  timing, sample construction, feature construction with an information-timing rule, temporal validation, evaluation under class
  imbalance, model comparison, then a leakage check that sends work back to feature construction or label provenance, and a
  sensitivity analysis. It fixes the *order and conditions* of work and carries **no method content**; content arrives through the
  knowledge lifecycle. Status `EXTRACTED`: nothing in it is verified.

`tests/fixtures/dw_edu_qualitative_fixture.json` is a test-only qualitative-education contrast (no separate results stage, a
saturation loop-back to data collection). It exists to prove structural difference and is not an education workflow.

## Static discipline profiles

`workflows/profiles/{accounting,finance,education}.py` are checklists written for one tradition and the data sources it lists;
they never defined a field. They keep working as before (the Methods stage still shows their checklist, with every item
`REQUIRES_INFORMATION`), and can be migrated into scoped knowledge:

```
python scripts/migrate_profiles.py accounting --out operations.json    # nothing is written to any project
```

Each checklist item and validity question becomes a `KnowledgeItem` of kind `checklist` whose **scope is exactly what the profile
states** (its domain and its own list of preferred data sources; no jurisdiction or period is inferred), with an explicit
exclusion ("not established outside the data sources this profile lists"), `generated_by: import`, no evidence basis, and
`source_fidelity: NOT_CHECKED`. They cannot be promoted into a shared library until they are given evidence. Wrap the operations in
a proposal with `build_proposal(project_id, snapshot, proposal_id, operations)` and import them through the normal gateway.

The six 9.2 object types (`DomainWorkflow`, `MethodPlaybook`, `JournalPolicy`, `KnowledgeItem`, `ContributionArgument`,
`AnalysisPlan`) are registered after the existing ones, are proposable, and have content schemas; the existing types and their
order are unchanged. Content keys are screened by the gateway: `role`, `actor`, `verified`, `revision`, `timestamp`, `token`
and similar are server-assigned and rejected anywhere in a proposal (hence `Dataset.data_role`).
