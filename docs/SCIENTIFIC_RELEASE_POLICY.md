# Research Harness 9.0: Scientific Release Policy Specification

**Version:** 9.0.4  
**Effective Date:** 2026-10-19  
**Scope:** Project-level scientific release gates, claim-proportional validation, adjudication boundaries, two-layer epistemic governance, and adversarial acceptance invariants.

---

## 1. Guiding Philosophy & Invariant

The release gate of Research Harness 9.0 ensures that no research project transitions to `ELIGIBLE` status unless its scientific claims are structurally sound, revision-pinned, grounded in appropriate evidence, and authorized by authenticated human scholars.

The fundamental architectural invariant:
> **Untrusted actors propose scientific content. Deterministic code validates technical properties. Authenticated and appropriately authorized humans record scientific judgments. Historical states remain immutable. No evidence or authorization is inherited across revisions without an explicit valid rule.**

### 1.1 Two-Layer Epistemic Governance

To maintain scientific integrity without exceeding computational boundaries, the harness strictly separates two layers:

- **Layer 1 — Machine-Verifiable Properties:**
  1. Cryptographic and relational integrity (SHA-256 digests, foreign keys, sealed revision digests, CAS artifacts).
  2. Provenance integrity (event hash-chains, formation declarations, actor authentication).
  3. Revision-pinned evidence relationships (edges strictly bound to exact object revisions via registered relations such as `depends_on` and `supports`).
  4. Server-only adjudication and peer review records (authenticated bearer tokens, inside-transaction role verification, independence group validation).
  5. Absence of stale evidence or unacknowledged dependency changes.

- **Layer 2 — Scholarly Judgment:**
  1. Epistemic credibility of the causal identification strategy.
  2. Conceptual validity of proxy operationalization and construct mapping.
  3. Adequacy of predictive out-of-sample evaluation methods.
  4. Persuasiveness and theoretical coherence of conceptual arguments.
  5. Ultimate scientific merit and scholarly significance.

**Core Rule:** An authorized operation is not necessarily an authorized scientific conclusion. The engine records the results of authorized scholarly judgments (`Adjudication` and `Approval` records). It **never converts structural object presence or an unauthenticated verdict into proof of scholarly validity**.

---

## 2. Seven Claim-Tier Specifications

The core data model defines seven scientific claim tiers:
1. `descriptive`
2. `associational`
3. `predictive`
4. `mechanism_consistent`
5. `causal`
6. `measurement_validation`
7. `conceptual`

Legacy migrated claims lacking classification are assigned `unclassified`, which deterministically blocks release (`UNCLASSIFIED_CLAIM_TIER`).

### 2.1 Seven-Tier Epistemic Policy Table

