"""Comprehensive regression tests for P0/P1 remediation and acceptance audit.

Covers:
- Gate G1 (P0-AUTH): Elimination of identity-based authorization bypass
- Gate G2 (P0-REVISION): Strictly revision-aware evidence & adjudication release evaluation
- Gate G3 (P0-SCHEMA): Atomic, idempotent, recoverable schema upgrades
- Gate G4 (P0-MIGRATION): Safe append-only repair of legacy imports, idempotency & reconciliation
- Gate G5 (P1-RELEASE): Scientific release policy allow_warnings enforcement & claim tiers
- CAS vs Text Fingerprint distinction in integrity verification
"""
from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from research_harness.core.auth import authenticate, issue_internal_capability, token_hash
from research_harness.core.errors import AuthorizationError, HarnessError
from research_harness.core.model import ReleaseEligibility, ReviewStatus, WorkflowStatus
from research_harness.legacy.adapter import migrate_legacy_project
from research_harness.protocols.snapshot import export_snapshot
from research_harness.state.actors import register_actor
from research_harness.state.schema import DB_SCHEMA_VERSION, EDGE_SEAL_TRIGGER, upgrade_schema
from research_harness.state.store import StateStore
from research_harness.validation.integrity import release_check, verify_database_integrity
from research_harness.validation.review import record_review


# ======================================================================================
# GATE G1 (P0-AUTH): Identity-Based Authorization Bypass Elimination
# ======================================================================================

def test_p0_auth_actor_id_without_token_rejected(harness_env):
    """P0-AUTH: Passing caller-provided actor_id without token MUST be rejected."""
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]

    with store.read() as conn:
        owner_actor = authenticate(conn, owner_token)
    assert owner_actor is not None

    # 1. Attempt to write with owner's actor_id but no token or capability
    with pytest.raises(AuthorizationError) as exc_info:
        with store.write_tx(actor_id=owner_actor.actor_id, require_roles=("owner",)) as tx:
            tx.conn.execute("SELECT 1")
    assert exc_info.value.code == "AUTHENTICATION_FAILED"
    assert "Authentication token required" in str(exc_info.value)

    # 2. Attempt to write with importer's actor_id but no token
    with pytest.raises(AuthorizationError) as exc_info:
        with store.write_tx(actor_id="author_bob", require_roles=("importer",)) as tx:
            tx.conn.execute("SELECT 1")
    assert exc_info.value.code == "AUTHENTICATION_FAILED"

    # 3. Attempt to write with independent reviewer's actor_id but no token
    with pytest.raises(AuthorizationError) as exc_info:
        with store.write_tx(actor_id="reviewer_carol", require_roles=("independent_reviewer",)) as tx:
            tx.conn.execute("SELECT 1")
    assert exc_info.value.code == "AUTHENTICATION_FAILED"


def test_p0_auth_internal_capability_cannot_confer_runtime_privilege(harness_env):
    """P0-AUTH: General-purpose internal capabilities cannot confer runtime write privilege."""
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]

    with store.read() as conn:
        owner_actor = authenticate(conn, owner_token)

    # 1. Capability created by issue_internal_capability cannot authorize write_tx
    cap = issue_internal_capability(owner_actor.actor_id, "system_maintenance")
    with pytest.raises(AuthorizationError) as exc_info:
        with store.write_tx(internal_capability=cap, require_roles=("owner",)) as tx:
            tx.conn.execute("SELECT 1")
    assert exc_info.value.code == "AUTHENTICATION_FAILED"

    # 2. Forged capability also fails
    class ForgedCapability:
        def __init__(self):
            self.actor_id = owner_actor.actor_id
            self.operation = "malicious_bypass"
            self._sentinel = object()

    with pytest.raises(AuthorizationError) as exc_info:
        with store.write_tx(internal_capability=ForgedCapability(), require_roles=("owner",)) as tx:
            tx.conn.execute("SELECT 1")
    assert exc_info.value.code == "AUTHENTICATION_FAILED"


