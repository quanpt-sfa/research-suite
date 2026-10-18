# Research Harness 9.0: D13 Production Deployment Runbook

**Target Host:** D13 Scientific Host  
**Operating System:** Ubuntu 24.04 LTS (x86_64 / aarch64)  
**Target Storage Root:** `/srv/research`  
**Engine Release:** Research Harness 9.0.0  
**Effective Date:** 2026-10-18  
**Deployment Readiness:** **CONDITIONAL** (Deployment readiness prepared and verified locally; awaiting execution of acceptance test suite directly on physical host D13)

> [!IMPORTANT]
> This runbook documents the deployment procedures, permission configurations, operational playbooks, and architectural boundary statuses for host D13. In strict compliance with the deployment mandate, no live mutations or production data modifications are performed on D13 during this phase. Full operational readiness requires direct execution on D13.

---

## 1. Host Architecture & Storage Decoupling

On host D13, research project data lives **strictly outside** the engine code repository to guarantee complete separation of immutable execution binaries and mutable, append-only scientific state. Multiple independent projects coexist under `/srv/research` without cross-project state pollution.

### 1.1 Directory Structure & File Permissions
```
/srv/research/                      (RESEARCH_HOME; owner: rharness:rharness, mode: 0750)
├── <project_id>/                   (Project Root; mode: 0750)
│   ├── state.sqlite3               (SQLite state database; mode: 0600)
│   ├── state.sqlite3-wal           (WAL journal)
│   ├── state.sqlite3-shm           (Shared-memory index)
│   ├── artifacts/                  (Content-addressed CAS storage; mode: 0750)
│   │   └── sha256/
│   │       └── <ab>/
│   │           └── <hash>          (Immutable artifact blobs; mode: 0640)
│   ├── staging/                    (Atomic write staging; mode: 0700)
│   │   └── <random>.tmp
│   ├── secrets/                    (Private credentials & HMAC keys; mode: 0700)
│   │   └── snapshot_hmac.key       (256-bit HMAC signing key; mode: 0600)
│   ├── migrations/                 (Migration reports and logs; mode: 0750)
│   └── backups/                    (Hot/cold point-in-time backups; mode: 0700)
└── ...
```

---

## 2. Host Provisioning & Security Isolation

### 2.1 Service User and Group Isolation
Create an unprivileged system service user and dedicated group:
```bash
sudo groupadd --system rharness
sudo useradd --system --gid rharness --no-create-home --shell /usr/sbin/nologin rharness
```

### 2.2 Filesystem Initialization
Mount `/srv/research` on durable, resilient storage (e.g., ZFS, XFS, or enterprise ext4 with write barriers enabled):
```bash
sudo mkdir -p /srv/research
sudo chown rharness:rharness /srv/research
sudo chmod 0750 /srv/research
```

### 2.3 Pinned Engine Release Installation
Install the pinned Python engine into an immutable system directory (`/opt/research-harness`):
```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip sqlite3 rsync

# Deploy pinned engine virtual environment
sudo mkdir -p /opt/research-harness
sudo python3 -m venv /opt/research-harness/venv
sudo /opt/research-harness/venv/bin/pip install --upgrade pip setuptools wheel

# Install pinned distribution wheel
sudo /opt/research-harness/venv/bin/pip install /opt/research-harness/dist/research_harness-9.0.0-py3-none-any.whl
sudo chown -R root:root /opt/research-harness
sudo chmod -R 0755 /opt/research-harness
```

---

## 3. Project Initialization & Actor Provisioning

All project mutations execute as the unprivileged service account `rharness`:
```bash
sudo -u rharness -i
export RESEARCH_HOME=/srv/research
export PATH=/opt/research-harness/venv/bin:$PATH
```

### 3.1 Initializing a Research Project
```bash
# Initialize project PRJ_SYNTH_01 with designated lead scientist
research project init PRJ_SYNTH_01 --owner-id lead_scientist --json
```
*Outputs an Owner Bearer Token (`rhk_...`). Save this token securely in a credential vault or root-owned secret file (`/srv/research/PRJ_SYNTH_01/secrets/owner.token`).*

### 3.2 Secure Token Handling (Avoiding Shell History & Process List Leaks)
> [!CAUTION]
> Never pass raw bearer tokens directly on the command line via `--token` in multi-user production environments. Tokens passed via command-line arguments are visible in `ps aux`, `htop`, `/proc`, and shell history (`.bash_history`).

