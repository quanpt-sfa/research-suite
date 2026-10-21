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
| `discover_contribution`, `analyze_results` | **not available yet**: `task goals` says what is missing | |

A goal that needs capabilities the suite lacks is refused with the list of what is missing; it is never planned with a stand-in (I19).

## What is stored where

* The task is a `ResearchTask` object in the project store (written by the engine, never proposed): goal, parameters, plan, budget, state, checkpoints
  with the decisions taken, a lease while it runs. Every change is a revision written against the revision the writer read (`STALE_REVISION` otherwise).
* Step state is **derived** from the run registry: each finished step is a `task_step:<capability>` row (`task_id`, `task_step_id`), and every run a
  step makes carries the same ids. Schema 9.0.4 adds these two nullable columns; `research project upgrade-schema` (owner token, on a copy first for
  evaluation evidence) adds them to an older store. Existing rows and the state digest are unchanged.
* A step is reused when its fingerprint is unchanged: capability, inputs, pinned objects (id, revision, digest), upstream fingerprints. A changed
  source, playbook or upstream step makes it `STALE`; it is redone only with `--rerun-stale`. A **decision** is bound to the fingerprint it was taken
  on: if what it decided changes, it is asked again. A failed run is recorded and never reused.

## States

`PLANNED → APPROVED → RUNNING → WAITING_FOR_RESEARCHER | BLOCKED | FAILED | COMPLETED`; `CANCELLED` from any open state; `revise` and `replan` return to
`PLANNED` (you approve again). `BLOCKED` carries the reason: `ADAPTER_REQUIRED`, `STALE_STEPS`, `BUDGET_EXCEEDED`, `BUDGET_NEAR_LIMIT`, an unavailable input.

## Budget (enforced per call)

Before each model call the input is counted with an upper bound, the answer is capped at what the budget still allows (the adapter's
`max_output_tokens`), and an intent record is written. A call is refused, not made, if it would break the budget; no new step starts at 90% spent.
Unreported usage is charged at the reservation. If a process dies between a call and its answer, the next `run` records that call as an
`unknown_outcome` run charged at its reservation. Limits: an adapter whose output cannot be capped (the scripted double) is only bounded by what it
reports; a provider may bill a request that finished after a client timeout; money needs a price table and is not capped by tokens. Estimates are
planning aids with a stated basis, not promises.

## Running safely

`run` takes a lease on the task (default 30 minutes, renewed between steps). A second runner gets `TASK_LEASED`; an expired lease is taken over; an owner
can `--break-lease` if the runner is known dead. A cancel, revise or replan made while a run is in progress stops that run at the next step.
