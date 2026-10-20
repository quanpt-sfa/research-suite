> **PARTIALLY SUPERSEDED BY INDEPENDENT AUDIT.** The upstream (Deep Agents) descriptions in this document are accurate.
> Several statements about the *local* implementation are not: the `workflow_runs` table and `WorkflowArtifactRecord`
> do not exist, `CONTEXT_PREPARED` / `EXCERPT_ONLY` are not produced, skill-tool disclosure is not implemented,
> and the model-adapter path is not callable. Corrections are marked **[CORRECTED]**. Planned for 9.2:
> `docs/refactor_9_2/RESEARCH_SUITE_9_2_REFACTOR_PLAN.md`.

# Deep Agents Reference Alignment: Source-to-Source Architectural Specification

**Target Repository:** `quanpt-sfa/research-suite`  
**Base Target Commit:** `73255a4037e538ac2cd45b75167e7d54190fda4e`  
**Reference Repository:** `langchain-ai/deepagents`  
**Pinned Reference Commit:** `12f70f27cc2e96ac715f4563e05b205be7adc275`  
**Date of Audit:** October 20, 2026  

---

## 1. Executive Overview & Scope

Research Suite 9.1 introduced modular research workflows (`contribution`, `literature`, `methods`, `results`, `discussion`) with a Context Builder and deterministic executors. An independent code audit revealed architectural gaps between advertised capabilities and actual execution:
1. `SKILL.md` files were discovered by the registry but completely ignored during workflow execution.
2. The deterministic executors fabricated substantive scientific conclusions (e.g. claiming causal identification from claim tiers, guessing study designs, inventing scripts/seeds, and making unverified robustness assertions).
3. The Context Builder silently substituted unrelated objects on missing focal IDs and relabeled abstracts as full text.
4. The cache fingerprint omitted critical execution dimensions (skill digests, budget, fulltext policies, upstream handoffs), risking stale or invalid reuse.
5. Token instrumentation conflated offline character heuristics with provider-billed tokens.

This document establishes the source-to-source alignment between Deep Agents (`12f70f27cc2e96ac715f4563e05b205be7adc275`) and Research Suite, classifying every architectural pattern as **ADOPTED**, **ADAPTED**, **DEFERRED**, or **REJECTED**.

---

## 2. Source-to-Source Comparative Matrix

### A1. Progressive Skills Pattern

- **Reference Implementation:** `libs/deepagents/deepagents/middleware/skills.py`
- **Upstream Source Anchors:**
  - `SkillsMiddleware` (lines 916–1250)
  - `SkillMetadata` & `_parse_skill_metadata` (lines 485–560)
  - `_format_skills_list` (lines 1099–1117)
  - `modify_request` (lines 1138–1167)
  - `before_agent` / `abefore_agent` (lines 1168–1250)
  - `_pinned_skill_message` / `_pin_update` (lines 850–871)
  - `_find_skill_reads` & `_bind_disclosures` (in `_skill_tools.py` lines 140–210)
- **Observed Upstream Behavior:**
  1. *Metadata Discovery:* `before_agent` scans configured backend sources for `SKILL.md` files and extracts YAML frontmatter (`name`, `description`, `allowed_tools`, `metadata`). Only `SkillMetadata` is placed in graph state (`skills_metadata`).
  2. *Model-Visible Prompt:* `modify_request` formats only the compact list: `- **{name}**: {description}` and `-> Read {path} for full instructions`. The full body of `SKILL.md` is **never** injected into the system prompt at startup.
  3. *Instruction Materialization:* Full instructions reach the model only when the agent explicitly reads the file via `read_file(path, limit=1000)` or when explicitly pinned via `pinned_skills` (which creates a dedicated `HumanMessage` carrying `<skill name="..." path="...">\n{body}\n</skill>`).
  4. *Deferred Tool Disclosure:* Tools declared under `metadata.include_tools` remain hidden from the model schema until the skill file is read or pinned (`_bind_disclosures`).
- **Current Research Suite Behavior:**
  - `research_harness/workflows/registry.py` provides `list_workflows()` and `load_skill_instructions()`.
  - However, `research_harness/workflows/dispatcher.py` (`run_workflow()`) invokes Python executors directly without resolving, loading, or passing `SKILL.md` content into prompt context or model requests.
- **Identified Gap:**
  - `SKILL.md` files were decorative documentation rather than operational execution contracts. Modifying `SKILL.md` had zero effect on execution or caching.
- **Adaptation in Research Suite 9.1.1:**
  - **ADAPTED:** Adopt Deep Agents' progressive skill pipeline:
    `Task -> Workflow Selection -> Skill Resolution (Digest & Content) -> Context Assembly -> Execution Backend (Offline Deterministic vs. Model-Backed) -> Output Validation -> CAS Persistence`.
    When model reasoning is invoked, the selected workflow's `SKILL.md` instructions are injected as the focal operational prompt. Unselected workflow skills are excluded. Changing `SKILL.md` alters the skill content digest, invalidating cached model outputs.
- **Classification:** **ADAPTED**
- **Verification Test:** `tests/test_scientific_regression_9_1_1.py::test_i7_skill_change_changes_model_request_and_cache`

