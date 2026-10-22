"""Relations between claims, and alignments of claims across the versions of one work (M5 WP6.2).

A relation (SUPPORTS, CHALLENGES, QUALIFIES, DEFINES, ASSUMES, DEPENDS_ON, EXTENDS, RECONCEPTUALIZES, CONTRADICTS_WITHIN_SCOPE, COMPLEMENTS, SAME_IDEA_PROPOSED) is a **proposal**: a model's,
the researcher's or a critic's. Every proposal goes through the deterministic gate below before it is stored, is `PROPOSED` and unconfirmed until the researcher decides, keeps its
rationale and what is missing, and is flagged `needs_reassessment` when something it rests on changes. None is derived from similarity of words, and `SAME_IDEA_PROPOSED` needs a stated
comparison of *meaning*. A finder lists claims worth reading (`claims.candidates`) and writes nothing.

An alignment says how a claim of one version of a work relates to the claim of another version: `UNCHANGED, QUALIFIED, NARROWED, WEAKENED, STRENGTHENED, REVERSED`, or, resting on an absence,
`WITHDRAWN` and `NEW_IN_LATER_VERSION`. It too is a proposal. Claims stay at their own Source@revision: nothing is copied from a preprint to its journal version, and a claim of one version
is never said to be the claim of the other until the researcher decides, and then only in this record.

The review records, the `acknowledge_missing` rule and the confirmation states are those of `discovery/relations.py` (a relation between studies in a Landscape).
"""
from __future__ import annotations

import json
from typing import Any, Optional

from jsonschema import Draft202012Validator

from research_harness import taxonomy
from research_harness.core.canonical import canonical_json_hash
from research_harness.discovery import relations as REL
from research_harness.discovery import text_index as X
from research_harness.knowledge import claims as CL
from research_harness.knowledge import corpus
from research_harness.knowledge.common import KnowledgeError
from research_harness.state import workflow_runs
from research_harness.state.store import StateStore
from research_harness.workflows.adapters import ModelError, adapter_identity
from research_harness.workflows.model_io import parse_model_output
from research_harness.workflows.status import Applicability, AssessmentStatus, ContextStatus, ExecutionStatus

PASSAGE_TARGET_KINDS = ("SUPPORTS", "CHALLENGES", "QUALIFIES")
SYMMETRIC = ("COMPLEMENTS", "CONTRADICTS_WITHIN_SCOPE", "SAME_IDEA_PROPOSED")
ABSENCE_BASED = ("WITHDRAWN", "NEW_IN_LATER_VERSION")
PAIR_CHANGES = ("UNCHANGED", "QUALIFIED", "NARROWED", "WEAKENED", "STRENGTHENED", "REVERSED")
DERIVATIONS = {"model_proposed": "model", "researcher_proposed": "researcher", "critic_proposed": "critic"}

RELATION_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "claim_relation_proposals", "type": "object",
    "properties": {"proposals": {"type": "array", "items": {
        "type": "object",
        "properties": {"kind": {"type": "string"}, "from": {"type": "string"}, "to": {"type": ["string", "object"]}, "rationale": {"type": "string"},
                       "missing_information": {"type": "array", "items": {"type": "string"}}, "levels": {"type": "object", "additionalProperties": {"type": "string"}},
                       "compatibility": {"type": "string"}, "scope_note": {"type": "string"}, "meaning_comparison": {"type": "string"}, "shared_scope": {"type": "string"}},
        "required": ["kind", "from", "to"]}}},
    "required": ["proposals"], "additionalProperties": True}
ALIGNMENT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "claim_alignment_proposals", "type": "object",
    "properties": {"proposals": {"type": "array", "items": {
        "type": "object",
        "properties": {"change": {"type": "string"}, "earlier": {"type": ["string", "null"]}, "later": {"type": ["string", "null"]}, "earlier_source": {"type": "string"}, "later_source": {"type": "string"},
                       "what_changed": {"type": "string"}, "conditions_added": {"type": "array", "items": {"type": "string"}}, "rationale": {"type": "string"},
                       "missing_information": {"type": "array", "items": {"type": "string"}}},
        "required": ["change"]}}},
    "required": ["proposals"], "additionalProperties": True}

