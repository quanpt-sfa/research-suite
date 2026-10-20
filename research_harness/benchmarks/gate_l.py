"""Gate L tooling: does a library built on Study A make Study B cheaper without making it worse? (plan v3, 6.3)

The design is a counterfactual on the *same* study. Study B is run twice, **cold** (empty library) and **warm** (the library
after Study A, its adjudications and its promotions). Difficulty and rubric are therefore identical. Study C is in the same
subfield with an incompatible scope and measures false transfer.

* `declare_rubric` stores the rubric, the gold annotations and the tolerances, hashed and timestamped. A run that started
  before the rubric was declared is refused by `compare`.
* The task families T1-T5 are scored by the functions below, from outputs and gold sets. Effort is read from the run registry
  (model calls, commands, tokens with their basis), not typed in; manual steps are the researcher's own count and are reported
  as such.
* `compare` evaluates L1-L4 from two run records and the Study C result; L5 (a real paper with real code in Study A) is
  checked separately and a synthetic run can never satisfy it.

`run_synthetic_gate_l` runs the whole procedure on invented material with scripted model answers. It checks the machinery.
The real evaluation uses the researcher's papers and is recorded as artefacts (`export_artifacts` / `validate_artifacts`).
"""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

from research_harness.knowledge import closure, corpus, lifecycle, literature, playbooks
from research_harness.knowledge.common import KnowledgeError, engine_origin
from research_harness.knowledge.library import create_library, pin
from research_harness.state.store import StateStore
from research_harness.workflows import eca, quotes
from research_harness.workflows.adapters import ScriptedModelAdapter

FAMILIES = ("T1", "T2", "T3", "T4", "T5")
QUALITY_FAMILIES = ("T1", "T2", "T3", "T4")


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


# --------------------------------------------------------------------------------------------------
# task-family scores (pure)
# --------------------------------------------------------------------------------------------------

def prf(tp: int, retrieved: int, gold: int) -> dict[str, float]:
    p = tp / retrieved if retrieved else 0.0
    r = tp / gold if gold else 0.0
    return {"precision": p, "recall": r, "f1": (2 * p * r / (p + r)) if p + r else 0.0}


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> float:
    inter = max(0, min(a[1], b[1]) - max(a[0], b[0]))
    return inter / max(1, min(a[1] - a[0], b[1] - b[0]))


def score_retrieval(gold_spans: Sequence[tuple[str, int, int]], retrieved_spans: Sequence[tuple[str, int, int]]) -> dict[str, Any]:
    """T1: spans `(source, start, end)` retrieved against gold spans; a retrieved span counts if it overlaps a gold span of the same source by half."""
    hit_gold = {i for i, g in enumerate(gold_spans) for r in retrieved_spans if r[0] == g[0] and _overlap((g[1], g[2]), (r[1], r[2])) >= 0.5}
    good = [r for r in retrieved_spans if any(r[0] == g[0] and _overlap((g[1], g[2]), (r[1], r[2])) >= 0.5 for g in gold_spans)]
    return {**prf(len(good), len(retrieved_spans), len(gold_spans)), "gold": len(gold_spans), "retrieved": len(retrieved_spans)}


def score_extraction(gold: Sequence[dict[str, Any]], records: Sequence[dict[str, Any]], texts: dict[str, str]) -> dict[str, Any]:
    """T2: field-level. A gold item `{source, field, quote}` is matched by a record of the same source and field whose span overlaps the quote's."""
    def span(source: str, quote: str) -> Optional[tuple[int, int]]:
        return quotes.find_quote(texts[source], quote)
    matched = 0
    used: set[int] = set()
    for g in gold:
        gs = span(g["source"], g["quote"])
        for i, r in enumerate(records):
            if i in used or r["source"] != g["source"] or r["field"] != g["field"] or gs is None:
                continue
            if _overlap(gs, (r["char_start"], r["char_end"])) >= 0.5:
                used.add(i)
                matched += 1
                break
    return {**prf(matched, len(records), len(gold)), "gold": len(gold), "records": len(records)}


