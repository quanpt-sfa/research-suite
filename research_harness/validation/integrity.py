"""Integrity and release eligibility verification.

Separates four distinct statuses:
1. IntegrityStatus: PASS | FAIL | UNKNOWN
2. WorkflowStatus: NOT_STARTED | IN_PROGRESS | INCOMPLETE | COMPLETE
3. ReviewStatus: UNASSESSED | NEEDS_REVIEW | AUTHOR_APPROVED | INDEPENDENTLY_REVIEWED | REJECTED
4. ReleaseEligibility: BLOCKED | ELIGIBLE

Rule: A project with empty or incomplete scientific objects, or with open invalidations,
unassessed claims, or missing independent reviews, CANNOT pass release-check.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional

from research_harness.core.model import (
    IntegrityStatus,
    Lifecycle,
    ReleaseEligibility,
    ReviewStatus,
    WorkflowStatus,
)
from research_harness.graph.dependency import stale_pinned_edges
from research_harness.provenance.events import verify_chain
from research_harness.state.store import StateStore, compute_state_digest


def verify_database_integrity(store: StateStore) -> list[dict]:
    findings = []
    with store.read() as conn:
        # 1. SQLite low-level integrity
        quick = conn.execute("PRAGMA quick_check").fetchall()
        for r in quick:
            if r[0] != "ok":
                findings.append({"rule": "SQLITE_CORRUPTION", "message": str(r[0])})

        # 2. Foreign keys check
        fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        for r in fk_violations:
            findings.append({"rule": "FOREIGN_KEY_VIOLATION", "table": r[0], "rowid": r[1], "parent": r[2]})

        # 3. Hash-chained event log verification
        chain_findings = verify_chain(conn)
        findings.extend(chain_findings)

        # 4. State revision digest verification
        latest_rev_row = conn.execute("SELECT revision, state_sha256 FROM state_revisions ORDER BY revision DESC LIMIT 1").fetchone()
        if latest_rev_row:
            actual_digest = compute_state_digest(conn)
            if actual_digest != latest_rev_row[1]:
                findings.append({
                    "rule": "STATE_DIGEST_MISMATCH",
                    "recorded": latest_rev_row[1],
                    "actual": actual_digest,
                    "state_revision": latest_rev_row[0],
                })

    # 5. Artifact verification
    for digest in store.artifacts.blobs():
        if not store.artifacts.verify(digest):
            findings.append({"rule": "ARTIFACT_BLOB_CORRUPT", "digest": digest})

    return findings


def audit_project_integrity(store: StateStore) -> dict[str, Any]:
    findings = verify_database_integrity(store)
    status = IntegrityStatus.PASS if not findings else IntegrityStatus.FAIL
    return {
        "status": status.value,
        "project_id": store.project_id,
        "findings": findings,
    }


def release_check(store: StateStore) -> dict[str, Any]:
    """Perform comprehensive release-check on the project.
    
    In P0/P1, releases are strictly controlled:
    - Integrity must PASS.
    - Research must have core research objects (Question, Theory/Mechanism/Proxy, Claim, Result/Evidence).
      An empty or placeholder project is INCOMPLETE and BLOCKED.
    - Every claim must be AUTHOR_APPROVED or INDEPENDENTLY_REVIEWED (no UNASSESSED or NEEDS_REVIEW).
    - No open invalidations or stale pinned edges.
    """
    integrity_res = audit_project_integrity(store)
    int_status = IntegrityStatus.PASS if integrity_res["status"] == "PASS" else IntegrityStatus.FAIL

    block_reasons: list[str] = []
    if int_status != IntegrityStatus.PASS:
        block_reasons.append("PROJECT_INTEGRITY_FAILED")

    with store.read() as conn:
        # Check object populations
        heads = conn.execute(
            "SELECT object_id, object_type, head_revision, lifecycle_status FROM object_heads WHERE lifecycle_status='ACTIVE'"
        ).fetchall()

        type_counts: dict[str, int] = {}
        for h in heads:
            type_counts[h["object_type"]] = type_counts.get(h["object_type"], 0) + 1

        claims = conn.execute(
            """SELECT o.object_id, o.revision, s.scientific_review_status, o.content
               FROM object_heads h
               JOIN objects o ON o.object_id = h.object_id AND o.revision = h.head_revision
               JOIN object_status s ON s.object_id = o.object_id AND s.revision = o.revision
               WHERE h.object_type = 'Claim' AND h.lifecycle_status = 'ACTIVE'"""
        ).fetchall()

        open_invalidations = conn.execute(
            "SELECT inval_id, object_id, revision, cause_object_id FROM invalidations WHERE status='OPEN'"
        ).fetchall()

    # Completeness check
    total_objects = len(heads)
    has_rq = type_counts.get("ResearchQuestion", 0) > 0
    has_claims = len(claims) > 0
    has_evidence = (
        type_counts.get("Result", 0) > 0
        or type_counts.get("EvidencePassage", 0) > 0
        or type_counts.get("Source", 0) > 0
    )

    if total_objects == 0 or not (has_rq and has_claims and has_evidence):
        workflow_status = WorkflowStatus.INCOMPLETE
        block_reasons.append("RESEARCH_INCOMPLETE: missing core scientific objects (RQ, Claim, Evidence)")
    else:
        workflow_status = WorkflowStatus.COMPLETE

    # Scientific review status evaluation
    if not has_claims:
        sci_review_status = ReviewStatus.UNASSESSED
        block_reasons.append("NO_CLAIMS_TO_REVIEW")
    else:
        statuses = {c["scientific_review_status"] for c in claims}
        if any(s == ReviewStatus.REJECTED.value for s in statuses):
            sci_review_status = ReviewStatus.REJECTED
            block_reasons.append("CONTAINS_REJECTED_CLAIMS")
        elif any(s == ReviewStatus.NEEDS_REVIEW.value for s in statuses):
            sci_review_status = ReviewStatus.NEEDS_REVIEW
            block_reasons.append("CLAIMS_NEED_REVIEW")
        elif any(s == ReviewStatus.UNASSESSED.value for s in statuses):
            sci_review_status = ReviewStatus.UNASSESSED
            block_reasons.append("CLAIMS_UNASSESSED")
        elif all(s == ReviewStatus.INDEPENDENTLY_REVIEWED.value for s in statuses):
            sci_review_status = ReviewStatus.INDEPENDENTLY_REVIEWED
        elif all(s in {ReviewStatus.AUTHOR_APPROVED.value, ReviewStatus.INDEPENDENTLY_REVIEWED.value} for s in statuses):
            sci_review_status = ReviewStatus.AUTHOR_APPROVED
        else:
            sci_review_status = ReviewStatus.NEEDS_REVIEW
            block_reasons.append("MIXED_REVIEW_STATUS")

    if open_invalidations:
        block_reasons.append(f"OPEN_INVALIDATIONS_EXIST: {len(open_invalidations)} unacknowledged invalidation(s)")

    # Check for unacknowledged stale pinned dependencies on head claims
    stale_deps_count = 0
    with store.read() as conn:
        for c in claims:
            pins = stale_pinned_edges(conn, c["object_id"], c["revision"])
            if pins:
                acked_pins = set()
                approvals = conn.execute(
                    "SELECT content FROM objects WHERE object_type='Approval' "
                    "AND json_extract(content, '$.subject.object_id')=? "
                    "AND json_extract(content, '$.subject.revision')=? "
                    "AND json_extract(content, '$.decision')='approve'",
                    (c["object_id"], c["revision"]),
                ).fetchall()
                for a in approvals:
                    try:
                        acont = json.loads(a[0])
                        for p_str in acont.get("acknowledged_stale_pins", []):
                            acked_pins.add(p_str)
                    except Exception:
                        pass
                unacked = [
                    p for p in pins
                    if f"{p['relation']}:{p['pinned']}->head@{p['current_head']}" not in acked_pins
                ]
                stale_deps_count += len(unacked)
    if stale_deps_count > 0:
        block_reasons.append(f"STALE_DEPENDENCIES_PINNED: {stale_deps_count} edge(s) point to superseded revisions")

    # Final eligibility determination
    if not block_reasons and int_status == IntegrityStatus.PASS and workflow_status == WorkflowStatus.COMPLETE and sci_review_status in {ReviewStatus.AUTHOR_APPROVED, ReviewStatus.INDEPENDENTLY_REVIEWED}:
        release_eligibility = ReleaseEligibility.ELIGIBLE
    else:
        release_eligibility = ReleaseEligibility.BLOCKED

    return {
        "project_id": store.project_id,
        "integrity_status": int_status.value,
        "workflow_status": workflow_status.value,
        "scientific_review_status": sci_review_status.value,
        "release_eligibility": release_eligibility.value,
        "block_reasons": block_reasons,
        "active_objects_count": total_objects,
        "claims_count": len(claims),
        "open_invalidations_count": len(open_invalidations),
        "notes": [
            "IntegrityStatus checks cryptographic, relational, and storage consistency.",
            "WorkflowStatus tracks empirical and analytical completeness.",
            "ScientificReviewStatus reflects human or independent peer appraisal; it does not claim absolute scientific truth.",
            "ReleaseEligibility verifies whether current project state meets gate requirements.",
        ],
    }
