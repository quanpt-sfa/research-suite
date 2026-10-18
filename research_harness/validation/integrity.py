"""Integrity, claim-proportional adjudication, and release eligibility verification.

Separates six distinct dimensions:
1. Structural integrity: Cryptographic digests, SQLite invariants, FKs, revision seals, CAS storage.
2. Provenance integrity: Hash-chained event log, formation declarations, HARKing safeguards.
3. Workflow completeness: Completeness tailored to research type (empirical, theoretical, conceptual, literature).
4. Semantic adjudication status: Claim-evidence entailment and methodological validity verdicts.
5. Scientific review status: Privileged human appraisal (Author Approval / Independent Review).
6. Release eligibility: Overall deterministic gate determination based on documented policy.

The overarching principle:
Untrusted agents propose; deterministic code validates; authenticated humans authorize;
scientific claims remain traceable to their evidence, assumptions, and inferential limitations.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional

from research_harness.core.canonical import canonical_json_hash
from research_harness.core.model import (
    CLAIM_TIERS,
    IntegrityStatus,
    Lifecycle,
    OriginKind,
    ReleaseEligibility,
    ReviewStatus,
    WorkflowStatus,
)
from research_harness.graph.dependency import stale_pinned_edges
from research_harness.provenance.events import verify_chain
from research_harness.state.actors import registry_findings
from research_harness.state.store import StateStore, compute_state_digest, revision_digest


def verify_database_integrity(store: StateStore) -> list[dict]:
    """Comprehensive cryptographic and relational integrity audit of the project state store.

    Verifies:
    1. SQLite low-level structural integrity (PRAGMA quick_check).
    2. Foreign key referential integrity (PRAGMA foreign_key_check).
    3. Hash-chained event log integrity and chronological sequence.
    4. Current state revision digest against cumulative state tables.
    5. Actual object content against stored content digests.
    6. Object origin/provenance against stored origin digests.
    7. Revision digests against final stored dependency edges (edge sealing verification).
    8. Referenced CAS artifacts exist and match recorded hashes.
    9. Unreferenced/orphaned CAS artifacts are identified separately.
    10. Recorded approvals are tied to correct object revision digests and authenticated actors.
    11. Actor registry consistency against replayed event log.
    """
    findings: list[dict] = []
    referenced_artifacts: set[str] = set()

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
        latest_rev_row = conn.execute(
            "SELECT revision, state_sha256 FROM state_revisions ORDER BY revision DESC LIMIT 1"
        ).fetchone()
        if latest_rev_row:
            actual_digest = compute_state_digest(conn)
            if actual_digest != latest_rev_row[1]:
                findings.append({
                    "rule": "STATE_DIGEST_MISMATCH",
                    "recorded": latest_rev_row[1],
                    "actual": actual_digest,
                    "state_revision": latest_rev_row[0],
                })

        # 5 & 6. Object content digests and origin digests verification
        obj_rows = conn.execute(
            "SELECT object_id, revision, object_type, content, content_digest, origin, origin_sha256, revision_digest "
            "FROM objects ORDER BY object_id, revision"
        ).fetchall()

        for oid, rev, otype, raw_c, stored_cdig, raw_orig, stored_osha, stored_rdig in obj_rows:
            try:
                c_data = json.loads(raw_c)
                actual_cdig = canonical_json_hash(c_data)
                if actual_cdig != stored_cdig:
                    findings.append({
                        "rule": "OBJECT_CONTENT_DIGEST_MISMATCH",
                        "object_id": oid,
                        "revision": rev,
                        "stored": stored_cdig,
                        "actual": actual_cdig,
                    })
                # Check for referenced physical CAS artifacts in content (do not treat text fingerprints as CAS blobs)
                if isinstance(c_data, dict):
                    for k in ("artifact_sha256", "blob_sha256"):
                        val = c_data.get(k)
                        if isinstance(val, str) and len(val) == 64:
                            referenced_artifacts.add(val)
            except Exception as e:
                findings.append({"rule": "OBJECT_CONTENT_MALFORMED_JSON", "object_id": oid, "revision": rev, "error": str(e)})

            try:
                orig_data = json.loads(raw_orig)
                actual_osha = canonical_json_hash(orig_data)
                if actual_osha != stored_osha:
                    findings.append({
                        "rule": "OBJECT_ORIGIN_DIGEST_MISMATCH",
                        "object_id": oid,
                        "revision": rev,
                        "stored": stored_osha,
                        "actual": actual_osha,
                    })
            except Exception as e:
                findings.append({"rule": "OBJECT_ORIGIN_MALFORMED_JSON", "object_id": oid, "revision": rev, "error": str(e)})

            # 7. Verify revision digest against outgoing edges
            edges = conn.execute(
                "SELECT relation, target_id, target_revision FROM edges "
                "WHERE source_id=? AND source_revision=? ORDER BY relation, target_id, target_revision",
                (oid, rev),
            ).fetchall()
            expected_rdig = revision_digest(stored_cdig, [(e[0], e[1], e[2]) for e in edges])
            if stored_rdig != expected_rdig:
                findings.append({
                    "rule": "REVISION_DIGEST_MISMATCH",
                    "object_id": oid,
                    "revision": rev,
                    "stored": stored_rdig,
                    "expected": expected_rdig,
                    "message": "revision digest does not match content digest combined with stored outgoing edges",
                })

            # 10. Check Approval bindings
            if otype == "Approval":
                try:
                    app_content = json.loads(raw_c)
                    subj = app_content.get("subject", {})
                    s_id, s_rev = subj.get("object_id"), subj.get("revision")
                    s_row = conn.execute(
                        "SELECT revision_digest FROM objects WHERE object_id=? AND revision=?",
                        (s_id, s_rev),
                    ).fetchone()
                    if not s_row:
                        findings.append({
                            "rule": "APPROVAL_TARGET_MISSING",
                            "object_id": oid,
                            "target_id": s_id,
                            "target_revision": s_rev,
                        })
                    else:
                        expected_subj_digest = s_row[0]
                        if app_content.get("subject_revision_digest") != expected_subj_digest:
                            findings.append({
                                "rule": "APPROVAL_DIGEST_MISMATCH",
                                "object_id": oid,
                                "recorded_target_digest": app_content.get("subject_revision_digest"),
                                "actual_target_digest": expected_subj_digest,
                            })
                    # Check approving actor
                    actor_id = app_content.get("actor_id")
                    if actor_id:
                        a_row = conn.execute("SELECT disabled_at FROM actors WHERE actor_id=?", (actor_id,)).fetchone()
                        if not a_row:
                            findings.append({"rule": "APPROVAL_ACTOR_UNKNOWN", "object_id": oid, "actor_id": actor_id})
                except Exception:
                    pass

        # Check for referenced CAS artifacts in proposals table
        try:
            prop_rows = conn.execute("SELECT artifact_sha256 FROM proposals WHERE artifact_sha256 IS NOT NULL").fetchall()
            for pr in prop_rows:
                if pr[0]:
                    referenced_artifacts.add(pr[0])
        except Exception:
            pass

        # 11. Actor registry consistency against events
        act_findings = registry_findings(conn)
        findings.extend(act_findings)

    # 8. CAS artifact verification
    for digest in referenced_artifacts:
        if not store.artifacts.has(digest):
            findings.append({"rule": "REFERENCED_ARTIFACT_MISSING", "digest": digest})
        elif not store.artifacts.verify(digest):
            findings.append({"rule": "REFERENCED_ARTIFACT_CORRUPT", "digest": digest})

    # Verify all existing blobs on disk
    for digest in store.artifacts.blobs():
        if not store.artifacts.verify(digest):
            findings.append({"rule": "ARTIFACT_BLOB_CORRUPT", "digest": digest})

    # 9. Orphan CAS artifact identification (Advisory)
    existing_blobs = set(store.artifacts.blobs())
    orphaned_blobs = existing_blobs - referenced_artifacts
    for orphan in sorted(orphaned_blobs):
        findings.append({
            "rule": "ORPHAN_ARTIFACT_BLOB",
            "severity": "INFO",
            "digest": orphan,
            "message": "artifact blob exists in CAS store but is not referenced by active objects",
        })

    return findings


def audit_project_integrity(store: StateStore) -> dict[str, Any]:
    findings = verify_database_integrity(store)
    hard_findings = [f for f in findings if f.get("severity") != "INFO"]
    status = IntegrityStatus.PASS if not hard_findings else IntegrityStatus.FAIL
    return {
        "status": status.value,
        "project_id": store.project_id,
        "findings": findings,
    }


def release_check(store: StateStore, policy: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Perform multi-dimensional release validation under claim-proportional adjudication policy.

    Dimensions evaluated:
    - structural_integrity: PASS | FAIL
    - provenance_integrity: PASS | WARNING
    - workflow_completeness: COMPLETE | INCOMPLETE
    - semantic_adjudication_status: PASSED | FAILED | UNCLEAR | NOT_REQUIRED
    - scientific_review_status: AUTHOR_APPROVED | INDEPENDENTLY_REVIEWED | NEEDS_REVIEW | REJECTED | UNASSESSED
    - release_eligibility: ELIGIBLE | BLOCKED
    """
    policy = policy or {}
    require_independent_review = policy.get("require_independent_review", False)
    require_adjudication_for_all = policy.get("require_adjudication", False)

    integrity_res = audit_project_integrity(store)
    int_status = IntegrityStatus.PASS if integrity_res["status"] == "PASS" else IntegrityStatus.FAIL

    block_reasons: list[str] = []
    if int_status != IntegrityStatus.PASS:
        block_reasons.append("PROJECT_INTEGRITY_FAILED")

    structural_status = "PASS" if int_status == IntegrityStatus.PASS else "FAIL"
    provenance_status = "PASS"
    adjudication_verdict_status = "NOT_REQUIRED"
    claims_evaluation: list[dict[str, Any]] = []

    with store.read() as conn:
        heads = conn.execute(
            "SELECT object_id, object_type, head_revision, lifecycle_status FROM object_heads WHERE lifecycle_status='ACTIVE'"
        ).fetchall()

        type_counts: dict[str, int] = {}
        for h in heads:
            type_counts[h["object_type"]] = type_counts.get(h["object_type"], 0) + 1

        claims = conn.execute(
            """SELECT o.object_id, o.revision, s.scientific_review_status, o.content, o.origin
               FROM object_heads h
               JOIN objects o ON o.object_id = h.object_id AND o.revision = h.head_revision
               JOIN object_status s ON s.object_id = o.object_id AND s.revision = o.revision
               WHERE h.object_type = 'Claim' AND h.lifecycle_status = 'ACTIVE'"""
        ).fetchall()

        open_invalidations = conn.execute(
            "SELECT inval_id, object_id, revision, cause_object_id FROM invalidations WHERE status='OPEN'"
        ).fetchall()

        # All incoming and outgoing edges for active heads
        all_edges = conn.execute(
            "SELECT source_id, source_revision, relation, target_id, target_revision FROM edges"
        ).fetchall()

        # Active adjudications
        adjudication_rows = conn.execute(
            """SELECT o.object_id, o.revision, o.content, o.origin
               FROM object_heads h
               JOIN objects o ON o.object_id = h.object_id AND o.revision = h.head_revision
               WHERE h.object_type = 'Adjudication' AND h.lifecycle_status = 'ACTIVE'"""
        ).fetchall()

    total_objects = len(heads)
    has_rq = type_counts.get("ResearchQuestion", 0) > 0
    has_claims = len(claims) > 0

    head_map = {h["object_id"]: h["head_revision"] for h in heads}
    head_types = {h["object_id"]: h["object_type"] for h in heads}

    # Index edges strictly by (id, revision) for both source and target to prevent revision leakage
    edges_by_source_rev: dict[tuple[str, int], list[tuple]] = {}
    edges_by_target_rev: dict[tuple[str, int], list[tuple]] = {}
    for e in all_edges:
        edges_by_source_rev.setdefault((e[0], e[1]), []).append(e)
        edges_by_target_rev.setdefault((e[3], e[4]), []).append(e)

    # Index adjudications strictly by subject (object_id, revision)
    adjudications_by_subject_rev: dict[tuple[str, int], list[dict]] = {}
    for a_id, a_rev, raw_c, raw_o in adjudication_rows:
        try:
            a_data = json.loads(raw_c)
            a_orig = json.loads(raw_o) if raw_o else {}
            # Scenario B-1 Invariant: Proposed adjudications (PROPOSAL_IMPORTED) cannot become authoritative scientific judgments!
            if a_orig.get("kind") == OriginKind.PROPOSAL_IMPORTED.value:
                continue

            subj = a_data.get("subject", {})
            s_id = subj.get("object_id")
            s_rev = subj.get("revision")
            if s_id and s_rev is not None:
                adjudications_by_subject_rev.setdefault((s_id, int(s_rev)), []).append({
                    **a_data, "adjudication_id": a_id, "origin_kind": a_orig.get("kind")
                })
        except Exception:
            pass

    # Workflow Completeness Evaluation:
    # Does not demand an empirical Result for conceptual, theoretical, or literature-based research.
    # Requires an RQ, at least one Claim, and corresponding grounded support.
    has_any_evidence = (
        type_counts.get("Result", 0) > 0
        or type_counts.get("EvidencePassage", 0) > 0
        or type_counts.get("Source", 0) > 0
        or type_counts.get("Theory", 0) > 0
        or type_counts.get("Construct", 0) > 0
    )

    if total_objects == 0 or not has_rq or not has_claims or not has_any_evidence:
        workflow_status = WorkflowStatus.INCOMPLETE
        block_reasons.append("RESEARCH_INCOMPLETE: missing core scientific objects (RQ, Claim, Evidence)")
    else:
        workflow_status = WorkflowStatus.COMPLETE

    # Claim-Proportional Adjudication & Revision-Pinned Evidence Evaluation
    adjudication_statuses_found: set[str] = set()

    for c in claims:
        cid = c[0]
        crev = c[1]
        cstatus = c[2]
        c_content = json.loads(c[3]) if isinstance(c[3], str) else c[3]
        c_origin = json.loads(c[4]) if isinstance(c[4], str) else c[4]
        tier = c_content.get("claim_tier", "unclassified")

        # Track formation provenance
        formation = c_origin.get("formation", {})
        if formation.get("downgrade_reason"):
            provenance_status = "WARNING"

        # Gather evidence linked strictly to THIS claim revision:
        # 1. Incoming 'supports' or 'tests' edges (where target is claim at revision crev)
        incoming = edges_by_target_rev.get((cid, crev), [])
        supporting_sources = [e for e in incoming if e[2] in {"supports", "tests"}]

        # 2. Outgoing 'depends_on' edges (where source is claim at revision crev)
        outgoing = edges_by_source_rev.get((cid, crev), [])
        dependent_targets = [e for e in outgoing if e[2] in {"depends_on", "measures", "operationalizes"}]

        # Check empirical results vs descriptive passages vs theoretical constructs
        has_empirical_result = False
        has_evidence_passage = False
        has_raw_source_only = False
        has_theory_or_construct = False
        has_construct = False
        has_proxy = False
        evidence_ids: set[str] = set()
        stale_evidence_found = False

        for e in supporting_sources:
            evid, ev_rev = e[0], e[1]
            obj_type = head_types.get(evid)
            if head_map.get(evid) != ev_rev:
                stale_evidence_found = True
            if obj_type in {"Result", "Analysis"}:
                has_empirical_result = True
                evidence_ids.add(evid)
            elif obj_type == "EvidencePassage":
                has_evidence_passage = True
                evidence_ids.add(evid)
            elif obj_type == "Source":
                has_raw_source_only = True
                evidence_ids.add(evid)

        for e in dependent_targets:
            dep_id, dep_rev = e[3], e[4]
            obj_type = head_types.get(dep_id)
            if head_map.get(dep_id) != dep_rev:
                stale_evidence_found = True
            if obj_type in {"Theory", "Construct"}:
                has_theory_or_construct = True
            if obj_type == "Construct":
                has_construct = True
            elif obj_type == "Proxy":
                has_proxy = True
            elif obj_type in {"Result", "Analysis"}:
                has_empirical_result = True
                evidence_ids.add(dep_id)
            elif obj_type == "EvidencePassage":
                has_evidence_passage = True
                evidence_ids.add(dep_id)
            elif obj_type == "Source":
                has_raw_source_only = True
                evidence_ids.add(dep_id)

        # Check adjudications strictly bound to THIS claim revision or its linked evidence revisions
        claim_adjudications = list(adjudications_by_subject_rev.get((cid, crev), []))
        for e in supporting_sources:
            evid, ev_rev = e[0], e[1]
            claim_adjudications.extend(adjudications_by_subject_rev.get((evid, ev_rev), []))

        # Determine claim-level eligibility
        claim_blocked_reasons: list[str] = []

        if stale_evidence_found:
            claim_blocked_reasons.append(f"STALE_DEPENDENT_EVIDENCE: Claim '{cid}' links to superseded evidence revision")

        # Check for Mechanism objects linked to this claim
        has_mechanism = False
        for e in dependent_targets:
            dep_id = e[3]
            if head_types.get(dep_id) == "Mechanism":
                has_mechanism = True
        for e in supporting_sources:
            evid = e[0]
            if head_types.get(evid) == "Mechanism":
                has_mechanism = True

        # Tier-specific evidence rules:
        if tier == "unclassified":
            claim_blocked_reasons.append(f"UNCLASSIFIED_CLAIM_TIER: Migrated claim '{cid}' must be classified before release")
        elif tier == "conceptual":
            if not has_theory_or_construct:
                claim_blocked_reasons.append(f"CONCEPTUAL_CLAIM_LACKS_THEORY_OR_CONSTRUCT: Conceptual claim '{cid}' requires theoretical construct or theory linkage")
        elif tier == "measurement_validation":
            if not (has_construct and has_proxy):
                claim_blocked_reasons.append(f"MEASUREMENT_CLAIM_LACKS_OPERATIONALIZATION: Measurement validation claim '{cid}' requires construct and proxy operationalization")
            if not (has_empirical_result or claim_adjudications):
                claim_blocked_reasons.append(f"MEASUREMENT_CLAIM_LACKS_VALIDATION_EVIDENCE: Measurement validation claim '{cid}' requires empirical Result or authorized Adjudication demonstrating validity")
        elif tier == "causal":
            supported_adjs = [a for a in claim_adjudications if a.get("verdict") == "SUPPORTED"]
            if not has_empirical_result and not supported_adjs:
                claim_blocked_reasons.append(
                    f"CAUSAL_CLAIM_LACKS_EMPIRICAL_OR_ADJUDICATED_SUPPORT: Causal claim '{cid}' lacks empirical result or verified adjudication"
                )
            if policy.get("require_causal_adjudication") and not supported_adjs:
                claim_blocked_reasons.append(
                    f"CAUSAL_CLAIM_LACKS_ADJUDICATION: Causal claim '{cid}' requires an authorized scientific Adjudication with verdict SUPPORTED"
                )
        elif tier == "mechanism_consistent":
            if not has_mechanism:
                claim_blocked_reasons.append(f"MECHANISM_CLAIM_LACKS_MECHANISM_LINK: Mechanism-consistent claim '{cid}' requires linkage to a Mechanism object")
            if not (has_empirical_result or has_evidence_passage):
                claim_blocked_reasons.append(f"MECHANISM_CLAIM_LACKS_EMPIRICAL_SUPPORT: Mechanism-consistent claim '{cid}' requires supporting empirical Result or EvidencePassage")
        elif tier == "predictive":
            if not has_empirical_result:
                claim_blocked_reasons.append(f"PREDICTIVE_CLAIM_LACKS_VALIDATION_RESULTS: Predictive claim '{cid}' requires empirical Result evaluating predictive validation")
        elif tier == "associational":
            if not has_empirical_result and not has_evidence_passage:
                claim_blocked_reasons.append(f"ASSOCIATIONAL_CLAIM_LACKS_SUPPORT: Associational claim '{cid}' requires empirical result or evidence passage")
        elif tier == "descriptive":
            if not has_empirical_result and not has_evidence_passage:
                if has_raw_source_only:
                    claim_blocked_reasons.append(
                        f"CLAIM_UNSUPPORTED_DESCRIPTIVE_SOURCE_ONLY: Mere generic Source '{list(evidence_ids)[0]}' is insufficient evidence for Claim '{cid}'; requires EvidencePassage"
                    )
                else:
                    claim_blocked_reasons.append(f"CLAIM_UNSUPPORTED: Descriptive claim '{cid}' has no supporting evidence")
        else:
            if not evidence_ids and not has_theory_or_construct:
                claim_blocked_reasons.append(f"CLAIM_UNSUPPORTED: Claim '{cid}' has no supporting evidence")

        # Adjudication checks
        if require_adjudication_for_all and not claim_adjudications:
            claim_blocked_reasons.append(f"REQUIRED_ADJUDICATION_MISSING: Claim '{cid}' requires semantic adjudication")

        for adj in claim_adjudications:
            v = adj.get("verdict")
            if v == "NOT_SUPPORTED":
                claim_blocked_reasons.append(f"ADJUDICATION_NOT_SUPPORTED: Claim '{cid}' has non-supporting adjudication {adj.get('adjudication_id')}")
                adjudication_statuses_found.add("FAILED")
            elif v == "UNCLEAR":
                claim_blocked_reasons.append(f"ADJUDICATION_UNRESOLVED: Claim '{cid}' has unclear adjudication {adj.get('adjudication_id')}")
                adjudication_statuses_found.add("UNCLEAR")
            elif v == "SUPPORTED":
                adjudication_statuses_found.add("PASSED")

        if claim_blocked_reasons:
            block_reasons.extend(claim_blocked_reasons)

        claims_evaluation.append({
            "claim_id": cid,
            "revision": crev,
            "claim_tier": tier,
            "review_status": cstatus,
            "linked_evidence_count": len(evidence_ids),
            "adjudications_count": len(claim_adjudications),
            "eligible": len(claim_blocked_reasons) == 0,
            "reasons": claim_blocked_reasons,
        })

    if "FAILED" in adjudication_statuses_found:
        adjudication_verdict_status = "FAILED"
    elif "UNCLEAR" in adjudication_statuses_found:
        adjudication_verdict_status = "UNCLEAR"
    elif "PASSED" in adjudication_statuses_found:
        adjudication_verdict_status = "PASSED"

    # Scientific Review Status Evaluation
    if not has_claims:
        sci_review_status = ReviewStatus.UNASSESSED
        block_reasons.append("NO_CLAIMS_TO_REVIEW")
    else:
        statuses = {c[2] for c in claims}
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
            if require_independent_review:
                block_reasons.append("INDEPENDENT_REVIEW_REQUIRED_BY_POLICY")
        else:
            sci_review_status = ReviewStatus.NEEDS_REVIEW
            block_reasons.append("MIXED_REVIEW_STATUS")

    if open_invalidations:
        block_reasons.append(f"OPEN_INVALIDATIONS_EXIST: {len(open_invalidations)} unacknowledged invalidation(s)")

    # Check for unacknowledged stale pinned dependencies on head claims
    stale_deps_count = 0
    with store.read() as conn:
        for c in claims:
            cid, crev = c[0], c[1]
            pins = stale_pinned_edges(conn, cid, crev)
            if pins:
                acked_pins = set()
                approvals = conn.execute(
                    "SELECT content FROM objects WHERE object_type='Approval' "
                    "AND json_extract(content, '$.subject.object_id')=? "
                    "AND json_extract(content, '$.subject.revision')=? "
                    "AND json_extract(content, '$.decision')='approve'",
                    (cid, crev),
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

    allow_warnings = policy.get("allow_warnings", True)
    if not allow_warnings and provenance_status == "WARNING":
        block_reasons.append("PROVENANCE_WARNING_NOT_PERMITTED: provenance warnings exist and release policy specifies allow_warnings=False")

    # Release Eligibility Determination
    permitted_review = {ReviewStatus.INDEPENDENTLY_REVIEWED} if require_independent_review else {
        ReviewStatus.AUTHOR_APPROVED, ReviewStatus.INDEPENDENTLY_REVIEWED
    }

    if (
        not block_reasons
        and int_status == IntegrityStatus.PASS
        and workflow_status == WorkflowStatus.COMPLETE
        and sci_review_status in permitted_review
    ):
        release_eligibility = ReleaseEligibility.ELIGIBLE
    else:
        release_eligibility = ReleaseEligibility.BLOCKED

    return {
        "project_id": store.project_id,
        "integrity_status": int_status.value,
        "structural_integrity_status": structural_status,
        "provenance_integrity_status": provenance_status,
        "workflow_status": workflow_status.value,
        "semantic_adjudication_status": adjudication_verdict_status,
        "scientific_review_status": sci_review_status.value,
        "release_eligibility": release_eligibility.value,
        "block_reasons": sorted(set(block_reasons)),
        "active_objects_count": total_objects,
        "claims_count": len(claims),
        "open_invalidations_count": len(open_invalidations),
        "claims_evaluation": claims_evaluation,
        "policy_applied": policy.get("name", "claim_proportional_default"),
        "notes": [
            "IntegrityStatus checks cryptographic, relational, and CAS storage consistency.",
            "StructuralIntegrityStatus verifies table constraints, hash chains, and sealed revision digests.",
            "ProvenanceIntegrityStatus audits formation basis declarations against ledger results exposure.",
            "WorkflowStatus tracks empirical and analytical completeness for declared research scope.",
            "SemanticAdjudicationStatus verifies directional entailment and source validity records.",
            "ScientificReviewStatus reflects human or independent peer appraisal; it does not claim absolute scientific truth.",
            "ReleaseEligibility verifies whether current project state meets gate requirements.",
        ],
    }
