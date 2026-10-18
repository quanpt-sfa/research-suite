"""Privileged review operations. These are the ONLY code paths that create Approval objects or move
an object to AUTHOR_APPROVED / INDEPENDENTLY_REVIEWED / REJECTED.

They are reachable only through authenticated, role-checked calls (CLI with an actor token), never
through the proposal gateway. The acting identity is read from the token registry, never from any
document. Independence is a policy over *recorded facts* (distinct authenticated actor, role,
owner-attested independence group not shared with any contributor); it is not a claim of
real-world independence, which the engine cannot observe (see docs/THREAT_MODEL.md).

Authorization is re-verified inside the write transaction (``write_tx(..., require_roles=...)``): the
token is authenticated up front only to produce a precise refusal, but the identity and roles that
authorize the mutation are the ones stored *at commit time*, so an actor disabled or demoted in between
cannot record a review. The audit row is written in the same transaction as the Approval.
"""
from __future__ import annotations

from typing import Optional

from research_harness.core.auth import Actor, authenticate
from research_harness.core.errors import AuthorizationError
from research_harness.core.model import Lifecycle, OriginKind, ReviewStatus
from research_harness.graph.dependency import stale_pinned_edges
from research_harness.state.store import StateStore

KINDS = ("author_approval", "independent_review")
DECISIONS = ("approve", "reject")


def contributors(conn, object_id: str) -> set[str]:
    """Authenticated actors who authored any revision of the object or recorded a decision on it."""
    out = {r[0] for r in conn.execute("SELECT DISTINCT submitted_by FROM objects WHERE object_id=?", (object_id,))}
    out |= {r[0] for r in conn.execute(
        "SELECT DISTINCT submitted_by FROM objects WHERE object_type='Approval' "
        "AND json_extract(content,'$.subject.object_id')=?", (object_id,))}
    return out


def independence_findings(conn, actor: Actor, object_id: str) -> list[str]:
    findings = []
    if not actor.has_role("independent_reviewer"):
        findings.append("REVIEWER_ROLE_MISSING")
    if not actor.independence_group:
        findings.append("INDEPENDENCE_EVIDENCE_MISSING")
    lineage = contributors(conn, object_id)
    if actor.actor_id in lineage:
        findings.append("REVIEWER_IS_CONTRIBUTOR")
    groups = {}
    for a in lineage:
        row = conn.execute("SELECT independence_group FROM actors WHERE actor_id=?", (a,)).fetchone()
        groups[a] = row[0] if row else None
    if any(g is None for g in groups.values()):
        findings.append("INDEPENDENCE_EVIDENCE_MISSING_FOR_CONTRIBUTOR")
    if actor.independence_group and actor.independence_group in {g for g in groups.values() if g}:
        findings.append("REVIEWER_SHARES_GROUP_WITH_CONTRIBUTOR")
    return sorted(set(findings))


class _Refused(Exception):
    def __init__(self, rule: str, message: str, **details):
        super().__init__(rule)
        self.rule, self.message, self.details = rule, message, details


def _refuse(store: StateStore, actor: Optional[Actor], rule: str, message: str, status: str = "REFUSED", **details):
    store.audit(actor_id=actor.actor_id if actor else None, action="review_record", decision=status,
                detail={"rule": rule, **{k: v for k, v in details.items() if k != "message"}})
    return {"decision": status, "rule": rule, "message": message, "state_changed": False, **details}


def record_review(store: StateStore, token: Optional[str], *, object_id: str, revision: int, kind: str,
                  decision: str, rationale: str = "", acknowledge_invalidations: bool = False) -> dict:
    with store.read() as conn:
        actor = authenticate(conn, token)
    if actor is None:
        return _refuse(store, None, "AUTHENTICATION_FAILED", "missing, unknown or disabled actor token", "UNAUTHORIZED")
    if kind not in KINDS or decision not in DECISIONS:
        return _refuse(store, actor, "INVALID_REVIEW_PARAMETERS", f"kind in {KINDS}, decision in {DECISIONS}")
    needed = ("author", "owner") if kind == "author_approval" else ("independent_reviewer",)
    if not actor.has_role(*needed):
        return _refuse(store, actor, "ROLE_REQUIRED", f"{kind} requires role {' or '.join(needed)}", "UNAUTHORIZED")
    try:
        return _record(store, actor, needed, object_id, revision, kind, decision, rationale, acknowledge_invalidations)
    except AuthorizationError as e:  # identity or role changed between the pre-check and the transaction
        return _refuse(store, actor, e.code, e.message or "authorization failed", "UNAUTHORIZED")
    except _Refused as r:
        return _refuse(store, actor, r.rule, r.message, **r.details)


