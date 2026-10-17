# Research Harness 9.0 Implementation Report

## 1. Executive Summary

This report documents the completion of the **Research Harness 9.0-dev (P0 + P1)** architectural refactoring from the legacy `Reusable_Research_Harness_Suite_v8.4.4_Novelty_Semantic_Execution` platform.

All objectives have been delivered and verified:
1. **ChatGPT Web Proposal Gateway & Authenticated Boundary:** External agents interact exclusively via HMAC-signed scoped snapshots and strict JSON proposals; no write permissions or arbitrary code execution are granted to untrusted models.
2. **Transactional State Management & CAS Storage:** Project state is governed by SQLite in WAL mode with `BEGIN IMMEDIATE` transactions and append-only database triggers, paired with content-addressed file storage.
3. **Versioned Dependency Graph & Invalidation Engine:** Revisions to upstream proxies, datasets, and empirical results trigger cascading invalidation to `NEEDS_REVIEW` across dependent claims with full BFS provenance tracking and cycle tolerance.
4. **HARKing Defense:** The ledger immutably tracks claim formation provenance and strictly enforces non-monotonic status transitions (claims cannot be retroactively elevated to confirmatory).
5. **Privileged Peer Review & 4-Way Status Separation:** Author sign-off and independent peer review are decoupled, reviewer independence is programmatically checked against recorded lineage facts, and release eligibility gates prevent incomplete or unreviewed releases.
6. **Non-Destructive Legacy Migration:** Existing LaTeX/CSV harness projects are migrated safely with automatic backup archives and fail-closed status assignment.

---

## 2. Test Verification & Metrics

### 2.1 Baseline Legacy Tests (v8.4.4)
- **Suite:** `legacy_v8_4_4/Reusable_Research_Harness_Suite_v8.4.4_Novelty_Semantic_Execution/tests`
- **Result:** **105 passed in 37.52s** (100% pass rate).
- **Log:** `reports/baseline_v8_4_4_pytest.txt`, JUnit XML: `reports/baseline_v8_4_4_junit.xml`.

### 2.2 Research Harness 9.0-dev Test Suite
- **Directory:** `tests/`
- **Result:** **33 passed in 5.05s** (100% pass rate).
- **Coverage:** Comprehensive coverage of all protocol guards, transaction boundaries, dependency cascades, and authority rules.

```
tests/test_dependency_invalidation.py   5 passed
tests/test_formation_provenance.py       5 passed
tests/test_integrity_and_release.py       4 passed
tests/test_legacy_adapter.py              3 passed
tests/test_review_authority.py            5 passed
tests/test_snapshot_gateway.py           11 passed
============================= 33 passed in 5.05s =============================
```

### 2.3 End-to-End Lifecycle Demonstration
- **Script:** `examples/end_to_end_demo.py`
- **Verification:** Completed all 8 phases with exit code 0:
  1. Project Initialization & Actor Registration
  2. Scoped Snapshot Export with D13 HMAC Signing
  3. ChatGPT Web Proposal Ingestion via Gateway (CAS & Transaction)
  4. Empirical Update: Proxy Revision & Cascading Invalidation
  5. Concurrency Conflict Detection on Stale Read-Sets
  6. Rejection of Forged / Unauthorized Review Attempts
  7. Privileged Review & Invalidation Resolution
  8. Final Integrity Audit & Release Gate Check (Blocked $\to$ Eligible)

---

## 3. Requirement Traceability Matrix (25 Mandated Cases)

