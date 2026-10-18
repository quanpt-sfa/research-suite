"""Tests for P0 Authorization Remediation (Audit Finding A1).

Proves that:
1. Missing token -> rejected.
2. Invalid token -> rejected.
3. Disabled actor -> rejected.
4. Importer attempts to create owner -> rejected.
5. Reviewer attempts to grant reviewer/owner roles -> rejected.
6. Valid owner creates an authorized actor -> accepted.
7. Failed operations leave no partial mutation.
8. Recorded audit identity matches the authenticated actor.
9. Disabling last remaining owner -> rejected.
"""
from __future__ import annotations

import argparse
import json
import pytest

from research_harness.cli import cmd_project_actor_add, cmd_project_actor_disable, cmd_project_actor_update
from research_harness.core.auth import authenticate, token_hash
from research_harness.core.errors import AuthorizationError, HarnessError
from research_harness.state.actors import disable_actor, register_actor, update_actor
from research_harness.state.store import StateStore


def test_missing_token_rejected(harness_env):
    store = harness_env["store"]
    with pytest.raises(AuthorizationError) as exc_info:
        register_actor(store, None, actor_id="new_actor", roles=["author"])
    assert exc_info.value.code in {"AUTHENTICATION_FAILED", "ROLE_REQUIRED"}


def test_invalid_token_rejected(harness_env):
    store = harness_env["store"]
    with pytest.raises(AuthorizationError) as exc_info:
        register_actor(store, "rhk_invalidtoken1234567890123456789012", actor_id="new_actor", roles=["author"])
    assert exc_info.value.code == "AUTHENTICATION_FAILED"


def test_disabled_actor_rejected(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]

    # 1. Owner creates actor_alice
    created = register_actor(store, owner_token, actor_id="actor_alice", roles=["owner"])
    alice_token = created["token"]

    # Alice can create an actor
    res = register_actor(store, alice_token, actor_id="actor_bob", roles=["author"])
    assert res["actor_id"] == "actor_bob"

    # Owner disables Alice
    disable_actor(store, owner_token, actor_id="actor_alice")

    # Disabled Alice attempts to create actor -> REJECTED
    with pytest.raises(AuthorizationError) as exc_info:
        register_actor(store, alice_token, actor_id="actor_charlie", roles=["author"])
    assert exc_info.value.code == "AUTHENTICATION_FAILED"


def test_importer_cannot_create_owner(harness_env):
    store = harness_env["store"]
    bob_token = harness_env["actors"]["bob"]["token"]  # importer + author

    # Importer attempts to create owner -> REJECTED with ROLE_REQUIRED
    with pytest.raises(AuthorizationError) as exc_info:
        register_actor(store, bob_token, actor_id="sneaky_owner", roles=["owner"])
    assert exc_info.value.code == "ROLE_REQUIRED"


def test_reviewer_cannot_grant_roles(harness_env):
    store = harness_env["store"]
    carol_token = harness_env["actors"]["carol"]["token"]  # independent_reviewer

    # Reviewer attempts to create reviewer or owner -> REJECTED
    with pytest.raises(AuthorizationError) as exc_info:
        register_actor(store, carol_token, actor_id="another_reviewer", roles=["independent_reviewer"])
    assert exc_info.value.code == "ROLE_REQUIRED"


def test_valid_owner_creates_authorized_actor(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]

    res = register_actor(
        store,
        owner_token,
        actor_id="valid_importer",
        roles=["importer"],
        display_name="Authorized Importer",
        independence_group="group_import",
    )
    assert res["actor_id"] == "valid_importer"
    assert "importer" in res["roles"]
    assert res["created_by"] == "owner_alice"
    assert res["token"].startswith("rhk_")

    # Verify actor can authenticate
    with store.read() as conn:
        actor = authenticate(conn, res["token"])
        assert actor is not None
        assert actor.actor_id == "valid_importer"
        assert actor.has_role("importer")


