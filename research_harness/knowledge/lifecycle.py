"""Knowledge lifecycle: acquire, formalize, validate, promote, match, revise (plan v3, 4.1 and 4.3).

A library item (`KnowledgeItem`) carries three validity dimensions that are computed and recorded separately, a
lifecycle, and, once promoted, the scope it was promoted for. Nothing here turns "it was used" into "it is valid":

* `source_fidelity` is computed from the item's elements: are the cited passages verbatim in the stored sources
  (QUOTE_VERIFIED), and has the researcher confirmed an evidence-claim adjudication that supports each element
  (MEANING_VERIFIED)? A not-supported adjudication or a challenging passage makes it DISPUTED.
* `computational_reproducibility` is computed from linked replication packages and their declared tolerances.
* `methodological_validity` is a list of assessments, each for one stated context. It is never global, and a model
  or a usage record cannot lift it: SUPPORTED_WITHIN_SCOPE needs methodological literature as a basis, every known
  criticism addressed or scoped out, diagnostics where they apply, and the researcher's confirmation.

Promotion (I14) is an explicit act of the owner with a scope, exclusions, all three dimensions checked at that
moment, and every adjudication resolved. Matching applies the recommendation rule of plan v3 4.1 and shows the
dimensions, known criticisms and failure records side by side.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from research_harness import taxonomy
from research_harness.core.canonical import canonical_json_hash
from research_harness.knowledge.common import (
    KnowledgeError, engine_origin, head_or_raise, id_fragment, next_numbered_id, scope_id)
from research_harness.knowledge.corpus import verify_passage
from research_harness.knowledge.library import make_pin, parse_pin, pins_in
from research_harness.knowledge.replication import reproduce_package
from research_harness.state.store import StateStore
from research_harness.workflows import eca

FIDELITY_ORDER = {"NOT_CHECKED": 0, "QUOTE_VERIFIED": 1, "MEANING_VERIFIED": 2}
VALIDITY_ORDER = {"NOT_ASSESSED": 0, "PROVISIONAL": 1, "SUPPORTED_WITHIN_SCOPE": 2}
ACQUIRE_FROM = ("source", "run", "policy", "review", "retrospective")
CONTEXT_KEYS = ("domain", "subfield", "study_design", "data_regime", "unit_of_analysis", "period", "journal", "estimand",
               "measurement", "assumptions")

_KIND_RULES: dict[str, str] = {
    "verification_rule": "elements",
    "checklist": "elements_or_items",
    "evidence_comparison_pattern": "body.pattern",
    "analysis_script": "body.source_sha256",
    "review_lesson": "body.lesson",
    "reading_protocol": "body.protocol_id",
    "replication_package": "body.files",
    "failure_record": "body.failure",
    "validity_assessment": "body.context",
}


def blank_validation() -> dict[str, Any]:
    return {"source_fidelity": "NOT_CHECKED", "computational_reproducibility": "NOT_ATTEMPTED", "validity_assessments": []}


def _item(store: StateStore, item_id: str, revision: Optional[int] = None) -> dict[str, Any]:
    obj = store.get_object(item_id, revision)
    if not obj or obj["object_type"] != "KnowledgeItem":
        raise KnowledgeError("ITEM_NOT_FOUND", f"{item_id} is not a KnowledgeItem in this store")
    return obj


def _write_item(store: StateStore, token: Optional[str], item_id: str, content: dict[str, Any], operation: str, *,
                roles: tuple = ("importer", "owner", "author"), edges: Optional[list] = None, detail: Optional[dict] = None) -> dict[str, Any]:
    with store.write_tx(token=token, require_roles=roles) as tx:
        head = tx.head(item_id)
        if edges is None:
            edges = []
            if head:
                with_edges = tx.conn.execute("SELECT relation,target_id,target_revision,provenance FROM edges WHERE source_id=? AND "
                                             "source_revision=?", (item_id, head["head_revision"])).fetchall()
                import json as _json
                edges = [(r[0], r[1], r[2], _json.loads(r[3])) for r in with_edges]
        res = tx.add_revision(object_id=item_id, object_type="KnowledgeItem", content=content, edges=edges,
                              origin=engine_origin(operation, tx.actor_id))
        tx.audit(operation, "RECORDED", {"object_id": item_id, "revision": res["revision"], **(detail or {})})
    return {**res, "pin": make_pin(item_id, res["revision"], res["content_digest"])}


# ---------------------------------------------------------------------------------------------------
# acquire, formalize
# ---------------------------------------------------------------------------------------------------

def acquire(store: StateStore, token: Optional[str], *, item_id: Optional[str] = None, kind: str, label: str, acquired_from: str,
            body: Optional[dict] = None, elements: Optional[list] = None, group: Optional[str] = None,
            generated_by: str = "researcher", evidence_basis: Optional[list] = None, pins: Optional[list] = None,
            edges: Iterable[tuple[str, str, int]] = ()) -> dict[str, Any]:
    """Create a candidate item: nothing about it is checked yet (`source_fidelity: NOT_CHECKED`, no validity assessment)."""
    taxonomy.validate_term("knowledge_kind", kind)
    if kind == "library_import_manifest":
        raise KnowledgeError("KIND_NOT_ACQUIRABLE", "import manifests are written only by `library use`")
    if acquired_from not in ACQUIRE_FROM:
        raise KnowledgeError("ACQUIRE_SOURCE_UNKNOWN", f"acquired_from must be one of {ACQUIRE_FROM}")
    from research_harness.workflows.provenance import validate_generated_by
    validate_generated_by(generated_by)
    with store.read() as conn:
        oid = scope_id(store, item_id) if item_id else scope_id(store, next_numbered_id(conn, f"KI_{id_fragment(kind, 12)}_", "KnowledgeItem"))
    content: dict[str, Any] = {"label": label, "kind": kind, "generated_by": generated_by, "lifecycle": "ACTIVE",
                               "validation": blank_validation(), "promotion": {}, "attributes": {"acquired_from": acquired_from}}
    for k, v in (("body", body), ("elements", elements), ("group", group), ("evidence_basis", evidence_basis), ("pins", pins)):
        if v:
            content[k] = v
    res = _write_item(store, token, oid, content, "library_acquire",
                      edges=[(r, t, rv, {"origin": "library_acquire"}) for r, t, rv in edges])
    return {"item_id": oid, **res}


def formalize_problems(content: dict[str, Any], as_kind: str) -> list[str]:
    problems: list[str] = []
    rule = _KIND_RULES.get(as_kind)
    if rule is None:
        return [f"{as_kind} is not a formalizable kind"]
    body = content.get("body") or {}
    elements = content.get("elements") or []
    if rule == "elements" and not elements:
        problems.append(f"{as_kind} needs at least one element")
    elif rule == "elements_or_items" and not (elements or body.get("items")):
        problems.append(f"{as_kind} needs elements or body.items")
    elif rule.startswith("body.") and not body.get(rule[5:]):
        problems.append(f"{as_kind} needs {rule}")
    for i, e in enumerate(elements):
        if not isinstance(e, dict) or not e.get("id") or not (e.get("text") or e.get("statement")):
            problems.append(f"element {i} needs an id and text")
            continue
        basis = e.get("evidence_basis") or []
        if not basis:
            problems.append(f"element {e['id']} has no evidence basis (generated_by is the producer, not evidence)")
        for b in basis:
            try:
                taxonomy.validate_term("evidence_basis_kind", b.get("kind"))
            except Exception:  # noqa: BLE001
                problems.append(f"element {e['id']}: evidence basis kind {b.get('kind')!r} is not in the vocabulary")
    return problems


def formalize(store: StateStore, token: Optional[str], item_id: str, *, as_kind: str) -> dict[str, Any]:
    taxonomy.validate_term("knowledge_kind", as_kind)
    obj = _item(store, item_id)
    problems = formalize_problems(obj["content"], as_kind)
    if problems:
        raise KnowledgeError("FORMALIZE_REFUSED", "; ".join(problems), problems=problems)
    content = {**obj["content"], "kind": as_kind, "attributes": {**(obj["content"].get("attributes") or {}), "formalized": True}}
    return _write_item(store, token, item_id, content, "library_formalize")


# ---------------------------------------------------------------------------------------------------
# the three dimensions, computed from recorded facts
# ---------------------------------------------------------------------------------------------------

def _passage_id(ref: str) -> str:
    return parse_pin(ref)[0] if ref.startswith("LIB:") else ref


def source_fidelity(store: StateStore, item_id: str) -> dict[str, Any]:
    """Compute the item's `source_fidelity` from its elements (see the module docstring)."""
    obj = _item(store, item_id)
    elements = [e for e in obj["content"].get("elements") or [] if isinstance(e, dict) and e.get("critical", True)]
    detail: list[dict[str, Any]] = []
    for e in elements:
        passages = [b for b in e.get("evidence_basis") or [] if b.get("kind") == "source_passage"]
        checks = []
        for b in passages:
            try:
                checks.append(verify_passage(store, _passage_id(b["ref"]))["source_match"])
            except KnowledgeError:
                checks.append("FAILED")
        quote_ok = bool(checks) and all(c == "VERIFIED" for c in checks)
        a = eca.statement_assessment(store, eca.resolve_statement(store, item_id, element=e["id"]))
        meaning = quote_ok and a["researcher_confirmed"] and a["eca_verdict"] in eca.SUPPORTING
        disputed = a["eca_verdict"] == "NOT_SUPPORTED" or bool(a["counter_evidence"])
        detail.append({"element": e["id"], "passages": len(passages), "quote_verified": quote_ok, "eca_verdict": a["eca_verdict"],
                       "researcher_confirmed": a["researcher_confirmed"], "meaning_verified": meaning, "disputed": disputed,
                       "reasons": a["reasons"]})
    if not detail:
        level = "NOT_CHECKED"
    elif any(d["disputed"] for d in detail):
        level = "DISPUTED"
    elif all(d["meaning_verified"] for d in detail):
        level = "MEANING_VERIFIED"
    elif all(d["quote_verified"] for d in detail):
        level = "QUOTE_VERIFIED"
    else:
        level = "NOT_CHECKED"
    return {"source_fidelity": level, "elements": detail}


