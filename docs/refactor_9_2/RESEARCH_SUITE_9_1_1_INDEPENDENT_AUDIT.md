# Research Suite 9.1.1 — Independent Functional Audit

**Audit date:** 2026-10-20
**Auditor:** Claude (independent reproduction; no production code modified)
**Verdict:** **NOT ACCEPTED**

---

## 1. Executive verdict

**NOT ACCEPTED.**

Research Suite 9.1.1 is still mainly a **metadata and workflow-infrastructure layer**. It is not yet a usable scientific research assistant. The Research Core underneath it (versioned objects, revisions, edges, CAS, dependency impact) works. All 154 existing tests pass on Python 3.10, 3.11 and 3.12, and CI is green. The PR's central claims, however, fail when you actually run the code.

1. **Model-backed execution does not work.** Every call of `run_workflow(..., model_adapter=ScriptedModelAdapter(...))` raises `TypeError: ScriptedModelAdapter.generate() got an unexpected keyword argument 'prompt'`, for all five workflows. The adapter never receives a request. No test in the repository uses the adapter. This alone fails the critical acceptance requirement in Part B.
2. **The deterministic executors still produce scientific conclusions from keywords and templates.**
   - Discussion reads a coefficient that is *negative as H1 predicts* as a challenge to the hypothesis. It then invents the inference "bounded or overturned by institutional frictions". When a result really does contradict the hypothesis but avoids trigger words, it is reported as an "Empirically corroborated formulation".
   - Literature labels "*not* consistent with … do *not* support prior findings" as `supports_prior`.
   - Methods proposes firm and year fixed effects, two-way clustering and Size/Leverage controls for a qualitative interview study in education.
3. **Contribution Finding cannot see competitor papers.** The context builder never expands `Source` objects for the contribution workflow. A competitor paper linked to the claim by a `challenges` edge, with its full text loaded, never appears in the output. This is also the item missing from the PR's own "75% recall" benchmark.
4. **Context leaks across research questions.** If you ask for RQ_A (accounting) without naming focus claims, the contribution candidate can come from RQ_B (education).
5. **The cache returns incomplete assessments as `REUSED`** and drops their `INCOMPLETE_CONTEXT` status.
6. **No workflow output is registered in the Research Core.** The `workflow_runs` table claimed in `docs/DEEPAGENTS_REFERENCE_ALIGNMENT.md:108` does not exist. The core's own `store.recover()` lists every workflow output as an *orphan artifact*.
7. **Several benchmark metrics are hard-coded.** `provenance_correctness_pct = 100.0`, `fulltext_inspection_status = "VERIFIED_ON_DEMAND"` and `cases_evaluated = 8` (only 7 cases are defined) are literals. The function that produces the reported numbers is never called by any test.

The token-efficiency mechanisms are real in part: only the selected skill is loaded, full text is loaded on demand, and the RQ and claims are pinned. But the token budget is only *detected* when exceeded, not *enforced*, and evidence text is rendered twice in each prompt.

---

## 2. Exact audit scope

| Item | Value |
|---|---|
| Target repository | https://github.com/quanpt-sfa/research-suite |
| PR | quanpt-sfa/research-suite#3 — **OPEN**, MERGEABLE |
| Audited head | `9728134f3da8cbfde5f9aa72cf0229ab8781ddc2` (verified: local = `origin/feature/workflow-9-1-1-scientific-correctness` = PR `headRefOid`; unchanged since the previous review) |
| Base | `73255a4037e538ac2cd45b75167e7d54190fda4e` (verified PR `baseRefOid`) |
| Deep Agents reference | `langchain-ai/deepagents` @ `12f70f27cc2e96ac715f4563e05b205be7adc275` (checked out and verified; upstream `main` has since moved to `a2f8843`, which was not audited) |
| Environment | Windows 11, uv 0.11.28, CPython 3.10.20 / 3.11.15 / 3.12.2, isolated envs with `jsonschema` + `pytest` only (no project venv; `tiktoken` absent unless stated) |
| Tests executed | Full suite on 3.10, 3.11, 3.12: **154 passed** each. (Without `PYTHONPATH`, `test_scenario_9_end_to_end_demo_usability` fails because its subprocess needs the package installed. This is an environment artefact, not a code defect.) |
| CI on exact commit | GitHub Actions run 39338874665: test (3.10) / (3.11) / (3.12) all **pass** |
| Independent reproductions | 67 checks in `repro.py` plus 2 in `repro2.py` (Section 8 and Appendix A) |
| Not independently verified | Real provider API usage (no provider adapter exists; no paid calls were made). Python 3.13+. Linux or macOS. D13 deployment (excluded). Enterprise and security behaviour (excluded). |

Evidence files (gitignored, outside the PR): `.tmp/audit_9_1_1/` contains `repro.py`, `repro2.py`, `repro_out.txt`, `repro_results.json` and `pytest_3.1x.log`.

---

## 3. Deep Agents source-to-source comparison

