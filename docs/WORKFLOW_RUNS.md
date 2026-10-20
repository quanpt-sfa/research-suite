# Workflow run registry (9.2, Phase 4.1, schema 9.0.3)

Every execution of a workflow is one row of the append-only table `workflow_runs`: normal runs, reuses, refused
requests, invalid model answers and failures. The registry replaces the 9.1.1 `workflow_cache/*.json` files.

## What a row holds

| Column | Meaning |
|---|---|
| `run_id`, `created_at`, `state_revision` | identity; the state revision the run read |
| `workflow`, `research_question_id`, `focus_claims` | what was run, for which question and focus |
| `input_fingerprint`, `input_revisions` | cache identity, and every object revision the run read (with content digests) |
| `skill_digest`, `executor`, `model_descriptor` | the skill text sent, `deterministic@<version>` or `model@<contract>`, the adapter's declared identity |
| `context_manifest_sha256`, `prompt_sha256`, `artifact_sha256` | CAS digests: the context manifest, the exact model request, the output |
| `execution_status`, `context_status`, `assessment_status`, `applicability`, `legacy_status` | the four status axes and the legacy combined string |
| `usage` | the `TaskUsage` record as reported |
| `reused_from_run_id`, `parent_run_id`, `upstream_run_ids` | reuse link; the run this one supersedes; runs whose output it read |
| `legacy`, `known_defects` | imported 9.1.1 output, and what the audit found wrong with that workflow's 9.1.1 outputs |

Rows cannot be updated or deleted (triggers). The table is not part of the state digest, and inserting a row does not
bump the state revision, so the scientific integrity hashes are unaffected.

## Reuse

A run is reused only if it is an original (not itself a reuse), non-legacy, `execution=SUCCEEDED`, `context!=INSUFFICIENT`,
its combined status is `SUCCESS` or `REQUIRES_VERIFICATION`, and its stored output still verifies against its hash.
The reused output carries **all four axes exactly as stored**; `status` reads `REUSED` only as a legacy marker, and
`metadata.original_status` / `metadata.reused_from_run_id` say what it stands for. Incomplete, invalid, refused and failed
runs are recorded but never reused.

The fingerprint covers the selected context (object revisions and content digests, loaded full texts, omissions, coverage),
the skill text, the executor version, the adapter's `describe()` (model runs), `allow_incomplete`, extra configuration, and
the *content* of upstream outputs (never their timestamps or task ids). The numeric budget is not hashed: a different
limit that selects the same context is a hit.

## Commands

```
research workflow runs  --project P [--workflow W] [--question RQ] [--legacy] [--limit N] [--json]
research workflow show  RUN_ID --project P [--prompt] [--json]     # --prompt prints the exact model request
research workflow diff  RUN_A RUN_B --project P [--json]           # status axes, input revisions, result fields
```

Python: `research_harness.workflows.runs` (`list_workflow_runs`, `get_workflow_run`, `find_run_by_artifact`,
`load_workflow_output`, `load_run_prompt`, `run_history`, `diff_runs`). An output is found by run id or by the digest of
its stored artifact; no cache file is involved.

## Upgrading an existing project

`research project upgrade-schema` (owner token) creates the table and imports each readable `workflow_cache/*.json`
as a **legacy** run: kept as history, `assessment=NOT_ASSESSED`, `known_defects` listing the problems the audit found in
that workflow's 9.1.1 output, never reusable. The files are then moved, unchanged, to `workflow_cache_legacy/`; unreadable
files are left in place. A 9.2 run of the same workflow and question records the legacy run as its `parent_run_id`, so
`workflow diff` shows exactly what changed. Until a project is upgraded, `run_workflow` raises
`SCHEMA_UPGRADE_REQUIRED`; all other Research Core functions are unaffected.
