"""Gate K tooling: is the method the system reconstructed the method the paper used? (plan v3, 6.2)

The order of work is the point of the gate:

1. `blank_annotation_form`: a form with the seven critical elements. The researcher (or a designated second reader) fills it
   from the paper and the replication package *before looking at any system output*.
2. `record_reference_annotation`: stores it, hashed, with the time it was stored. `blindness` compares that time with the
   first system output about the same sources (a reading-protocol run, a playbook, a plan). An annotation stored after any of
   them is not blind, and the gate refuses it.
3. `comparison_sheet`: reference and system side by side, with empty score fields.
4. `record_scores`: the researcher's scores, per element: MATCH, JUSTIFIED_ADAPTATION, PARTIAL or MISMATCH. The system never
   fills a reference answer or a score. A JUSTIFIED_ADAPTATION must correspond to an adapted step of the plan that carries its
   rationale; whether a MISMATCH is *flagged* is read from the plan, not declared.
5. `evaluate_gate_k`: K1 (fidelity report complete), K2 (every element MATCH or JUSTIFIED_ADAPTATION, zero unflagged MISMATCH,
   annotation blind), K3 (key outputs reproduce within tolerance), K4 (no SUPPORTED_WITHIN_SCOPE without a methodological basis,
   open criticisms listed).

`export_artifacts` writes the files that `validate_artifacts` (and the acceptance test) check.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from research_harness.knowledge import lifecycle, playbooks
from research_harness.knowledge.common import KnowledgeError, engine_origin
from research_harness.knowledge.replication import reproduce_package
from research_harness.state.store import StateStore

FORM_VERSION = "1"
SCORES = ("MATCH", "JUSTIFIED_ADAPTATION", "PARTIAL", "MISMATCH")
PASSING = ("MATCH", "JUSTIFIED_ADAPTATION")
ELEMENTS: dict[str, dict[str, Any]] = {
    "label_source_and_timing": {"question": "Where does the label (outcome) come from, and when is it known relative to the period it is used to predict?",
                                "playbook_categories": ["estimand"]},
    "sample_construction": {"question": "How was the sample constructed: population, period, filters, exclusions, missing-data handling?",
                            "playbook_categories": ["sample_construction"]},
    "estimand_or_prediction_target": {"question": "What is estimated or predicted, and in what form?", "playbook_categories": ["estimand"]},
    "specification_or_feature_set": {"question": "What model or specification, and which features or regressors?", "playbook_categories": ["specification"]},
    "standard_errors_or_validation_scheme": {"question": "How is uncertainty or out-of-sample performance assessed (standard errors, splits, folds, baselines)?",
                                             "playbook_categories": ["validation_scheme"]},
    "diagnostics": {"question": "Which diagnostics or checks does the paper run on its model, data or inference?", "playbook_categories": ["diagnostics"]},
    "interpretation_limits": {"question": "How do the authors say the results may and may not be interpreted?", "playbook_categories": ["interpretation"]},
}
INSTRUCTIONS = (
    "Fill this form from the paper and its replication package BEFORE you look at anything the system produced. For each element "
    "write what the authors did, in your own words, give the passage that shows it (quote and source id) or say that the paper does "
    "not state it (`not_stated` with a reason). Do not copy the system's wording. The time you store this form is recorded; a form "
    "stored after any system output about the same sources is not blind and will be refused.")


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def blank_annotation_form(source_ids: list[str], replication_package: Optional[str] = None) -> dict[str, Any]:
    return {"form_version": FORM_VERSION, "instructions": INSTRUCTIONS, "sources": list(source_ids), "replication_package": replication_package,
            "annotator": "", "second_reader": "",
            "elements": [{"id": eid, "question": e["question"], "reference": "", "source_id": "", "quote": "", "not_stated": False, "not_stated_reason": ""}
                         for eid, e in ELEMENTS.items()]}


def annotation_problems(annotation: dict[str, Any], store: StateStore) -> list[str]:
    problems: list[str] = []
    if not (annotation.get("annotator") or "").strip():
        problems.append("the annotator is not named")
    if annotation.get("form_version") != FORM_VERSION:
        problems.append(f"form_version must be {FORM_VERSION}")
    by_id = {e.get("id"): e for e in annotation.get("elements") or []}
    for eid in ELEMENTS:
        e = by_id.get(eid)
        if e is None:
            problems.append(f"element {eid} is missing")
            continue
        if e.get("not_stated"):
            if not (e.get("not_stated_reason") or "").strip():
                problems.append(f"{eid}: not_stated needs a reason")
            continue
        if not (e.get("reference") or "").strip():
            problems.append(f"{eid}: no reference answer (or mark it not_stated with a reason)")
        if e.get("quote"):
            from research_harness.knowledge.corpus import source_text
            from research_harness.workflows import quotes
            try:
                _, text = source_text(store, e.get("source_id") or "")
            except KnowledgeError:
                problems.append(f"{eid}: source {e.get('source_id')!r} has no stored text to check the quote against")
                continue
            if not quotes.find_quote(text, e["quote"]):
                problems.append(f"{eid}: the quote is not in the stored text of {e.get('source_id')}")
    return problems


def record_reference_annotation(store: StateStore, token: Optional[str], annotation: dict[str, Any], *, annotation_id: str = "REFANN_PILOT") -> dict[str, Any]:
    """Store the researcher's blind reference annotation (author or owner token). The system never calls this on its own."""
    problems = annotation_problems(annotation, store)
    if problems:
        raise KnowledgeError("ANNOTATION_INCOMPLETE", "; ".join(problems), problems=problems)
    raw = json.dumps(annotation, sort_keys=True, ensure_ascii=False).encode("utf-8")
    sha = store.artifacts.put_bytes(raw)
    with store.write_tx(token=token, require_roles=("author", "owner")) as tx:
        if tx.head(annotation_id):
            raise KnowledgeError("ANNOTATION_EXISTS", f"{annotation_id} already exists; a reference annotation is not rewritten after the fact")
        res = tx.add_revision(object_id=annotation_id, object_type="KnowledgeItem", edges=[], origin=engine_origin("reference_annotation", tx.actor_id),
                              content={"label": f"Reference annotation by {annotation['annotator']}", "kind": "reference_annotation", "generated_by": "researcher",
                                       "lifecycle": "ACTIVE", "body": {"annotation": annotation, "annotation_sha256": sha, "sources": annotation["sources"],
                                                                       "replication_package": annotation.get("replication_package"), "stored_at": tx.at}})
        tx.audit("reference_annotation", "RECORDED", {"annotation_id": annotation_id, "sha256": sha})
    return {"annotation_id": annotation_id, "revision": res["revision"], "sha256": sha}


