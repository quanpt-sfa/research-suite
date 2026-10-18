# Research Harness 9.0: Scientific Release Policy Specification

**Version:** 9.0.2  
**Effective Date:** 2026-10-18  
**Scope:** Project-level scientific release gates, claim-proportional validation, and adjudication rules  

---

## 1. Guiding Philosophy

The release gate of Research Harness 9.0 ensures that no research project can transition to `ELIGIBLE` status unless its scientific claims are structurally sound, revision-pinned, grounded in appropriate evidence, and formally authorized by authenticated human scholars.

The foundational principle:
> **Untrusted agents propose; deterministic code validates; authenticated humans authorize; scientific claims remain traceable to their evidence, assumptions, and inferential limitations.**

### 1.1 Distinction Between Technical Verification and Scholarly Judgment
The release gate deterministically verifies:
1. Cryptographic and relational integrity (digests, foreign keys, edge seals, CAS blobs).
2. Provenance integrity (event hash-chains, formation declarations, actor attestations).
3. Revision-pinned evidence relationships (edges strictly bound to exact object revisions).
4. Recorded adjudications and peer reviews (authenticated tokens, independence groups).

**Scope Boundary:** The release engine does **not** claim to evaluate the ultimate novelty, epistemic truth, elegance, or scholarly importance of a paper. Scientific judgment remains strictly attributable to authenticated human researchers and independent reviewers.

---

## 2. Six-Tier Claim-Proportional Classification

Every claim in Research Harness 9.0 must be classified into one of six scientific claim tiers. Each tier enforces clear, testable structural requirements:

### 2.1 Descriptive (`descriptive`)
- **Definition:** Factual assertions describing characteristics of a corpus, codebase, dataset, or observed phenomenon without inferring causal direction or out-of-sample generalization.
- **Evidence Threshold:**
  - Must be grounded in at least one `EvidencePassage` (with verified locator) or an empirical `Result` / `Analysis`.
  - Citation of a generic `Source` alone without a specific excerpt or result is rejected as `CLAIM_UNSUPPORTED`.

### 2.2 Associational (`associational`)
- **Definition:** Assertions of statistical correlation, covariance, or systematic co-occurrence between variables or phenomena.
- **Evidence Threshold:**
  - Must be supported by an empirical `Result` or `EvidencePassage`.
  - Requires definition of relevant `Construct` or `Proxy` objects linked via `measures` or `operationalizes` relations.

### 2.3 Predictive (`predictive`)
- **Definition:** Claims that a model, heuristic, or framework accurately forecasts unseen data or future states.
- **Evidence Threshold:**
  - Must reference empirical `Result` objects documenting train/test separation, cross-validation metrics, or prospective evaluation.

### 2.4 Causal (`causal`)
- **Definition:** Claims asserting that a specific intervention, treatment, or variable manipulation causes a change in an outcome.
- **Evidence Threshold (Strict):**
  - **Cannot be accepted using only descriptive literature passages or raw sources.**
  - Must be supported either by:
    1. An empirical `Result` derived from a controlled experiment, quasi-experiment, or formal causal inference method; OR
    2. A formal `Adjudication` object with verdict `SUPPORTED` and assessment kind `scientific_judgment` or `semantic_entailment`.
  - Failure to provide this triggers block reason: `CAUSAL_CLAIM_LACKS_EMPIRICAL_OR_ADJUDICATED_SUPPORT`.

### 2.5 Conceptual (`conceptual`)
- **Definition:** Theoretical frameworks, ontological categories, or qualitative taxonomic definitions.
- **Evidence Threshold:**
  - Must link to supporting `Theory` and `Construct` objects framing the research question.
  - **No Empirical Dataset Required:** Conceptual research is not forced to fabricate artificial empirical results. When formal adjudication is required, must possess a `SUPPORTED` adjudication confirming conceptual coherence.

### 2.6 Measurement Validation (`measurement_validation`)
- **Definition:** Claims asserting the reliability, convergent validity, or discriminant validity of an empirical measurement instrument or metric.
- **Evidence Threshold:**
  - Must link to both `Construct` and `Proxy` objects, with empirical `Result` data demonstrating psychometric or operational validation.

### 2.7 Unclassified Claims (`unclassified`)
- **Legacy Migration Safeguard:** Claims migrated from legacy systems with ambiguous tiers are designated `unclassified`.
- **Policy Invariant:** An unclassified claim **always blocks release** with rule `UNCLASSIFIED_CLAIM_TIER` until an authenticated author classifies it.

---

## 3. Revision-Aware Evidence & Adjudication Binding