def test_p0_auth_failed_authorization_leaves_no_partial_mutation(harness_env):
    """P0-AUTH: Authorization failures must abort before any mutation occurs."""
    store = harness_env["store"]
    with store.read() as conn:
        init_rev = conn.execute("SELECT MAX(revision) FROM state_revisions").fetchone()[0]
        init_objs = conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]

    with pytest.raises(AuthorizationError):
        with store.write_tx(actor_id="owner", require_roles=("owner",)) as tx:
            tx.conn.execute("INSERT INTO objects(object_id,revision,object_type) VALUES ('FAIL',1,'Claim')")

    with store.read() as conn:
        post_rev = conn.execute("SELECT MAX(revision) FROM state_revisions").fetchone()[0]
        post_objs = conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
        assert post_rev == init_rev
        assert post_objs == init_objs


# ======================================================================================
# GATE G2 (P0-REVISION): Strictly Revision-Aware Release Validation
# ======================================================================================

def test_p0_revision_scenario_a_evidence_not_inherited(harness_env):
    """Reproduction Scenario A:
    Claim rev 1 is supported by EvidencePassage rev 1.
    Claim rev 2 is created WITHOUT evidence relationship.
    Claim rev 2 approved.
    Release check MUST NOT reuse rev 1 evidence as support for rev 2.
    """
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    carol_token = harness_env["actors"]["carol"]["token"]

    # 1. Create RQ, Source, Claim rev 1, and EvidencePassage rev 1 (which supports Claim rev 1)
    with store.write_tx(token=owner_token) as tx:
        tx.add_revision(object_id="RQ_01", object_type="ResearchQuestion", content={"text": "Q1"}, origin={}, edges=[])
        tx.add_revision(object_id="SRC_01", object_type="Source", content={"citation_key": "Src1"}, origin={}, edges=[])
        tx.add_revision(
            object_id="CLAIM_01",
            object_type="Claim",
            content={"text": "Claim v1 with evidence", "claim_tier": "descriptive"},
            origin={},
            edges=[("depends_on", "RQ_01", 1, {})],
        )
        tx.add_revision(
            object_id="EV_01",
            object_type="EvidencePassage",
            content={"locator": "p. 1", "normalized_claim": "Empirical passage"},
            origin={},
            edges=[("derived_from", "SRC_01", 1, {}), ("supports", "CLAIM_01", 1, {})],
        )

    # 2. Revise Claim -> rev 2 WITHOUT evidence relation (only depends on RQ_01)
    with store.write_tx(token=owner_token) as tx:
        tx.add_revision(
            object_id="CLAIM_01",
            object_type="Claim",
            content={"text": "Claim v2 isolated content", "claim_tier": "descriptive"},
            origin={},
            edges=[("depends_on", "RQ_01", 1, {})],
        )

    # 3. Approve Claim rev 2
    record_review(
        store,
        token=carol_token,
        object_id="CLAIM_01",
        revision=2,
        kind="independent_review",
        decision="approve",
        rationale="Approved Claim rev 2",
    )

    # 4. Run release gate
    gate = release_check(store)
    # The release gate must not report CLAIM_01 as eligible because rev 2 has NO supporting evidence!
    assert gate["release_eligibility"] == ReleaseEligibility.BLOCKED.value
    assert any("has no supporting evidence" in r or "CLAIM_01" in r for r in gate["block_reasons"])


