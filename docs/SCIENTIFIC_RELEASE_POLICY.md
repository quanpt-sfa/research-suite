# Research Harness 9.0: Scientific Release Policy Specification

**Version:** 9.0.0  
**Effective Date:** 2026-10-18  
**Scope:** Project-level scientific release gates, claim-proportional validation, and adjudication rules  

---

## 1. Guiding Philosophy

The release gate of Research Harness 9.0 ensures that no research project can transition to `RELEASE_ELIGIBLE` status unless its claims are structurally sound, cryptographically verifiable, grounded in appropriate evidence, and formally authorized by authenticated humans.

The foundational principle:
> **Untrusted agents propose; deterministic code validates; authenticated humans authorize; scientific claims remain traceable to their evidence, assumptions, and inferential limitations.**

---

## 2. Six-Tier Claim-Proportional Classification

Every claim in Research Harness 9.0 must be classified into one of six scientific claim tiers. Each tier imposes distinct evidence thresholds:

### 2.1 Descriptive (`descriptive`)
- **Definition:** Factual assertions describing characteristics of a corpus, codebase, dataset, or observed phenomenon without inferring causal direction or out-of-sample generalization.
- **Evidence Threshold:**
  - Must be grounded in at least one `EvidencePassage` (with verified locator) or an empirical `Result` / `Analysis`.
  - Mere citation of a raw `Source` without a specific excerpt or result is rejected as `CLAIM_UNSUPPORTED`.

### 2.2 Associational (`associational`)
- **Definition:** Assertions of statistical correlation, covariance, or systematic co-occurrence between variables or phenomena.
- **Evidence Threshold:**
  - Must be supported by an empirical `Result` or statistical `Analysis` quantifying the observed relationship.
  - Requires definition of relevant `Construct` or `Proxy` objects linked via `measures` or `operationalizes` relations.

### 2.3 Predictive (`predictive`)
- **Definition:** Claims that a model, heuristic, or framework accurately forecasts unseen data or future states.
- **Evidence Threshold:**
  - Must reference empirical `Result` objects documenting train/test separation, cross-validation metrics, or prospective evaluation.

### 2.4 Causal (`causal`)
- **Definition:** Claims asserting that a specific intervention, treatment, or variable manipulation causes a change in an outcome.
- **Evidence Threshold (STRICT):**
  - **Cannot be accepted using only descriptive literature passages or raw sources.**
  - Must be supported either by:
    1. An empirical `Result` derived from a controlled experiment, quasi-experiment, or formal causal inference method; OR
    2. A formal `Adjudication` object with verdict `SUPPORTED` and assessment kind `scientific_judgment` or `semantic_entailment`.
  - Failure to provide this triggers block reason: `CAUSAL_CLAIM_LACKS_EMPIRICAL_OR_ADJUDICATED_SUPPORT`.

### 2.5 Conceptual (`conceptual`)
- **Definition:** Theoretical frameworks, ontological categories, or qualitative taxonomic definitions.
- **Evidence Threshold:**
  - Must link to supporting `Theory` and `Construct` objects framing the research question.
  - When formal adjudication is required, must possess a `SUPPORTED` adjudication confirming conceptual coherence.

### 2.6 Measurement Validation (`measurement_validation`)
- **Definition:** Claims asserting the reliability, convergent validity, or discriminant validity of an empirical measurement instrument or metric.
- **Evidence Threshold:**
  - Must link to both `Construct` and `Proxy` objects, with empirical `Result` data demonstrating psychometric or measurement validation.

### 2.7 Unclassified Claims (`unclassified`)
- **Legacy Migration Safeguard:** Claims migrated from legacy systems with ambiguous tiers are designated `unclassified`.
- **Policy Invariant:** An unclassified claim **always blocks release** with rule `UNCLASSIFIED_CLAIM_TIER` until an authenticated author classifies it.

---

## 3. Evaluation Dimensions

When `release_check(store, policy)` is invoked, it evaluates six distinct dimensions:

