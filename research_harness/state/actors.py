"""Authenticated actor administration (the ONLY supported way to create, change or disable actors).

Closes audit finding A1. Every operation here:

* authenticates the caller's token *inside* the same ``BEGIN IMMEDIATE`` transaction that mutates the
  ``actors`` table (``StateStore.write_tx(token=..., require_roles=("owner",))``); a missing, invalid or
  disabled token, or a non-owner role, raises ``AuthorizationError`` before any write;
* never trusts a caller-supplied identity: ``created_by`` and the event/audit actor are the
  *authenticated* actor;
* writes a hash-chained event (``ACTOR_REGISTERED`` / ``ACTOR_UPDATED`` / ``ACTOR_DISABLED``) and an
  ``audit_log`` row in that same transaction, so the mutation and its evidence commit or roll back together;
* records refusals (outside the failed transaction) without ever storing the presented token.

``registry_findings`` replays the actor events and compares the result with the ``actors`` table, so a
row inserted/edited with a raw SQL client (bypassing this module) is detected by ``project audit``.
Like every internal hash chain this detects unanchored mutation only; see docs/INTEGRITY_AUDIT_SPEC.md.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Optional

from research_harness.core.auth import authenticate, new_token, token_fingerprint, token_hash, validate_roles
from research_harness.core.errors import AuthorizationError, HarnessError
from research_harness.core.model import ACTOR_ID_RE
from research_harness.state.store import StateStore

GROUP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
_UNSET = object()
ACTOR_EVENT_TYPES = ("ACTOR_REGISTERED", "ACTOR_UPDATED", "ACTOR_DISABLED", "ACTOR_REGISTRY_ATTESTED")


def _entry(row: sqlite3.Row | tuple) -> dict:
    actor_id, roles, group, display, tsha, disabled_at = row
    return {"actor_id": actor_id, "roles": sorted(json.loads(roles)), "independence_group": group,
            "display_name": display, "token_fingerprint": token_fingerprint(tsha),
            "disabled": bool(disabled_at)}


def registry_snapshot(conn: sqlite3.Connection) -> list[dict]:
    return [_entry(r) for r in conn.execute(
        "SELECT actor_id,roles,independence_group,display_name,token_sha256,disabled_at FROM actors ORDER BY actor_id")]


def _validate_identity(actor_id: str, group: Optional[str]) -> None:
    if not isinstance(actor_id, str) or not ACTOR_ID_RE.match(actor_id):
        raise HarnessError("ACTOR_ID_INVALID", "actor id must match " + ACTOR_ID_RE.pattern)
    if group is not None and not (isinstance(group, str) and GROUP_RE.match(group)):
        raise HarnessError("INDEPENDENCE_GROUP_INVALID", "independence group must match " + GROUP_RE.pattern)


def _validated_roles(roles) -> list[str]:
    try:
        return validate_roles(roles)
    except ValueError as e:
        raise HarnessError("ROLES_INVALID", str(e)) from e


def _record_refusal(store: StateStore, token: Optional[str], action: str, error: HarnessError, **detail: Any) -> None:
    """Audit a refused attempt. The presented token is only used to *name* the caller, never stored."""
    actor_id = None
    try:
        with store.read() as conn:
            actor = authenticate(conn, token)
            actor_id = actor.actor_id if actor else None
    except Exception:  # auditing must never mask the original refusal
        pass
    decision = "UNAUTHORIZED" if isinstance(error, AuthorizationError) else "REFUSED"
    store.audit(actor_id=actor_id, action=action, decision=decision, detail={"rule": error.code, **detail})


def _enabled_owner_count(conn: sqlite3.Connection, excluding: Optional[str] = None) -> int:
    n = 0
    for aid, roles, disabled in conn.execute("SELECT actor_id,roles,disabled_at FROM actors"):
        if aid != excluding and not disabled and "owner" in json.loads(roles):
            n += 1
    return n


def register_actor(store: StateStore, token: Optional[str], *, actor_id: str, roles, display_name: Optional[str] = None,
                   independence_group: Optional[str] = None) -> dict:
    """Create an actor. Requires an authenticated, enabled ``owner``. Returns the new actor's one-time token."""
    detail = {"target_actor_id": actor_id}
    try:
        _validate_identity(actor_id, independence_group)
        role_list = _validated_roles(roles)
        detail["requested_roles"] = role_list
        new_tok = new_token()
        with store.write_tx(token=token, require_roles=("owner",), bump=False) as tx:
            if tx.conn.execute("SELECT 1 FROM actors WHERE actor_id=?", (actor_id,)).fetchone():
                raise HarnessError("ACTOR_EXISTS", "actor id already registered")
            tsha = token_hash(new_tok)
            tx.conn.execute(
                "INSERT INTO actors(actor_id,display_name,roles,independence_group,token_sha256,created_at,created_by)"
                " VALUES (?,?,?,?,?,?,?)",
                (actor_id, display_name or actor_id, json.dumps(role_list), independence_group, tsha, tx.at, tx.actor_id))
            payload = {"actor_id": actor_id, "roles": role_list, "independence_group": independence_group,
                       "display_name": display_name or actor_id, "token_fingerprint": token_fingerprint(tsha),
                       "created_by": tx.actor_id}
            tx.event("ACTOR_REGISTERED", payload)
            tx.audit("actor_register", "RECORDED", payload)
            created_by = tx.actor_id
    except HarnessError as e:
        _record_refusal(store, token, "actor_register", e, **detail)
        raise
    return {"actor_id": actor_id, "roles": role_list, "independence_group": independence_group,
            "display_name": display_name or actor_id, "created_by": created_by, "token": new_tok}


