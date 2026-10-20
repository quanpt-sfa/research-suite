"""Literature-grounded method playbooks, selection, and analysis plans (plan v3, 3D).

A `MethodPlaybook` is a procedure reconstructed from the methods literature. It is a list of **elements**, each one a
statement with its evidence basis (quote-verified passages) and its own three validity dimensions:

* `source_fidelity`: computed per element by `lifecycle.source_fidelity` (quote verified; meaning verified only through a
  confirmed adjudication);
* `computational_reproducibility`: from the replication outputs the element is linked to (`NO_ASSETS` if none);
* `methodological_validity`: the validity assessments that cover the element, each for one stated context.

A playbook that is merely extracted and reproduced is therefore "candidate, validity not established here" until an
assessment exists for the project's context. **Selection** (`select_playbooks`) applies the recommendation rule and the
playbook's own *conditions* (facts that must hold for the procedure to make sense). It never recommends a method because of
the shape of the data (audit finding C3): a context that only describes the data matches no validity assessment.

An **analysis plan** is built from a playbook for one project as a *proposal* (I7): every critical element is addressed as a
step that is `inherited`, `adapted` (with what changed and why), or `omitted` (with a reason); `added` steps need a basis.
The fidelity report says what the procedure rests on, what was kept, changed, added or dropped, and which conditions hold.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from research_harness.core.canonical import canonical_json_hash
from research_harness.knowledge import lifecycle
from research_harness.knowledge.common import (
    WRITE_ROLES, KnowledgeError, engine_origin, head_or_raise, scope_id)
from research_harness.knowledge.library import make_pin, parse_pin
from research_harness.knowledge.replication import reproduce_package
from research_harness.state.store import StateStore
from research_harness.workflows.provenance import Basis, MissingEvidenceBasis, Statement, require_evidence_basis

CATEGORY_ORDER = ("data_preparation", "sample_construction", "estimand", "specification", "validation_scheme", "diagnostics",
                  "robustness", "interpretation", "alternatives")
CRITICAL = frozenset({"sample_construction", "estimand", "specification", "validation_scheme", "diagnostics", "interpretation"})
FIELD_CATEGORY = {"study_design": "specification", "sample_selection": "sample_construction", "label_construction": "estimand",
                  "variable_measurement": "specification", "model_or_estimator": "specification", "validation_or_tests": "validation_scheme",
                  "interpretation_rule": "interpretation", "limitation_handling": "diagnostics"}
FIDELITY = ("inherited", "adapted", "added", "omitted")
OMIT_REASONS = ("not_applicable", "infeasible", "replaced_by")
EXECUTOR = "playbooks@1"
_ASSESS_ORDER = {"NOT_ASSESSED": 0, "PROVISIONAL": 1, "SUPPORTED_WITHIN_SCOPE": 2}


def _model_label(model: Optional[dict[str, Any]]) -> str:
    return f"model:{(model or {}).get('model_id') or (model or {}).get('provider') or 'unknown'}"


def _put(store: StateStore, token: Optional[str], object_id: str, object_type: str, content: dict[str, Any], edges: list[tuple],
         operation: str) -> dict[str, Any]:
    with store.write_tx(token=token, require_roles=WRITE_ROLES) as tx:
        res = tx.add_revision(object_id=object_id, object_type=object_type, content=content, edges=edges,
                              origin=engine_origin(operation, tx.actor_id))
        tx.audit(operation, "RECORDED", {"object_id": object_id, "revision": res["revision"]})
    return {**res, "object_id": object_id, "pin": make_pin(object_id, res["revision"], res["content_digest"])}


def _edges_of(tx: Any, object_id: str, revision: int) -> list[tuple]:
    rows = tx.conn.execute("SELECT relation,target_id,target_revision,provenance FROM edges WHERE source_id=? AND source_revision=?",
                           (object_id, revision)).fetchall()
    return [(r[0], r[1], r[2], json.loads(r[3])) for r in rows]


# --------------------------------------------------------------------------------------------------
# building a playbook
# --------------------------------------------------------------------------------------------------

def draft_playbook_from_methods(store: StateStore, token: Optional[str], method_review: dict[str, Any], *, playbook_id: str, label: str,
                                scope: Optional[dict[str, Any]] = None, exclusions: Optional[list[str]] = None,
                                replication_package: Optional[str] = None, replication_links: Optional[dict[str, list[str]]] = None,
                                conditions: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    """Assemble a draft playbook from the MethodRecords of a 3C method review (and, optionally, a replication package).

    The draft is `EXTRACTED`: every element is a model-generated statement whose evidence is a quote-verified passage.
    Nothing in it is validated; `lifecycle.validate` and `lifecycle.promote` decide that, per dimension and context.
    """
    if method_review.get("purpose") != "method" or "method_records" not in method_review:
        raise KnowledgeError("NOT_A_METHOD_REVIEW", "a playbook is drafted from run_literature(purpose='method') output")
    pid = scope_id(store, playbook_id)
    elements: list[dict[str, Any]] = []
    sources: dict[str, int] = {}
    passage_edges: list[tuple[str, int]] = []
    for mr in method_review["method_records"]:
        sources[mr["source_id"]] = mr["source_revision"]
        gen = _model_label(mr.get("model"))
        for fld, recs in mr["fields"].items():
            category = FIELD_CATEGORY.get(fld)
            if category is None:
                continue
            for rec in recs:
                elements.append({"id": f"E{len(elements) + 1:02d}", "category": category, "critical": category in CRITICAL, "text": rec["value"],
                                 "source_field": fld, "source_id": mr["source_id"], "generated_by": gen, "risk_tier": "HIGH",
                                 "evidence_basis": [{"kind": "source_passage", "ref": rec["passage_id"], "quote_verified": True,
                                                     "char_start": rec["char_start"], "char_end": rec["char_end"]}]})
                passage_edges.append((rec["passage_id"], 0))
    if not elements:
        raise KnowledgeError("NO_ELEMENTS", "the method review has no records to build a playbook from")
    order = {c: i for i, c in enumerate(CATEGORY_ORDER)}
    elements.sort(key=lambda e: (order[e["category"]], e["id"]))
    sequence = [{"step": f"S{i + 1:02d}", "element": e["id"], "purpose": e["category"]} for i, e in enumerate(elements)
                if e["category"] != "alternatives"]
    links = replication_links or {}
    for e in elements:
        if links.get(e["category"]):
            e["replication_outputs"] = list(links[e["category"]])
    pins: list[str] = []
    edges: list[tuple] = []
    assets: dict[str, Any] = {}
    with store.read() as conn:
        for sid, rev in sources.items():
            edges.append(("derived_from", sid, rev, {"origin": "playbook_draft"}))
        for p in sorted({p for p, _ in passage_edges}):
            edges.append(("depends_on", p, store.get_object(p, conn=conn)["revision"], {"origin": "playbook_draft"}))
        if replication_package:
            pkg = store.get_object(replication_package, conn=conn)
            if not pkg or pkg["content"].get("kind") != "replication_package":
                raise KnowledgeError("NOT_A_REPLICATION_PACKAGE", f"{replication_package} is not a replication_package item")
            pins.append(make_pin(pkg["object_id"], pkg["revision"], pkg["content_digest"]))
            edges.append(("depends_on", pkg["object_id"], pkg["revision"], {"origin": "playbook_draft"}))
            assets = {"package": pkg["object_id"], "files": [{"name": f["name"], "sha256": f["sha256"]} for f in (pkg["content"].get("body") or {}).get("files", [])]}
    content = {
        "label": label, "generated_by": "deterministic:playbooks@1", "lifecycle": "ACTIVE", "validation_status": "EXTRACTED",
        "validation": lifecycle.blank_validation(), "promotion": {}, "elements": elements, "conditions": conditions or [],
        "scope": scope or {}, "exclusions": exclusions or [], "pins": pins,
        "playbook": {"lineage": [{"source_id": s, "revision": r} for s, r in sorted(sources.items())],
                     "source_verification": {"basis": "quote-verified passages", "passages": len(passage_edges)},
                     "analysis_sequence": sequence, "replication_assets": assets, "journal_alignment": []}}
    res = _put(store, token, pid, "MethodPlaybook", content, edges, "playbook_draft")
    return {**res, "elements": len(elements), "critical": sum(1 for e in elements if e["critical"])}


def add_element(store: StateStore, token: Optional[str], playbook_id: str, *, category: str, text: str,
                evidence_basis: Optional[list[dict[str, Any]]] = None, generated_by: str = "researcher",
                allow_unbased: bool = False, replication_outputs: Optional[list[str]] = None) -> dict[str, Any]:
    """Add an element. Every element needs an evidence basis (I9): without one it is refused, unless `allow_unbased` stores
    it flagged `basis_missing`, which blocks promotion."""
    if category not in CATEGORY_ORDER:
        raise KnowledgeError("CATEGORY_UNKNOWN", f"category must be one of {CATEGORY_ORDER}")
    st = Statement(text=text, generated_by=generated_by)
    for b in evidence_basis or []:
        st.evidence_basis.append(Basis(b["kind"], b["ref"], b.get("char_start"), b.get("char_end"), b.get("quote_verified")))
    try:
        require_evidence_basis(st, what=f"playbook element ({category})")
    except MissingEvidenceBasis as exc:
        if not allow_unbased:
            raise KnowledgeError("ELEMENT_WITHOUT_BASIS", str(exc)) from exc
    pb = store.get_object(playbook_id)
    if not pb or pb["object_type"] != "MethodPlaybook":
        raise KnowledgeError("PLAYBOOK_NOT_FOUND", f"{playbook_id} is not a MethodPlaybook")
    els = list(pb["content"]["elements"])
    el = {"id": f"E{len(els) + 1:02d}", "category": category, "critical": category in CRITICAL, "text": text, "generated_by": generated_by,
          "risk_tier": "HIGH", "evidence_basis": evidence_basis or []}
    if not evidence_basis:
        el["basis_missing"] = True
    if replication_outputs:
        el["replication_outputs"] = replication_outputs
    els.append(el)
    with store.write_tx(token=token, require_roles=WRITE_ROLES) as tx:
        cur = tx.get(playbook_id)
        edges = _edges_of(tx, playbook_id, cur["revision"])
        for b in evidence_basis or []:
            h = tx.head(b["ref"]) if b["kind"] == "source_passage" else None
            if h and not any(e[1] == b["ref"] for e in edges):
                edges.append(("depends_on", b["ref"], h["head_revision"], {"origin": "playbook_element"}))
        res = tx.add_revision(object_id=playbook_id, object_type="MethodPlaybook", content={**cur["content"], "elements": els}, edges=edges,
                              origin=engine_origin("playbook_add_element", tx.actor_id))
    return {"playbook_id": playbook_id, "revision": res["revision"], "element": el["id"]}


def set_conditions(store: StateStore, token: Optional[str], playbook_id: str, conditions: list[dict[str, Any]]) -> dict[str, Any]:
    """Record the facts that must hold for the procedure to make sense: `{id, text, requires: {fact, equals|in}, basis}`."""
    for c in conditions:
        req = c.get("requires") or {}
        if not c.get("id") or not req.get("fact") or not ("equals" in req or "in" in req):
            raise KnowledgeError("CONDITION_INVALID", "a condition needs an id and requires {fact, equals|in}")
        if not c.get("basis"):
            raise KnowledgeError("CONDITION_INVALID", f"condition {c['id']} needs a basis: where does the requirement come from?")
    with store.write_tx(token=token, require_roles=WRITE_ROLES) as tx:
        cur = tx.get(playbook_id)
        if not cur or cur["object_type"] != "MethodPlaybook":
            raise KnowledgeError("PLAYBOOK_NOT_FOUND", f"{playbook_id} is not a MethodPlaybook")
        res = tx.add_revision(object_id=playbook_id, object_type="MethodPlaybook", content={**cur["content"], "conditions": conditions},
                              edges=_edges_of(tx, playbook_id, cur["revision"]), origin=engine_origin("playbook_conditions", tx.actor_id))
    return {"playbook_id": playbook_id, "revision": res["revision"]}


# --------------------------------------------------------------------------------------------------
# per-element dimensions
# --------------------------------------------------------------------------------------------------

def element_report(store: StateStore, playbook_id: str) -> list[dict[str, Any]]:
    """For every element: its own source fidelity, computational reproducibility, and the validity assessments covering it."""
    pb = store.get_object(playbook_id)
    if not pb or pb["object_type"] != "MethodPlaybook":
        raise KnowledgeError("PLAYBOOK_NOT_FOUND", f"{playbook_id} is not a MethodPlaybook")
    fid = {d["element"]: d for d in lifecycle.source_fidelity(store, playbook_id)["elements"]}
    pkgs: dict[str, dict[str, Any]] = {}
    for p in pb["content"].get("pins") or []:
        oid = parse_pin(p)[0]
        o = store.get_object(oid)
        if o and o["content"].get("kind") == "replication_package":
            pkgs[oid] = reproduce_package(store, oid)
    assessments = (pb["content"].get("validation") or {}).get("validity_assessments", [])
    out = []
    for e in pb["content"]["elements"]:
        d = fid.get(e["id"])
        level = "NOT_CHECKED"
        if d:
            level = "DISPUTED" if d["disputed"] else "MEANING_VERIFIED" if d["meaning_verified"] else "QUOTE_VERIFIED" if d["quote_verified"] else "NOT_CHECKED"
        outs = e.get("replication_outputs") or []
        repro = "NO_ASSETS"
        if outs and pkgs:
            states = [o["status"] for r in pkgs.values() for o in r["outputs"] if o["id"] in outs]
            repro = ("NO_ASSETS" if not states else "FAILED_TO_REPRODUCE" if "FAILED" in states
                     else "REPRODUCED_WITHIN_TOLERANCE" if all(s == "REPRODUCED" for s in states) else "RAN")
        covering = [{"context": a["context"], "verdict": a["verdict"]} for a in assessments if not a.get("elements") or e["id"] in a["elements"]]
        out.append({"element": e["id"], "category": e["category"], "critical": e.get("critical", False), "text": e["text"],
                    "source_fidelity": level, "computational_reproducibility": repro, "validity_assessments": covering,
                    "basis_missing": bool(e.get("basis_missing") or not e.get("evidence_basis"))})
    return out


# --------------------------------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------------------------------

def evaluate_conditions(conditions: list[dict[str, Any]], facts: dict[str, Any]) -> list[dict[str, Any]]:
    """SATISFIED (with the fact and its value), NOT_SATISFIED (with the reason) or UNKNOWN (with the fact needed)."""
    out = []
    for c in conditions:
        req = c["requires"]
        fact = req["fact"]
        if fact not in facts:
            out.append({"id": c["id"], "status": "UNKNOWN", "text": c["text"], "needs": fact})
            continue
        ok = facts[fact] == req["equals"] if "equals" in req else facts[fact] in req["in"]
        out.append({"id": c["id"], "status": "SATISFIED" if ok else "NOT_SATISFIED", "text": c["text"],
                    "evidence": {"fact": fact, "value": facts[fact]}, **({} if ok else {"reason": f"{fact} is {facts[fact]!r}, the procedure needs "
                                                                                              f"{req['equals'] if 'equals' in req else req['in']!r}"})})
    return out


def select_playbooks(store: StateStore, context: dict[str, Any]) -> list[dict[str, Any]]:
    """Playbooks for a project context, ranked and justified. `context` holds the scope dimensions, estimand, measurement,
    assumptions, `conditions` (exclusion tags) and `facts` (the project facts the playbook conditions are evaluated against).

    A playbook is `applicable` only if the recommendation rule says so *and* every condition is satisfied. Data shape alone
    never makes one applicable: that needs a validity assessment covering this context.
    """
    facts = context.get("facts") or {}
    rows = lifecycle.match(store, {k: v for k, v in context.items() if k != "facts"}, types=("MethodPlaybook",))
    out = []
    for r in rows:
        pb = store.get_object(r["item_id"])
        conds = evaluate_conditions(pb["content"].get("conditions") or [], facts)
        failed = [c for c in conds if c["status"] == "NOT_SATISFIED"]
        unknown = [c for c in conds if c["status"] == "UNKNOWN"]
        status, because = r["status"], list(r["because"])
        questions = list(r.get("questions", []))
        if failed:
            status = "related_not_applicable"
            because = [f"condition {c['id']} is not satisfied: {c['reason']}" for c in failed] + because
        elif status == "applicable" and unknown:
            status = "candidate_validity_not_established_here"
            because = ["validity is supported for this context, but a condition of the procedure is not yet known to hold"]
        questions += [f"Does '{c['text']}' hold? Provide the fact '{c['needs']}'." for c in unknown]
        out.append({**r, "status": status, "because": because, "questions": questions, "conditions": conds,
                    "elements": element_report(store, r["item_id"])})
    rank = {"applicable": 0, "candidate_validity_not_established_here": 1, "related_not_applicable": 2}
    out.sort(key=lambda x: (rank[x["status"]], len([c for c in x["conditions"] if c["status"] == "UNKNOWN"]), x["item_id"]))
    return out


# --------------------------------------------------------------------------------------------------
# analysis plan (a proposal, I7)
# --------------------------------------------------------------------------------------------------

def validate_steps(playbook: dict[str, Any], steps: list[dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    els = {e["id"]: e for e in playbook["content"]["elements"]}
    ids = [s.get("id") for s in steps]
    if len(set(ids)) != len(ids) or not all(ids):
        problems.append("step ids must be present and unique")
    addressed = set()
    for s in steps:
        f = s.get("fidelity")
        if f not in FIDELITY:
            problems.append(f"step {s.get('id')}: fidelity must be one of {FIDELITY}")
            continue
        if f != "added":
            if s.get("element") not in els:
                problems.append(f"step {s['id']}: {f} step must name a playbook element")
                continue
            addressed.add(s["element"])
        if f == "adapted" and not (s.get("changes") and s.get("rationale")):
            problems.append(f"step {s['id']}: an adapted step records what changed relative to the reference and why")
        if f == "added" and not (s.get("basis") and s.get("rationale")):
            problems.append(f"step {s['id']}: an added step needs a rationale and a basis")
        if f == "omitted":
            if s.get("omit_reason") not in OMIT_REASONS:
                problems.append(f"step {s['id']}: an omitted step gives omit_reason in {OMIT_REASONS}")
            elif s["omit_reason"] == "replaced_by" and s.get("replaced_by") not in ids:
                problems.append(f"step {s['id']}: replaced_by must name another step of the plan")
        elif not s.get("command"):
            problems.append(f"step {s['id']}: a step that runs needs a command (a list of arguments)")
        for dep in s.get("depends_on_steps") or []:
            if dep not in ids:
                problems.append(f"step {s['id']}: depends on unknown step {dep}")
    for e in els.values():
        if e.get("critical") and e["id"] not in addressed:
            problems.append(f"critical element {e['id']} ({e['category']}) is not addressed: inherit, adapt or omit it with a reason")
    return problems


def draft_analysis_plan(store: StateStore, playbook_id: str, *, plan_id: str, label: str, context: dict[str, Any], steps: list[dict[str, Any]],
                        declared: Optional[dict[str, Any]] = None, domain_workflow: Optional[str] = None) -> dict[str, Any]:
    """Build the operations that propose an AnalysisPlan (nothing is written). Refuses a playbook that does not apply to the
    project's context (a qualitative study is never given an econometric plan), and a plan that leaves a critical element unaddressed."""
    pb = store.get_object(playbook_id)
    if not pb or pb["object_type"] != "MethodPlaybook":
        raise KnowledgeError("PLAYBOOK_NOT_FOUND", f"{playbook_id} is not a MethodPlaybook")
    sel = next((r for r in select_playbooks(store, context) if r["item_id"] == playbook_id), None)
    if sel is None or sel["status"] == "related_not_applicable":
        raise KnowledgeError("PLAYBOOK_NOT_APPLICABLE", f"{playbook_id} is not applicable to this context: "
                             + "; ".join(sel["because"] if sel else ["not found"]))
    problems = validate_steps(pb, steps)
    if problems:
        raise KnowledgeError("PLAN_INVALID", "; ".join(problems), problems=problems)
    summary = {f: [s["id"] for s in steps if s["fidelity"] == f] for f in FIDELITY}
    content = {"label": label, "playbook_pins": [make_pin(pb["object_id"], pb["revision"], pb["content_digest"])], "steps": steps,
               "declared": declared or {}, "context": {k: v for k, v in context.items() if k != "facts"},
               "conditions_evaluation": sel["conditions"], "fidelity_summary": summary}
    if domain_workflow:
        content["domain_workflow"] = domain_workflow
    return {"operations": [{"operation": "propose_object", "proposed_id": plan_id, "object_type": "AnalysisPlan", "content": content,
                            "relations": [{"relation": "depends_on", "target_id": playbook_id}]}],
            "playbook_status": sel["status"], "fidelity_summary": summary}


