"""Project status and object history inspection."""
from __future__ import annotations

import json
from typing import Any, Optional

from research_harness.state.store import StateStore


def get_project_status(store: StateStore) -> dict[str, Any]:
    with store.read() as conn:
        rev_row = conn.execute("SELECT MAX(revision) FROM state_revisions").fetchone()
        current_rev = rev_row[0] if rev_row else 0
        state_sha = conn.execute(
            "SELECT state_sha256 FROM state_revisions WHERE revision=?", (current_rev,)
        ).fetchone()
        sha_val = state_sha[0] if state_sha else ""

        heads = conn.execute(
            "SELECT object_type, lifecycle_status, COUNT(*) as cnt FROM object_heads GROUP BY object_type, lifecycle_status"
        ).fetchall()

        objects_by_type: dict[str, dict[str, int]] = {}
        for h in heads:
            otype, lcycle, cnt = h["object_type"], h["lifecycle_status"], h["cnt"]
            if otype not in objects_by_type:
                objects_by_type[otype] = {}
            objects_by_type[otype][lcycle] = cnt

        review_counts = conn.execute(
            """SELECT s.scientific_review_status, COUNT(*) as cnt
               FROM object_heads h
               JOIN object_status s ON s.object_id = h.object_id AND s.revision = h.head_revision
               WHERE h.lifecycle_status = 'ACTIVE'
               GROUP BY s.scientific_review_status"""
        ).fetchall()
        reviews = {r["scientific_review_status"]: r["cnt"] for r in review_counts}

        invals = conn.execute("SELECT COUNT(*) FROM invalidations WHERE status='OPEN'").fetchone()[0]
        events_cnt = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        proposals_cnt = conn.execute("SELECT COUNT(*) FROM proposals").fetchone()[0]
        snapshots_cnt = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]

    return {
        "project_id": store.project_id,
        "state_revision": current_rev,
        "state_sha256": sha_val,
        "active_objects_by_type": objects_by_type,
        "head_review_statuses": reviews,
        "open_invalidations": invals,
        "event_count": events_cnt,
        "proposals_accepted": proposals_cnt,
        "snapshots_exported": snapshots_cnt,
    }


def get_object_history(store: StateStore, object_id: str) -> dict[str, Any]:
    with store.read() as conn:
        head = conn.execute("SELECT * FROM object_heads WHERE object_id=?", (object_id,)).fetchone()
        if not head:
            return {"object_id": object_id, "found": False}

        revisions = conn.execute(
            """SELECT o.revision, o.object_type, o.schema_version, o.content_digest, o.revision_digest,
                      o.origin, o.created_at, o.created_state_revision, o.submitted_by,
                      s.scientific_review_status, s.prior_review_status, s.updated_at as status_updated_at
               FROM objects o
               JOIN object_status s ON s.object_id = o.object_id AND s.revision = o.revision
               WHERE o.object_id = ?
               ORDER BY o.revision ASC""",
            (object_id,),
        ).fetchall()

        rev_list = []
        for r in revisions:
            item = dict(r)
            try:
                item["origin"] = json.loads(item["origin"])
            except Exception:
                pass
            # Fetch edges out
            edges_out = [
                {"relation": e["relation"], "target_id": e["target_id"], "target_revision": e["target_revision"]}
                for e in store.edges_from(conn, object_id, r["revision"])
            ]
            item["edges_out"] = edges_out
            rev_list.append(item)

        status_logs = [
            dict(row)
            for row in conn.execute(
                "SELECT seq, revision, dimension, old_value, new_value, cause, state_revision, actor_id, at "
                "FROM status_log WHERE object_id=? ORDER BY seq ASC",
                (object_id,),
            ).fetchall()
        ]
        for sl in status_logs:
            try:
                sl["cause"] = json.loads(sl["cause"])
            except Exception:
                pass

        invals = [
            dict(row)
            for row in conn.execute(
                "SELECT inval_id, revision, cause_object_id, cause_from_revision, cause_to_revision, "
                "via_relation, hops, status, path, created_state_revision "
                "FROM invalidations WHERE object_id=? ORDER BY inval_id ASC",
                (object_id,),
            ).fetchall()
        ]
        for inv in invals:
            try:
                inv["path"] = json.loads(inv["path"])
            except Exception:
                pass

    return {
        "object_id": object_id,
        "found": True,
        "object_type": head["object_type"],
        "head_revision": head["head_revision"],
        "lifecycle_status": head["lifecycle_status"],
        "revisions": rev_list,
        "status_timeline": status_logs,
        "invalidations": invals,
    }