def _record(store: StateStore, pre_actor: Actor, needed: tuple, object_id: str, revision: int, kind: str,
            decision: str, rationale: str, acknowledge_invalidations: bool) -> dict:
    with store.write_tx(pre_actor.actor_id, require_roles=needed) as tx:
        actor = tx.actor  # identity/roles as stored inside this transaction
        conn = tx.conn
        head = tx.head(object_id)
        if head is None:
            raise _Refused("OBJECT_UNKNOWN", f"{object_id} does not exist")
        if head["object_type"] == "Approval":
            raise _Refused("CANNOT_REVIEW_APPROVAL", "approvals are not themselves reviewed")
        if head["head_revision"] != revision:
            raise _Refused("REVISION_NOT_HEAD", "only the head revision can be reviewed",
                           expected=revision, actual=head["head_revision"])
        if head["lifecycle_status"] != Lifecycle.ACTIVE.value:
            raise _Refused("OBJECT_WITHDRAWN", f"{object_id} is withdrawn")
        open_inv = [dict(r) for r in conn.execute(
            "SELECT inval_id,cause_object_id,cause_from_revision,cause_to_revision,via_relation,hops FROM invalidations "
            "WHERE object_id=? AND revision=? AND status='OPEN' ORDER BY inval_id", (object_id, revision))]
        pins = [f"{p['relation']}:{p['pinned']}->head@{p['current_head']}" for p in stale_pinned_edges(conn, object_id, revision)]
        if decision == "approve" and (open_inv or pins) and not acknowledge_invalidations:
            raise _Refused("OPEN_INVALIDATIONS_UNACKNOWLEDGED",
                           "dependencies changed since this revision; re-check them and pass "
                           "--acknowledge-invalidations, or revise the object against the current dependencies",
                           open_invalidations=open_inv, stale_pins=pins)
        if kind == "independent_review":
            problems = independence_findings(conn, actor, object_id)
            if problems:
                raise _Refused("INDEPENDENCE_POLICY_NOT_MET",
                               "recorded facts do not establish an independent reviewer", problems=problems)
        n = conn.execute("SELECT COUNT(*) FROM object_heads WHERE object_type='Approval'").fetchone()[0] + 1
        aid = f"APR_{n:06d}"
        subject = tx.get(object_id, revision)
        content = {"subject": {"object_id": object_id, "revision": revision}, "kind": kind, "decision": decision,
                   "rationale": rationale[:20000], "actor_id": actor.actor_id,
                   "actor_roles": sorted(actor.roles), "independence_group": actor.independence_group,
                   "acknowledged_invalidations": [i["inval_id"] for i in open_inv] if acknowledge_invalidations else [],
                   "acknowledged_stale_pins": pins if acknowledge_invalidations else [],
                   "subject_revision_digest": subject["revision_digest"],
                   "state_sha256_at_review": conn.execute(
                       "SELECT state_sha256 FROM state_revisions WHERE revision=?", (tx.base_revision,)).fetchone()[0]}
        tx.add_revision(object_id=aid, object_type="Approval", content=content, edges=[],
                        origin={"kind": OriginKind.SYSTEM_DERIVED.value, "operation": "record_review",
                                "authenticated_actor": actor.actor_id})
        if acknowledge_invalidations and open_inv:
            conn.execute("UPDATE invalidations SET status='RESOLVED', resolved_state_revision=?, resolved_by=?, "
                         "resolution=? WHERE object_id=? AND revision=? AND status='OPEN'",
                         (tx.revision, actor.actor_id, f"{aid}: {rationale[:500]}", object_id, revision))
        if decision == "reject":
            new = ReviewStatus.REJECTED
        elif kind == "author_approval":
            new = ReviewStatus.AUTHOR_APPROVED
        else:
            new = ReviewStatus.INDEPENDENTLY_REVIEWED
        tx.set_review(object_id, revision, new, {"kind": "REVIEW_RECORDED", "approval_id": aid, "review_kind": kind,
                                                 "decision": decision, "actor_id": actor.actor_id})
        tx.event("REVIEW_RECORDED", {"approval_id": aid, "object_id": object_id, "revision": revision, "kind": kind,
                                     "decision": decision, "new_status": new.value})
        result = {"decision": "RECORDED", "approval_id": aid, "object_id": object_id, "revision": revision,
                  "kind": kind, "review_decision": decision, "new_review_status": new.value,
                  "new_state_revision": tx.revision, "actor": actor.actor_id,
                  "resolved_invalidations": [i["inval_id"] for i in open_inv] if acknowledge_invalidations else []}
        tx.audit("review_record", "RECORDED", result)
    return result
