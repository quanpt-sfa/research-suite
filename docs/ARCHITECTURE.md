# Research Harness 9.0 Architecture & Formal Specification

## 1. Architectural Philosophy

Research Harness 9.0-dev is designed as an immutable, transactional **Research Operating System Core**. It solves the tension between automated AI reasoning (ChatGPT Web, Codex) and strict scientific reproducibility:

1. **Untrusted Agents Propose, Engine Validates, Authorized Actors Import:** No external agent has direct access to the database or filesystem. Proposals are strictly structured documents evaluated under deterministic security guards and optimistic concurrency controls.
2. **Crash-Proof State Consistency:** State updates occur within atomic SQLite `BEGIN IMMEDIATE` transactions in WAL mode, paired with content-addressed artifact storage (CAS) where files are published *before* database references are committed.
3. **Explicit, Cascading Dependency Invalidation:** Scientific claims depend on theories, proxies, and empirical results. Modifying an upstream object automatically invalidates all downstream dependents to `NEEDS_REVIEW` without rewriting history.
4. **HARKing Defense:** The engine records whether a claim was pre-specified or exploratory. Status transitions are strictly non-monotonic: claims cannot be retroactively elevated to confirmatory.
5. **Separation of Four Independent Dimensions:** Integrity, Completeness, Review, and Release Eligibility are distinct axes. Integrity failure is separate from scientific disagreement or incompleteness.

---

## 2. System Architecture Diagram

```
+---------------------------------------------------------------------------------+
|                                 UNTRUSTED ZONE                                  |
|                                                                                 |
|   +--------------------+               +------------------------------------+   |
|   |  ChatGPT Web / UI  | <--- JSON --- | Scoped Snapshot (HMAC Authenticated|   |
|   |  or Codex Agent    | --- Prop. --> | Proposal JSON (read-set declared)  |   |
|   +--------------------+               +------------------------------------+   |
+-------------------------------------------|-------------------------------------+
                                            |  HTTPS / CLI Gateway
+-------------------------------------------v-------------------------------------+
|                              D13 GATEWAY BOUNDARY                               |
|                                                                                 |
|   1. Authenticate Importing Actor Token (rhk_...)                               |
|   2. Strict JSON Parse (depth <= 20, size <= 16MB, no NaN, no duplicate keys)   |
|   3. Static Security Guards (reject forbidden authority fields & credentials)   |
|   4. Structural Validation (Draft 2020-12 Schemas)                             |
+-------------------------------------------|-------------------------------------+
                                            |
+-------------------------------------------v-------------------------------------+
|                      TRANSACTIONAL RESEARCH CORE (WAL)                          |
|                                                                                 |
|   BEGIN IMMEDIATE Critical Section:                                             |
|     * Idempotency & Proposal Duplicate Detection (SHA-256 match)                |
|     * Snapshot Authenticity Verification (HMAC-SHA256 & Registry Check)         |
|     * Read-Set Concurrency Check (Detect conflicts on modified objects)         |
|     * Apply Operations (Object revisions, claims, typed relation edges)         |
|     * Cascading Invalidation Engine (BFS traversal -> NEEDS_REVIEW)             |
|     * Content-Addressed Storage (CAS: Staging -> Atomic Publish -> SQLite ref)  |
|     * Hash-Chained Event Ledger (prev_sha256 -> event_sha256)                   |
|     * Deterministic State Revision Bump (state_sha256 digest recalculation)     |
|   COMMIT                                                                        |
+---------------------------------------------------------------------------------+
```

---

## 3. Data Storage & Relational Schema

All project data resides under `<RESEARCH_HOME>/<project_id>/` (default `/srv/research/<project_id>` on D13 or user home in local dev).

### 3.1 SQLite Tables & Append-Only Invariants

| Table | Mutability | Invariant & Enforced Trigger |
|:---|:---|:---|
| `objects` | Append-Only | Revision history of every scientific entity. `objects_no_update` and `objects_no_delete` triggers abort any modification. Sequential revisions enforced by `objects_revision_sequence`. |
| `object_heads` | Updatable Head Pointer | Tracks active `head_revision` and `lifecycle_status` (ACTIVE / WITHDRAWN). `heads_identity_immutable` and `heads_monotonic` ensure head revision advances by exactly +1. |
| `object_status` | Status Ledger | Current and prior scientific review status (`UNASSESSED`, `NEEDS_REVIEW`, `AUTHOR_APPROVED`, `INDEPENDENTLY_REVIEWED`, `REJECTED`). |
| `status_log` | Append-Only | Chronological trace of every dimension change with actor ID, timestamp, and machine-readable cause. |
| `edges` | Append-Only | Directed typed dependency links between specific `(source_id, source_rev)` and `(target_id, target_rev)` nodes. |
| `invalidations` | State Machine | Records open and resolved invalidations with full BFS causal path, hops, and root cause object. |
| `events` | Append-Only | Cryptographic audit chain where `event_sha256 = SHA256(seq, prev_sha256, event_id, payload)`. |
| `state_revisions` | Append-Only | Deterministic state digest `compute_state_digest()` committed at every state advance. |
| `proposals` | Append-Only | Full record of accepted proposals, content hash, importing actor, receipt, and CAS blob reference. |
| `snapshots` | Append-Only | Registry of exported scoped snapshots with HMAC-SHA256 signatures and permissions. |
| `actors` | Managed | Registered identities, assigned roles, display names, and owner-asserted independence groups. |
| `audit_log` | Append-Only | Records all gateway attempts, including rejections, auth failures, and dry-runs outside state revisions. |

---

## 4. Content-Addressed Artifact Storage (CAS)

Files, datasets, raw proposal payloads, and manuscript artifacts are stored using SHA-256 content addressing:

1. **Staging:** Incoming payload is written to `staging/<random>.tmp`, flushed, and synced to disk (`os.fsync`).
2. **Atomic Publish:** Renamed via `os.replace` to `artifacts/sha256/<prefix>/<digest>` (where prefix is the first 2 hex digits).
3. **Commit:** Only after the artifact exists on disk does the SQLite transaction commit the reference metadata.

If a crash occurs before commit, the staged or published blob is harmlessly orphaned and detected by audit routines; dangling metadata references are mathematically impossible.

---

## 5. Dependency Graph & Invalidation Engine

### 5.1 Typed Relations & Propagation Rules

Every edge in Research Harness 9.0 has an explicit semantics and dependency direction defined in `graph/relations.py`:

| Relation | Allowed Sources | Allowed Targets | Dependent End | Propagates | Meaning |
|:---|:---|:---|:---|:---|:---|
| `defines` | Theory, Construct, Source, Mechanism | Construct, Theory, Mechanism | Target | Yes | Source defines target |
| `operationalizes` | Proxy, Dataset | Construct | Source | Yes | Source operationalizes target construct |
| `measures` | Proxy, Dataset | Construct, Mechanism | Source | Yes | Source measures target |
| `tests` | Analysis, Result | Theory, Mechanism, Claim, RQ | Source | Yes | Source tests target hypothesis |
| `estimated_with` | Analysis, Result | Dataset, Proxy, Analysis | Source | Yes | Source estimated with target |
| `supports` | Result, EvidencePassage, Source, Analysis | Claim, Theory, Mechanism | Target | Yes | Evidence supports hypothesis |
| `challenges` | Result, EvidencePassage, Source, Analysis | Claim, Theory, Mechanism | Target | No | Evidence challenges hypothesis |
| `derived_from` | Any | Any | Source | Yes | Source derived from target |
| `depends_on` | Any | Any | Source | Yes | Source validity depends on target |
| `cited_by` | Source, EvidencePassage, Claim | ManuscriptArtifact | Target | No | Source cited in manuscript |

### 5.2 Traversal Algorithm & Cycle Resilience

1. When object $U$ receives a new revision $r_{\text{new}}$, all previous revisions $r \in [1, r_{\text{new}}-1]$ are deemed stale.
2. The engine invokes `traverse(conn, [(U, 1), ..., (U, r_{\text{new}}-1)])`.
3. Traversal uses Breadth-First Search (BFS) over active head revisions of dependent objects.
4. Cycle Resilience: Traversal tracks a `visited` set and sorts frontiers deterministically. Non-linear research cycles (e.g., Theory $\to$ Claim $\to$ revised Theory) terminate without infinite loops.
5. Invalidation: Affected head revisions are flagged `scientific_review_status = NEEDS_REVIEW`, and an open row is added to `invalidations`.

---

## 6. Claim Formation Provenance (HARKing Defense)

Research Harness 9.0 explicitly tracks claim origins:

- **`CONFIRMATORY_DECLARED`:** Proposer declared confirmatory basis, explicitly passed an empty `observed_results_at_formation`, and the project ledger contained zero Result objects when the claim was first recorded.
- **`EXPLORATORY`:** Declared exploratory, or proposed after Result objects already existed in the ledger. If a proposer claims confirmatory status after results exist, the engine automatically downgrades it to `EXPLORATORY` with `downgrade_reason = "DECLARED_CONFIRMATORY_BUT_RESULTS_EXPOSED"`.
- **`UNKNOWN`:** Omitted formation declarations or legacy migrated claims.
- **Non-Monotonicity Invariant:** A claim first recorded as `EXPLORATORY` or `UNKNOWN` cannot be elevated to `CONFIRMATORY` by revision (`FORMATION_RELABEL_FORBIDDEN`).

---

## 7. Privileged Review Engine

Approvals are never proposed by untrusted agents. They are created strictly through the authenticated review API:

- **`author_approval`:** Requires role `author` or `owner`.
- **`independent_review`:** Requires role `independent_reviewer`. The engine enforces:
  1. Reviewer is not in the object's contributor lineage (`REVIEWER_IS_CONTRIBUTOR`).
  2. Reviewer has an owner-asserted `independence_group` (`INDEPENDENCE_EVIDENCE_MISSING`).
  3. Reviewer does not share an `independence_group` with any contributor (`REVIEWER_SHARES_GROUP_WITH_CONTRIBUTOR`).
- **Invalidation Acknowledgment:** Approving an object with open invalidations or stale pinned edges is refused unless `acknowledge_invalidations=True` is provided with explicit review rationale.

---

## 8. Four-Dimensional Status Evaluation

```
+-----------------------------------------------------------------------------------------+
| IntegrityStatus:        PASS | FAIL | UNKNOWN                                           |
| WorkflowStatus:         NOT_STARTED | IN_PROGRESS | INCOMPLETE | COMPLETE               |
| ScientificReviewStatus: UNASSESSED | NEEDS_REVIEW | AUTHOR_APPROVED |                   |
|                         INDEPENDENTLY_REVIEWED | REJECTED                                |
| ReleaseEligibility:     BLOCKED | ELIGIBLE                                              |
+-----------------------------------------------------------------------------------------+
```

A project is **ELIGIBLE** for release if and only if:
1. `IntegrityStatus == PASS` (cryptographic hashes, foreign keys, CAS blobs verified).
2. `WorkflowStatus == COMPLETE` (contains ResearchQuestion, Claim, and empirical Evidence/Result).
3. `ScientificReviewStatus in {AUTHOR_APPROVED, INDEPENDENTLY_REVIEWED}` (no unassessed or rejected claims).
4. Zero open invalidations and zero unacknowledged stale pinned dependencies.