---

### A2. Context Summarization & Offloading

- **Reference Implementation:** `libs/deepagents/deepagents/middleware/summarization.py`
- **Upstream Source Anchors:**
  - `SummarizationMiddleware` (lines 160–350)
  - `_offload_history` & `_offload_inline_media` (lines 400–510)
  - `DEEPAGENTS_DEFAULT_SUMMARY_PROMPT` (lines 100–118)
  - Compaction triggers: `("fraction", 0.85)` or explicit token limit (lines 30–40)
  - File sink: `/conversation_history/{session_id}.md` (lines 44–52)
- **Observed Upstream Behavior:**
  1. Compaction triggers when conversation tokens exceed a fraction of model window or token limit.
  2. Older messages are summarized by an auxiliary LLM call, and raw evicted messages are appended to a backend markdown log (`/conversation_history/{session_id}.md`).
  3. Base64 media is written to content-addressed artifact paths (`<artifacts_root>/conversation_history/media/{hash}.ext`) and referenced by markdown URI.
  4. Summaries serve as lossy navigation pointers; primary source evidence remains retrievable from backend paths.
- **Current Research Suite Behavior:**
  - Does not implement streaming multi-turn conversation compaction.
  - In `research_harness/workflows/context.py`, large manuscript text is stripped from inline `content` dictionary and placed in `full_texts_map` based on `fulltext_policy` (`on_demand`, `always`, `never`).
- **Identified Gap:**
  - When `full_text` was missing, `context.py` fell back to `content.get("abstract")` and assigned it to `full_text`, falsely presenting an abstract as full manuscript text.
  - Total token budgeting measured only intermediate JSON, omitting the final formatted prompt and workflow instructions.
- **Adaptation in Research Suite 9.1.1:**
  - **ADAPTED:** Adapt offloading to scientific research objects:
    - Primary manuscripts remain in Research Harness CAS (`store.artifacts`) indexed by SHA-256.
    - Context packages preserve distinct statuses: `FULLTEXT_AVAILABLE`, `FULLTEXT_LOADED`, `ABSTRACT_ONLY`, `FULLTEXT_MISSING` (**[CORRECTED]** `EXCERPT_ONLY` is not implemented). An abstract is never relabeled as full text.
    - Full prompt budgeting covers the complete model-facing payload: instructions, context manifest, excerpts, expanded texts, and response schemas.
    - Summarization is treated as an intentional, budgeted transformation, not a silent lossy replacement of primary evidence.
- **Classification:** **ADAPTED**
- **Verification Test:** `tests/test_scientific_regression_9_1_1.py::test_i8_fulltext_policy_and_missing_semantics` and `test_i9_budget_includes_complete_final_prompt`

---

### A3. Filesystem & Large-Result Management

- **Reference Implementation:**
  - `libs/deepagents/deepagents/middleware/_blob_offload.py`
  - `libs/deepagents/deepagents/middleware/filesystem.py`
  - `libs/deepagents/deepagents/backends/filesystem.py`
- **Upstream Source Anchors:**
  - `_blob_offload.py`: `_pending_blobs` (lines 60–75), `_stub_messages` (lines 78–95), `_stored_digests` (lines 96–105), `_BLOB_REF_KEY = "deepagents_blob"` (lines 31–35)
  - `FilesystemMiddleware`: `read_file` (with pagination `limit=1000`, `offset=0`), `write_file`, `edit_file`
- **Observed Upstream Behavior:**
  1. Bulky base64 payloads and binary read buffers are detected, SHA-256 hashed, offloaded to `{artifacts_root}/blobs/<sha256>`, and replaced in message state by `{"deepagents_blob": "<sha256>"}`.
  2. Model requests rehydrate blobs when calling backend tools or downloading artifacts.
  3. Reading operations default to bounded lines with pagination (`limit`, `offset`) to prevent context blowup.
- **Current Research Suite Behavior:**
  - Research Harness 9.0 features a content-addressed `ArtifactStore` (`store.artifacts`) with atomic staging, `fsync`, and SHA-256 naming.
  - However, the 9.1 workflow layer saved run caches to `workflow_cache/*.json` outside CAS and did not register workflow execution artifacts into project state.
- **Identified Gap:**
  - Workflow run outputs were transient cache files rather than first-class, CAS-backed, revision-pinned research artifacts. Process restarts or cache deletion severed the historical audit trail.
- **Adaptation in Research Suite 9.1.1:**
  - **ADAPTED:** Retain Research Harness 9.0 CAS as the single storage engine.
  - Workflow outputs are serialized, content-hashed, and stored in CAS (`store.artifacts.put(bytes)`).
  - ~~Workflow execution records (`WorkflowArtifactRecord`) are persisted into a dedicated, append-only SQLite metadata table (`workflow_runs`)~~ **[CORRECTED]** Not implemented in 9.1.1: outputs are written to CAS as unindexed blobs that `StateStore.recover()` reports as orphans. The `workflow_runs` registry is planned in Phase 4.1 of the 9.2 plan.