RELATION_PROMPT = (
    "You help a researcher see how the claims of the papers in an inventory relate. You do not decide who is right and you state nothing as established: every item is a proposal the "
    "researcher will decide on. Work only from the claims shown (each has an id, who holds it, its role, the source and a quotation).\n\n"
    "Kinds: SUPPORTS (the first claim is offered for the second), CHALLENGES, QUALIFIES (adds a condition or limit), DEFINES (the first is a DEFINITION the second uses), ASSUMES (the second is an "
    "ASSUMPTION the first rests on), DEPENDS_ON, EXTENDS, RECONCEPTUALIZES, CONTRADICTS_WITHIN_SCOPE (only if you state the scope both share in `shared_scope`; give the level of analysis of "
    "each claim in `levels`: if the levels differ they do not contradict), COMPLEMENTS, SAME_IDEA_PROPOSED (two papers state the same idea in different words: say in `meaning_comparison` how the "
    "MEANINGS match, not only the words; never because the wording is similar). A claim that only REPORTS earlier work is that paper's report, not the cited authors' claim. Claims of two "
    "versions of one work are not related here.\n\nFor each relation give `kind`, `from` and `to` (claim ids), `rationale`, and `missing_information` (what you would need to know to be sure; an "
    "empty list only if nothing is missing). Respond with exactly one JSON object and nothing else. It must validate against this JSON Schema:\n" + json.dumps(RELATION_SCHEMA, indent=2, sort_keys=True))
ALIGNMENT_PROMPT = (
    "You help a researcher see how the claims of two versions of one work differ. Each claim stays in its own version; you do not say the versions are the same work's claims, you only propose "
    "how a claim of the earlier version corresponds to a claim of the later one: change is one of " + ", ".join(taxonomy.vocabulary("claim_version_change")) + ". UNCHANGED means the same "
    "proposition; QUALIFIED means conditions were added (list them in `conditions_added`); NARROWED, WEAKENED, STRENGTHENED, REVERSED as the words say. WITHDRAWN (a claim of the earlier version "
    "with no counterpart in the later) and NEW_IN_LATER_VERSION rest on an absence: the later extraction may simply have missed it, so give `missing_information`. Give `earlier` and `later` "
    "(claim ids), `what_changed`, `rationale`.\n\nRespond with exactly one JSON object and nothing else. It must validate against this JSON Schema:\n" + json.dumps(ALIGNMENT_SCHEMA, indent=2, sort_keys=True))
SKILL_DIGEST = canonical_json_hash([RELATION_PROMPT, ALIGNMENT_PROMPT])


def _txt(p: dict[str, Any], key: str, what: Optional[str] = None) -> str:
    v = p.get(key)
    if not isinstance(v, str) or not v.strip():
        raise KnowledgeError("FIELD_REQUIRED", f"{what or key} is required and must say something", field=key)
    return v.strip()


def _state(c: dict[str, Any]) -> str:
    """What the relation was about, as the researcher's decisions leave it: a later correction to any of it makes the relation need a second look."""
    e = CL._effective(c)
    return canonical_json_hash([e["attribution"].get("value"), e["role"], e["text"], e["status"] == "REJECTED_BY_RESEARCHER"])


def _end(c: dict[str, Any]) -> dict[str, Any]:
    return {"claim": c["id"], "source_id": c.get("source_id"), "source_revision": c.get("source_revision"), "work_id": c.get("work_id"), "version_id": c.get("version_id"), "role": c["role"],
            "passage_id": (c.get("wording") or {}).get("passage_id"), "passage_revision": (c.get("wording") or {}).get("passage_revision")}


def _find(content: dict[str, Any], cid: Any) -> dict[str, Any]:
    if not isinstance(cid, str):
        raise KnowledgeError("CLAIM_UNKNOWN", f"not a claim id: {cid!r}")
    c = next((e for e in content["elements"] if e["id"] == cid), None)
    if c is None:
        raise KnowledgeError("CLAIM_UNKNOWN", f"no claim {cid} in this inventory")
    if CL._effective(c)["status"] == "REJECTED_BY_RESEARCHER":
        raise KnowledgeError("CLAIM_REJECTED", f"{cid} was rejected by the researcher and cannot be related to anything")
    return c


