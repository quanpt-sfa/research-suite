# Migration Guide: Upgrading from Research Harness v8.4.4 to v9.0-dev

## 1. Migration Overview & Core Guarantees

Research Harness 9.0 includes an automated migration adapter (`legacy/adapter.py`) designed to ingest existing LaTeX research harness projects from v8.4.4 into the v9 StateStore.

### Non-Negotiable Safety Invariants:
1. **Non-Destructive:** Existing legacy files, directories, LaTeX chapters, and CSV registries are **never deleted, overwritten, or renamed**.
2. **Automated Backup Archive:** Before any data is written to the v9 database, a complete ZIP archive of the legacy `research_harness` tree is created under `<project_root>/backups/`.
3. **Full Provenance Retention:** Every migrated object records `origin.kind = "LEGACY_MIGRATED"`, retaining the original CSV file path and row content.
4. **Fail-Closed Scientific Verification:** **Legacy stage PASS locks are never converted to scientific approval.** All imported claims start with `scientific_review_status = "UNASSESSED"` and `inference_status = "UNKNOWN"`.
5. **Strict Idempotency:** Rerunning migration on an already migrated project returns `ALREADY_MIGRATED` without creating duplicate objects, duplicate edges, or revision drift.

---

## 2. Legacy Registry to v9 Object Mapping

The migration adapter parses the registries located in `research_harness/00_master/`:

| Legacy Registry File | Legacy Identity Key | v9 Object Type | Created Edges / Semantics |
|:---|:---|:---|:---|
| `research_questions.csv` | `rq_id` | `ResearchQuestion` | Top-level research inquiry |
| `construct_registry.csv` | `construct_id` | `Construct` | Theoretical conceptual definitions |
| `source_registry.csv` | `source_id` | `Source` | Bibliographic references & DOIs |
| `claim_source_registry.csv` | `claim_source_id` | `EvidencePassage` | Empirical or literature excerpts |
| `proposition_registry.csv` | `proposition_id` | `Claim` | Scientific hypotheses and claims |
| `proposition_source_binding.csv` | `proposition_id` + `claim_source_id` | Edge: `supports` | Connects EvidencePassage to Claim |
| `semantic_adjudication_registry.csv`| `adjudication_id` | `Adjudication` | Source integrity & semantic entailment verdicts |

---

## 3. Step-by-Step Migration Instructions

### Step 3.1: Initialize Target v9 Project
Initialize a fresh project directory managed by Research Harness 9.0:
```bash
research project init PRJ_MIGRATED_2026 --owner alice_owner
```
*Note the returned owner token (`rhk_...`).*

### Step 3.2: Execute Migration Dry-Run
Inspect the legacy project structure and verify registry counts without writing any data:
```bash
research project migrate-v8 PRJ_MIGRATED_2026 \
  --legacy-root /path/to/legacy/latex_project \
  --token <OWNER_TOKEN> \
  --dry-run
```
Output:
```json
{
  "decision": "DRY_RUN",
  "detected_registries": {
    "research_questions": 3,
    "constructs": 5,
    "sources": 12,
    "claim_sources": 18,
    "propositions": 7,
    "bindings": 18,
    "semantic_adjudications": 7
  },
  "notes": [
    "Dry run completed. No data written to v9 store.",
    "Legacy gate locks will NOT be treated as scientific approval.",
    "All imported claims will have review_status=UNASSESSED and inference_status=UNKNOWN."
  ]
}
```

### Step 3.3: Run Migration
Execute the full migration:
```bash
research project migrate-v8 PRJ_MIGRATED_2026 \
  --legacy-root /path/to/legacy/latex_project \
  --token <OWNER_TOKEN>
```
Output confirms:
- Backup ZIP archived to `<project_root>/backups/legacy_backup_PRJ_MIGRATED_2026_<timestamp>.zip`
- All objects and relation edges created in a single atomic transaction.
- New state revision committed.

### Step 3.4: Verify Integrity & Review Status
Run the integrity audit to ensure database and artifact consistency:
```bash
research project audit PRJ_MIGRATED_2026
```
Run release check to observe the fail-closed status:
```bash
research release-check PRJ_MIGRATED_2026
```
Because migrated claims start as `UNASSESSED`, the project will report:
- `IntegrityStatus: PASS`
- `WorkflowStatus: COMPLETE`
- `ScientificReviewStatus: UNASSESSED`
- `ReleaseEligibility: BLOCKED` (Reason: `CLAIMS_UNASSESSED`)

To release the migrated project, qualified peer reviewers must independently review and approve each claim using `research review record`.

---

## 4. Rollback & Recovery Procedures

If migration needs to be reversed:
1. The original legacy LaTeX repository was untouched and remains immediately usable by v8.4.4.
2. The complete pre-migration backup ZIP exists in `<project_root>/backups/`.
3. The v9 state can be reset or re-initialized at any time by running `research project init` with a new project ID.
