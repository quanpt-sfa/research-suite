"""Hash-chained event log living inside the state database.

Events are appended in the same SQLite transaction as the state change they describe, so the
log can never describe a change that did not commit (or omit one that did). The chain uses the
v8.4.4 ``canonical_json_hash``. Like v8, an internal chain detects accidental or unanchored
tampering only; see ``project anchor`` for exporting the head to an external trusted place.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from research_harness.core.canonical import canonical_json_hash

GENESIS = "GENESIS"


def _body(seq, event_type, actor_id, state_revision, at, payload, prev):
    return {"seq": seq, "event_type": event_type, "actor_id": actor_id,
            "state_revision": state_revision, "at": at, "payload": payload, "prev_sha256": prev}


def chain_head(conn: sqlite3.Connection) -> tuple[int, str]:
    row = conn.execute("SELECT seq, event_sha256 FROM events ORDER BY seq DESC LIMIT 1").fetchone()
    return (row[0], row[1]) if row else (0, GENESIS)


def append_event(conn: sqlite3.Connection, *, event_type: str, actor_id: str, state_revision: int,
                 at: str, payload: dict[str, Any]) -> dict:
    """Append one event. Caller must hold the write transaction (BEGIN IMMEDIATE)."""
    last_seq, prev = chain_head(conn)
    seq = last_seq + 1
    body = _body(seq, event_type, actor_id, state_revision, at, payload, prev)
    digest = canonical_json_hash(body)
    conn.execute(
        "INSERT INTO events(seq,event_id,state_revision,event_type,actor_id,at,payload,prev_sha256,event_sha256)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (seq, f"EVT_{seq:07d}", state_revision, event_type, actor_id, at,
         json.dumps(payload, sort_keys=True, ensure_ascii=False), prev, digest))
    return {"seq": seq, "event_id": f"EVT_{seq:07d}", "event_sha256": digest}


def verify_chain(conn: sqlite3.Connection) -> list[dict]:
    findings: list[dict] = []
    prev = GENESIS
    expected_seq = 1
    for row in conn.execute("SELECT seq,event_type,actor_id,state_revision,at,payload,prev_sha256,event_sha256 "
                            "FROM events ORDER BY seq"):
        seq, et, actor, rev, at, payload, p, h = row
        if seq != expected_seq:
            findings.append({"rule": "EVENT_SEQUENCE_GAP", "expected": expected_seq, "actual": seq})
            expected_seq = seq
        expected_seq += 1
        try:
            payload_obj = json.loads(payload)
        except Exception:
            findings.append({"rule": "EVENT_PAYLOAD_INVALID_JSON", "seq": seq})
            prev = h
            continue
        if p != prev:
            findings.append({"rule": "EVENT_CHAIN_BROKEN", "seq": seq, "expected_prev": prev, "actual_prev": p})
        if canonical_json_hash(_body(seq, et, actor, rev, at, payload_obj, p)) != h:
            findings.append({"rule": "EVENT_HASH_MISMATCH", "seq": seq})
        prev = h
    return findings
