"""Phase D: Concurrency, Optimistic Locking, and Fault-Injection Tests.

Verifies:
1. Fault injection atomicity (RESEARCH_TEST_FAULT='raise:after_apply') rolls back cleanly with zero partial writes.
2. Fault injection atomicity (RESEARCH_TEST_FAULT='raise:after_blob') rolls back SQLite transaction cleanly.
3. Stale snapshot conflict rejection (optimistic concurrency via read_set).
4. Multi-threaded concurrent proposal imports serialize cleanly under SQLite WAL mode without deadlocks or corruptions.
5. Rollback on unhandled exception inside write_tx preserves database consistency and foreign key integrity.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import sqlite3
import pytest

from research_harness.core.auth import authenticate
from research_harness.core.model import Decision, IntegrityStatus
from research_harness.proposals.gateway import import_proposal
from research_harness.protocols.snapshot import export_snapshot
from research_harness.validation.integrity import audit_project_integrity
from tests.conftest import make_proposal


def test_fault_injection_atomicity_after_apply(harness_env, monkeypatch):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
        init_objs = conn.execute("SELECT count(*) FROM objects").fetchone()[0]
        init_rev = conn.execute("SELECT max(revision) FROM state_revisions").fetchone()[0] or 0

    snap = export_snapshot(store, actor, scope="full")

    ops = [
        {
            "operation": "propose_object",
            "proposed_id": "RQ_FAULT_01",
            "object_type": "ResearchQuestion",
            "content": {"label": "RQ", "text": "Fault test inquiry?"},
            "relations": [],
        }
    ]
    prop = make_proposal(store.project_id, snap, "PROP_FAULT_01", ops)

    # Inject fault at 'after_apply'
    monkeypatch.setenv("RESEARCH_TEST_FAULT", "raise:after_apply")

    with pytest.raises(RuntimeError, match="injected fault at after_apply"):
        import_proposal(store, json.dumps(prop), bob_token)

    # Clear fault and verify database state was completely rolled back
    monkeypatch.delenv("RESEARCH_TEST_FAULT", raising=False)

    with store.read() as conn:
        final_objs = conn.execute("SELECT count(*) FROM objects").fetchone()[0]
        final_rev = conn.execute("SELECT max(revision) FROM state_revisions").fetchone()[0] or 0
        prop_row = conn.execute("SELECT count(*) FROM proposals WHERE proposal_id='PROP_FAULT_01'").fetchone()[0]

    assert final_objs == init_objs
    assert final_rev == init_rev
    assert prop_row == 0

    # Project integrity must still be 100% PASS
    audit = audit_project_integrity(store)
    assert audit["status"] == IntegrityStatus.PASS.value


def test_fault_injection_atomicity_after_blob(harness_env, monkeypatch):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
        init_objs = conn.execute("SELECT count(*) FROM objects").fetchone()[0]
        init_rev = conn.execute("SELECT max(revision) FROM state_revisions").fetchone()[0] or 0

    snap = export_snapshot(store, actor, scope="full")

    ops = [
        {
            "operation": "propose_object",
            "proposed_id": "RQ_FAULT_02",
            "object_type": "ResearchQuestion",
            "content": {"label": "RQ", "text": "Blob fault test inquiry?"},
            "relations": [],
        }
    ]
    prop = make_proposal(store.project_id, snap, "PROP_FAULT_02", ops)

    # Inject fault at 'after_blob'
    monkeypatch.setenv("RESEARCH_TEST_FAULT", "raise:after_blob")

    with pytest.raises(RuntimeError, match="injected fault at after_blob"):
        import_proposal(store, json.dumps(prop), bob_token)

    monkeypatch.delenv("RESEARCH_TEST_FAULT", raising=False)

    with store.read() as conn:
        final_objs = conn.execute("SELECT count(*) FROM objects").fetchone()[0]
        final_rev = conn.execute("SELECT max(revision) FROM state_revisions").fetchone()[0] or 0

    assert final_objs == init_objs
    assert final_rev == init_rev

    audit = audit_project_integrity(store)
    assert audit["status"] == IntegrityStatus.PASS.value


def test_stale_snapshot_optimistic_concurrency_conflict(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)

    # Create an initial object
    snap0 = export_snapshot(store, actor, scope="full")
    prop0 = make_proposal(store.project_id, snap0, "PROP_BASE", [
        {
            "operation": "propose_object",
            "proposed_id": "RQ_STALE_01",
            "object_type": "ResearchQuestion",
            "content": {"label": "RQ Initial", "text": "Initial text"},
            "relations": [],
        }
    ])
    rec0 = import_proposal(store, json.dumps(prop0), bob_token)
    assert rec0["decision"] == Decision.ACCEPTED.value

    # Agent A and Agent B both take snapshot at state revision 1
    snap_a = export_snapshot(store, actor, scope="full")
    snap_b = export_snapshot(store, actor, scope="full")

    # Agent A proposes revising RQ_STALE_01
    prop_a = make_proposal(
        store.project_id,
        snap_a,
        "PROP_AGENT_A",
        [
            {
                "operation": "revise_object",
                "object_id": "RQ_STALE_01",
                "base_revision": 1,
                "content": {"label": "RQ Revised by A", "text": "A revised this"},
            }
        ],
        read_set={"RQ_STALE_01": 1},
    )
    rec_a = import_proposal(store, json.dumps(prop_a), bob_token)
    assert rec_a["decision"] == Decision.ACCEPTED.value

    # Agent B now attempts to import a proposal that reads RQ_STALE_01 at revision 1
    prop_b = make_proposal(
        store.project_id,
        snap_b,
        "PROP_AGENT_B",
        [
            {
                "operation": "revise_object",
                "object_id": "RQ_STALE_01",
                "base_revision": 1,
                "content": {"label": "RQ Revised by B", "text": "B tried to revise this"},
            }
        ],
        read_set={"RQ_STALE_01": 1},
    )
    rec_b = import_proposal(store, json.dumps(prop_b), bob_token)

    # Agent B's proposal must be rejected due to optimistic concurrency conflict!
    assert rec_b["decision"] == Decision.CONFLICT.value
    assert rec_b["rule"] == "READ_SET_CONFLICT"
    assert any(c["object_id"] == "RQ_STALE_01" for c in rec_b["conflicts"])


def test_concurrent_threads_proposal_import(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)

    # Take snapshot
    snap = export_snapshot(store, actor, scope="full")

    num_concurrent = 5
    proposals = []
    for i in range(num_concurrent):
        ops = [
            {
                "operation": "propose_object",
                "proposed_id": f"RQ_CONCUR_{i:02d}",
                "object_type": "ResearchQuestion",
                "content": {"label": f"RQ Concur {i}", "text": f"Inquiry concurrency test {i}"},
                "relations": [],
            }
        ]
        p = make_proposal(store.project_id, snap, f"PROP_CONCUR_{i:02d}", ops)
        proposals.append(json.dumps(p))

    # Submit concurrently using ThreadPoolExecutor
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_concurrent) as executor:
        futures = [executor.submit(import_proposal, store, p_json, bob_token) for p_json in proposals]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    # All proposals must succeed and be accepted (rebased across revisions under WAL mode)
    assert len(results) == num_concurrent
    for r in results:
        assert r["decision"] == Decision.ACCEPTED.value

    # Verify that all 5 objects exist and database integrity is intact
    with store.read() as conn:
        cnt = conn.execute("SELECT count(*) FROM object_heads WHERE object_id LIKE 'RQ_CONCUR_%'").fetchone()[0]
        assert cnt == num_concurrent

    audit = audit_project_integrity(store)
    assert audit["status"] == IntegrityStatus.PASS.value


def test_crash_injection_before_commit_subprocess(harness_env, tmp_path):
    """Exercise RESEARCH_TEST_FAULT='exit:before_commit' through an isolated subprocess."""
    import subprocess
    import sys

    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
        init_objs = conn.execute("SELECT count(*) FROM objects").fetchone()[0]
        init_rev = conn.execute("SELECT max(revision) FROM state_revisions").fetchone()[0] or 0

    snap = export_snapshot(store, actor, scope="full")
    prop = make_proposal(store.project_id, snap, "PROP_CRASH_BEFORE", [
        {
            "operation": "propose_object",
            "proposed_id": "RQ_CRASH_01",
            "object_type": "ResearchQuestion",
            "content": {"label": "Crash test", "text": "Will before_commit crash safely?"},
            "relations": [],
        }
    ])
    prop_path = tmp_path / "prop_crash_before.json"
    prop_path.write_text(json.dumps(prop), encoding="utf-8")

    sub_script = f"""
