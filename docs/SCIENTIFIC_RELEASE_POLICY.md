# Research Harness 9.0: Scientific Release Policy Specification

**Version:** 9.0.3  
**Effective Date:** 2026-10-18  
**Scope:** Project-level scientific release gates, claim-proportional validation, adjudication boundaries, and two-layer epistemic governance.

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
  3. Revision-pinned evidence relationships (edges strictly bound to exact object revisions).
  4. Server-only adjudication and peer review records (authenticated tokens, role verification, independence group validation).
  5. Absence of stale evidence or unacknowledged dependency changes.

- **Layer 2 — Scholarly Judgment:**
  1. Epistemic credibility of the causal identification strategy.
  2. Conceptual validity of proxy operationalization and construct mapping.
  3. Adequacy of predictive out-of-sample evaluation methods.
  4. Persuasiveness and theoretical coherence of conceptual arguments.
  5. Ultimate scientific merit and scholarly significance.

**Core Rule:** The engine records the results of authorized scholarly judgments (`Adjudication` and `Approval` records). It **never converts structural object presence or an unauthenticated verdict into proof of scholarly validity**.

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
| **`mechanism_consistent`** | Evidence showing observed patterns are consistent with a hypothesized mechanism. **Does not prove causal identification.** | Linkage to `Mechanism` object AND supporting `Result` or `EvidencePassage`. | Revision-pinned mechanism definition and empirical run. | Mechanistic consistency assessment. | Author approval; independent review if policy requires. | Missing `Mechanism` linkage (`MECHANISM_CLAIM_LACKS_MECHANISM_LINK`) or lacking empirical support. | Mechanism plausibility without directional test. | Alternative mechanisms generating identical observational patterns. | Verify `Mechanism` object exists and connects. | Direct empirical result without `Mechanism` link fails. Cannot be silently promoted to `causal`. |
| **`causal`** | Assertion that an intervention or treatment causes change in outcome (counterfactual inference). | Empirical `Result` (experiment, quasi-experiment, IV, DID, RDD) AND authorized `Adjudication` (`SUPPORTED`). | Pre-registered experiment or identified observational design. | Causal identification strategy adjudication (`scientific_judgment` or `semantic_entailment`). | Independent scientific reviewer if causal adjudication policy active. | Lacks empirical Result (`CAUSAL_CLAIM_LACKS_EMPIRICAL_OR_ADJUDICATED_SUPPORT`) or lacks `SUPPORTED` adjudication. | Unmeasured confounders identified in sensitivity analysis. | Plausibility of exclusion restrictions, parallel trends, SUTVA. | Verify causal adjudication recorded by authorized human. | Generic empirical table or agent-proposed `SUPPORTED` verdict fails. |
| **`measurement_validation`** | Reliability, convergent validity, or discriminant validity of an empirical measurement instrument/proxy. | Linkage to `Construct` AND `Proxy` objects, PLUS empirical `Result` demonstrating validation. | Instrument administration data or benchmark dataset. | Psychometric/operational validation evaluation. | Author approval; independent reviewer if policy requires. | Missing `Construct` or `Proxy` (`MEASUREMENT_CLAIM_LACKS_OPERATIONALIZATION`), or lacking validation `Result`. | Low sample size for validation study. | Construct validity, face validity, measurement invariance across contexts. | Verify construct-proxy operationalization and empirical testing. | Mere existence of `Construct` + `Proxy` without validation result fails. |
| **`conceptual`** | Theoretical definitions, taxonomic categories, qualitative ontology. **No empirical dataset required.** | Linkage to `Theory` or `Construct` framing the research question. | Formal theoretical text provenance. | Conceptual coherence adjudication or theoretical peer review. | Author approval; independent reviewer if policy requires. | Missing `Theory` or `Construct` (`CONCEPTUAL_CLAIM_LACKS_THEORY_OR_CONSTRUCT`). | Informal heuristic definitions without formal constructs. | Internal consistency, logical completeness, conceptual utility. | Verify theory/construct linkage; confirm no empirical dataset forced. | Forcing fake numerical results onto conceptual papers is strictly prohibited. |

---

## 3. Scientific Adjudication Architecture

### 3.1 Adjudication Statuses

The release engine distinguishes five mutually exclusive adjudication statuses:

1. **Proposed Assessment:** A textual interpretation or candidate finding submitted through a proposal. Prohibited from using the authoritative `Adjudication` type; non-authoritative and excluded from release calculations.
2. **Recorded Scientific Adjudication:** An authoritative scientific decision explicitly entered via `record_adjudication()` by an authenticated human actor possessing appropriate roles (`author`, `owner`, or `independent_reviewer`).
3. **Historical Adjudication:** An assessment imported from a legacy migration source with preserved original provenance, raw legacy verdict, and `is_historical: True`. Cannot be silently upgraded to a fresh human review.
4. **Superseded Adjudication:** An adjudication bound to an earlier revision of an object. Because revision binding is immutable, revising an object leaves earlier adjudications attached only to the prior revision.
5. **Unresolved Adjudication:** An active adjudication with verdict `UNCLEAR` or `NOT_SUPPORTED`. Blocks project release.

