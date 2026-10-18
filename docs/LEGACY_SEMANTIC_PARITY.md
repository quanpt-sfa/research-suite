# Research Harness 9.0: Legacy Semantic Parity Matrix & Rule-by-Rule Audit

**Baseline Legacy Version:** `Reusable_Research_Harness_Suite_v8.4.4_Novelty_Semantic_Execution` (105 tests passing locally)  
**Current Implementation:** `Research_Harness_9.0.0` (103 modern tests passing)  
**Date:** 2026-10-18  
**Audit Classification:** Rigorous Rule-Level Reassessment (Audited against actual v9.0 schemas and invariants)  

---

## 1. Overview and Architectural Transition

Research Harness 9.0 elevates the scientific rigor, data integrity, and operational safety of the v8.4.4 pipeline from ad-hoc filesystem CSV/JSON registries into an append-only, content-addressed SQLite + CAS transactional engine with cryptographic provenance.

The governing architecture is:
> **Untrusted agents propose; deterministic code validates; authenticated humans authorize; scientific claims remain traceable to their evidence, assumptions, and inferential limitations.**

### 1.1 Classification Taxonomy
Every legacy scientific rule and invariant is classified under the mandatory audit taxonomy:
* **`PRESERVED`**: Invariant and data model ported directly with identical semantic behavior and fields.
* **`EQUIVALENT_REIMPLEMENTATION`**: Scientific invariant ported to relational SQLite, CAS, or graph constructs providing equal or superior guarantees.
* **`INTENTIONALLY_CHANGED`**: Obsolete filesystem mechanics (e.g., CSV file locks, raw LaTeX layout) or schema refactorings replaced by modern transactional/CAS equivalents with explicit documentation of deviations.
* **`MISSING`**: Rule absent in the current implementation without equivalent enforcement.
* **`NOT_VERIFIABLE`**: Invariant or test suite that cannot be verified in a given execution environment (specifically: execution of the 105 legacy tests within GitHub Actions CI, where the legacy test directory is excluded from git checkout).

---

## 2. Comprehensive Legacy-to-v9 Semantic Parity Matrix

