# M5 work packages

> **Amended (M5_DESIGN_AMENDMENT_1.md A11):** scopes and efforts of WP1, WP2, WP3, WP5, WP6, WP7, WP8, WP9, WP11, WP12 change; a new WP14 (the
> pre-emption truth table as data, with a completeness test, reviewed by the owner) precedes WP7/WP8. Revised totals: ≈64 dev-days expected
> (≈47–92), researcher ≈20–30 h. The per-package fields below are the pre-amendment baseline; A11 lists the deltas.

Status: **proposal**. No package has started. Effort in developer-days: best / expected / worst. "Researcher" = time the owner spends.
Every package's Definition of Done includes: CI green on all four jobs of `.github/workflows` (run id quoted in the milestone report), no change to
existing scientific state (stores under `sample/` untouched), docs updated, and a milestone-report entry in `docs/refactor_9_2/milestones.json`.

---

### WP0 — CI repair and CI-status rule (P0)

1. **Goal**: make `main` green; stop reporting milestones from local runs alone.
2. **Prerequisites**: none.
3. **Reused**: `cli_knowledge.py::_json_arg`; `scripts/acceptance_report.py`.
4. **Files**: `research_harness/cli_knowledge.py`; `tests/test_m3_cli.py` (or new `tests/test_cli_json_arg.py`); `scripts/acceptance_report.py`.
5. **Schema/API/CLI**: none visible.
6. **Algorithm**: `s = value.lstrip(); if s[:1] in "[{": raw = value; else: try: raw = Path(value).read_text() if Path(value).is_file() else value; except OSError: raw = value`.
   Acceptance report gains a `--ci-run` field and refuses to print PASS for a milestone when the given run is not `success`.
7. **Migrations**: none.
8. **Errors**: invalid JSON → the existing `ValueError("<option>: not valid JSON (…)")` (`cli_knowledge.py:85-88`), unchanged.
9. **Security**: a path value is still read only when it is an existing file; no shell.
10. **Artefacts**: green CI run id.
11. **Tests**: `test_json_arg_inline_value_longer_than_255_bytes` (fails on Linux today), `test_json_arg_file_path_still_read`, `test_json_arg_bracket_file_name`.
12. **Effort**: 0.25 / 0.5 / 1.
13. **Depends on**: —. Blocks: every other WP's DoD.
14. **DoD**: CI green on all four jobs; `test_m3_cli.py::test_replication_plan_and_coverage_commands` passes on Linux.
15. **Rollback**: revert the single commit.

### WP1 — Store foundations: expected-head writes, schema, vocabularies, run linkage

1. **Goal**: the data model of `M5_TASKS_AND_DATA_MODEL.md` §8–9 without behaviour.
2. **Prerequisites**: WP0.
3. **Reused**: `state/store.py::Tx.add_revision`; `state/workflow_runs.py`; `protocols/schema.py::_TYPE_PROPS`; `taxonomy`; `graph/relations.py::SPECS`; `validation/integrity.py`.
4. **Files**: those modules; new `research_harness/taxonomy/discovery.json`; migration in `state/` (pattern of the existing registry migration).
5. **Schema/API**: `add_revision(..., expected_head: Optional[int] = None)` → `STALE_REVISION`; types `ResearchTask`, `ResearchProblem`, `ContributionCandidate`,
   `ResearchDecision`, `RetrievalRun`; columns `workflow_runs.task_id`, `task_step_id`; **no new relations** (SQL CHECK on `edges.relation`, `state/schema.py:156`):
   `depends_on` / `derived_from` with `provenance.edge_kind`; `ContributionCandidate` added to `graph/relations.py::_HYPOTHESIS`.
6. **Algorithm**: inside `add_revision`, compare `expected_head` with `head["head_revision"]` (or `None` for creation) before insert.
7. **Migrations**: M5-1..M5-4 (additive; see data-model doc §9).
8. **Errors**: `STALE_REVISION`, `SCHEMA_VERSION_UNSUPPORTED` when M4 code opens an M5 store (documented).
9. **Security**: roles unchanged; new types are not writable through proposals except `ContributionCandidate` drafts and `RetrievalRun` (engine) — decided per type in the schema.
10. **Artefacts**: schema export (`protocols/schema.py::export_schemas`) updated.
11. **Tests**: `test_add_revision_expected_head_refuses_stale`, `test_migration_m5_keeps_old_rows_and_digests`, integrity tests for each type, existing suite unchanged.
12. **Effort**: 1.5 / 2 / 3.
13. **Depends on**: WP0.
14. **DoD**: an M4 store (copy of a synthetic fixture, never `sample/`) migrates; all existing tests pass unchanged.
15. **Rollback**: migration is additive; revert code; columns stay unused.

