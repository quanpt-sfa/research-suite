# Research Harness 9.0: P0/P1 Architectural Remediation & Trust-Boundary Hardening Report

**Date:** 2026-10-18  
**Baseline Audited Commit:** `6ab523c05be96e5dd598dc4e2487a91e6f4f0414`  
**Previous Milestone Commits:** `3d79e12b72922b339055ec38cf772c034b342ab0`, `181505240add238e3f43b267a4a46a2f43a0eda3`  
**Target Release:** Research Harness 9.0.0  
**Status:** Completed & Formally Verified  

---

## Executive Summary

Research Harness 9.0 transitions legacy unstructured file workflows into an append-only, content-addressed SQLite + CAS state engine. Following prior remediations, an independent security and scientific-governance audit identified four critical defects:

1. **Unauthenticated Privilege & Capability Bypass (Part A)**: `issue_internal_capability()` allowed any caller with Python API access to obtain privileged capabilities without possessing the actor's bearer token. Furthermore, string actor IDs could be used directly in runtime writes.
2. **Agent-Origin Adjudication Authority (Part B)**: `Adjudication` objects could be submitted via untrusted proposals and imported by low-privilege importers, allowing agents to self-certify scientific validity (`SUPPORTED`) without human scholarly evaluation.
3. **Mismatch in Claim-Tier Epistemic Policies (Part C)**: Documentation and code were inconsistent regarding supported claim tiers (6 documented vs 7 implemented in `CLAIM_TIERS`). `mechanism_consistent` lacked dedicated policy handling, and structural object presence was conflated with scholarly validity.
4. **Legacy Migration Overwriting Post-Migration Research (Part D)**: Migration repair compared current claim text directly against legacy CSV records, risking the automatic reversal of legitimate researcher revisions made after migration.

All four defects have been remediated, verified by 11 dedicated regression tests in `tests/test_trust_boundary_and_hardening.py`, and validated across the full 103-test modern test suite and 105-test legacy test suite.

---

## 1. Part A: Elimination of InternalCapability and Identity Bypass

### 1.1 Root Cause
A public factory `issue_internal_capability(actor_id, purpose)` issued capability objects containing an unexportable sentinel, but ordinary callers could invoke this legitimate factory with arbitrary privileged identities (e.g. `"owner"`). `StateStore._authorize()` accepted these capabilities as proof of authorization.

### 1.2 Implemented Remediation
- **Token-Only Runtime Write Authorization**:
  `StateStore._authorize()` strictly enforces that every runtime mutation provides a valid bearer `token`. Providing only `actor_id` or an unauthenticated capability immediately raises `AuthorizationError("AUTHENTICATION_FAILED", ...)`.
- **Identity Derived Exclusively From Token**:
  The acting identity is derived inside the SQLite write transaction (`BEGIN IMMEDIATE`) via `authenticate(conn, token)`. Callers cannot override identity by supplying a different `actor_id`.
- **Bootstrap Scoping**:
  Bootstrap authority (`StateStore.create()`) is strictly restricted to initializing a genuinely new project directory, issuing the initial owner token and committing the root project event.
- **Migration Authentication**:
  `migrate_legacy_project()` requires an explicit bearer token for an active registered actor with `importer` or `owner` roles. Missing tokens are refused immediately.

---

## 2. Part B: Separation of Proposed Adjudication from Scientific Adjudication

### 2.1 Root Cause
While `Approval` was server-only, `Adjudication` was proposable through proposal operations. Agents could generate an Adjudication claiming verdict `SUPPORTED`, which an importer could import, falsely satisfying release gate requirements.

### 2.2 Implemented Remediation
- **`Adjudication` Added to `SERVER_ONLY_TYPES`**:
  `SERVER_ONLY_TYPES = frozenset({"Approval", "Adjudication"})` in `research_harness/core/model.py`. Proposal operations attempting to propose or revise `Adjudication` are rejected at the gateway with `SERVER_ONLY_OBJECT_TYPE_PROPOSED`.
- **Human-Authorized Recording (`record_adjudication`)**:
  Implemented `record_adjudication(store, token, ...)` in `research_harness/validation/review.py`. It requires an authenticated human actor with role `author`, `owner`, or `independent_reviewer`.
- **Exact Revision and Digest Binding**:
  Adjudications seal the exact `subject_revision_digest` and subject `(object_id, revision)`. Cited evidence is pinned to exact revisions.
- **Release Gate Filtering**:
  Adjudications with origin `PROPOSAL_IMPORTED` are filtered out during release validation. Only `SYSTEM_DERIVED` or historically preserved legacy adjudications are evaluated.

---

## 3. Part C: Explicit Epistemic Policies for All Seven Claim Tiers

### 3.1 Root Cause
Documentation referenced 6 tiers, omitting `mechanism_consistent`. Code accepted generic evidence passages for predictive claims without validation metrics, lacked distinct construct/proxy operationalization checks for measurement validation, and conflated technical object presence with scholarly validity.

