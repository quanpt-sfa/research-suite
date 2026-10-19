"""Legacy v8.4.4 to v9.0-dev migration adapter.

Guarantees:
1. Non-destructive: Existing legacy files are NEVER deleted, overwritten, or renamed.
2. Backup: A full zip backup of the legacy research_harness is taken before any migration with sha256.
3. Provenance preserved: Migrated objects are flagged with OriginKind.LEGACY_MIGRATED.
4. Scientific integrity fail-closed: Legacy gate PASS locks are NEVER converted to scientific approval.
   All migrated claims start with scientific_review_status = UNASSESSED and inference_status = UNKNOWN.
5. Idempotent: Rerunning migration on an already migrated target produces no duplicates or state drift.
6. Edge sealing: All object revisions and outgoing dependency/support edges are created in sealed order.
7. Semantic parity: Proposition text is preserved, semantic adjudications are imported as Adjudication objects,
   and formation metadata contains complete fields for subsequent revisions.
8. Comprehensive reconciliation report: Details imported objects, edges, unmapped fields, and backup hashes.
"""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any, Optional

from research_harness.core.auth import Actor, InternalCapability, authenticate
from research_harness.core.canonical import canonical_json_hash
from research_harness.core.errors import AuthorizationError, HarnessError, utc_now
from research_harness.core.model import CLAIM_TIERS, Decision, OriginKind, ReviewStatus
from research_harness.provenance.formation import text_sha
from research_harness.state.store import StateStore