def test_failed_operations_leave_no_partial_mutation(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    carol_token = harness_env["actors"]["carol"]["token"]

    # Count actors and events before failed attempt
    with store.read() as conn:
        actors_before = conn.execute("SELECT COUNT(*) FROM actors").fetchone()[0]
        events_before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    # Attempt unauthorized registration
    with pytest.raises(AuthorizationError):
        register_actor(store, carol_token, actor_id="phantom_actor", roles=["author"])

    # Count after: no partial rows inserted
    with store.read() as conn:
        actors_after = conn.execute("SELECT COUNT(*) FROM actors").fetchone()[0]
        events_after = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        phantom = conn.execute("SELECT 1 FROM actors WHERE actor_id='phantom_actor'").fetchone()

    assert actors_before == actors_after
    assert events_before == events_after
    assert phantom is None


def test_recorded_audit_identity_matches_authenticated_actor(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    # 1. Owner creates actor -> audit log records owner_alice
    register_actor(store, owner_token, actor_id="actor_target_1", roles=["author"])
    with store.read() as conn:
        row = conn.execute(
            "SELECT actor_id, action, decision, detail FROM audit_log WHERE action='actor_register' AND decision='RECORDED' ORDER BY at DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row[0] == "owner_alice"
        det = json.loads(row[3])
        assert det["actor_id"] == "actor_target_1"

    # 2. Unauthorized attempt by bob -> audit log records refusal tied to author_bob
    with pytest.raises(AuthorizationError):
        register_actor(store, bob_token, actor_id="actor_target_2", roles=["author"])

    with store.read() as conn:
        row_refusal = conn.execute(
            "SELECT actor_id, action, decision, detail FROM audit_log WHERE action='actor_register' AND decision='UNAUTHORIZED' ORDER BY at DESC LIMIT 1"
        ).fetchone()
        assert row_refusal is not None
        assert row_refusal[0] == "author_bob"


def test_cannot_disable_or_demote_last_owner(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]

    # Attempt to disable owner_alice (who is the sole owner) -> REJECTED with LAST_OWNER
    with pytest.raises(HarnessError) as exc_info:
        disable_actor(store, owner_token, actor_id="owner_alice")
    assert exc_info.value.code == "LAST_OWNER"

    # Attempt to remove owner role from owner_alice -> REJECTED with LAST_OWNER
    with pytest.raises(HarnessError) as exc_info2:
        update_actor(store, owner_token, actor_id="owner_alice", roles=["author"])
    assert exc_info2.value.code == "LAST_OWNER"


def test_cli_actor_add_requires_owner_token(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    # 1. CLI with missing token fails
    args_no_token = argparse.Namespace(
        project_id=store.project_id,
        actor_id="cli_actor_1",
        roles="author",
        display_name="CLI Actor",
        independence_group=None,
        token=None,
        token_file=None,
        home=str(harness_env["home"]),
        json=True,
    )
    rc = cmd_project_actor_add(args_no_token)
    assert rc == 1

    # 2. CLI with unauthorized token (bob) fails
    args_bob = argparse.Namespace(
        project_id=store.project_id,
        actor_id="cli_actor_2",
        roles="author",
        display_name="CLI Actor 2",
        independence_group=None,
        token=bob_token,
        token_file=None,
        home=str(harness_env["home"]),
        json=True,
    )
    rc_bob = cmd_project_actor_add(args_bob)
    assert rc_bob == 1

    # 3. CLI with owner token succeeds
    args_owner = argparse.Namespace(
        project_id=store.project_id,
        actor_id="cli_actor_3",
        roles="author",
        display_name="CLI Actor 3",
        independence_group=None,
        token=owner_token,
        token_file=None,
        home=str(harness_env["home"]),
        json=True,
    )
    rc_owner = cmd_project_actor_add(args_owner)
    assert rc_owner == 0