def _system_outputs(store: StateStore, sources: list[str]) -> list[tuple[str, str]]:
    """(time, what) of every system output that concerns these sources: protocol runs, playbooks built from them, plans."""
    out: list[tuple[str, str]] = []
    with store.read() as conn:
        for r in conn.execute("SELECT created_at, workflow, input_revisions FROM workflow_runs WHERE workflow LIKE 'reading_protocol:%' OR workflow LIKE 'literature_review:%'"):
            if any(i.get("object_id") in sources for i in json.loads(r["input_revisions"] or "[]")):
                out.append((r["created_at"], r["workflow"]))
        for r in conn.execute("SELECT object_id FROM object_heads WHERE object_type IN ('MethodPlaybook','AnalysisPlan')"):
            first = conn.execute("SELECT created_at, content FROM objects WHERE object_id=? AND revision=1", (r[0],)).fetchone()
            c = json.loads(first["content"])
            lineage = [x["source_id"] for x in (c.get("playbook") or {}).get("lineage", [])]
            if any(s in sources for s in lineage) or c.get("playbook_pins"):
                out.append((first["created_at"], r[0]))
    return sorted(out, key=lambda x: _parse(x[0]))


def blindness(store: StateStore, annotation_id: str) -> dict[str, Any]:
    ann = store.get_object(annotation_id)
    if not ann or ann["content"].get("kind") != "reference_annotation":
        raise KnowledgeError("ANNOTATION_NOT_FOUND", f"{annotation_id} is not a reference annotation")
    stored = ann["content"]["body"]["stored_at"]
    outputs = _system_outputs(store, ann["content"]["body"]["sources"])
    earlier = [o for o in outputs if _parse(o[0]) <= _parse(stored)]
    later = [o for o in outputs if _parse(o[0]) > _parse(stored)]
    first_output = outputs[0] if outputs else None
    return {"annotation_stored_at": stored, "first_system_output": first_output[0] if first_output else None, "first_system_output_what": first_output[1] if first_output else None,
            "blind": not earlier, "system_outputs_before_annotation": [w for _, w in earlier], "system_outputs_after_annotation": len(later)}


