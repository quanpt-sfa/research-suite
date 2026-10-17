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