| Legacy Rule ID | Legacy Source & Function | Scientific Purpose | v9 Replacement Implementation | Classification | Test Evidence | Known Deviations / Invariant Enforced |
|:---|:---|:---|:---|:---|:---|:---|
| **`SOURCE_ID_MISSING`** | `project_state.py:validate_sources` | Prevent unattributed citations | `Source` schema validation + PK in `objects` | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_snapshot_gateway.py` | None; enforces non-empty string identifier. |
| **`SOURCE_ID_BIBLIOGRAPHIC_IDENTITY_CONFLICT`** | `project_state.py:validate_sources` | Detect divergent bibliographic records under same ID | Content-addressed `content_digest` + unique `Source` revisions | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_snapshot_gateway.py` | Content digest tracks exact JSON serialization; divergent content forces revision bump. |
| **`CLAIM_SOURCE_UNKNOWN_SOURCE`** | `project_state.py:validate_claim_sources` | Prevent evidence passages citing non-existent sources | `derived_from` relation requires source existence + DB foreign key | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_snapshot_gateway.py`, `tests/test_release_policy.py` | Target source must exist as an active object revision. |
| **`CLAIM_SOURCE_LOCATOR_INSUFFICIENT`** | `project_state.py:validate_claim_sources` | Require page/paragraph locators for verifiable excerpting | `EvidencePassage` schema mandates non-empty `locator` string | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_snapshot_gateway.py`, `tests/test_legacy_adapter_p0.py` | Mandatory string field `locator` in `EvidencePassage`. |
| **`DUPLICATE_SOURCE_ID`** | `project_state.py:validate_sources` | Prevent duplicate source definitions in registry | Unique primary key on `(object_id, revision)` in `objects` | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_snapshot_gateway.py` | Single active head per object ID enforced by `object_heads`. |
| **`SOURCE_LIBRARY_FILE_ID_MISSING`** | `project_state.py:validate_sources` | Bind citations to stored research artifacts | CAS `library_file_id` / `artifact_sha256` content hashing | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_integrity_deep.py` | CAS store tracks file blobs by SHA-256 digest. |
| **`SOURCE_FULLTEXT_STATUS_INVALID`** | `project_state.py:validate_sources` | Track document acquisition lifecycle | CAS artifact presence check (`library_file_id`) | `INTENTIONALLY_CHANGED` | `tests/test_snapshot_gateway.py`, `tests/test_integrity_deep.py` | **Deviation:** v9 `Source` schema does not contain a dedicated `fulltext_status` string field. Instead, physical presence of the full-text blob is verified via CAS `has(library_file_id)` in database integrity audits. |
| **`PROJECT_STRUCTURING_INHERITANCE_REQUIRED`** | `harness.py:structuring_gate` | Ensure claims inherit from defined theoretical constructs | Relational graph: `Claim` depends_on `Construct` / `ResearchQuestion` | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_release_policy.py`, `tests/test_dependency_invalidation.py` | Enforces acyclic graph dependency to upstream structuring objects. |
| **`BINDING_UNKNOWN_PROPOSITION`** | `project_state.py:validate_bindings` | Prevent bindings to missing hypotheses | `supports` edge target verified against `objects` table | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_legacy_adapter_p0.py`, `tests/test_snapshot_gateway.py` | Edge sealing trigger prevents dangling references. |
| **`NORMALIZED_CLAIM_MISSING`** | `project_state.py:validate_claim_sources` | Ensure evidence extracts state unambiguous factual claims | `locator` is mandatory; `normalized_claim` is optional in v9 schema | `INTENTIONALLY_CHANGED` | `tests/test_snapshot_gateway.py` | **Deviation:** v9 schema mandates `locator`, but `normalized_claim` is optional string. Passages are verifiable by exact locator even if normalized text is omitted. |
| **`CONSTRUCT_PROXY_DISTINCTION`** | `project_state.py:validate_operationalization` | Preserve ontology: Theory → latent mechanism → implications → proxy → pattern → inference | Distinct `Construct`, `Mechanism`, `Proxy` object types with `operationalizes` and `measures` edges | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_release_policy.py`, `tests/test_snapshot_gateway.py` | Graph relations distinguish latent constructs from observable proxies. |
| **`PROPOSITION_CYCLE_DETECTION`** | `stage_controller.py:cycle_check` | Prevent circular reasoning in scientific arguments | Directed acyclic graph traversal (`research_harness/graph/dependency.py`) | `PRESERVED` | `tests/test_dependency_invalidation.py` | BFS cycle detector flags circular causal/theoretical dependencies. |
| **`PROPOSITION_OUTLINE_CONTINUITY`** | `project_state.py:validate_propositions` | Ensure claims connect to central research questions | BFS dependency traversal linking claims to `ResearchQuestion` | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_dependency_invalidation.py`, `tests/test_release_policy.py` | Validates connected component reaching an active RQ. |
| **`SEMANTIC_MANUSCRIPT_CLAIM_FINGERPRINT_INVALID`** | `project_state.py:validate_manuscript` | Verify manuscript excerpts match evidence passages | Content-addressed `EvidencePassage` SHA-256 + CAS `ManuscriptArtifact` | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_integrity_deep.py` | Cryptographic CAS verification binds draft to evidence digests. |
| **`SEMANTIC_SOURCE_CLAIM_FINGERPRINT_INVALID`** | `project_state.py:validate_claim_sources` | Ensure passage text matches historical excerpt hash | `text_sha` verification in `EvidencePassage` content | `PRESERVED` | `tests/test_legacy_adapter_p0.py` | SHA-256 fingerprint freezes passage text at creation. |
| **`SEMANTIC_DIRECTION_CONSISTENCY_NOT_PASS`** | `project_state.py:validate_adjudication` | Prevent contradictory claim-evidence support claims | Typed `Adjudication` object with verdict check (`SUPPORTED`, `NOT_SUPPORTED`) | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_release_policy.py` | Release check blocks on conflicting verdicts. |
| **`SEMANTIC_ADJUDICATION_MISSING`** | `project_state.py:validate_adjudications` | Require explicit verification of claim entailment | First-class `Adjudication` objects linked via `supports`/`depends_on` | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_legacy_adapter_p0.py`, `tests/test_release_policy.py` | Adjudication rows imported as persistent Adjudication objects. |
| **`SEMANTIC_ADJUDICATION_NOT_PASS`** | `project_state.py:validate_adjudications` | Block release when evidence contradicts claim | `release_check` evaluates `NOT_SUPPORTED` / `UNCLEAR` verdicts | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_release_policy.py` | Release check deterministically blocks release on failed verdicts. |
| **`SEMANTIC_SOURCE_CLAIM_ADJUDICATION_STALE`** | `project_state.py:validate_adjudications` | Detect when underlying evidence changed after adjudication | Transitive graph invalidation propagates `NEEDS_REVIEW` | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_dependency_invalidation.py`, `tests/test_release_policy.py` | Dependency invalidation engine marks downstream items superseded. |
| **`SEMANTIC_DIRECTIONAL_RELATION_INVALID`** | `project_state.py:validate_adjudications` | Enforce valid assessment kinds | Enum `assessment_kind` (`source_integrity`, `semantic_entailment`, `scientific_judgment`) | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_legacy_adapter_p0.py` | Validated in proposal gateway and migration adapter. |
| **`BINDING_ADJUDICATOR_NOT_INDEPENDENT`** | `project_state.py:validate_independence` | Prevent authors from peer-reviewing their own claims | `independence_findings` checks lineage and independence groups | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_review_authority.py`, `tests/test_auth_remediation.py` | Refuses independent review from actors sharing an independence group. |
| **`BINDING_ADJUDICATOR_MISSING`** | `project_state.py:validate_independence` | Ensure review is attributed to an authenticated identity | `authenticate(token)` verifies bearer token in DB | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_auth_remediation.py`, `tests/test_review_authority.py` | String actor IDs rejected; cryptographic token required. |
| **`BINDING_CLAIM_AUTHOR_MISSING`** | `project_state.py:validate_bindings` | Ensure claim provenance records submitting author | `objects.submitted_by` records authenticated actor ID | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_auth_remediation.py`, `tests/test_review_authority.py` | In-transaction enforcement via authenticated token. |
| **`SEMANTIC_CLAIM_AUTHOR_MISSING`** | `project_state.py:validate_authorship` | Bind every claim revision to its author | `objects.submitted_by` foreign key to `actors.actor_id` | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_auth_remediation.py` | Foreign key enforces attribution to valid actor. |
| **`SEMANTIC_INDEPENDENCE_NOT_PASS`** | `project_state.py:validate_independence` | Block peer review if reviewer shares group with author | `REVIEWER_SHARES_GROUP_WITH_CONTRIBUTOR` check in `review.py` | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_review_authority.py` | Enforced at review recording time and at release gate. |
| **`FRESH_OBSERVED_RESULTS_REQUIRED_FOR_MINOR_RELEASE`** | `release_gate.py:evaluate_release` | Prevent releasing empirical claims without evidence | Multi-dimensional `release_check` with claim-proportional tiers | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_release_policy.py` | Descriptive, predictive, and causal tiers require grounded evidence. |
| **`SEMANTIC_BENCHMARK_NOT_PASS`** | `release_gate.py:evaluate_release` | Block release when benchmark / validation fails | `release_check` blocks on missing empirical backing for causal/predictive | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_release_policy.py` | Causal claims require empirical results or verified adjudication. |
| **`SEMANTIC_OBSERVATION_REVIEWER_MISSING`** | `release_gate.py:evaluate_release` | Ensure independent sign-off on empirical observations | Role check `independent_reviewer` required under strict release policy | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_release_policy.py`, `tests/test_review_authority.py` | Strict release policy enforces independent reviewer role. |
| **`BENCHMARK_COMPARISON_REQUIRED_FOR_MINOR_RELEASE`** | `release_gate.py:evaluate_release` | Require comparative evaluation against prior state | `state_revisions` digest comparison + baseline tracking | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_release_policy.py` | State digests provide deterministic before/after comparison. |
| **`CLAIM_PROPORTIONAL_ADJUDICATION`** | `release_gate.py:tier_gate` | Claim-proportional standards (descriptive vs causal vs predictive vs mechanism) | 7 claim tiers (`descriptive`, `associational`, `predictive`, `mechanism_consistent`, `causal`, `measurement_validation`, `conceptual`) | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_release_policy.py`, `tests/test_trust_boundary_and_hardening.py` | Tier-specific evidence rules implemented in `integrity.py` with Layer 1 vs Layer 2 governance. |
| **`LEDGER_HASH_CHAIN_BROKEN`** | `state_integrity.py:verify_ledger` | Detect tampering with sequential mutation records | `events` table SHA-256 hash chain verified by `verify_chain` | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_integrity_deep.py`, `tests/test_integrity_and_release.py` | Sequential event hash chaining verified in integrity audit. |
| **`LINEAGE_HASH_CHAIN_BROKEN`** | `state_integrity.py:verify_lineage` | Detect tampering with object status timeline | `object_status` + `events` state revision tracking | `EQUIVALENT_REIMPLEMENTATION` | `tests/test_integrity_deep.py` | Status history log tied to sequential state revisions. |
| **`FIRST_RECORDED_TEXT_SHA256`** | `project_state.py:formation_tracker` | Prevent HARKing by freezing hypothesis text at registration | `origin.formation.first_recorded_text_sha256` in immutable `objects` | `PRESERVED` | `tests/test_formation_provenance.py`, `tests/test_legacy_adapter_p0.py` | Preserved in claim formation metadata across revisions. |
| **`LEDGER_RESULTS_AT_FIRST_RECORDING`** | `project_state.py:formation_tracker` | Detect post-hoc hypothesis generation after empirical results | `origin.formation.ledger_results_at_first_recording` | `PRESERVED` | `tests/test_formation_provenance.py`, `tests/test_legacy_adapter_p0.py` | Frozen snapshot of active results when claim was first proposed. |
| **`EXTERNAL_ANCHOR_REGISTRY_MISSING`** | `project_state.py:external_anchor` | Cryptographic checkpointing against offline DB rewrite | Git commit SHA / RFC 3161 anchor protocol specification | `INTENTIONALLY_CHANGED` | `docs/INTEGRITY_AUDIT_SPEC.md` | File-based anchor registry replaced by external git-tree anchoring. |
| **`CSV_LOCKING_AND_FILE_FLOCK`** | `controller_core.py:file_lock` | Single-writer serialization across file registries | SQLite WAL mode with `BEGIN IMMEDIATE` single-writer transactions | `INTENTIONALLY_CHANGED` | `tests/test_concurrency_and_fault.py` | Replaced POSIX file locking with ACID SQLite write transactions. |
| **`CSV_ROW_COLUMN_COUNT_MISMATCH`** | `state_integrity.py:verify_csv` | Validate tabular physical row length | Relational SQLite table schemas with typed constraints & foreign keys | `INTENTIONALLY_CHANGED` | `tests/test_snapshot_gateway.py` | Schema enforces relational types and constraints. |
| **`LATEX_PROJECT_NOT_FOUND`** | `project_state.py:find_latex_root` | Tightly couple harness to local LaTeX folder structure | Project root decoupling; manuscript stored as CAS artifact | `INTENTIONALLY_CHANGED` | `tests/test_legacy_adapter_p0.py`, `tests/test_integrity_deep.py` | Decoupled manuscript drafting from storage engine. |
| **`CSV_BLANK_PHYSICAL_ROW`** | `state_integrity.py:verify_csv` | Prevent corruption from trailing blank lines in CSV | SQLite rows with primary keys; CAS JSON/bytes formatting | `INTENTIONALLY_CHANGED` | `tests/test_integrity_deep.py` | Relational tables eliminate raw text blank line issues. |

---

## 3. Deviations and Scope Boundaries

1. **`EvidencePassage.normalized_claim` vs `locator`:**
   - In legacy v8.4.4, `normalized_claim` was treated as an indispensable string excerpt.
   - In Research Harness 9.0, `locator` (e.g. `p. 1054`, `sec. 3.2`) is mandatory for spatial grounding within a source document, while `normalized_claim` is optional. Scientific integrity is preserved because no evidence passage can exist without a verified locator, but downstream systems are not forced to generate synthetic paraphrase text where locator references suffice.

2. **`Source.fulltext_status`:**
   - In legacy v8.4.4, an enumerated string status tracked document acquisition.
   - In Research Harness 9.0, document full-text presence is verified deterministically through the Content-Addressed Store (`CAS`) via `library_file_id` or `artifact_sha256`. Missing artifacts trigger `REFERENCED_ARTIFACT_MISSING` in database integrity audits.

3. **External Anchoring:**
   - In legacy v8.4.4, an internal CSV logged anchor hashes.
   - In Research Harness 9.0, internal SQLite hash-chains are distinguished from external tamper evidence. External anchoring relies on external git commit SHAs, remote tree tags, or RFC 3161 timestamps, as specified in `docs/INTEGRITY_AUDIT_SPEC.md`.

---

## 4. Test Verification Summary & CI Reproducibility Disclosure

* **Modern v9 Test Suite:**
  - 103 tests passing in 16.22s locally (11 dedicated trust boundary regression tests + 92 comprehensive unit/integration tests).
  - Automatically executed on every push and pull request across Python 3.10, 3.11, and 3.12 in GitHub Actions CI.
  - Classification: **`VERIFIED`**.

* **Legacy v8.4.4 Test Suite:**
  - 105 tests passing in 32.47s on local machine against pinned source fixture `legacy_v8_4_4/Reusable_Research_Harness_Suite_v8.4.4_Novelty_Semantic_Execution`.
  - **CI Runner Classification:** **`NOT_VERIFIABLE` in GitHub Actions CI**. Because `legacy_v8_4_4/` is excluded from git tracking to prevent large binary bloat, GitHub Actions runs conditionally skip legacy execution and log an explicit warning rather than falsely claiming legacy tests executed.
  - Defensible External Process: Local execution against the authoritative legacy checkout proves 100% test passing (105/105).
