# M5 design: readable export contract (all five workflows)

Status: **proposal**. Nothing here is implemented. Part of `PLAN_M5_TASKS_AND_DISCOVERY.md`.

## 1. Rules common to every export

> **Amended (M5_DESIGN_AMENDMENT_1.md A9):** (1) paths are refused inside a store directory (one containing `state.sqlite3`), `.git`, or the
> repository's tracked tree without `--into-repo`; `sample/` itself is allowed; the default is `<RESEARCH_HOME>/exports/<project>/<scope>/<closure-digest>/`.
> (2) Reproducibility is defined over content files and a deterministic `manifest.json`; variable data (time, host, global state digest) goes to
> `export_info.json`. (3) The basis is a **task closure digest** plus the corpus snapshot, not the global `state_sha256`.

* **Reproducible from pinned state.** An export is a function of `(state_sha256, task_id@revision, exporter version)`. The manifest records all three;
  `research export verify DIR --project P` re-renders and compares digests. Rendering performs **no model call**.
* **Every line traces back.** Each table row and each quoted line carries an id (`object_id@revision`, `passage_id`, `run_id`); quotes are re-verified
  at export time (`corpus.verify_passage`); a passage that no longer verifies is rendered with `QUOTE_NO_LONGER_VERIFIES`, never silently.
* **Provenance class per file and per row**: `data_derived` (computed from stored data or runs, deterministic), `model_synthesised` (text produced by a
  model, not yet confirmed), `researcher_confirmed` (a researcher decision or confirmation exists for that exact revision). A file mixing classes
  marks each row; the manifest gives counts per class.
* **Uncertainty is shown, not hidden**: statuses (`NOT_ASSESSED`, `PROVISIONAL`, `INSUFFICIENT_COVERAGE`), open adjudications, and coverage blocks
  appear next to what they qualify.
* **`NOT_AVAILABLE`** when an upstream output is missing: the file is still written, with a header `NOT_AVAILABLE: <what is missing> (<step> not run /
  failed run <run_id>)`. No file is omitted, so a reader can tell "absent" from "empty".
* **Cost**: `cost_report.md` per export: per step model calls, input/output/cached tokens, wall time, money (only with a price table), researcher
  decisions count; estimate vs actual (O13 merged view).
* **No secrets**: environment values, tokens, `owner.token` paths, API keys and base URLs with credentials are never written; a test scans every export
  with secret patterns and the actual configured key value (held in memory during the test only).
* **No undistributable full text**: exports contain quotes (≤ a configurable length, default 300 characters) and locators, never full texts or
  sections. `--include-model-text` adds raw model answers (marked `model_synthesised`), off by default.
* **Safe paths**: output directory must not exist or be empty; refuses paths inside the project store, the library, `sample/`, or `.git`; file names are
  fixed by the contract (no names derived from object labels), so no path traversal from content.
* **Manifest** `manifest.json`: exporter version, task pin, state digest, list of files with sha256, provenance class counts, sources and revisions,
  runs (ids, workflows, models, usage with `usage_source`), coverage blocks, `NOT_AVAILABLE` list.

**Auto vs manual export default.** Recommended: **manual** (`research task export`), plus an automatic *preview* of `decision_report.md` into the task's
CAS artefacts when the task reaches `WAITING_FOR_RESEARCHER` (so the researcher has something to read at each checkpoint). Automatic writing to the file
system on every run would leave stale exports that look current; manual export with the state digest in each file header avoids that.

## 2. Files per workflow

### Contribution Discovery

| File | Content | Class |
|---|---|---|
| `research_landscape.md` | entries by type; per entry author claim / evidence permits / suite inference, passages | mixed (per row) |
| `contribution_candidates.md` | per candidate: type, claim, mechanism, gap entries, what would have to be true, setting, generator | model_synthesised until a decision exists |
| `closest_competitors.csv` | candidate, competitor source, retrieval stage and reason, dimension, outcome, passage id, quote, locator, adjudication id, confirmed y/n | mixed |
| `novelty_challenges.md` (file name kept from the spec; content never says "novel") | per candidate: challenge status, challenging passages, what is left, unchecked dimensions, coverage block | data_derived from confirmed adjudications |
| `research_feasibility.md` | data, measures, assumptions with SATISFIED/NOT/UNKNOWN, playbooks (candidate / applicable), journal alignment | data_derived + model_synthesised |
| `decision_report.md` | per candidate: status, researcher decisions with rationale, revisit conditions, open items; prior attempts from the library | researcher_confirmed + data_derived |
| `manifest.json` | as above | data_derived |
| `coverage_report.md`, `cost_report.md` | retrieval coverage; cost | data_derived |

### Literature review

`literature_review.md` (theory/method/contribution sections from `run_literature`), `constructs.csv`, `assumptions_untested.csv`, `prisma_flow.json`
(systematic mode only, flow counts; the 27-item checklist only if O11 is done), `manifest.json`, `coverage_report.md`, `cost_report.md`.

### Methods

`playbook.md` (elements with category, decisive/supporting, fidelity, reproducibility, validity per context), `analysis_plan.md`, `fidelity_report.md`
(from `playbooks.fidelity_report`), `adjudication_sheet.md` (from `eca.review_sheet`), `extraction.md` (per protocol run: records, rejected with reasons,
caps), `manifest.json`, `cost_report.md`.

### Results

`results_report.md` (per Result: VERIFIED_RUN/PROVISIONAL, diagnostics with criteria and signals, divergences against declared tolerances,
non-significance readings only with declared SESOI/alpha), `claim_changes.md` (derived change, confirmed y/n), `promise_check.md` (suggestions, none
applied), `runs.csv`, `manifest.json`, `cost_report.md`.

### Discussion / claims

`claims_status.md` (each claim with ECA status and coverage), `manuscript_suggestions.md` (suggestions with basis, never applied), `manifest.json`.

Gate K and Gate L exports keep their existing JSON contracts (`benchmarks/gate_k.py::export_artifacts`, `gate_l.py::export_artifacts`); the readable
files are *added* next to them, so `research gate-validate` is unchanged.

## 3. Tests

`test_export_lines_trace_to_objects_and_passages`, `test_export_marks_not_available_for_missing_upstream`, `test_export_is_reproducible_from_state_digest`,
`test_export_contains_no_secret_or_token`, `test_export_refuses_unsafe_paths`, `test_export_quotes_bounded_and_no_full_text`,
`test_export_never_contains_the_word_novel_as_a_status`, `test_provenance_class_counts_match_rows`.