| Dimension | Possible Values | Passing Criteria |
|:---|:---|:---|
| **Structural Integrity** | `PASS`, `FAIL` | All SQLite checks, foreign keys, CAS blobs, content hashes, and revision digests must pass without a single corruption finding. |
| **Provenance Integrity** | `PASS`, `WARNING` | Event log hash-chain must be intact. If any claim was revised after empirical results (HARKing warning), status is `WARNING` (recorded in provenance audit). |
| **Workflow Completeness** | `COMPLETE`, `INCOMPLETE` | Project must contain: (1) at least one `ResearchQuestion`, (2) at least one `Claim`, and (3) corresponding supporting evidence objects. |
| **Semantic Adjudication** | `PASSED`, `FAILED`, `UNCLEAR`, `NOT_REQUIRED` | Any active `NOT_SUPPORTED` adjudication yields `FAILED`. Any unresolved `UNCLEAR` adjudication yields `UNCLEAR`. Both block release. |
| **Scientific Review Status** | `AUTHOR_APPROVED`, `INDEPENDENTLY_REVIEWED`, `NEEDS_REVIEW`, `REJECTED`, `UNASSESSED` | Claims must not be rejected or unassessed. Under strict policy, all claims must be `INDEPENDENTLY_REVIEWED`. |
| **Release Eligibility** | `ELIGIBLE`, `BLOCKED` | `ELIGIBLE` only if all dimensions pass and block reasons are empty. |

---

## 4. Deterministic Block Conditions

The following conditions deterministically set `release_eligibility = "BLOCKED"`:

1. **`PROJECT_INTEGRITY_FAILED`**: Any cryptographic digest mismatch, missing CAS blob, or relational FK failure.
2. **`RESEARCH_INCOMPLETE`**: Zero active objects, or missing core scientific objects (RQ, Claim, Evidence).
3. **`CLAIM_UNSUPPORTED`**: A claim has no incoming `supports`/`tests` edges or outgoing `depends_on` edges to genuine evidence (`Result`, `Analysis`, `EvidencePassage`, or `Source`).
4. **`GENERIC_SOURCE_ONLY`**: A claim depends solely on a generic bibliographic `Source` without a specific excerpt or result.
5. **`UNCLASSIFIED_CLAIM_TIER`**: A migrated claim has not been classified into one of the 6 defined tiers.
6. **`CAUSAL_CLAIM_LACKS_EMPIRICAL_OR_ADJUDICATED_SUPPORT`**: A causal claim lacks experimental results or verified adjudication.
7. **`REQUIRED_ADJUDICATION_MISSING`**: Policy requires semantic adjudication, but no adjudication exists for the claim.
8. **`ADJUDICATION_NOT_SUPPORTED`**: An adjudication concluded that evidence does not support the claim (`NOT_SUPPORTED`).
9. **`ADJUDICATION_UNRESOLVED`**: An adjudication is ambiguous or contradictory (`UNCLEAR`).
10. **`CONTAINS_REJECTED_CLAIMS`**: One or more claims were formally rejected by a human reviewer.
11. **`CLAIMS_NEED_REVIEW`**: One or more claims are in `NEEDS_REVIEW` or `UNASSESSED` status.
12. **`INDEPENDENT_REVIEW_REQUIRED_BY_POLICY`**: Policy mandates independent peer review, but claims have only author-level approval.
13. **`OPEN_INVALIDATIONS`**: Graph dependencies were modified, leaving open invalidation flags that have not been acknowledged.
14. **`STALE_PINNED_EDGES`**: Dependency edges point to older revisions of objects when newer revisions exist.

---

## 5. Policy Configuration Options

The policy dictionary passed to `release_check(store, policy)` accepts:

```json
{
  "require_independent_review": true,
  "require_adjudication": false,
  "allow_warnings": false
}
```

- **`require_independent_review`** (boolean, default: `false`): When `true`, all claims must achieve `INDEPENDENTLY_REVIEWED` status by an authenticated actor possessing the `independent_reviewer` role and belonging to an independent group.
- **`require_adjudication`** (boolean, default: `false`): When `true`, every claim must be accompanied by at least one formal `Adjudication` object with verdict `SUPPORTED`.
- **`allow_warnings`** (boolean, default: `true`): When `false`, any provenance warning (e.g., post-hoc claim modification) escalates to a blocking condition.