def test_p0_revision_scenario_b_adjudication_not_inherited(harness_env):
    """Reproduction Scenario B:
    Claim rev 1 has an adjudication with verdict SUPPORTED.
    Claim rev 2 has revised content.
    Approve rev 2 without a new adjudication.
    Run release check with require_adjudication=True.
    Previous adjudication MUST NOT justify rev 2.
    """
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    carol_token = harness_env["actors"]["carol"]["token"]

    # 1. Create RQ, Source, Claim rev 1, EV_02, and Adjudication assessing Claim rev 1
    with store.write_tx(token=owner_token) as tx:
        tx.add_revision(object_id="RQ_02", object_type="ResearchQuestion", content={"text": "Q2"}, origin={}, edges=[])
        tx.add_revision(object_id="SRC_02", object_type="Source", content={"citation_key": "Src2"}, origin={}, edges=[])
        tx.add_revision(
            object_id="CLAIM_02",
            object_type="Claim",
            content={"text": "Claim 2 initial content", "claim_tier": "descriptive"},
            origin={},
            edges=[("depends_on", "RQ_02", 1, {})],
        )
        tx.add_revision(
            object_id="EV_02",
            object_type="EvidencePassage",
            content={"locator": "p. 2", "normalized_claim": "Passage 2"},
            origin={},
            edges=[("derived_from", "SRC_02", 1, {}), ("supports", "CLAIM_02", 1, {})],
        )
        # Adjudication strictly targets subject (CLAIM_02, revision 1)
        tx.add_revision(
            object_id="ADJ_02",
            object_type="Adjudication",
            content={
                "subject": {"object_id": "CLAIM_02", "revision": 1},
                "verdict": "SUPPORTED",
                "assessment_kind": "semantic_entailment",
                "rationale": "Direct entailment for rev 1",
            },
            origin={},
            edges=[("supports", "CLAIM_02", 1, {}), ("depends_on", "EV_02", 1, {})],
        )

    # 2. Revise Claim -> rev 2 with new content
    with store.write_tx(token=owner_token) as tx:
        tx.add_revision(
            object_id="CLAIM_02",
            object_type="Claim",
            content={"text": "Claim 2 materially revised content", "claim_tier": "descriptive"},
            origin={},
            edges=[("depends_on", "RQ_02", 1, {})],
        )
        # Attach evidence to rev 2 as well
        tx.add_revision(
            object_id="EV_02",
            object_type="EvidencePassage",
            content={"locator": "p. 2", "normalized_claim": "Passage 2"},
            origin={},
            edges=[("derived_from", "SRC_02", 1, {}), ("supports", "CLAIM_02", 2, {})],
        )

    # 3. Approve Claim rev 2
    record_review(
        store,
        token=carol_token,
        object_id="CLAIM_02",
        revision=2,
        kind="independent_review",
        decision="approve",
        rationale="Approved Claim 2 rev 2",
    )

    # 4. Check release with require_adjudication=True
    policy = {"require_adjudication": True, "require_peer_review": True}
    gate = release_check(store, policy=policy)

    # Gate must be BLOCKED because Adjudication was for rev 1, NOT rev 2!
    assert gate["release_eligibility"] == ReleaseEligibility.BLOCKED.value
    assert any("adjudication" in r.lower() and "CLAIM_02" in r for r in gate["block_reasons"])


def test_p0_revision_stale_evidence_revision_detected(harness_env):
    """P0-REVISION: When evidence linked by a claim has been superseded, flag STALE_DEPENDENT_EVIDENCE."""
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    carol_token = harness_env["actors"]["carol"]["token"]

    # 1. Setup claim linked to EV_03 rev 1
    with store.write_tx(token=owner_token) as tx:
        tx.add_revision(object_id="RQ_03", object_type="ResearchQuestion", content={"text": "Q3"}, origin={}, edges=[])
        tx.add_revision(object_id="SRC_03", object_type="Source", content={"citation_key": "Src3"}, origin={}, edges=[])
        tx.add_revision(
            object_id="CLAIM_03",
            object_type="Claim",
            content={"text": "Claim 3", "claim_tier": "descriptive"},
            origin={},
            edges=[("depends_on", "RQ_03", 1, {})],
        )
        tx.add_revision(
            object_id="EV_03",
            object_type="EvidencePassage",
            content={"locator": "p. 3", "normalized_claim": "Passage 3 rev 1"},
            origin={},
            edges=[("derived_from", "SRC_03", 1, {}), ("supports", "CLAIM_03", 1, {})],
        )

    # 2. Supersede EV_03 -> rev 2 (unlinked to claim 3)
    with store.write_tx(token=owner_token) as tx:
        tx.add_revision(
            object_id="EV_03",
            object_type="EvidencePassage",
            content={"locator": "p. 3 revised", "normalized_claim": "Passage 3 rev 2 corrected"},
            origin={},
            edges=[("derived_from", "SRC_03", 1, {})],
        )

    # 3. Approve Claim_03 rev 1
    record_review(store, token=carol_token, object_id="CLAIM_03", revision=1, kind="independent_review", decision="approve", rationale="ok")

    gate = release_check(store)
    # Evidence is stale (CLAIM_03 links to EV_03 rev 1, but EV_03 is at rev 2)
    assert any("STALE_DEPENDENT_EVIDENCE" in r for r in gate["block_reasons"])


