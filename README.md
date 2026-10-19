# Research Harness 9.0

[![CI](https://github.com/quanpt-sfa/research-suite/actions/workflows/ci.yml/badge.svg)](https://github.com/quanpt-sfa/research-suite/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Release](https://img.shields.io/badge/release-9.0.0-success.svg)](https://github.com/quanpt-sfa/research-suite)

**Research Harness 9.0** is an append-only, transactional research execution engine and cryptographic provenance platform. It enforces a strict separation of powers between automated discovery agents, deterministic validation code, and authenticated human authorities.

> **Untrusted agents propose; deterministic code validates; authenticated humans authorize; scientific claims remain traceable to their evidence, assumptions, and inferential limitations.**

---

## Key Architectural Pillars

1. **Transactional SQLite Core (WAL Mode):**
   - Single-writer/multiple-reader concurrency with `BEGIN IMMEDIATE` transactions, eliminating lock-upgrade deadlocks and TOCTOU vulnerabilities.
   - Append-only database triggers prevent in-place modification of historical revisions, events, proposals, and edge graphs.
2. **Content-Addressed Storage (CAS):**
   - Manuscript drafts, raw proposals, and experimental datasets are published via a two-phase protocol (`staging` $\to$ `fsync` $\to$ atomic `os.replace`).
   - Blobs are named and indexed by their SHA-256 hashes. Metadata commits only after file durability is guaranteed.
3. **Cryptographically Sealed Revision Digests:**
   - Every object revision is sealed with a Merkle digest combining its canonical content hash with its sorted outgoing edges.
   - Enforced by database trigger `edges_sealed_at_revision_creation`—late or retroactive edge tampering fails closed.
4. **Cascading Dependency Invalidation Engine:**
   - Updates to underlying evidence, proxies, or theoretical constructs propagate transitive `NEEDS_REVIEW` flags across dependent claims via breadth-first search (BFS), with complete cycle tolerance.
5. **HARKing & Post-Hoc Defense:**
   - Immutable formation records capture the exact state of empirical results when a hypothesis or claim was formulated.
   - Claims modified after empirical results are recorded are automatically tagged with a provenance downgrade warning.
6. **Multi-Dimensional Claim-Proportional Release Gate:**
   - Evaluates 6 distinct dimensions: Structural Integrity, Provenance Integrity, Workflow Completeness, Semantic Adjudication, Scientific Review, and Overall Eligibility.
   - Supports 6 scientific claim tiers (`descriptive`, `associational`, `predictive`, `causal`, `conceptual`, `measurement_validation`), ensuring causal claims cannot pass on mere descriptive citations.

---

## Installation

Ensure Python 3.10 or newer is installed:

```bash
git clone https://github.com/quanpt-sfa/research-suite.git
cd research-suite
pip install -e .[test]
```

---

## CLI Quickstart

The `research` CLI provides administrative and operational commands:

### 1. Initialize a Research Project
```bash
# Sets up state database, CAS directories, and HMAC keys under $RESEARCH_HOME
research project init PRJ_DEMO_01 --owner-id lead_investigator
```
*Outputs an Owner Bearer Token (`rhk_...`). Save this token securely.*

### 2. Manage Authenticated Actors
```bash
# Register an author
research project actor add PRJ_DEMO_01 \
  --token "$OWNER_TOKEN" \
  --actor-id author_alice \
  --display-name "Dr. Alice" \
  --roles author \
  --group group_alpha

# Register an independent reviewer (must belong to distinct independence group)
research project actor add PRJ_DEMO_01 \
  --token "$OWNER_TOKEN" \
  --actor-id reviewer_bob \
  --display-name "Prof. Bob" \
  --roles independent_reviewer \
  --group group_beta
```

### 3. Export a Scoped Snapshot
```bash
# Export an HMAC-signed, state-pinned snapshot for a literature search agent
research snapshot export PRJ_DEMO_01 --token "$OWNER_TOKEN" --scope literature --out snap_lit.json
```

### 4. Import an Untrusted Proposal
```bash
# Ingest an external agent proposal (validated against schema, scope, and optimistic read-set)
research proposal import PRJ_DEMO_01 proposal.json --token "$ALICE_TOKEN"
```

### 5. Review & Authorize Claims
```bash
# Author sign-off
research review record PRJ_DEMO_01 \
  --token "$ALICE_TOKEN" \
  --object-id CLAIM_01 \
  --revision 1 \
  --kind author_approval \
  --decision approve \
  --rationale "Empirical findings verified."

# Privileged independent peer review
research review record PRJ_DEMO_01 \
  --token "$BOB_TOKEN" \
  --object-id CLAIM_01 \
  --revision 1 \
  --kind independent_review \
  --decision approve \
  --rationale "Methodology and statistical power validated."
```

### 6. Modular Research Workflows (Research Suite 9.1)

Research Suite 9.1 provides five independently callable research workflows built above the Research Harness state engine:

```bash
# List available workflows and their contract inputs/outputs
research workflow list

# Preview selective context and token budget for a task (offline, zero LLM)
research context preview PRJ_DEMO_01 \
  --workflow contribution \
  --question RQ_01 \
  --budget 12000

# Execute Contribution Finding workflow
research workflow run PRJ_DEMO_01 contribution \
  --question RQ_01 \
  --focus-claim CLAIM_01 \
  --budget 12000

# Execute Methods workflow with a discipline profile (e.g. accounting, finance, education)
research workflow run PRJ_DEMO_01 methods \
  --question RQ_01 \
  --discipline accounting

# Inspect token usage and task accounting
research usage report PRJ_DEMO_01
```

### 7. Audit Integrity & Scientific Release Check
```bash
# Perform deep cryptographic audit across SQLite, event hash chain, and CAS blobs
research project audit PRJ_DEMO_01

# Perform multi-dimensional release gate validation
research project release-check PRJ_DEMO_01
```

---

## Testing & Verification

Run the full modern test suite (142 tests, ~25s runtime):
```bash
pytest tests/ -v
```

All 142 modern tests and 10 workflow scenarios pass with a 100% pass rate.

---

## Documentation

Comprehensive architecture, workflow, and verification documentation:

- [Modular Workflows Architecture & Benchmark](docs/MODULAR_WORKFLOWS_V9_1.md) (`docs/MODULAR_WORKFLOWS_V9_1.md`): Deep Agents patterns adopted vs. rejected, Context Builder design, benchmark metrics, and workflow contracts.
- [Architecture Overview](docs/ARCHITECTURE.md) (`docs/ARCHITECTURE.md`): Core layering and protocol specification.
- [P0 Remediation Report](docs/P0_REMEDIATION_REPORT.md) (`docs/P0_REMEDIATION_REPORT.md`): Detailed root-cause fixes for defects A1–A4.
- [Legacy Semantic Parity Matrix](docs/LEGACY_SEMANTIC_PARITY.md) (`docs/LEGACY_SEMANTIC_PARITY.md`): Full mapping across all 11 scientific stages.
- [Scientific Release Policy](docs/SCIENTIFIC_RELEASE_POLICY.md) (`docs/SCIENTIFIC_RELEASE_POLICY.md`): Specification of the 6 claim tiers and gate rules.
- [Cryptographic Integrity Audit Spec](docs/INTEGRITY_AUDIT_SPEC.md) (`docs/INTEGRITY_AUDIT_SPEC.md`): Hashing schemes, Merkle roots, and CAS protocols.
- [Threat Model](docs/THREAT_MODEL.md) (`docs/THREAT_MODEL.md`): Adversarial boundaries, untrusted agents, and defense-in-depth guarantees.

---

## License

This project is licensed under the MIT License.