def update_actor(store: StateStore, token: Optional[str], *, actor_id: str, roles=_UNSET,
                 independence_group=_UNSET, display_name=_UNSET) -> dict:
    """Change roles / independence group / display name. Owner only; the last owner cannot lose ``owner``."""
    detail = {"target_actor_id": actor_id}
    try:
        if independence_group is not _UNSET:
            _validate_identity(actor_id, independence_group)
        role_list = None if roles is _UNSET else _validated_roles(roles)
        with store.write_tx(token=token, require_roles=("owner",), bump=False) as tx:
            row = tx.conn.execute("SELECT actor_id,roles,independence_group,display_name,token_sha256,disabled_at "
                                  "FROM actors WHERE actor_id=?", (actor_id,)).fetchone()
            if row is None:
                raise HarnessError("ACTOR_UNKNOWN", "no such actor")
            before = _entry(row)
            after = dict(before)
            if role_list is not None:
                after["roles"] = role_list
                if "owner" in before["roles"] and "owner" not in role_list and not before["disabled"] \
                        and _enabled_owner_count(tx.conn, excluding=actor_id) == 0:
                    raise HarnessError("LAST_OWNER", "cannot remove the owner role from the last enabled owner")
            if independence_group is not _UNSET:
                after["independence_group"] = independence_group
            if display_name is not _UNSET:
                after["display_name"] = display_name
            changed = sorted(k for k in ("roles", "independence_group", "display_name") if after[k] != before[k])
            if not changed:
                raise HarnessError("NO_CHANGE", "nothing to update")
            tx.conn.execute("UPDATE actors SET roles=?, independence_group=?, display_name=? WHERE actor_id=?",
                            (json.dumps(after["roles"]), after["independence_group"], after["display_name"], actor_id))
            payload = {**after, "changed": changed, "previous": {k: before[k] for k in changed},
                       "updated_by": tx.actor_id}
            payload.pop("disabled", None)
            tx.event("ACTOR_UPDATED", payload)
            tx.audit("actor_update", "RECORDED", payload)
            updated_by = tx.actor_id
    except HarnessError as e:
        _record_refusal(store, token, "actor_update", e, **detail)
        raise
    return {"actor_id": actor_id, "changed": changed, "roles": after["roles"],
            "independence_group": after["independence_group"], "updated_by": updated_by}


def disable_actor(store: StateStore, token: Optional[str], *, actor_id: str) -> dict:
    """Disable an actor (its token stops authenticating). Owner only; the last owner cannot be disabled."""
    detail = {"target_actor_id": actor_id}
    try:
        with store.write_tx(token=token, require_roles=("owner",), bump=False) as tx:
            row = tx.conn.execute("SELECT roles,disabled_at FROM actors WHERE actor_id=?", (actor_id,)).fetchone()
            if row is None:
                raise HarnessError("ACTOR_UNKNOWN", "no such actor")
            if row[1]:
                raise HarnessError("ACTOR_ALREADY_DISABLED", "actor already disabled")
            if "owner" in json.loads(row[0]) and _enabled_owner_count(tx.conn, excluding=actor_id) == 0:
                raise HarnessError("LAST_OWNER", "cannot disable the last enabled owner")
            tx.conn.execute("UPDATE actors SET disabled_at=? WHERE actor_id=?", (tx.at, actor_id))
            payload = {"actor_id": actor_id, "disabled_by": tx.actor_id}
            tx.event("ACTOR_DISABLED", payload)
            tx.audit("actor_disable", "RECORDED", payload)
            disabled_by = tx.actor_id
    except HarnessError as e:
        _record_refusal(store, token, "actor_disable", e, **detail)
        raise
    return {"actor_id": actor_id, "disabled_by": disabled_by}