| Requirement / Test Case | Module & Test Function | Verification Status |
|:---|:---|:---|
| 1. Valid snapshot round-trip (export $\to$ verify) | `tests/test_snapshot_gateway.py::test_snapshot_export_and_verify` | **PASS** |
| 2. Stale snapshot rejection / rebase | `tests/test_snapshot_gateway.py::test_read_set_conflict_on_concurrent_modification` | **PASS** |
| 3. Concurrent import safety (isolated write lock) | `tests/test_snapshot_gateway.py::test_read_set_conflict_on_concurrent_modification` | **PASS** |
| 4. Read-set conflict detection | `tests/test_snapshot_gateway.py::test_read_set_conflict_on_concurrent_modification` | **PASS** |
| 5. Duplicate proposal detection (exact hash match) | `tests/test_snapshot_gateway.py::test_proposal_idempotency_duplicate` | **PASS** |
| 6. Proposal ID reuse with differing content | `tests/test_snapshot_gateway.py::test_proposal_id_reuse_with_different_content_rejected` | **PASS** |
| 7. Invalid reference rejection | `tests/test_snapshot_gateway.py::test_invalid_reference_rejected` | **PASS** |
| 8. Unauthorized operation rejection | `tests/test_snapshot_gateway.py::test_unauthorized_operation_rejected` | **PASS** |
| 9. Forbidden approval/revision fields rejection | `tests/test_snapshot_gateway.py::test_forbidden_fields_rejected` | **PASS** |
| 10. Secret / path traversal inputs rejection | `tests/test_snapshot_gateway.py::test_secrets_and_path_traversal_rejected` | **PASS** |
| 11. Protocol version mismatch rejection | `tests/test_snapshot_gateway.py::test_protocol_version_mismatch` | **PASS** |
| 12. Cascading invalidation on Proxy revision | `tests/test_dependency_invalidation.py::test_cascading_invalidation_on_proxy_revision` | **PASS** |
| 13. Cascading invalidation on Result revision | `tests/test_dependency_invalidation.py::test_cascading_invalidation_on_result_revision` | **PASS** |
| 14. Cycle tolerance in dependency traversal | `tests/test_dependency_invalidation.py::test_cycle_tolerance_in_dependency_traversal` | **PASS** |
| 15. Unrelated object stability | `tests/test_dependency_invalidation.py::test_unrelated_object_stability` | **PASS** |
| 16. Stale pinned edge detection | `tests/test_dependency_invalidation.py::test_stale_pinned_edge_detection` | **PASS** |
| 17. Exploratory claim formation provenance | `tests/test_formation_provenance.py::test_exploratory_claim_declared_as_exploratory` | **PASS** |
| 18. Confirmatory claim formation provenance | `tests/test_formation_provenance.py::test_confirmatory_claim_declared_prior_to_results` | **PASS** |
| 19. Non-monotonic status elevation rejection | `tests/test_formation_provenance.py::test_relabel_forbidden_exploratory_cannot_elevate_to_confirmatory` | **PASS** |
| 20. Unknown formation provenance on legacy input | `tests/test_formation_provenance.py::test_unknown_formation_basis_on_unspecified_declaration` | **PASS** |
| 21. Spoofed reviewer / token rejection | `tests/test_review_authority.py::test_unauthenticated_and_unauthorized_review_rejected` | **PASS** |
| 22. Non-independent reviewer rejection | `tests/test_review_authority.py::test_independent_review_rejected_when_sharing_independence_group` | **PASS** |
| 23. Invalidation override requirements | `tests/test_review_authority.py::test_review_refused_on_open_invalidations_without_acknowledgment` | **PASS** |
| 24. Empty project release check (PASS, INCOMPLETE, UNASSESSED, BLOCKED) | `tests/test_integrity_and_release.py::test_empty_project_release_check` | **PASS** |
| 25. Tampered database & CAS artifact detection | `tests/test_integrity_and_release.py::test_tampered_database_fails_integrity_audit`, `::test_tampered_artifact_blob_fails_audit` | **PASS** |
| 26. Legacy migration non-destructive & backup | `tests/test_legacy_adapter.py::test_legacy_migration_dry_run`, `::test_legacy_migration_full_and_idempotent` | **PASS** |

---

## 4. Component Structure & Architecture Inventory

```
research_harness/
├── __init__.py             # Protocol v9.0.0, Schema v9.0, Engine v9.0.0.dev0
├── cli.py                  # CLI with project, snapshot, proposal, review, dependency, audit, release-check
├── core/
│   ├── auth.py             # Actor dataclass, token generation, token hashing, role verification
│   ├── canonical.py        # Canonical JSON, SHA-256, HMAC-SHA256, strict JSON loader
│   ├── errors.py           # HarnessError, ProposalRejection, Clock abstraction
│   └── model.py            # Enums, regexes, forbidden keys, constants
├── graph/
│   ├── dependency.py       # BFS traversal, cascading invalidation, stale pin detection
│   └── relations.py        # 10 typed edge specifications & propagation rules
├── legacy/
│   ├── VENDORED.json       # SHA-256 integrity anchor of vendored legacy code
│   ├── adapter.py          # Non-destructive v8.4.4 migration adapter with zip backup
│   └── v8_state_integrity.py # Byte-identical legacy canonical hashing & atomic writing
├── proposals/
│   ├── gateway.py          # static_check & import_proposal (optimistic concurrency & CAS)
│   └── operations.py       # Handlers for propose_object, propose_claim, revise_object
├── protocols/
│   ├── guards.py           # Recursive forbidden authority field & secret key scanners
│   ├── schema.py           # Draft 2020-12 JSON schemas for proposals, receipts, objects
│   └── snapshot.py         # Scoped snapshot export with D13 HMAC signing & verification
├── provenance/
│   ├── events.py           # Hash-chained SQLite event log & tamper detection
│   └── formation.py        # HARKing-aware claim formation provenance & non-monotonic status
├── state/
│   ├── artifacts.py        # Content-addressed artifact store (staging -> atomic publish)
│   ├── schema.py           # SQLite DDL with append-only triggers & revision monotonicity
│   └── store.py            # StateStore, Tx transaction manager, state digest computation
└── validation/
    ├── integrity.py        # verify_database_integrity, audit_project_integrity, release_check
    ├── project_status.py   # get_project_status, get_object_history
    └── review.py           # record_review (privileged approval endpoint & independence checks)
```

---

## 5. Deployment Readiness

The codebase has been designed and tested for deployment on standard Ubuntu 24.04 LTS servers (D13 host):
- Target repository: `research-harness` installed via standard `pip install -e .` or wheel packaging.
- Target storage path: Defaults to `/srv/research` on POSIX systems or customizable via `RESEARCH_HOME`.
- Zero external daemon requirements: Runs with standard Python 3.10+ and SQLite3 with WAL mode; no heavy external databases or privileged Docker socket requirements.