def validate_relation(store: StateStore, content: dict[str, Any], p: dict[str, Any], *, derivation: str) -> dict[str, Any]:
    """The checked, normalised relation, or a KnowledgeError whose code says what was missing. Reads only the inventory (and the passage a target names)."""
    kind = p.get("kind")
    if kind not in taxonomy.vocabulary("claim_relation_kind"):
        raise KnowledgeError("PROPOSAL_KIND_NOT_ALLOWED", f"{kind!r} is not a relation kind ({taxonomy.vocabulary('claim_relation_kind')})")
    a = _find(content, p.get("from"))
    to = p.get("to")
    target_passage = None
    if isinstance(to, dict) and ("passage" in to or "passage_id" in to):
        if kind not in PASSAGE_TARGET_KINDS:
            raise KnowledgeError("TARGET_NOT_ALLOWED", f"{kind} relates claims; only {PASSAGE_TARGET_KINDS} may end at a passage")
        pid = to.get("passage_id") or (to["passage"][0] if isinstance(to.get("passage"), list) else to.get("passage"))
        prev = store.get_object(pid)
        if not prev or prev["object_type"] != "EvidencePassage":
            raise KnowledgeError("PASSAGE_UNKNOWN", f"{pid!r} is not an EvidencePassage in this project")
        target_passage = {"passage_id": pid, "passage_revision": prev["revision"], "source_id": prev["content"].get("source_id"), "source_revision": prev["content"].get("source_revision"),
                          "quote": prev["content"].get("quote")}
        b = None
    else:
        b = _find(content, to.get("claim") if isinstance(to, dict) else to)
        if a["id"] == b["id"]:
            raise KnowledgeError("SAME_CLAIM", "a claim is not related to itself")
        if a.get("work_id") and a.get("work_id") == b.get("work_id") and a.get("source_id") != b.get("source_id"):
            raise KnowledgeError("VERSIONS_OF_ONE_WORK", "these claims belong to two versions of one work: that is an alignment (claim_relations.add_alignment), not a relation between studies")
        if kind == "SAME_IDEA_PROPOSED" and (not a.get("work_id") or not b.get("work_id") or a["work_id"] == b["work_id"]):
            raise KnowledgeError("SAME_WORK", "SAME_IDEA_PROPOSED relates claims of two different works")
        if kind == "DEFINES" and a["role"] != "DEFINITION":
            raise KnowledgeError("FROM_NOT_A_DEFINITION", "DEFINES starts at a claim whose role is DEFINITION")
        if kind == "ASSUMES" and b["role"] != "ASSUMPTION":
            raise KnowledgeError("TO_NOT_AN_ASSUMPTION", "ASSUMES ends at a claim whose role is ASSUMPTION")
    rationale = _txt(p, "rationale")
    if derivation == "model_proposed" or "missing_information" in p:
        if not isinstance(p.get("missing_information"), list):
            raise KnowledgeError("FIELD_REQUIRED", "missing_information is required (a list; empty only if nothing is missing)", field="missing_information")
    levels = {k: str(v).strip() for k, v in (p.get("levels") or {}).items() if str(v).strip()}
    compat = p.get("compatibility") or ("NOT_ASSESSED" if kind != "CONTRADICTS_WITHIN_SCOPE" else None)
    out: dict[str, Any] = {"kind": kind, "from": _end(a), "to": _end(b) if b else {"passage": target_passage}, "rationale": rationale,
                           "missing_information": [str(m).strip() for m in p.get("missing_information") or [] if str(m).strip()], "meaning_comparison": None, "shared_scope": None,
                           "scope": {"levels": levels, "compatibility": compat, "note": (p.get("scope_note") or "").strip() or None}}
    if kind == "SAME_IDEA_PROPOSED":
        out["meaning_comparison"] = _txt(p, "meaning_comparison", "a comparison of meaning (how the meanings match, not the words)")
    if kind == "CONTRADICTS_WITHIN_SCOPE":
        out["shared_scope"] = _txt(p, "shared_scope", "the scope both claims share")
        if compat not in ("COMPATIBLE", None):
            raise KnowledgeError("SCOPES_DIFFER", "claims whose scopes differ do not contradict within a scope: propose QUALIFIES or COMPLEMENTS")
        out["scope"]["compatibility"] = "COMPATIBLE"
        if derivation == "model_proposed" and b is not None:
            if any(i not in levels for i in (a["id"], b["id"])):
                raise KnowledgeError("LEVELS_REQUIRED", "CONTRADICTS_WITHIN_SCOPE needs the level of analysis of both claims")
            if len({X_norm(levels[i]) for i in (a["id"], b["id"])}) > 1:
                raise KnowledgeError("LEVELS_DIFFER", "the claims are at different levels of analysis: they do not contradict; they may complement each other or meet at a boundary")
    if compat is not None and compat not in taxonomy.vocabulary("claim_scope_compatibility"):
        raise KnowledgeError("COMPATIBILITY_UNKNOWN", f"compatibility must be one of {taxonomy.vocabulary('claim_scope_compatibility')}")
    ends = {a["id"], b["id"] if b else target_passage["passage_id"]}
    for r in content["relations"]:
        rends = {r["from"]["claim"], r["to"].get("claim") or (r["to"].get("passage") or {}).get("passage_id")}
        if r["kind"] == kind and ((rends == ends) if kind in SYMMETRIC else ([r["from"]["claim"], r["to"].get("claim") or (r["to"].get("passage") or {}).get("passage_id")] == [a["id"], list(ends - {a["id"]})[0] if len(ends) == 2 else a["id"]])):
            raise KnowledgeError("DUPLICATE", f"{r['id']} already records this {kind}")
    out["claims_state"] = {a["id"]: _state(a), **({b["id"]: _state(b)} if b else {})}
    return out


def X_norm(s: str) -> str:
    return " ".join(str(s).casefold().split())


def _next_id(items: list[dict[str, Any]], prefix: str) -> str:
    return f"{prefix}{max((int(i['id'][1:]) for i in items), default=0) + 1:03d}"


def _record(norm: dict[str, Any], rid: str, *, derivation: str, provenance: str, generated_by: str, by: Optional[str], at: Optional[str], snapshot: str) -> dict[str, Any]:
    return {"id": rid, **norm, "derivation": derivation, "provenance": provenance, "generated_by": generated_by, "status": "PROPOSED", "reviews": [], "proposed_by": by, "proposed_at": at,
            "corpus_snapshot_at_proposal": snapshot}


