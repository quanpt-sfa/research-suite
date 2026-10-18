# Research Harness 9.0: Final Adversarial Acceptance Audit Report

**Date of Audit:** 2026-10-19  
**Auditor / Engine:** Independent Acceptance Audit Agent (Pair Programming)  
**Repository:** [https://github.com/quanpt-sfa/research-suite](https://github.com/quanpt-sfa/research-suite)  
**Pull Request:** `#1`  
**Branch:** `remediation/p0-p1-acceptance`  
**Audited Baseline Commit:** `0f5a94ea562b6828208c5df78953c526b3fc0422`  
**Target Release:** Research Harness 9.0.0  

---

## 1. Executive Summary & Verdict

Following an adversarial source review of commit `0f5a94ea562b6828208c5df78953c526b3fc0422`, four principal security and scientific governance defects were identified and remediated:

1. **Snapshot Identity Impersonation (Gate A - P0):** `export_snapshot()` accepted an untrusted caller-provided `Actor` object without verifying bearer credentials. An untrusted caller could impersonate an existing registered owner.
   * *Remediation:* Enforced bearer token authentication; caller-supplied `Actor` cannot confer identity; token identity mismatch is rejected (`IDENTITY_MISMATCH`); disabled tokens are rejected (`AUTHENTICATION_FAILED`); SQLite audit deadlock in snapshot export resolved.
2. **Adjudication Evidence-Link Constraint Failure (Gate B - P0):** `record_adjudication()` attempted to record cited evidence using the unregistered edge relation `"evaluates"`, triggering SQLite relational CHECK constraint aborts.
   * *Remediation:* Changed edge relation to registered `"depends_on"`; added strict cited evidence validation (rejecting unknown objects with `CITED_EVIDENCE_UNKNOWN`, stale revisions with `STALE_CITED_EVIDENCE`, and unsupported types with `UNSUPPORTED_EVIDENCE_TYPE`) without partial edge or revision mutations.
3. **Causal Scientific Policy Discrepancy & Bypassed Default (Gate C - P0):** Default causal policy enforced `Result OR Adjudication` rather than documented `Result AND Adjudication`, and required an explicit flag to evaluate adjudication.
   * *Remediation:* Under default policy, both empirical `Result` and current authorized `SUPPORTED` `Adjudication` are independently mandatory. Truth table fully verified across all 4 scenarios.
4. **Historical Adjudication Conflation (Gate D - P1):** Migrated legacy adjudications were counted identically to current authorized adjudications when revisions matched.
   * *Remediation:* Classified adjudications into 5 distinct categories (`CURRENT_AUTHORIZED`, `LEGACY_HISTORICAL`, `SUPERSEDED`, `UNRESOLVED`, `UNAUTHORIZED_OR_UNVERIFIABLE`). Under default release policy, historical adjudications cannot substitute for contemporary authorized review.
5. **Seven Claim-Tier Harmonization (Gate E - P1):** Verified all seven tiers (`descriptive`, `associational`, `predictive`, `mechanism_consistent`, `causal`, `measurement_validation`, `conceptual`) with positive and negative complete tests; resolved measurement validation discrepancy to strictly require validation `Result`.
6. **Preserved Migration Invariants (Gate F):** Preserved 4-case provenance model, repair of proven historical imports, dry-run repair planning, changed legacy detection, and non-reversal of post-migration researcher revisions.
7. **End-to-End Operational Workflows (Gate G):** Verified six complete end-to-end workflows (G1–G6) executing real multi-step proposal, review, integrity audit, and release protocols.

### Final Acceptance Verdict: **`ACCEPTED`**

**Verdict Rationale:**
- **All Mandatory Gates (A through I) Pass with Executable Evidence:** Every negative, positive, and integration condition has been demonstrated via deterministic tests.
- **Zero Critical Defects Remain:** Authentication, adjudication execution, default causal policy, historical authority, and scientific-history integrity defects are completely closed.
- **Production Isolation Preserved:** Production host D13 remains untouched; PR #1 remains unmerged.

---

## 2. Mandatory Final Acceptance Table (Gates A – I)

| Gate | Requirement | Status | Exact Source File & Function | Exact Regression Test | Negative-Test Result | Positive-Test Result | Integration-Test Result | Remaining Limitations |
|:---:|:---|:---:|:---|:---|:---|:---|:---|:---|
| **A** | Snapshot identity impersonation impossible through supported API | **PASS** | `research_harness/protocols/snapshot.py`<br>`export_snapshot` | `tests/test_adversarial_acceptance_v9.py`<br>`test_a_neg_1_existing_owner_impersonation`<br>`test_a_neg_2_wrong_actor_with_valid_token`<br>`test_a_neg_3_disabled_token`<br>`test_a_pos_1_authenticated_owner` | PASS: Unauthenticated owner Actor rejected (`AUTHENTICATION_FAILED`); mismatched token rejected (`IDENTITY_MISMATCH`); disabled token rejected (`AUTHENTICATION_FAILED`); 0 snapshots registered, 0 audit successes. | PASS: Authenticated owner token exports snapshot with valid HMAC, registered snapshot record, verifiable via `verify_snapshot_document`. | PASS: `test_g3_unauthorized_identity_workflow` and `test_g1_valid_descriptive_research_workflow`. | None. Bearer token strictly required; caller Actor object cannot authenticate. |
| **B** | Evidence-bound adjudication recorded and integrity-verified | **PASS** | `research_harness/validation/review.py`<br>`record_adjudication` | `tests/test_adversarial_acceptance_v9.py`<br>`test_b_neg_1_unknown_cited_evidence`<br>`test_b_neg_2_invalid_evidence_revision`<br>`test_b_neg_3_unauthorized_adjudication`<br>`test_b_neg_4_agent_origin_adjudication_rejected`<br>`test_b_pos_1_valid_cited_evidence` | PASS: Unknown evidence `RES_999` rejected (`CITED_EVIDENCE_UNKNOWN`); stale revision rejected (`STALE_CITED_EVIDENCE`); author without reviewer role rejected (`ROLE_REQUIRED`); agent proposal rejected at gateway (`PRIVILEGED_OBJECT_TYPE`); 0 partial edges/mutations. | PASS: `record_adjudication` with valid cited evidence creates `depends_on` edge (no SQLite check constraint error), valid digest, passes `audit_project_integrity`. | PASS: `test_g2_valid_empirical_causal_research_workflow` where causal adjudication with cited evidence yields release `ELIGIBLE`. | None. Uses registered relation `depends_on`. |
| **C** | Default causal policy enforces Result AND authorized adjudication | **PASS** | `research_harness/validation/integrity.py`<br>`release_check` | `tests/test_adversarial_acceptance_v9.py`<br>`test_c_causal_truth_table_scenario_1_no_result_no_adj`<br>`test_c_causal_truth_table_scenario_2_yes_result_no_adj`<br>`test_c_causal_truth_table_scenario_3_no_result_yes_adj`<br>`test_c_causal_truth_table_scenario_4_yes_result_yes_adj` | PASS: Scenario 1 [No, No] BLOCKED (`CAUSAL_CLAIM_LACKS_EMPIRICAL_SUPPORT` & `CAUSAL_CLAIM_LACKS_ADJUDICATION`); Scenario 2 [Yes, No] BLOCKED (`CAUSAL_CLAIM_LACKS_ADJUDICATION`); Scenario 3 [No, Yes] BLOCKED (`CAUSAL_CLAIM_LACKS_EMPIRICAL_SUPPORT`). | PASS: Scenario 4 [Yes, Yes] becomes `ELIGIBLE` when all other review and integrity requirements are satisfied. | PASS: Evaluated in full causal workflow `test_g2_valid_empirical_causal_research_workflow`. | None. Both conditions independently enforced under default policy. |
| **D** | Historical adjudication distinct from current adjudication | **PASS** | `research_harness/validation/integrity.py`<br>`_classify_adjudications`<br>`release_check` | `tests/test_adversarial_acceptance_v9.py`<br>`test_d_historical_supported_alone_blocked_by_default`<br>`test_d_historical_on_old_revision_cannot_support_current` | PASS: Historical adjudication alone under default policy does not satisfy current authorization (`BLOCKED: HISTORICAL_ADJUDICATION_NOT_CURRENT`); historical adjudication on rev 1 cannot support rev 2 (`BLOCKED: CAUSAL_CLAIM_LACKS_ADJUDICATION`, `SUPERSEDED=1`). | PASS: When contemporary authorized adjudication is recorded, `CURRENT_AUTHORIZED=1`, `LEGACY_HISTORICAL=1`, release `ELIGIBLE`. Non-default policy `allow_historical_adjudication=true` reported in output. | PASS: `test_g5_historical_adjudication_workflow` (BLOCKED until current adjudication recorded $\to$ ELIGIBLE). | None. 5-way classification explicitly computed and returned. |
| **E** | Seven claim tiers have consistent positive and negative tests | **PASS** | `research_harness/validation/integrity.py`<br>`release_check`<br>`docs/SCIENTIFIC_RELEASE_POLICY.md` | `tests/test_adversarial_acceptance_v9.py`<br>`test_e_1_descriptive_tier` through `test_e_7_conceptual_tier` | PASS: Exact negative block reasons asserted for each tier (e.g. `MEASUREMENT_CLAIM_LACKS_VALIDATION_RESULT`, `PREDICTIVE_CLAIM_LACKS_VALIDATION_RESULTS`, `MECHANISM_CLAIM_LACKS_MECHANISM`). | PASS: Every tier has a valid complete fixture achieving `ELIGIBLE` with positive verification of domain criteria. | PASS: `test_g1_valid_descriptive_research_workflow` and `test_g2_valid_empirical_causal_research_workflow`. | None. All seven tiers fully aligned between docs and code. |
| **F** | Prior migration fixes remain valid | **PASS** | `research_harness/legacy/adapter.py`<br>`migrate_legacy_project` | `tests/test_adversarial_acceptance_v9.py`<br>`test_f_migration_invariants_preserved`<br>`tests/test_legacy_adapter_p0.py` | PASS: Dry run mutates 0 store objects; changed legacy source and ambiguous provenance flagged under `conflicts_requiring_human_decision`. | PASS: Migration cleanly creates claims with complete proposition text and formation metadata; subsequent revisions preserved on repeat migration. | PASS: `test_g6_preservation_workflow`. | None. 4-case provenance model preserved. |
| **G** | Six end-to-end acceptance scenarios pass | **PASS** | `tests/test_adversarial_acceptance_v9.py` | `test_g1_valid_descriptive_research_workflow`<br>`test_g2_valid_empirical_causal_research_workflow`<br>`test_g3_unauthorized_identity_workflow`<br>`test_g4_agent_self_certification_workflow`<br>`test_g5_historical_adjudication_workflow`<br>`test_g6_preservation_workflow` | PASS: G3 rejects impersonation; G4 rejects agent self-certification; G5 blocks historical-only verdict under default release policy. | PASS: G1 descriptive workflow achieves `ELIGIBLE`; G2 causal workflow with cited evidence achieves `ELIGIBLE`; G6 preserves post-migration research revision across re-migration. | PASS: All six scenarios execute multi-step workflows across snapshot, proposal import, review, audit, and release check. | None. Deterministic assertions across all 6 scenarios. |
| **H** | Full v9 CI passes and skipped legacy verification is disclosed | **PASS** | `.github/workflows/ci.yml`<br>`tests/` | Modern suite: 132/132 tests passing locally in `tests/`. Legacy suite: 105/105 tests passing locally. | PASS: Negative assertions and fault injections all verify expected refusals. | PASS: 100% test pass rate across modern and legacy suites (237 total tests). | PASS: GitHub Actions CI workflow runs modern tests on Python 3.10, 3.11, 3.12. | Legacy tests gitignored to prevent binary bloat; explicitly disclosed as run locally. |
| **I** | D13 production remains untouched | **PASS** | Workspace boundary check;<br>`docs/D13_DEPLOYMENT_RUNBOOK.md` | Audit of git tree, branches, remotes, and network connections. | PASS: Zero connections made to host D13; PR #1 not merged. | PASS: All work confined to branch `remediation/p0-p1-acceptance`. | PASS: Deployment runbook documented without execution. | None. Host D13 completely untouched. |

---

## 3. Test Execution Verification

### 3.1 Modern Test Suite (132 Tests)
Command: `pytest tests/ -v`
```
tests/test_adversarial_acceptance_v9.py  29 passed  (Adversarial Acceptance Suite for Gates A-G)
tests/test_auth_remediation.py           10 passed  (Authentication, Token Derivation, Roles)
tests/test_concurrency_and_fault.py       9 passed  (Optimistic Concurrency, Crash Faults)
tests/test_dependency_invalidation.py     7 passed  (Cascading Invalidation, Stale Edges)
tests/test_formation_provenance.py        5 passed  (HARKing Defense, Formation Basis)
tests/test_integrity_and_release.py       4 passed  (Release Checks, Tamper Detection)
tests/test_integrity_deep.py             11 passed  (Cryptographic Digest Verification, CAS)
tests/test_legacy_adapter.py              3 passed  (Migration Baselines & Backups)
tests/test_legacy_adapter_p0.py           5 passed  (Priority Mapping, Adjudications, Repair)
tests/test_p0_p1_remediation_final.py    12 passed  (Schema Upgrades, Revision Digests)
tests/test_release_policy.py             12 passed  (Claim-Proportional Release Gates)
tests/test_review_authority.py            5 passed  (Review Authority, Independence Groups)
tests/test_snapshot_gateway.py           11 passed  (Gateway Validation, HMAC, Schemas)
tests/test_trust_boundary_and_hardening.py 9 passed (Trust Boundary Closures)
============================ 132 passed in 20.30s =============================
```

### 3.2 Legacy Test Suite (105 Tests)
Command: `pytest legacy_v8_4_4/Reusable_Research_Harness_Suite_v8.4.4_Novelty_Semantic_Execution/tests -q`
```
........................................................................ [ 68%]
.................................                                        [100%]
105 passed in 37.42s
```

### 3.3 Public CLI Verification
```bash
python -m research_harness.cli project init PRJ_CLI_TEST --home ./tmp --owner alice --json
# Output: Status INITIALIZED, owner_token issued

python -m research_harness.cli snapshot export PRJ_CLI_TEST --home ./tmp --scope full --json
# Output: Error: Actor token required (--token, --token-file, or RESEARCH_HARNESS_TOKEN)

python -m research_harness.cli snapshot export PRJ_CLI_TEST --home ./tmp --scope full --token $TOKEN --json
# Output: Snapshot ID SNAP_00001, Exported by alice, HMAC valid
```

---

## 4. Final Sign-Off

Research Harness 9.0 has completed adversarial acceptance remediation. All nine gates (A through I) are verified with executable evidence.

**Final Acceptance Verdict:** **`ACCEPTED`**
