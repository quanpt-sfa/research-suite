# M5 design: ResearchTask, ResearchProblem, data model, migrations, API/CLI

Status: **proposal**. Nothing here is implemented. Part of `PLAN_M5_TASKS_AND_DISCOVERY.md`. Code references point at `main` `c6397de`.

## 1. Principles

* **No parallel store.** Tasks, problems, candidates and decisions are objects in the existing project `StateStore` (`state/store.py`), with revisions,
  sealed edges, audit rows and the state hash chain. Run linkage uses the existing `workflow_runs` table with two nullable columns.
* **A task orchestrates; it does not decide.** A task plan is a list of steps that call existing capabilities. Every step that is a scientific
  judgement is a `decide` step and halts the run until the researcher acts (I14, I3).
* **Goals are data.** A goal maps to a planner: a pure function `plan(goal_spec, project_facts, library_matches) -> steps` plus a JSON spec. No agent loop.

## 2. Objects

### 2.1 `ResearchTask` (new object type)

```jsonc
{
  "label": "Discover a contribution: strategic adaptation of fraud after detection",
  "goal": "discover_contribution",              // vocabulary task_goal
  "goal_version": "1",
  "domain_workflow": "LIB_DW_ACC_FRAUD_DETECTION@3#…",   // pin, or "generic"
  "statement": {"problem_id": "RP_0001", "problem_revision": 2},   // or {"research_question_id": ...}
  "parent_task": null,                          // {"task_id", "revision"} when continued from another task
  "continued_from_step": null,
  "plan": {                                     // the plan as approved; re-planning creates a new revision
    "planner": "discover_contribution@1",
    "steps": [ {"step_id": "S01", "capability": "retrieve", "kind": "run", "inputs": {...}, "depends_on": [],
                 "estimate": {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "basis": "deterministic"}},
               {"step_id": "S04", "capability": "frame_problem", "kind": "decide", ...} ]
  },
  "budget": {"max_llm_calls": 120, "max_output_tokens": 2000000, "max_cost": null, "currency": null},
  "state": "PLANNED",                           // state machine, section 3
  "state_reason": null,
  "checkpoints": [ {"step_id": "S04", "decision_id": null} ],
  "outputs": [],                                // object pins produced by the steps
  "generated_by": "planner:discover_contribution@1"
}
```

Edges (existing relations with an `edge_kind` in provenance, see §8): `depends_on{edge_kind: addresses} → ResearchProblem@rev` (or `ResearchQuestion@rev`),
`derived_from{edge_kind: continues} → ResearchTask@rev`, `depends_on{edge_kind: uses} → MethodPlaybook/KnowledgeItem@rev` for library items the planner
chose (with the reason). Step states are **not** stored in the task object; they are derived from `workflow_runs` rows with the task's
`task_id` and the step id (single source of truth, I5). The task object changes only when the plan, the budget or the state changes.

### 2.2 Identity

* `task_id = TASK_<4-digit sequence>` within the project (`knowledge/common.py::next_numbered_id`); library-scoped ids are never used for tasks.
* A **goal revision** (the researcher changes the goal text or scope) and a **statement revision** (the problem is reframed) are revisions of the
  `ResearchProblem` / `ResearchTask`, never new ids, so the history of a line of inquiry stays one chain. Branching an inquiry (two framings pursued)
  creates a second task with `parent_task` set.
* Step identity: `(task_id, step_id, step_fingerprint)` where `step_fingerprint = sha256(capability, capability_version, canonical inputs, input pins,
  skill digest, protocol pin, model descriptor)`. The model descriptor is part of the fingerprint for model steps, so changing the model re-runs.

### 2.3 `ResearchProblem` (new object type, decision D16)

```jsonc
{
  "label": "Strategic adaptation of reporting fraud after detection models are deployed",
  "kind": "phenomenon",            // phenomenon | puzzle | disagreement | unexpected_result | practical_problem | method_gap
  "statement": "…researcher's words…",
  "scope": {"domain": "accounting", "population": "US listed firms", "period": null, "unit_of_analysis": "firm-year"},
  "exclusions": ["non-US"],
  "interpretations": [             // 5.1 of the discovery protocol: several framings kept side by side
    {"id": "I1", "text": "…", "generated_by": "researcher"},
    {"id": "I2", "text": "…", "generated_by": "model:deepseek-v4-pro", "status": "PROPOSED"}
  ],
  "chosen_interpretations": ["I1"], // set only by a researcher decision
  "status": "OPEN",                // OPEN | FRAMED | PROMOTED_TO_RQ | SET_ASIDE
  "generated_by": "researcher"
}
```