# ======================================================================================
# GATE G3 (P0-SCHEMA): Transactional, Idempotent, Recoverable Upgrades
# ======================================================================================

def test_p0_schema_upgrade_atomic_and_idempotent(tmp_path):
    """P0-SCHEMA: Upgrade from v9.0.1 to v9.0.2 is atomic and repeatable."""
    project_dir = tmp_path / "schema_proj"
    store, owner_token = StateStore.create(project_dir, "PRJ_UPGRADE_01")

    # Manually revert meta.db_schema_version to v9.0.1 and drop trigger to simulate genuine v9.0.1 DB
    conn = store.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DROP TRIGGER IF EXISTS edges_sealed_at_revision_creation")
        conn.execute("UPDATE meta SET value='9.0.1' WHERE key='db_schema_version'")
        conn.execute("COMMIT")
    finally:
        conn.close()

    # 1. Perform upgrade
    res1 = upgrade_schema(store, owner_token)
    assert res1["status"] == "UPGRADED"
    assert res1["to_version"] == DB_SCHEMA_VERSION

    # Verify trigger exists in DB and meta is updated
    conn = store.connect()
    try:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name='edges_sealed_at_revision_creation'").fetchone()
        assert row is not None
        vrow = conn.execute("SELECT value FROM meta WHERE key='db_schema_version'").fetchone()
        assert vrow[0] == DB_SCHEMA_VERSION
    finally:
        conn.close()

    # 2. Repeated upgrade must be idempotent
    res2 = upgrade_schema(store, owner_token)
    assert res2["status"] == "NOOP"
    assert "already" in res2["message"]


def test_p0_schema_upgrade_rollback_on_failure(tmp_path):
    """P0-SCHEMA: Injected failure inside upgrade rolls back cleanly without leaving partial changes."""
    project_dir = tmp_path / "schema_proj_fail"
    store, owner_token = StateStore.create(project_dir, "PRJ_UPGRADE_FAIL")

    conn = store.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DROP TRIGGER IF EXISTS edges_sealed_at_revision_creation")
        conn.execute("UPDATE meta SET value='9.0.1' WHERE key='db_schema_version'")
        conn.execute("COMMIT")
    finally:
        conn.close()

    # Inject failure by executing a transaction that raises an unhandled error midway
    with pytest.raises(RuntimeError):
        with store.write_tx(token=owner_token, bump=False) as tx:
            # Install trigger
            tx.conn.execute(EDGE_SEAL_TRIGGER.strip())
            # Injected failure
            raise RuntimeError("simulated upgrade failure midway")

    # Verify trigger was rolled back and does NOT exist
    conn = store.connect()
    try:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name='edges_sealed_at_revision_creation'").fetchone()
        assert row is None
        vrow = conn.execute("SELECT value FROM meta WHERE key='db_schema_version'").fetchone()
        assert vrow[0] == "9.0.1"
    finally:
        conn.close()


# ======================================================================================
# GATE G4 (P0-MIGRATION): Legacy Import Repair, Idempotency & Reconciliation
# ======================================================================================