def comparison_sheet(store: StateStore, annotation_id: str, playbook_id: str, plan_id: str) -> dict[str, Any]:
    """Reference and system side by side. The score and note fields are left empty for the researcher."""
    ann = store.get_object(annotation_id)["content"]["body"]["annotation"]
    pb = store.get_object(playbook_id)
    plan = store.get_object(plan_id)
    if not pb or pb["object_type"] != "MethodPlaybook" or not plan or plan["object_type"] != "AnalysisPlan":
        raise KnowledgeError("SYSTEM_OUTPUT_MISSING", "a MethodPlaybook and an AnalysisPlan are needed")
    steps = plan["content"]["steps"]
    rows = []
    for ref in ann["elements"]:
        cats = ELEMENTS[ref["id"]]["playbook_categories"]
        els = [e for e in pb["content"]["elements"] if e["category"] in cats]
        rows.append({"element": ref["id"], "question": ELEMENTS[ref["id"]]["question"],
                     "reference": ref["reference"] if not ref.get("not_stated") else f"(not stated: {ref['not_stated_reason']})", "reference_quote": ref.get("quote"),
                     "system": [{"element": e["id"], "text": e["text"], "basis": [b["ref"] for b in e.get("evidence_basis", [])],
                                 "plan_steps": [{"step": s["id"], "fidelity": s["fidelity"], "changes": s.get("changes"), "rationale": s.get("rationale"),
                                                 "omit_reason": s.get("omit_reason")} for s in steps if s.get("element") == e["id"]]} for e in els],
                     "score": "", "note": ""})
    return {"annotation_id": annotation_id, "playbook": playbook_id, "plan": plan_id, "allowed_scores": list(SCORES),
            "instruction": "Score each element yourself: MATCH, JUSTIFIED_ADAPTATION (the plan records an adapted step with its rationale), PARTIAL or MISMATCH. "
                           "Give a note for PARTIAL and MISMATCH. The system does not score itself.", "rows": rows}


def _flagged(plan: dict[str, Any], playbook: dict[str, Any], element: str) -> bool:
    cats = ELEMENTS[element]["playbook_categories"]
    ids = {e["id"] for e in playbook["content"]["elements"] if e["category"] in cats}
    return any(s.get("element") in ids and s["fidelity"] in {"adapted", "omitted"} and (s.get("rationale") or s.get("omit_reason")) for s in plan["content"]["steps"])