def add_relation(store: StateStore, token: Optional[str], inventory_id: str, proposal: dict[str, Any], *, provenance: str = "researcher_proposed", critic: Optional[str] = None) -> dict[str, Any]:
    """The researcher's own proposal (or a critic's that the researcher records), through the same gate. PROPOSED and unconfirmed until decided."""
    if provenance not in REL.PROVENANCES:
        raise KnowledgeError("PROVENANCE_UNKNOWN", f"provenance must be one of {REL.PROVENANCES}")
    if provenance == "critic_proposed" and not (critic and critic.strip()):
        raise KnowledgeError("CRITIC_REQUIRED", "name the critic whose proposal this is")
    snapshot = corpus.corpus_snapshot_id(store)
    made: dict[str, str] = {}

    def build_(c: dict[str, Any], tx: Any) -> dict[str, Any]:
        norm = validate_relation(store, c, proposal, derivation=provenance)
        rec = _record(norm, _next_id(c["relations"], "R"), derivation=provenance, provenance=f"{provenance}:{critic.strip()}" if provenance == "critic_proposed" else provenance,
                      generated_by="researcher", by=tx.actor_id, at=tx.at, snapshot=snapshot)
        made["id"] = rec["id"]
        return {**c, "relations": [*c["relations"], rec]}

    out = CL._write_with(store, token, inventory_id, build_, "claims_add_relation")
    return {**out, "relation": made["id"]}


def _model_run(store: StateStore, token: Optional[str], inventory_id: str, adapter: Any, *, prompt: str, schema: dict[str, Any], user: str, workflow: str, fp_extra: Any,
               apply: Any) -> dict[str, Any]:
    cur = CL.load_inventory(store, inventory_id)
    model = adapter_identity(adapter)
    who = f"model:{model.get('model_id') or model.get('provider')}"
    fp = canonical_json_hash({"inventory": [inventory_id, cur["revision"], cur["revision_digest"]], "extra": fp_extra, "skill": SKILL_DIGEST, "model": model, "workflow": workflow})
    base = {"workflow": workflow, "input_fingerprint": fp, "executor": f"{workflow}@1", "applicability": Applicability.APPLICABLE.value, "model_descriptor": model, "skill_digest": SKILL_DIGEST,
            "input_revisions": [{"object_id": inventory_id, "revision": cur["revision"], "content_digest": cur["content_digest"]}]}

    def failed(reason: str, usage: dict) -> str:
        return workflow_runs.append_run(store, {**base, "execution_status": ExecutionStatus.FAILED.value, "context_status": ContextStatus.COMPLETE.value,
                                                "assessment_status": AssessmentStatus.NOT_ASSESSED.value, "legacy_status": "FAILED", "status_reason": reason, "usage": usage})

    try:
        raw, usage = adapter.generate(prompt, user, expected_schema=schema, temperature=0.0)
    except ModelError as exc:
        return {"status": "FAILED", "reason": type(exc).__name__, "message": str(exc), "run_id": failed(type(exc).__name__, {"llm_calls": 1}), "accepted": [], "rejected": []}
    use = {"llm_calls": 1, "input_tokens": getattr(usage, "input_tokens", None), "output_tokens": getattr(usage, "output_tokens", None), "usage_source": getattr(usage, "usage_source", None)}
    parsed, err = parse_model_output(raw)
    problems = [] if parsed is None else sorted(e.message[:120] for e in Draft202012Validator(schema).iter_errors(parsed))
    if parsed is None or problems:
        return {"status": "FAILED", "reason": "invalid_output", "message": err or "; ".join(problems[:3]), "run_id": failed("invalid_output", use), "accepted": [], "rejected": []}
    row = {**base, "execution_status": ExecutionStatus.SUCCEEDED.value, "context_status": ContextStatus.COMPLETE.value, "assessment_status": AssessmentStatus.PROVISIONAL.value,
           "legacy_status": "SUCCESS", "status_reason": None, "usage": use}
    snapshot = corpus.corpus_snapshot_id(store)
    accepted: list[str] = []
    rejected: list[dict[str, Any]] = []

    def build_(c: dict[str, Any], tx: Any) -> dict[str, Any]:
        return apply(c, tx, parsed["proposals"], who, snapshot, accepted, rejected)

    if parsed["proposals"]:
        try:
            with store.write_tx(token=token, require_roles=CL.ROLES) as tx:
                head = tx.head(inventory_id)
                curr = tx.get(inventory_id)
                content = build_(curr["content"], tx)
                if content != curr["content"]:
                    content = CL._summary(content)
                    res = tx.add_revision(object_id=inventory_id, object_type="ClaimInventory", content=content, edges=CL._edges_for(content), expected_head=head["head_revision"],
                                          origin=CL.engine_origin(workflow, tx.actor_id))
                    revision = res["revision"]
                else:
                    revision = curr["revision"]
                rid = workflow_runs.insert_run_tx(tx, row)
        except Exception as exc:
            try:
                failed(f"write_refused:{getattr(exc, 'code', type(exc).__name__)}", use)
            except Exception:  # noqa: BLE001
                pass
            raise
    else:
        rid, revision = workflow_runs.append_run(store, row), cur["revision"]
    return {"status": "PROPOSED", "inventory_id": inventory_id, "revision": revision, "accepted": accepted, "rejected": rejected, "run_id": rid, "model": who}