### 3.2 Human-Authorized Recording Gateway (`record_adjudication`)

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

**Security Invariants:**
- `Adjudication` is in `SERVER_ONLY_TYPES`. Proposals attempting `propose_object` with `object_type="Adjudication"` are rejected with `SERVER_ONLY_OBJECT_TYPE_PROPOSED`.
- Proposing `import_proposal` does not confer scientific judgment.
- Adjudications with `OriginKind.PROPOSAL_IMPORTED` are rejected during release validation.
- Author-level adjudications cannot self-certify as independent review.

---

## 4. Revision-Aware Evidence & Invalidation Binding

1. **Exact Edge Indexing:** All edges are indexed by exact `(source_id, source_revision)` and `(target_id, target_revision)`.
2. **Strict Non-Inheritance:** If Claim revision 1 has evidence or adjudications, and revision 2 is created, revision 2 **inherits zero evidence and zero adjudications**.
3. **Stale Dependent Evidence:** If an evidence object is revised from revision 1 to revision 2, any claim pointing to revision 1 triggers `STALE_DEPENDENT_EVIDENCE` until the claim is updated or acknowledged.
4. **Dependency Invalidation:** Modifying upstream objects creates open `Invalidation` records. Release is blocked until all invalidations are explicitly acknowledged or resolved.

---

## 5. Warning Policy Enforcement (`allow_warnings`)

- **`allow_warnings: true` (Default):**
  - Provenance warnings (e.g., claims downgraded to `EXPLORATORY` due to ledger exposure) are recorded in `provenance_integrity_status = "WARNING"`.
  - Project may reach `ELIGIBLE` if all structural, review, and completeness requirements are satisfied.
- **`allow_warnings: false` (Strict Mode):**
  - Any provenance warning deterministically sets `release_eligibility = "BLOCKED"` with reason `PROVENANCE_WARNING_NOT_PERMITTED`.

---

## 6. Deterministic Block Conditions

1. `PROJECT_INTEGRITY_FAILED`: Cryptographic mismatch, corrupted CAS blob, or SQLite foreign key failure.
2. `RESEARCH_INCOMPLETE`: Missing core objects (ResearchQuestion, Claim, Evidence).
3. `CLAIM_UNSUPPORTED`: Claim lacks revision-pinned evidence edges.
4. `CLAIM_UNSUPPORTED_DESCRIPTIVE_SOURCE_ONLY`: Descriptive claim cites only raw Source without EvidencePassage.
5. `UNCLASSIFIED_CLAIM_TIER`: Claim is unclassified.
6. `CONCEPTUAL_CLAIM_LACKS_THEORY_OR_CONSTRUCT`: Conceptual claim lacks Theory or Construct linkage.
7. `MEASUREMENT_CLAIM_LACKS_OPERATIONALIZATION`: Measurement claim lacks Construct and Proxy linkage.
8. `MEASUREMENT_CLAIM_LACKS_VALIDATION_EVIDENCE`: Measurement claim lacks validation Result or Adjudication.
9. `CAUSAL_CLAIM_LACKS_EMPIRICAL_OR_ADJUDICATED_SUPPORT`: Causal claim lacks empirical result or supported adjudication.
10. `CAUSAL_CLAIM_LACKS_ADJUDICATION`: Causal claim lacks required independent causal adjudication under strict policy.
11. `MECHANISM_CLAIM_LACKS_MECHANISM_LINK`: Mechanism-consistent claim lacks Mechanism object link.
12. `MECHANISM_CLAIM_LACKS_EMPIRICAL_SUPPORT`: Mechanism-consistent claim lacks empirical Result or EvidencePassage.
13. `PREDICTIVE_CLAIM_LACKS_VALIDATION_RESULTS`: Predictive claim lacks empirical validation Result.
14. `ASSOCIATIONAL_CLAIM_LACKS_SUPPORT`: Associational claim lacks empirical Result or EvidencePassage.
15. `REQUIRED_ADJUDICATION_MISSING`: Policy requires adjudication, but none is recorded for claim head revision.
16. `ADJUDICATION_NOT_SUPPORTED`: Claim has non-supporting adjudication (`NOT_SUPPORTED`).
17. `ADJUDICATION_UNRESOLVED`: Claim has ambiguous adjudication (`UNCLEAR`).
18. `CONTAINS_REJECTED_CLAIMS`: One or more active claims are rejected by review.
19. `CLAIMS_NEED_REVIEW`: Active claims are in `NEEDS_REVIEW` or `UNASSESSED`.
20. `INDEPENDENT_REVIEW_REQUIRED_BY_POLICY`: Policy mandates independent peer review, but only author approval exists.
21. `OPEN_INVALIDATIONS_EXIST`: Unacknowledged invalidation flags exist.
22. `STALE_DEPENDENCIES_PINNED`: Pinned dependency edges point to superseded revisions without acknowledgment.
23. `STALE_DEPENDENT_EVIDENCE`: Pinned evidence has been superseded by a newer revision.
24. `PROVENANCE_WARNING_NOT_PERMITTED`: Provenance warnings present when `allow_warnings=false`.
