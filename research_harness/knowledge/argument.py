"""Introduction Argument Analysis (plan v3, 3B): reconstruct the argument an Introduction makes, and compare it.

`analyze_introduction` runs reading protocol `RP_INTRO_ARGUMENT` on a stored Source and writes a `ContributionArgument`:
five components, each made of elements (statements) that carry their producer (`generated_by`) apart from their
evidence (a quote-verified span of the paper) and a status from the `intro_component_status` vocabulary.

* What the authors assert is `AUTHOR_ASSERTED`, always, whatever the paper (I11). A statement becomes `CORROBORATED` only
  through an adjudication that a researcher confirmed against a passage from a *different* source; `UNSUPPORTED` if an
  adjudication found it not supported; `CONTESTED` if a passage challenges it.
* `established_knowledge`, `unresolved_tension` and `proposed_contribution` are HIGH risk (ECA applies).
* Deterministic checks only report structure: a missing component, established knowledge with no external source, a
  tension that rests on the authors' word alone, a contribution typed `new_setting_only`.

`compare_contribution` sets the focus argument against the contribution records 3C extracted from the closest papers.
Whether a difference matters is a model judgement with its basis (`PROVISIONAL`) or the researcher's (`VERIFIED_WITHIN_SCOPE`),
never a deterministic result.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from jsonschema import Draft202012Validator

from research_harness import taxonomy
from research_harness.core.canonical import canonical_json_hash
from research_harness.knowledge.corpus import verify_passage
from research_harness.knowledge.common import (
    WRITE_ROLES, KnowledgeError, engine_origin, id_fragment, register_run, run_row, scope_id)
from research_harness.state import workflow_runs
from research_harness.state.store import StateStore
from research_harness.workflows import eca
from research_harness.workflows import reading_protocols as rp
from research_harness.workflows.adapters import ModelError, adapter_identity
from research_harness.workflows.model_io import parse_model_output
from research_harness.workflows.status import AssessmentStatus, apply_caps

COMPONENTS = ("phenomenon", "established_knowledge", "unresolved_tension", "proposed_contribution", "evidence_needed")
REQUIRED = ("phenomenon", "established_knowledge", "unresolved_tension", "proposed_contribution")
HIGH_COMPONENTS = frozenset({"established_knowledge", "unresolved_tension", "proposed_contribution"})
DIMENSIONS = ("phenomenon", "tension", "mechanism", "design", "setting", "measurement")
_DIM_OF_COMPONENT = {"phenomenon": "phenomenon", "unresolved_tension": "tension", "proposed_contribution": "mechanism"}
RELATIONS = ("same", "overlapping", "different", "not_stated")
CONTRIBUTION_FIELD_DIM = {"research_question": "phenomenon", "design": "design", "setting": "setting", "mechanism": "mechanism",
                          "claimed_contribution": "tension"}
EXECUTOR = "intro_argument@1"


def _element(component: str, n: int, rec: dict[str, Any], model_id: str) -> dict[str, Any]:
    e = {"id": f"{component}.{n}", "component": component, "text": rec["value"], "generated_by": f"model:{model_id}",
         "evidence_basis": [{"kind": "source_passage", "ref": rec["passage_id"], "char_start": rec["char_start"],
                             "char_end": rec["char_end"], "quote_verified": True}],
         "risk_tier": "HIGH" if component in HIGH_COMPONENTS else "STANDARD", "status": "AUTHOR_ASSERTED"}
    if rec.get("type"):
        e["contribution_type"] = rec["type"]
    return e


def review_flags(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    by = {c: [e for e in elements if e["component"] == c] for c in COMPONENTS}
    for c in REQUIRED:
        if not by[c]:
            flags.append({"flag": "MISSING_COMPONENT", "component": c})
    for e in by["established_knowledge"]:
        if e["status"] != "CORROBORATED":
            flags.append({"flag": "ESTABLISHED_KNOWLEDGE_WITHOUT_EXTERNAL_SOURCE", "element": e["id"],
                          "note": "the authors say so; no passage from another source has been adjudicated against it"})
    for e in by["unresolved_tension"]:
        if e["status"] != "CORROBORATED":
            flags.append({"flag": "TENSION_IS_THE_AUTHORS_ASSERTION_ONLY", "element": e["id"]})
    for e in by["proposed_contribution"]:
        if e.get("contribution_type") == "new_setting_only":
            flags.append({"flag": "NEW_SETTING_ONLY", "element": e["id"],
                          "note": "a new setting alone is not a theoretical contribution; say what the setting lets the study learn"})
    return flags


def analyze_introduction(store: StateStore, token: Optional[str], source_id: str, adapter: Any, *, argument_id: Optional[str] = None,
                         max_input_tokens: int = rp.DEFAULT_MAX_INPUT_TOKENS, reuse: bool = True) -> dict[str, Any]:
    """Reconstruct the Introduction argument of a stored Source and write it as a ContributionArgument."""
    ext = rp.run_protocol(store, token, source_id, "RP_INTRO_ARGUMENT", adapter, max_input_tokens=max_input_tokens, reuse=reuse)
    if ext["status"] == "FAILED":
        raise KnowledgeError("EXTRACTION_FAILED", f"{ext.get('reason')}: {ext.get('message')}")
    model = ext.get("model") or {}
    model_id = model.get("model_id") or model.get("provider") or "unknown"
    elements: list[dict[str, Any]] = []
    for comp in COMPONENTS:
        for n, rec in enumerate([r for r in ext["records"] if r["field"] == comp], 1):
            elements.append(_element(comp, n, rec, model_id))
    flags = review_flags(elements)
    missing = [f["component"] for f in flags if f["flag"] == "MISSING_COMPONENT"]
    status = "INSUFFICIENT_EVIDENCE" if missing else "REQUIRES_VERIFICATION"
    aid = scope_id(store, argument_id or f"CARG_{id_fragment(source_id, 50)}")
    content = {"label": f"Introduction argument of {source_id}", "subject": {"object_id": source_id, "revision": ext["source_revision"]},
               "components": {c: [e["id"] for e in elements if e["component"] == c] for c in COMPONENTS}, "elements": elements,
               "paper": {"source_id": source_id, "revision": ext["source_revision"]}, "status": status, "review_flags": flags,
               "protocol": ext["protocol"], "generated_by": f"model:{model_id}", "attributes": {"author_asserted": True}}
    with store.write_tx(token=token, require_roles=WRITE_ROLES) as tx:
        head = tx.head(aid)
        if head:
            cur = tx.get(aid)
            keep = {k: cur["content"].get(k) for k in ("differentiation", "comparison_run") if cur["content"].get(k)}
            if {k: v for k, v in cur["content"].items() if k not in keep} == content:
                return {"argument_id": aid, "revision": cur["revision"], "status": status, "review_flags": flags, "missing_components": missing,
                        "created": False, "extraction_run": ext["run_id"]}
            content = {**content, **keep}
        edges = [("derived_from", source_id, ext["source_revision"], {"origin": "intro_argument"})]
        edges += [("depends_on", r["passage_id"], r["revision"], {"origin": "intro_argument"}) for r in ext["records"]]
        res = tx.add_revision(object_id=aid, object_type="ContributionArgument", content=content, edges=edges,
                              origin=engine_origin("analyze_introduction", tx.actor_id, protocol=ext["protocol"]))
        tx.audit("analyze_introduction", "RECORDED", {"argument_id": aid, "status": status})
    return {"argument_id": aid, "revision": res["revision"], "status": status, "review_flags": flags, "missing_components": missing,
            "created": True, "extraction_run": ext["run_id"]}


def component_status(store: StateStore, argument_id: str, element_id: str) -> dict[str, Any]:
    """The `intro_component_status` of one element, from recorded adjudications and graph edges (never from wording)."""
    arg = store.get_object(argument_id)
    if not arg or arg["object_type"] != "ContributionArgument":
        raise KnowledgeError("ARGUMENT_NOT_FOUND", f"{argument_id} is not a ContributionArgument")
    el = next((e for e in arg["content"]["elements"] if e["id"] == element_id), None)
    if el is None:
        raise KnowledgeError("ELEMENT_UNKNOWN", f"{argument_id} has no element {element_id}")
    paper = arg["content"]["paper"]["source_id"]
    a = eca.statement_assessment(store, eca.resolve_statement(store, argument_id, element=element_id))
    external = False
    for adj_id in a["adjudications"]:
        adj = store.get_object(adj_id)["content"]
        if adj.get("confirms") and adj["eca_verdict"] in eca.SUPPORTING:
            p = store.get_object(adj["passage"]["object_id"])
            if p and p["content"].get("source_id") not in (None, paper):
                external = True
    if a["counter_evidence"]:
        status = "CONTESTED"
    elif a["eca_verdict"] == "NOT_SUPPORTED":
        status = "UNSUPPORTED"
    elif external and a["researcher_confirmed"]:
        status = "CORROBORATED"
    else:
        status = "AUTHOR_ASSERTED"
    taxonomy.validate_term("intro_component_status", status)
    return {"element": element_id, "status": status, "assessment": a["assessment"], "reasons": a["reasons"]}


# --------------------------------------------------------------------------------------------------
# contribution comparison (consumes 3C)
# --------------------------------------------------------------------------------------------------

_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "contribution_comparison", "type": "object",
    "properties": {"differentiation": {"type": "array", "items": {
        "type": "object",
        "properties": {"dimension": {"enum": list(DIMENSIONS)}, "ours": {"type": "string"}, "theirs": {"type": "string"},
                       "relation": {"enum": list(RELATIONS)}, "substantive_difference": {"type": "boolean"}, "reason": {"type": "string", "minLength": 1}},
        "required": ["dimension", "ours", "theirs", "relation", "reason"]}}, "gaps": {"type": "array", "items": {"type": "string"}}},
    "required": ["differentiation"], "additionalProperties": True}

_SYSTEM = (
    "You compare the argument of a focus study with what one earlier paper says about its own contribution. For each "
    "dimension you can speak to, name the focus element (`ours`: an element id from the focus list) and the earlier paper's "
    "passage (`theirs`: a passage id from the list), say how they relate (same, overlapping, different, or not_stated if the "
    "earlier paper says nothing on the dimension), and give the reason. Say `substantive_difference` only if the difference "
    "would change what the focus study contributes. You are giving a judgement with its basis; it is not a finding. "
    "Use only the ids you are given. Respond with exactly one JSON object that validates against this JSON Schema:\n"
    + json.dumps(_SCHEMA, indent=2, sort_keys=True))


def _focus_view(arg: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"id": e["id"], "dimension": _DIM_OF_COMPONENT.get(e["component"]), "component": e["component"], "text": e["text"]}
            for e in arg["content"]["elements"] if e["component"] in {"phenomenon", "unresolved_tension", "proposed_contribution"}]


def _verifies(store: StateStore, passage_id: str) -> bool:
    try:
        return verify_passage(store, passage_id)["source_match"] == "VERIFIED"
    except KnowledgeError:
        return False


def compare_contribution(store: StateStore, token: Optional[str], focus_argument_id: str, review: dict[str, Any], adapter: Any) -> dict[str, Any]:
    """Compare a focus ContributionArgument with the contribution records of a 3C contribution review (one model call per
    paper). Every cited passage must be one of that review's records and every cited element one of the focus argument's."""
    if review.get("purpose") != "contribution" or "contribution_records" not in review:
        raise KnowledgeError("NOT_A_CONTRIBUTION_REVIEW", "compare_contribution consumes the output of run_literature(purpose='contribution')")
    arg = store.get_object(focus_argument_id)
    if not arg or arg["object_type"] != "ContributionArgument":
        raise KnowledgeError("ARGUMENT_NOT_FOUND", f"{focus_argument_id} is not a ContributionArgument")
    focus = _focus_view(arg)
    focus_ids = {f["id"] for f in focus}
    model = adapter_identity(adapter)
    differentiation: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    calls = 0
    for paper in review["contribution_records"]:
        # only passages that exist in this store and still verify against their stored source can be cited
        passages = {r["passage_id"]: r for r in paper["records"] if _verifies(store, r["passage_id"])}
        if not passages:
            continue
        user = ("FOCUS STUDY ELEMENTS:\n" + "\n".join(f"[{f['id']}] ({f['component']}) {f['text']}" for f in focus)
                + f"\n\nEARLIER PAPER {paper['source_id']} (its own statements about itself):\n"
                + "\n".join(f"[{pid}] ({r['field']}) \"{r['quote']}\"" for pid, r in passages.items()))
        try:
            raw, _ = adapter.generate(_SYSTEM, user, expected_schema=_SCHEMA, temperature=0.0)
        except ModelError as exc:
            raise KnowledgeError("COMPARISON_FAILED", f"{paper['source_id']}: {type(exc).__name__}: {exc}") from exc
        calls += 1
        parsed, err = parse_model_output(raw)
        problems = [err] if parsed is None else [e.message[:120] for e in Draft202012Validator(_SCHEMA).iter_errors(parsed)]
        if parsed is None or problems:
            raise KnowledgeError("COMPARISON_INVALID_OUTPUT", f"{paper['source_id']}: {'; '.join(p for p in problems if p)}")
        for d in parsed["differentiation"]:
            if d["ours"] not in focus_ids:
                rejected.append({**d, "source_id": paper["source_id"], "reason_rejected": "cites an element that is not in the focus argument"})
                continue
            if d["theirs"] not in passages and d["relation"] != "not_stated":
                rejected.append({**d, "source_id": paper["source_id"], "reason_rejected": "cites a passage that is not in the contribution review"})
                continue
            p = passages.get(d["theirs"])
            differentiation.append({
                "dimension": d["dimension"], "ours": {"element": d["ours"], "text": next(f["text"] for f in focus if f["id"] == d["ours"])},
                "theirs": {"source_id": paper["source_id"], "passage_id": d["theirs"] if p else None, "quote": p["quote"] if p else None,
                           "char_start": p["char_start"] if p else None, "char_end": p["char_end"] if p else None},
                "relation": d["relation"], "model_says_substantive": bool(d.get("substantive_difference")), "reason": d["reason"],
                "basis": [{"kind": "source_passage", "ref": d["theirs"]}] if p else [],
                "substantive": "model_assessed", "generated_by": f"model:{model.get('model_id')}",
                "assessment": apply_caps(AssessmentStatus.PROVISIONAL, generated_by=f"model:{model.get('model_id')}").value})
    result = {"focus": focus_argument_id, "review_run": review.get("run_id"), "differentiation": differentiation, "rejected": rejected}
    run_fields = dict(workflow="contribution_comparison", fingerprint=canonical_json_hash(
        {"focus": focus_argument_id, "revision": arg["revision"], "review": review.get("run_id"), "model": model}),
        executor=EXECUTOR, usage={"llm_calls": calls}, model=model,
        upstream_run_ids=[review["run_id"]] if review.get("run_id") else [],
        input_revisions=[{"object_id": focus_argument_id, "revision": arg["revision"]}])
    # the argument revision names the run, and the run is registered in the same transaction (I22); a refused write leaves a FAILED run
    run = workflow_runs.new_run_id()
    row = run_row(store, result=result, run_id=run, **run_fields)
    try:
        _store_comparison(store, token, focus_argument_id, differentiation, run, row)
    except Exception as exc:
        try:
            register_run(store, execution="FAILED", context="PARTIAL", assessment="NOT_ASSESSED",
                         reason=f"write_refused:{getattr(exc, 'code', type(exc).__name__)}", **run_fields)
        except Exception:  # noqa: BLE001 - the original error is the one to report
            pass
        raise
    return {**result, "run_id": run, "llm_calls": calls}


def _store_comparison(store: StateStore, token: Optional[str], focus_argument_id: str, differentiation: list[dict[str, Any]], run: str,
                      row: dict[str, Any]) -> None:
    with store.write_tx(token=token, require_roles=WRITE_ROLES) as tx:
        cur = tx.get(focus_argument_id)
        rows = tx.conn.execute("SELECT relation,target_id,target_revision,provenance FROM edges WHERE source_id=? AND source_revision=?",
                               (focus_argument_id, cur["revision"])).fetchall()
        edges = [(r[0], r[1], r[2], json.loads(r[3])) for r in rows]
        edges += [("depends_on", d["theirs"]["passage_id"], tx.head(d["theirs"]["passage_id"])["head_revision"], {"origin": "contribution_comparison"})
                  for d in differentiation if d["theirs"]["passage_id"] and not any(e[1] == d["theirs"]["passage_id"] for e in edges)]
        tx.add_revision(object_id=focus_argument_id, object_type="ContributionArgument", edges=edges,
                        content={**cur["content"], "differentiation": differentiation, "comparison_run": run},
                        origin=engine_origin("compare_contribution", tx.actor_id))
        tx.fault("comparison_before_run")
        workflow_runs.insert_run_tx(tx, row)


def confirm_difference(store: StateStore, token: Optional[str], argument_id: str, index: int, *, substantive: bool, rationale: str) -> dict[str, Any]:
    """The researcher's judgement on one differentiation entry (author or owner). The judgement is theirs: it replaces the
    model's, and only then does the entry reach VERIFIED_WITHIN_SCOPE."""
    if len((rationale or "").strip()) < 10:
        raise KnowledgeError("RATIONALE_REQUIRED", "give the reason for the judgement (10 characters at least)")
    with store.write_tx(token=token, require_roles=("author", "owner")) as tx:
        cur = tx.get(argument_id)
        diff = list(cur["content"].get("differentiation") or [])
        if not 0 <= index < len(diff):
            raise KnowledgeError("ENTRY_UNKNOWN", f"{argument_id} has no differentiation entry {index}")
        entry = {**diff[index], "substantive": "researcher_confirmed", "researcher_says_substantive": substantive,
                 "researcher_rationale": rationale.strip(), "generated_by": "researcher",
                 "assessment": apply_caps(AssessmentStatus.VERIFIED_WITHIN_SCOPE, generated_by="researcher", researcher_confirmed=True).value}
        diff[index] = entry
        rows = tx.conn.execute("SELECT relation,target_id,target_revision,provenance FROM edges WHERE source_id=? AND source_revision=?",
                               (argument_id, cur["revision"])).fetchall()
        res = tx.add_revision(object_id=argument_id, object_type="ContributionArgument", content={**cur["content"], "differentiation": diff},
                              edges=[(r[0], r[1], r[2], json.loads(r[3])) for r in rows], origin=engine_origin("confirm_difference", tx.actor_id))
    return {"argument_id": argument_id, "revision": res["revision"], "entry": entry}
