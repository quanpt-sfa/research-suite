# Research tasks (9.2, M5 WP1b + WP3)

A task turns a goal into a recorded plan that runs capabilities the suite already has and **stops at every decision that is yours**. It needs no
research question, manuscript, methods or results to start. Design: `docs/refactor_9_2/M5_TASKS_AND_DATA_MODEL.md` and Amendments 1–2.

```
research task goals
research task start    --goal evaluate_methods --statement "..." --params '{"sources": ["SRC_X"], "playbook_id": "PB_X", "label": "..."}' --budget-tokens 2000000 ...
research task plan    TASK            # steps, checkpoints (mandatory ones marked), estimates with their basis
research task approve TASK            # you have seen the plan; a budget is set (or --unlimited was stated)
research task run     TASK --model ...   # stops at the first checkpoint, failure or budget limit
research task decide  TASK S01 --rationale "..."    # author or owner; no default; bound to what it decided
research task status|retry|revise|replan|cancel|continue ...
```

## Goals

| Goal | Steps | Mandatory checkpoints |
|---|---|---|
| `review_literature` | confirm sources → literature review (theory, method or contribution) | reading list |
| `evaluate_methods` | confirm sources → method review → draft playbook → adjudicate pending (proposals) → confirm adjudications → readable export | reading list, adjudications |
| `assess_claims` | coverage → adjudicate pending → confirm adjudications → coverage | adjudications |
| `build_landscape` | confirm the retrieval run's reading list → build the landscape (`landscape_build`, model calls) → review inferences and later versions not read → export (`docs/LANDSCAPE.md`) | reading list, landscape review |
| `discover_contribution`, `analyze_results` | **not available yet**: `task goals` says what is missing (`discover_contribution` needs candidate generation and adversarial verification, WP7–WP8) | |

A goal that needs capabilities the suite lacks is refused with the list of what is missing; it is never planned with a stand-in (I19).

## What is stored where

* The task is a `ResearchTask` object in the project store (written by the engine, never proposed): goal, parameters, plan, budget, state, checkpoints
  with the decisions taken, a lease while it runs. Every change is a revision written against the revision the writer read (`STALE_REVISION` otherwise).
* Step state is **derived** from the run registry: each finished step is a `task_step:<capability>` row (`task_id`, `task_step_id`), and every run a
  step makes carries the same ids. Schema 9.0.4 adds these two nullable columns; `research project upgrade-schema` (owner token, on a copy first for
  evaluation evidence) adds them to an older store. Existing rows and the state digest are unchanged.
* A step is reused when its fingerprint is unchanged: capability, inputs, pinned objects (id, revision, digest), upstream steps. A model step is
  reused **only if the model in use is the one that produced it** (model id and configuration); otherwise it is `STALE`, and so is everything
  downstream of it (what dependents rest on is the step's fingerprint plus the model that made it). A stale step is redone only with `--rerun-stale`;
  when it is redone because the model changed, the new model is actually asked (no cached answer of the old model is served under the new name; the
  adjudication step re-proposes every statement you have not confirmed, and your confirmations stand). `run` asks for the model before it decides to
  reuse a model step; without one it is `BLOCKED: ADAPTER_REQUIRED`, unless you pass `--accept-recorded-models`. A **decision** is bound to the
  fingerprint it was taken on: if what it decided changes, it is asked again. A failed run is recorded and never reused.

## States

`PLANNED → APPROVED → RUNNING → WAITING_FOR_RESEARCHER | BLOCKED | FAILED | COMPLETED`; `CANCELLED` from any open state; `revise` and `replan` return to
`PLANNED` (you approve again). `BLOCKED` carries the reason: `ADAPTER_REQUIRED`, `STALE_STEPS`, `BUDGET_EXCEEDED`, `BUDGET_NEAR_LIMIT`, an unavailable input.

## Budget (enforced per call)

Before each model call the input is counted with an upper bound, the answer is capped at what the budget still allows (the adapter's
`max_output_tokens`), and an intent record is written. A call is refused, not made, if it would break the budget; no new step starts at 90% spent.
When a call returns or fails, its **charge is written to the run registry** (`task_call:charged`: tokens as reported, or the reservation when the
provider reported nothing or the call failed) and only then is the intent removed. **The spend of a task is the sum of those charge rows** (plus calls
in flight, for display): it survives a restart, and the runs the calls were made for and the step summaries are never added to it. If a process dies
between a call and its charge, the next `run` records that call as an `unknown_outcome` charge at its reservation.
Limits: an adapter whose output cannot be capped (the scripted double) is reserved a bounded answer and charged what it reports; a provider may bill
a request that finished after a client timeout; money needs a price table and is not capped by tokens; tasks created before this change have no charge
rows and show a spend of 0. Estimates are planning aids with a stated basis, not promises.

## The adjudication checkpoint

It closes only when **every HIGH statement in the task's scope has a researcher confirmation for its current words**. Two different things can
stand in the way and are reported apart: proposals nobody has confirmed (`research adjudicate sheet`, `confirm-batch`) and statements that have no
proposal at all (the run was limited with `limit`, the statement has no passage, nobody ran it: `research adjudicate run-pending`). A statement with no
proposal can be set aside only with a reason of yours, `--exempt OBJECT#ELEMENT=reason`, recorded in the decision.

## Running safely

`run` takes a lease on the task (default 30 minutes). A **heartbeat renews the lease every third of its length while a step executes**, so a step
that takes longer than the lease keeps it and no second runner starts the same step (`TASK_LEASED`). An expired lease is taken over; an owner can
`--break-lease` if the runner is known dead. If a run does lose the task (it was cancelled, revised, replanned or taken over), the check made before
a step's result is registered notices: the step is recorded as `FAILED: lease_lost`, never as a result, the task state is not touched, and the run
ends `LEASE_LOST`. Limit: a runner that stalls without any heartbeat (a suspended process) can have started a model call that its successor also
makes; the fencing prevents a second result, not a second charge.