**Path to a question that keeps history.** `problem promote-to-rq RP_0001 --interpretation I1 --text "…"` creates a `ResearchQuestion` with an edge
`derived_from → ResearchProblem@rev` and a new problem revision with `status: PROMOTED_TO_RQ` and `promoted_to: RQ_…`. The problem is never deleted
or rewritten; later reframing creates new problem revisions, and the RQ's `derived_from` edge keeps pointing at the revision it came from
(`graph/dependency.py::stale_pinned_edges` reports it when the problem moves on).

**D16 options compared**

| Option | For | Against |
|---|---|---|
| A. New `ResearchProblem` type (recommended) | a problem may have several framings, no answerable question yet, may be a disagreement or an unexpected result; dispatcher and `context.py` keep treating `ResearchQuestion` as answerable (`context.py::build_context` refuses a non-RQ id) | one more type in schema and integrity checks |
| B. `ResearchQuestion` with `status: exploratory` | no new type | every RQ consumer must learn to ignore exploratory RQs; `facts_from_store` (`domain_engine.py`) would treat a problem as a question and open stages that should stay closed; framings do not fit `text` |

## 3. State machine

```
            plan                approve                run
  DRAFT ─────────► PLANNED ───────────► APPROVED ───────────► RUNNING ──┬──► WAITING_FOR_RESEARCHER ──(decision)──► RUNNING
                     ▲ │                                         │      ├──► BLOCKED (missing input / budget / upstream failed)
           replan    │ │ revise                                  │      ├──► FAILED (step failed, not retried)
                     │ ▼                                         │      └──► COMPLETED
                  PLANNED (new revision)                         │
  any non-terminal ──cancel──► CANCELLED        COMPLETED/CANCELLED ──continue──► new task (parent_task)
  RUNNING ─(upstream object revised)─► STALE ──replan──► PLANNED
```

| Transition | Who | Guard |
|---|---|---|
| DRAFT→PLANNED | planner (engine) | goal known, statement exists |
| PLANNED→APPROVED | researcher (`author`/`owner`) | cost estimate shown; budget set or explicitly unlimited |
| APPROVED/RUNNING→RUNNING | engine | next step's `depends_on` all SUCCEEDED, its entry conditions true (`domain_engine.evaluate_predicate`) |
| RUNNING→WAITING_FOR_RESEARCHER | engine | next step is `decide` |
| WAITING→RUNNING | researcher decision recorded | decision object exists and is bound to the current revisions of what it decides |
| →BLOCKED | engine | missing input, budget would be exceeded by the *estimate* of the next step, or upstream failure |
| →STALE | invalidation | an input pinned by a completed step got a new revision (`graph/dependency.py::invalidate_dependents` marks dependents) |
| →CANCELLED | researcher | none; runs already registered stay registered |

`COMPLETED`, `CANCELLED`, `FAILED` are terminal for that task revision. `FAILED` can be re-opened only by `task retry STEP`, which creates a new run row;
the failed row stays (I5).

## 4. Retry, resume, cancel, idempotency, concurrency

> **Amended (M5_DESIGN_AMENDMENT_1.md A7, A8.3):** resume relies on a *stage-then-commit* rule: model answers are staged in CAS with an attempt
> manifest, and one commit transaction per unit of work writes objects, edges, run row and audit together; objects carry `produced_by_run`, orphans are
> excluded and reported. Two existing sites violate this today (`run_protocol`, `eca.adjudicate`) and are fixed in WP1. A task lease prevents two runners.

* **Retry**: `task retry TASK STEP` re-runs a FAILED or BLOCKED step. Same fingerprint + previous SUCCEEDED row ⇒ cache hit, no model call
  (`workflow_runs.latest_reusable`). A failed row is never reused (existing rule, `tests/test_workflow_runs.py::test_refused_invalid_and_failed_runs_are_recorded_and_never_reusable`).
