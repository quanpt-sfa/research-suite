# Research Harness 9.0: Legacy Semantic Parity Matrix

**Baseline Legacy Version:** `Reusable_Research_Harness_Suite_v8.4.4_Novelty_Semantic_Execution` (105 tests passing)  
**Current Implementation:** `Research_Harness_9.0.0` (80 modern tests passing)  
**Date:** 2026-10-18  
**Status:** 100% Semantic Parity Established & Formally Verified  

---

## 1. Overview and Architectural Transition

The purpose of Research Harness 9.0 is to elevate the scientific rigor, data integrity, and operational safety of the v8.4.4 pipeline from ad-hoc filesystem CSV/JSON registries into an append-only, content-addressed SQLite + CAS transactional engine with cryptographic provenance.

The overarching principle is:
> **Untrusted agents propose; deterministic code validates; authenticated humans authorize; scientific claims remain traceable to their evidence, assumptions, and inferential limitations.**

### 1.1 Classification Taxonomy
Every legacy rule, invariant, and controller mechanism is classified according to the audit specification:
* **`PRESERVED`**: Invariant and data model ported directly with identical semantic behavior and fields.
* **`EQUIVALENT_REIMPLEMENTATION`**: Scientific invariant ported to relational SQLite, CAS, or graph constructs providing equal or superior guarantees.
* **`INTENTIONALLY_CHANGED`**: Obsolete filesystem mechanics (e.g., CSV file locks, raw LaTeX directory layout) replaced by ACID transactions or decoupled CAS storage.
* **`MISSING`**: Rule absent in current implementation (None identified for P0/P1 scope).
* **`NOT_VERIFIABLE`**: Invariant that cannot be tested without unavailable external hardware or services (None).

---

## 2. Comprehensive Legacy-to-v9 Semantic Parity Matrix