def _claims_prompt(content: dict[str, Any]) -> str:
    from research_harness.workflows import quotes
    lines = []
    for e in content["elements"]:
        eff = CL._effective(e)
        if eff["status"] == "REJECTED_BY_RESEARCHER":
            continue
        q = quotes.normalize_whitespace((e.get("wording") or {}).get("quote") or "")[:300]
        lines.append(f"{e['id']} [{e['role']}] held: {eff['attribution'].get('value')} | {e.get('source_id')}@{e.get('source_revision')} (work {e.get('work_id')}"
                     f"{', version ' + str(e['version_id']) if e.get('version_id') else ''})\n   says: {eff['text']}" + (f"\n   quote: \"{q}\"" if q else ""))
    return "CLAIMS OF THE INVENTORY:\n" + "\n".join(lines)


def propose_relations(store: StateStore, token: Optional[str], inventory_id: str, adapter: Any, *, kinds: Optional[list[str]] = None) -> dict[str, Any]:
    """One model call: relations between the claims. Each goes through the gate before it is stored, as PROPOSED and UNCONFIRMED with the provenance `suite_proposed:model:<id>`; rejected
    items are listed with the reason. A model proposal never confirms itself and never says a claim is new."""
    bad = [k for k in kinds or [] if k not in taxonomy.vocabulary("claim_relation_kind")]
    if bad:
        raise KnowledgeError("PROPOSAL_KIND_NOT_ALLOWED", f"{bad} are not relation kinds")
    c0 = CL.load_inventory(store, inventory_id)["content"]
    user = _claims_prompt(c0) + (f"\n\nPropose only these kinds: {', '.join(kinds)}." if kinds else "")

    def apply(c: dict[str, Any], tx: Any, proposals: list[dict[str, Any]], who: str, snapshot: str, accepted: list[str], rejected: list[dict[str, Any]]) -> dict[str, Any]:
        rels = list(c["relations"])
        for p in proposals:
            try:
                norm = validate_relation(store, {**c, "relations": rels}, p, derivation="model_proposed")
            except KnowledgeError as exc:
                rejected.append({"proposal": {k: p.get(k) for k in ("kind", "from", "to")}, "code": exc.code, "reason": exc.message})
                continue
            rec = _record(norm, _next_id(rels, "R"), derivation="model_proposed", provenance=f"suite_proposed:{who}", generated_by=who, by=who, at=tx.at, snapshot=snapshot)
            rels.append(rec)
            accepted.append(rec["id"])
        return {**c, "relations": rels}

    return _model_run(store, token, inventory_id, adapter, prompt=RELATION_PROMPT, schema=RELATION_SCHEMA, user=user, workflow="claim_relations", fp_extra=kinds, apply=apply)


def review_relation(store: StateStore, token: Optional[str], inventory_id: str, relation_id: str, decision: str, reason: str, *, acknowledge_missing: bool = False) -> dict[str, Any]:
    """The researcher's decision on a relation or an alignment: CONFIRMED, DISMISSED or NEEDS_SCOPE, with who and when. The record keeps what it is (a model's, the researcher's):
    confirming it does not change its provenance."""
    return _review(store, token, inventory_id, "relations", relation_id, decision, reason, acknowledge_missing=acknowledge_missing, op="claims_review_relation")


def _review(store: StateStore, token: Optional[str], inventory_id: str, key: str, item_id: str, decision: str, reason: str, *, acknowledge_missing: bool, op: str) -> dict[str, Any]:
    if decision not in ("CONFIRMED", "DISMISSED", "NEEDS_SCOPE"):
        raise KnowledgeError("DECISION_UNKNOWN", "decision must be CONFIRMED, DISMISSED or NEEDS_SCOPE")
    reason = CL._need_text(reason, "your reason")

    def build_(c: dict[str, Any], tx: Any) -> dict[str, Any]:
        item = next((i for i in c[key] if i["id"] == item_id), None)
        if item is None:
            raise KnowledgeError("ITEM_UNKNOWN", f"{inventory_id} has no {item_id}")
        if decision == "CONFIRMED" and item.get("missing_information") and not acknowledge_missing:
            raise KnowledgeError("MISSING_INFORMATION_NOT_ACKNOWLEDGED", "this proposal lists information that is missing (" + "; ".join(item["missing_information"])
                                 + "): confirm only after reading it, and say so with acknowledge_missing / --acknowledge-missing")
        rec = {"at": tx.at, "by": tx.actor_id, "decision": decision, "reason": reason, **({"acknowledged_missing": True} if acknowledge_missing else {})}
        return {**c, key: [{**i, "reviews": [*i["reviews"], rec]} if i["id"] == item_id else i for i in c[key]]}

    out = CL._write_with(store, token, inventory_id, build_, op)
    return {**out, "id": item_id, "decision": decision}