* **Resume after a crash**: the state is derived from the registry, so `task run` recomputes the next step. A step that crashed mid-way left no run row
  (the run is registered after the step's transaction commits) or a FAILED row; either way it is re-run. Writes of one step are one transaction
  (pattern `tests/test_concurrency_and_fault.py`, fault injection `Tx.fault`).
* **Cancel**: sets the state; a model call in flight is not interrupted (the adapter is synchronous); its result is registered and the task stays CANCELLED.
* **Idempotency of decisions**: a decision carries `decides: {object_id, revision}`. Recording the same decision twice for the same revision returns the
  first one (`DECISION_EXISTS`); a decision on an outdated revision is refused (`STALE_REVISION`).
* **Optimistic concurrency**: every task write passes `expected_head` (new optional argument of `Tx.add_revision`, see audit B). Two terminals running the
  same task: the second write fails with `STALE_REVISION` and nothing is written; the CLI tells the user to re-read.

## 5. Human checkpoints, re-planning, continuation

* **Checkpoints are in the plan before anything runs** (`task plan` lists them). The goal spec may not omit the mandatory ones (problem framing,
  candidate decision); the researcher may add more (`task revise --add-checkpoint S07`).
* **Re-planning**: `task replan TASK` re-runs the planner with current facts; the result is a new task revision with a diff of steps (added, removed,
  re-parameterized). Steps whose fingerprint is unchanged keep their runs. The researcher approves the new plan (PLANNED→APPROVED again).
* **Continuation**: `task continue --from TASK --goal evaluate_methods --candidate CAND_0003` creates a child task whose statement is the candidate's
  pinned revision. The child reads the parent's outputs by pin; nothing is copied.

## 6. Cache invalidation

A step output is stale when any pinned input changed revision, the protocol/skill digest changed, or the model descriptor changed. The first case is
detected by the graph (outputs have edges to inputs); the other two by fingerprint mismatch at the next `task run`. Stale outputs are **not deleted**:
the step is shown as `STALE (input X rev 3 → 4)` and re-runs only when the researcher approves the re-plan. Retrieval results also record the corpus
digest (sorted list of `(source_id, revision, text_sha256)`); adding a paper makes retrieval stale, not the candidates directly; the candidates become
stale only if re-retrieval returns a different set (selective invalidation, section 9 of the main plan).

## 7. Token budget and cost

> **Amended (A8):** the estimate check below is a planning aid only. Enforcement is per call: input tokens counted with an upper bound, output capped at
> what the budget still allows (`max_output_tokens`), unreported usage charged at the full reservation, scheduling stops at 90%.

* Each planned step has an **estimate** with its basis: `deterministic` (0 calls), `from_registry` (median of past runs of the same capability and protocol
  in this project or library, `workflow_runs.usage`), or `heuristic` (tokens of the input text via `context.py::_tokens` + protocol output cap).
* The budget is checked **before** each model step against `spent + estimate(next)`. Exceeding it → BLOCKED with the numbers; the researcher raises the
  budget or narrows the step. Cost in money is computed only if the researcher supplies a price table (`--prices prices.json`); otherwise tokens only.
* Actual usage comes from the run rows (`usage_source` kept: `provider_reported`, `estimated`, `not_available`).

## 8. Schemas and vocabularies (additions)

| Addition | Where | Notes |
|---|---|---|
| `ResearchTask`, `ResearchProblem`, `ContributionCandidate`, `ResearchDecision`, `RetrievalRun` types | `protocols/schema.py::_TYPE_PROPS` | JSON Schema like existing types; `schema_version` bump |
| vocabularies `task_goal`, `task_state`, `problem_kind`, `candidate_type`, `challenge_status`, `decision_action`, `feasibility_status` | new `taxonomy/discovery.json` | validated with `taxonomy.validate_term` |
| **no new relations.** The `edges` table has a SQL `CHECK(relation IN (…))` built from `RELATIONS` (`state/schema.py:72,156`); SQLite cannot alter a CHECK, so a new relation would mean rebuilding `edges` in every existing store. Instead: `depends_on` (task → problem, step output → input, decision → decided object) and `derived_from` (RQ → problem, child task → parent, candidate → landscape entries), both existing `ANY→ANY`, dependent = source, invalidating; the edge provenance carries `edge_kind` (`addresses`, `continues`, `decides`, `retrieved_for`, `rests_on`) — not `role`, which the proposal gateway forbids. `ContributionCandidate` is added to `_HYPOTHESIS` (a Python type check, not SQL) so existing `supports` / `challenges` edges apply | `graph/relations.py::_HYPOTHESIS`; readers filter on `provenance.edge_kind` | `graph/dependency.py` propagates through `depends_on`/`derived_from` unchanged; `challenges` stays non-invalidating as declared today |
| knowledge kinds (section 6 of main plan) | `taxonomy/knowledge.json` + `lifecycle._KIND_RULES` | formalize rules per kind |

`ContributionCandidate` vs `KnowledgeItem{kind: contribution_candidate}`: the earlier draft stored candidates as KnowledgeItems. Revised proposal:
candidates live in the **project** as `ContributionCandidate` objects (they are hypotheses of this project, not knowledge); the retrospective may
propose an **idea-history** KnowledgeItem *about* a candidate for the library (section 6). This keeps "hypothesis" and "knowledge" apart (spec 6).

## 9. Migrations and compatibility

| Migration | Kind | Reversible | Test |
|---|---|---|---|
| M5-1 `workflow_runs` add `task_id TEXT NULL`, `task_step_id TEXT NULL`, index | additive `ALTER TABLE` | yes (columns ignored by old code) | open a 9.2-M4 store, run migration, old rows NULL, `compute_state_digest` unchanged for objects |
| M5-2 schema version bump with new types | additive | yes | a store without M5 objects validates under both versions |
| M5-3 vocabularies (no relation changes, so the `edges` CHECK constraint is untouched) | additive | yes | `tests/test_phase0_status_taxonomy_provenance.py` extended; test that an M4 store's `edges` DDL is unchanged after migration |
| M5-4 `Tx.add_revision(expected_head=None)` | API, default keeps behaviour | yes | all existing tests unchanged |

No existing object is rewritten. `research doctor`/integrity (`validation/integrity.py`) learns the new types. A store written by M5 opened by M4 code:
new types are unknown and refused by M4 integrity checks; documented in `docs/MIGRATION_9_1_TO_9_2.md` as forward-only.

## 10. API and CLI (proposed)

Python (new modules): `research_harness/tasks/{model,planner,runner,goals}.py`, `research_harness/discovery/{problem,retrieval,landscape,candidates,adversarial,feasibility,decisions}.py`,
`research_harness/export/{manifest,render_md,render_csv,contracts}.py`.

```
research problem new      --project P --kind phenomenon --statement-file problem.md [--scope scope.json]
research problem frame    RP --propose            # model proposes interpretations (PROPOSED), never chooses
research problem choose   RP --interpretations I1,I3 --rationale "…" --token-file T
research problem promote-to-rq RP --interpretation I1 --text "…" --token-file T

research task start       --project P --goal discover_contribution --problem RP [--domain PIN] [--budget-calls 120]
research task plan        TASK [--json]           # steps, inputs, outputs, checkpoints, estimate with basis
research task approve     TASK --token-file T
research task revise      TASK --drop S | --add SPEC | --set S.param=v | --add-checkpoint S
research task run         TASK [--until S] [--provider …]   # stops at every decide step
research task status      TASK                    # per step: state from the registry, cost so far
research task retry       TASK S
research task cancel      TASK --reason "…"
research task replan      TASK
research task continue    --from TASK --goal evaluate_methods --candidate CAND
research task export      TASK --out DIR [--include-model-text]   # section 8 of the main plan

research discover decide  CAND --action keep|drop|merge|split|change_mechanism|narrow|read_more|go_to_methods|revisit_when_results --rationale "…" [--with CAND2] --token-file T
research discover sheet   TASK --out decisions.json   # every candidate with what is needed to decide; actions left empty
research discover apply   TASK --file decisions.json --token-file T
```

All inline-JSON options use the fixed `_json_arg` (WP0).
