# M5 backlog (items found during WP3 review, not in the scope of WP4–WP5)

Each item: what is wrong or untested today, where, how to show it, and what a fix has to do. None is fixed by WP4 or WP5.

## B1. Partial provider usage

* **Where:** `research_harness/tasks/budget.py`, `BudgetedAdapter._persist_charge`.
* **Today:** `reported = tin is not None or tout is not None`. If a provider reports only one side (for instance output tokens without input tokens, or the
  reverse), the call is charged as reported: the missing side counts as 0. The call is under-charged, and the budget can be exceeded without any block.
  `usage_source` says `provider_reported` even though half of the figure is missing.
* **How to show:** a fake adapter returning `SimpleNamespace(input_tokens=None, output_tokens=120)`; the charge row has `charged_tokens == 120`, while the
  reservation was several thousand. (Today only the all-missing case is tested: `test_tokens_charged_for_a_provider_that_reports_no_usage_survive_a_restart`.)
* **Fix has to:** charge a missing side at its reservation (`input_reserved` or `output_reserved`), record `usage_source: "partial"` with which side was
  reserved, and add tests for input-only, output-only, zero, negative and non-integer values (a provider returning a float or a string).
* **Also check:** cache-read and reasoning tokens (`TaskUsage.cache_read_tokens`) are not part of the charge today; decide whether reasoning tokens that a
  provider reports separately must be added.

## B2. Fencing at the result-write boundary

* **Where:** `research_harness/tasks/engine.py`, `_run_loop`: `run.still_mine()` is evaluated, then the step row is written by `workflow_runs.append_run`.
* **Today tested:** a lease lost *during* a step (the thief writes while the step runs) is detected by the check before the write
  (`test_a_runner_that_lost_its_lease_does_not_overwrite_the_task_or_register_a_success`), and a step longer than the TTL keeps its lease.
* **Not tested, and a real window:** the lease is lost *between* `still_mine()` and the registry write (another runner takes the task in those
  milliseconds): the step is registered as SUCCEEDED by a runner that no longer holds the task. The write of the step row is not conditional on the
  task revision because the registry is append-only and separate from the object store.
* **Also untested:** the process dying between the capability's own commits and the step row (the capability's objects exist, no step summary: the next
  run redoes the step, relying on the capability's own idempotence); a heartbeat that fails repeatedly with transient database errors (it keeps trying,
  and the final check is what decides); `break_lease` taken while the previous runner is still alive and mid-step.
* **Fix has to:** write the step row and a task-revision check in one transaction (a `task_step` row carrying the task revision it was made under, and an
  insert that fails if the task head moved: possible with a trigger or by writing the step summary as part of a task revision), then test the window with a
  barrier between the check and the write, and kill the process at each of the three points.

## B3. Smaller items noticed

* `task status` shows `produced_by` only for the newest recorded model; when a step was redone by another model, the older rows remain in the registry
  but are not listed. A `task history STEP` view would show them.
* Proposals made by an earlier model for statements the researcher has not confirmed stay on record after a model change and still appear in
  `research adjudicate sheet` next to the new model's proposals; the sheet could mark them superseded.
* Tasks created before the charge rows existed (2026-10-22) report a spend of 0. No such task exists outside tests.

## B4. Noticed in WP5 review follow-up and WP6 (2026-10-22), not fixed

* **Relationship naming is the weak point of contradiction detection.** The landscape compares findings only by the relationship label the extraction gave them (case and
  spacing normalised, plus equivalences the researcher declares with `--same`). A contradiction stated in other words is missed; the coverage counts findings without a label but cannot
  count findings whose label differs. A measured answer needs the researcher's own list of known contradictions in a real pilot corpus (not written here); a model-assisted label
  normaliser would be a proposal to the researcher, never a decision, and should be evaluated against that list before it is adopted.
* **No model-proposed method assessment.** `landscape assess` is written by the researcher only. A model could draft criteria and threats from the paper's design passages and the
  methodological literature in the library; it would be stored as a proposal, and the cap in `method_verdict` would still apply to anything the researcher confirms. Not built.
* **Introduction arguments and contribution claims are not part of the landscape** (`RP_INTRO_ARGUMENT`, `RP_CONTRIBUTION` are not run by `landscape build`): WP7 needs the claimed
  contribution of the closest papers and will add that step with its own tests.
* **Retrieval top-n union.** The reading list is the union of the R1 and R2 lists, each cut at `top_n` per tier and query, so it can hold up to twice that; a paper ranked below the
  cut in both lists is not shown. Whether a larger default `top_n` is worth the reading cost is an empirical question for a real corpus.
* **Landscape entries join the ECA sampling pool.** Every landscape entry is a STANDARD statement sampled at the D15 rate; a large landscape therefore adds adjudication work. The
  coverage report already shows it; whether findings should default to HIGH once a candidate rests on them is decided in WP7 (`use_in_conclusion`).
