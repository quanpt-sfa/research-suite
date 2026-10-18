"""Proposal operations. Each handler runs inside the import transaction and either applies its
changes or raises ``ProposalRejection`` (which rolls back the whole proposal).

Agents may *propose* ``propose_object``, ``propose_claim`` and ``revise_object`` only. Nothing in
this module can set approval, verification or release fields: those exist solely in
``validation/review.py`` behind authenticated, role-checked CLI calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from research_harness.core.canonical import canonical_json_hash
from research_harness.core.errors import ProposalRejection
from research_harness.core.model import CLAIM_TIERS, Decision, Lifecycle, OriginKind, SERVER_ONLY_TYPES
from research_harness.graph.dependency import invalidate_dependents
from research_harness.graph.relations import edge_type_error
from research_harness.protocols.schema import content_schema, schema_findings
from research_harness.provenance.formation import build_formation
from research_harness.state.store import Tx, revision_digest

CLAIM_ONLY_FIELDS = ("formation_basis", "observed_results_at_formation", "suggested_status")


@dataclass
class Ctx:
    tx: Tx
    doc: dict
    proposal_sha256: str
    snapshot: dict
    read_set: dict
    rebased: bool
    created: dict = field(default_factory=dict)       # object_id -> (revision, type)
    created_ids: list = field(default_factory=list)
    revised: list = field(default_factory=list)
    invalidated: list = field(default_factory=list)
    edge_count: int = 0


def reject(rule: str, message: str = "", decision: Decision = Decision.REJECTED, **details: Any):
    raise ProposalRejection(decision, rule, message, **details)


def _origin(ctx: Ctx, op_name: str, index: int) -> dict:
    return {"kind": OriginKind.PROPOSAL_IMPORTED.value, "proposal_id": ctx.doc["proposal_id"],
            "proposal_sha256": ctx.proposal_sha256, "snapshot_id": ctx.doc["base"]["snapshot_id"],
            "base_state_revision": ctx.doc["base"]["state_revision"], "rebased": ctx.rebased,
            "declared_origin_channel": ctx.doc["origin_channel"],  # untrusted label, never used for authorization
            "import_actor": ctx.tx.actor_id, "operation": op_name, "op_index": index}


def _edge_prov(ctx: Ctx) -> dict:
    return {"kind": OriginKind.PROPOSAL_IMPORTED.value, "proposal_id": ctx.doc["proposal_id"],
            "import_actor": ctx.tx.actor_id}


def _scope(ctx: Ctx, op_name: str, object_type: str) -> None:
    if object_type not in ctx.snapshot["permitted_operations"].get(op_name, []):
        reject("OPERATION_OUTSIDE_SNAPSHOT_SCOPE",
               f"{op_name} on {object_type} is not permitted by snapshot scope {ctx.snapshot['scope']!r}",
               Decision.UNAUTHORIZED, operation=op_name, object_type=object_type, scope=ctx.snapshot["scope"])


def resolve_ref(ctx: Ctx, object_id: str) -> tuple[int, str]:
    """A referenced object must be created earlier in this proposal, or be in the read set at its head."""
    if object_id in ctx.created:
        return ctx.created[object_id]
    head = ctx.tx.head(object_id)
    if head is None:
        reject("REFERENCE_UNKNOWN", f"object {object_id} does not exist", object_id=object_id)
    if object_id not in ctx.read_set:
        reject("REFERENCE_NOT_IN_READ_SET", f"{object_id} is referenced but was not declared in read_set",
               object_id=object_id)
    if head["lifecycle_status"] != Lifecycle.ACTIVE.value:
        reject("REFERENCE_WITHDRAWN", f"{object_id} is withdrawn", object_id=object_id)
    return head["head_revision"], head["object_type"]


def _edges(ctx: Ctx, source_id: str, source_type: str, relations: list, depends_on: list) -> list[tuple]:
    items = [("depends_on", d) for d in depends_on] + [(r["relation"], r["target_id"]) for r in relations]
    seen, edges = set(), []
    for rel, tid in items:
        if tid == source_id:
            reject("SELF_DEPENDENCY", f"{source_id} cannot relate to itself", object_id=source_id)
        if (rel, tid) in seen:
            continue
        seen.add((rel, tid))
        trev, ttype = resolve_ref(ctx, tid)
        err = edge_type_error(rel, source_type, ttype)
        if err:
            reject("EDGE_TYPE_INVALID", err, source_id=source_id, relation=rel, target_id=tid)
        edges.append((rel, tid, trev, _edge_prov(ctx)))
    ctx.edge_count += len(edges)
    return edges


def _validate_content(object_type: str, content: dict) -> None:
    findings = schema_findings(content_schema(object_type), content, prefix="/content")
    if findings:
        reject("CONTENT_SCHEMA_VIOLATION", f"content invalid for {object_type}", Decision.INVALID_SCHEMA,
               findings=findings)


def _claim_observed(ctx: Ctx, op: dict) -> None:
    for rid in op.get("observed_results_at_formation", []):
        h = ctx.tx.head(rid)
        if h is None or h["object_type"] != "Result":
            reject("OBSERVED_RESULT_INVALID", f"{rid} is not a Result in this project", object_id=rid)


def _new_id_free(ctx: Ctx, object_id: str) -> None:
    head = ctx.tx.head(object_id)
    if head is not None or object_id in ctx.created:
        reject("ID_ALREADY_EXISTS", f"{object_id} already exists", Decision.CONFLICT,
               conflicts=[{"object_id": object_id, "expected_revision": None,
                           "actual_revision": head["head_revision"] if head else 1}])


def op_propose_object(ctx: Ctx, i: int, op: dict) -> None:
    oid, otype = op["proposed_id"], op["object_type"]
    if otype in SERVER_ONLY_TYPES:
        reject("SERVER_ONLY_OBJECT_TYPE", f"object_type {otype!r} cannot be proposed", Decision.REJECTED, object_type=otype)
    if otype == "Claim":
        reject("USE_PROPOSE_CLAIM", "claims must be proposed with propose_claim so formation provenance is recorded")
    _scope(ctx, "propose_object", otype)
    _new_id_free(ctx, oid)
    _validate_content(otype, op["content"])
    if otype == "Adjudication":
        subj = op["content"]["subject"]
        rev, _ = resolve_ref(ctx, subj["object_id"])
        if rev != subj["revision"]:
            reject("ADJUDICATION_SUBJECT_REVISION_MISMATCH", "subject revision is not the current head",
                   decision=Decision.CONFLICT, conflicts=[{"object_id": subj["object_id"],
                                                          "expected_revision": subj["revision"], "actual_revision": rev}])
    edges = _edges(ctx, oid, otype, op.get("relations", []), [])
    res = ctx.tx.add_revision(object_id=oid, object_type=otype, content=op["content"],
                              origin=_origin(ctx, "propose_object", i), edges=edges)
    ctx.created[oid] = (1, otype)
    ctx.created_ids.append(oid)


def op_propose_claim(ctx: Ctx, i: int, op: dict) -> None:
    oid = op["proposed_id"]
    _scope(ctx, "propose_claim", "Claim")
    _new_id_free(ctx, oid)
    content = {k: op[k] for k in ("text", "claim_tier", "label", "description", "sensitivity", "attributes") if k in op}
    _validate_content("Claim", content)
    _claim_observed(ctx, op)
    edges = _edges(ctx, oid, "Claim", op.get("relations", []), op["depends_on"])
    origin = _origin(ctx, "propose_claim", i)
    origin["formation"] = build_formation(conn=ctx.tx.conn, at=ctx.tx.at, state_revision=ctx.tx.revision,
                                          claim_text=content["text"], claim_tier=content["claim_tier"], op=op,
                                          previous=None, revision=1)
    ctx.tx.add_revision(object_id=oid, object_type="Claim", content=content, origin=origin, edges=edges)
    ctx.created[oid] = (1, "Claim")
    ctx.created_ids.append(oid)


def op_revise_object(ctx: Ctx, i: int, op: dict) -> None:
    oid, base_rev = op["object_id"], op["base_revision"]
    head = ctx.tx.head(oid)
    if head is None:
        reject("OBJECT_UNKNOWN", f"{oid} does not exist", object_id=oid)
    if oid not in ctx.read_set:
        reject("WRITE_WITHOUT_READ", f"{oid} is revised but not declared in read_set", object_id=oid)
    if ctx.read_set[oid] != base_rev:
        reject("BASE_REVISION_INCONSISTENT_WITH_READ_SET", "base_revision must equal read_set revision",
               object_id=oid, base_revision=base_rev, read_set_revision=ctx.read_set[oid])
    if head["head_revision"] != base_rev:  # normally pre-empted by the read-set conflict pass
        reject("REVISION_CONFLICT", f"{oid} changed", Decision.CONFLICT, conflicts=[
            {"object_id": oid, "expected_revision": base_rev, "actual_revision": head["head_revision"]}])
    if head["lifecycle_status"] != Lifecycle.ACTIVE.value:
        reject("OBJECT_WITHDRAWN", f"{oid} is withdrawn", object_id=oid)
    otype = head["object_type"]
    if otype in SERVER_ONLY_TYPES:
        reject("SERVER_ONLY_OBJECT_TYPE", f"object_type {otype!r} cannot be revised via proposal", Decision.REJECTED, object_type=otype)
    _scope(ctx, "revise_object", otype)
    prev = ctx.tx.get(oid)
    content = op["content"]
    _validate_content(otype, content)
    is_claim = otype == "Claim"
    if not is_claim and any(k in op for k in CLAIM_ONLY_FIELDS):
        reject("CLAIM_FIELD_ON_NON_CLAIM", "formation fields apply to Claim only", object_id=oid)
    if is_claim and content["claim_tier"] not in CLAIM_TIERS and prev["content"]["claim_tier"] != content["claim_tier"]:
        reject("CLAIM_TIER_NOT_ALLOWED", "tier 'unclassified' may only be retained, not newly assigned", object_id=oid)
    if is_claim:
        _claim_observed(ctx, op)

    if "relations" in op or "depends_on" in op:
        edges = _edges(ctx, oid, otype, op.get("relations", []), op.get("depends_on", []))
    else:  # carry over exactly the previous outgoing edges (still pinned to their old revisions)
        edges = [(e["relation"], e["target_id"], e["target_revision"], _edge_prov(ctx))
                 for e in ctx.tx.store.edges_from(ctx.tx.conn, oid, base_rev)]
    if (canonical_json_hash(content) == prev["content_digest"]
            and revision_digest(prev["content_digest"], [(e[0], e[1], e[2]) for e in edges]) == prev["revision_digest"]):
        reject("NO_CHANGE", "revision would be identical to the current one", object_id=oid)

    origin = _origin(ctx, "revise_object", i)
    if is_claim:
        origin["formation"] = build_formation(
            conn=ctx.tx.conn, at=ctx.tx.at, state_revision=ctx.tx.revision, claim_text=content["text"],
            claim_tier=content["claim_tier"], op=op, previous=prev["origin"].get("formation"), revision=base_rev + 1)
    new = ctx.tx.add_revision(object_id=oid, object_type=otype, content=content, origin=origin, edges=edges)
    ctx.revised.append({"object_id": oid, "from_revision": base_rev, "to_revision": new["revision"]})
    ctx.invalidated.extend(invalidate_dependents(ctx.tx, oid, new["revision"]))


HANDLERS = {"propose_object": op_propose_object, "propose_claim": op_propose_claim,
            "revise_object": op_revise_object}