def score_method_selection(gold: dict[str, Any], rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """T3: the top-ranked playbook must be in the gold set and carry the gold status for the project's context."""
    top = rows[0] if rows else None
    correct = bool(top) and top["item_id"] in gold["chosen"] and top["status"] == gold["status"]
    return {"score": 1.0 if correct else 0.0, "top": top["item_id"] if top else None, "status": top["status"] if top else None, "gold": gold}


def score_fidelity(scores: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """T4: the share of critical elements scored MATCH or JUSTIFIED_ADAPTATION (the scores are Gate K's)."""
    ok = sum(1 for s in scores if s["score"] in ("MATCH", "JUSTIFIED_ADAPTATION"))
    return {"score": ok / len(scores) if scores else 0.0, "elements": len(scores), "passing": ok}


def count_inference_errors(store: StateStore, extra_flags: Sequence[dict[str, Any]] = ()) -> dict[str, Any]:
    """T5: adjudicated statements the check found overgeneral, with a dropped condition, contradicted or not supported, plus the researcher's
    blind flags. Counted from the adjudication records."""
    errors = []
    with store.read() as conn:
        for r in conn.execute("SELECT object_id FROM object_heads WHERE object_type='Adjudication' ORDER BY object_id"):
            c = store.get_object(r[0], conn=conn)["content"]
            if c.get("kind") != "evidence_claim" or c.get("confirms"):
                continue
            if c.get("guard") or c["eca_verdict"] in ("NOT_SUPPORTED", "UNRESOLVED") or c.get("dropped_conditions"):
                errors.append({"adjudication": r[0], "verdict": c["eca_verdict"], "guard": c.get("guard"), "dropped": c.get("dropped_conditions")})
    return {"score": len(errors) + len(extra_flags), "from_adjudications": errors, "researcher_flags": list(extra_flags)}


# --------------------------------------------------------------------------------------------------
# effort, from the registry
# --------------------------------------------------------------------------------------------------

def effort_from_registry(store: StateStore, *, manual_steps: Optional[int] = None) -> dict[str, Any]:
    """Model calls, reused runs, commands, tokens with their basis and elapsed time, all read from `workflow_runs`."""
    calls = reused = commands = 0
    tokens = {"input": 0, "output": 0}
    basis: dict[str, int] = {}
    first = last = None
    with store.read() as conn:
        rows = [dict(r) for r in conn.execute("SELECT workflow, created_at, usage, reused_from_run_id, status_reason FROM workflow_runs ORDER BY rowid")]
    for r in rows:
        u = json.loads(r["usage"] or "{}")
        calls += int(u.get("llm_calls") or 0)
        if r["reused_from_run_id"] or r["status_reason"] in ("reused", "reused_imported_passages"):
            reused += 1
        if r["workflow"] == "execution":
            commands += 1
        for k in ("input", "output"):
            v = u.get(f"{k}_tokens")
            if isinstance(v, int):
                tokens[k] += v
                basis[u.get("usage_source") or "unknown"] = basis.get(u.get("usage_source") or "unknown", 0) + v
        t = _parse(r["created_at"])
        first, last = min(first or t, t), max(last or t, t)
    return {"model_calls": calls, "reused_runs": reused, "commands": commands, "tokens": {**tokens, "basis": basis},
            "elapsed_seconds": (last - first).total_seconds() if first else 0.0, "registry_runs": len(rows),
            "manual_steps": manual_steps, "manual_steps_basis": "the researcher's own count" if manual_steps is not None else "not counted",
            "effort_units": calls + commands}


# --------------------------------------------------------------------------------------------------
# rubric, records, comparison
# --------------------------------------------------------------------------------------------------

def declare_rubric(store: StateStore, token: Optional[str], rubric: dict[str, Any], *, rubric_id: str = "GATEL_RUBRIC") -> dict[str, Any]:
    """Store the rubric (gold sets and tolerances) before any run, hashed and timestamped. It is never rewritten."""
    need = {"tolerance": set(QUALITY_FAMILIES), "gold": set(FAMILIES) - {"T5"}}
    for key, fams in need.items():
        missing = sorted(fams - set(rubric.get(key) or {}))
        if missing:
            raise KnowledgeError("RUBRIC_INCOMPLETE", f"the rubric needs {key} for {missing}")
    raw = json.dumps(rubric, sort_keys=True, ensure_ascii=False).encode("utf-8")
    sha = store.artifacts.put_bytes(raw)
    with store.write_tx(token=token, require_roles=("author", "owner")) as tx:
        if tx.head(rubric_id):
            raise KnowledgeError("RUBRIC_EXISTS", f"{rubric_id} is already declared; a rubric is not rewritten after the fact")
        tx.add_revision(object_id=rubric_id, object_type="KnowledgeItem", edges=[], origin=engine_origin("gate_l_rubric", tx.actor_id),
                        content={"label": "Gate L rubric", "kind": "evaluation_record", "generated_by": "researcher", "lifecycle": "ACTIVE",
                                 "body": {"type": "gate_l_rubric", "rubric": rubric, "sha256": sha, "declared_at": tx.at}})
    return {"rubric_id": rubric_id, "sha256": sha}


def run_record(label: str, *, started_at: str, scores: dict[str, Any], effort: dict[str, Any], reuse: dict[str, bool]) -> dict[str, Any]:
    if set(scores) != set(FAMILIES):
        raise KnowledgeError("RUN_RECORD_INCOMPLETE", f"a run record scores every family {FAMILIES}")
    return {"label": label, "started_at": started_at, "scores": scores, "effort": effort, "reuse": reuse}


def false_transfer(store: StateStore, context: dict[str, Any]) -> dict[str, Any]:
    """Study C: what the library offers for an incompatible context. Anything not marked `related_not_applicable` is a false transfer;
    every excluded item is listed with its reason."""
    rows = lifecycle.match(store, context) + [
        {**r, "object_type": "MethodPlaybook"} for r in playbooks.select_playbooks(store, context)]
    seen, excluded, leaked = set(), [], []
    for r in rows:
        if r["item_id"] in seen:
            continue
        seen.add(r["item_id"])
        (excluded if r["status"] == "related_not_applicable" else leaked).append({"item": r["item_id"], "status": r["status"], "because": r["because"]})
    return {"offered": len(seen), "false_transfers": len(leaked), "leaked": leaked, "excluded_with_reasons": excluded}


def compare(rubric: dict[str, Any], rubric_declared_at: str, cold: dict[str, Any], warm: dict[str, Any], study_c: dict[str, Any],
            real_evidence: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """L1-L5, computed from the two run records, the Study C result and the real-evidence record."""
    declared = _parse(rubric_declared_at)
    before = [r["label"] for r in (cold, warm) if _parse(r["started_at"]) < declared]
    tol = rubric["tolerance"]
    reuse_fams = [f for f in FAMILIES if warm["reuse"].get(f)]
    l1_detail = {}
    for f in reuse_fams:
        l1_detail[f] = {"cold_units": cold["effort"].get("family_units", {}).get(f, cold["effort"]["effort_units"]),
                        "warm_units": warm["effort"].get("family_units", {}).get(f, warm["effort"]["effort_units"])}
    l1 = bool(reuse_fams) and all(v["warm_units"] < v["cold_units"] for v in l1_detail.values())
    l2_detail = {f: {"cold": cold["scores"][f]["score"], "warm": warm["scores"][f]["score"], "tolerance": tol[f],
                     "no_worse": warm["scores"][f]["score"] >= cold["scores"][f]["score"] - tol[f]} for f in QUALITY_FAMILIES}
    l2 = all(d["no_worse"] for d in l2_detail.values())
    l3 = warm["scores"]["T5"]["score"] <= cold["scores"]["T5"]["score"]
    l4 = study_c["false_transfers"] == 0 and study_c["offered"] > 0 and all(e["because"] for e in study_c["excluded_with_reasons"])
    real = real_evidence or {}
    l5 = bool(real.get("real_paper") and real.get("real_code") and real.get("paper_source") and real.get("package"))
    out = {"gate": "L", "rubric_declared_before_runs": not before, "runs_started_before_rubric": before,
           "L1": {"passed": l1, "families_with_reuse": reuse_fams, "effort": l1_detail},
           "L2": {"passed": l2, "families": l2_detail}, "L3": {"passed": l3, "cold": cold["scores"]["T5"]["score"], "warm": warm["scores"]["T5"]["score"]},
           "L4": {"passed": l4, "false_transfers": study_c["false_transfers"], "offered": study_c["offered"], "excluded_with_reasons": study_c["excluded_with_reasons"]},
           "L5": {"passed": l5, "evidence": real or "no real paper with real code is recorded for Study A"}}
    out["passed"] = out["rubric_declared_before_runs"] and all(out[k]["passed"] for k in ("L1", "L2", "L3", "L4", "L5"))
    return out


# --------------------------------------------------------------------------------------------------
# artefacts
# --------------------------------------------------------------------------------------------------

def _write(path: Path, data: Any) -> str:
    raw = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def export_artifacts(out_dir: Path | str, *, rubric: dict[str, Any], rubric_declared_at: str, cold: dict[str, Any], warm: dict[str, Any], study_c: dict[str, Any],
                     real_evidence: Optional[dict[str, Any]], real: bool, study: str) -> dict[str, Any]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    report = compare(rubric, rubric_declared_at, cold, warm, study_c, real_evidence)
    files = {"declared_rubric.json": {"rubric": rubric, "declared_at": rubric_declared_at}, "cold_run.json": cold, "warm_run.json": warm, "study_c.json": study_c,
             "gate_l_report.json": report}
    hashes = {name: _write(out / name, data) for name, data in files.items()}
    manifest = {"gate": "L", "real": real, "study": study, "files": hashes, "passed": report["passed"]}
    _write(out / "manifest.json", manifest)
    return manifest


def validate_artifacts(directory: Path | str) -> dict[str, Any]:
    """Check an exported Gate L artefact directory: hashes match, the rubric precedes both runs, cold and warm are matched on the same study,
    Study C and the real-evidence record are present, and L1-L5 hold."""
    d = Path(directory)
    try:
        manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "problems": ["manifest.json is missing or unreadable"]}
    problems = [f"{n} is missing or does not match its recorded hash" for n, h in manifest.get("files", {}).items()
                if not (d / n).is_file() or hashlib.sha256((d / n).read_bytes()).hexdigest() != h]
    if problems:
        return {"ok": False, "problems": problems}
    if not manifest.get("real"):
        problems.append("the artefacts are not marked as a real evaluation")
    rub = json.loads((d / "declared_rubric.json").read_text(encoding="utf-8"))
    cold, warm = (json.loads((d / n).read_text(encoding="utf-8")) for n in ("cold_run.json", "warm_run.json"))
    report = json.loads((d / "gate_l_report.json").read_text(encoding="utf-8"))
    for r in (cold, warm):
        if _parse(r["started_at"]) < _parse(rub["declared_at"]):
            problems.append(f"the {r['label']} run started before the rubric was declared")
    if set(cold["scores"]) != set(FAMILIES) or set(warm["scores"]) != set(FAMILIES):
        problems.append("a run does not score every task family")
    for k in ("L1", "L2", "L3", "L4", "L5"):
        if not report.get(k, {}).get("passed"):
            problems.append(f"{k} does not hold")
    return {"ok": not problems, "problems": problems}


# --------------------------------------------------------------------------------------------------
# the synthetic run (CI)
# --------------------------------------------------------------------------------------------------

PAPER_A = ("SYNTHETIC GATE L PAPER A. Invented for the benchmark.\n\nAbstract\nWe study restatement prediction.\n\n1 Introduction\nRestatements are costly. "
           "We contribute by testing a model across industries.\n\n3 Experimental setup\nWe drop firms with fewer than three consecutive annual observations. "
           "The outcome label equals one when the firm later filed an amended annual report. Models are validated by training on earlier years and testing on later years.\n\n"
           "4 Results\nAverage precision is low.\n\nReferences\n[1] A reference entry.\n")
PAPER_B = ("SYNTHETIC GATE L PAPER B. Invented for the benchmark.\n\nAbstract\nWe propose out-of-time validation.\n\n1 Introduction\nRestatements are hard to predict. "
           "Our contribution is an out-of-time validation design.\n\n3 Research design\nWe keep firms with at least five years of data. "
           "The outcome label equals one when a restated report follows within three years. Models are validated by training on years before the test year only.\n\n"
           "4 Results\nAverage precision falls.\n\nReferences\n[1] A reference entry.\n")
TEXTS = {"SRC_GLA2026": ("gla2026", PAPER_A), "SRC_GLB2026": ("glb2026", PAPER_B)}
GOLD_RECORDS = [
    {"source": "SRC_GLA2026", "field": "sample_selection", "quote": "drop firms with fewer than three consecutive annual observations"},
    {"source": "SRC_GLA2026", "field": "label_construction", "quote": "The outcome label equals one when the firm later filed an amended annual report"},
    {"source": "SRC_GLA2026", "field": "validation_or_tests", "quote": "Models are validated by training on earlier years and testing on later years"},
    {"source": "SRC_GLB2026", "field": "sample_selection", "quote": "keep firms with at least five years of data"},
    {"source": "SRC_GLB2026", "field": "label_construction", "quote": "The outcome label equals one when a restated report follows within three years"},
    {"source": "SRC_GLB2026", "field": "validation_or_tests", "quote": "Models are validated by training on years before the test year only"}]
CTX_B = {"domain": "accounting", "subfield": "archival_financial_reporting", "study_design": "predictive_modeling",
         "estimand": "probability of a later restatement", "measurement": "label = amended annual report"}
CTX_C = {"domain": "accounting", "subfield": "archival_financial_reporting", "study_design": "predictive_modeling", "estimand": "probability of a later restatement",
         "measurement": "label = amended annual report", "conditions": ["label known only after the prediction date"]}
SCOPE = {"study_design": "predictive_modeling", "subfield": "archival_financial_reporting"}


def _extractor() -> ScriptedModelAdapter:
    def handler(system: str, user: str) -> dict[str, Any]:
        src = re.search(r"\((?:LIB_)?(SRC_[A-Z0-9_]+) revision", user).group(1)
        proto = re.search(r"\((RP_[A-Z_]+):", system).group(1)
        recs = [{"field": g["field"], "quote": g["quote"], "value": g["field"].replace("_", " ")} for g in GOLD_RECORDS if g["source"] == src] if proto == "RP_METHOD" else []
        return {"records": recs, "gaps": []}
    return ScriptedModelAdapter(model_id="gate-l-extractor", custom_handler=handler)


def _adjudicator() -> ScriptedModelAdapter:
    return ScriptedModelAdapter(model_id="gate-l-adjudicator", default_response={
        "passage_proposition": "As quoted.", "equivalence": "EQUIVALENT", "dropped_conditions": [], "cue_handling": [], "verdict": "SUPPORTED_AS_STATED", "required_qualification": []})


def _study_b_run(store: StateStore, token: str, label: str, source_ids: list[str], texts: dict[str, str], *, adjudicate: bool) -> dict[str, Any]:
    started = store.clock()
    ex = _extractor()
    review = literature.run_literature(store, token, "method", source_ids, ex)
    records, spans = [], []
    for mr in review["method_records"]:
        for fld, recs in mr["fields"].items():
            for r in recs:
                records.append({"source": mr["source_id"], "field": fld, "char_start": r["char_start"], "char_end": r["char_end"]})
                if fld == "label_construction":
                    spans.append((mr["source_id"], r["char_start"], r["char_end"]))
    gold_t2 = [{**g, "source": g["source"] if g["source"] in texts else "LIB_" + g["source"]} for g in GOLD_RECORDS]
    gold_spans = [(g["source"], *quotes.find_quote(texts[g["source"]], g["quote"])) for g in gold_t2 if g["field"] == "label_construction"]
    existing = [r for r in playbooks.select_playbooks(store, CTX_B)]
    if not existing:   # cold: nothing to reuse, so a playbook is drafted from the extraction
        playbooks.draft_playbook_from_methods(store, token, review, playbook_id="PB_STUDYB", label="Study B playbook", scope=SCOPE)
    if adjudicate:     # the researcher's work on every HIGH element, scripted here: one adjudication call each
        pb_id = "PB_STUDYB"
        for e in store.get_object(pb_id)["content"]["elements"]:
            r = eca.adjudicate(store, token, eca.resolve_statement(store, pb_id, element=e["id"]), e["evidence_basis"][0]["ref"], _adjudicator())
            eca.confirm(store, token, r["adjudication_id"], verdict="SUPPORTED_AS_STATED", rationale="Reads its passage correctly.")
    rows = playbooks.select_playbooks(store, CTX_B)
    pb = rows[0]
    els = store.get_object(pb["item_id"])["content"]["elements"]
    quote_of = {e["id"]: store.get_object(e["evidence_basis"][0]["ref"])["content"]["quote"] for e in els}
    scores_t4 = []
    for gid, cats in (("sample", ["sample_construction"]), ("label", ["estimand"]), ("validation", ["validation_scheme"])):
        mine = [e for e in els if e["category"] in cats]
        gold_quotes = [g["quote"] for g in GOLD_RECORDS if g["field"] == {"sample": "sample_selection", "label": "label_construction", "validation": "validation_or_tests"}[gid]]
        ok = mine and all(any(quotes.normalize_whitespace(quote_of[e["id"]]) == quotes.normalize_whitespace(gq) for gq in gold_quotes) for e in mine)
        scores_t4.append({"element": gid, "score": "MATCH" if ok else "MISMATCH"})
    t5 = count_inference_errors(store)
    effort = effort_from_registry(store, manual_steps=None)
    effort["family_units"] = {"T2": review["extraction"]["model_calls"], "T3": effort["model_calls"] - review["extraction"]["model_calls"] + effort["commands"]}
    scores = {"T1": score_retrieval(gold_spans, spans), "T2": score_extraction(gold_t2, records, texts),
              "T3": score_method_selection({"chosen": ["PB_STUDYB", "LIB_PB_STUDYA"], "status": "applicable"}, rows), "T4": score_fidelity(scores_t4), "T5": t5}
    scores["T1"]["score"], scores["T2"]["score"] = scores["T1"]["f1"], scores["T2"]["f1"]   # the family score is the F1 of its precision and recall
    reuse = {"T2": review["extraction"]["reused"] == len(source_ids), "T3": bool(existing) or False}
    return run_record(label, started_at=started, scores=scores, effort=effort, reuse=reuse)


def run_synthetic_gate_l(work_dir: Optional[Path] = None) -> dict[str, Any]:
    """The whole Gate L procedure on invented material. Study A builds and promotes a playbook in a library; Study B runs cold and warm;
    Study C asks the library for an incompatible context."""
    tmp = tempfile.TemporaryDirectory() if work_dir is None else None
    root = Path(work_dir) if work_dir is not None else Path(tmp.name)  # type: ignore[union-attr]
    root.mkdir(parents=True, exist_ok=True)
    try:
        home = root / "home"
        # --- the rubric is declared first
        rubric_store, rtoken = StateStore.create(home, "GATEL_EVAL", owner_id="researcher")
        rubric = {"tolerance": {"T1": 0.02, "T2": 0.02, "T3": 0.0, "T4": 0.0}, "gold": {"T1": "label_construction spans", "T2": GOLD_RECORDS, "T3": {"chosen": ["PB_STUDYB", "LIB_PB_STUDYA"], "status": "applicable"},
                  "T4": "sample, label and validation elements match the gold quotes"}, "effort": "model calls + commands (computed from the run registry)",
                  "scorer_note": "synthetic: T4 is scored against the gold quotes by a script; the real evaluation is scored by the researcher"}
        declared = declare_rubric(rubric_store, rtoken, rubric)
        declared_at = rubric_store.get_object("GATEL_RUBRIC")["content"]["body"]["declared_at"]

        # --- Study A, in the library: extract, adjudicate, validate, promote
        lib, ltoken = create_library(home)
        for sid, (key, text) in TEXTS.items():
            corpus.import_source(lib, ltoken, citation_key=key, text=text)
        lsrc = ["LIB_" + s for s in TEXTS]
        review_a = literature.run_literature(lib, ltoken, "method", lsrc, _extractor())
        playbooks.draft_playbook_from_methods(lib, ltoken, review_a, playbook_id="PB_STUDYA", label="Study A playbook", scope=SCOPE, exclusions=["label known only after the prediction date"])
        for e in lib.get_object("LIB_PB_STUDYA")["content"]["elements"]:
            r = eca.adjudicate(lib, ltoken, eca.resolve_statement(lib, "LIB_PB_STUDYA", element=e["id"]), e["evidence_basis"][0]["ref"], _adjudicator())
            eca.confirm(lib, ltoken, r["adjudication_id"], verdict="SUPPORTED_AS_STATED", rationale="Reads its passage correctly.")
        ref = lib.get_object("LIB_PB_STUDYA")["content"]["elements"][0]["evidence_basis"][0]["ref"]
        lifecycle.validate(lib, ltoken, "LIB_PB_STUDYA", assessment={"context": CTX_B, "confirmed_by_researcher": True,
                                                                     "evidence_basis": [{"kind": "source_passage", "ref": ref, "methodological_literature": True}]})
        lifecycle.promote(lib, ltoken, "LIB_PB_STUDYA", scope=SCOPE, exclusions=["label known only after the prediction date"])

        # --- Study B, cold: an empty library
        cold_store, ctoken = StateStore.create(home, "STUDYB_COLD", owner_id="researcher")
        for sid, (key, text) in TEXTS.items():
            corpus.import_source(cold_store, ctoken, citation_key=key, text=text)
        cold = _study_b_run(cold_store, ctoken, "cold", list(TEXTS), {s: t[1] for s, t in TEXTS.items()}, adjudicate=True)

        # --- Study B, warm: the library after Study A
        warm_store, wtoken = StateStore.create(home, "STUDYB_WARM", owner_id="researcher")
        closure.use_in_project(warm_store, wtoken, lib, pin(lib, "LIB_PB_STUDYA"))
        warm = _study_b_run(warm_store, wtoken, "warm", lsrc, {"LIB_" + s: t[1] for s, t in TEXTS.items()}, adjudicate=False)

        # --- Study C: the same subfield, an incompatible scope
        study_c = false_transfer(warm_store, CTX_C)
        allowed = false_transfer(warm_store, CTX_B)      # the same library for a compatible study does transfer: C is not empty because nothing ever transfers
        report = compare(rubric, declared_at, cold, warm, study_c, real_evidence=None)
        return {"benchmark": "synthetic_gate_l", "rubric": declared, "cold": cold, "warm": warm, "study_c": study_c, "compatible_context_for_comparison": {
                    "offered": allowed["offered"], "transferred": allowed["false_transfers"]}, "report": report,
                "synthetic_machinery_ok": all(report[k]["passed"] for k in ("L1", "L2", "L3", "L4")) and report["rubric_declared_before_runs"],
                "real_gate_passed": report["passed"], "statement": "This checks the machinery on invented material. L5 needs a real paper with real code, so a synthetic run never passes Gate L."}
    finally:
        if tmp is not None:
            tmp.cleanup()