import os, sys
from research_harness.state.store import StateStore
from research_harness.proposals.gateway import import_proposal

home = r'{store.paths.home}'
project_id = '{store.project_id}'
store = StateStore.open(home, project_id)
with open(r'{prop_path}', 'r', encoding='utf-8') as f:
    raw = f.read()

import_proposal(store, raw, '{bob_token}')
"""
    env = os.environ.copy()
    env["RESEARCH_TEST_FAULT"] = "exit:before_commit"

    proc = subprocess.run([sys.executable, "-c", sub_script], env=env, capture_output=True)
    # The process must exit abruptly due to os._exit(137)
    assert proc.returncode != 0

    # Verify that SQLite rolled back completely and nothing was written
    with store.read() as conn:
        final_objs = conn.execute("SELECT count(*) FROM objects").fetchone()[0]
        final_rev = conn.execute("SELECT max(revision) FROM state_revisions").fetchone()[0] or 0
        prop_cnt = conn.execute("SELECT count(*) FROM proposals WHERE proposal_id='PROP_CRASH_BEFORE'").fetchone()[0]

    assert final_objs == init_objs
    assert final_rev == init_rev
    assert prop_cnt == 0

    audit = audit_project_integrity(store)
    assert audit["status"] == IntegrityStatus.PASS.value


def test_crash_injection_after_blob_subprocess(harness_env, tmp_path):
    """Exercise RESEARCH_TEST_FAULT='exit:after_blob' through an isolated subprocess."""
    import subprocess
    import sys

    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
        init_objs = conn.execute("SELECT count(*) FROM objects").fetchone()[0]
        init_rev = conn.execute("SELECT max(revision) FROM state_revisions").fetchone()[0] or 0

    snap = export_snapshot(store, actor, scope="full")
    prop = make_proposal(store.project_id, snap, "PROP_CRASH_AFTER_BLOB", [
        {
            "operation": "propose_object",
            "proposed_id": "RQ_CRASH_02",
            "object_type": "ResearchQuestion",
            "content": {"label": "Blob crash", "text": "Will after_blob leave recoverable orphan?"},
            "relations": [],
        }
    ])
    prop_path = tmp_path / "prop_crash_blob.json"
    prop_raw = json.dumps(prop)
    prop_path.write_text(prop_raw, encoding="utf-8")

    sub_script = f"""