def submit_operations(store: StateStore, token: str, operations: list[dict[str, Any]], *, proposal_id: str, read_set_extra: tuple = ()) -> dict[str, Any]:
    """Import operations through the proposal gateway as the token's actor (the researcher accepting the proposal)."""
    from research_harness import PROTOCOL_VERSION
    from research_harness.core.auth import authenticate
    from research_harness.proposals.gateway import import_proposal
    from research_harness.protocols.snapshot import export_snapshot
    with store.read() as conn:
        actor = authenticate(conn, token)
    snap = export_snapshot(store, actor, scope="full", token=token)
    read_set = dict(snap.get("object_revisions", {}))
    doc = {"protocol_version": PROTOCOL_VERSION, "proposal_id": proposal_id, "project_id": store.project_id,
           "base": {"snapshot_id": snap["snapshot_id"], "state_revision": snap["state_revision"], "state_sha256": snap["state_sha256"]},
           "read_set": read_set, "origin_channel": "human_cli", "operations": operations, "note": "analysis plan accepted by the researcher"}
    return import_proposal(store, json.dumps(doc).encode("utf-8"), token)


def fidelity_report(store: StateStore, plan_id: str) -> dict[str, Any]:
    """Gate K's report on a plan: what it rests on, what was inherited/adapted/added/omitted and why, which conditions hold, and
    (after execution) what the runs and diagnostics showed."""
    plan = store.get_object(plan_id)
    if not plan or plan["object_type"] != "AnalysisPlan":
        raise KnowledgeError("PLAN_NOT_FOUND", f"{plan_id} is not an AnalysisPlan")
    c = plan["content"]
    pins = c.get("playbook_pins") or []
    rests_on = []
    for p in pins:
        oid = parse_pin(p)[0]
        pb = store.get_object(oid)
        rests_on.append({"playbook": p, "lineage": ((pb or {}).get("content", {}).get("playbook") or {}).get("lineage", []),
                         "elements": element_report(store, oid) if pb else []})
    by = {f: [s for s in c["steps"] if s["fidelity"] == f] for f in FIDELITY}
    report = {"plan": plan_id, "rests_on": rests_on,
              "inherited": [s["id"] for s in by["inherited"]],
              "adapted": [{"step": s["id"], "changes": s["changes"], "rationale": s["rationale"]} for s in by["adapted"]],
              "added": [{"step": s["id"], "rationale": s["rationale"], "basis": s["basis"]} for s in by["added"]],
              "omitted": [{"step": s["id"], "element": s.get("element"), "reason": s["omit_reason"], "replaced_by": s.get("replaced_by")} for s in by["omitted"]],
              "conditions": c.get("conditions_evaluation") or []}
    try:
        from research_harness.execution.dag import plan_execution_summary
        report["execution"] = plan_execution_summary(store, plan_id)
    except ImportError:  # pragma: no cover - the execution layer is optional here
        report["execution"] = None
    return report