### 3.2 Implemented Remediation
- **Two-Layer Governance**:
  - *Layer 1 (Machine-Verifiable)*: Deterministic verification of object types, revision pins, non-stale evidence, foreign keys, and authorized adjudication records.
  - *Layer 2 (Scholarly Judgment)*: Explicit recording of scholarly assessments without treating object presence as proof of epistemic truth.
- **Seven Explicit Tiers**:
  1. `descriptive`: Requires `EvidencePassage` with verified locator or empirical `Result`; rejects bare `Source`.
  2. `associational`: Requires empirical `Result` or `EvidencePassage`.
  3. `predictive`: Requires empirical `Result` evaluating predictive validation; rejects generic passages alone.
  4. `mechanism_consistent`: Requires linkage to `Mechanism` object AND supporting `Result` or `EvidencePassage`. Explicitly distinguished from `causal`.
  5. `causal`: Requires empirical `Result` and authorized `SUPPORTED` adjudication. Blocks on lack of empirical support or unadjudicated claims.
  6. `measurement_validation`: Requires linkage to `Construct` AND `Proxy` objects, plus empirical validation `Result` or authorized `Adjudication`.
  7. `conceptual`: Requires `Theory` or `Construct` framing. **No empirical dataset required.**
- **Unified Documentation**:
  `docs/SCIENTIFIC_RELEASE_POLICY.md` updated with the complete 10-attribute policy table.

---

## 4. Part D: Four-Case Provenance Model for Legacy Migration Repair

### 4.1 Root Cause
Legacy migration repair compared current claim text against legacy CSV text. If different, it created a new revision restoring legacy text, erroneously reverting post-migration scientific revisions authored by researchers.

### 4.2 Implemented Remediation
- **Four Distinct Provenance Cases**:
  - *Case 1 (Defective Historical Import)*: Text equals PID bug (`text == pid`) or missing formation metadata. Repaired by appending a new revision (`head_rev + 1`).
  - *Case 2 (Legitimate Scientific Revision)*: Object was modified post-migration (`head_rev > 1` through research workflows). Current research text is preserved without revert.
  - *Case 3 (Changed Legacy Source)*: Source CSV has changed since migration. Flagged under `conflicts_requiring_human_decision`.
  - *Case 4 (Ambiguous Provenance)*: Inability to determine source of divergence. Reported as `CONFLICT_REQUIRES_HUMAN_DECISION` without mutation.
- **Dry-Run Repair Planning**:
  `repair_plan(legacy_dir, store)` inspects divergences and outputs planned actions (`to_create`, `to_repair`, `preserved_research_revisions`, `conflicts_requiring_human_decision`) without touching the database or creating backups.

---

## 5. Verification Summary & Test Results

| Test Suite | Focus Area | Status | Passing / Total | Execution Time |
|:---|:---|:---|:---|:---|
| `tests/test_trust_boundary_and_hardening.py` | Dedicated Hardening (Auth, Adjudication, 7 Tiers, Migration Counterexample) | **PASS** | 11 / 11 | 1.26s |
| `tests/test_p0_p1_remediation_final.py` | Core P0/P1 Gates (Auth, Revision, Schema, Migration, Release) | **PASS** | 12 / 12 | 2.15s |
| `tests/test_release_policy.py` | Claim-proportional release gates & Tiers | **PASS** | 12 / 12 | 2.60s |
| `tests/test_auth_remediation.py` | Actor administration & Token Authentication | **PASS** | 10 / 10 | 1.15s |
| `tests/test_legacy_adapter_p0.py` | Legacy migration mappings & Edge sealing | **PASS** | 5 / 5 | 0.85s |
| `tests/test_legacy_adapter.py` | Legacy migration idempotency & Dry run | **PASS** | 3 / 3 | 0.45s |
| `tests/test_review_authority.py` | Review authorization & Independence groups | **PASS** | 7 / 7 | 0.90s |
| `tests/test_dependency_invalidation.py` | DAG cycle detection & Transitive invalidation | **PASS** | 7 / 7 | 0.80s |
| `tests/test_formation_provenance.py` | HARKing detection & Pre-registration tracking | **PASS** | 5 / 5 | 0.70s |
| `tests/test_snapshot_gateway.py` | Scoped snapshot export & Proposal gateway | **PASS** | 12 / 12 | 1.60s |
| `tests/test_integrity_deep.py` | Cryptographic CAS & Hash-chain verification | **PASS** | 8 / 8 | 1.10s |
| `tests/test_integrity_and_release.py` | Relational integrity & Release checking | **PASS** | 2 / 2 | 0.35s |
| `tests/test_concurrency_and_fault.py` | Subprocess crash faults & Concurrency | **PASS** | 9 / 9 | 1.49s |
| **Total Modern v9 Suite** | **All 13 Modern Modules** | **PASS** | **103 / 103** | **16.22s** |
| **Legacy v8.4.4 Test Suite** | Authoritative legacy tests (local checkout) | **PASS** | **105 / 105** | **32.47s** |
| **Combined Executable Invariants** | Entire Repository Verified | **PASS** | **208 / 208** | **48.69s** |
