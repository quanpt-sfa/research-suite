"""Proposal import gateway: agent proposes, engine validates, authorized actor imports.

Order of checks (fail closed; each failure maps to exactly one ``Decision``):

1. authenticate the *importing* actor from the server-side token  -> UNAUTHORIZED
2. strict JSON parse, protocol version, forbidden/secret fields, operation allow-list, schema
                                                                   -> INVALID_SCHEMA / REJECTED / UNAUTHORIZED
3. role check (importer|owner), project id                         -> UNAUTHORIZED / REJECTED
4. one ``BEGIN IMMEDIATE`` transaction containing, in order: idempotency, snapshot authenticity,
   scope, read-set / object-level optimistic concurrency, operation application (incl. dependency
   invalidation), receipt persistence, event log and state revision. Nothing is visible unless
   the whole transaction commits.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Optional

from research_harness import SUPPORTED_PROTOCOL_VERSIONS
from research_harness.core.auth import authenticate
from research_harness.core.canonical import StrictJSONError, sha256_bytes, strict_json_loads
from research_harness.core.errors import AuthorizationError, HarnessError, ProposalRejection
from research_harness.core.model import SERVER_ONLY_TYPES, Decision
from research_harness.protocols.guards import forbidden_key_paths, secret_findings
from research_harness.protocols.schema import ALLOWED_OPERATIONS, validate_proposal_structure
from research_harness.protocols.snapshot import registered_snapshot, registered_snapshot_authentic
from research_harness.proposals.operations import HANDLERS, Ctx, reject
from research_harness.state.store import StateStore


def static_check(raw: bytes | str) -> dict:
    """Context-free validation of an untrusted proposal. Returns the parsed doc or raises."""
    try:
        doc = strict_json_loads(raw)
    except StrictJSONError as e:
        raise ProposalRejection(Decision.INVALID_SCHEMA, "MALFORMED_JSON", str(e)) from e
    if not isinstance(doc, dict):
        raise ProposalRejection(Decision.INVALID_SCHEMA, "NOT_A_JSON_OBJECT", "proposal must be a JSON object")
    pv = doc.get("protocol_version")
    if isinstance(pv, str) and pv not in SUPPORTED_PROTOCOL_VERSIONS:
        raise ProposalRejection(Decision.REJECTED, "PROTOCOL_VERSION_UNSUPPORTED",
                                f"protocol {pv!r} not supported", supported=list(SUPPORTED_PROTOCOL_VERSIONS))
    bad = forbidden_key_paths(doc)
    if bad:
        raise ProposalRejection(Decision.UNAUTHORIZED, "FORBIDDEN_FIELD",
                                "identity, time, revision, approval and verification fields are server-assigned",
                                paths=bad[:20])
    ops = doc.get("operations")
    if isinstance(ops, list):
        for i, op in enumerate(ops):
            if not isinstance(op, dict) or not isinstance(op.get("operation"), str):
                continue  # structural problem; the schema reports it
            if op["operation"] not in ALLOWED_OPERATIONS:
                raise ProposalRejection(Decision.UNAUTHORIZED, "OPERATION_NOT_ALLOWED",
                                        f"operation {op['operation']!r} is not in the allow-list",
                                        index=i, allowed=list(ALLOWED_OPERATIONS))
            if op.get("object_type") in SERVER_ONLY_TYPES:
                raise ProposalRejection(Decision.UNAUTHORIZED, "PRIVILEGED_OBJECT_TYPE",
                                        f"{op['object_type']} objects are created only by the engine", index=i)
    leaks = secret_findings(doc)
    if leaks:
        raise ProposalRejection(Decision.REJECTED, "SECRET_LIKE_CONTENT",
                                "proposal contains credential-like keys or values", findings=leaks[:10])
    findings = validate_proposal_structure(doc)
    if findings:
        raise ProposalRejection(Decision.INVALID_SCHEMA, "SCHEMA_VIOLATION", "proposal does not match schema",
                                findings=findings)
    return doc


def _receipt(store: StateStore, decision: Decision, *, doc: Optional[dict], sha: str, actor_id: Optional[str],
             rule: Optional[str] = None, message: str = "", dry_run: bool = False, **extra) -> dict:
    base = (doc or {}).get("base") if isinstance((doc or {}).get("base"), dict) else None
    out = {"receipt_version": "9.0", "proposal_id": (doc or {}).get("proposal_id") if doc else None,
           "decision": str(decision), "rule": rule, "message": message, "project_id": store.project_id,
           "base": base, "proposal_sha256": sha, "import_actor": actor_id, "dry_run": dry_run,
           "timestamp": store.clock()}
    out.update(extra)
    return out


def _from_rejection(store, r: ProposalRejection, doc, sha, actor_id, dry_run) -> dict:
    extra = dict(r.details)
    conflicts = extra.pop("conflicts", None)
    rec = _receipt(store, r.decision, doc=doc, sha=sha, actor_id=actor_id, rule=r.rule, message=r.message,
                   dry_run=dry_run, details=extra)
    if conflicts is not None:
        rec["conflicts"] = conflicts
    rec.update(created_objects=[], revised_objects=[], invalidated_objects=[], new_state_revision=None)
    return rec


def _apply(store: StateStore, tx, doc: dict, raw: bytes, sha: str, dry_run: bool) -> dict:
    conn = tx.conn
    pid = doc["proposal_id"]
    # 1. idempotency -----------------------------------------------------------------------
    prior = conn.execute("SELECT content_sha256, receipt FROM proposals WHERE proposal_id=?", (pid,)).fetchone()
    if prior:
        if prior["content_sha256"] == sha:
            stored = json.loads(prior["receipt"])
            return {**stored, "decision": str(Decision.DUPLICATE), "original_decision": stored["decision"],
                    "timestamp": store.clock(), "dry_run": dry_run}
        reject("PROPOSAL_ID_REUSED_WITH_DIFFERENT_CONTENT",
               "this proposal_id was already accepted with different content; use a new proposal_id",
               Decision.CONFLICT, proposal_id=pid)
    # 2. snapshot authenticity --------------------------------------------------------------
    base = doc["base"]
    snap = registered_snapshot(conn, base["snapshot_id"])
    if snap is None:
        reject("SNAPSHOT_UNKNOWN", "base snapshot was not exported by this project", snapshot_id=base["snapshot_id"])
    if not registered_snapshot_authentic(store, snap):
        reject("SNAPSHOT_REGISTRY_TAMPERED", "snapshot registry entry fails HMAC verification")
    if snap["state_revision"] != base["state_revision"] or snap["state_sha256"] != base["state_sha256"]:
        reject("SNAPSHOT_BASE_MISMATCH", "base state revision/digest does not match the registered snapshot",
               registered_revision=snap["state_revision"], claimed_revision=base["state_revision"])
    hist = conn.execute("SELECT state_sha256 FROM state_revisions WHERE revision=?", (snap["state_revision"],)).fetchone()
    if not hist or hist[0] != snap["state_sha256"]:
        reject("SNAPSHOT_STATE_DIGEST_UNVERIFIABLE", "registered digest does not match the revision history")
    # 3. read set belongs to the snapshot; object-level optimistic concurrency ---------------
    read_set = doc["read_set"]
    outside = sorted(k for k, v in read_set.items() if snap["object_revisions"].get(k) != v)
    if outside:
        reject("READ_SET_NOT_IN_SNAPSHOT", "read_set may only list objects/revisions present in the base snapshot",
               objects=outside[:20])
    conflicts = []
    for oid, rev in sorted(read_set.items()):
        head = tx.head(oid)
        if head is None:
            reject("READ_SET_UNKNOWN_OBJECT", f"{oid} does not exist", object_id=oid)
        if head["head_revision"] != rev:
            conflicts.append({"object_id": oid, "expected_revision": rev, "actual_revision": head["head_revision"]})
    if conflicts:
        reject("READ_SET_CONFLICT", "objects read by this proposal changed after its base snapshot",
               Decision.CONFLICT, conflicts=conflicts)
    rebased = tx.base_revision != base["state_revision"]
    # 4. apply ------------------------------------------------------------------------------
    ctx = Ctx(tx=tx, doc=doc, proposal_sha256=sha, snapshot=snap, read_set=read_set, rebased=rebased)
    for i, op in enumerate(doc["operations"]):
        HANDLERS[op["operation"]](ctx, i, op)
    tx.fault("after_apply")
    tx.event("PROPOSAL_IMPORTED", {"proposal_id": pid, "proposal_sha256": sha, "snapshot_id": base["snapshot_id"],
                                   "base_state_revision": base["state_revision"], "rebased": rebased,
                                   "created": ctx.created_ids, "revised": ctx.revised,
                                   "invalidated": [[x["object_id"], x["revision"]] for x in ctx.invalidated]})
    receipt = _receipt(
        store, Decision.ACCEPTED, doc=doc, sha=sha, actor_id=tx.actor_id, dry_run=dry_run,
        new_state_revision=tx.revision, rebased=rebased, applied_on_state_revision=tx.base_revision,
        created_objects=ctx.created_ids, revised_objects=ctx.revised, created_edges=ctx.edge_count,
        invalidated_objects=ctx.invalidated)
    if not dry_run:
        artifact = store.artifacts.put_bytes(raw)  # stage -> atomic publish; metadata is committed after the file exists
        tx.fault("after_blob")
        tx.finalize()
        receipt["state_sha256"] = tx.state_sha256
        conn.execute("INSERT INTO proposals(proposal_id,content_sha256,state_revision,actor_id,at,artifact_sha256,receipt)"
                     " VALUES (?,?,?,?,?,?,?)",
                     (pid, sha, tx.revision, tx.actor_id, tx.at, artifact, json.dumps(receipt, sort_keys=True)))
        tx.bump = False  # already finalized; write_tx must not finalize twice
    return receipt


def import_proposal(store: StateStore, raw: bytes | str, token: Optional[str], *, dry_run: bool = False) -> dict:
    """Validate and import one proposal. Always returns a machine-readable receipt."""
    raw_b = raw.encode("utf-8") if isinstance(raw, str) else raw
    sha = sha256_bytes(raw_b)
    with store.read() as conn:
        actor = authenticate(conn, token)
    if actor is None:
        rec = _receipt(store, Decision.UNAUTHORIZED, doc=None, sha=sha, actor_id=None, rule="AUTHENTICATION_FAILED",
                       message="missing, unknown or disabled actor token", dry_run=dry_run)
        store.audit(actor_id=None, action="proposal_import", decision=rec["decision"], detail={"sha256": sha, "rule": rec["rule"]})
        return rec
    doc = None
    try:
        doc = static_check(raw_b)
        if not actor.has_role("importer", "owner"):
            reject("ROLE_IMPORTER_REQUIRED", f"actor {actor.actor_id} lacks role importer", Decision.UNAUTHORIZED)
        if doc["project_id"] != store.project_id:
            reject("PROJECT_MISMATCH", "proposal targets a different project", expected=store.project_id,
                   actual=doc["project_id"])
        with store.write_tx(actor.actor_id, require_roles=("importer", "owner"), commit=not dry_run) as tx:
            rec = _apply(store, tx, doc, raw_b, sha, dry_run)
    except AuthorizationError as e:  # actor disabled / role revoked between authentication and the transaction
        rec = _from_rejection(store, ProposalRejection(Decision.UNAUTHORIZED, e.code, e.message), doc, sha,
                              actor.actor_id, dry_run)
    except ProposalRejection as r:
        rec = _from_rejection(store, r, doc, sha, actor.actor_id, dry_run)
    except sqlite3.IntegrityError as e:
        rec = _from_rejection(store, ProposalRejection(Decision.REJECTED, "STATE_CONSTRAINT_VIOLATION", str(e)),
                              doc, sha, actor.actor_id, dry_run)
    except sqlite3.OperationalError as e:
        raise HarnessError("STATE_DB_UNAVAILABLE", str(e)) from e
    store.audit(actor_id=actor.actor_id, action="proposal_import" + ("_dry_run" if dry_run else ""),
                decision=rec["decision"], proposal_id=rec.get("proposal_id"),
                detail={"sha256": sha, "rule": rec.get("rule"), "new_state_revision": rec.get("new_state_revision")})
    return rec