def computational_reproducibility(store: StateStore, item_id: str) -> dict[str, Any]:
    """From the item's own files (if it is a replication package) and from replication packages it pins."""
    obj = _item(store, item_id)
    packages: list[str] = [item_id] if obj["content"].get("kind") == "replication_package" else []
    for p in pins_in(obj["content"]):
        pid = parse_pin(p)[0]
        other = store.get_object(pid)
        if other and other["object_type"] == "KnowledgeItem" and other["content"].get("kind") == "replication_package" and pid not in packages:
            packages.append(pid)
    results = [reproduce_package(store, pid) for pid in packages]
    states = [r["computational_reproducibility"] for r in results]
    if not results:
        level = "NO_ASSETS"
    elif "FAILED_TO_REPRODUCE" in states:
        level = "FAILED_TO_REPRODUCE"
    elif all(s == "REPRODUCED_WITHIN_TOLERANCE" for s in states):
        level = "REPRODUCED_WITHIN_TOLERANCE"
    elif all(s == "NO_ASSETS" for s in states):
        level = "NO_ASSETS"
    else:
        level = "RAN"
    return {"computational_reproducibility": level, "packages": results}


def context_key(context: dict[str, Any]) -> str:
    return canonical_json_hash({k: context.get(k) for k in CONTEXT_KEYS if context.get(k) is not None})


