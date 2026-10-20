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