| Tier | Meaning & Permissible Inference | Minimum Machine-Verifiable Evidence Relationships (Layer 1) | Required Evidence Provenance | Applicable Scientific Assessment | Required Human Authorization | Block Conditions | Warning Conditions | Scholarly Judgment Conditions (Layer 2) | Required Evidence Tests | Negative Counterexamples |
|---|---|---|---|---|---|---|---|---|---|---|
| **`descriptive`** | Factual depiction of corpus, dataset, or observed sample properties. No causal or out-of-sample claims. | At least 1 `EvidencePassage` with verified locator, or empirical `Result`/`Analysis`. | Pinned revision, CAS/text digest valid, declared origin. | Textual fidelity or sample accuracy. | Author approval; independent review if policy requires. | Missing evidence; raw `Source` only without passage (`CLAIM_UNSUPPORTED_DESCRIPTIVE_SOURCE_ONLY`). | Pre-existing data exposure without pre-registration (`EXPLORATORY`). | Representativeness of sample; measurement precision. | Verify passage locator resolves and digest matches. | Generic citation `SRC_001` without excerpt passage fails. |
| **`associational`** | Covariance, statistical correlation, or non-causal co-occurrence between variables. | Linked `Result` or `EvidencePassage`. | Pinned empirical run or passage extraction. | Statistical significance, correlation magnitude, covariate reporting. | Author approval; independent review if policy requires. | Missing empirical result or evidence passage (`ASSOCIATIONAL_CLAIM_LACKS_SUPPORT`). | Observation dataset used without pre-analysis ledger plan. | Confounding variables, selection bias, spurious correlation. | Verify `Result` contains statistical metric. | Unconnected claim or qualitative remark without data fails. |
| **`predictive`** | Forecasting unseen data, out-of-sample prediction, generalization to prospective states. | Empirical `Result` specifically evaluating predictive performance (train/test, CV). | Pinned dataset partition or prospective validation run. | Cross-validation metric, AUC, RMSE, out-of-sample error. | Author approval; independent review if policy requires. | Lacks empirical validation `Result` (`PREDICTIVE_CLAIM_LACKS_VALIDATION_RESULTS`). | In-sample fit metrics reported alongside predictive claim. | Overfitting risk, data leakage across train/test splits, domain shift. | Verify predictive validation evaluation. | Generic literature passage or in-sample descriptive stat fails. |
| **`mechanism_consistent`** | Evidence showing observed patterns are consistent with a hypothesized mechanism. **Does not prove causal identification.** | Linkage to `Mechanism` object AND supporting `Result` or `EvidencePassage`. | Revision-pinned mechanism definition and empirical run. | Mechanistic consistency assessment. | Author approval; independent review if policy requires. | Missing `Mechanism` linkage (`MECHANISM_CLAIM_LACKS_MECHANISM`) or lacking empirical support (`MECHANISM_CLAIM_LACKS_EMPIRICAL_SUPPORT`). | Mechanism plausibility without directional test. | Alternative mechanisms generating identical observational patterns. | Verify `Mechanism` object exists and connects. | Direct empirical result without `Mechanism` link fails. Cannot be silently promoted to `causal`. |
| **`causal`** | Assertion that an intervention or treatment causes change in outcome (counterfactual inference). | Empirical `Result` (experiment, quasi-experiment, IV, DID, RDD) **AND** authorized current `Adjudication` (`SUPPORTED`). Both conditions independently mandatory under default policy. | Pre-registered experiment or identified observational design. | Causal identification strategy adjudication (`scientific_judgment` or `semantic_entailment`). | Independent scientific reviewer (`independent_reviewer` role). | Lacks empirical Result (`CAUSAL_CLAIM_LACKS_EMPIRICAL_SUPPORT`) and/or lacks authorized current `SUPPORTED` adjudication (`CAUSAL_CLAIM_LACKS_ADJUDICATION`). | Unmeasured confounders identified in sensitivity analysis. | Plausibility of exclusion restrictions, parallel trends, SUTVA. | Verify both empirical Result exists and causal adjudication recorded by authorized human. | Generic empirical table alone, or adjudication without empirical Result, or agent-proposed `SUPPORTED` verdict fails. |
| **`measurement_validation`** | Reliability, convergent validity, or discriminant validity of an empirical measurement instrument/proxy. | Linkage to `Construct` AND `Proxy` objects, **PLUS** empirical `Result` demonstrating validation. | Instrument administration data or benchmark dataset. | Psychometric/operational validation evaluation. | Author approval; independent reviewer if policy requires. | Missing `Construct` or `Proxy` (`MEASUREMENT_CLAIM_LACKS_OPERATIONALIZATION`), or lacking validation `Result` (`MEASUREMENT_CLAIM_LACKS_VALIDATION_RESULT`). | Low sample size for validation study. | Construct validity, face validity, measurement invariance across contexts. | Verify construct-proxy operationalization and empirical testing. | Mere existence of `Construct` + `Proxy` without validation result fails. |
| **`conceptual`** | Theoretical definitions, taxonomic categories, qualitative ontology. **No empirical dataset required.** | Linkage to `Theory` or `Construct` framing the research question. | Formal theoretical text provenance. | Conceptual coherence adjudication or theoretical peer review. | Author approval; independent reviewer if policy requires. | Missing `Theory` or `Construct` (`CONCEPTUAL_CLAIM_LACKS_THEORY_OR_CONSTRUCT`). | Informal heuristic definitions without formal constructs. | Internal consistency, logical completeness, conceptual utility. | Verify theory/construct linkage; confirm no empirical dataset forced. | Forcing fake numerical results onto conceptual papers is strictly prohibited. |

---

## 3. Causal Scientific Policy Semantics & Truth Table

Under the documented default causal policy, a causal claim mandates **both** conditions evaluated independently:
1. Appropriate revision-pinned empirical evidence (`Result`) is present.
2. A valid, authorized, current `SUPPORTED` adjudication is present.