To maintain scientific integrity across evolving research:
1. **Edge Indexing:** All dependency edges are indexed by exact `(source_id, source_revision)` and `(target_id, target_revision)`.
2. **No Automatic Inheritance:** If Claim revision 1 has evidence or adjudications, and Claim revision 2 is published with revised text, revision 2 **does not inherit** evidence or adjudications from revision 1. Evidence and adjudications must explicitly bind to revision 2.
3. **Stale Dependent Evidence:** If an evidence object is superseded by a newer revision, claims pointing to the older revision trigger `STALE_DEPENDENT_EVIDENCE` until re-evaluated.
4. **Adjudication Revision Binding:** Adjudications are bound to exact `(subject_id, subject_revision)` pairs. An adjudication evaluating revision 1 cannot justify release of revision 2.

---

## 4. Warning Policy Configuration (`allow_warnings`)

Release policies support both permissive and strict warning enforcement via the `allow_warnings` configuration:

```json
{
  "allow_warnings": true,
  "require_peer_review": true,
  "require_adjudication": false
}
```

- **`allow_warnings: true` (Permissive / Default):**
  - Non-fatal provenance warnings (e.g. claim downgraded to `EXPLORATORY` due to pre-existing ledger results, or unclassified legacy formation status) are recorded in `provenance_integrity_status = "WARNING"`.
  - Release eligibility proceeds to `ELIGIBLE` if all structural, review, and completeness invariants pass.
- **`allow_warnings: false` (Strict Mode):**
  - Any provenance warning deterministically sets `release_eligibility = "BLOCKED"` with reason `PROVENANCE_WARNING_NOT_PERMITTED`.

---

## 5. Artifact References vs Content Fingerprints

Database integrity verification (`verify_database_integrity`) is strictly schema-aware:

1. **Physical CAS Blobs:**
   - Fields explicitly representing stored binaries or documents (`artifact_sha256`, `blob_sha256`, `library_file_id`) must exist in the CAS directory (`.research/artifacts/`) and match their SHA-256 digest.
   - Missing physical blobs trigger `REFERENCED_ARTIFACT_MISSING`. Corrupted blobs trigger `REFERENCED_ARTIFACT_CORRUPT`.
2. **Text Fingerprints:**
   - Fields representing cryptographic digests of text strings (`passage_norm_sha256`, `first_recorded_text_sha256`, `text_sha256`) are verified for valid SHA-256 formatting and immutable match against the actual text content.
   - **No CAS Blob Required:** Text fingerprints are not misinterpreted as CAS storage pointers.
3. **External Resource Identifiers:**
   - External DOIs, URL locators, or opaque dataset references are verified as valid bibliographic locators without forcing local disk persistence.

---

## 6. Deterministic Block Conditions

The following conditions deterministically set `release_eligibility = "BLOCKED"`:

1. **`PROJECT_INTEGRITY_FAILED`**: Any cryptographic digest mismatch, missing physical CAS blob, or SQLite foreign key failure.
2. **`RESEARCH_INCOMPLETE`**: Missing core scientific objects (RQ, Claim, Evidence).
3. **`CLAIM_UNSUPPORTED`**: A claim has no revision-pinned incoming `supports`/`tests` edges or outgoing `depends_on` edges to valid evidence.
4. **`GENERIC_SOURCE_ONLY`**: A claim depends solely on a generic bibliographic `Source` without a specific excerpt locator or empirical result.
5. **`UNCLASSIFIED_CLAIM_TIER`**: A claim has not been assigned to one of the 6 defined scientific tiers.
6. **`CAUSAL_CLAIM_LACKS_EMPIRICAL_OR_ADJUDICATED_SUPPORT`**: A causal claim lacks experimental results or verified adjudication.
7. **`REQUIRED_ADJUDICATION_MISSING`**: Policy requires semantic adjudication, but no adjudication exists for the claim revision.
8. **`ADJUDICATION_NOT_SUPPORTED`**: An adjudication concluded that evidence contradicts or fails to support the claim (`NOT_SUPPORTED`).
9. **`ADJUDICATION_UNRESOLVED`**: An adjudication is ambiguous or contradictory (`UNCLEAR`).
10. **`CONTAINS_REJECTED_CLAIMS`**: One or more active claims were formally rejected by a human reviewer.
11. **`CLAIMS_NEED_REVIEW`**: One or more claims are in `NEEDS_REVIEW` or `UNASSESSED` status.
12. **`INDEPENDENT_REVIEW_REQUIRED_BY_POLICY`**: Policy mandates independent peer review, but claims have only author-level approval.
13. **`OPEN_INVALIDATIONS_EXIST`**: Dependency edges were modified, leaving open invalidation flags that have not been acknowledged.
14. **`STALE_DEPENDENCIES_PINNED`**: Dependency edges point to superseded revisions without explicit approval acknowledgment.
15. **`STALE_DEPENDENT_EVIDENCE`**: Evidence linked by a claim has been superseded by a newer evidence revision.
16. **`PROVENANCE_WARNING_NOT_PERMITTED`**: Provenance warnings exist when `allow_warnings=false`.