# ------------------------------------------------------------------------------------------------ alignments across versions of one work

def _version_info(store: StateStore, source_id: str) -> dict[str, Any]:
    o = store.get_object(source_id)
    c = (o or {}).get("content", {})
    return {"first_public_date": c.get("first_public_date"), "version_id": c.get("version_id"), "document_type": c.get("document_type")}


def validate_alignment(store: StateStore, content: dict[str, Any], p: dict[str, Any], *, derivation: str) -> dict[str, Any]:
    change = p.get("change")
    if change not in taxonomy.vocabulary("claim_version_change"):
        raise KnowledgeError("CHANGE_UNKNOWN", f"change must be one of {taxonomy.vocabulary('claim_version_change')}")
    pair = change in PAIR_CHANGES
    a = _find(content, p["earlier"]) if p.get("earlier") else None
    b = _find(content, p["later"]) if p.get("later") else None
    if pair and not (a and b):
        raise KnowledgeError("BOTH_CLAIMS_REQUIRED", f"{change} relates a claim of the earlier version to a claim of the later one: give `earlier` and `later`")
    if change == "WITHDRAWN" and (not a or b):
        raise KnowledgeError("EARLIER_ONLY", "WITHDRAWN names the earlier claim only (it has no counterpart in the later version) and the later version it was looked for in (`later_source`)")
    if change == "NEW_IN_LATER_VERSION" and (not b or a):
        raise KnowledgeError("LATER_ONLY", "NEW_IN_LATER_VERSION names the later claim only and the earlier version it was looked for in (`earlier_source`)")
    known = [x for x in (a, b) if x]
    for x in known:
        if not x.get("work_id") or not x.get("wording"):
            raise KnowledgeError("CLAIM_HAS_NO_VERSION", f"{x['id']} has no work: only claims read from a Source belong to a version")
    other_src = p.get("later_source") if change == "WITHDRAWN" else p.get("earlier_source") if change == "NEW_IN_LATER_VERSION" else None
    if not pair:
        if not other_src:
            raise KnowledgeError("OTHER_VERSION_REQUIRED", "name the Source of the other version that was searched (`later_source` / `earlier_source`)")
        osrc = store.get_object(other_src)
        if not osrc or osrc["object_type"] != "Source":
            raise KnowledgeError("SOURCE_NOT_FOUND", f"{other_src} is not a Source in this project")
        if (osrc["content"].get("work_id") or other_src) != known[0]["work_id"] or other_src == known[0]["source_id"]:
            raise KnowledgeError("NOT_ANOTHER_VERSION", f"{other_src} is not another version of work {known[0]['work_id']}")
        other_entry = next((s for s in content["sources"] if s["source_id"] == other_src), None)
        if other_entry is None:
            raise KnowledgeError("VERSION_NOT_IN_INVENTORY", f"{other_src} was not read for this inventory, so an absence in it says nothing")
    if pair:
        if a["work_id"] != b["work_id"]:
            raise KnowledgeError("DIFFERENT_WORKS", "an alignment relates claims of two versions of the SAME work; claims of different works are related with claim_relations.add_relation")
        if a["source_id"] == b["source_id"]:
            raise KnowledgeError("SAME_VERSION", "both claims are in one version")
        other_entry = None
    ea, eb = (a, b) if pair else ((a, None) if a else (None, b))
    rationale = _txt(p, "rationale")
    what = _txt(p, "what_changed") if change != "UNCHANGED" else (p.get("what_changed") or "").strip()
    cond = [str(x).strip() for x in p.get("conditions_added") or [] if str(x).strip()]
    if change == "QUALIFIED" and not cond:
        raise KnowledgeError("CONDITIONS_REQUIRED", "QUALIFIED says which conditions were added (`conditions_added`)")
    missing = [str(m).strip() for m in p.get("missing_information") or [] if str(m).strip()]
    if change in ABSENCE_BASED and not missing:
        raise KnowledgeError("MISSING_INFORMATION_REQUIRED", f"{change} rests on an absence: say what you would need to know (for example whether the other version states it elsewhere)")
    if derivation == "model_proposed" and not isinstance(p.get("missing_information"), list):
        raise KnowledgeError("FIELD_REQUIRED", "missing_information is required (a list; empty only if nothing is missing)", field="missing_information")
    order_basis = None
    if pair:
        da, db = _version_info(store, a["source_id"])["first_public_date"], _version_info(store, b["source_id"])["first_public_date"]
        if da and db:
            if da >= db:
                raise KnowledgeError("ORDER_REVERSED", f"{a['source_id']} (public {da}) is not earlier than {b['source_id']} (public {db}): name the earlier version first")
            order_basis = "first_public_date"
        elif p.get("order_stated_by_researcher") is True:
            order_basis = "stated_by_researcher"
        else:
            raise KnowledgeError("ORDER_UNKNOWN", "the public dates of the two versions cannot say which is later: say so with order_stated_by_researcher=true")
    caveat = None
    if other_entry is not None:
        ex = other_entry["extraction"]
        caveat = {"source_id": other_entry["source_id"], "sections_not_read": ex.get("sections_not_read"), "chars_read": ex.get("chars_read"), "chars_total": ex.get("chars_total"),
                  "note": "an absence in a reading of this version is not proof the claim is absent from it: the extraction may have missed it"}
    pair_key = [ea["id"] if ea else None, eb["id"] if eb else None, other_src]
    for r in content["alignments"]:
        if r["reviews"] and r["reviews"][-1]["decision"] == "DISMISSED":
            continue                                   # a dismissed proposal no longer stands in the way of another
        if [(r["earlier"] or {}).get("claim"), (r["later"] or {}).get("claim"), r.get("other_version_source")] == pair_key:
            if r["change"] == change:
                raise KnowledgeError("DUPLICATE", f"{r['id']} already records this alignment")
            raise KnowledgeError("PAIR_ALREADY_ALIGNED", f"{r['id']} already proposes {r['change']} for this pair: dismiss it first if you propose another change")
    return {"work_id": known[0]["work_id"], "change": change, "earlier": _end(ea) if ea else None, "later": _end(eb) if eb else None, "other_version_source": other_src,
            "what_changed": what, "conditions_added": cond, "rationale": rationale, "missing_information": missing, "coverage_caveat": caveat, "order_basis": order_basis,
            "claims_state": {x["id"]: _state(x) for x in known}}