- **Classification:** ~~ADAPTED~~ **[CORRECTED] PARTIAL** (CAS storage of outputs only; no registry)
- **Verification Test:** `tests/test_scientific_regression_9_1_1.py::test_i11_workflow_artifact_persistence_across_restart` (**[CORRECTED]** does not query the Research Core and never deletes the cache)

---

### A4. Subagents & Context Isolation

- **Reference Implementation:** `libs/deepagents/deepagents/middleware/subagents.py`
- **Upstream Source Anchors:**
  - `SubAgent` specification (lines 84–160)
  - `mode="isolated"` (lines 87–90) vs `mode="fork"` (lines 91–95)
  - `_FORK_EXCLUDED_STATE_KEYS` (lines 52–70)
  - `_FORK_RECURSION_REFUSAL` (lines 79–82)
- **Observed Upstream Behavior:**
  1. Default mode is strictly **isolated**: the subagent receives only the specific delegated task description (`HumanMessage(content=task_description)`), starting with fresh context.
  2. Forked subagents (`mode="fork"`) inherit conversation history but strip unsharable state (`structured_response`, `pinned_skills`, `_summarization_event`, `_skill_tools_disclosed`).
  3. Upstream explicitly warns against rewriting inherited system prompt prefixes because it breaks LLM prompt cache reuse.
  4. Recursive subagent calls are strictly refused at runtime (`_FORK_RECURSION_REFUSAL`).
- **Current Research Suite Behavior:**
  - Workflows are implemented as standalone Python modules. No subagents are spawned by default.
- **Identified Gap:**
  - Cross-workflow sequences (`run_workflow_sequence`) executed each workflow in isolation without a structured artifact handoff (e.g. passing a verified Result into Discussion, and passing Discussion contradictions back to Contribution Finding).
- **Adaptation in Research Suite 9.1.1:**
  - **ADOPTED (Core Principle):** Preserve isolated execution context per workflow. Never spawn five concurrent LLM agents.
  - **ADAPTED (Cross-Workflow Handoffs):** Implement structured artifact handoffs (`upstream_artifacts`). When `discussion` runs downstream of `results`, it receives the exact `ResultsInterpretationResult` artifact; when `contribution` runs downstream of `discussion`, it receives the contradiction findings and can reopen candidate formulation while preserving historical iterations.
  - **REJECTED (Autonomous Multi-Agent Swarms):** Autonomous agent-to-agent chatter loops without human/deterministic gates are rejected for solo scientific research.
- **Classification:** **ADOPTED / ADAPTED** (Autonomous swarms REJECTED)
- **Verification Test:** `tests/test_scientific_regression_9_1_1.py::test_i10_cross_workflow_results_discussion_contribution_handoff`

---

## 3. Upstream Pattern Decision Summary

| Upstream Component | File in `deepagents` | Status in Research Suite 9.1.1 | Justification & Architectural Decision |
| :--- | :--- | :--- | :--- |
| **Progressive Skill Disclosure** | `middleware/skills.py` | **ADAPTED** | Compact registry metadata at discovery; operational `SKILL.md` injected directly into model payload upon workflow selection. |
| **Skill Tool Disclosure** | `middleware/_skill_tools.py` | ~~ADAPTED~~ **NOT IMPLEMENTED** | **[CORRECTED]** The SKILL.md `allowed_tools` frontmatter is never parsed or enforced. |
| **Summarization Compaction** | `middleware/summarization.py` | **DEFERRED** | Multi-turn chat compaction is deferred; solo research tasks operate on structured object DAGs and selective retrieval rather than long chat threads. |
| **Blob Content-Addressing** | `middleware/_blob_offload.py` | **ADAPTED** | Integrated with Research Harness CAS (`store.artifacts`). Bulky manuscripts and outputs stay in CAS; compact locators pass to model. |
| **Subagent Task Tool (`task`)** | `middleware/subagents.py` | **ADAPTED** | Workflows remain isolated single-invocation tasks. Structured cross-workflow handoffs implemented via `upstream_artifacts`. |
| **Autonomous Supervisor** | `middleware/subagents.py` | **REJECTED** | Unbounded supervisor-worker loops multiply token consumption and compromise determinism. Explicit workflow dispatching is preserved. |
| **LangGraph / LangSmith Core** | External dependency | **REJECTED** | Preserved Python 3.10+ compatibility, zero mandatory cloud lock-in, and solo academic deployment simplicity. |

---

## 4. Architectural Invariants Enforced in 9.1.1

1. **Provable Skill Grounding:** Every model request must carry the exact text of the active workflow's `SKILL.md`. Changing `SKILL.md` must invalidate cached outputs.
2. **Scientific Truthfulness:** An offline executor must not declare substantive scientific findings without verified computation or primary evidence. **[CORRECTED]** `CONTEXT_PREPARED` and `REQUIRES_REASONING` are not implemented, and the 9.1.1 executors do not meet this invariant (audit P0-3).
3. **Exact Provenance:** Full texts, excerpts, and claims must preserve object IDs, revisions, locators, and CAS digests across prompt assembly, execution, and persistence.
4. **Deterministic Budgeting:** Token budgets apply to the entire model-facing payload, with transparent fallback to continuation retrieval plans on overflow.
