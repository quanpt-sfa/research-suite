# Model-backed workflow runs (9.2, Phase 1)

`run_workflow(..., model_adapter=adapter)` executes a workflow through a model. Without an adapter nothing in the
harness makes a network call.

```python
from research_harness.workflows.adapters import AnthropicMessagesAdapter, ScriptedModelAdapter
from research_harness.workflows.dispatcher import run_workflow

adapter = AnthropicMessagesAdapter("<model id>")      # or set RESEARCH_MODEL; needs: pip install "research-harness[model]"
out = run_workflow("contribution", store, research_question_id="RQ_1", model_adapter=adapter)
```

## What a run does

1. Builds the budgeted context (unchanged selection logic).
2. Builds the request: **system prompt** = the selected workflow's SKILL.md + the output contract (JSON Schema
   derived from the workflow's result dataclass, plus mandatory `evidence_refs`); **user prompt** = the evidence.
   Unselected skills never appear.
3. Counts the request (`adapter.count_tokens` when exact, else the local estimate; the basis is recorded in
   `metadata.token_count_basis`). **A request larger than `max_input_tokens` is never sent** (`INCOMPLETE_CONTEXT`,
   `execution=NOT_RUN`, reason `budget_refused`). If evidence was omitted for budget the call is also refused unless
   `allow_incomplete=True`; the answer is then marked `context=PARTIAL`.
4. Calls `adapter.generate(system_prompt, user_prompt, expected_schema=..., temperature=0.0)`.
5. Validates the answer: schema, every cited `(object_id, revision)` must be in the request, every `quote` must
   appear verbatim in the text shown to the model (character offsets are computed by the harness; the model is
   not asked to count characters). Failure ⇒ `INVALID_OUTPUT` (stored with the validation errors and raw output,
   never cached).
6. Runs the deterministic evidence gate on the same context and records any contradiction between the answer and
   the gate in `result.gate_conflicts`.
7. Stores the output in the content-addressed store (a storage failure raises `WorkflowPersistenceError`).

## Status of a model answer

| Outcome | `status` | `execution` | `assessment` |
|---|---|---|---|
| valid answer | `REQUIRES_VERIFICATION` | `SUCCEEDED` | `PROVISIONAL` (never verified: not yet adjudicated) |
| malformed / uncited / misquoted / truncated | `INVALID_OUTPUT` | `FAILED` (`invalid_output`) | `NOT_ASSESSED` |
| refusal by the provider | `FAILED` | `FAILED` (`refusal`) | `NOT_ASSESSED` |
| adapter exception | `FAILED` | `FAILED` (`exception`) | `NOT_ASSESSED` |
| over budget / incomplete evidence | `INCOMPLETE_CONTEXT` | `NOT_RUN` (`budget_refused`) | `NOT_ASSESSED` |

`WorkflowOutput.axes` returns the four-axis `Status`; `status` remains the legacy combined string.

## Usage accounting

`TaskUsage` carries exactly what the adapter reported. `input_tokens` is the whole prompt (cached + uncached);
`cache_read_tokens` / `cache_creation_tokens` are portions of it. Anything the adapter did not report stays `None`.
`usage_source` is `provider` (live adapter), `scripted` (test double), `estimated` (offline executors) or
`offline_cache` (local reuse, never provider cache tokens).

## Writing an adapter

```python
class MyAdapter:
    model_id = "..."
    def generate(self, system_prompt, user_prompt, *, expected_schema=None, temperature=0.0):
        ...
        return answer_dict_or_json_string, TaskUsage(...)
    def describe(self):      # identity for cache fingerprints: provider, model id, every setting that changes answers
        ...
    def count_tokens(self, system_prompt, user_prompt):   # optional; exact count or None
        ...
```

Raise `ModelRefusal` / `ModelOutputTruncated` (carrying `usage=`) so the call is accounted for. Never let
`describe()` depend on `repr()` or memory addresses.

## `AnthropicMessagesAdapter` notes

* The model id is configuration (`model_id=` or `RESEARCH_MODEL`); it is never defaulted.
* No sampling parameters are sent. Depth is `effort` (`output_config.effort`, default `"high"`; `None` to omit).
* The system prompt is marked cacheable, so repeated runs of one workflow can show `cache_read_tokens`.
* For models documented to support it, the server-side refusal fallback is requested (`fallbacks="default"`,
  beta `server-side-fallback-2026-07-01`, beta endpoint). Disable with `use_fallbacks=False`.
* **Verification status:** exercised against a fake client and the installed SDK's real call signatures
  (`anthropic` 1.15.0). It has **not** been run against the live API.