def record_scores(store: StateStore, token: Optional[str], annotation_id: str, playbook_id: str, plan_id: str, scores: list[dict[str, Any]], *,
                  scorer: str, scores_id: str = "GATEK_SCORES_PILOT") -> dict[str, Any]:
    """Store the researcher's element scores (author or owner token). Refused if the annotation is not blind or a score is not coherent
    with what the plan records."""
    blind = blindness(store, annotation_id)
    if not blind["blind"]:
        raise KnowledgeError("ANNOTATION_NOT_BLIND", "system output about these sources existed before the reference annotation was stored: "
                             + ", ".join(blind["system_outputs_before_annotation"]), blindness=blind)
    ann = store.get_object(annotation_id)["content"]["body"]["annotation"]
    allowed_scorers = {ann["annotator"], ann.get("second_reader") or ann["annotator"]}
    if scorer not in allowed_scorers:
        raise KnowledgeError("SCORER_NOT_DESIGNATED", f"scores come from the annotator or the designated second reader: {sorted(allowed_scorers)}")
    pb, plan = store.get_object(playbook_id), store.get_object(plan_id)
    by = {s.get("element"): s for s in scores}
    problems: list[str] = []
    out = []
    for eid in ELEMENTS:
        s = by.get(eid)
        if s is None or s.get("score") not in SCORES:
            problems.append(f"{eid}: a score in {SCORES} is needed")
            continue
        if s["score"] in {"PARTIAL", "MISMATCH"} and not (s.get("note") or "").strip():
            problems.append(f"{eid}: a {s['score']} needs a note saying what differs")
        ref = next(e for e in ann["elements"] if e["id"] == eid)
        has_system = any(e["category"] in ELEMENTS[eid]["playbook_categories"] for e in pb["content"]["elements"])
        if s["score"] in PASSING and not ref.get("not_stated") and not has_system:
            problems.append(f"{eid}: the paper states this and the playbook has no element for it, so it cannot be {s['score']}")
        if s["score"] in PASSING and ref.get("not_stated") and has_system:
            problems.append(f"{eid}: the paper does not state this but the playbook has an element for it; compare them and score PARTIAL or MISMATCH")
        flagged = _flagged(plan, pb, eid)
        if s["score"] == "JUSTIFIED_ADAPTATION" and not flagged:
            problems.append(f"{eid}: JUSTIFIED_ADAPTATION needs an adapted step with its rationale in the plan; the plan records none")
        out.append({"element": eid, "score": s["score"], "note": (s.get("note") or "").strip(), "flagged_in_fidelity_report": flagged})
    if problems:
        raise KnowledgeError("SCORES_INCOMPLETE", "; ".join(problems), problems=problems)
    with store.write_tx(token=token, require_roles=("author", "owner")) as tx:
        res = tx.add_revision(object_id=scores_id, object_type="KnowledgeItem", edges=[("depends_on", annotation_id, store.get_object(annotation_id)["revision"], {"origin": "gate_k"}),
                                                                                       ("depends_on", plan_id, plan["revision"], {"origin": "gate_k"})],
                              origin=engine_origin("gate_k_scores", tx.actor_id),
                              content={"label": f"Gate K scores by {scorer}", "kind": "evaluation_record", "generated_by": "researcher", "lifecycle": "ACTIVE",
                                       "body": {"type": "gate_k_scores", "scorer": scorer, "scores": out, "annotation_id": annotation_id, "playbook": playbook_id,
                                                "plan": plan_id, "scored_at": tx.at, "blindness": blind}})
    return {"scores_id": scores_id, "revision": res["revision"], "scores": out}


def evaluate_gate_k(store: StateStore, scores_id: str, *, package_id: Optional[str] = None, real_pilot: bool = False) -> dict[str, Any]:
    """K1-K4 computed from the stored annotation, scores, plan, playbook and package. `passed` only if all four hold."""
    sc = store.get_object(scores_id)["content"]["body"]
    pb, plan = store.get_object(sc["playbook"]), store.get_object(sc["plan"])
    blind = blindness(store, sc["annotation_id"])
    # K1: the fidelity report is complete for every plan step and every critical element
    k1_problems = playbooks.validate_steps(pb, plan["content"]["steps"])
    report = playbooks.fidelity_report(store, sc["plan"])
    k1 = not k1_problems and sum(len(report[k]) for k in ("inherited", "adapted", "added", "omitted")) == len(plan["content"]["steps"])
    # K2
    failing = [s for s in sc["scores"] if s["score"] not in PASSING]
    unflagged = [s["element"] for s in sc["scores"] if s["score"] == "MISMATCH" and not s["flagged_in_fidelity_report"]]
    k2 = len(sc["scores"]) == len(ELEMENTS) and not failing and not unflagged and blind["blind"]
    # K3
    repro = reproduce_package(store, package_id) if package_id else None
    k3 = bool(repro) and repro["computational_reproducibility"] == "REPRODUCED_WITHIN_TOLERANCE"
    # K4
    assessments = (pb["content"].get("validation") or {}).get("validity_assessments", [])
    without_basis = [a["context_key"][:8] for a in assessments if a["verdict"] == "SUPPORTED_WITHIN_SCOPE" and not any(b.get("methodological_literature") for b in a["evidence_basis"])]
    open_criticisms = [{"context": a["context_key"][:8], "ref": c.get("ref")} for a in assessments for c in a.get("known_criticisms", []) if c.get("status") == "open"]
    k4 = bool(assessments) and not without_basis
    result = {"gate": "K", "real_pilot": real_pilot, "scores_id": scores_id, "evaluated_at": store.clock(),
              "K1": {"passed": k1, "problems": k1_problems}, "K2": {"passed": k2, "not_passing": failing, "unflagged_mismatch": unflagged, "blind": blind},
              "K3": {"passed": k3, "reproduction": repro}, "K4": {"passed": k4, "assessments": len(assessments), "supported_without_methodological_basis": without_basis,
                                                                  "open_known_criticisms_listed": open_criticisms}}
    result["passed"] = all(result[k]["passed"] for k in ("K1", "K2", "K3", "K4"))
    return result


