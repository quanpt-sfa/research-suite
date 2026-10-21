# WP1 / WP2 technical contracts (closed before code, as the third review asked)

Status: contracts for implementation. Short by design; the background is in Amendments 1–2. Code references: `main` = `c6397de`.

## WP1 split

* **WP1a — integrity (now):** atomic run boundaries for the two existing sites, recursive blob-reference scan (O17), integrity rule I22,
  `expected_head` on `Tx.add_revision`. No schema change, no migration.
* **WP1b — M5 schema (when WP3 opens):** new object types, vocabularies, `workflow_runs.task_id`/`task_step_id`, attempt index, budget recovery.
  Deferred so that nothing in the store format changes before it is needed.

## C1. Which SHA-256 values are CAS blobs

| Kind | Keys / places | Treated as blob reference |
|---|---|---|
| CAS blob references in object content (any depth) | `knowledge/closure.py::ARTIFACT_KEYS`: `artifact_sha256`, `blob_sha256`, `sections_sha256`, `source_sha256`, `sidecar_sha256`, `file_sha256`, `snapshot_sha256`, `sha256` | yes |
| CAS blob references in tables | `workflow_runs.artifact_sha256`, `proposals.artifact_sha256` | yes |
| text and passage fingerprints | `text_sha256`, `passage_norm_sha256`, `passage_head_sha256`, `passage_tail_sha256`, `content_digest`, `revision_digest`, `origin_sha256`, `state_sha256`, `subject_revision_digest`, hashes of quotes | **no** (they identify text, not a stored file) |

Rules: one shared function (the existing `closure.artifact_digests_in`) is used by `StateStore.recover`, `validation/integrity.py` (orphan rule 9 and
`REFERENCED_ARTIFACT_MISSING`) and closure; a test classifies every `*_sha256` property in `protocols/schema.py` as blob or fingerprint, so a new key
cannot be added without a decision. Effect of the fix: referenced nested blobs are no longer reported as orphans; a missing nested blob is reported.
There is still no deletion command; a future one must use this scan, run dry by default and respect a grace period.

## C2. Run boundary (I22)

* A unit of work writes its scientific objects and its run row in **one** SQLite transaction; the CAS blobs it references are written before that
  transaction (existing protocol in `state/artifacts.py`) and verified inside it.
* `reading_protocols.run_protocol` and `reading_protocols.reconsider`: passages and the run row in one transaction (today: `_store_records`, then
  `_register`).
* `eca.adjudicate`: the adjudication record and the run row in one transaction (today: `_register` SUCCEEDED, then `_write_record`).
* Failed runs (no objects) keep their own single write, as today.
* Integrity rule `SUCCEEDED_RUN_WITHOUT_OUTPUTS` for these two workflows: a SUCCEEDED `reading_protocol:*` run whose artefact lists records but no
  passage with its extraction key exists; a SUCCEEDED `eca` run without an adjudication record bound to it. Existing stores are only *reported*, never
  repaired automatically.
* Crash tests: fault injection between the CAS write and the commit and inside the commit (`RESEARCH_TEST_FAULT=raise:<point>` and `exit:<point>`):
  after the crash either both the objects and the run exist or neither; a retry reuses nothing partial.

## C3. Budget recovery (WP1b / WP3; stated now because the review asked)

* Before an API call, an **intent record** is written to the attempt index (`staging/attempts/<uow_key>.json`: reservation, time, model). After the
  call, the answer and usage are added. A crash between leaves an intent without an answer: on recovery it is charged at its full reservation as an
  *unknown-outcome call* and recorded in the registry as a `FAILED` run with `usage_source: reserved`.
* Money cannot be capped by `max_output_tokens` alone: with a price table the worst case is the sum of reservations, including unknown-outcome calls;
  the provider's own billing may still differ (e.g. requests completed after a client timeout). Reports say so.

## C4. Evaluation data stays untouched

* No test and no tool opens or writes `sample/` or any store under it; WP1a changes no schema, so no migration exists to run. A test asserts that the
  test suite's homes are temporary directories (conftest), and the CI checkout has no `sample/` (it is gitignored).
* `GATEL_REAL` remains readable by the new code: WP1a only changes how *new* writes are grouped, and the integrity rules only report.

## Implementation status (2026-10-22)

* **WP0 done:** `_json_arg` fix and `acceptance_report.py --ci-run`; commit `abe1578`; CI run 39656916379 green on all four jobs.
* **WP1a implemented**, commit `f529989`, CI run 39658649667 green on all four jobs (tests `tests/test_run_atomicity.py`; full suite locally 705 passed, 2 skipped):
  * one transaction for objects + run row in `reading_protocols.run_protocol`, `reading_protocols.reconsider`, `eca.adjudicate` (both paths) and
    a third site found during the work, `argument.compare_contribution` (it registered the run before revising the argument);
  * a refused or failed write registers the model calls already made as a FAILED run (`write_refused:<code>`) and re-raises;
  * `state/blob_refs.py` is the single blob-reference scan (the bare key `sha256` counts only inside a `files` list; a rubric digest is not a blob);
    used by `closure`, `StateStore.recover` and `integrity`; integrity now also checks run artefacts and reports `SUCCEEDED_RUN_WITHOUT_OUTPUTS`;
  * `Tx.add_revision(expected_head=…)` raises `STALE_REVISION`;
  * checked on a **copy** of `GATEL_REAL` in the scratchpad (original `state.sqlite3` sha256 `5fcfd51c…` unchanged, no new files): no I22 finding, no
    missing blob, one orphan blob (INFO).
* **Not changed, noted:** `execution/dag.py` registers an execution run and then writes Result objects that name it; the run records an execution
  that really happened, and the Results are re-derivable from its artefact. Grouping them is left for the Results work (O18).

## WP2 implementation status (2026-10-22)

* `research export methods --playbook PB [--plan PLAN] [--out DIR] [--into-repo]` and `research export verify DIR` (commit `4c76e89`, CI run 39688587818
  green). Files: `playbook.md`, `extraction.md`, `adjudication_sheet.md`, `fidelity_report.md`, `cost_report.md`, `manifest.json`, `export_info.json`.
* Implemented as contracted: provenance class per row; `NOT_AVAILABLE` files; quotes re-verified and bounded to 300 characters; deterministic manifest
  with a closure digest (objects, runs and artefacts the files rest on) and the corpus snapshot id; variable data only in `export_info.json`;
  secret patterns and configured key values refused before writing; output refused inside a store, `.git`, a symlink, a non-empty directory, or the
  tracked tree of a repository (ignored paths such as `sample/` are allowed).
* Checked on a **copy** of `GATEL_REAL`: `PB_ZAV` exports (30 passages, one extraction run family) and verifies; the original `state.sqlite3` hash is unchanged.
* Not in WP2 (later): Results, claims and Discussion exports; Gate K/L readable files next to their JSON contracts; `NOT_AVAILABLE` for a missing
  extraction is covered by a unit of `Doc.unavailable` only (a playbook without any run is hard to build through the public API).
* Researcher note: to read the real playbook, run `research export methods --playbook PB_ZAV --project GATEL_REAL --home "<_eval_home>"` — it only
  reads the store and writes under `<home>/exports/...` (inside the git-ignored `sample/`). Opening a store can still touch its SQLite WAL files;
  work on a copy if the file must stay byte-identical.

## C5. WP2 export

Amendment 1 §A9 is the contract (path rule, `manifest.json` vs `export_info.json`, task closure digest, fixed formatting). First renderers: Methods
files (playbook, extraction, adjudication sheet, fidelity report). Exports default to `<RESEARCH_HOME>/exports/…`, outside every store.