Neither condition can substitute for the other. The engine distinguishes structural presence of empirical evidence from scholarly assessment of identification credibility.

### 3.1 Causal Policy Truth Table (Default Policy)

| Scenario | Empirical Result Present | Authorized SUPPORTED Adjudication Present | Expected Causal Gate Status | Deterministic Block Reason(s) |
|---|---|---|---|---|
| **Scenario 1** | No | No | **BLOCKED** | `CAUSAL_CLAIM_LACKS_EMPIRICAL_SUPPORT`<br>`CAUSAL_CLAIM_LACKS_ADJUDICATION` |
| **Scenario 2** | Yes | No | **BLOCKED** | `CAUSAL_CLAIM_LACKS_ADJUDICATION` |
| **Scenario 3** | No | Yes | **BLOCKED** | `CAUSAL_CLAIM_LACKS_EMPIRICAL_SUPPORT` |
| **Scenario 4** | Yes | Yes | **ELIGIBLE** *(if other gates pass)* | None (gate passes) |

---

## 4. Scientific Adjudication Architecture

### 4.1 Five-Way Adjudication Status Classification

The release engine indexes and reports all active and historical adjudications into five distinct classifications:

1. **`CURRENT_AUTHORIZED`:** An adjudication recorded for the current active head revision by an authenticated actor possessing the required role (`independent_reviewer`), with valid subject and cited evidence bindings.
2. **`LEGACY_HISTORICAL`:** An adjudication imported from a legacy migration source (`is_historical: true`) with preserved provenance and legacy verdict. **Under the default policy, historical adjudications do not satisfy the requirement for current authenticated adjudication.** (May be accepted only if an explicit policy configuration `allow_historical_adjudication: true` is activated, which is explicitly noted in `historical_adjudications_accepted`).
3. **`SUPERSEDED`:** An adjudication bound to an earlier revision of an object. Because revision binding is immutable, revising an object leaves earlier adjudications attached only to the prior revision; they cannot support the new head.
4. **`UNRESOLVED`:** An active adjudication with verdict `UNCLEAR` or `NOT_SUPPORTED`. Blocks project release.
5. **`UNAUTHORIZED_OR_UNVERIFIABLE`:** An adjudication created without requisite roles, or proposing server-only adjudications via untrusted proposal import (`OriginKind.PROPOSAL_IMPORTED`).

### 4.2 Human-Authorized Recording Gateway (`record_adjudication`)

Authoritative adjudications can only be created by calling the server-side API:

```python
record_adjudication(
    store,
    token="<authenticated_token>",
    subject_id="CLAIM_001",
    subject_revision=1,
    assessment_kind="semantic_entailment",  # or "scientific_judgment"
    verdict="SUPPORTED",                     # "SUPPORTED", "NOT_SUPPORTED", "UNCLEAR"
    rationale="Methodological causal identification is sound.",
    cited_evidence=[{"object_id": "RES_001", "revision": 1}],
    is_independent=True,                     # requires role 'independent_reviewer'
)
```

**Security and Evidentiary Invariants:**
- `Adjudication` is in `SERVER_ONLY_TYPES`. Proposals attempting `propose_object` or `revise_object` on `Adjudication` are rejected at the gateway with `PRIVILEGED_OBJECT_TYPE`.
- Bearer token authentication occurs inside the transaction; disabled actors are rejected.
- Reliance on cited evidence creates edges using the registered graph relation `depends_on` (not unregistered `evaluates`), preserving schema constraints.
- Cited evidence references are strictly validated: nonexistent objects (`CITED_EVIDENCE_UNKNOWN`), invalid/stale revisions (`STALE_CITED_EVIDENCE`), and unsupported evidence types (`UNSUPPORTED_EVIDENCE_TYPE`) are refused without partial edge mutations.
- The acting adjudicator's identity and independence group are verified and attributed.

---

## 5. Revision-Aware Evidence & Invalidation Binding

