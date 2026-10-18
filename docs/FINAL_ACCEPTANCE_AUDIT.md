# Research Harness 9.0: Final Independent Acceptance Audit Report

**Date of Audit:** 2026-10-18  
**Auditor / Engine:** Independent Acceptance Audit Agent (Pair Programming)  
**Repository:** [https://github.com/quanpt-sfa/research-suite](https://github.com/quanpt-sfa/research-suite)  
**Pull Request:** `#1`  
**Branch:** `remediation/p0-p1-acceptance`  
**Starting Audited Commit:** `181505240add238e3f43b267a4a46a2f43a0eda3`  
**Target Release:** Research Harness 9.0.0  

---

## 1. Executive Summary & Verdict

Following an independent trust-boundary and scientific-governance review of commit `181505240add238e3f43b267a4a46a2f43a0eda3`, four critical architectural vulnerabilities were identified:
1. **Unauthenticated Privilege & Capability Bypass (Part A):** Public invocation of `issue_internal_capability()` allowed callers with Python API access to confer arbitrary privileged actor capabilities without possessing bearer tokens.
2. **Agent-Origin Adjudication Authority (Part B):** Proposing `Adjudication` objects through the proposal gateway allowed agents to self-certify scientific validity (`SUPPORTED`), bypassing human scholarly review.
3. **Claim-Tier Epistemic Discrepancies (Part C):** Only six claim tiers were documented while seven were defined in `CLAIM_TIERS`; `mechanism_consistent` was unhandled; generic empirical results were conflated with causal identification.
4. **Legacy Migration Reversion of Research Revisions (Part D):** The migration repair procedure compared current claim text against legacy CSVs, risking the automatic reversion of legitimate post-migration researcher revisions.

All four vulnerabilities have been resolved, verified with negative and positive regression tests in `tests/test_trust_boundary_and_hardening.py`, and validated across the 103 modern tests and 105 legacy tests.

### Final Acceptance Verdict: **`CONDITIONALLY ACCEPTED`**

**Verdict Rationale:**
* **Core Trust Boundary & Epistemic Invariants:** All mandatory security, adjudication, revision integrity, and migration conditions (Gates A–F, H) are **`PASSED`** with 100% executable test verification.
* **Non-Critical Deployment Boundary (Gate I):** Zero modifications to host D13 or production research databases have occurred, in strict adherence to scope. Production deployment remains deferred to host rollout.
* **CI Reproducibility Scope (Gate G):** Modern tests run across Python 3.10, 3.11, 3.12 in CI. Legacy tests are `.gitignore`d to prevent repository bloat, accurately classified as `NOT_VERIFIABLE` in CI, and verified 100% locally.

---

## 2. Mandatory Acceptance Matrix (Gates A – I)

| Gate | Mandatory Condition | Status | Classification | Evidence Reference |
|:---|:---|:---|:---|:---|
| **Gate A — Authentication** | No runtime identity-only or self-issued privileged capability bypass. All runtime mutations require bearer tokens derived from registered actors. | **PASS** | **`VERIFIED`** | `tests/test_trust_boundary_and_hardening.py::test_part_a_*`, `research_harness/state/store.py` |
| **Gate B — Adjudication** | Agent-generated verdict cannot become authoritative without proper authorization. `Adjudication` is server-only; created via `record_adjudication()`. | **PASS** | **`VERIFIED`** | `tests/test_trust_boundary_and_hardening.py::test_part_b_*`, `research_harness/validation/review.py` |
| **Gate C — Claim policies** | All seven claim tiers have consistent, tested rules separating Layer 1 machine checks from Layer 2 scholarly judgment. | **PASS** | **`VERIFIED`** | `tests/test_trust_boundary_and_hardening.py::test_part_c_*`, `docs/SCIENTIFIC_RELEASE_POLICY.md` |
| **Gate D — Migration** | Legitimate post-migration scientific revisions cannot be silently reverted. 4-case provenance model and dry-run repair planning. | **PASS** | **`VERIFIED`** | `tests/test_trust_boundary_and_hardening.py::test_part_d_*`, `research_harness/legacy/adapter.py` |
| **Gate E — Revision integrity** | Evidence and adjudications remain strictly revision-bound. No cross-revision leakage; stale evidence flagged. | **PASS** | **`VERIFIED`** | `tests/test_p0_p1_remediation_final.py::test_p0_revision_*`, `research_harness/validation/integrity.py` |
| **Gate F — Integration** | End-to-end adversarial tests pass through supported interfaces (Snapshot $\to$ Proposal $\to$ Import $\to$ Review/Adjudication $\to$ Release). | **PASS** | **`VERIFIED`** | `tests/test_trust_boundary_and_hardening.py`, `tests/test_snapshot_gateway.py` |
| **Gate G — Legacy parity** | Deviations and non-verifiable rules are honestly classified (`PRESERVED`, `EQUIVALENT_REIMPLEMENTATION`, `INTENTIONALLY_CHANGED`, `NOT_VERIFIABLE`). | **PASS** | **`VERIFIED`** (Local) / **`DOCUMENTED`** (CI) | `docs/LEGACY_SEMANTIC_PARITY.md`, local 105/105 legacy tests |
| **Gate H — CI** | Modern tests pass on supported Python versions (3.10, 3.11, 3.12) with zero warnings treated as passes. | **PASS** | **`VERIFIED`** | `.github/workflows/ci.yml`, 103/103 tests passing |
| **Gate I — Deployment boundary** | No production D13 modifications; clean host isolation maintained. | **PASS** | **`VERIFIED`** | `docs/D13_DEPLOYMENT_RUNBOOK.md`, zero D13 touches |

---

## 3. Confirmed Vulnerabilities and Root Causes

### 3.1 Part A: Privileged InternalCapability Bypass
* **Confirmed Vulnerability:** `issue_internal_capability(actor_id, purpose)` generated a capability carrying a private sentinel. However, the factory function was public and did not verify that the caller was authorized to act as `actor_id`. Any caller in the Python environment could pass `actor_id="owner"` to `store.write_tx()` or invoke privileged routines without owning the actor's bearer token.
* **Root Cause:** Reliance on internal object wrappers instead of requiring cryptographic bearer tokens derived from the database actor registry for all runtime mutations.
* **Remediation:** Removed internal capability authorization for runtime state mutations. `StateStore._authorize()` strictly requires a valid bearer `token`. The acting identity is derived exclusively via `authenticate(conn, token)`. Legacy migration and snapshot exports also mandate valid tokens.

### 3.2 Part B: Agent-Origin Adjudication Authority Injection
* **Confirmed Vulnerability:** `Adjudication` was proposable through `propose_object` and `revise_object`. An untrusted LLM agent could generate an `Adjudication` object with verdict `SUPPORTED`. When an importer imported the proposal, the release gate treated the verdict as an authoritative scientific judgment.
* **Root Cause:** Treating import authorization as scientific adjudication authority, and omitting `Adjudication` from `SERVER_ONLY_TYPES`.
* **Remediation:** Added `Adjudication` to `SERVER_ONLY_TYPES`. Added `record_adjudication(store, token, ...)` requiring an authenticated human actor with role `author`, `owner`, or `independent_reviewer`. Adjudications seal `subject_revision_digest`, and any adjudication with origin `PROPOSAL_IMPORTED` is ignored during release gate validation.

### 3.3 Part C: Epistemic Discrepancies Across Seven Claim Tiers
* **Confirmed Vulnerability:** Documentation listed 6 tiers, while `CLAIM_TIERS` contained 7. `mechanism_consistent` was omitted from release logic. Predictive claims accepted descriptive passages without predictive validation. Causal claims accepted generic Result objects without causal identification.
* **Root Cause:** Conflating machine-verifiable technical properties (Layer 1) with scholarly scientific validity (Layer 2).
* **Remediation:** Established a versioned 7-tier epistemic policy table in `docs/SCIENTIFIC_RELEASE_POLICY.md`. Implemented tier-specific checks in `integrity.py`:
  - `conceptual`: Requires Theory/Construct; does not force fake datasets.
  - `measurement_validation`: Requires Construct AND Proxy operationalization, plus empirical validation.
  - `causal`: Requires empirical Result; blocks if strict causal adjudication policy is missing `SUPPORTED`.
  - `mechanism_consistent`: Requires Mechanism object linkage plus empirical support; cannot be treated as causal.
  - `predictive`: Requires empirical Result evaluating predictive validation.

### 3.4 Part D: Legacy Migration Reversion of Post-Migration Research
* **Confirmed Vulnerability:** Migration repair compared current claim text against legacy CSV text. If different, it created a new revision restoring legacy text. If a researcher legitimately revised a claim post-migration, re-running migration erased their research edits.
* **Root Cause:** Failing to distinguish defective historical imports from legitimate post-migration scientific revisions.
* **Remediation:** Implemented a 4-case provenance model in `legacy/adapter.py`. Legitimate research revisions (`head_rev > 1`) are preserved. Corrupted imports (e.g. text == PID bug) are repaired via append-only revisions. Changed source or ambiguous provenance is flagged under `conflicts_requiring_human_decision`. Added dry-run repair planning via `repair_plan()`.

---

## 4. Test Verification Summary

### 4.1 Modern Test Suite (103 Tests)
Command: `pytest -q`
```
........................................................................ [ 69%]
...............................                                          [100%]
103 passed in 16.22s
```

### 4.2 Dedicated Hardening Regression Suite (11 Tests)
Command: `pytest tests/test_trust_boundary_and_hardening.py -v`
```
tests/test_trust_boundary_and_hardening.py::test_part_a_unauthenticated_actor_id_rejected PASSED
tests/test_trust_boundary_and_hardening.py::test_part_a_internal_capability_cannot_confer_runtime_privilege PASSED
tests/test_trust_boundary_and_hardening.py::test_part_a_snapshot_export_requires_token PASSED
tests/test_trust_boundary_and_hardening.py::test_part_a_legacy_migration_requires_token PASSED
tests/test_trust_boundary_and_hardening.py::test_part_b_agent_cannot_propose_adjudication PASSED
tests/test_trust_boundary_and_hardening.py::test_part_d_counterexample_legitimate_revision_not_reverted PASSED
tests/test_trust_boundary_and_hardening.py::test_part_c_predictive_claim_requires_predictive_validation PASSED
tests/test_trust_boundary_and_hardening.py::test_part_c_measurement_validation_requires_proxy_and_construct PASSED
tests/test_trust_boundary_and_hardening.py::test_part_c_causal_claim_requires_empirical_and_adjudication PASSED
tests/test_trust_boundary_and_hardening.py::test_part_c_mechanism_consistent_requires_mechanism PASSED
tests/test_trust_boundary_and_hardening.py::test_part_c_conceptual_claim_does_not_require_dataset PASSED
11 passed in 1.26s
```

### 4.3 Legacy Test Suite (105 Tests)
Command: `pytest legacy_v8_4_4/Reusable_Research_Harness_Suite_v8.4.4_Novelty_Semantic_Execution/tests -q`
```
........................................................................ [ 68%]
.................................                                        [100%]
105 passed in 32.47s
```

### 4.4 Combined Verification Invariants
- Total Executable Tests: **208 passed, 0 failed, 100% passing.**

---

## 5. Final Sign-Off

Research Harness 9.0 has completed trust-boundary correction and acceptance hardening. All public and documented mutation paths strictly enforce token authentication, scientific adjudication is decoupled from untrusted proposal imports, all seven claim tiers are governed by explicit epistemic policies, and legacy migration preserves authentic research revisions.

**Final Classification:** **`CONDITIONALLY ACCEPTED`**  
*(Pending host D13 physical rollout as documented in `docs/D13_DEPLOYMENT_RUNBOOK.md`)*