def _read_csv(p: Path) -> list[dict[str, str]]:
    if not p.is_file():
        return []
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def migrate_legacy_project(
    legacy_project_root: Path | str,
    target_store: StateStore,
    actor: Optional[Actor] = None,
    *,
    token: Optional[str] = None,
    internal_capability: Optional[InternalCapability] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Migrate a v8.4.4 LaTeX/research_harness project into a v9 StateStore."""
    if token is None:
        raise AuthorizationError("AUTHENTICATION_FAILED", "Legacy migration requires an authenticated bearer token")
    if internal_capability is not None:
        raise AuthorizationError("AUTHENTICATION_FAILED", "InternalCapability is not accepted; provide an authenticated bearer token")

    with target_store.read() as conn:
        authed_actor = authenticate(conn, token)
    if not authed_actor or not authed_actor.has_role("owner", "importer"):
        raise AuthorizationError("ROLE_NOT_AUTHORIZED", "Actor lacks required roles ('owner' or 'importer') for migration")
    if actor is not None and actor.actor_id != authed_actor.actor_id:
        raise AuthorizationError("ACTOR_MISMATCH", f"Supplied actor {actor.actor_id} does not match token actor {authed_actor.actor_id}")

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
        dry_to_create: list[str] = []
        dry_to_repair: list[str] = []
        dry_preserved: list[dict[str, Any]] = []
        dry_conflicts: list[dict[str, Any]] = []
        dry_unchanged: list[str] = []

        with target_store.read() as conn:
            existing_heads = {
                r[0] for r in conn.execute("SELECT object_id FROM object_heads").fetchall()
            }
            for r in rq_rows:
                rid = (r.get("rq_id") or "").strip()
                if rid:
                    (dry_unchanged if rid in existing_heads else dry_to_create).append(rid)
            for r in construct_rows:
                cid = (r.get("construct_id") or "").strip()
                if cid:
                    (dry_unchanged if cid in existing_heads else dry_to_create).append(cid)
            for r in source_rows:
                sid = (r.get("source_id") or "").strip()
                if sid:
                    (dry_unchanged if sid in existing_heads else dry_to_create).append(sid)
            for r in prop_rows:
                pid = (r.get("proposition_id") or "").strip()
                if not pid:
                    continue
                if pid not in existing_heads:
                    dry_to_create.append(pid)
                    continue
                head_claim = target_store.get_object(pid, conn=conn)
                c_content = head_claim.get("content", {})
                c_origin = head_claim.get("origin", {})
                c_formation = c_origin.get("formation", {})
                head_rev = head_claim["head_revision"]
                prop_text = (r.get("proposition_text") or r.get("normalized_proposition") or pid).strip()
                text_mismatch = (c_content.get("text") != prop_text)
                formation_missing = (not c_formation or "first_recorded_text_sha256" not in c_formation or not c_formation.get("history"))
                if not text_mismatch and not formation_missing:
                    dry_unchanged.append(pid)
                    continue

                rev_rows = conn.execute(
                    "SELECT revision, content, origin FROM objects WHERE object_id = ? ORDER BY revision ASC",
                    (pid,)
                ).fetchall()
                r1_content = json.loads(rev_rows[0][1]) if isinstance(rev_rows[0][1], str) else (rev_rows[0][1] or {})
                r1_origin = json.loads(rev_rows[0][2]) if isinstance(rev_rows[0][2], str) else (rev_rows[0][2] or {})
                r1_text = r1_content.get("text", "")
                r1_legacy_record = r1_origin.get("legacy_record", {})
                r1_legacy_text = (r1_legacy_record.get("proposition_text") or r1_legacy_record.get("normalized_proposition") or "").strip()

                has_research_revisions = False
                if head_rev > 1:
                    for row in rev_rows[1:]:
                        o_data = json.loads(row[2]) if row[2] else {}
                        o_kind = o_data.get("kind")
                        if o_kind in ("PROPOSAL_IMPORTED", "USER_DIRECT", "SYSTEM_DERIVED") or o_kind != "MIGRATION_REPAIRED":
                            has_research_revisions = True
                            break

                if has_research_revisions:
                    dry_preserved.append({
                        "object_id": pid, "head_revision": head_rev,
                        "current_text": c_content.get("text"), "legacy_text": prop_text,
                        "reason": "POST_MIGRATION_RESEARCH_MODIFICATION"
                    })
                    dry_unchanged.append(pid)
                elif r1_legacy_text and r1_legacy_text != prop_text:
                    dry_conflicts.append({
                        "object_id": pid, "conflict_type": "CHANGED_LEGACY_SOURCE",
                        "status": "CONFLICT_REQUIRES_HUMAN_DECISION"
                    })
                elif (text_mismatch and (r1_text == pid and prop_text != pid)) or formation_missing or r1_origin.get("defective_import") is True or r1_origin.get("repair_candidate") is True:
                    dry_to_repair.append(pid)
                else:
                    dry_conflicts.append({
                        "object_id": pid, "conflict_type": "AMBIGUOUS_PROVENANCE",
                        "status": "CONFLICT_REQUIRES_HUMAN_DECISION"
                    })
            for r in claim_source_rows:
                csid = (r.get("claim_source_id") or "").strip()
                if csid:
                    (dry_unchanged if csid in existing_heads else dry_to_create).append(csid)
            for r in adjudication_rows:
                aid = (r.get("semantic_adjudication_id") or r.get("adjudication_id") or "").strip()
                if aid:
                    (dry_unchanged if aid in existing_heads else dry_to_create).append(aid)

        return {
            "status": "PASS",
            "decision": "DRY_RUN",
            "legacy_root": str(legacy_root),
            "target_project_id": target_store.project_id,
            "detected_registries": stats,
            "repair_plan": {
                "to_create": dry_to_create,
                "to_repair": dry_to_repair,
                "preserved_research_revisions": dry_preserved,
                "conflicts_requiring_human_decision": dry_conflicts,
                "skipped_unchanged": dry_unchanged,
            },
            "notes": [
                "Dry run completed. No data written to v9 store. No backup archive created.",
                "Legacy gate locks will NOT be treated as scientific approval.",
                "All imported claims will have review_status=UNASSESSED and inference_status=UNKNOWN.",
                "Propositions will be imported with full proposition text and complete formation metadata.",
                "Semantic adjudications will be imported as persistent Adjudication objects.",
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
    backup_sha256 = _sha256_file(backup_zip)

    # Avoid duplicate backup archives if an identical backup already exists
    existing_backups = sorted(backup_dir.glob(f"legacy_backup_{target_store.project_id}_*.zip"))
    for b in existing_backups:
        if b != backup_zip and _sha256_file(b) == backup_sha256:
            try:
                backup_zip.unlink()
            except OSError:
                pass
            backup_zip = b
            break

    created_objects: list[str] = []
    created_objects_by_type: dict[str, int] = {}
    repaired_objects: list[str] = []
    repaired_objects_by_type: dict[str, int] = {}
    skipped_unchanged: list[str] = []
    preserved_research_revisions: list[dict[str, Any]] = []
    conflicts_requiring_human_decision: list[dict[str, Any]] = []
    content_mismatches: list[dict[str, Any]] = []
    provenance_mismatches: list[dict[str, Any]] = []
    created_edges = 0
    missing_refs: list[dict[str, str]] = []
    unresolved_records: list[dict[str, Any]] = []
    unmapped_fields: dict[str, list[str]] = {}

    with target_store.write_tx(token=token, require_roles=("owner", "importer")) as tx:
        actor = tx.actor
        existing_heads = {
            r[0] for r in tx.conn.execute("SELECT object_id FROM object_heads").fetchall()
        }

        # 1. Migrate Research Questions
        rq_mapped_fields = {"rq_id", "rq_text", "text", "rq_label", "label", "status", "notes"}
        if rq_rows:
            unmapped_fields["research_questions.csv"] = sorted(set(rq_rows[0].keys()) - rq_mapped_fields)
        for r in rq_rows:
            rq_id = (r.get("rq_id") or "").strip()
            if not rq_id:
                continue
            if rq_id in existing_heads:
                skipped_unchanged.append(rq_id)
                continue
            content = {
                "text": (r.get("rq_text") or r.get("text") or rq_id).strip(),
                "label": (r.get("rq_label") or r.get("label") or rq_id).strip(),
            }
            origin = {
                "kind": OriginKind.LEGACY_MIGRATED.value,
                "legacy_source_file": "00_master/research_questions.csv",
                "legacy_record": r,
            }
            tx.add_revision(object_id=rq_id, object_type="ResearchQuestion", content=content, origin=origin, edges=[])
            created_objects.append(rq_id)
            created_objects_by_type["ResearchQuestion"] = created_objects_by_type.get("ResearchQuestion", 0) + 1
            existing_heads.add(rq_id)

        # 2. Migrate Constructs
        const_mapped_fields = {"construct_id", "canonical_name", "construct_label", "construct_definition", "definition", "role", "status", "notes"}
        if construct_rows:
            unmapped_fields["construct_registry.csv"] = sorted(set(construct_rows[0].keys()) - const_mapped_fields)
        for r in construct_rows:
            cid = (r.get("construct_id") or "").strip()
            if not cid:
                continue
            if cid in existing_heads:
                skipped_unchanged.append(cid)
                continue
            content = {
                "label": (r.get("canonical_name") or r.get("construct_label") or cid).strip(),
                "definition": (r.get("construct_definition") or r.get("definition") or "").strip(),
            }
            origin = {
                "kind": OriginKind.LEGACY_MIGRATED.value,
                "legacy_source_file": "00_master/construct_registry.csv",
                "legacy_record": r,
            }
            tx.add_revision(object_id=cid, object_type="Construct", content=content, origin=origin, edges=[])
            created_objects.append(cid)
            created_objects_by_type["Construct"] = created_objects_by_type.get("Construct", 0) + 1
            existing_heads.add(cid)

        # 3. Migrate Sources
        src_mapped_fields = {"source_id", "citation_key", "source_code", "bibliographic_identity", "doi", "library_file_id", "source_type", "status", "notes"}
        if source_rows:
            unmapped_fields["source_registry.csv"] = sorted(set(source_rows[0].keys()) - src_mapped_fields)
        for r in source_rows:
            sid = (r.get("source_id") or "").strip()
            if not sid:
                continue
            if sid in existing_heads:
                skipped_unchanged.append(sid)
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
            created_objects_by_type["Source"] = created_objects_by_type.get("Source", 0) + 1
            existing_heads.add(sid)

        # 4. Migrate Propositions / Claims (Defects 1 & 3, plus Repair Strategy)
        # Note: Created BEFORE EvidencePassages so EvidencePassages can seal their `supports` edges
        # to Claims directly at revision creation time (audit finding A3).
        prop_mapped_fields = {"proposition_id", "proposition_text", "normalized_proposition", "claim_tier", "rq_id", "scientific_object_id", "status", "notes"}
        if prop_rows:
            unmapped_fields["proposition_registry.csv"] = sorted(set(prop_rows[0].keys()) - prop_mapped_fields)
        for r in prop_rows:
            pid = (r.get("proposition_id") or "").strip()
            if not pid:
                continue
            # Prioritize proposition_text over normalized_proposition over pid
            prop_text = (r.get("proposition_text") or r.get("normalized_proposition") or pid).strip()
            raw_tier = (r.get("claim_tier") or "").strip().lower()
            claim_tier = raw_tier if raw_tier in CLAIM_TIERS else "unclassified"
            tsha = text_sha(prop_text)

            formation = {
                "first_recorded_at": tx.at,
                "first_recorded_state_revision": tx.revision,
                "first_recorded_text_sha256": tsha,
                "ledger_results_at_first_recording": [],
                "declared_formation_basis": "unknown",
                "results_exposure": "UNKNOWN",
                "inference_status": "UNKNOWN",
                "history": [{
                    "revision": 1,
                    "text_sha256": tsha,
                    "claim_tier": claim_tier,
                    "inference_status": "UNKNOWN",
                    "recorded_at": tx.at,
                    "state_revision": tx.revision,
                    "declared_formation_basis": "unknown",
                }],
            }

            if pid in existing_heads:
                head_claim = tx.get(pid)
                head_rev = head_claim["head_revision"]
                c_content = head_claim.get("content", {})
                c_origin = head_claim.get("origin", {})
                c_formation = c_origin.get("formation", {})

                text_mismatch = (c_content.get("text") != prop_text)
                formation_missing = (not c_formation or "first_recorded_text_sha256" not in c_formation or not c_formation.get("history"))

                if not text_mismatch and not formation_missing:
                    skipped_unchanged.append(pid)
                    continue

                rev_rows = tx.conn.execute(
                    "SELECT revision, content, origin FROM objects WHERE object_id = ? ORDER BY revision ASC",
                    (pid,)
                ).fetchall()
                r1_content = json.loads(rev_rows[0][1]) if isinstance(rev_rows[0][1], str) else (rev_rows[0][1] or {})
                r1_origin = json.loads(rev_rows[0][2]) if isinstance(rev_rows[0][2], str) else (rev_rows[0][2] or {})
                r1_text = r1_content.get("text", "")
                r1_legacy_record = r1_origin.get("legacy_record", {})
                r1_legacy_text = (r1_legacy_record.get("proposition_text") or r1_legacy_record.get("normalized_proposition") or "").strip()

                has_research_revisions = False
                if head_rev > 1:
                    for row in rev_rows[1:]:
                        o_data = json.loads(row[2]) if row[2] else {}
                        o_kind = o_data.get("kind")
                        if o_kind in ("PROPOSAL_IMPORTED", "USER_DIRECT", "SYSTEM_DERIVED") or o_kind != "MIGRATION_REPAIRED":
                            has_research_revisions = True
                            break

                if has_research_revisions:
                    preserved_research_revisions.append({
                        "object_id": pid,
                        "head_revision": head_rev,
                        "current_text": c_content.get("text"),
                        "legacy_text": prop_text,
                        "reason": "POST_MIGRATION_RESEARCH_MODIFICATION",
                    })
                    skipped_unchanged.append(pid)
                    continue

                if r1_legacy_text and r1_legacy_text != prop_text:
                    conflicts_requiring_human_decision.append({
                        "object_id": pid,
                        "head_revision": head_rev,
                        "current_text": c_content.get("text"),
                        "historical_legacy_text": r1_legacy_text,
                        "new_legacy_text": prop_text,
                        "conflict_type": "CHANGED_LEGACY_SOURCE",
                        "status": "CONFLICT_REQUIRES_HUMAN_DECISION",
                    })
                    continue

                is_known_text_corruption = (text_mismatch and (r1_text == pid and prop_text != pid))
                is_proven_defect = (
                    is_known_text_corruption
                    or formation_missing
                    or r1_origin.get("defective_import") is True
                    or r1_origin.get("repair_candidate") is True
                )

                if is_proven_defect:
                    if text_mismatch:
                        content_mismatches.append({"object_id": pid, "current_text": c_content.get("text"), "authoritative_text": prop_text})
                    if formation_missing:
                        provenance_mismatches.append({"object_id": pid, "reason": "missing_formation_fields"})

                    repaired_content = dict(c_content)
                    repaired_content["text"] = prop_text
                    repaired_content["label"] = pid
                    if "claim_tier" not in repaired_content or repaired_content["claim_tier"] == "unclassified":
                        repaired_content["claim_tier"] = claim_tier

                    repaired_formation = dict(c_formation) if c_formation else formation
                    repaired_formation.setdefault("first_recorded_text_sha256", tsha)
                    repaired_formation.setdefault("ledger_results_at_first_recording", [])
                    repaired_formation.setdefault("declared_formation_basis", "unknown")
                    repaired_formation.setdefault("results_exposure", "UNKNOWN")
                    repaired_formation.setdefault("inference_status", "UNKNOWN")
                    repaired_formation.setdefault("first_recorded_at", tx.at)
                    repaired_formation.setdefault("first_recorded_state_revision", tx.revision)

                    new_rev = head_claim["head_revision"] + 1
                    hist = list(repaired_formation.get("history", []))
                    hist.append({
                        "revision": new_rev,
                        "text_sha256": tsha,
                        "claim_tier": repaired_content["claim_tier"],
                        "inference_status": "UNKNOWN",
                        "recorded_at": tx.at,
                        "state_revision": tx.revision,
                        "declared_formation_basis": "unknown",
                    })
                    repaired_formation["history"] = hist

                    repaired_origin = {
                        "kind": "MIGRATION_REPAIRED",
                        "repaired_from_revision": head_claim["head_revision"],
                        "legacy_source_file": "00_master/proposition_registry.csv",
                        "legacy_record": r,
                        "formation": repaired_formation,
                        "repair_reason": "RESTORE_AUTHORITATIVE_PROPOSITION_TEXT_AND_FORMATION",
                    }

                    existing_edges = [
                        (e["relation"], e["target_id"], e["target_revision"], e["provenance"])
                        for e in target_store.edges_from(tx.conn, pid, head_claim["head_revision"])
                    ]
                    tx.add_revision(object_id=pid, object_type="Claim", content=repaired_content, origin=repaired_origin, edges=existing_edges)
                    repaired_objects.append(pid)
                    repaired_objects_by_type["Claim"] = repaired_objects_by_type.get("Claim", 0) + 1
                else:
                    conflicts_requiring_human_decision.append({
                        "object_id": pid,
                        "head_revision": head_rev,
                        "current_text": c_content.get("text"),
                        "legacy_text": prop_text,
                        "conflict_type": "AMBIGUOUS_PROVENANCE",
                        "status": "CONFLICT_REQUIRES_HUMAN_DECISION",
                    })
                continue

            content = {
                "text": prop_text,
                "claim_tier": claim_tier,
                "label": pid,
            }
            origin = {
                "kind": OriginKind.LEGACY_MIGRATED.value,
                "legacy_source_file": "00_master/proposition_registry.csv",
                "legacy_record": r,
                "formation": formation,
            }
            edges = []
            rq_id = (r.get("rq_id") or "").strip()
            if rq_id:
                if rq_id in existing_heads:
                    head_rq = tx.head(rq_id)
                    edges.append(("depends_on", rq_id, head_rq["head_revision"], {"origin": "legacy_rq_link"}))
                    created_edges += 1
                else:
                    missing_refs.append({"record": pid, "field": "rq_id", "target": rq_id})

            tx.add_revision(object_id=pid, object_type="Claim", content=content, origin=origin, edges=edges)
            created_objects.append(pid)
            created_objects_by_type["Claim"] = created_objects_by_type.get("Claim", 0) + 1
            existing_heads.add(pid)

        # 5. Migrate Evidence Passages (from claim_source_registry.csv and proposition_source_binding.csv)
        # Groups bindings by claim_source_id so all outgoing edges are attached at revision creation.
        cs_bindings: dict[str, list[dict[str, str]]] = {}
        for b in binding_rows:
            csid_b = (b.get("claim_source_id") or "").strip()
            if csid_b:
                cs_bindings.setdefault(csid_b, []).append(b)

        cs_mapped_fields = {"claim_source_id", "source_id", "claim_class", "normalized_claim", "locator_type",
                            "page_pdf", "page_printed", "section_heading", "evidence_locator", "passage_text"}
        if claim_source_rows:
            unmapped_fields["claim_source_registry.csv"] = sorted(set(claim_source_rows[0].keys()) - cs_mapped_fields)

        for r in claim_source_rows:
            csid = (r.get("claim_source_id") or "").strip()
            if not csid:
                continue
            sid = (r.get("source_id") or "").strip()
            loc_val = (r.get("evidence_locator") or r.get("page_pdf") or r.get("page_printed") or r.get("section_heading") or "loc").strip()
            loc_type = (r.get("locator_type") or "page").strip()
            locator = loc_val if loc_val.startswith(loc_type) else f"{loc_type}: {loc_val}"
            expected_norm = (r.get("normalized_claim") or "").strip()
            content = {
                "locator": locator,
                "normalized_claim": expected_norm,
                "label": csid,
            }

            if csid in existing_heads:
                head_cs = tx.get(csid)
                cur_content = head_cs.get("content", {})
                cur_loc = cur_content.get("locator", "")
                cur_norm = cur_content.get("normalized_claim", "")
                if cur_loc != locator or cur_norm != expected_norm:
                    content_mismatches.append({
                        "object_id": csid,
                        "field": "locator_or_normalized_claim",
                        "expected": {"locator": locator, "normalized_claim": expected_norm},
                        "found": cur_content,
                    })
                    repaired_origin = {
                        "kind": "MIGRATION_REPAIRED",
                        "repaired_from_revision": head_cs["head_revision"],
                        "legacy_source_file": "00_master/claim_source_registry.csv",
                        "legacy_record": r,
                        "repair_reason": "RESTORE_AUTHORITATIVE_LOCATOR_OR_NORMALIZED_CLAIM",
                    }
                    existing_edges = [
                        (e["relation"], e["target_id"], e["target_revision"], e["provenance"])
                        for e in target_store.edges_from(tx.conn, csid, head_cs["head_revision"])
                    ]
                    tx.add_revision(object_id=csid, object_type="EvidencePassage", content=content, origin=repaired_origin, edges=existing_edges)
                    repaired_objects.append(csid)
                    repaired_objects_by_type["EvidencePassage"] = repaired_objects_by_type.get("EvidencePassage", 0) + 1
                else:
                    skipped_unchanged.append(csid)
                continue

            edges = []
            if sid:
                if sid in existing_heads:
                    head_src = tx.head(sid)
                    edges.append(("derived_from", sid, head_src["head_revision"], {"origin": "legacy_source_binding"}))
                    created_edges += 1
                else:
                    missing_refs.append({"record": csid, "field": "source_id", "target": sid})

            # Attach supports edges from proposition_source_binding.csv at revision creation!
            for b in cs_bindings.get(csid, []):
                pid = (b.get("proposition_id") or "").strip()
                if pid:
                    if pid in existing_heads:
                        head_p = tx.head(pid)
                        edges.append(("supports", pid, head_p["head_revision"],
                                      {"origin": "legacy_proposition_source_binding", "legacy_row": b}))
                        created_edges += 1
                    else:
                        missing_refs.append({"record": csid, "field": "proposition_id", "target": pid})

            origin = {
                "kind": OriginKind.LEGACY_MIGRATED.value,
                "legacy_source_file": "00_master/claim_source_registry.csv",
                "legacy_record": r,
            }
            tx.add_revision(object_id=csid, object_type="EvidencePassage", content=content, origin=origin, edges=edges)
            created_objects.append(csid)
            created_objects_by_type["EvidencePassage"] = created_objects_by_type.get("EvidencePassage", 0) + 1
            existing_heads.add(csid)

        # 6. Migrate Semantic Adjudications (Defect 2 & Repair Strategy)
        adj_mapped_fields = {"semantic_adjudication_id", "adjudication_id", "proposition_id", "claim_source_id",
                            "assessment_kind", "verdict", "entailment_verdict", "rationale", "notes"}
        if adjudication_rows:
            unmapped_fields["semantic_adjudication_registry.csv"] = sorted(set(adjudication_rows[0].keys()) - adj_mapped_fields)

        adjudications_imported = 0
        adjudications_repaired = 0
        for r in adjudication_rows:
            aid = (r.get("semantic_adjudication_id") or r.get("adjudication_id") or "").strip()
            if not aid:
                continue
            pid = (r.get("proposition_id") or "").strip()
            csid = (r.get("claim_source_id") or "").strip()

            raw_v = (r.get("entailment_verdict") or r.get("verdict") or "").strip().upper()
            if raw_v in {"SUPPORTED", "ENTAILS", "PASS", "TRUE"}:
                verdict = "SUPPORTED"
            elif raw_v in {"NOT_SUPPORTED", "CONTRADICTS", "FAIL", "FALSE"}:
                verdict = "NOT_SUPPORTED"
            else:
                verdict = "UNCLEAR"

            raw_kind = (r.get("assessment_kind") or "semantic_entailment").strip()
            if raw_kind not in {"source_integrity", "semantic_entailment", "scientific_judgment"}:
                raw_kind = "semantic_entailment"

            rationale = (r.get("rationale") or r.get("notes") or f"Legacy adjudication {aid} for {pid}").strip()

            # Determine subject: Claim preferred, EvidencePassage as fallback
            # Historical adjudication must remain linked to the original historical subject revision (revision 1)
            subject = None
            if pid and pid in existing_heads:
                subject = {"object_id": pid, "revision": 1}
            elif csid and csid in existing_heads:
                subject = {"object_id": csid, "revision": 1}
            else:
                missing_refs.append({"record": aid, "field": "subject", "target": f"prop={pid},cs={csid}"})
                unresolved_records.append({"registry": "semantic_adjudication_registry.csv", "record": r, "reason": "missing_subject"})
                continue

            content = {
                "subject": subject,
                "assessment_kind": raw_kind,
                "verdict": verdict,
                "rationale": rationale,
                "is_historical": True,
                "raw_legacy_verdict": raw_v,
                "source_registry": "00_master/semantic_adjudication_registry.csv",
                "legacy_record_id": aid,
            }

            if aid in existing_heads:
                skipped_unchanged.append(aid)
                continue

            edges = []
            if csid and csid in existing_heads:
                head_cs = tx.head(csid)
                edges.append(("depends_on", csid, head_cs["head_revision"], {"origin": "legacy_adjudication_evidence"}))
                created_edges += 1
            if verdict == "SUPPORTED" and pid and pid in existing_heads:
                edges.append(("supports", pid, 1, {"origin": "legacy_adjudication_claim"}))
                created_edges += 1

            origin = {
                "kind": OriginKind.LEGACY_MIGRATED.value,
                "legacy_source_file": "00_master/semantic_adjudication_registry.csv",
                "legacy_record": r,
                "declared_historical_verdict": raw_v,
                "is_historical": True,
            }
            tx.add_revision(object_id=aid, object_type="Adjudication", content=content, origin=origin, edges=edges)
            created_objects.append(aid)
            created_objects_by_type["Adjudication"] = created_objects_by_type.get("Adjudication", 0) + 1
            existing_heads.add(aid)
            adjudications_imported += 1

        if created_objects or repaired_objects:
            tx.event("LEGACY_PROJECT_MIGRATED", {
                "legacy_root": str(legacy_root),
                "backup_zip": str(backup_zip),
                "backup_sha256": backup_sha256,
                "created_objects_count": len(created_objects),
                "created_objects_by_type": created_objects_by_type,
                "repaired_objects_count": len(repaired_objects),
                "repaired_objects_by_type": repaired_objects_by_type,
                "created_edges_count": created_edges,
                "adjudications_imported_count": adjudications_imported,
                "adjudications_repaired_count": adjudications_repaired,
            })
        else:
            tx.bump = False

    if conflicts_requiring_human_decision:
        status = "CONFLICT_REQUIRES_HUMAN_DECISION"
        decision = "CONFLICT_REQUIRES_HUMAN_DECISION"
    elif repaired_objects:
        decision = "REPAIRED"
    elif created_objects:
        decision = "ACCEPTED"
    else:
        decision = "ALREADY_MIGRATED"

    # Equivalence checks: each source registry accounted for
    equivalence_checks = {
        "research_questions_complete": len(rq_rows) == (
            created_objects_by_type.get("ResearchQuestion", 0) +
            repaired_objects_by_type.get("ResearchQuestion", 0) +
            sum(1 for x in skipped_unchanged if x in {r.get("rq_id") for r in rq_rows})
        ),
        "constructs_complete": len(construct_rows) == (
            created_objects_by_type.get("Construct", 0) +
            repaired_objects_by_type.get("Construct", 0) +
            sum(1 for x in skipped_unchanged if x in {r.get("construct_id") for r in construct_rows})
        ),
        "sources_complete": len(source_rows) == (
            created_objects_by_type.get("Source", 0) +
            repaired_objects_by_type.get("Source", 0) +
            sum(1 for x in skipped_unchanged if x in {r.get("source_id") for r in source_rows})
        ),
        "claims_complete": len(prop_rows) == (
            created_objects_by_type.get("Claim", 0) +
            repaired_objects_by_type.get("Claim", 0) +
            sum(1 for x in skipped_unchanged if x in {r.get("proposition_id") for r in prop_rows})
        ),
        "evidence_passages_complete": len(claim_source_rows) == (
            created_objects_by_type.get("EvidencePassage", 0) +
            repaired_objects_by_type.get("EvidencePassage", 0) +
            sum(1 for x in skipped_unchanged if x in {r.get("claim_source_id") for r in claim_source_rows})
        ),
    }

    full_equivalence = (
        len(missing_refs) == 0
        and len(unresolved_records) == 0
        and all(equivalence_checks.values())
    )

    if conflicts_requiring_human_decision:
        status = "CONFLICT_REQUIRES_HUMAN_DECISION"
    elif missing_refs or unresolved_records or not all(equivalence_checks.values()):
        status = "INCOMPLETE"
    elif repaired_objects or content_mismatches or provenance_mismatches or unmapped_fields:
        status = "PASS_WITH_WARNINGS"
    else:
        status = "PASS"

    status_criteria = {
        "FAILED": "Catastrophic error or database integrity violation prevented migration.",
        "CONFLICT_REQUIRES_HUMAN_DECISION": "One or more records contain ambiguous differences or modified legacy sources requiring human decision.",
        "INCOMPLETE": "One or more mandatory source records had missing or unresolved references.",
        "PASS_WITH_WARNINGS": "All records accounted for, but repairs, content mismatches, or unmapped source fields occurred.",
        "PASS": "100% equivalence demonstrated: all records mapped, zero missing references, zero unresolved records.",
    }

    reconciliation_report = {
        "status": status,
        "status_criteria": status_criteria,
        "decision": decision,
        "target_project_id": target_store.project_id,
        "legacy_root": str(legacy_root),
        "source_record_count": sum(stats.values()),
        "source_registry_row_counts": stats,
        "imported_object_count": len(created_objects),
        "imported_object_counts_by_type": created_objects_by_type,
        "repaired_object_count": len(repaired_objects),
        "repaired_object_counts_by_type": repaired_objects_by_type,
        "skipped_unchanged_count": len(skipped_unchanged),
        "skipped_unchanged": skipped_unchanged,
        "preserved_research_revisions": preserved_research_revisions,
        "conflicts_requiring_human_decision": conflicts_requiring_human_decision,
        "imported_adjudications": adjudications_imported,
        "imported_adjudication_counts": adjudications_imported,
        "repaired_adjudications": adjudications_repaired,
        "repaired_adjudication_counts": adjudications_repaired,
        "imported_edge_counts": created_edges,
        "missing_references": missing_refs,
        "missing_or_malformed_references": missing_refs,
        "unresolved_records": unresolved_records,
        "unresolved_migration_records": unresolved_records,
        "content_mismatches": content_mismatches,
        "provenance_mismatches": provenance_mismatches,
        "unmapped_source_fields": unmapped_fields,
        "pre_post_content_equivalence": equivalence_checks,
        "full_migration_equivalence_demonstrated": full_equivalence,
        "backup_location": str(backup_zip),
        "backup_checksum": backup_sha256,
        "backup_integrity_hash": backup_sha256,
        "timestamp": target_store.clock(),
    }

    report_file = target_store.paths.root / "migrations" / f"migration_reconciliation_{stamp}.json"
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(json.dumps(reconciliation_report, indent=2, sort_keys=True), encoding="utf-8")

    receipt = {
        **reconciliation_report,
        "backup_zip": str(backup_zip),
        "backup_archive": str(backup_zip),
        "created_objects_count": len(created_objects),
        "created_objects": created_objects,
        "created_edges_count": created_edges,
        "repaired_objects_count": len(repaired_objects),
        "repaired_objects": repaired_objects,
        "new_state_revision": tx.revision,
        "reconciliation_report_path": str(report_file),
    }

    return receipt
