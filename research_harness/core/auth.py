"""Actors, roles and token authentication.

Honest scope: a token proves the caller holds a secret that the owner issued to a registered
actor. It does NOT prove real-world independence of two reviewers; ``independence_group`` is an
owner-asserted label. Both limits are stated in docs/THREAT_MODEL.md.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from dataclasses import dataclass
from typing import Optional

from research_harness.core.model import ROLES


@dataclass(frozen=True)
class Actor:
    actor_id: str
    roles: frozenset
    independence_group: Optional[str] = None
    display_name: str = ""

    def has_role(self, *roles: str) -> bool:
        return any(r in self.roles for r in roles)


def new_token() -> str:
    return "rhk_" + secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def validate_roles(roles) -> list[str]:
    roles = sorted(set(roles))
    bad = [r for r in roles if r not in ROLES]
    if bad or not roles:
        raise ValueError(f"invalid roles {bad or roles}; allowed: {list(ROLES)}")
    return roles


def authenticate(conn: sqlite3.Connection, token: Optional[str]) -> Optional[Actor]:
    """Return the *server-side* actor bound to this token, or None."""
    if not token or not isinstance(token, str) or len(token) > 256:
        return None
    row = conn.execute(
        "SELECT actor_id,roles,independence_group,display_name,disabled_at FROM actors WHERE token_sha256=?",
        (token_hash(token),)).fetchone()
    if not row or row[4]:
        return None
    return Actor(row[0], frozenset(json.loads(row[1])), row[2], row[3])


def load_actor(conn: sqlite3.Connection, actor_id: str) -> Optional[Actor]:
    """Re-read an *enabled* actor by id (used to re-verify an identity inside a write transaction)."""
    if not actor_id or not isinstance(actor_id, str):
        return None
    row = conn.execute(
        "SELECT actor_id,roles,independence_group,display_name,disabled_at FROM actors WHERE actor_id=?",
        (actor_id,)).fetchone()
    if not row or row[4]:
        return None
    return Actor(row[0], frozenset(json.loads(row[1])), row[2], row[3])


def token_fingerprint(token_sha256: str) -> str:
    """Short, non-reversible identifier of a stored token hash for the event log.

    It is a hash *of the stored hash*, so it cannot be used to authenticate and it reveals nothing
    that is not already in the database; it only lets the audit prove which registry row an event
    described.
    """
    return hashlib.sha256(("rh-token-fp:" + token_sha256).encode("utf-8")).hexdigest()[:16]


class InternalCapability:
    """Non-forgeable capability object for engine-internal, offline maintenance, or test fixtures.

    Cannot be instantiated with raw strings or spoofed by external callers.
    """
    _sentinel = object()

    def __init__(self, sentinel: object, actor_id: str, purpose: str):
        if sentinel is not InternalCapability._sentinel:
            raise PermissionError("InternalCapability can only be issued via issue_internal_capability()")
        self.actor_id = actor_id
        self.purpose = purpose

    def __repr__(self) -> str:
        return f"<InternalCapability actor={self.actor_id} purpose={self.purpose}>"


def issue_internal_capability(actor_id: str, purpose: str = "engine_internal") -> InternalCapability:
    """Issue a scoped internal capability for engine-internal maintenance or test fixtures."""
    if not actor_id or not isinstance(actor_id, str):
        raise ValueError("actor_id must be a non-empty string")
    return InternalCapability(InternalCapability._sentinel, actor_id, purpose)