def assess_validity(store: StateStore, *, context: dict[str, Any], evidence_basis: list[dict[str, Any]],
                    known_criticisms: Optional[list[dict[str, Any]]] = None, diagnostics_runs: Optional[list[str]] = None,
                    diagnostics_applicable: bool = False, contested: bool = False, generated_by: str = "researcher",
                    confirmed_by_researcher: bool = False, assessed_at: Optional[str] = None) -> dict[str, Any]:
    """One validity assessment for one context. The verdict follows from the recorded facts; see the module docstring."""
    if not any(context.get(k) for k in CONTEXT_KEYS):
        raise KnowledgeError("CONTEXT_REQUIRED", f"validity is assessed for a stated context: give at least one of {CONTEXT_KEYS}")
    criticisms = known_criticisms or []
    for c in criticisms:
        if c.get("status") not in {"addressed", "scoped_out", "open"}:
            raise KnowledgeError("CRITICISM_STATUS_INVALID", "a known criticism has status addressed, scoped_out or open")
    for b in evidence_basis:
        taxonomy.validate_term("evidence_basis_kind", b.get("kind"))
        if not b.get("ref"):
            raise KnowledgeError("BASIS_REF_REQUIRED", "an evidence basis names what it rests on")
    methodological = [b for b in evidence_basis if b.get("methodological_literature")]
    blockers: list[str] = []
    if not evidence_basis:
        verdict = "NOT_ASSESSED"
        blockers.append("no evidence basis")
    elif contested:
        verdict = "CONTESTED"
    else:
        verdict = "PROVISIONAL"
        if not methodological:
            blockers.append("no basis from methodological literature (application papers alone do not establish validity)")
        open_ = [c for c in criticisms if c["status"] == "open"]
        if open_:
            blockers.append(f"{len(open_)} known criticism(s) still open")
        if diagnostics_applicable and not diagnostics_runs:
            blockers.append("diagnostics apply here and none were run")
        if not confirmed_by_researcher:
            blockers.append("no researcher confirmation")
        if not blockers:
            verdict = "SUPPORTED_WITHIN_SCOPE"
    return {"context": {k: v for k, v in context.items() if v is not None}, "context_key": context_key(context), "verdict": verdict,
            "evidence_basis": evidence_basis, "known_criticisms": criticisms, "diagnostics_runs": diagnostics_runs or [],
            "diagnostics_applicable": diagnostics_applicable, "generated_by": generated_by,
            "confirmed_by_researcher": confirmed_by_researcher, "blockers": blockers, "assessed_at": assessed_at}