def add_alignment(store: StateStore, token: Optional[str], inventory_id: str, proposal: dict[str, Any], *, provenance: str = "researcher_proposed") -> dict[str, Any]:
    if provenance not in REL.PROVENANCES:
        raise KnowledgeError("PROVENANCE_UNKNOWN", f"provenance must be one of {REL.PROVENANCES}")
    snapshot = corpus.corpus_snapshot_id(store)
    made: dict[str, str] = {}

    def build_(c: dict[str, Any], tx: Any) -> dict[str, Any]:
        norm = validate_alignment(store, c, proposal, derivation=provenance)
        rec = _record(norm, _next_id(c["alignments"], "A"), derivation=provenance, provenance=provenance, generated_by="researcher", by=tx.actor_id, at=tx.at, snapshot=snapshot)
        made["id"] = rec["id"]
        return {**c, "alignments": [*c["alignments"], rec]}

    return {**CL._write_with(store, token, inventory_id, build_, "claims_add_alignment"), "alignment": made["id"]}


def propose_alignments(store: StateStore, token: Optional[str], inventory_id: str, adapter: Any, *, work_id: Optional[str] = None) -> dict[str, Any]:
    """One model call over the claims of the versions of a work (only works with at least two versions in the inventory): proposed alignments, each through the gate."""
    c0 = CL.load_inventory(store, inventory_id)["content"]
    works: dict[str, set[str]] = {}
    for e in c0["elements"]:
        if e.get("work_id"):
            works.setdefault(e["work_id"], set()).add(e["source_id"])
    multi = sorted(w for w, s in works.items() if len(s) >= 2 and (work_id is None or w == work_id))
    if not multi:
        raise KnowledgeError("NO_VERSIONS", "no work in this inventory has two versions to align")
    sub = {**c0, "elements": [e for e in c0["elements"] if e.get("work_id") in multi]}
    user = _claims_prompt(sub)

    def apply(c: dict[str, Any], tx: Any, proposals: list[dict[str, Any]], who: str, snapshot: str, accepted: list[str], rejected: list[dict[str, Any]]) -> dict[str, Any]:
        als = list(c["alignments"])
        for p in proposals:
            try:
                norm = validate_alignment(store, {**c, "alignments": als}, p, derivation="model_proposed")
            except KnowledgeError as exc:
                rejected.append({"proposal": {k: p.get(k) for k in ("change", "earlier", "later")}, "code": exc.code, "reason": exc.message})
                continue
            rec = _record(norm, _next_id(als, "A"), derivation="model_proposed", provenance=f"suite_proposed:{who}", generated_by=who, by=who, at=tx.at, snapshot=snapshot)
            als.append(rec)
            accepted.append(rec["id"])
        return {**c, "alignments": als}

    return _model_run(store, token, inventory_id, adapter, prompt=ALIGNMENT_PROMPT, schema=ALIGNMENT_SCHEMA, user=user, workflow="claim_alignments", fp_extra=work_id, apply=apply)