Always use `--token-file` or the `RESEARCH_HARNESS_TOKEN` environment variable:
```bash
# Option A: Save to a mode 0600 file
echo -n "rhk_SECRET_TOKEN" > /tmp/owner.token
chmod 0600 /tmp/owner.token

# Option B: Pass via environment variable
export RESEARCH_HARNESS_TOKEN="rhk_SECRET_TOKEN"
```

### 3.3 Provisioning Authorized Actors
```bash
# Register an Author
research project actor add PRJ_SYNTH_01 \
  --token-file /tmp/owner.token \
  --actor-id author_alice \
  --display-name "Dr. Alice Smith" \
  --roles author \
  --independence-group group_alpha

# Register an Independent Reviewer (must belong to distinct independence group)
research project actor add PRJ_SYNTH_01 \
  --token-file /tmp/owner.token \
  --actor-id reviewer_bob \
  --display-name "Prof. Bob Jones" \
  --roles independent_reviewer \
  --independence-group group_beta
```

---

## 4. Verification & Operational Health Checks

### 4.1 Real-Time Cryptographic Integrity Audit
Run the automated integrity audit across SQLite, event hash chain, and CAS blobs:
```bash
research project audit PRJ_SYNTH_01
```
*Expected Output for healthy project:*
```
Project Integrity: PASS
All cryptographic hashes, foreign keys, event chains, and artifacts verified intact.
```

### 4.2 Scientific Release Gate Evaluation
Evaluate readiness under claim-proportional adjudication policy:
```bash
research project release-check PRJ_SYNTH_01 --json
```
*Expected Output for compliant project:*
```json
{
  "project_id": "PRJ_SYNTH_01",
  "release_eligibility": "ELIGIBLE",
  "structural_integrity": "PASS",
  "provenance_integrity": "PASS",
  "workflow_completeness": "COMPLETE",
  "semantic_adjudication_status": "PASSED",
  "scientific_review_status": "INDEPENDENTLY_REVIEWED",
  "block_reasons": []
}
```

---

## 5. Schema Upgrades & Safe Engine Maintenance

### 5.1 Safe Engine Upgrade Protocol
Because immutable engine code is decoupled from mutable project data, upgrading the engine does not modify existing research projects until explicitly authorized:

1. Deploy the new engine package to `/opt/research-harness/venv`:
   ```bash
   sudo /opt/research-harness/venv/bin/pip install --upgrade /opt/dist/research_harness-9.0.1-py3-none-any.whl
   ```
2. Verify existing projects continue operating under backward-compatible mode without changes.
3. For projects requiring new database triggers or schema versions:
   ```bash
   research project upgrade-schema PRJ_SYNTH_01 --token-file /tmp/owner.token
   ```
   *Applies `EDGE_SEAL_TRIGGER` and updates `meta.db_schema_version` to `9.0.2`.*

---

## 6. Backup, Reconciliation & Disaster Recovery Playbook

### 6.1 Online Point-in-Time Backup Protocol
SQLite WAL mode permits hot, online backups without locking out readers or halting operations:
```bash
TIMESTAMP=$(date -u +%Y%m%d_%H%M%SZ)
BACKUP_DIR="/srv/research/PRJ_SYNTH_01/backups/$TIMESTAMP"
mkdir -p "$BACKUP_DIR"

# 1. Hot atomic SQLite backup via SQLite Online Backup API
sqlite3 /srv/research/PRJ_SYNTH_01/state.sqlite3 ".backup '$BACKUP_DIR/state.sqlite3'"

# 2. Synchronize CAS artifacts (content-addressed files are immutable)
rsync -a /srv/research/PRJ_SYNTH_01/artifacts/ "$BACKUP_DIR/artifacts/"

# 3. Synchronize snapshot signing key
rsync -a /srv/research/PRJ_SYNTH_01/secrets/ "$BACKUP_DIR/secrets/"

# 4. Generate checksum manifest
cd "$BACKUP_DIR" && sha256sum state.sqlite3 secrets/* > SHA256SUMS
```

### 6.2 CAS Backup and Storage Reconciliation
To verify that all database-referenced CAS files exist on disk and identify orphan blobs:
```bash
research project recover PRJ_SYNTH_01 --json
```
*Report lists any stale staging files cleaned up and all orphan blobs detected.*

### 6.3 Point-in-Time Rollback Procedure
If a catastrophic operational failure occurs:
1. Halt proposal ingestion for the project.
2. Verify integrity of the backup archive:
   ```bash
   cd "/srv/research/PRJ_SYNTH_01/backups/$RESTORE_TIMESTAMP"
   sha256sum -c SHA256SUMS
   ```
