"""Dependency traversal and the invalidation engine.

Graph nodes are ``(object_id, revision)``. Because every edge points at *existing* revisions, the
revision-level graph is acyclic by construction; object-level cycles (Theory -> Claim -> revised
Theory ...) are legitimate non-linear research and are handled deterministically: traversal is a
breadth-first search over revision nodes with a visited set and sorted frontiers, so it
terminates (even on cycles injected by migration or tampering) and always yields the same order.

Invalidation never rewrites history: it flags the *head* revision of each affected dependent as
``NEEDS_REVIEW`` and records an ``invalidations`` row with the cause and path. Previous review
statuses stay in ``status_log`` / Approval objects.
"""
from __future__ import annotations

import json
import sqlite3
from collections import deque
from typing import Optional

from research_harness.core.model import Lifecycle, ReviewStatus
from research_harness.graph.relations import SPECS

Node = tuple  # (object_id, revision)


def _fmt(n: Node) -> str:
    return f"{n[0]}@{n[1]}"


def dependents_of(conn: sqlite3.Connection, node: Node) -> list[tuple[dict, Node]]:
    """Edges through which some node depends on ``node`` -> [(edge, dependent_node)], sorted."""
    out: list[tuple[dict, Node]] = []
    for e in conn.execute("SELECT * FROM edges WHERE target_id=? AND target_revision=?", node):
        if SPECS[e["relation"]].dependent == "source":
            out.append((dict(e), (e["source_id"], e["source_revision"])))
    for e in conn.execute("SELECT * FROM edges WHERE source_id=? AND source_revision=?", node):
        if SPECS[e["relation"]].dependent == "target":
            out.append((dict(e), (e["target_id"], e["target_revision"])))
    out.sort(key=lambda t: (t[1], t[0]["relation"], t[0]["edge_id"]))
    return out


def _head_info(conn: sqlite3.Connection, object_id: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT head_revision, lifecycle_status FROM object_heads WHERE object_id=?",
                        (object_id,)).fetchone()


def traverse(conn: sqlite3.Connection, start_nodes: list[Node]) -> list[dict]:
    """BFS from stale upstream nodes; returns affected dependents (head revisions only).

    A dependent is reported when it is the active head of its object. The search continues
    *through* a dependent only when the relation it used propagates.
    """
    affected: dict[Node, dict] = {}
    visited: set[Node] = set(start_nodes)
    queue: deque = deque((n, [], 0) for n in sorted(start_nodes))
    while queue:
        node, path, hops = queue.popleft()
        for edge, dep in dependents_of(conn, node):
            head = _head_info(conn, dep[0])
            if head is None or head["head_revision"] != dep[1] or head["lifecycle_status"] != Lifecycle.ACTIVE.value:
                continue  # superseded or withdrawn revisions are history, not work items
            step = {"from": _fmt(node), "relation": edge["relation"], "to": _fmt(dep)}
            if dep in affected or dep in visited:
                continue
            affected[dep] = {"object_id": dep[0], "revision": dep[1], "hops": hops + 1,
                             "via_relation": edge["relation"], "path": path + [step]}
            visited.add(dep)
            if SPECS[edge["relation"]].propagates:
                queue.append((dep, path + [step], hops + 1))
    return sorted(affected.values(), key=lambda a: (a["hops"], a["object_id"], a["revision"]))


def invalidate_dependents(tx, changed_id: str, new_revision: int) -> list[dict]:
    """Call after ``changed_id`` received ``new_revision``. Flags dependents of every older revision."""
    stale = [(changed_id, r) for r in range(1, new_revision)]
    if not stale:
        return []
    results = []
    for a in traverse(tx.conn, stale):
        cur = tx.conn.execute("SELECT scientific_review_status FROM object_status WHERE object_id=? AND revision=?",
                              (a["object_id"], a["revision"])).fetchone()[0]
        cause = {"kind": "UPSTREAM_REVISION_CHANGED", "cause_object_id": changed_id,
                 "cause_from_revision": new_revision - 1, "cause_to_revision": new_revision,
                 "via_relation": a["via_relation"], "hops": a["hops"], "path": a["path"]}
        if cur == ReviewStatus.REJECTED.value:
            continue  # a rejected object stays rejected; nothing to re-review
        tx.conn.execute(
            "INSERT INTO invalidations(object_id,revision,cause_object_id,cause_from_revision,cause_to_revision,"
            "via_relation,hops,path,created_state_revision,status) VALUES (?,?,?,?,?,?,?,?,?,'OPEN')",
            (a["object_id"], a["revision"], changed_id, new_revision - 1, new_revision, a["via_relation"],
             a["hops"], json.dumps(a["path"]), tx.revision))
        tx.set_review(a["object_id"], a["revision"], ReviewStatus.NEEDS_REVIEW, cause)
        tx.changed = True
        tx.event("INVALIDATION_RECORDED", {"object_id": a["object_id"], "revision": a["revision"],
                                           "previous_review_status": cur, **cause})
        results.append({"object_id": a["object_id"], "revision": a["revision"], "previous_review_status": cur,
                        "cause_object_id": changed_id, "cause_to_revision": new_revision,
                        "via_relation": a["via_relation"], "hops": a["hops"], "path": a["path"]})
    return results


def impact_report(conn: sqlite3.Connection, object_id: str) -> dict:
    """Read-only: who is affected if ``object_id`` changes now, and what is already invalidated by it."""
    head = _head_info(conn, object_id)
    if head is None:
        return {"object_id": object_id, "found": False}
    would = traverse(conn, [(object_id, head["head_revision"])])
    stale_edges = []
    for r in range(1, head["head_revision"]):
        for edge, dep in dependents_of(conn, (object_id, r)):
            h = _head_info(conn, dep[0])
            if h and h["head_revision"] == dep[1]:
                stale_edges.append({"dependent": _fmt(dep), "relation": edge["relation"],
                                    "pinned_upstream": _fmt((object_id, r)),
                                    "current_upstream": _fmt((object_id, head["head_revision"]))})
    opened = [dict(r) for r in conn.execute(
        "SELECT inval_id,object_id,revision,cause_from_revision,cause_to_revision,via_relation,hops,status "
        "FROM invalidations WHERE cause_object_id=? ORDER BY inval_id", (object_id,))]
    return {"object_id": object_id, "found": True, "head_revision": head["head_revision"],
            "if_changed_would_flag": would, "dependents_pinned_to_older_revisions": stale_edges,
            "invalidations_caused_by_this_object": opened}


def stale_pinned_edges(conn: sqlite3.Connection, object_id: str, revision: int) -> list[dict]:
    """Outgoing dependency edges of a head revision that point at superseded upstream revisions."""
    out = []
    for e in conn.execute("SELECT * FROM edges WHERE source_id=? AND source_revision=?", (object_id, revision)):
        spec = SPECS[e["relation"]]
        if spec.dependent == "source":
            up = (e["target_id"], e["target_revision"])
        else:
            continue
        h = _head_info(conn, up[0])
        if h and h["head_revision"] != up[1]:
            out.append({"relation": e["relation"], "pinned": _fmt(up), "current_head": h["head_revision"]})
    for e in conn.execute("SELECT * FROM edges WHERE target_id=? AND target_revision=?", (object_id, revision)):
        spec = SPECS[e["relation"]]
        if spec.dependent == "target":
            up = (e["source_id"], e["source_revision"])
            h = _head_info(conn, up[0])
            if h and h["head_revision"] != up[1]:
                out.append({"relation": e["relation"], "pinned": _fmt(up), "current_head": h["head_revision"]})
    return out