# --------------------------------------------------------------------------------------------------
# artefacts
# --------------------------------------------------------------------------------------------------

def _write(path: Path, data: Any) -> str:
    raw = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def export_artifacts(store: StateStore, scores_id: str, out_dir: Path | str, *, package_id: Optional[str] = None, real_pilot: bool = False) -> dict[str, Any]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    sc = store.get_object(scores_id)["content"]["body"]
    ann_obj = store.get_object(sc["annotation_id"])
    report = evaluate_gate_k(store, scores_id, package_id=package_id, real_pilot=real_pilot)
    files = {"reference_annotation.json": {"annotation": ann_obj["content"]["body"]["annotation"], "stored_at": ann_obj["content"]["body"]["stored_at"],
                                           "sha256": ann_obj["content"]["body"]["annotation_sha256"]},
             "scores.json": sc, "gate_k_report.json": report, "comparison_sheet.json": comparison_sheet(store, sc["annotation_id"], sc["playbook"], sc["plan"]),
             "fidelity_report.json": playbooks.fidelity_report(store, sc["plan"])}
    hashes = {name: _write(out / name, data) for name, data in files.items()}
    manifest = {"gate": "K", "real_pilot": real_pilot, "files": hashes, "annotation_stored_at": ann_obj["content"]["body"]["stored_at"],
                "first_system_output_at": report["K2"]["blind"]["first_system_output"], "passed": report["passed"], "project": store.project_id}
    _write(out / "manifest.json", manifest)
    return manifest


def validate_artifacts(directory: Path | str) -> dict[str, Any]:
    """Check an exported Gate K artefact directory: files match their hashes, the annotation precedes every system output,
    every element is scored, and K1-K4 hold. Returns `{ok, problems}`."""
    d = Path(directory)
    problems: list[str] = []
    try:
        manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "problems": ["manifest.json is missing or unreadable"]}
    for name, sha in manifest.get("files", {}).items():
        p = d / name
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != sha:
            problems.append(f"{name} is missing or does not match its recorded hash")
    if problems:
        return {"ok": False, "problems": problems}
    ann = json.loads((d / "reference_annotation.json").read_text(encoding="utf-8"))
    scores = json.loads((d / "scores.json").read_text(encoding="utf-8"))
    report = json.loads((d / "gate_k_report.json").read_text(encoding="utf-8"))
    if not manifest.get("real_pilot"):
        problems.append("the artefacts are not marked as a real pilot")
    if manifest.get("first_system_output_at") and _parse(ann["stored_at"]) >= _parse(manifest["first_system_output_at"]):
        problems.append("the reference annotation was not stored before the first system output")
    if hashlib.sha256(json.dumps(ann["annotation"], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest() != ann["sha256"]:
        problems.append("the annotation does not match its recorded hash")
    if sorted(s["element"] for s in scores["scores"]) != sorted(ELEMENTS):
        problems.append("not every critical element is scored")
    if any(s["score"] not in SCORES for s in scores["scores"]):
        problems.append("a score is outside the allowed values")
    for k in ("K1", "K2", "K3", "K4"):
        if not report.get(k, {}).get("passed"):
            problems.append(f"{k} does not hold")
    return {"ok": not problems, "problems": problems}