### WP2 — Readable exporter core (+ Methods and Gate K readable files)

1. **Goal**: the shared exporter and manifest; first renderers for Methods (playbook, extraction, adjudication sheet, fidelity) so `GATEL_REAL` can be read without SQLite.
2. **Prerequisites**: WP0 (WP1 not needed for Methods files).
3. **Reused**: `publish.py` manifest/digest pattern; `playbooks.element_report`, `fidelity_report`; `eca.review_sheet`, `coverage_report`; `reading_protocols.extraction_status`; `corpus.verify_passage`; registry usage.
4. **Files**: new `research_harness/export/{manifest,render_md,render_csv,contracts,safety}.py`; `cli_knowledge.py` (`register_m5`: `export methods`, `export verify`).
5. **CLI**: `research export methods --project P --playbook PB [--plan PLAN] --out DIR`; `research export verify DIR --project P`.
6. **Algorithm**: collect → re-verify quotes → classify rows → render deterministic text (sorted ids, fixed float format) → write files → manifest with sha256.
7. **Migrations**: none.
8. **Errors**: `EXPORT_PATH_UNSAFE`, `EXPORT_DIR_NOT_EMPTY`, upstream missing → `NOT_AVAILABLE` file headers (not an error).
9. **Security**: path rules, quote length bound, secret scan of output before writing the manifest.
10. **Artefacts**: export of a synthetic fixture checked in as expected output.
11. **Tests**: the export-contract list (`M5_EXPORT_CONTRACT.md` §3) for Methods files.
12. **Effort**: 2.5 / 3.5 / 5.
13. **Depends on**: WP0.
14. **DoD**: export of the synthetic pilot chain (`tests/test_pilot_chain.py` fixture) reproducible byte-for-byte; the researcher can (optionally, on their machine) run it on `GATEL_REAL` — this reads the store and writes only to a new directory.
15. **Rollback**: new package; remove the CLI group.

### WP3 — ResearchTask core

1. **Goal**: tasks with goals, plans, state machine, checkpoints, retry/resume/cancel, budget.
2. **Prerequisites**: WP1.
3. **Reused**: `workflow_runs` (state source), `domain_engine.evaluate_predicate`, `dispatcher.run_workflow`, `context._tokens`, `knowledge/common.next_numbered_id`, `register_run`.
4. **Files**: new `research_harness/tasks/{model,planner,runner,goals,estimate}.py`; goal specs `research_harness/tasks/goals/*.json`; CLI `task` group.
5. **Schema/CLI**: as in data-model doc §10 (`task start|plan|approve|revise|run|status|retry|cancel|replan|continue`).
6. **Algorithm**: pseudocode in the discovery protocol (end) — runner loop over steps in dependency order, fingerprint reuse, decide-halts, budget check before each model step.
7. **Migrations**: none beyond WP1.
8. **Errors**: `TASK_STATE_INVALID`, `STEP_INPUT_MISSING`, `BUDGET_EXCEEDED` (BLOCKED, not raised), `STALE_REVISION`, `CHECKPOINT_REQUIRED`.
9. **Security**: approve/decide need `author`/`owner`; run of model steps needs a token as today.
10. **Artefacts**: plan JSON in CAS per revision.
11. **Tests**: task list in the Gate M test matrix (§5).
12. **Effort**: 4 / 5 / 7.
13. **Depends on**: WP1. Parallel with WP2, WP4, WP5.
14. **DoD**: a goal `review_literature` (existing capabilities only) runs end to end as a task on scripted adapters; crash-injection test passes.
15. **Rollback**: new package; tasks are additive objects.

### WP4 — Problem representation and framing (D16)

