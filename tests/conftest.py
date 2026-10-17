"""Shared pytest fixtures for Research Harness 9.0-dev."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pytest

from research_harness import PROTOCOL_VERSION
from research_harness.core.auth import new_token, token_hash, validate_roles
from research_harness.state.store import StateStore


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    d = tmp_path / "research_home"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def harness_env(project_dir: Path):
    project_id = "PRJ_TEST_001"
    store, owner_token = StateStore.create(project_dir, project_id, owner_id="owner_alice")

    # Register additional actors
    actors = {}
    with store.write_tx("owner_alice", bump=False) as tx:
        tx.conn.execute("UPDATE actors SET independence_group='group_owner' WHERE actor_id='owner_alice'")

        # 1. Author Bob
        token_bob = new_token()
        tx.conn.execute(
            "INSERT INTO actors(actor_id, display_name, roles, independence_group, token_sha256, created_at, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("author_bob", "Author Bob", json.dumps(["author", "importer"]), "group_alpha", token_hash(token_bob), tx.at, "owner_alice"),
        )
        actors["bob"] = {"actor_id": "author_bob", "token": token_bob, "group": "group_alpha"}

        # 2. Reviewer Carol (independent, group_beta)
        token_carol = new_token()
        tx.conn.execute(
            "INSERT INTO actors(actor_id, display_name, roles, independence_group, token_sha256, created_at, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("rev_carol", "Reviewer Carol", json.dumps(["independent_reviewer"]), "group_beta", token_hash(token_carol), tx.at, "owner_alice"),
        )
        actors["carol"] = {"actor_id": "rev_carol", "token": token_carol, "group": "group_beta"}

        # 3. Reviewer Dave (in same group_alpha as Bob - non-independent of Bob)
        token_dave = new_token()
        tx.conn.execute(
            "INSERT INTO actors(actor_id, display_name, roles, independence_group, token_sha256, created_at, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("rev_dave", "Reviewer Dave", json.dumps(["independent_reviewer"]), "group_alpha", token_hash(token_dave), tx.at, "owner_alice"),
        )
        actors["dave"] = {"actor_id": "rev_dave", "token": token_dave, "group": "group_alpha"}

    return {
        "home": project_dir,
        "project_id": project_id,
        "store": store,
        "owner_token": owner_token,
        "actors": actors,
    }


def make_proposal(
    project_id: str,
    snapshot: dict,
    proposal_id: str,
    operations: list[dict],
    read_set: dict[str, int] | None = None,
    origin_channel: str = "chatgpt_web",
    note: str = "Automated test proposal",
) -> dict[str, Any]:
    base_info = {
        "snapshot_id": snapshot["snapshot_id"],
        "state_revision": snapshot["state_revision"],
        "state_sha256": snapshot["state_sha256"],
    }
    rs = read_set if read_set is not None else dict(snapshot.get("object_revisions", {}))
    return {
        "protocol_version": PROTOCOL_VERSION,
        "proposal_id": proposal_id,
        "project_id": project_id,
        "base": base_info,
        "read_set": rs,
        "origin_channel": origin_channel,
        "operations": operations,
        "note": note,
    }