| Pattern | Deep Agents (pinned) — actual behaviour | Research Suite — actual behaviour | Missing / different | Evidence | Verdict |
|---|---|---|---|---|---|
| **A1 Progressive skill disclosure** | `middleware/skills.py` `SkillsMiddleware.before_agent` (l.1168) scans backend sources and parses frontmatter (`_parse_skill_metadata`, l.462) into `skills_metadata`. `modify_request` (l.1138) puts only `- **name**: description` plus `Read <path>` into the system prompt (`_format_skills_list`, l.1099). The body reaches the model only through `read_file`, or as a pinned `HumanMessage` (`_pinned_skill_message`, l.849). `include_tools` stay hidden until disclosed (`_disclose_skill_tools`, l.1304). | `registry.py` `_initialize_metadata` (l.49–93) **hard-codes** metadata; SKILL.md frontmatter is never parsed (its `allowed_tools` and `version` are ignored, and metadata version stays `9.1.0`). `load_skill_instructions` (l.106) strips frontmatter and memoizes the body for the life of the process. `build_context` (`context.py:43-44`) loads only the selected skill. `ContextPackage.formatted_prompt` (`contracts.py:172-173`) embeds it. The caller chooses the workflow (an eager pin, not model choice), which is a reasonable adaptation for one researcher. | No tool disclosure, although the alignment doc's table says "Skill Tool Disclosure ADAPTED". The skill body memo means an on-disk edit is invisible until restart. **The deterministic executors never read SKILL.md, so the skill affects only the cache digest and a prompt that no working adapter receives.** | B3a PASS (prompt holds only the contribution body, via a duck-typed adapter). B1 FAIL (shipped adapter crashes). E1 PARTIAL. | **PARTIAL** |
| **A2 Context management / offload** | `summarization.py` `_call_with_budget` (l.1464) counts the *complete* request (messages + system + tools). It clips the tail, and `_check_reduction` raises `ContextOverflowError` if the request is still over budget, so it never sends an oversize request. `_offload_to_backend` (l.1266) appends evicted history to `/conversation_history/{session}.md`, so originals stay retrievable. `filesystem.py` `_process_large_message` (l.3498) moves tool results above `tool_token_limit_before_evict` (default 20000) to `/large_tool_results/…` and leaves a preview plus a path. `read_file` paginates (`DEFAULT_READ_LIMIT = 100`, offset/limit). | `context.py:167-211` pops `full_text` out of Source content and records a status (`FULLTEXT_AVAILABLE/LOADED`, `ABSTRACT_ONLY`, `FULLTEXT_MISSING`). It loads the body only for `always`, or for `on_demand` plus an explicit source id; the body can come from CAS via `artifact_sha256`. No summaries replace evidence (good). Every object keeps `object_id` + `revision` + `content_digest`. The budget is checked object by object, plus a final check on the prompt (`l.422-437`) that **only relabels** the status. | No enforcement: the prompt is sent at up to 1.8× the budget. Skill text (~598 est. tokens) is not reserved during selection. There is no paging or preview stub: a large full text drops the **whole Source**, including its metadata. Dropping a low-priority item leaves `budget_status = "OK"` (`l.337-341`). Evidence text appears twice in the prompt. | D3-400/900/1300 PARTIAL (709/400, 1356/900, 1677/1300). D3b FAIL (Source dropped, status OK). A4-dup FAIL. D4 PASS. | **PARTIAL** |
| **A3 Isolated execution** | `subagents.py` `SubAgent` (l.84): isolated by default, so the child gets only the task description. `_EXCLUDED_STATE_KEYS` (l.~420) strips parent state, and only the final message returns. | No subagents; each `run_workflow` builds its own context, so five agents never launch. `run_workflow_sequence` (`dispatcher.py:300-326`) passes earlier `WorkflowOutput` objects as `upstream_artifacts` (structured, not chat history). This is the right principle. | Consumers read only a few keys, through keyword filters (`contribution.py:56-67` keeps a Results record only if it contains `fail`, `not significant` or `persist`). There is no handoff schema. The CLI cannot run a handoff. Nothing is written back (no revised Claim, no link between versions). | I-11 PARTIAL (repro2: chain runs; a confirmation becomes a "contradictory finding"; CLAIM_1 stays at rev 1). | **PARTIAL** |
| **A4 Token efficiency / blob offload** | `_blob_offload.py` `_pending_blobs` / `_stub_messages` / `_hydrate_messages`: bulky payloads go to `blobs/<sha256>` with `{"deepagents_blob": sha}` stubs, rehydrated on use. | Source full text in CAS is loaded by `artifact_sha256` on demand (the closest genuine adaptation). Unrelated skills are excluded. Workflow outputs go to CAS (`dispatcher.py:276-283`) **with no reference** from the Research Core. Local cache reuse works for identical inputs. | Duplicate evidence is not removed (rendered as an object *and* an excerpt). There are avoidable cache misses (unrelated claims, budget value, upstream timestamps). No model call works, so "avoiding unnecessary model calls" cannot be shown. | A4-dup FAIL. E-unrelated / E4 / E10 AVOIDABLE_MISS. F2 FAIL. | **PARTIAL** |

**Summary:** The upstream patterns are described accurately in `docs/DEEPAGENTS_REFERENCE_ALIGNMENT.md`. What was genuinely adapted is single-skill loading, on-demand full text with honest status labels, and structured (non-chat) handoffs. What is missing is call-time budget enforcement with compaction or refusal, paging or preview of large text, a working model-call path, and registry metadata parsed from the SKILL.md files. Four statements in the alignment and acceptance docs have no corresponding code:
- a `workflow_runs` table and `WorkflowArtifactRecord`;
- a `CONTEXT_PREPARED` status;
- an `EXCERPT_ONLY` status;
- conditional tool disclosure.

---

## 4. Functional workflow results

