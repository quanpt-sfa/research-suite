# Research Harness 9.0 Threat Model & Security Architecture

**Version:** 9.0.4  
**Effective Date:** 2026-10-19  

---

## 1. Context & Trust Boundaries

Research Harness 9.0 operates across a clear, hardened trust boundary:

```
+----------------------------------------------------------------------------------+
|                            UNTRUSTED AGENT DOMAIN                                |
|                                                                                  |
|  * ChatGPT Web sessions, Codex agents, web LLM interfaces, public network        |
|  * Capabilities: Prompt generation, proposal construction, citation fabrication, |
|    hallucinated claims, malicious payload injection.                             |
+----------------------------------------------------------------------------------+
                                        ||
                                        || [UNTRUSTED CHANNEL]
                                        || JSON Payloads via Gateway
                                        \/
+==================================================================================+
|                            TRUSTED ENGINE & SERVER DOMAIN                        |
|                                                                                  |
|  * D13 Server / Host environment, Local File System, SQLite Database, CAS Blobs  |
|  * Capabilities: Bearer token authentication, optimistic concurrency,            |
|    cryptographic audit logging, privileged review/adjudication recording.       |
+==================================================================================+
```

### 1.1 In-Process Boundary Distinction

> [!IMPORTANT]
> **Threat-Model Boundary Clarification:**  
> Python cannot securely sandbox arbitrary hostile bytecode running in the same process with unrestricted access to application memory or file handles.  
> The engine security target is: **Public and documented engine mutation APIs must not authorize actions from self-declared identities or unauthenticated capability wrappers.**  
> Operating-system file permissions, process isolation, containerization, and protection against hostile arbitrary Python code execution are host-level deployment controls, not Python runtime sandbox claims.

---

## 2. Threat Actors & Adversary Models

1. **Untrusted Agent / Malicious LLM:** Attempts to invent citations, alter database records directly, declare claims "verified" or "approved", bypass review, or inject malicious code payloads.
2. **Untrusted Caller Attempting Self-Declared Privilege:** Invokes public API methods passing an arbitrary `actor_id`, an untrusted `Actor` object, or a factory capability without possessing the actor's registered bearer token.
3. **Man-in-the-Middle (MitM) / Transport Tampering:** Modifies exported snapshots or proposals in transit to cause state corruption or replay attacks.
4. **Malicious / Compromised Contributor:** Holds a valid low-privilege importer token; attempts path traversal, denial-of-service, or race conditions to overwrite concurrent work or perform owner operations.
5. **Collusive Reviewer:** An author who attempts to review their own work under a different persona or in collusion with lab members.
6. **Host Storage Tamperer:** Direct modification of SQLite database files or CAS disk blobs outside of engine transaction controls.

---

## 3. Attack Surfaces & Concrete Mitigations

### 3.1 Unauthenticated Privilege, Capability Bypass & Snapshot Identity Impersonation (Gates A & A1-A3)
- **Threat:** Callers invoke internal capability issuers (e.g. `issue_internal_capability("owner")`), pass raw strings `actor_id="owner"` to `store.write_tx()`, or pass an untrusted `Actor` object to `export_snapshot()` to export privileged snapshot data and register audit events under an existing owner's ID without possessing their bearer token.
- **Mitigation:**
  - `store.write_tx()` strictly requires a valid `token` parameter. Passing only `actor_id` or an `InternalCapability` immediately raises `AuthorizationError("AUTHENTICATION_FAILED", ...)`.
  - `export_snapshot()` strictly requires a valid bearer `token`. Caller-supplied `Actor` objects cannot authenticate a request. If supplied, the actor's ID must match the token's authenticated ID (`IDENTITY_MISMATCH`).
  - Disabled actors are immediately rejected (`AUTHENTICATION_FAILED`).
  - Audit logging inside `export_snapshot` executes directly on the connection holding the snapshot transaction, preventing SQLite concurrency deadlocks.
  - Roles (`require_roles`) are verified inside `BEGIN IMMEDIATE` against the immutable database registry.

### 3.2 Scientific Self-Certification, Adjudication Injection & Evidence Link Integrity (Gates B & B1-B4)
- **Threat:** An agent proposes an `Adjudication` object with verdict `SUPPORTED` and an importer imports it, creating an authoritative scientific judgment without human scholarly evaluation. Alternatively, an adjudication creates invalid graph edges or cites nonexistent evidence to bypass checks.
- **Mitigation:**
  - `Adjudication` is in `SERVER_ONLY_TYPES = frozenset({"Approval", "Adjudication"})`.
  - Proposal operations attempting `propose_object` or `revise_object` on `Adjudication` are rejected at the gateway with `PRIVILEGED_OBJECT_TYPE`.
  - Authoritative adjudications can only be created via the server-side `record_adjudication()` operation by authenticated human actors with appropriate roles (`author`, `owner`, or `independent_reviewer`).
  - Adjudications with origin `PROPOSAL_IMPORTED` are deterministically ignored during release validation.
  - Cited evidence linkage creates edges strictly using the registered graph relation `depends_on` (not unregistered `evaluates`), complying with database relational CHECK constraints.
  - Cited evidence references are validated at record time: missing objects (`CITED_EVIDENCE_UNKNOWN`), invalid/stale revisions (`STALE_CITED_EVIDENCE`), and unsupported types are rejected with zero partial state mutations.