def validate(store: StateStore, token: Optional[str], item_id: str, *, assessment: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Compute all dimensions now and record them on the item (a new revision; history is kept).

    `assessment` is the keyword set for one methodological-validity assessment (see `assess_validity`). Confirming it
    (`confirmed_by_researcher`) needs the token of an author or owner.
    """
    obj = _item(store, item_id)
    fid = source_fidelity(store, item_id)
    comp = computational_reproducibility(store, item_id)
    validation = {**blank_validation(), **(obj["content"].get("validation") or {})}
    if assessment:
        with store.read() as conn:
            from research_harness.core.auth import authenticate
            actor = authenticate(conn, token)
        if assessment.get("confirmed_by_researcher") and not (actor and actor.has_role("author", "owner")):
            raise KnowledgeError("CONFIRMATION_NEEDS_RESEARCHER", "only an author or owner can confirm a validity assessment")
        new = assess_validity(store, **assessment, assessed_at=store.clock())
        kept = [a for a in validation["validity_assessments"] if a["context_key"] != new["context_key"]]
        validation["validity_assessments"] = kept + [new]
    validation["source_fidelity"] = fid["source_fidelity"]
    validation["computational_reproducibility"] = comp["computational_reproducibility"]
    validation["validated_at"] = store.clock()
    content = {**obj["content"], "validation": validation}
    res = _write_item(store, token, item_id, content, "library_validate")
    return {"item_id": item_id, "revision": res["revision"], "pin": res["pin"], "source_fidelity": fid, "computational_reproducibility": comp,
            "validity_assessments": validation["validity_assessments"]}


# ---------------------------------------------------------------------------------------------------
# promote
# ---------------------------------------------------------------------------------------------------

def scope_matches(assessment_context: dict[str, Any], scope: dict[str, Any]) -> bool:
    """Every dimension the scope states is stated, and equal, in the assessment's context."""
    for k, v in scope.items():
        have = assessment_context.get(k)
        if have is None:
            return False
        allowed = v if isinstance(v, list) else [v]
        have_vals = have if isinstance(have, list) else [have]
        if not all(h in allowed for h in have_vals):
            return False
    return True


def promotion_blockers(store: StateStore, item_id: str, *, scope: dict[str, Any], exclusions: Optional[list[str]],
                       none_known: bool) -> list[str]:
    obj = _item(store, item_id)
    blockers: list[str] = []
    if not scope:
        blockers.append("scope is empty: say where the item is meant to be reused")
    else:
        for k in scope:
            if k not in taxonomy.vocabulary("scope_dimension"):
                blockers.append(f"scope dimension {k!r} is not in the vocabulary {tuple(taxonomy.vocabulary('scope_dimension'))}")
    if not exclusions and not none_known:
        blockers.append("exclusions are empty: list them, or record explicitly that none are known")
    elements = obj["content"].get("elements") or []
    fid = source_fidelity(store, item_id)
    if elements and fid["source_fidelity"] != "MEANING_VERIFIED":
        blockers.append(f"source_fidelity is {fid['source_fidelity']}, not MEANING_VERIFIED for every critical element")
    unresolved = [d["element"] for d in fid["elements"] if not (d["researcher_confirmed"] and d["eca_verdict"] in eca.SUPPORTING)]
    if unresolved:
        blockers.append("adjudication unresolved (a promoted element is high-impact): " + ", ".join(unresolved))
    matching = [a for a in (obj["content"].get("validation") or {}).get("validity_assessments", [])
                if scope and scope_matches(a["context"], scope) and VALIDITY_ORDER.get(a["verdict"], 0) >= 1
                and any(b.get("methodological_literature") for b in a["evidence_basis"])]
    if not matching:
        blockers.append("no validity assessment for the promoted scope (PROVISIONAL or higher, with a methodological basis)")
    return blockers


def promote(store: StateStore, token: Optional[str], item_id: str, *, scope: dict[str, Any], exclusions: Optional[list[str]] = None,
            none_known: bool = False) -> dict[str, Any]:
    """Promote an item for a scope (owner token). Every condition is checked now, from recorded facts."""
    obj = _item(store, item_id)
    if (obj["content"].get("lifecycle") or "ACTIVE") != "ACTIVE":
        raise KnowledgeError("ITEM_NOT_ACTIVE", f"{item_id} is {obj['content'].get('lifecycle')}")
    blockers = promotion_blockers(store, item_id, scope=scope, exclusions=exclusions, none_known=none_known)
    if blockers:
        raise KnowledgeError("PROMOTION_BLOCKED", "; ".join(blockers), blockers=blockers)
    fresh = _item(store, item_id)
    validation = fresh["content"].get("validation") or blank_validation()
    fid = source_fidelity(store, item_id)["source_fidelity"]
    comp = computational_reproducibility(store, item_id)["computational_reproducibility"]
    with store.write_tx(token=token, require_roles=("owner",)) as tx:
        head = head_or_raise(tx, item_id, "KnowledgeItem")
        if head["head_revision"] != fresh["revision"]:
            raise KnowledgeError("REVISION_NOT_HEAD", f"{item_id} changed while being checked")
        content = {**fresh["content"], "validation": {**validation, "source_fidelity": fid, "computational_reproducibility": comp},
                   "promotion": {"promoted": True, "scope": scope, "exclusions": exclusions or [], "exclusions_none_known": bool(none_known and not exclusions),
                                 "promoted_by": tx.actor_id, "promoted_at": tx.at, "dimensions_at_promotion": {
                                     "source_fidelity": fid, "computational_reproducibility": comp,
                                     "methodological_validity": [{"context_key": a["context_key"], "verdict": a["verdict"]}
                                                                 for a in validation.get("validity_assessments", [])]}},
                   "scope": scope, "exclusions": exclusions or []}
        rows = tx.conn.execute("SELECT relation,target_id,target_revision,provenance FROM edges WHERE source_id=? AND source_revision=?",
                               (item_id, fresh["revision"])).fetchall()
        import json as _json
        res = tx.add_revision(object_id=item_id, object_type="KnowledgeItem", content=content,
                              edges=[(r[0], r[1], r[2], _json.loads(r[3])) for r in rows],
                              origin=engine_origin("library_promote", tx.actor_id))
        tx.audit("library_promote", "PROMOTED", {"object_id": item_id, "revision": res["revision"], "scope": scope})
    return {"item_id": item_id, "revision": res["revision"], "pin": make_pin(item_id, res["revision"], res["content_digest"]),
            "scope": scope, "dimensions": content["promotion"]["dimensions_at_promotion"]}


# ---------------------------------------------------------------------------------------------------
# match (reuse)
# ---------------------------------------------------------------------------------------------------

def _failure_records_for(store: StateStore, item_id: str) -> list[dict[str, Any]]:
    out = []
    with store.read() as conn:
        rows = conn.execute("SELECT object_id FROM object_heads WHERE object_type='KnowledgeItem' ORDER BY object_id").fetchall()
        for r in rows:
            o = store.get_object(r[0], conn=conn)
            if o["content"].get("kind") != "failure_record":
                continue
            ids = {parse_pin(p)[0] for p in pins_in(o["content"])} | {e["target_id"] for e in store.edges_from(conn, o["object_id"], o["revision"])}
            if item_id in ids:
                out.append({"object_id": o["object_id"], "failure": (o["content"].get("body") or {}).get("failure"),
                            "context": (o["content"].get("body") or {}).get("context")})
    return out


def _exclusion_mismatch(item: dict[str, Any], context: dict[str, Any]) -> list[str]:
    conditions = {str(c) for c in context.get("conditions") or []}
    return [x for x in item["content"].get("exclusions") or [] if x in conditions]


def _scope_mismatch(item: dict[str, Any], context: dict[str, Any]) -> list[str]:
    promoted = (item["content"].get("promotion") or {}).get("scope") or item["content"].get("scope") or {}
    bad = []
    for k, v in promoted.items():
        have = context.get(k)
        if have is None:
            continue
        allowed = v if isinstance(v, list) else [v]
        if have not in allowed:
            bad.append(k)
    return bad


def match(store: StateStore, context: dict[str, Any]) -> list[dict[str, Any]]:
    """Apply the recommendation rule (plan v3 4.1) to every non-manifest item for a project context.

    Statuses: `applicable` (SUPPORTED_WITHIN_SCOPE for a matching context, source fidelity established, no exclusion hit),
    `candidate_validity_not_established_here` (with the questions to answer), `related_not_applicable` (with the reason).
    """
    out: list[dict[str, Any]] = []
    with store.read() as conn:
        ids = [r[0] for r in conn.execute("SELECT object_id FROM object_heads WHERE object_type='KnowledgeItem' ORDER BY object_id")]
    for iid in ids:
        item = _item(store, iid)
        c = item["content"]
        if c.get("kind") in {"library_import_manifest", "failure_record"}:
            continue
        lifecycle = "WITHDRAWN" if item.get("lifecycle_status") == "WITHDRAWN" else (c.get("lifecycle") or "ACTIVE")
        fid = source_fidelity(store, iid)
        comp = computational_reproducibility(store, iid)
        assessments = (c.get("validation") or {}).get("validity_assessments", [])
        here = [a for a in assessments if scope_matches(a["context"], {k: v for k, v in context.items() if k in CONTEXT_KEYS and v is not None})]
        validity_here = max((a["verdict"] for a in here), key=lambda v: (v == "CONTESTED", VALIDITY_ORDER.get(v, 0)), default="NOT_ASSESSED")
        row = {"item_id": iid, "label": c.get("label"), "kind": c.get("kind"), "pin": make_pin(iid, item["revision"], item["content_digest"]),
               "promoted": bool((c.get("promotion") or {}).get("promoted")), "lifecycle": lifecycle,
               "dimensions": {"source_fidelity": fid["source_fidelity"], "computational_reproducibility": comp["computational_reproducibility"],
                              "methodological_validity_for_this_context": validity_here,
                              "methodological_validity_all_contexts": [{"context": a["context"], "verdict": a["verdict"]} for a in assessments]},
               "known_criticisms": [k for a in assessments for k in a.get("known_criticisms", [])],
               "failure_records": _failure_records_for(store, iid)}
        reasons: list[str] = []
        if lifecycle in {"WITHDRAWN", "SUPERSEDED"}:
            reasons.append(f"the item is {lifecycle.lower()}")
        hit = _exclusion_mismatch(item, context)
        if hit:
            reasons.append("exclusion applies here: " + "; ".join(hit))
        bad = _scope_mismatch(item, context)
        if bad:
            reasons.append("outside the item's scope on: " + ", ".join(bad))
        if validity_here == "CONTESTED" or fid["source_fidelity"] == "DISPUTED":
            reasons.append("contested" if validity_here == "CONTESTED" else "its source fidelity is disputed")
        if reasons:
            row.update(status="related_not_applicable", because=reasons)
        else:
            elements = c.get("elements") or []
            fidelity_ok = (fid["source_fidelity"] == "MEANING_VERIFIED") if elements else (fid["source_fidelity"] != "DISPUTED")
            if validity_here == "SUPPORTED_WITHIN_SCOPE" and fidelity_ok:
                row.update(status="applicable", because=["methodological validity is supported for a context that matches this one"])
            else:
                questions = ["For which estimand, measurement, assumptions and data regime would this procedure be valid here?"]
                missing = [k for k in ("estimand", "measurement", "assumptions") if not context.get(k)]
                if missing:
                    questions.append("State " + ", ".join(missing) + " for this project.")
                for k in row["known_criticisms"]:
                    if k.get("status") == "open":
                        questions.append(f"Address the open criticism {k.get('ref')}.")
                if elements and fid["source_fidelity"] != "MEANING_VERIFIED":
                    questions.append("Confirm the evidence-claim adjudication of each element.")
                row.update(status="candidate_validity_not_established_here", because=["fidelity or reproducibility may be good, but validity is not "
                           "assessed for this context"], questions=questions)
        out.append(row)
    return out


# ---------------------------------------------------------------------------------------------------
# revise, usage, retrospective
# ---------------------------------------------------------------------------------------------------

def revise(store: StateStore, token: Optional[str], item_id: str, *, restrict: Optional[list[str]] = None, extend: Optional[dict] = None,
           supersede_by: Optional[str] = None, record_failure: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Record a change to an item as a new revision (history kept). Exactly one action per call."""
    actions = [a for a in (restrict, extend, supersede_by, record_failure) if a]
    if len(actions) != 1:
        raise KnowledgeError("ONE_ACTION", "give exactly one of restrict, extend, supersede_by, record_failure")
    obj = _item(store, item_id)
    c = dict(obj["content"])
    if restrict:
        c["exclusions"] = sorted(set(c.get("exclusions") or []) | set(restrict))
        c["lifecycle"] = "RESTRICTED"
        c["promotion"] = {**(c.get("promotion") or {}), "exclusions": c["exclusions"]}
        res = _write_item(store, token, item_id, c, "library_revise_restrict", roles=("owner", "author"))
    elif extend:
        c["scope"] = {**(c.get("scope") or {}), **extend}
        c["promotion"] = {**(c.get("promotion") or {}), "extension_pending": extend}   # promotion still covers the old scope only
        res = _write_item(store, token, item_id, c, "library_revise_extend", roles=("owner", "author"))
    elif supersede_by:
        oid, _, _ = parse_pin(supersede_by)
        if oid == item_id:
            raise KnowledgeError("SUPERSEDE_SELF", "an item cannot supersede itself")
        c["lifecycle"] = "SUPERSEDED"
        c["superseded_by"] = supersede_by
        res = _write_item(store, token, item_id, c, "library_revise_supersede", roles=("owner", "author"))
    else:
        with store.read() as conn:
            fid = next_numbered_id(conn, scope_id(store, "KI_FAILURE_"), "KnowledgeItem")
        failure = _write_item(store, token, fid, {
            "label": f"Failure of {item_id}", "kind": "failure_record", "generated_by": record_failure.get("generated_by", "researcher"),
            "lifecycle": "ACTIVE", "validation": blank_validation(), "pins": [make_pin(item_id, obj["revision"], obj["content_digest"])],
            "body": {"failure": record_failure["failure"], "context": record_failure.get("context"),
                     "observed_in": record_failure.get("observed_in")}},
            "library_record_failure", roles=("owner", "author"), edges=[("depends_on", item_id, obj["revision"], {"origin": "failure_record"})])
        return {"item_id": item_id, "failure_record": fid, "pin": failure["pin"]}
    return {"item_id": item_id, "revision": res["revision"], "pin": res["pin"], "lifecycle": c["lifecycle"]}


def record_usage(project: StateStore, token: Optional[str], manifest_id: str, *, context: dict[str, Any], outcome: dict[str, Any]) -> dict[str, Any]:
    """Note, in the project, that an imported item was used here and what happened. Descriptive only: the library item
    and its validity are not touched, so usage can never raise validity."""
    m = project.get_object(manifest_id)
    if not m or m["content"].get("kind") != "library_import_manifest":
        raise KnowledgeError("MANIFEST_UNKNOWN", f"{manifest_id} is not an import manifest of this project")
    records = list(m["content"].get("usage_records") or [])
    records.append({"project_id": project.project_id, "context": context, "outcome": outcome, "recorded_at": project.clock()})
    return _write_item(project, token, manifest_id, {**m["content"], "usage_records": records}, "library_usage")


def retrospective(project: StateStore) -> dict[str, Any]:
    """What this project produced that could become library knowledge, with each dimension and its context reported
    separately. Every candidate starts unpromoted."""
    candidates, imported = [], []
    with project.read() as conn:
        ids = [r[0] for r in conn.execute("SELECT object_id FROM object_heads WHERE object_type='KnowledgeItem' ORDER BY object_id")]
    for iid in ids:
        obj = _item(project, iid)
        kind = obj["content"].get("kind")
        if kind == "library_import_manifest":
            imported.append({"manifest_id": iid, "pin": (obj["content"].get("pins") or [None])[0],
                             "usage_records": obj["content"].get("usage_records") or []})
            continue
        if obj["origin"].get("kind") == "LIBRARY_IMPORTED":
            continue
        fid = source_fidelity(project, iid)
        comp = computational_reproducibility(project, iid)
        candidates.append({"item_id": iid, "kind": kind, "promoted": False,
                           "dimensions": {"source_fidelity": fid["source_fidelity"], "computational_reproducibility": comp["computational_reproducibility"],
                                          "methodological_validity": [{"context": a["context"], "verdict": a["verdict"]}
                                                                      for a in (obj["content"].get("validation") or {}).get("validity_assessments", [])]},
                           "failures": [{"object_id": iid, "failure": (obj["content"].get("body") or {}).get("failure")}] if kind == "failure_record" else []})
    return {"candidates": candidates, "imported_items_used": imported, "note": "every candidate starts unpromoted"}