1. **Exact Edge Indexing:** All edges are indexed by exact `(source_id, source_revision)` and `(target_id, target_revision)`.
2. **Strict Non-Inheritance:** If Claim revision 1 has evidence or adjudications, and revision 2 is created, revision 2 **inherits zero evidence and zero adjudications**.
3. **Stale Dependent Evidence:** If an evidence object is revised from revision 1 to revision 2, any claim pointing to revision 1 triggers `STALE_DEPENDENT_EVIDENCE` until the claim is updated or acknowledged.
4. **Dependency Invalidation:** Modifying upstream objects creates open `Invalidation` records. Release is blocked until all invalidations are explicitly acknowledged or resolved.

---

## 6. Warning Policy Enforcement (`allow_warnings`)

- **`allow_warnings: true` (Default):**
  - Provenance warnings (e.g., claims downgraded to `EXPLORATORY` due to ledger exposure) are recorded in `provenance_integrity_status = "WARNING"`.
  - Project may reach `ELIGIBLE` if all structural, review, and completeness requirements are satisfied.
- **`allow_warnings: false` (Strict Mode):**
  - Any provenance warning deterministically sets `release_eligibility = "BLOCKED"` with reason `PROVENANCE_WARNING_NOT_PERMITTED`.

---

## 7. Deterministic Block Conditions

1. `PROJECT_INTEGRITY_FAILED`: Cryptographic mismatch, corrupted CAS blob, or SQLite foreign key failure.
2. `RESEARCH_INCOMPLETE`: Missing core objects (ResearchQuestion, Claim, Evidence).
3. `CLAIM_UNSUPPORTED`: Claim lacks revision-pinned evidence edges.
4. `CLAIM_UNSUPPORTED_DESCRIPTIVE_SOURCE_ONLY`: Descriptive claim cites only raw Source without EvidencePassage.
5. `UNCLASSIFIED_CLAIM_TIER`: Claim is unclassified.
6. `CONCEPTUAL_CLAIM_LACKS_THEORY_OR_CONSTRUCT`: Conceptual claim lacks Theory or Construct linkage.
7. `MEASUREMENT_CLAIM_LACKS_OPERATIONALIZATION`: Measurement claim lacks Construct and Proxy linkage.
8. `MEASUREMENT_CLAIM_LACKS_VALIDATION_RESULT`: Measurement claim lacks empirical validation Result.
9. `CAUSAL_CLAIM_LACKS_EMPIRICAL_SUPPORT`: Causal claim lacks empirical Result.
10. `CAUSAL_CLAIM_LACKS_ADJUDICATION`: Causal claim lacks required current authorized `SUPPORTED` adjudication.
11. `HISTORICAL_ADJUDICATION_NOT_CURRENT`: Causal claim relies solely on historical adjudication under default release policy.
12. `MECHANISM_CLAIM_LACKS_MECHANISM`: Mechanism-consistent claim lacks Mechanism object link.
13. `MECHANISM_CLAIM_LACKS_EMPIRICAL_SUPPORT`: Mechanism-consistent claim lacks empirical Result or EvidencePassage.
14. `PREDICTIVE_CLAIM_LACKS_VALIDATION_RESULTS`: Predictive claim lacks empirical validation Result.
15. `ASSOCIATIONAL_CLAIM_LACKS_SUPPORT`: Associational claim lacks empirical Result or EvidencePassage.
16. `REQUIRED_ADJUDICATION_MISSING`: Policy requires adjudication, but none is recorded for claim head revision.
17. `ADJUDICATION_NOT_SUPPORTED`: Claim has non-supporting adjudication (`NOT_SUPPORTED`).
18. `ADJUDICATION_UNRESOLVED`: Claim has ambiguous adjudication (`UNCLEAR`).
19. `CONTAINS_REJECTED_CLAIMS`: One or more active claims are rejected by review.
20. `CLAIMS_NEED_REVIEW`: Active claims are in `NEEDS_REVIEW` or `UNASSESSED`.
21. `INDEPENDENT_REVIEW_REQUIRED_BY_POLICY`: Policy mandates independent peer review, but only author approval exists.
22. `OPEN_INVALIDATIONS_EXIST`: Unacknowledged invalidation flags exist.
23. `STALE_DEPENDENCIES_PINNED`: Pinned dependency edges point to superseded revisions without acknowledgment.
24. `STALE_DEPENDENT_EVIDENCE`: Pinned evidence has been superseded by a newer revision.
25. `PROVENANCE_WARNING_NOT_PERMITTED`: Provenance warnings present when `allow_warnings=false`.
