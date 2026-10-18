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

    from research_harness.state.actors import register_actor, update_actor
    update_actor(store, owner_token, actor_id="owner_alice", independence_group="group_owner")

    # Register additional actors
    actors = {}
    # 1. Author Bob
    res_bob = register_actor(
        store, owner_token, actor_id="author_bob", display_name="Author Bob",
        roles=["author", "importer"], independence_group="group_alpha",
    )
    actors["bob"] = {"actor_id": "author_bob", "token": res_bob["token"], "group": "group_alpha"}

    # 2. Reviewer Carol (independent, group_beta)
    res_carol = register_actor(
        store, owner_token, actor_id="rev_carol", display_name="Reviewer Carol",
        roles=["independent_reviewer"], independence_group="group_beta",
    )
    actors["carol"] = {"actor_id": "rev_carol", "token": res_carol["token"], "group": "group_beta"}

    # 3. Reviewer Dave (in same group_alpha as Bob - non-independent of Bob)
    res_dave = register_actor(
        store, owner_token, actor_id="rev_dave", display_name="Reviewer Dave",
        roles=["independent_reviewer"], independence_group="group_alpha",
    )
    actors["dave"] = {"actor_id": "rev_dave", "token": res_dave["token"], "group": "group_alpha"}

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
