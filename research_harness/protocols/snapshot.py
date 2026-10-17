"""Snapshot export and verification.

A snapshot is *read-only context* handed to an agent over an untrusted channel. It carries:

* ``body_sha256``       - plain SHA-256: integrity only (anyone can recompute it)
* ``body_hmac_sha256``  - HMAC-SHA256 with a key that exists only on the D13 host: authenticity
* ``meta_hmac_sha256``  - HMAC over the scope/permissions/revision map, also registered in the
                          project database so the gateway can authenticate the base of a proposal
                          without trusting anything the proposal says about itself.

The snapshot grants no authority: operations an agent may *propose* are declared in
``permitted_operations`` and enforced again by the gateway.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from research_harness import PROTOCOL_VERSION
from research_harness.core.auth import Actor
from research_harness.core.canonical import canonical_json_hash, hmac_equal, hmac_hex, strict_json_loads
from research_harness.core.errors import HarnessError
from research_harness.core.model import OBJECT_TYPES, SERVER_ONLY_TYPES
from research_harness.protocols.guards import secret_findings
from research_harness.protocols.schema import PROPOSABLE_TYPES
from research_harness.state.store import StateStore

_ALL_READ = list(OBJECT_TYPES)
SCOPES: dict[str, dict] = {
    "literature": {
        "read": ["ResearchQuestion", "Theory", "Construct", "Source", "EvidencePassage", "Claim"],
        "write": ["Source", "EvidencePassage"], "claims": False},
    "theory_novelty": {
        "read": ["ResearchQuestion", "Theory", "Construct", "Mechanism", "Proxy", "Claim", "Source",
                 "EvidencePassage", "Adjudication"],
        "write": ["ResearchQuestion", "Theory", "Construct", "Mechanism", "Adjudication", "Claim"], "claims": True},
    "design": {
        "read": ["ResearchQuestion", "Construct", "Mechanism", "Proxy", "Dataset", "Analysis", "Claim"],
        "write": ["Construct", "Proxy", "Dataset", "Analysis"], "claims": False},
    "analysis": {
        "read": ["Construct", "Mechanism", "Proxy", "Dataset", "Analysis", "Result", "Claim"],
        "write": ["Dataset", "Analysis", "Result"], "claims": False},
    "writing": {
        "read": ["Claim", "Source", "EvidencePassage", "Result", "ManuscriptArtifact"],
        "write": ["ManuscriptArtifact"], "claims": False},
    "full": {"read": _ALL_READ, "write": list(PROPOSABLE_TYPES), "claims": True},
}


def permitted_operations(scope: str) -> dict[str, list[str]]:
    s = SCOPES[scope]
    write = [t for t in s["write"] if t not in SERVER_ONLY_TYPES]
    return {"propose_object": [t for t in write if t != "Claim"],
            "revise_object": write,
            "propose_claim": ["Claim"] if s["claims"] else []}


def snapshot_meta(snapshot_id, project_id, scope, state_revision, state_sha256, permitted, object_revisions) -> dict:
    return {"snapshot_id": snapshot_id, "project_id": project_id, "scope": scope,
            "state_revision": state_revision, "state_sha256": state_sha256,
            "permitted_operations": permitted, "object_revisions": object_revisions}


def _summarize(store: StateStore, conn, head_row) -> dict:
    o = store.get_object(head_row["object_id"], None, conn)
    restricted = o["content"].get("sensitivity") == "restricted"
    edges = [{"relation": e["relation"], "target_id": e["target_id"], "target_revision": e["target_revision"]}
             for e in store.edges_from(conn, o["object_id"], o["revision"])]
    d = {"object_id": o["object_id"], "object_type": o["object_type"], "revision": o["revision"],
         "schema_version": o["schema_version"], "content_digest": o["content_digest"],
         "revision_digest": o["revision_digest"], "lifecycle_status": o["lifecycle_status"],
         "scientific_review_status": o["scientific_review_status"], "origin_kind": o["origin"].get("kind"),
         "edges_out": edges, "content": None if restricted else o["content"], "content_redacted": restricted}
    formation = o["origin"].get("formation")
    if formation:
        d["inference_status"] = formation["inference_status"]
        d["first_recorded_at"] = formation["first_recorded_at"]
    return d


def export_snapshot(store: StateStore, actor: Actor, scope: str) -> dict:
    if scope not in SCOPES:
        raise HarnessError("SCOPE_UNKNOWN", f"scope must be one of {sorted(SCOPES)}")
    if not actor.has_role("owner", "importer", "author"):
        raise HarnessError("UNAUTHORIZED", "exporting a snapshot requires role owner|importer|author")
    key = store.hmac_key
    with store.write_tx(actor.actor_id, bump=False) as tx:
        conn = tx.conn
        rev = tx.base_revision
        state_sha = conn.execute("SELECT state_sha256 FROM state_revisions WHERE revision=?", (rev,)).fetchone()[0]
        types = SCOPES[scope]["read"]
        heads = conn.execute(
            "SELECT * FROM object_heads WHERE object_type IN (%s) ORDER BY object_id" % ",".join("?" * len(types)),
            types).fetchall()
        objects = [_summarize(store, conn, h) for h in heads]
        included = {o["object_id"] for o in objects}
        neighbours: set[str] = set()
        for o in objects:
            for e in store.edges_from(conn, o["object_id"], o["revision"]):
                neighbours.add(e["target_id"])
            for e in store.edges_to(conn, o["object_id"], o["revision"]):
                neighbours.add(e["source_id"])
        referenced = []
        for oid in sorted(neighbours - included):
            h = tx.head(oid)
            digest = conn.execute("SELECT content_digest FROM objects WHERE object_id=? AND revision=?",
                                  (oid, h["head_revision"])).fetchone()[0]
            referenced.append({"object_id": oid, "object_type": h["object_type"], "revision": h["head_revision"],
                               "content_digest": digest})
        object_revisions = {o["object_id"]: o["revision"] for o in objects}
        object_revisions.update({r["object_id"]: r["revision"] for r in referenced})
        n = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] + 1
        sid = f"SNAP_{n:05d}"
        permitted = permitted_operations(scope)
        meta = snapshot_meta(sid, store.project_id, scope, rev, state_sha, permitted, object_revisions)
        body: dict[str, Any] = {
            "protocol_version": PROTOCOL_VERSION, "snapshot_id": sid, "project_id": store.project_id,
            "scope": scope, "state_revision": rev, "state_sha256": state_sha, "exported_at": tx.at,
            "exported_by": actor.actor_id, "objects": objects, "referenced_objects": referenced,
            "permitted_operations": permitted, "object_revisions": object_revisions,
            "notice": "Read-only context. Propose changes with the proposal protocol; this snapshot grants no "
                      "write, approval or verification authority."}
        leaks = secret_findings(body)
        if leaks:  # fail closed: never export something that looks like a credential
            raise HarnessError("SNAPSHOT_CONTAINS_SECRET_LIKE_DATA", "refusing to export", findings=leaks[:10])
        meta_hmac = hmac_hex(key, meta)
        conn.execute("INSERT INTO snapshots(snapshot_id,state_revision,state_sha256,scope,permitted_operations,"
                     "object_revisions,exported_at,exported_by,hmac_sha256) VALUES (?,?,?,?,?,?,?,?,?)",
                     (sid, rev, state_sha, scope, json.dumps(permitted, sort_keys=True),
                      json.dumps(object_revisions, sort_keys=True), tx.at, actor.actor_id, meta_hmac))
        conn.execute("INSERT INTO audit_log(at,actor_id,action,decision,proposal_id,detail) VALUES (?,?,?,?,?,?)",
                     (tx.at, actor.actor_id, "snapshot_export", "OK", None,
                      json.dumps({"snapshot_id": sid, "scope": scope, "state_revision": rev})))
    integrity = {
        "canonicalization": "json sorted keys, compact separators, UTF-8, ensure_ascii=false",
        "body_sha256": canonical_json_hash(body),
        "body_sha256_note": "integrity only; recomputable by anyone, NOT proof of origin",
        "meta_hmac_sha256": meta_hmac, "body_hmac_sha256": hmac_hex(key, body),
        "hmac_note": "HMAC-SHA256 with a key that exists only on the D13 host; verify with `research snapshot verify`"}
    return {**body, "integrity": integrity}


def registered_snapshot(conn, snapshot_id: str) -> Optional[dict]:
    r = conn.execute("SELECT * FROM snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["permitted_operations"] = json.loads(d["permitted_operations"])
    d["object_revisions"] = json.loads(d["object_revisions"])
    return d


def registered_snapshot_authentic(store: StateStore, snap: dict) -> bool:
    meta = snapshot_meta(snap["snapshot_id"], store.project_id, snap["scope"], snap["state_revision"],
                         snap["state_sha256"], snap["permitted_operations"], snap["object_revisions"])
    return hmac_equal(hmac_hex(store.hmac_key, meta), snap["hmac_sha256"])


def verify_snapshot_document(store: StateStore, raw: bytes | str) -> dict:
    findings = []
    try:
        doc = strict_json_loads(raw, max_bytes=64 * 1024 * 1024)
    except ValueError as e:
        return {"status": "FAIL", "findings": [{"rule": "SNAPSHOT_UNPARSEABLE", "message": str(e)}]}
    if not isinstance(doc, dict) or not isinstance(doc.get("integrity"), dict):
        return {"status": "FAIL", "findings": [{"rule": "SNAPSHOT_INTEGRITY_BLOCK_MISSING"}]}
    integrity = doc["integrity"]
    body = {k: v for k, v in doc.items() if k != "integrity"}
    key = store.hmac_key
    if canonical_json_hash(body) != integrity.get("body_sha256"):
        findings.append({"rule": "SNAPSHOT_BODY_SHA256_MISMATCH"})
    if not hmac_equal(hmac_hex(key, body), str(integrity.get("body_hmac_sha256", ""))):
        findings.append({"rule": "SNAPSHOT_HMAC_INVALID",
                         "message": "not produced by this D13 project key or modified in transit"})
    if body.get("project_id") != store.project_id:
        findings.append({"rule": "SNAPSHOT_PROJECT_MISMATCH"})
    with store.read() as conn:
        snap = registered_snapshot(conn, str(body.get("snapshot_id")))
    if snap is None:
        findings.append({"rule": "SNAPSHOT_NOT_REGISTERED"})
    else:
        meta = snapshot_meta(snap["snapshot_id"], store.project_id, snap["scope"], snap["state_revision"],
                             snap["state_sha256"], snap["permitted_operations"], snap["object_revisions"])
        if not hmac_equal(hmac_hex(key, meta), str(integrity.get("meta_hmac_sha256", ""))):
            findings.append({"rule": "SNAPSHOT_META_HMAC_MISMATCH"})
        if body.get("state_sha256") != snap["state_sha256"] or body.get("state_revision") != snap["state_revision"]:
            findings.append({"rule": "SNAPSHOT_STATE_MISMATCH_WITH_REGISTRY"})
    return {"status": "FAIL" if findings else "PASS", "snapshot_id": body.get("snapshot_id"), "findings": findings}