1. **Goal**: `ResearchProblem`, interpretations, choose, promote-to-RQ.
2. **Prerequisites**: WP1; D16 approved.
3. **Reused**: `eca` (model framings are not statements, but chosen interpretations used in conclusions become HIGH via `mark_high`), `workflows/registry.py` skill files.
4. **Files**: `research_harness/discovery/problem.py`; skill `workflows/skills/discovery/frame_problem.md`; CLI `problem` group.
5. **Schema/CLI**: data-model doc §2.3, §10.
6. **Algorithm**: framing = one model call → validated JSON (`static_check`) → stored as PROPOSED interpretations in a new problem revision.
7. **Migrations**: none.
8. **Errors**: `PROBLEM_NOT_FOUND`, `INTERPRETATION_UNKNOWN`, `RQ_TEXT_REQUIRED`.
9. **Security**: problem text is researcher data; sent to a provider only when the researcher runs `frame --propose`.
10. **Artefacts**: —.
11. **Tests**: problem tests in the matrix.
12. **Effort**: 1.5 / 2 / 3.
13. **Depends on**: WP1, D16.
14. **DoD**: promote-to-RQ history test passes; `context.build_context` still refuses a problem id as RQ.
15. **Rollback**: remove type use; objects remain readable.

### WP5 — Retrieval and coverage

1. **Goal**: R1 BM25, R2 expansion, R3a in-corpus citations, R5 researcher edits, coverage block; R3b external metadata behind D17.
2. **Prerequisites**: WP1; D17 for R3b only.
3. **Reused**: `corpus.source_text`, `source_sections`; library objects; `literature.search_protocol_problems`; adapters.
4. **Files**: `research_harness/discovery/retrieval.py` (BM25 in stdlib), `discovery/metadata_sources.py` (R3b, stdlib `urllib`, as the OpenAI-compatible adapter); benchmark fixture `tests/fixtures/retrieval_bench/`.
5. **Schema/CLI**: `RetrievalRun` object; `research discover retrieve --task T [--expand] [--metadata-source openalex]`.
6. **Algorithm**: tokenization (lower-case, Unicode word split, stop list), BM25 (k1=1.2, b=0.75, declared in the run), section-level documents with source roll-up; expansion stored; stable sort by (score desc, id).
7. **Migrations**: none.
8. **Errors**: `CORPUS_EMPTY` (task BLOCKED with "import papers"), `METADATA_SOURCE_DISABLED`, HTTP errors recorded, never fatal for in-corpus retrieval.
9. **Security**: only query terms leave the machine; responses stored; no download of full text.
10. **Artefacts**: retrieval benchmark numbers (recall@10, precision@10, MRR) in the milestone report.
11. **Tests**: retrieval list in the matrix.
12. **Effort**: 3.5 / 5 / 7 (R3b: +1.5 / 2 / 3).
13. **Depends on**: WP1. Parallel with WP3, WP4.
14. **DoD**: benchmark baseline recorded; tests C and E of Gate M synthetic pass at unit level.
15. **Rollback**: retrieval is read-only over the store; remove module.

### WP6 — Landscape

1. **Goal**: landscape entries with three layers.
2. **Prerequisites**: WP5 (reading list), WP3 (task step).
3. **Reused**: `literature.run_literature`, `argument.analyze_introduction`, `reading_protocols.run_protocol` with new data entry `RP_LANDSCAPE`; `eca.statement_assessment`.
4. **Files**: `workflows/protocols_data/reading_protocols.json` (new entry), `discovery/landscape.py`.
5. **Schema**: landscape CAS artefact; entries reference `EvidencePassage` ids.
6. **Algorithm**: run protocols (cache shared with Methods/Gate K) → group by entry type → derive contradictions (same construct, opposite findings, overlapping scope) as suite inferences → fill `evidence_permits` only from adjudications.
7. **Migrations**: protocol data version bump.
8. **Errors**: extraction failures propagate as BLOCKED steps (existing `test_failed_extraction_stops_the_review` behaviour).
9. **Security**: rights check `send_to_provider` before model calls (WP13).
10. **Artefacts**: `research_landscape.md` renderer (WP2 core).
11. **Tests**: landscape list in the matrix.
12. **Effort**: 3 / 4 / 6.
13. **Depends on**: WP2, WP3, WP5.
14. **DoD**: synthetic landscape with planted contradiction renders with correct layers.
15. **Rollback**: remove module and protocol entry (version kept in history).

### WP7 — Candidate generation (discovery)

1. **Goal**: candidates with required fields, prior attempts first.
2. **Prerequisites**: WP6, WP10 (prior-attempt listing; can start with an empty library stub).
3. **Reused**: proposal gateway `static_check`, `lifecycle.match`, skill registry.
4. **Files**: `discovery/candidates.py`, skill `discovery/generate_candidates.md`.
5. **Schema**: `ContributionCandidate`.
6. **Algorithm**: list prior attempts → one call per interpretation → validate fields and gap ids → dedupe against existing candidates and idea history → store PROPOSED.
7. **Migrations**: none.
8. **Errors**: `GAP_ENTRY_UNKNOWN`, `CANDIDATE_TYPE_UNKNOWN`; invalid answer = failed run, nothing stored (existing pattern `test_an_unusable_answer_is_a_failed_run_and_stores_nothing`).
9. **Security**: model output passes `static_check`.
10. **Artefacts**: `contribution_candidates.md`.
11. **Tests**: candidate list in the matrix.
12. **Effort**: 2 / 3 / 4.
13. **Depends on**: WP6.
14. **DoD**: tests A (partial) and the candidate tests pass.
15. **Rollback**: remove module.