def review_alignment(store: StateStore, token: Optional[str], inventory_id: str, alignment_id: str, decision: str, reason: str, *, acknowledge_missing: bool = False) -> dict[str, Any]:
    return _review(store, token, inventory_id, "alignments", alignment_id, decision, reason, acknowledge_missing=acknowledge_missing, op="claims_review_alignment")


def alignment_candidates(store: StateStore, inventory_id: str, work_id: str, *, per_claim: int = 3) -> dict[str, Any]:
    """For each claim of the earlier version, the claims of the later one that share its words (BM25 over text and constructs). **Candidates only**: nothing is written, and a high score is not
    an identity: the wording of a reversed claim is nearly the same as the original's.**"""
    c = CL.load_inventory(store, inventory_id)["content"]
    mine = [e for e in c["elements"] if e.get("work_id") == work_id and e.get("wording")]
    sources = sorted({e["source_id"] for e in mine}, key=lambda s: (_version_info(store, s)["first_public_date"] or "", s))
    if len(sources) < 2:
        return {"work_id": work_id, "candidates": [], "note": "fewer than two versions of this work in the inventory"}
    pairs = []
    for earlier, later in zip(sources, sources[1:]):
        e_claims = [e for e in mine if e["source_id"] == earlier]
        l_claims = [e for e in mine if e["source_id"] == later]
        bm = X.Bm25({e["id"]: X.tokenize(e["text"] + " " + " ".join(e["constructs"])) for e in l_claims})
        for e in e_claims:
            q = X.tokenize(e["text"] + " " + " ".join(e["constructs"]))
            sc = bm.score(q)
            top = sorted(sc, key=lambda i: (-round(sc[i], 9), i))[:per_claim]
            pairs.append({"earlier": e["id"], "earlier_source": earlier, "later_source": later, "candidates": [{"claim": i, "score": round(sc[i], 4)} for i in top]})
    return {"work_id": work_id, "pairs": pairs, "note": "candidates found by words alone; an alignment is a proposal the researcher decides"}


# ------------------------------------------------------------------------------------------------ what is shown

def _why_reassess(store: StateStore, item: dict[str, Any], by_id: dict[str, dict[str, Any]], claim_ids: list[str]) -> list[str]:
    why = []
    for cid in claim_ids:
        c = by_id.get(cid)
        if c is None:
            continue
        if c["currency"]["status"] not in ("CURRENT", "NOT_APPLICABLE"):
            why.append(f"{cid}: {c['currency']['status']}")
        if c["status"] == "REJECTED_BY_RESEARCHER":
            why.append(f"{cid}: REJECTED_BY_RESEARCHER")
        elif item.get("claims_state", {}).get(cid) and item["claims_state"][cid] != _state(c):
            why.append(f"{cid}: CHANGED_AFTER_PROPOSAL (attribution, role or restatement)")
    return why


def enrich_relations(store: StateStore, content: dict[str, Any], claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {c["id"]: c for c in claims}
    out = []
    for r in content["relations"]:
        ends = [r["from"]["claim"]] + ([r["to"]["claim"]] if r["to"].get("claim") else [])
        why = _why_reassess(store, r, {k: {**v, "claims_state_now": None} for k, v in by_id.items()}, ends)
        out.append({**r, "confirmation": REL.confirmation(r), "needs_reassessment": why, "provenance_class": _prov_class(r)})
    return out


def enrich_alignments(store: StateStore, content: dict[str, Any], claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {c["id"]: c for c in claims}
    out = []
    for a in content["alignments"]:
        ends = [x["claim"] for x in (a["earlier"], a["later"]) if x]
        out.append({**a, "confirmation": REL.confirmation(a), "needs_reassessment": _why_reassess(store, a, by_id, ends), "provenance_class": _prov_class(a)})
    return out


def _prov_class(item: dict[str, Any]) -> str:
    if item.get("reviews") and item["reviews"][-1]["decision"] == "CONFIRMED":
        return "researcher_confirmed"
    return "model_synthesised" if item.get("derivation") == "model_proposed" else "researcher_proposed_unconfirmed"


def cited_source_checked(content: dict[str, Any], by_id: dict[str, dict[str, Any]], claim_id: str, source_id: str) -> bool:
    """A cited work counts as checked only when the researcher confirmed a relation between this claim and a claim read from that very Source."""
    for r in content["relations"]:
        if not r["reviews"] or r["reviews"][-1]["decision"] != "CONFIRMED" or r["kind"] not in ("SAME_IDEA_PROPOSED", "SUPPORTS"):
            continue
        a, b = r["from"].get("claim"), r["to"].get("claim")
        if claim_id in (a, b):
            other = b if a == claim_id else a
            if other and (by_id.get(other) or {}).get("source_id") == source_id:
                return True
    return False