| Workflow | Status | What genuinely works | What fails | Commands / manual steps / real model calls for a realistic task\* |
|---|---|---|---|---|
| **Contribution** | **PARTIALLY WORKING** | Does not claim causal identification from tier (C1a). Absence of papers ≠ novelty (C1e). Returns `REQUIRES_VERIFICATION`. | Competitor Sources invisible (C1c, D4b, G3-missing). Anticipating passage counted as *supporting* (C1d). `identification` substring ⇒ `IDENTIFIED_BY_VERIFIED_DESIGN` (C1b). Only the first mechanism is used (C1f). The researcher's own H2 is reported as "closest competing contribution" (C1g). Candidate taken from another RQ (D5b). | 1 `project init` + hand-authored proposal JSON (each Source, passage + locator, Claim, edge) + 1 `proposal import` + 1 `workflow run`. **0 model calls possible.** |
| **Literature** | **PARTIALLY WORKING** | Passage-level provenance (source, revision, locator) preserved. `UNKNOWN` / `NOT_REPORTED` for missing design and sample (C2d). Full-text status correct (C2e, D4). | No search, ingestion or passage extraction. Every passage and locator is typed by hand. `"not consistent with"` → `supports_prior` (C2b). Loaded full text is never examined, so a contradiction in the Limitations section is missed (C2c). A conflict without keywords is not detected (C2a). Zero sources ⇒ `SUCCESS` (C2f). | Same as above; each paper ≈ 1 Source + n passages typed manually. |
| **Methods** | **PLACEHOLDER** | Marks designs `CANDIDATE_METHOD` / `justified: False`; checklist `REQUIRES_INFORMATION`. | Same template for every design: DID/IV for any causal tier, otherwise "Panel Regression with Multi-Way FE", always FE + two-way clustering + Size/Leverage/PriorPerformance/MacroTrend + panel-attrition assumption. This includes conceptual work and qualitative education interviews (C3-*). The researcher's candidate methods are ignored (C3-competing). | 1 `workflow run`; output is template-filled. |
| **Results** | **PARTIALLY WORKING** | Does not fabricate seed, script or estimator (C4a). Revision diff + `depends_on` affected claims. `check_result_change_impact` works. | No execution capture. A caller flag `verified_computation=True` ⇒ `VERIFIED_COMPUTATIONAL_RUN` / `SUCCESS` with no run at all (C4c). An author-typed summary is listed under `findings_supported_by_available_outputs` (C4b). | Results must be typed in by hand as `Result` objects; scripts and tables cannot be attached through the CLI. |
| **Discussion** | **PLACEHOLDER (produces incorrect interpretations)** | Rival explanations default to `NOT_VERIFIED` in normal runs. | Keyword direction (C5-1/2/3/5), invented "institutional frictions" inference, "Empirically corroborated formulation" when no keyword fires or with zero results (D2). A planned (not run) robustness test ⇒ `ADDRESSED_BY_SPECIFICATION` if Analysis objects reach it (C5-4; latent, because the discussion expansion currently drops Analysis). | Python API only for handoffs (`upstream_artifacts` not exposed in CLI). |
| **Model-backed path (all five)** | **NOT IMPLEMENTED (non-functional)** | Prompt assembly would carry the selected skill + evidence (B3a/B3b). | `TypeError` on first call (B1). No output schema in the request (B3c). | — |

\*Realistic task: 1 RQ, 5 papers with full text, 2 results, 1 hypothesis. No PDF or text ingestion exists. `proposal import` takes JSON in which full text must be pasted into `content.full_text`, and every `EvidencePassage` quote and locator must be written by hand. That comes to roughly 25–30 hand-authored objects and edges, plus `project init`, `proposal import`, and five `workflow run` commands. Discussion→Contribution handoff and run metadata cannot be reached from the CLI (`cli.py:641-674,953-962`). **Zero real model calls are possible.**

---

## 5. Confirmed defects

### P0 — prevent core execution, invent conclusions, lose evidence, or return stale assessments