### 3.3 Default Causal Scientific Policy & Historical Adjudication Quarantine (Gates C & D)
- **Threat:** Releasing causal claims with only generic empirical results and no scholarly causal identification review; or treating legacy imported adjudications as contemporary authorizations.
- **Mitigation:**
  - Under the default causal release policy, empirical evidence (`Result`) and authorized current `SUPPORTED` adjudication are **independently mandatory**. A generic `Result` cannot substitute for adjudication, and an adjudication cannot substitute for empirical evidence.
  - All four combinations of the causal truth table are enforced deterministically.
  - Adjudications are classified into 5 categories (`CURRENT_AUTHORIZED`, `LEGACY_HISTORICAL`, `SUPERSEDED`, `UNRESOLVED`, `UNAUTHORIZED_OR_UNVERIFIABLE`).
  - Under default policy, `LEGACY_HISTORICAL` adjudications cannot substitute for contemporary authorized review (`HISTORICAL_ADJUDICATION_NOT_CURRENT`).

### 3.4 Deserialization & Denial of Service (DoS)
- **Threat:** Enormous JSON payloads, deeply nested arrays/objects (Billion Laughs / stack overflow), duplicate key collision confusion, IEEE-754 `NaN` / `Infinity` poisoning.
- **Mitigation:**
  - `strict_json_loads()` enforces a hard size limit (16MB for proposals, 64MB for snapshots).
  - Maximum nesting depth of 20 levels.
  - Rejection of duplicate object keys (RFC 8259 compliance).
  - Rejection of `NaN`, `Infinity`, and unquoted special tokens.

### 3.5 Authority Elevation & Forbidden Field Injection
- **Threat:** An agent constructs a proposal with `"scientific_review_status": "AUTHOR_APPROVED"` or `"approved_by": "alice"`, or sets `"state_revision": 999` to falsify review or revision state.
- **Mitigation:**
  - `forbidden_key_paths()` recursively inspects every key in the proposal before database access. Any presence of authority-bearing keys (`approved_by`, `verified_by`, `review_status`, `state_revision`, `actor_id`, etc.) triggers immediate refusal with `UNAUTHORIZED: FORBIDDEN_FIELD`.

### 3.6 Secret & Credential Leakage
- **Threat:** An agent inadvertently embeds API keys, private keys, or credentials into proposals or exported snapshots.
- **Mitigation:**
  - `secret_findings()` scans all content keys and values using regex patterns for private keys (`-----BEGIN ... PRIVATE KEY-----`), OpenAI keys (`sk-...`), AWS keys (`AKIA...`), GitHub tokens (`ghp_...`), and harness tokens (`rhk_...`).
  - Found secrets cause immediate rejection (`SECRET_LIKE_CONTENT`), failing closed to prevent credential storage in the CAS repository.

### 3.7 Replay Attacks & Stale Race Conditions
- **Threat:** An agent submits a proposal based on outdated project state, overwriting intervening scientific progress.
- **Mitigation:**
  - Every proposal binds to a specific `snapshot_id`, `state_revision`, and `state_sha256`.
  - The gateway verifies that the base snapshot was signed by the server's secret HMAC key (`snapshot_hmac.key`) and registered in the database.
  - Object-level optimistic concurrency: the proposal declares a `read_set`. Inside `BEGIN IMMEDIATE`, the gateway checks that every object in `read_set` matches the current active head revision. If any object has changed, the proposal is rejected with `Decision.CONFLICT: READ_SET_CONFLICT`.

### 3.8 Direct Database & Storage Tampering
- **Threat:** An adversary with local filesystem access directly alters rows in `objects`, `edges`, or `state_revisions` using the `sqlite3` CLI, or modifies files in `artifacts/`.
- **Mitigation:**
  - SQLite triggers abort any `UPDATE` or `DELETE` on append-only tables (`objects`, `edges`, `events`, `status_log`, `state_revisions`, `proposals`, `snapshots`, `audit_log`).
  - The deterministic state digest `compute_state_digest()` computes a canonical hash of all objects, edges, heads, and statuses. Direct modifications without state revision commitment cause immediate `STATE_DIGEST_MISMATCH` during `research project audit`.
  - Every event is linked via a cryptographic hash chain (`prev_sha256` $\to$ `event_sha256`). Broken links are detected by `verify_chain()`.
  - CAS artifact store recalculates the SHA-256 hash of each blob during audit; any byte modification triggers `ARTIFACT_BLOB_CORRUPT`.

---

## 4. Honest Scope & Explicit Non-Goals

1. **Host-Level Sandboxing:** Process isolation, containerization, and OS user separation must be configured at the OS level (e.g. systemd service with restricted capabilities, D13 host isolation).
2. **Real-World Collusion:** Software cannot determine if two humans from different universities privately agree to review each other's papers favorably. The engine only verifies *recorded facts* (distinct registered actors, valid role tokens, and distinct owner-attested independence groups).
3. **Absolute Empirical Truth:** Research Harness 9.0 guarantees provenance, reproducible dependency tracking, and verification gates. It does not certify that a scientific theory is objectively true in nature.
4. **Network Perimeter Security:** Research Harness 9.0 provides the application-level core. Production deployments on Ubuntu D13 must wrap the HTTP gateway in TLS (HTTPS) with standard firewall and rate-limiting controls.