import os, sys
from research_harness.state.store import StateStore
from research_harness.proposals.gateway import import_proposal

home = r'{store.paths.home}'
project_id = '{store.project_id}'
store = StateStore.open(home, project_id)
with open(r'{prop_path}', 'r', encoding='utf-8') as f:
    raw = f.read()

import_proposal(store, raw, '{bob_token}')
"""
    env = os.environ.copy()
    env["RESEARCH_TEST_FAULT"] = "exit:after_blob"

    proc = subprocess.run([sys.executable, "-c", sub_script], env=env, capture_output=True)
    assert proc.returncode != 0

    # The SQLite transaction did not commit
    with store.read() as conn:
        final_objs = conn.execute("SELECT count(*) FROM objects").fetchone()[0]
        final_rev = conn.execute("SELECT max(revision) FROM state_revisions").fetchone()[0] or 0
        prop_cnt = conn.execute("SELECT count(*) FROM proposals WHERE proposal_id='PROP_CRASH_AFTER_BLOB'").fetchone()[0]

    assert final_objs == init_objs
    assert final_rev == init_rev
    assert prop_cnt == 0

    # Run store recovery and verify it identifies the orphan blob cleanly
    rec_res = store.recover()
    assert rec_res["status"] == "RECOVERED"

    # Integrity audit remains PASS (orphan blobs produce advisory INFO)
    audit = audit_project_integrity(store)
    assert audit["status"] == IntegrityStatus.PASS.value


def test_repeated_proposal_import_idempotency(harness_env):
    """Test importing the exact same proposal twice and importing different content with same ID."""
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)

    snap = export_snapshot(store, actor, scope="full")
    prop = make_proposal(store.project_id, snap, "PROP_IDEMP_01", [
        {
            "operation": "propose_object",
            "proposed_id": "RQ_IDEMP_01",
            "object_type": "ResearchQuestion",
            "content": {"label": "Idempotency inquiry", "text": "Is proposal ingestion idempotent?"},
            "relations": [],
        }
    ])
    raw = json.dumps(prop)

    # First import: ACCEPTED
    rec1 = import_proposal(store, raw, bob_token)
    assert rec1["decision"] == Decision.ACCEPTED.value
    rev1 = rec1["new_state_revision"]

    # Second import with identical payload: DUPLICATE (idempotent, does not advance revision)
    rec2 = import_proposal(store, raw, bob_token)
    assert rec2["decision"] == str(Decision.DUPLICATE)
    assert rec2["original_decision"] == Decision.ACCEPTED.value

    with store.read() as conn:
        cur_rev = conn.execute("SELECT max(revision) FROM state_revisions").fetchone()[0]
        assert cur_rev == rev1

    # Third import with SAME proposal_id but different content: CONFLICT
    prop_diff = make_proposal(store.project_id, snap, "PROP_IDEMP_01", [
        {
            "operation": "propose_object",
            "proposed_id": "RQ_IDEMP_DIFF",
            "object_type": "ResearchQuestion",
            "content": {"label": "Different content", "text": "This content is different."},
            "relations": [],
        }
    ])
    rec3 = import_proposal(store, json.dumps(prop_diff), bob_token)
    assert rec3["decision"] == Decision.CONFLICT.value
    assert rec3["rule"] == "PROPOSAL_ID_REUSED_WITH_DIFFERENT_CONTENT"


def test_recovery_cleans_stale_staging_files(harness_env):
    """Verify StateStore.recover cleans up stale .tmp staging files."""
    store = harness_env["store"]

    # Create dummy staging files
    tmp1 = store.paths.staging / "test_stale_1.tmp"
    tmp2 = store.paths.staging / "test_stale_2.tmp"
    tmp1.write_bytes(b"partial byte stream 1")
    tmp2.write_bytes(b"partial byte stream 2")
    assert tmp1.is_file()
    assert tmp2.is_file()

    res = store.recover(max_staging_age_seconds=0.0)
    assert res["status"] == "RECOVERED"
    assert res["stale_staging_removed"] >= 2
    assert not tmp1.exists()
    assert not tmp2.exists()


def test_multiprocess_concurrent_proposal_import(harness_env, tmp_path):
    """Exercise real multi-process concurrent writes to the SQLite database."""
    import subprocess
    import sys

    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)

    snap = export_snapshot(store, actor, scope="full")

    num_procs = 4
    prop_files = []
    for i in range(num_procs):
        p = make_proposal(store.project_id, snap, f"PROP_MP_{i:02d}", [
            {
                "operation": "propose_object",
                "proposed_id": f"RQ_MP_{i:02d}",
                "object_type": "ResearchQuestion",
                "content": {"label": f"RQ MP {i}", "text": f"Multi-process inquiry {i}"},
                "relations": [],
            }
        ])
        p_path = tmp_path / f"prop_mp_{i}.json"
        p_path.write_text(json.dumps(p), encoding="utf-8")
        prop_files.append(str(p_path))

    # Launch separate Python subprocesses concurrently
    sub_cmd = f"""
import sys
from research_harness.state.store import StateStore
from research_harness.proposals.gateway import import_proposal

prop_file = sys.argv[1]
home = r'{store.paths.home}'
project_id = '{store.project_id}'
store = StateStore.open(home, project_id)
with open(prop_file, 'r', encoding='utf-8') as f:
    raw = f.read()

rec = import_proposal(store, raw, '{bob_token}')
assert rec['decision'] == 'ACCEPTED'
"""
    processes = []
    for pf in prop_files:
        proc = subprocess.Popen([sys.executable, "-c", sub_cmd, pf])
        processes.append(proc)

    # Wait for all processes to complete
    exit_codes = [p.wait(timeout=30) for p in processes]
    assert all(code == 0 for code in exit_codes)

    # Verify all objects exist and integrity is intact
    with store.read() as conn:
        cnt = conn.execute("SELECT count(*) FROM object_heads WHERE object_id LIKE 'RQ_MP_%'").fetchone()[0]
        assert cnt == num_procs

    audit = audit_project_integrity(store)
    assert audit["status"] == IntegrityStatus.PASS.value