### WP8 — Adversarial verification and status derivation

1. **Goal**: R4 + per-(candidate, competitor) adversarial calls + ECA statements + deterministic status.
2. **Prerequisites**: WP5, WP7.
3. **Reused**: `quotes.find_quote`, `eca.adjudicate`, `eca.run_pending`, `eca.review_sheet`, `eca.confirm_batch`, `compare_contribution` rules, `context.build_context` full-text policy.
4. **Files**: `discovery/adversarial.py`, `discovery/status.py`, skill `discovery/adversarial_case.md`.
5. **Schema**: challenge records in CAS; ECA statements on `ContributionCandidate` elements (dimension).
6. **Algorithm**: status table in the discovery protocol §5.5 (pure function of confirmed adjudications + coverage).
7. **Migrations**: none.
8. **Errors**: unverifiable quotes dropped and counted; competitor without full text → coverage reason, no call.
9. **Security**: rights check; prompt-injection test.
10. **Artefacts**: `closest_competitors.csv`, `novelty_challenges.md`.
11. **Tests**: verification list; Gate M B, C, D, E, H at unit level.
12. **Effort**: 4 / 5.5 / 8.
13. **Depends on**: WP5, WP7.
14. **DoD**: status-rule table test covers every row; no export contains "novel" as a status.
15. **Rollback**: remove modules; ECA records created stay (they are append-only and valid).

### WP9 — Feasibility and researcher decisions

1. **Goal**: feasibility per candidate; `ResearchDecision` with all actions; sheet/apply batch.
2. **Prerequisites**: WP8 (statuses), WP3 (checkpoints).
3. **Reused**: `playbooks.select_playbooks`, `evaluate_conditions`, `lifecycle.match`, `journal_policy.alignment`, `eca.confirm_batch` pattern.
4. **Files**: `discovery/feasibility.py`, `discovery/decisions.py`; CLI `discover decide|sheet|apply`.
5. **Schema**: `ResearchDecision`.
6. **Algorithm**: deterministic condition evaluation; decisions bound to `(object_id, revision)`; claim-changing actions require a researcher-written new revision.
7. **Migrations**: none.
8. **Errors**: `RATIONALE_REQUIRED`, `ACTION_UNKNOWN`, `STALE_REVISION`, `DECISION_EXISTS`.
9. **Security**: role check; no default action.
10. **Artefacts**: `research_feasibility.md`, `decision_report.md`.
11. **Tests**: feasibility and decision lists.
12. **Effort**: 3 / 4 / 5.
13. **Depends on**: WP8.
14. **DoD**: Gate M H passes; `go_to_methods` creates a child task.
15. **Rollback**: remove modules; decisions remain as records.

### WP10 — Idea history and revisit

1. **Goal**: new knowledge kinds, retrospective proposals, revisit-condition evaluation, prior-attempt listing.
2. **Prerequisites**: WP1.
3. **Reused**: `lifecycle.acquire`, `formalize`, `promote`, `match`, `retrospective`, `revise`; `closure.use_in_project`; `domain_engine.ProjectFacts`.
4. **Files**: `taxonomy/knowledge.json`, `knowledge/lifecycle.py` (`_KIND_RULES`, blockers), new `knowledge/idea_history.py`.
5. **Schema**: kinds and fields in `M5_KNOWLEDGE_AND_INTEGRATION.md` §6.2.
6. **Algorithm**: revisit evaluation = per condition SATISFIED/NOT/UNKNOWN from facts; listing sorted by status then scope match.
7. **Migrations**: vocabulary version bump.
8. **Errors**: `REVISIT_CONDITION_INVALID`.
9. **Security**: none new.
10. **Artefacts**: prior attempts section of `decision_report.md`.
11. **Tests**: §6.5 list; Gate M F, G, J.
12. **Effort**: 2.5 / 3.5 / 5.
13. **Depends on**: WP1. Parallel with WP5–WP8 (integrates in WP7).
14. **DoD**: Gate M F and G pass.
15. **Rollback**: kinds unused; vocabulary entries harmless.