def test_p0_migration_repairs_corrupted_claim_text_append_only(tmp_path):
    """P0-MIGRATION: Detects corrupted claim text from defective previous import
    and repairs it through append-only revision N+1 without overwriting rev 1.
    """
    dest = tmp_path / "legacy_corrupted"
    master = dest / "research_harness" / "00_master"
    master.mkdir(parents=True, exist_ok=True)
    (dest / "main.tex").write_text("% tex\n", encoding="utf-8")

    authoritative_text = "Correct authoritative proposition text from legacy master"
    with (master / "proposition_registry.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["proposition_id", "proposition_text", "claim_tier"])
        w.writerow(["PROP_REPAIR_01", authoritative_text, "causal"])

    store_home = tmp_path / "store_home"
    store, owner_token = StateStore.create(store_home, "PRJ_REPAIR_01")
    with store.read() as conn:
        actor = authenticate(conn, owner_token)

    # 1. Simulate defective v9.0.0.dev0 import: imported with defective text (e.g. truncated or label only)
    with store.write_tx(token=owner_token) as tx:
        tx.add_revision(
            object_id="PROP_REPAIR_01",
            object_type="Claim",
            content={"text": "DEFECTIVE_TRUNCATED_TEXT", "claim_tier": "causal", "label": "PROP_REPAIR_01"},
            origin={"kind": "LEGACY_MIGRATED", "legacy_source_file": "00_master/proposition_registry.csv"},
            edges=[],
        )

    # Verify revision 1 exists with defective text
    claim_v1 = store.get_object("PROP_REPAIR_01", revision=1)
    assert claim_v1["content"]["text"] == "DEFECTIVE_TRUNCATED_TEXT"

    # 2. Run migration adapter repair
    receipt = migrate_legacy_project(dest, store, actor=actor, token=owner_token, dry_run=False)

    assert receipt["decision"] == "REPAIRED"
    assert "PROP_REPAIR_01" in receipt["repaired_objects"]
    assert receipt["repaired_object_count"] == 1

    # 3. Verify rev 1 was NOT overwritten, and rev 2 contains authoritative text
    claim_v1_post = store.get_object("PROP_REPAIR_01", revision=1)
    assert claim_v1_post["content"]["text"] == "DEFECTIVE_TRUNCATED_TEXT"

    claim_head = store.get_object("PROP_REPAIR_01")
    assert claim_head["head_revision"] == 2
    assert claim_head["content"]["text"] == authoritative_text
    assert claim_head["origin"]["kind"] == "MIGRATION_REPAIRED"
    assert claim_head["scientific_review_status"] == ReviewStatus.UNASSESSED.value
    assert claim_head["origin"]["formation"]["inference_status"] == "UNKNOWN"


def test_p0_migration_idempotent_no_duplicate_backups_or_events(tmp_path):
    """P0-MIGRATION: Repeated migration on clean project creates no extra revisions, events, or backups."""
    dest = tmp_path / "legacy_clean"
    master = dest / "research_harness" / "00_master"
    master.mkdir(parents=True, exist_ok=True)
    (dest / "main.tex").write_text("% tex\n", encoding="utf-8")

    with (master / "proposition_registry.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["proposition_id", "proposition_text", "claim_tier"])
        w.writerow(["PROP_IDEM_01", "Clean proposition text", "descriptive"])

    store_home = tmp_path / "store_home_idem"
    store, owner_token = StateStore.create(store_home, "PRJ_IDEM_01")
    with store.read() as conn:
        actor = authenticate(conn, owner_token)

    # Initial migration
    res1 = migrate_legacy_project(dest, store, actor=actor, token=owner_token, dry_run=False)
    assert res1["decision"] == "ACCEPTED"
    init_backups = list(store.paths.backups.glob("*.zip"))
    assert len(init_backups) == 1

    with store.read() as conn:
        rev1 = conn.execute("SELECT MAX(revision) FROM state_revisions").fetchone()[0]
        events1 = conn.execute("SELECT COUNT(*) FROM events WHERE event_type='LEGACY_PROJECT_MIGRATED'").fetchone()[0]
        assert events1 == 1

    # Repeated migration
    res2 = migrate_legacy_project(dest, store, actor=actor, token=owner_token, dry_run=False)
    assert res2["decision"] == "ALREADY_MIGRATED"
    assert res2["repaired_object_count"] == 0
    assert len(res2["created_objects"]) == 0

    with store.read() as conn:
        rev2 = conn.execute("SELECT MAX(revision) FROM state_revisions").fetchone()[0]
        events2 = conn.execute("SELECT COUNT(*) FROM events WHERE event_type='LEGACY_PROJECT_MIGRATED'").fetchone()[0]
        # State revision not bumped
        assert rev2 == rev1
        # No duplicate migration event
        assert events2 == events1

    post_backups = list(store.paths.backups.glob("*.zip"))
    # No unnecessary duplicate zip created
    assert len(post_backups) == 1


# ======================================================================================
# GATE G5 (P1-RELEASE): Release Policy Enforcement & Claim Tiers
# ======================================================================================

def test_p1_release_allow_warnings_policy_enforced(harness_env):
    """P1-RELEASE: allow_warnings=False blocks release on provenance warnings."""
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    carol_token = harness_env["actors"]["carol"]["token"]

    # Create fully supported project but with an UNKNOWN formation provenance warning
    with store.write_tx(token=owner_token) as tx:
        tx.add_revision(object_id="RQ_WARN", object_type="ResearchQuestion", content={"text": "Q"}, origin={}, edges=[])
        tx.add_revision(object_id="SRC_WARN", object_type="Source", content={"citation_key": "S"}, origin={}, edges=[])
        tx.add_revision(
            object_id="CLAIM_WARN",
            object_type="Claim",
            content={"text": "Claim text", "claim_tier": "descriptive"},
            origin={
                "kind": "PROPOSED",
                "formation": {
                    "inference_status": "EXPLORATORY",
                    "downgrade_reason": "DECLARED_CONFIRMATORY_BUT_RESULTS_EXPOSED",
                },
            },
            edges=[("depends_on", "RQ_WARN", 1, {})],
        )
        tx.add_revision(
            object_id="EV_WARN",
            object_type="EvidencePassage",
            content={"locator": "p. 1", "normalized_claim": "Text"},
            origin={},
            edges=[("derived_from", "SRC_WARN", 1, {}), ("supports", "CLAIM_WARN", 1, {})],
        )

    record_review(store, token=carol_token, object_id="CLAIM_WARN", revision=1, kind="independent_review", decision="approve", rationale="ok")

    # 1. Strict mode: allow_warnings=False -> BLOCKED
    gate_strict = release_check(store, policy={"allow_warnings": False, "require_peer_review": True})
    assert gate_strict["release_eligibility"] == ReleaseEligibility.BLOCKED.value
    assert gate_strict["provenance_integrity_status"] == "WARNING"
    assert any("warning" in r.lower() for r in gate_strict["block_reasons"])

    # 2. Permissive mode: allow_warnings=True -> ELIGIBLE (with provenance warning recorded)
    gate_permissive = release_check(store, policy={"allow_warnings": True, "require_peer_review": True})
    assert gate_permissive["release_eligibility"] == ReleaseEligibility.ELIGIBLE.value
    assert gate_permissive["provenance_integrity_status"] == "WARNING"


def test_p1_release_cas_blob_distinguished_from_content_fingerprints(harness_env):
    """P1-RELEASE: Content hashes (passage_norm_sha256, first_recorded_text_sha256)
    must NOT be misinterpreted as physical CAS blob references.
    """
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]

    with store.write_tx(token=owner_token) as tx:
        tx.add_revision(
            object_id="CLAIM_FP",
            object_type="Claim",
            content={
                "text": "Some text",
                "claim_tier": "conceptual",
                "text_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            },
            origin={
                "kind": "PROPOSED",
                "formation": {
                    "first_recorded_text_sha256": "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
                },
            },
            edges=[],
        )

    # Database integrity check must PASS and not complain about missing CAS blobs for fingerprints
    findings = verify_database_integrity(store)
    assert len(findings) == 0