def attest_registry(store: StateStore, token: Optional[str]) -> dict:
    """Owner-authenticated baseline of the whole registry (used by the 9.0.1 -> 9.0.2 upgrade)."""
    try:
        with store.write_tx(token=token, require_roles=("owner",), bump=False) as tx:
            attest_registry_in_tx(tx)
            n = len(registry_snapshot(tx.conn))
    except HarnessError as e:
        _record_refusal(store, token, "actor_registry_attest", e)
        raise
    return {"actors_attested": n}


def attest_registry_in_tx(tx) -> None:
    actors = registry_snapshot(tx.conn)
    tx.event("ACTOR_REGISTRY_ATTESTED", {"actors": actors, "attested_by": tx.actor_id})
    tx.audit("actor_registry_attest", "RECORDED", {"actors": len(actors)})


# ---------------------------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------------------------
def replay_registry(conn: sqlite3.Connection) -> dict[str, dict]:
    reg: dict[str, dict] = {}
    for et, payload in conn.execute("SELECT event_type,payload FROM events ORDER BY seq"):
        try:
            p = json.loads(payload)
        except Exception:
            continue
        if et == "PROJECT_INITIALIZED" and isinstance(p.get("owner"), dict):
            o = p["owner"]
            reg[o["actor_id"]] = {"actor_id": o["actor_id"], "roles": sorted(o["roles"]),
                                  "independence_group": o.get("independence_group"),
                                  "display_name": o.get("display_name", ""), "token_fingerprint": o["token_fingerprint"],
                                  "disabled": False}
        elif et == "ACTOR_REGISTRY_ATTESTED":
            reg = {a["actor_id"]: dict(a) for a in p.get("actors", [])}
        elif et == "ACTOR_REGISTERED":
            reg[p["actor_id"]] = {"actor_id": p["actor_id"], "roles": sorted(p["roles"]),
                                  "independence_group": p.get("independence_group"),
                                  "display_name": p.get("display_name", ""),
                                  "token_fingerprint": p["token_fingerprint"], "disabled": False}
        elif et == "ACTOR_UPDATED" and p.get("actor_id") in reg:
            for k in ("roles", "independence_group", "display_name"):
                if k in p:
                    reg[p["actor_id"]][k] = sorted(p[k]) if k == "roles" else p[k]
        elif et == "ACTOR_DISABLED" and p.get("actor_id") in reg:
            reg[p["actor_id"]]["disabled"] = True
    return reg


def _schema_tuple(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.split("."))
    except Exception:
        return (0,)


def registry_findings(conn: sqlite3.Connection) -> list[dict]:
    """Compare the actors table with the registry reconstructed from the hash-chained events."""
    row = conn.execute("SELECT value FROM meta WHERE key='db_schema_version'").fetchone()
    pre_attestation = row is not None and _schema_tuple(row[0]) < (9, 0, 2)
    replayed = replay_registry(conn)
    table = {a["actor_id"]: a for a in registry_snapshot(conn)}
    out: list[dict] = []
    severity = "WARNING" if pre_attestation else "ERROR"
    for aid, t in sorted(table.items()):
        r = replayed.get(aid)
        if r is None:
            out.append({"rule": "ACTOR_NOT_IN_EVENT_LOG", "severity": severity, "actor_id": aid,
                        "message": "actor row has no registration event; created outside actor administration"
                        + (" (database predates 9.0.2: run `research project upgrade-schema`)" if pre_attestation else "")})
            continue
        diff = sorted(k for k in t if t[k] != r.get(k))
        if diff:
            out.append({"rule": "ACTOR_REGISTRY_MISMATCH", "severity": "ERROR", "actor_id": aid, "fields": diff,
                        "message": "actors table differs from the registry reconstructed from the event log"})
    for aid in sorted(set(replayed) - set(table)):
        out.append({"rule": "ACTOR_EVENT_WITHOUT_ROW", "severity": "ERROR", "actor_id": aid})
    return out