### WP11 — Gate M synthetic

1. **Goal**: A–J synthetic in CI; logic-benchmark perturbations.
2. **Prerequisites**: WP2–WP10.
3. **Reused**: `benchmarks/logic.py`, fixtures pattern of `tests/benchmark_fixture.py`.
4. **Files**: `tests/test_gate_m_synthetic.py`, `tests/fixtures/gate_m/`, `benchmarks/gate_m.py`, `milestones.json` (M5 entry).
5. **CLI**: `research benchmark gate-m-synthetic`.
6. **Algorithm**: planted corpus generator (deterministic seed).
7. **Migrations**: none.
8. **Errors**: —.
9. **Security**: prompt-injection fixture included.
10. **Artefacts**: milestone report section.
11. **Tests**: A–J.
12. **Effort**: 2 / 3 / 4.
13. **Depends on**: WP2–WP10.
14. **DoD**: A–J pass on all four CI jobs.
15. **Rollback**: tests only.

### WP12 — Gate M real (tooling + researcher)

1. **Goal**: blind reference form, sheet, scoring, export, `gate-validate M`.
2. **Prerequisites**: WP11; D18, D19 approved; researcher writes the problem statement and the reference.
3. **Reused**: `benchmarks/gate_k.py` blindness and export patterns.
4. **Files**: `benchmarks/gate_m.py` (real part), CLI `gate-m form|annotate|blindness|sheet|score|evaluate|export`, `docs/refactor_9_2/GATE_M_PROTOCOL.md`.
5. **CLI**: as listed.
6. **Algorithm**: as Gate K.
7. **Migrations**: none.
8. **Errors**: `REFERENCE_NOT_BLIND` refuses scoring.
9. **Security**: reference files stay in `sample/` or `$RESEARCH_EVAL_DIR` until the owner decides to commit the export.
10. **Artefacts**: `docs/refactor_9_2/evaluation/gate_m/` (owner's decision to commit).
11. **Tests**: `test_gate_m_real_artifacts_present` (skips until present), validator tests.
12. **Effort**: dev 1.5 / 2 / 3; researcher 4 / 6 / 10 hours (statement, reference, decisions, matching).
13. **Depends on**: WP11, researcher.
14. **DoD**: `gate-validate M` passes on the exported directory.
15. **Rollback**: tooling only; evaluation state lives outside git.

### WP13 — Integration with Gates K/L, Methods/Results, security tests

1. **Goal**: selective invalidation edges, shared extraction cache, rights fields, security tests, readable K/L/Results exports.
2. **Prerequisites**: WP1, WP2.
3. **Reused**: `graph/dependency.py`, `dispatcher.check_result_change_impact` (rewritten as a view over `impact_report`), `replication.set_entitlement` pattern.
4. **Files**: `graph/dependency.py` callers (edge-kind filters, no relation change), `knowledge/corpus.py` (rights fields), `export/` renderers for Results/claims, `tests/test_m5_security.py`.
5. **CLI**: `research corpus rights SRC --use … --store … --send-to-provider …`.
6. **Algorithm**: rights check before any model step that sends source text.
7. **Migrations**: none beyond WP1 (rights fields optional; missing = `UNKNOWN`).
8. **Errors**: `RIGHTS_NOT_ALLOWED`, `RIGHTS_UNKNOWN` (needs `--allow-unknown-rights`).
9. **Security**: §4 of the security doc.
10. **Artefacts**: readable Gate K/L files next to the JSON contracts.
11. **Tests**: integration and security lists in the matrix.
12. **Effort**: 3 / 4 / 6.
13. **Depends on**: WP1, WP2; parallel with WP5–WP10.
14. **DoD**: selective-invalidation and prompt-injection tests pass; `gate-validate K|L` unchanged.
15. **Rollback**: rights default `UNKNOWN` with a warning only, if the refusal proves too strict.

## Totals

| | Best | Expected | Worst |
|---|---|---|---|
| Development (WP0–WP13, R3b included; sum of the package estimates) | 36 | 49 | 70 |
| Researcher | ~8 h | ~14 h | ~24 h (D-decisions, problem statement, reference, decisions, matching, reviews at checkpoints) |

The earlier draft estimated 21–27 days (expected about 24); the increase comes from the concurrency foundation, rights handling, the retrieval benchmark, the coverage
status, the export contract for five workflows, and Gate M A–J instead of CD1–CD4.