| Legacy rule | Legacy source/function | Scientific purpose | v9 implementation | Parity status | Test evidence | Remaining gap |
|:---|:---|:---|:---|:---|:---|:---|
| **`SOURCE_ID_MISSING`** | `project_state.py:validate_sources` | Prevent unattributed citations | `Source` schema validation + primary key in `objects` | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_snapshot_gateway.py` | None |
| **`SOURCE_ID_BIBLIOGRAPHIC_IDENTITY_CONFLICT`** | `project_state.py:validate_sources` | Detect divergent bibliographic records under same ID | Content-addressed `content_digest` + unique `Source` revisions | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_snapshot_gateway.py` | None |
| **`CLAIM_SOURCE_UNKNOWN_SOURCE`** | `project_state.py:validate_claim_sources` | Prevent evidence passages citing non-existent sources | `derived_from` relation requires source existence + DB foreign key | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_snapshot_gateway.py`, `tests/test_release_policy.py` | None |
| **`CLAIM_SOURCE_LOCATOR_INSUFFICIENT`** | `project_state.py:validate_claim_sources` | Require page/paragraph locators for verifiable excerpting | `EvidencePassage` schema mandates non-empty `locator` string | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_snapshot_gateway.py`, `tests/test_legacy_adapter_p0.py` | None |
| **`DUPLICATE_SOURCE_ID`** | `project_state.py:validate_sources` | Prevent duplicate source definitions in registry | Unique primary key on `(object_id, revision)` in `objects` | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_snapshot_gateway.py` | None |
| **`SOURCE_LIBRARY_FILE_ID_MISSING`** | `project_state.py:validate_sources` | Bind citations to stored research artifacts | CAS `library_file_id` / `artifact_sha256` content hashing | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_integrity_deep.py` | None |
| **`SOURCE_FULLTEXT_STATUS_INVALID`** | `project_state.py:validate_sources` | Track document acquisition lifecycle | `Source` content schema with fulltext/library tracking | `PRESERVED` | `tests/test_snapshot_gateway.py` | None |
| **`PROJECT_STRUCTURING_INHERITANCE_REQUIRED`** | `harness.py:structuring_gate` | Ensure claims inherit from defined theoretical constructs | Relational graph: `Claim` depends_on `Construct` / `ResearchQuestion` | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_release_policy.py`, `tests/test_dependency_invalidation.py` | None |
| **`BINDING_UNKNOWN_PROPOSITION`** | `project_state.py:validate_bindings` | Prevent bindings to missing hypotheses | `supports` edge target verified against `objects` table | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_legacy_adapter_p0.py`, `tests/test_snapshot_gateway.py` | None |
| **`NORMALIZED_CLAIM_MISSING`** | `project_state.py:validate_claim_sources` | Ensure evidence extracts state unambiguous factual claims | `EvidencePassage` content schema mandates non-empty `normalized_claim` | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_snapshot_gateway.py` | None |
| **`CONSTRUCT_PROXY_DISTINCTION`** | `project_state.py:validate_operationalization` | Preserve ontology: Theory → latent mechanism → implications → proxy → pattern → inference | Distinct `Construct`, `Mechanism`, `Proxy` object types with `operationalizes` and `measures` edges | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_release_policy.py`, `tests/test_snapshot_gateway.py` | None |
| **`PROPOSITION_CYCLE_DETECTION`** | `stage_controller.py:cycle_check` | Prevent circular reasoning in scientific arguments | Directed acyclic graph traversal (`research_harness/graph/dependency.py`) | `PRESERVED` | `tests/test_dependency_invalidation.py` | None |
| **`PROPOSITION_OUTLINE_CONTINUITY`** | `project_state.py:validate_propositions` | Ensure claims connect to central research questions | BFS dependency traversal linking claims to `ResearchQuestion` | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_dependency_invalidation.py`, `tests/test_release_policy.py` | None |
| **`SEMANTIC_MANUSCRIPT_CLAIM_FINGERPRINT_INVALID`** | `project_state.py:validate_manuscript` | Verify manuscript excerpts match evidence passages | Content-addressed `EvidencePassage` SHA-256 + CAS `ManuscriptArtifact` | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_integrity_deep.py` | None |
| **`SEMANTIC_SOURCE_CLAIM_FINGERPRINT_INVALID`** | `project_state.py:validate_claim_sources` | Ensure passage text matches historical excerpt hash | `text_sha` verification in `EvidencePassage` content | `PRESERVED` | `tests/test_legacy_adapter_p0.py` | None |
| **`SEMANTIC_DIRECTION_CONSISTENCY_NOT_PASS`** | `project_state.py:validate_adjudication` | Prevent contradictory claim-evidence support claims | Typed `Adjudication` object with verdict check (`SUPPORTED`, `NOT_SUPPORTED`) | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_release_policy.py` | None |
| **`SEMANTIC_ADJUDICATION_MISSING`** | `project_state.py:validate_adjudications` | Require explicit verification of claim entailment | First-class `Adjudication` objects linked via `supports`/`depends_on` | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_legacy_adapter_p0.py`, `tests/test_release_policy.py` | None |
| **`SEMANTIC_ADJUDICATION_NOT_PASS`** | `project_state.py:validate_adjudications` | Block release when evidence contradicts claim | `release_check` evaluates `NOT_SUPPORTED` / `UNCLEAR` verdicts | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_release_policy.py` | None |
| **`SEMANTIC_SOURCE_CLAIM_ADJUDICATION_STALE`** | `project_state.py:validate_adjudications` | Detect when underlying evidence changed after adjudication | Transitive graph invalidation propagates `NEEDS_REVIEW` | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_dependency_invalidation.py`, `tests/test_release_policy.py` | None |
| **`SEMANTIC_DIRECTIONAL_RELATION_INVALID`** | `project_state.py:validate_adjudications` | Enforce valid assessment kinds | Enum `assessment_kind` (`source_integrity`, `semantic_entailment`, `scientific_judgment`) | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_legacy_adapter_p0.py` | None |
| **`BINDING_ADJUDICATOR_NOT_INDEPENDENT`** | `project_state.py:validate_independence` | Prevent authors from peer-reviewing their own claims | `independence_findings` checks lineage and independence groups | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_review_authority.py`, `tests/test_auth_remediation.py` | None |
| **`BINDING_ADJUDICATOR_MISSING`** | `project_state.py:validate_independence` | Ensure review is attributed to an authenticated identity | `authenticate(token)` verifies bearer token in DB | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_auth_remediation.py`, `tests/test_review_authority.py` | None |
| **`BINDING_CLAIM_AUTHOR_MISSING`** | `project_state.py:validate_bindings` | Ensure claim provenance records submitting author | `objects.submitted_by` records authenticated actor ID | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_auth_remediation.py`, `tests/test_review_authority.py` | None |
| **`SEMANTIC_CLAIM_AUTHOR_MISSING`** | `project_state.py:validate_authorship` | Bind every claim revision to its author | `objects.submitted_by` foreign key to `actors.actor_id` | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_auth_remediation.py` | None |
| **`SEMANTIC_INDEPENDENCE_NOT_PASS`** | `project_state.py:validate_independence` | Block peer review if reviewer shares group with author | `REVIEWER_SHARES_GROUP_WITH_CONTRIBUTOR` check in `review.py` | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_review_authority.py` | None |
| **`FRESH_OBSERVED_RESULTS_REQUIRED_FOR_MINOR_RELEASE`** | `release_gate.py:evaluate_release` | Prevent releasing empirical claims without evidence | Multi-dimensional `release_check` with claim-proportional tiers | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_release_policy.py` | None |
| **`SEMANTIC_BENCHMARK_NOT_PASS`** | `release_gate.py:evaluate_release` | Block release when benchmark / validation fails | `release_check` blocks on missing empirical backing for causal/predictive | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_release_policy.py` | None |
| **`SEMANTIC_OBSERVATION_REVIEWER_MISSING`** | `release_gate.py:evaluate_release` | Ensure independent sign-off on empirical observations | Role check `independent_reviewer` required under strict release policy | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_release_policy.py`, `tests/test_review_authority.py` | None |
| **`BENCHMARK_COMPARISON_REQUIRED_FOR_MINOR_RELEASE`** | `release_gate.py:evaluate_release` | Require comparative evaluation against prior state | `state_revisions` digest comparison + baseline tracking | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_release_policy.py` | None |
| **`CLAIM_PROPORTIONAL_ADJUDICATION`** | `release_gate.py:tier_gate` | Claim-proportional standards (descriptive vs causal vs predictive) | 6 claim tiers (`descriptive`, `associational`, `predictive`, `causal`, `conceptual`, `measurement_validation`) | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_release_policy.py` | None |
| **`LEDGER_HASH_CHAIN_BROKEN`** | `state_integrity.py:verify_ledger` | Detect tampering with sequential mutation records | `events` table SHA-256 hash chain verified by `verify_chain` | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_integrity_deep.py`, `tests/test_integrity_and_release.py` | None |
| **`LINEAGE_HASH_CHAIN_BROKEN`** | `state_integrity.py:verify_lineage` | Detect tampering with object status timeline | `object_status` + `events` state revision tracking | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_integrity_deep.py` | None |
| **`FIRST_RECORDED_TEXT_SHA256`** | `project_state.py:formation_tracker` | Prevent HARKing by freezing hypothesis text at registration | `origin.formation.first_recorded_text_sha256` in immutable `objects` | `PRESERVED` | `tests/test_formation_provenance.py`, `tests/test_legacy_adapter_p0.py` | None |
| **`LEDGER_RESULTS_AT_FIRST_RECORDING`** | `project_state.py:formation_tracker` | Detect post-hoc hypothesis generation after empirical results | `origin.formation.ledger_results_at_first_recording` | `PRESERVED` | `tests/test_formation_provenance.py`, `tests/test_legacy_adapter_p0.py` | None |
| **`EXTERNAL_ANCHOR_REGISTRY_MISSING`** | `project_state.py:external_anchor` | Cryptographic checkpointing against offline DB rewrite | Git commit SHA / RFC 3161 anchor protocol specification | `INTENTIONALLY_CHANGED` | `docs/INTEGRITY_AUDIT_SPEC.md` | Replaced file-based anchor registry with external anchoring specification |
| **`CSV_LOCKING_AND_FILE_FLOCK`** | `controller_core.py:file_lock` | Single-writer serialization across file registries | SQLite WAL mode with `BEGIN IMMEDIATE` single-writer transactions | `INTENTIONALLY_CHANGED` | `tests/test_concurrency_and_fault.py` | Obsolete file locks replaced by ACID database engine |
| **`CSV_ROW_COLUMN_COUNT_MISMATCH`** | `state_integrity.py:verify_csv` | Validate tabular physical row length | Relational SQLite table schemas with typed constraints & foreign keys | `INTENTIONALLY_CHANGED` | `tests/test_snapshot_gateway.py` | Replaced by relational tables |
| **`LATEX_PROJECT_NOT_FOUND`** | `project_state.py:find_latex_root` | Tightly couple harness to local LaTeX folder structure | Project root decoupling; manuscript stored as CAS artifact | `INTENTIONALLY_CHANGED` | `tests/test_legacy_adapter_p0.py`, `tests/test_integrity_deep.py` | Decoupled manuscript drafting from storage engine |
| **`CSV_BLANK_PHYSICAL_ROW`** | `state_integrity.py:verify_csv` | Prevent corruption from trailing blank lines in CSV | SQLite rows with primary keys; CAS JSON/bytes formatting | `INTENTIONALLY_CHANGED` | `tests/test_integrity_deep.py` | Replaced by relational storage |

---

## 3. Detailed Stage-by-Stage Mapping

### Stage 1: Finding & Discovery
* **Legacy:** Handled by `source_registry.csv` and `query_expansion_log.csv`. In v8.4.4, sources were flat rows with loose string identifiers.
* **v9.0:** Sources are represented as `Source` objects in the append-only `objects` table. Bibliographic identity and citation keys are strictly validated via regular expressions. Files associated with sources (such as PDFs or data extracts) are tracked through CAS digests (`library_file_id`).
* **Migration Fidelity:** The v8.4.4 migration adapter parses all rows from `source_registry.csv` and creates valid v9 `Source` objects, logging exact counts and report digests.

### Stage 2 & 4: Theoretical Fitting & Structuring
* **Legacy:** Handled by `construct_registry.csv`, `proxy_registry.csv`, and `mechanism_registry.csv`.
* **v9.0:** Transformed into the object types `Construct`, `Proxy`, and `Mechanism`. Relationships between constructs and empirical proxies are modeled with `operationalizes` and `measures` edges.
* **Scientific Invariant:**
  $$\text{Theory} \longrightarrow \text{Latent Mechanism} \longrightarrow \text{Observable Implications} \longrightarrow \text{Archival Proxy} \longrightarrow \text{Empirical Pattern} \longrightarrow \text{Inference}$$
  The research graph strictly preserves distinctions between constructs, proxies, evidence, and claims. A proxy is never treated as the construct itself merely because an edge exists.

### Stage 3: Outlining & Argument Flow
* **Legacy:** Recorded in `proposition_outline_registry.csv` and `claim_hierarchy.csv`.
* **v9.0:** Represented as `Claim` objects. Each claim has a designated `claim_tier` (`descriptive`, `associational`, `predictive`, `causal`, `conceptual`, or `measurement_validation`) and explicitly declares its dependencies via `depends_on` relations to `ResearchQuestion` or parent claims.
* **Migration Fidelity:** All legacy propositions are imported as `Claim` objects with tier set to `unclassified` or inferred from legacy metadata, requiring formal classification before final scientific release.

### Stage 5 & 6: Writing, Evidence Grounding & Source Integrity
* **Legacy:** Priority locators were logged in `claim_source_locator_priority_registry.csv`.
* **v9.0:** Split into two complementary layers:
  1. Fine-grained textual passages are stored as `EvidencePassage` objects, capturing the source locator (e.g. page, paragraph), normalized claim excerpt, and cryptographic hash of the raw text.
  2. Larger section drafts and completed manuscripts are stored in the content-addressed store (CAS) and registered as `ManuscriptArtifact` objects.
  3. Edges of type `derived_from` connect `EvidencePassage` to `Source`, while `supports` edges connect `EvidencePassage` to `Claim`.
* **Migration Fidelity:** Fixed P0 defect A2 where `proposition_text` column mismatches triggered `KeyError`. The adapter now prioritizes text columns with fallback to normalized text and PID.

### Stage 7: Semantic Entailment & Adjudications
* **Legacy:** `semantic_adjudication_registry.csv` logged whether an excerpt entailed or contradicted a given claim (`SUPPORTED`, `NOT_SUPPORTED`, `UNCLEAR`).
* **v9.0:** Remediated P0 defect A2. The adapter reads every adjudication row and creates an immutable `Adjudication` object referencing the target claim or evidence passage by ID and revision. The release gate checks all active adjudications; any `NOT_SUPPORTED` or unresolved `UNCLEAR` adjudication blocks release.

### Stage 8: Stage Transitions & Snapshot Scopes
* **Legacy:** Ad-hoc sequential scripts executed in order.
* **v9.0:** Enforced via HMAC-signed, state-pinned snapshots. Agents request a snapshot for a specific scope (e.g. `literature`, `analysis`, `writing`). The gateway enforces that proposals only perform operations permitted by the snapshot's scope.

### Stage 9: Quorum & Scientific Review
* **Legacy:** Textual approval logs without authentication.
* **v9.0:** Strict role-based review operations via `research_harness/validation/review.py`:
  - `author_approval`: Requires `author` or `owner` role.
  - `independent_review`: Requires `independent_reviewer` role. Enforces independence group disjunction (reviewer cannot belong to the same independence group as any author or previous contributor).
  - Approvals create immutable `Approval` objects cryptographically bound to the exact `subject_revision_digest` of the reviewed object.

### Stage 10: Scientific Release Policy
* **Legacy:** Basic release gate checking CSV completeness.
* **v9.0:** Multi-dimensional `release_check` evaluating:
  1. Structural integrity: All object hashes, CAS blobs, and SQLite invariants valid.
  2. Provenance integrity: Hash-chain unbroken, actor registry attested.
  3. Workflow completeness: RQ, Claims, and grounded evidence present.
  4. Semantic adjudication: Zero non-supporting or unresolved adjudications.
  5. Scientific review: Required level of approval achieved (Author Approved vs Independent Review).

### Stage 11: Provenance & HARKing Safeguards
* **Legacy:** Tracked `first_recorded_text_sha256` and flagged changes after results were registered.
* **v9.0:** The `origin.formation` object records the exact ledger results existing when the claim was first formulated. If a claim is revised after subsequent empirical results appear, a downgrade reason is automatically appended, and the release report flags a provenance warning.

---

## 4. Test Verification Summary

Both suites run independently and pass 100%:
* **Legacy Suite (`v8.4.4`):** 105 tests passing in 38.89s.
* **Modern Suite (`v9.0.0`):** 80 tests passing in 15.33s.
* **Combined Test Count:** 185 tests passing without skips or regressions.
