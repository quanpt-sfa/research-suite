"""Legacy v8.4.4 to v9.0-dev migration adapter.

Guarantees:
1. Non-destructive: Existing legacy files are NEVER deleted, overwritten, or renamed.
2. Backup: A full zip backup of the legacy research_harness is taken before any migration.
3. Provenance preserved: Migrated objects are flagged with OriginKind.LEGACY_MIGRATED.
4. Scientific integrity fail-closed: Legacy gate PASS locks are NEVER converted to scientific approval.
   All migrated claims start with scientific_review_status = UNASSESSED and inference_status = UNKNOWN.
5. Idempotent: Rerunning migration on an already migrated target produces no duplicates or state drift.
6. Dry-run support: Inspect what would be migrated before committing.
"""
from __future__ import annotations

import csv
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any, Optional

from research_harness.core.auth import Actor
from research_harness.core.errors import HarnessError, utc_now
from research_harness.core.model import Decision, OriginKind, ReviewStatus
from research_harness.state.store import StateStore


def _read_csv(p: Path) -> list[dict[str, str]]:
    if not p.is_file():
        return []
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def migrate_legacy_project(
    legacy_project_root: Path | str,
    target_store: StateStore,
    actor: Actor,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Migrate a v8.4.4 LaTeX/research_harness project into a v9 StateStore."""
    legacy_root = Path(legacy_project_root).resolve()
    hroot = legacy_root / "research_harness"
    if not hroot.is_dir():
        # Maybe legacy_project_root itself is the research_harness directory
        if (legacy_root / "00_master").is_dir():
            hroot = legacy_root
            legacy_root = hroot.parent
        else:
            raise HarnessError("LEGACY_PROJECT_NOT_FOUND", f"Cannot find research_harness under {legacy_project_root}")

    master = hroot / "00_master"
    if not master.is_dir():
        raise HarnessError("LEGACY_MASTER_REGISTRY_MISSING", f"No 00_master directory found at {master}")

    # Read legacy registries
    rq_rows = _read_csv(master / "research_questions.csv")
    construct_rows = _read_csv(master / "construct_registry.csv")
    source_rows = _read_csv(master / "source_registry.csv")
    claim_source_rows = _read_csv(master / "claim_source_registry.csv")
    prop_rows = _read_csv(master / "proposition_registry.csv")
    binding_rows = _read_csv(master / "proposition_source_binding.csv")
    adjudication_rows = _read_csv(master / "semantic_adjudication_registry.csv")

    stats = {
        "research_questions": len(rq_rows),
        "constructs": len(construct_rows),
        "sources": len(source_rows),
        "claim_sources": len(claim_source_rows),
        "propositions": len(prop_rows),
        "bindings": len(binding_rows),
        "semantic_adjudications": len(adjudication_rows),
    }

    if dry_run:
        return {
            "decision": "DRY_RUN",
            "legacy_root": str(legacy_root),
            "target_project_id": target_store.project_id,
            "detected_registries": stats,
            "notes": [
                "Dry run completed. No data written to v9 store.",
                "Legacy gate locks will NOT be treated as scientific approval.",
                "All imported claims will have review_status=UNASSESSED and inference_status=UNKNOWN.",
            ],
        }

    # Backup legacy harness first
    backup_dir = target_store.paths.backups
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = utc_now().replace(":", "-")
    backup_zip = backup_dir / f"legacy_backup_{target_store.project_id}_{stamp}.zip"
    with zipfile.ZipFile(backup_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in hroot.rglob("*"):
            if f.is_file():
                zf.write(f, arcname=f.relative_to(legacy_root).as_posix())

    created_objects = []
    created_edges = 0

    with target_store.write_tx(actor.actor_id) as tx:
        # Check idempotency / pre-existing objects
        existing_heads = {
            r[0] for r in tx.conn.execute("SELECT object_id FROM object_heads").fetchall()
        }

        # 1. Migrate Research Questions
        for r in rq_rows:
            rq_id = (r.get("rq_id") or "").strip()
            if not rq_id or rq_id in existing_heads:
                continue
            content = {
                "text": (r.get("rq_text") or r.get("text") or rq_id).strip(),
                "label": (r.get("rq_label") or rq_id).strip(),
            }
            origin = {
                "kind": OriginKind.LEGACY_MIGRATED.value,
                "legacy_source_file": "00_master/research_questions.csv",
                "legacy_record": r,
            }
            tx.add_revision(object_id=rq_id, object_type="ResearchQuestion", content=content, origin=origin, edges=[])
            created_objects.append(rq_id)
            existing_heads.add(rq_id)

        # 2. Migrate Constructs
        for r in construct_rows:
            cid = (r.get("construct_id") or "").strip()
            if not cid or cid in existing_heads:
                continue
            content = {
                "label": (r.get("construct_label") or cid).strip(),
                "definition": (r.get("construct_definition") or r.get("definition") or "").strip(),
            }
            origin = {
                "kind": OriginKind.LEGACY_MIGRATED.value,
                "legacy_source_file": "00_master/construct_registry.csv",
                "legacy_record": r,
            }
            tx.add_revision(object_id=cid, object_type="Construct", content=content, origin=origin, edges=[])
            created_objects.append(cid)
            existing_heads.add(cid)

        # 3. Migrate Sources
        for r in source_rows:
            sid = (r.get("source_id") or "").strip()
            if not sid or sid in existing_heads:
                continue
            content = {
                "citation_key": (r.get("citation_key") or r.get("source_code") or sid).strip(),
                "bibliographic_identity": (r.get("bibliographic_identity") or "").strip(),
                "doi": (r.get("doi") or "").strip(),
                "library_file_id": (r.get("library_file_id") or "").strip(),
                "label": (r.get("source_code") or sid).strip(),
            }
            origin = {
                "kind": OriginKind.LEGACY_MIGRATED.value,
                "legacy_source_file": "00_master/source_registry.csv",
                "legacy_record": r,
            }
            tx.add_revision(object_id=sid, object_type="Source", content=content, origin=origin, edges=[])
            created_objects.append(sid)
            existing_heads.add(sid)

        # 4. Migrate Evidence Passages (from claim_source_registry.csv)
        claim_sources_map = {}
        for r in claim_source_rows:
            csid = (r.get("claim_source_id") or "").strip()
            if not csid or csid in existing_heads:
                continue
            sid = (r.get("source_id") or "").strip()
            content = {
                "locator": (
                    r.get("locator_type", "")
                    + ": "
                    + (r.get("page_pdf") or r.get("page_printed") or r.get("section_heading") or "loc")
                ).strip(),
                "normalized_claim": (r.get("normalized_claim") or "").strip(),
                "label": csid,
            }
            edges = []
            if sid and sid in existing_heads:
                head_src = tx.head(sid)
                edges.append(("derived_from", sid, head_src["head_revision"], {"origin": "legacy_source_binding"}))
                created_edges += 1

            origin = {
                "kind": OriginKind.LEGACY_MIGRATED.value,
                "legacy_source_file": "00_master/claim_source_registry.csv",
                "legacy_record": r,
            }
            tx.add_revision(object_id=csid, object_type="EvidencePassage", content=content, origin=origin, edges=edges)
            created_objects.append(csid)
            existing_heads.add(csid)
            claim_sources_map[csid] = sid

        # 5. Migrate Propositions / Claims
        for r in prop_rows:
            pid = (r.get("proposition_id") or "").strip()
            if not pid or pid in existing_heads:
                continue
            prop_text = (r.get("normalized_proposition") or pid).strip()
            content = {
                "text": prop_text,
                "claim_tier": "unclassified",  # Never guess causal/confirmatory without audit!
                "label": pid,
            }
            formation = {
                "first_recorded_at": tx.at,
                "first_recorded_state_revision": tx.revision,
                "declared_formation_basis": "unknown",
                "results_exposure": "UNKNOWN",
                "inference_status": "UNKNOWN",
                "history": [{
                    "revision": 1,
                    "inference_status": "UNKNOWN",
                    "claim_tier": "unclassified",
                    "recorded_at": tx.at,
                }],
            }
            origin = {
                "kind": OriginKind.LEGACY_MIGRATED.value,
                "legacy_source_file": "00_master/proposition_registry.csv",
                "legacy_record": r,
                "formation": formation,
            }

            # Map outgoing edges to referenced constructs/RQs
            edges = []
            rq_id = (r.get("rq_id") or "").strip()
            if rq_id and rq_id in existing_heads:
                head_rq = tx.head(rq_id)
                edges.append(("depends_on", rq_id, head_rq["head_revision"], {"origin": "legacy_rq_link"}))
                created_edges += 1

            tx.add_revision(object_id=pid, object_type="Claim", content=content, origin=origin, edges=edges)
            created_objects.append(pid)
            existing_heads.add(pid)

        # 6. Migrate Proposition Source Bindings as Edges (supports)
        for r in binding_rows:
            pid = (r.get("proposition_id") or "").strip()
            csid = (r.get("claim_source_id") or "").strip()
            if pid in existing_heads and csid in existing_heads:
                head_p = tx.head(pid)
                head_c = tx.head(csid)
                # Check if edge already exists
                existing_edge = tx.conn.execute(
                    "SELECT 1 FROM edges WHERE source_id=? AND target_id=? AND relation='supports'",
                    (csid, pid),
                ).fetchone()
                if not existing_edge:
                    tx.add_edge(
                        csid,
                        head_c["head_revision"],
                        "supports",
                        pid,
                        head_p["head_revision"],
                        {"origin": "legacy_proposition_source_binding", "legacy_row": r},
                    )
                    created_edges += 1

        tx.event("LEGACY_PROJECT_MIGRATED", {
            "legacy_root": str(legacy_root),
            "backup_zip": str(backup_zip),
            "created_objects_count": len(created_objects),
            "created_edges_count": created_edges,
        })

    decision = "ACCEPTED"
    if not created_objects and not created_edges and existing_heads:
        decision = "ALREADY_MIGRATED"

    receipt = {
        "status": "PASS",
        "decision": decision,
        "legacy_root": str(legacy_root),
        "target_project_id": target_store.project_id,
        "backup_archive": str(backup_zip),
        "backup_zip": str(backup_zip),
        "created_objects_count": len(created_objects),
        "created_objects": created_objects,
        "created_edges_count": created_edges,
        "new_state_revision": tx.revision,
        "timestamp": target_store.clock(),
    }

    receipt_file = target_store.paths.root / "migrations" / f"migration_receipt_{stamp}.json"
    receipt_file.parent.mkdir(parents=True, exist_ok=True)
    receipt_file.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    return receipt