3. Restore `state.sqlite3`, `artifacts/`, and `secrets/`:
   ```bash
   cp -f "$BACKUP_DIR/state.sqlite3" /srv/research/PRJ_SYNTH_01/state.sqlite3
   rm -f /srv/research/PRJ_SYNTH_01/state.sqlite3-wal /srv/research/PRJ_SYNTH_01/state.sqlite3-shm
   rsync -a "$BACKUP_DIR/artifacts/" /srv/research/PRJ_SYNTH_01/artifacts/
   ```
4. Execute storage recovery to remove stale temporary files:
   ```bash
   research project recover PRJ_SYNTH_01
   ```
5. Run full integrity audit to verify state restoration:
   ```bash
   research project audit PRJ_SYNTH_01
   ```

---

## 7. Architectural Integration Status Disclosures

The Research Harness 9.0 engine strictly enforces architectural boundaries. To prevent operational ambiguity on host D13, integration capabilities are explicitly categorized below:

### 7.1 CLI-Supported Operations
* `research project init`: Initialize append-only project repository and owner token.
* `research project actor {add,update,disable}`: Role-based actor management with in-transaction authorization.
* `research project audit`: Deep cryptographic integrity audit (Merkle roots, CAS blobs, event hash chain).
* `research project recover`: Atomic clean-up of staging files, orphan identification, and WAL checkpoint.
* `research project upgrade-schema`: Idempotent schema migration for live projects.
* `research project migrate-v8`: Non-destructive legacy LaTeX/v8.4.4 migration with automated backups.
* `research snapshot export`: HMAC-signed, state-pinned snapshot generation with scoped visibility.
* `research snapshot verify`: Cryptographic verification of snapshot HMAC signature and project origin.
* `research proposal validate`: Static validation of JSON proposals against structural schemas and guards.
* `research proposal import`: Transactional proposal ingestion via single-writer SQLite WAL with optimistic conflict rejection.
* `research review record`: Privileged human peer review authorization (Author sign-off / Independent review).
* `research dependency impact`: Transitive dependency analysis and cascading invalidation simulation.
* `research history`: Full chronological revision and status timeline for any object.
* `research release-check`: Multi-dimensional scientific release gate under claim-proportional policy.
* `research schema export`: Export all formal JSON schemas for protocol interoperability.

### 7.2 Direct ChatGPT Web Integration Status
* **Status:** `NOT_IMPLEMENTED`
* **Details:** Research Harness 9.0 does not include a direct browser extension, OAuth2 callback endpoint, or web-based ChatGPT session bridge. AI agents interact with the harness exclusively via the offline proposal gateway protocol (exporting snapshots $\to$ generating JSON proposals $\to$ importing via CLI).

### 7.3 HTTP / MCP (Model Context Protocol) Integration Status
* **Status:** `DEFERRED / NOT_IMPLEMENTED`
* **Details:** Research Harness 9.0 does not expose an HTTP daemon or MCP server. Execution is local and transactional via the `research` CLI. Remote agents must submit signed proposal files through standard file transport or SSH execution.

### 7.4 Features Not Yet Implemented
* Automated distributed consensus across multi-host SQLite replicas (single-host WAL architecture).
* Real-time WebSocket streaming of live event logs.
* Direct integration with cloud CAS providers (e.g., S3/GCS) beyond the local filesystem CAS.

---

## 8. Live D13 Host Acceptance Checklist

Before declaring production operational status on D13:
- [ ] Confirm `/srv/research` is mounted on durable, resilient storage with write caching verified safe.
- [ ] Verify directory ownership `rharness:rharness` and permissions `0750`.
- [ ] Verify SQLite WAL mode: `sqlite3 /srv/research/PRJ/state.sqlite3 'PRAGMA journal_mode;'` returns `wal`.
- [ ] Verify `EDGE_SEAL_TRIGGER` is active: `SELECT name FROM sqlite_master WHERE type='trigger' AND name='edges_sealed_at_revision_creation';`.
- [ ] Execute `pytest tests/` (all 80 tests passing) in the live `/opt/research-harness/venv` environment on host D13.
- [ ] Execute online backup and recovery restore drill on test project `PRJ_D13_SMOKE`.
- [ ] Record host sign-off in `reports/D13_ACCEPTANCE_LOG.md`.