**P0-1. Model adapter cannot be called (all five workflows).**
- **Lines:** `dispatcher.py:224-228` calls `adapter.generate(prompt=…, workflow=…, schema_version="1.0")`; `adapters.py:23-30,66-73` defines `generate(system_prompt, user_prompt, *, expected_schema=None, temperature=0.0)`.
- **Second mismatch:** the adapter returns a tuple `(result, usage)`. The dispatcher reads `getattr(res, "usage")` and `getattr(res, "result", res)` (`l.229,247`), so `result` becomes the tuple. JSON serialisation then fails, and both CAS persistence and cache writing are swallowed by `except Exception: pass` (`l.282-283, 291-292`).
- **Repro:** B1-* (`TypeError … unexpected keyword argument 'prompt'`, `recorded_requests=0`). B2 (a signature-compatible shim gives `type(result)=tuple`, `artifact_sha256 present=False`, and the adapter's own `TaskUsage` is discarded).
- **Consequence:** model-backed reasoning cannot be used at all. Every model-related acceptance claim is untested; no test imports `ScriptedModelAdapter`.
- **Minimal fix:** call `generate(system_prompt=<skill + output contract>, user_prompt=<context>, expected_schema=<workflow schema>)`, unpack `(result, usage)`, and add one end-to-end test per workflow.

**P0-2. Model output is accepted as `SUCCESS` whatever its budget, schema or format.**
- **Lines:** `dispatcher.py:248-265`.
- **Repro:** B3e (`{"assessment", "evidence_refs"}` only ⇒ `SUCCESS`). B3f (free text ⇒ `{"raw_output": …}`, `SUCCESS`). B3g (prompt ~737 est. tokens against a 200 budget is sent, `SUCCESS`).
- **Consequence:** once P0-1 is fixed, a truncated or unparseable model answer would be stored and reused as a complete scientific assessment.
- **Minimal fix:** if `context.budget_status != "OK"`, return `INCOMPLETE_CONTEXT` (or refuse to send). Validate against the workflow output schema and mark failures `FAILED` / `INVALID_OUTPUT`.

**P0-3. Scientific direction and support are inferred from keywords.**
- **Discussion** (`skills/discussion.py:87-108, 180-188, 197`): `negative`, `null`, `zero`, `regression`, `persist`, `not significant` ⇒ contradiction, `reopens_contribution_workflow=True`, and the template inference "hypothesized effect is bounded or overturned by institutional frictions". No keyword ⇒ "consistent with expected direction" and "Empirically corroborated formulation".
  - C5-1: a negative coefficient that H1 predicts is read as a challenge.
  - C5-2: p = 0.41 is read as "does not conform"; non-significance is treated as falsification.
  - C5-3: the word "regression" is read as a contradiction.
  - C5-5: a result that *increased* restatements, against H1, is reported as "corroborated".
- **Literature** (`skills/literature.py:62-70`): the substrings `consistent with` / `supports` ⇒ `supports_prior` (C2b: "not consistent with … do not support" ⇒ `supports_prior` and listed in `supported_findings`).
- **Contribution** (`skills/contribution.py:78-80, 161-174`):
  - the substring `identification` in any SUPPORTED adjudication rationale ⇒ `IDENTIFIED_BY_VERIFIED_DESIGN` (C1b: "identification is NOT credible");
  - every passage without `contradict` / `fails` / `unsupported` ⇒ `supporting_evidence`, ignoring the `challenges` edge (C1d; also G3-contra, where both benchmark contradictory items are classified *supporting*).
- **Context builder** (`context.py:222-227`): the substring `contradict` matches the *key name* `contradictory_signal` (and `"contradictory": false`). Six benchmark objects are flagged and promoted to high priority.
- **Consequence:** confirmed hypotheses get reopened; falsified ones get reported as corroborated. The error then propagates through the handoff into Contribution (repro2).
- **Minimal fix:** stop setting direction or verdict from substrings. Use structured fields where they exist (edge relation `supports` / `challenges`, and Result fields `expected_sign`, `estimate`, `p_value`, `hypothesis_id`). Otherwise emit `NOT_ASSESSED` / `REQUIRES_REASONING`. Keyword hits may only raise a `flag_for_review`.

**P0-4. Contribution and Discussion cannot see Sources, so competitor papers are invisible.**
- **Lines:** `context.py:89-91, 101-103, 114-116`. `Source` is not in the contribution or discussion type filters, and the passage expansion at `l.125-130` only follows Sources that are already selected.
- **Repro:**
  - C1c: a competitor with loaded full text, linked by `challenges`, appears nowhere in the output; `closest_competing=[]`.
  - D4b: `fulltext_policy="always"` gives contribution 0 sources.
  - G3-missing: `SRC_COMPETITOR_FULL` is the item missing from the benchmark's "75% recall". The acceptance doc's explanation, "omitted due to explicit unrequested status", is wrong.
- **Consequence:** the novelty assessment ignores the very paper that anticipates the contribution.
- **Minimal fix:** add `Source` (and its passages) to the contribution and discussion expansion through edges from the focus claims and the RQ.

**P0-5. Explicit RQ does not scope claims; the focal claim can come from another research question.**
- **Lines:** `context.py:75-78` (all project Claims when there is no focus); `skills/contribution.py:70-72` (`focus_claims = [claims[0]]`).
- **Repro:** D5b. `research_question_id="RQ_A"` (board independence) gives the candidate `CLAIM_B` "Feedback raises motivation" from RQ_B (education).
- **Consequence:** an unrelated scientific object is presented as the contribution for the question that was asked.
- **Minimal fix:** without focus claims, select only claims connected to the RQ. Never default to `claims[0]`; evaluate all RQ claims or require a focus.

**P0-6. An incomplete assessment is reused as `REUSED` and loses its status.**
- **Lines:** `dispatcher.py:206-217`. `status="REUSED"`, and the original status is not stored or returned.
- **Repro:** E-incomplete. The first run is `INCOMPLETE_CONTEXT`; the rerun is `REUSED`, and the original status appears nowhere in the output.
- **Minimal fix:** persist the original status; return it with `metadata.reused=True`. Do not reuse `INCOMPLETE_CONTEXT` / `FAILED` outputs by default.

**P0-7. Methods emits a fixed econometric specification for any design.**
- **Lines:** `skills/methods.py:52-135`.
- **Repro:** C3-causal / cross_sectional / conceptual / edu_qualitative all get FE (Entity/Firm + Year), two-way clustering, Size/Leverage/PriorPerformance/MacroTrend, and a panel-attrition assumption. C3-competing: researcher-proposed RDD and synthetic control are ignored.
- **Consequence:** a template is presented as a design evaluation and pushes a panel-econometrics design onto qualitative and conceptual work. The label "PROPOSED" does not undo the steer.
- **Minimal fix:** remove the hard-coded `specification_plans`, `controls`, `fixed_effects` and `clustering`. Derive them from Dataset and Analysis objects. When they are absent, list the required information instead. Evaluate `candidate_methods`.

**P0-8. Results "verification" is self-attested.**
- **Lines:** `skills/results.py:134-142`. `verified_computation=True` in caller kwargs, plus any Dataset (every object has a `content_digest`) ⇒ `VERIFIED_COMPUTATIONAL_RUN`, `deterministic_verification=True`, workflow `SUCCESS`.
- **Repro:** C4c.
- **Consequence:** a hand-typed coefficient is reported as computationally verified.
- **Minimal fix:** verification requires a recorded execution: script hash, input data hash and output artifact hash, all present in the core.

### P1 — misleading metrics, unreliable context, incomplete provenance, wrong usage, repeated work

**P1-1. Workflow outputs are not registered in the Research Core.**
- **Lines:** `dispatcher.py:276-283`; `state/schema.py` has no `workflow_runs`. The claim in `DEEPAGENTS_REFERENCE_ALIGNMENT.md:108` is false.
- **Repro:** F1 (table list). F2: in a new process, 0 database rows reference the artifact id or sha; `store.recover()` reports it as an orphan; `workflow_usage.jsonl` has no sha. F3: after the cache is deleted, an identical request recomputes. F4: there is no previous-version link.
- **Consequence:** outputs survive only as unindexed CAS blobs. An earlier Contribution or Discussion assessment cannot be found or reopened by identity.
- **Minimal fix:** one append-only table (`run_id, workflow, input_fingerprint, status, artifact_sha256, skill_digest, model_descriptor, input_object_revisions, parent_run_id, created_at`), written in the same step as the CAS put.

**P1-2. The budget is detected but not enforced.**
- **Lines:** `context.py:294-343, 422-437`.
- **Repro:**
  - D3 assembled prompts: 709 / 1356 / 1677 est. tokens against budgets of 400 / 900 / 1300.
  - Skill text (~598) is not reserved during selection.
  - D3b: an oversized full text drops the entire Source while `budget_status="OK"` (a low-priority omission does not change the status).
  - A4-dup: each passage is rendered twice (`contracts.py:175-188`).
- **Minimal fix:** reserve header + skill + schema tokens first. Fit to the budget or refuse. Mark any omission that touches focal or RQ-linked evidence as INCOMPLETE. Render evidence once. Page or preview full text instead of dropping the Source.

**P1-3. Usage is mislabelled as provider usage.**
- **Lines:** each executor sets `usage_source="estimated" if context.is_token_estimate else "provider"` (e.g. `contribution.py:265`). `instrumentation.py:30-33` returns `is_estimate=False` when `tiktoken` is installed. `dispatcher.py:237-244` invents `output_tokens=50`, uses the intermediate-JSON estimate as `input_tokens`, labels it `"provider"`, and ignores the adapter's `TaskUsage`.
- **Repro:** repro2 `tokenizer` (with tiktoken: `llm_calls=0`, `usage_source="provider"`). B3d. B2 (dispatcher reports 435 input tokens; the prompt the adapter saw was ~832).
- **Note:** `test_i12` passes only because CI lacks `tiktoken`.
- **Minimal fix:** `usage_source="estimated"` whenever `llm_calls == 0`; use only adapter-reported usage for `"provider"`.

**P1-4. Cache identity is unstable or incomplete.**
- **Model** (`dispatcher.py:71-72`): `str(adapter)` (a memory address) is fingerprinted. Two identically configured adapters give different fingerprints (avoidable miss). Changing `model_id` on the same instance gives the same fingerprint (false hit) (E9).
- **Skill:** body memoized per process (`registry.py:109-110,125`), so an edited SKILL.md gives a same-process false hit (E1).
- **Upstream** (`dispatcher.py:57-60`): an upstream `WorkflowOutput` is hashed through `to_dict()`, which includes `timestamp` and a random `task_id`. Downstream workflows therefore never reuse, even for identical inputs (E10).
- **Over-broad invalidation:** adding an unrelated claim under another RQ invalidates all five workflows (E-unrelated). Changing the budget number alone invalidates (E4).
- **Selection-stale hit:** a new contradictory passage on an RQ-linked Source does not invalidate contribution or discussion (E6), because of P0-4.

**P1-5. Benchmark metrics are partly hard-coded and unfairly compared.**
- **Lines:** `tests/benchmark_fixture.py:499` (`cases_evaluated: 8`; 7 cases are defined, and "Case 7" is absent), `:501` (`provenance_correctness_pct: 100.0`), `:502` (`fulltext_inspection_status: "VERIFIED_ON_DEMAND"`).
- **Token metric** (`:488`): uses `pkg.estimated_tokens` (intermediate JSON, 3010), not the formatted prompt (2358), and compares it to a full snapshot export (31005 est. tokens: hashes, edges, audit metadata), which is not a prompt for the same task.
- **"Contradictory recall"** counts *retention*, not whether the workflow treats the items as contradicting. Both retained items are classified as supporting (G3-contra).
- `run_scientific_benchmark_evaluation` is never called by a test.

**P1-6. Literature never reads loaded full text; empty workflows report `SUCCESS`.**
- **Lines:** `skills/literature.py:51-117` analyses only excerpts (C2c). `literature.py:199-204` (C2f) and `discussion.py:225-230` (D2) give `SUCCESS` with zero evidence.

**P1-7. No output contract is sent to the model.**
- **Lines:** `dispatcher.py:224-228` sends neither `expected_schema` nor the output field list (B3c), so a model cannot produce the structured result the downstream handoffs expect.

**P1-8. Results lists author-typed summaries as "supported by available outputs".**
- **Lines:** `skills/results.py:71-72` (C4b).

### P2 — documentation, ergonomics, non-critical

- **P2-1.** Documented statuses `CONTEXT_PREPARED`, `REQUIRES_REASONING` and `EXCERPT_ONLY` are not produced anywhere. Executors return `REQUIRES_VERIFICATION`, and the dispatcher returns `usage_source="offline_cache"`; neither value is in the declared `Literal` types (`contracts.py:44,372`). The acceptance report's gate table cites tests that do not assert what the gates claim (Section 8).
- **P2-2.** The CLI `workflow run` has no `--fulltext`, `--fulltext-sources`, upstream, adapter or run-metadata options (`cli.py:953-962`).
- **P2-3.** Registry metadata is hard-coded instead of parsed from SKILL.md frontmatter (`registry.py:49-93`).
- **P2-4.** `discussion.py:151-170` would mark planned robustness tests `ADDRESSED_BY_SPECIFICATION` from the substrings `lag` / `year fe` (C5-4). This is latent today, because the discussion expansion drops Analysis.
- **P2-5.** `check_result_change_impact(..., to_revision)` ignores `to_revision`, and `requires_workflows` is a fixed list (`dispatcher.py:329-365`).

---

## 6. Context and token efficiency

All token counts are **approximate**: `ceil(len/3.8)` (`instrumentation.py:37`), because `tiktoken` was absent, exactly as in the PR's own run. With `tiktoken` installed the count is OpenAI `cl100k_base`, which is not the tokenizer of any Claude model. **No provider token was measured; real model calls made: 0.**

| Measurement | Value | Basis |
|---|---|---|
| PR-reported token reduction | **90.29%** reproduced exactly | candidate intermediate JSON (3010) vs snapshot JSON (31005) |
| Byte reduction (same function) | **92.4%** (acceptance doc says 88.4%; not reproduced) | candidate formatted prompt bytes vs snapshot JSON bytes |
| Candidate *model-facing prompt* | 2358 est. tokens | `formatted_prompt` |
| Same-format complete baseline (every head object, same renderer, same skill text) | 10862 est. tokens | prompt vs prompt |
| **Fair prompt-vs-prompt reduction** | **78.29%** | 1 − 2358/10862 |
| Skill instructions share of candidate prompt | ~598 / 2358 ≈ 25% | |
| Evidence duplication in prompt | each passage text ×2 | A4-dup |
| Budget overflow, assembled vs limit | 709/400, 1356/900, 1677/1300 | D3 |
| Required evidence recall | **3/4 = 75%** (missing: competitor Source, by design defect P0-4) | |
| Contradictory evidence *retention* | 2/2 | |
| Contradictory evidence *correctly classified* | **0/2** (both classified supporting) | G3-contra |
| Source-reference correctness | 4/4 excerpts resolved to source + locator (computed here; the fixture's 100.0 is a literal) | G3-prov |
| Revision correctness | object revisions pinned in `provenance_refs`; history policy includes predecessor revisions for Claim/Result | code + E5 |
| Full-text verification completeness | NOT MEASURED (literal string in fixture) | |
| Local cache reuse | identical-input reuse works (E0); see P1-4 for misses and false hits | |

**Interpretation.** Selective retrieval does shrink the prompt by roughly 78% in a fair comparison. But the smaller context omits the one competitor paper that matters, and the workflow misclassifies the contradictory evidence it does keep. Coverage, not compression, is the limiting factor. Nothing supports any claim of paid-API savings.

---

## 7. Scientific reliability evaluation

| Category | Cases |
|---|---|
| **Evidence-grounded** | Passage provenance in the Literature matrix (C2d/C2e). Result revision diffs and `depends_on` affected-claim tracing (E5, Results). Explicit unknown RQ/Claim → `KeyError` (D5a). |
| **Provisional (correctly so)** | Causal claim without design → `IDENTIFICATION_NOT_VERIFIED` (C1a). No papers → `NOVELTY_NOT_VERIFIED` (C1e). Missing design/sample → `UNKNOWN`/`NOT_REPORTED`. Missing seed/script → `None` (C4a). Abstract-only stays abstract (D4). |
| **Unsupported** | Methods specification template (C3-*). "Institutional frictions" inference (C5). "Empirically corroborated formulation" (C5-5, D2). Own hypothesis labelled as literature competitor (C1g). |
| **Incomplete but reported complete** | Literature/Discussion with zero evidence → `SUCCESS` (C2f, D2). Source dropped with `budget_status=OK` (D3b). Incomplete output → `REUSED` (E-incomplete). Model output with no schema → `SUCCESS` (B3e/B3f). |
| **Incorrect** | Negative-as-predicted → challenge (C5-1). Non-significant → falsification (C5-2). "regression" → contradiction (C5-3). "not consistent with" → supports (C2b). "identification is NOT credible" → identified (C1b). Self-attested computation → verified (C4c). Foreign-RQ claim as candidate (D5b). Anticipating competitor → supporting (C1d). |

---

## 8. Test coverage gap analysis

The existing tests pass, but most of them assert only that a particular *old* defect phrase is absent.

| Existing test | What it actually asserts | Claim it is cited for | Gap |
|---|---|---|---|
| `test_i7` | fingerprint changes when the module function `load_skill_instructions` is monkeypatched | "SKILL.md participates in model-backed execution" | never calls a model adapter; never edits a file (memoization not exercised) |
| `test_i3` | a phrase is absent; `len(evidence_matrix) >= 2` | false consensus prevented | does not check `relationship` values; would not catch C2b |
| `test_i4` | status ∈ {CANDIDATE_METHOD, …} | no default methods | does not check FE/controls/clustering defaults |
| `test_i5` | `deterministic_verification is False or …` | no fabricated verification | does not test caller flag (C4c) |
| `test_i9` | `budget_exceeded` flag is set | budget includes full prompt | does not assert the prompt fits the budget |
| `test_i10` | `reopens == True` for a Result with `contradictory: True` and "persist" | handoff works | no false-positive cases; no check of what Contribution does with it |
| `test_i11` | `status in {"SUCCESS","CONTEXT_PREPARED"}`, then cache reuse | artifact persistence across restart | same process; never deletes cache; never queries Research Core; `SUCCESS` here is itself defect C2f |
| `test_i12` | `usage_source in {"estimated","offline"}` | honest token accounting | passes only without `tiktoken`; the cache assertion uses `or` |
| — | — | Benchmark numbers | `run_scientific_benchmark_evaluation` never executed by tests |
| — | — | Model adapter | **no test references `ScriptedModelAdapter` or `model_adapter`** |

### Required independent reproductions (Part I)

Command for all items, run from the repo root:
```bash
PYTHONIOENCODING=utf-8 PYTHONPATH=. uv run --no-project --isolated --python 3.12 --with jsonschema python .tmp/audit_9_1_1/repro.py
```

| # | Reproduction | Expected | Observed | Source | Verdict |
|---|---|---|---|---|---|
| 1 | ScriptedModelAdapter via `run_workflow` (×5) | request reaches adapter | `TypeError: … unexpected keyword argument 'prompt'`; 0 requests | dispatcher.py:224-228 | **FAIL** |
| 2 | Skill content in model request | only contribution body | present, others absent; **only reachable with a non-shipped duck adapter** | contracts.py:172-173 | **PASS** (assembly) / **FAIL** (shipped path) |
| 3 | Provider usage handling | adapter usage recorded | adapter `TaskUsage` discarded; fabricated `output_tokens=50` labelled `provider` | dispatcher.py:229-244 | **FAIL** |
| 4 | Invalid model output | rejected or INVALID | `SUCCESS` with `raw_output` | dispatcher.py:248-265 | **FAIL** |
| 5 | Budget overflow, model-backed | not sent / INCOMPLETE | sent at 737 vs 200; `SUCCESS` | dispatcher.py:223-265 | **FAIL** |
| 6 | Negative coefficient consistent with H1 | consistent | reopens contribution; "overturned by frictions" | discussion.py:87-108 | **FAIL** |
| 7 | Non-significant ≠ contradiction | inconclusive | "does not conform"; reopens | discussion.py:87-108 | **FAIL** |
| 8 | Full-text policy invalidates cache | miss | miss | dispatcher.py:79-82 | **PASS** |
| 9 | Model configuration invalidates cache | stable per config, changes with model id | unstable per instance; unchanged on model_id change | dispatcher.py:71-72 | **FAIL** |
| 10 | Artifact recovery without JSON cache | via Research Core | 0 core references; orphan per `recover()`; recompute | dispatcher.py:276-283 | **FAIL** |
| 11 | Results → Discussion → Contribution | meaningful revision | runs, consumes upstream; a *confirming* result becomes a "contradictory finding"; no Claim revision (`repro2.py handoff`) | contribution.py:56-67; discussion.py:87-108 | **PARTIAL** |
| 12 | Benchmark computed, not hard-coded | computed | 3 literal fields; prompt vs JSON mismatch | benchmark_fixture.py:487-502 | **FAIL** |

Supplementary commands:
```bash
PYTHONIOENCODING=utf-8 PYTHONPATH=. uv run --no-project --isolated --python 3.12 --with jsonschema python .tmp/audit_9_1_1/repro2.py handoff
```
```bash
PYTHONIOENCODING=utf-8 PYTHONPATH=. uv run --no-project --isolated --python 3.12 --with jsonschema --with tiktoken python .tmp/audit_9_1_1/repro2.py tokenizer
```
Full per-check output is in `.tmp/audit_9_1_1/repro_out.txt` and `repro_results.json`.

---

## 9. Minimal remediation plan

Ranked by research impact. Each step is a local change to existing files; no new agents or abstraction layers are needed.

1. **Make one model call work end to end** (P0-1, P0-2, P1-7). Fix the call signature and tuple unpacking in `dispatcher.py`. Put the workflow's output fields (or JSON schema) in the system prompt. Validate the result. Map `budget_status != OK` to `INCOMPLETE_CONTEXT` or refuse to send. Remove the silent `except: pass` around persistence. Add five `ScriptedModelAdapter` tests through `run_workflow`. *This step is what turns the PR into model-backed research assistance at all.*
2. **Remove keyword verdicts** (P0-3). In `discussion.py`, `literature.py`, `contribution.py` and `context.py:222-227`, direction and support come only from structured data: edge relations and Result fields (`hypothesis_id`, `expected_sign`, `estimate`, `p_value`). Otherwise output `NOT_ASSESSED` and leave the judgement to the model step. Delete the "institutional frictions" and "Empirically corroborated" templates.
3. **Fix selection scope** (P0-4, P0-5). Add `Source` and passages to the contribution and discussion expansion. Scope claims to the RQ graph when no focus is given. Drop the `claims[0]` default.
4. **Status-preserving cache** (P0-6, P1-4). Store and return the original status. Do not reuse incomplete or failed runs. Fingerprint the adapter by an explicit descriptor (`model_id`, temperature, max tokens). Hash upstream *results*, not `to_dict()`. Key the skill memo by file digest or mtime.
5. **Register runs in the Research Core** (P1-1). One append-only table written next to the CAS put, with a `parent_run_id` so a reopened Contribution keeps its earlier version.
6. **Enforce the budget** (P1-2). Reserve skill, schema and header tokens; fit or refuse; render each passage once; treat a dropped RQ-linked Source as INCOMPLETE; give a preview plus a locator instead of dropping the Source.
7. **Methods and Results honesty** (P0-7, P0-8, P1-8). Replace the hard-coded specification with "required information". Make verification depend on recorded script, data and output hashes. Label author summaries `AUTHOR_REPORTED`.
8. **Honest metrics** (P1-3, P1-5). Set `usage_source="estimated"` whenever `llm_calls == 0`. Compute provenance and case counts. Compare prompt to prompt. Report classification recall alongside retention. Call the benchmark from a test.
9. **Correct the acceptance and alignment docs** (P2-1). Remove the claims about `workflow_runs`, `CONTEXT_PREPARED`, `EXCERPT_ONLY` and tool disclosure, or implement them.

Steps 1–4 are the minimum before any workflow output should be trusted. Steps 5–6 are the minimum before the system can claim reproducible, token-bounded research.

---

## 10. Acceptance matrix

| Gate | Criterion | Verdict | Basis |
|---|---|---|---|
| **A** | Deep Agents architecture correctly understood and adapted | **PARTIAL** | Upstream described accurately. Skill selection, on-demand full text and structured handoff adapted. Budget enforcement, paging/eviction and the model path missing. Several doc claims have no code (§3). |
| **B** | Five workflows perform meaningful scientific tasks | **FAIL** | Methods and Discussion are templates or keyword logic; Contribution blind to competitors; §4. |
| **C** | SKILL.md genuinely participates in model-backed execution | **FAIL** | Body reaches the prompt, but no shipped adapter can be called (B1). Deterministic executors ignore SKILL.md. |
| **D** | Context completeness and token budgets enforced | **PARTIAL** | Unknown focal objects rejected; full-text statuses correct. Budget detected, not enforced; Source dropped with status OK; cross-RQ leakage (D3, D3b, D5b). |
| **E** | No unsupported scientific inference from lexical shortcuts | **FAIL** | C1b, C1d, C2b, C5-1/2/3/5, G3-contra. |
| **F** | Cache reuse and invalidation scientifically correct | **FAIL** | Incomplete reused as `REUSED`; unstable model fingerprint; same-process skill false hit; avoidable misses (§P1-4). Revision-based invalidation itself works (E2, E3, E5, E8). |
| **G** | Workflow output provenance and persistence reliable | **FAIL** | No Research Core registration; outputs orphaned; not recoverable without cache filenames (F1–F4). |
| **H** | Token usage and benchmark metrics honestly measured | **FAIL** | Hard-coded benchmark fields; JSON-vs-snapshot comparison; zero-call runs labelled `provider` with tiktoken; fabricated model output tokens. |
| **I** | Cross-workflow scientific reasoning is functional | **PARTIAL** | Handoff executes and is consumed, but propagates keyword misreadings; no revised Contribution is recorded; not reachable from the CLI. |
| **J** | Existing Research Core compatibility preserved | **PASS** | 154/154 on 3.10, 3.11, 3.12 locally; CI green on `9728134`; core schema unchanged. |

**Bottom line:** the path *Research Question → Evidence → Contribution → Methods → Results → Discussion → Revised Contribution* can be traversed mechanically through the Python API, but not reliably. Evidence provenance and revision history are preserved at the object level. Scientific meaning is not: it is decided by keywords and templates. Workflow results are not registered as reproducible artifacts in the Research Core. Token use is estimated but not bounded, and no model call works. The smallest set of changes that closes the gap is steps 1–4 of §9.

---

## Appendix A — Reproduction inventory

`repro.py` sections:
- **B1–B3h:** model adapter path.
- **C1a–C1g:** Contribution.
- **C2a–C2f:** Literature.
- **C3-\*:** Methods.
- **C4a–C4c:** Results.
- **C5-\*:** Discussion.
- **D1–D5b:** context and budget.
- **E0–E10:** cache.
- **F1–F4:** persistence; F2 runs in a separate `python -I` process, reopens the project with `StateStore.open`, deletes that fixture's `workflow_cache`, and calls `store.recover()`.
- **G1–G3:** benchmark.

All fixtures are created in throwaway projects under the script's `projects/` folder. No user research data was touched.

`repro2.py`:
- `handoff`: Results → Discussion → Contribution with a confirming negative coefficient.
- `tokenizer`: `usage_source` with `tiktoken` installed.
