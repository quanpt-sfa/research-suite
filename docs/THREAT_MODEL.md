# Research Harness 9.0 Threat Model & Security Architecture

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
|                            TRUSTED D13 SERVER DOMAIN                             |
|                                                                                  |
|  * Ubuntu 24.04 D13 Server, Local File System, SQLite Database (WAL), CAS Blobs  |
|  * Capabilities: Actor authentication, HMAC verification, optimistic concurrency,|
|    cryptographic audit logging, privileged review recording.                     |
+==================================================================================+
```

---

## 2. Threat Actors & Adversary Models

1. **Malicious or Hallucinating LLM:** Attempts to invent citations, alter database records directly, declare claims "verified" or "approved", bypass review, or inject malicious code payloads.
2. **Man-in-the-Middle (MitM) / Transport Tampering:** Modifies exported snapshots or proposals in transit to cause state corruption or replay attacks.
3. **Malicious / Compromised Contributor:** Holds a valid importer token; attempts path traversal, denial-of-service (deep JSON recursion), or race conditions to overwrite concurrent work.
4. **Collusive Reviewer:** An author who attempts to review their own work under a different persona or in collusion with lab members.
5. **Host Storage Tamperer:** Direct modification of SQLite database files or CAS disk blobs outside of engine transaction controls.

---

## 3. Attack Surfaces & Mitigations

### 3.1 Deserialization & Denial of Service (DoS)
- **Threat:** Enormous JSON payloads, deeply nested arrays/objects (Billion Laughs / stack overflow), duplicate key collision confusion, IEEE-754 `NaN` / `Infinity` poisoning.
- **Mitigation:**
  - `strict_json_loads()` enforces a hard size limit (16MB for proposals, 64MB for snapshots).
  - Maximum nesting depth of 20 levels.
  - Rejection of duplicate object keys (RFC 8259 compliance).
  - Rejection of `NaN`, `Infinity`, and unquoted special tokens.

### 3.2 Authority Elevation & Forbidden Field Injection
- **Threat:** An agent constructs a proposal with `"scientific_review_status": "AUTHOR_APPROVED"` or `"approved_by": "alice"`, or sets `"state_revision": 999` to falsify review or revision state.
- **Mitigation:**
  - `forbidden_key_paths()` recursively inspects every key in the proposal before database access. Any presence of authority-bearing keys (`approved_by`, `verified_by`, `review_status`, `state_revision`, `actor_id`, etc.) triggers immediate refusal with `UNAUTHORIZED: FORBIDDEN_FIELD`.
  - Proposals only accept operations `propose_object`, `propose_claim`, and `revise_object`. The `Approval` object type is restricted to `SERVER_ONLY_TYPES` and rejected at the gateway.

### 3.3 Path Traversal & Arbitrary Code Execution
- **Threat:** Agent injects relative paths (e.g. `../../etc/passwd` or `C:\Windows\...`) into `proposed_id`, `data_ref`, or relation targets.
- **Mitigation:**
  - All object IDs are constrained by regex `^[A-Z][A-Z0-9_]{1,63}$`.
  - Data references (`data_ref`) are restricted to `^[A-Za-z0-9._:\-]{1,200}$` without slashes or path separators.
  - No user or proposal input is ever evaluated as a filesystem path, shell command, or SQL query.

### 3.4 Secret & Credential Leakage
- **Threat:** An agent inadvertently embeds API keys, private keys, or credentials into proposals or exported snapshots.
- **Mitigation:**
  - `secret_findings()` scans all content keys and values using regex patterns for private keys (`-----BEGIN ... PRIVATE KEY-----`), OpenAI keys (`sk-...`), AWS keys (`AKIA...`), GitHub tokens (`ghp_...`), and harness tokens (`rhk_...`).
  - Found secrets cause immediate rejection (`SECRET_LIKE_CONTENT`), failing closed to prevent credential storage in the CAS repository.

### 3.5 Replay Attacks & Stale Race Conditions
- **Threat:** An agent submits a proposal based on outdated project state, overwriting intervening scientific progress.
- **Mitigation:**
  - Every proposal binds to a specific `snapshot_id`, `state_revision`, and `state_sha256`.
  - The gateway verifies that the base snapshot was signed by the server's secret HMAC key (`snapshot_hmac.key`) and registered in the database.
  - Object-level optimistic concurrency: the proposal declares a `read_set`. Inside `BEGIN IMMEDIATE`, the gateway checks that every object in `read_set` matches the current active head revision. If any object has changed, the proposal is rejected with `Decision.CONFLICT: READ_SET_CONFLICT`.

### 3.6 Direct Database & Storage Tampering
- **Threat:** An adversary with local filesystem access directly alters rows in `objects`, `events`, or `state_revisions` using the `sqlite3` CLI, or modifies files in `artifacts/`.
- **Mitigation:**
  - SQLite triggers abort any `UPDATE` or `DELETE` on append-only tables (`objects`, `edges`, `events`, `status_log`, `state_revisions`, `proposals`, `snapshots`, `audit_log`).
  - The deterministic state digest `compute_state_digest()` computes a canonical hash of all objects, edges, heads, and statuses. Direct modifications without state revision commitment cause immediate `STATE_DIGEST_MISMATCH` during `research project audit`.
  - Every event is linked via a cryptographic hash chain (`prev_sha256` $\to$ `event_sha256`). Broken links are detected by `verify_chain()`.
  - CAS artifact store recalculates the SHA-256 hash of each blob during audit; any byte modification triggers `ARTIFACT_BLOB_CORRUPT`.

---

## 4. Honest Scope & Explicit Non-Goals

1. **Real-World Collusion:** Software cannot determine if two humans from different universities privately agree to review each other's papers favorably. The engine only verifies *recorded facts* (distinct registered actors, valid role tokens, and distinct owner-attested independence groups).
2. **Absolute Empirical Truth:** Research Harness 9.0 guarantees provenance, reproducible dependency tracking, and verification gates. It does not certify that a scientific theory is objectively true in nature.
3. **Network Perimeter Security:** Research Harness 9.0 provides the application-level core. Production deployments on Ubuntu D13 must wrap the HTTP gateway in TLS (HTTPS) with standard firewall and rate-limiting controls.
