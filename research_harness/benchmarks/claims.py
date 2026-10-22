"""Benchmark harness for claim extraction, evidence linking, relations and version alignment (M5 WP6.2).

What this module is: metric code over (system output, gold). What it is not: a benchmark. A benchmark needs gold judgements made independently of the system under test, by people who
followed written guidelines, with their disagreements kept and adjudicated. This module loads such gold, refuses gold that carries a model's output, and computes the metrics below.
Run on a small invented gold fixture it shows that the metric code is right; the result is labelled `SYNTHETIC_FIXTURE` and says nothing about any model.

Matching is reported in separate modes and never mixed:

* `exact`: identical character span;
* `overlap`: intersection over union of the two character spans at least `threshold`;
* `lexical`: the Jaccard overlap of the content words of the two texts at least `threshold`. This is a word-overlap measure, **not** semantic matching: a judgement of meaning needs a
  person or a judge model, and is not implemented here.

Metrics (no composite score is computed): claim precision/recall/F1; attribution and role accuracy with a confusion matrix; evidence span precision/recall and mean overlap; claim-evidence
link precision/recall; qualifier omission rate; multi-span evidence coverage; relation precision/recall; version-alignment error; unsupported-claim rate; and the cost figures (tokens per
source, tokens per confirmed claim, calls per source, cache reuse).
"""
from __future__ import annotations

import json
from typing import Any, Optional

from research_harness import taxonomy
from research_harness.core.canonical import canonical_json_hash
from research_harness.discovery import text_index as X
from research_harness.knowledge.common import KnowledgeError, register_run

BENCHMARK_WORKFLOW = "claim_benchmark"
MODES = ("exact", "overlap", "lexical")
TASKS = {1: "claim detection", 2: "claim type classification", 3: "own versus background attribution", 4: "evidence span identification", 5: "claim-evidence linking",
         6: "scope and qualifier preservation", 7: "claim decomposition", 8: "cross-paper relation identification", 9: "claim version alignment", 10: "recognition of already-addressed tensions"}
SYMMETRIC = ("COMPLEMENTS", "CONTRADICTS_WITHIN_SCOPE", "SAME_IDEA_PROPOSED")


# ------------------------------------------------------------------------------------------------ basic measures

def prf(tp: int, fp: int, fn: int) -> dict[str, Any]:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = (2 * p * r / (p + r) if (p + r) else 0.0) if p is not None and r is not None else None
    return {"tp": tp, "fp": fp, "fn": fn, "precision": _r(p), "recall": _r(r), "f1": _r(f)}


def _r(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(x, 4)


def iou(a: dict[str, Any], b: dict[str, Any]) -> float:
    inter = max(0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
    union = (a["end"] - a["start"]) + (b["end"] - b["start"]) - inter
    return inter / union if union else 0.0


def jaccard(a: str, b: str) -> float:
    sa, sb = set(X.tokenize(a)), set(X.tokenize(b))
    return len(sa & sb) / len(sa | sb) if sa | sb else 0.0


def similarity(a: dict[str, Any], b: dict[str, Any], mode: str) -> float:
    if mode == "lexical":
        return jaccard(a.get("text") or "", b.get("text") or "")
    return iou(a, b)


def match(pred: list[dict[str, Any]], gold: list[dict[str, Any]], mode: str = "overlap", threshold: float = 0.5) -> dict[str, Any]:
    """One-to-one matching, best similarity first (ties by index, so the result is reproducible). In `exact` mode a pair matches only if the spans are identical."""
    if mode not in MODES:
        raise KnowledgeError("MODE_UNKNOWN", f"mode must be one of {MODES}")
    cand = []
    for i, p in enumerate(pred):
        for j, g in enumerate(gold):
            s = similarity(p, g, mode)
            if (mode == "exact" and (p["start"], p["end"]) == (g["start"], g["end"])) or (mode != "exact" and s >= threshold):
                cand.append((-s, i, j))
    used_p: set[int] = set()
    used_g: set[int] = set()
    pairs = []
    for ns, i, j in sorted(cand):
        if i in used_p or j in used_g:
            continue
        used_p.add(i)
        used_g.add(j)
        pairs.append((i, j, -ns))
    return {"pairs": sorted(pairs), "tp": len(pairs), "fp": len(pred) - len(pairs), "fn": len(gold) - len(pairs), "mode": mode, "threshold": None if mode == "exact" else threshold}


def cohen_kappa(a: list[Any], b: list[Any]) -> Optional[float]:
    if len(a) != len(b) or not a:
        return None
    n = len(a)
    labels = sorted(set(a) | set(b), key=str)
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pe = sum((a.count(l) / n) * (b.count(l) / n) for l in labels)
    return None if pe == 1 else round((po - pe) / (1 - pe), 4)


# ------------------------------------------------------------------------------------------------ system output and gold in one shape

def claims_from_view(view: dict[str, Any], source_id: str) -> list[dict[str, Any]]:
    """The system's claims of one source as the metrics read them: character span of the wording, effective attribution and role, and evidence spans with their roles."""
    out = []
    for c in view["claims"]:
        if c.get("source_id") != source_id or not c.get("wording") or c["status"] == "REJECTED_BY_RESEARCHER":
            continue
        w = c["wording"]
        out.append({"id": c["id"], "start": w["char_start"], "end": w["char_end"], "text": c["text"], "attribution": c["attribution"].get("value"), "role": c["role"],
                    "evidence": [{"start": e["char_start"], "end": e["char_end"], "role": e["link_role"], "text": e["quote"]} for e in c["evidence"] if e["link_role"] != "WORDING"],
                    "granularity": c["granularity"], "components": [x["text"] for x in c["components"]]})
    return out


def load_gold(source: Any) -> dict[str, Any]:
    """Gold judgements, validated. Refused: gold that declares it was not made independently of the system, that names a model as annotator or carries a model's `generated_by`, and any
    record with a span or label outside the vocabularies. A missing file is not a gold set: callers report `DATA_UNAVAILABLE`."""
    g = json.loads(source) if isinstance(source, str) and source.lstrip().startswith("{") else (json.load(open(source, encoding="utf-8")) if isinstance(source, str) else source)
    ann = g.get("annotation") or {}
    if ann.get("independent_of_system_output") is not True:
        raise KnowledgeError("GOLD_NOT_INDEPENDENT", "gold must declare annotation.independent_of_system_output = true: it must be made without sight of the system's output")
    names = [str(a) for a in ann.get("annotators") or []]
    if not names:
        raise KnowledgeError("GOLD_NO_ANNOTATORS", "gold names its annotators (annotation.annotators)")
    if any(n.lower().startswith(("model:", "system:", "scripted", "llm")) for n in names):
        raise KnowledgeError("GOLD_FROM_MODEL_OUTPUT", "gold built from a model's own output is refused: it would measure the model against itself")
    if any(str(c.get("generated_by", "")).startswith("model:") for c in g.get("claims") or []):
        raise KnowledgeError("GOLD_FROM_MODEL_OUTPUT", "a gold claim carries generated_by = model:..., so it is not a human judgement")
    for c in g.get("claims") or []:
        if not (isinstance(c.get("start"), int) and isinstance(c.get("end"), int) and 0 <= c["start"] < c["end"]):
            raise KnowledgeError("GOLD_SPAN_INVALID", f"gold claim {c.get('id')} needs integer start < end")
        if c.get("attribution") and c["attribution"] not in taxonomy.vocabulary("claim_attribution"):
            raise KnowledgeError("GOLD_LABEL_UNKNOWN", f"attribution {c['attribution']!r} is not in the vocabulary")
        if c.get("role") and c["role"] not in taxonomy.vocabulary("claim_role"):
            raise KnowledgeError("GOLD_LABEL_UNKNOWN", f"role {c['role']!r} is not in the vocabulary")
    for r in g.get("relations") or []:
        if r["kind"] not in taxonomy.vocabulary("claim_relation_kind"):
            raise KnowledgeError("GOLD_LABEL_UNKNOWN", f"relation kind {r['kind']!r} is not in the vocabulary")
    for a in g.get("alignments") or []:
        if a["change"] not in taxonomy.vocabulary("claim_version_change"):
            raise KnowledgeError("GOLD_LABEL_UNKNOWN", f"change {a['change']!r} is not in the vocabulary")
    return {**g, "kind": ann.get("kind", "HUMAN_ANNOTATION")}


# ------------------------------------------------------------------------------------------------ tasks

def claim_detection(pred: list[dict[str, Any]], gold: list[dict[str, Any]], *, mode: str = "overlap", threshold: float = 0.5) -> dict[str, Any]:
    m = match(pred, gold, mode, threshold)
    return {**prf(m["tp"], m["fp"], m["fn"]), "mode": mode, "threshold": m["threshold"], "_pairs": m["pairs"]}


def label_eval(pred: list[dict[str, Any]], gold: list[dict[str, Any]], pairs: list[tuple], key: str) -> dict[str, Any]:
    """Accuracy and confusion matrix of `key` (attribution or role) over the claims that were matched; claims the system did not find are counted apart, not as errors of the label."""
    conf: dict[str, dict[str, int]] = {}
    right = 0
    for i, j, _ in pairs:
        g, p = gold[j].get(key), pred[i].get(key)
        if g is None:
            continue
        conf.setdefault(g, {}).setdefault(str(p), 0)
        conf[g][str(p)] += 1
        right += 1 if p == g else 0
    n = sum(sum(v.values()) for v in conf.values())
    per = {}
    for label in sorted(set(conf) | {k for v in conf.values() for k in v if k != "None"}):
        tp = conf.get(label, {}).get(label, 0)
        fn = sum(conf.get(label, {}).values()) - tp
        fp = sum(conf.get(g, {}).get(label, 0) for g in conf if g != label)
        per[label] = prf(tp, fp, fn)
    return {"n_matched_and_labelled": n, "accuracy": _r(right / n) if n else None, "confusion_gold_by_predicted": conf, "per_class": per,
            "gold_claims_not_found": len(gold) - len(pairs), "kappa_with_gold": cohen_kappa([gold[j].get(key) for _, j, _ in pairs if gold[j].get(key)], [pred[i].get(key) for i, j, _ in pairs if gold[j].get(key)])}


def evidence_eval(pred: list[dict[str, Any]], gold: list[dict[str, Any]], pairs: list[tuple], *, mode: str = "overlap", threshold: float = 0.5) -> dict[str, Any]:
    """Evidence spans and claim-evidence links, over the claims both sides have (a claim the system missed has no link to score; it is counted in the claim recall)."""
    tp = fp = fn = 0
    ious: list[float] = []
    multi_total = multi_found = multi_claims = multi_claims_all = 0
    qual_total = qual_omitted = 0
    for i, j, _ in pairs:
        pe, ge = pred[i].get("evidence") or [], gold[j].get("evidence") or []
        m = match(pe, ge, mode, threshold)
        tp, fp, fn = tp + m["tp"], fp + m["fp"], fn + m["fn"]
        ious += [s for _, _, s in m["pairs"]] if mode != "lexical" else []
        if len(ge) >= 2:
            multi_claims += 1
            multi_total += len(ge)
            multi_found += m["tp"]
            multi_claims_all += 1 if m["fn"] == 0 else 0
        for q in gold[j].get("qualifiers") or []:
            qual_total += 1
            if not any(iou(q, e) > 0 for e in pe):
                qual_omitted += 1
    return {"evidence_spans": prf(tp, fp, fn), "mean_overlap_of_matched_spans": _r(sum(ious) / len(ious)) if ious else None, "mode": mode, "threshold": None if mode == "exact" else threshold,
            "multi_span_claims": multi_claims, "multi_span_evidence_coverage": _r(multi_found / multi_total) if multi_total else None,
            "multi_span_claims_fully_covered": _r(multi_claims_all / multi_claims) if multi_claims else None,
            "qualifier_omission_rate": _r(qual_omitted / qual_total) if qual_total else None, "qualifiers_in_gold": qual_total}


def link_eval(pred: list[dict[str, Any]], gold: list[dict[str, Any]], pairs: list[tuple], *, mode: str = "overlap", threshold: float = 0.5) -> dict[str, Any]:
    """A link is a (claim, evidence span). A predicted link is right when its claim was matched and its span matches a gold span of that claim; a predicted link of an unmatched claim is wrong,
    and a gold link of an unmatched claim is missed."""
    matched_p = {i: j for i, j, _ in pairs}
    tp = fp = fn = 0
    for i, p in enumerate(pred):
        pe = p.get("evidence") or []
        if i not in matched_p:
            fp += len(pe)
            continue
        m = match(pe, gold[matched_p[i]].get("evidence") or [], mode, threshold)
        tp, fp, fn = tp + m["tp"], fp + m["fp"], fn + m["fn"]
    matched_g = set(matched_p.values())
    fn += sum(len(g.get("evidence") or []) for j, g in enumerate(gold) if j not in matched_g)
    return {**prf(tp, fp, fn), "mode": mode}


def alignment_eval(pred_al: list[dict[str, Any]], gold_al: list[dict[str, Any]], p2g: dict[str, str]) -> dict[str, Any]:
    """Version alignment. The error rate is the share of gold alignments the system got wrong or did not propose (a wrong class, or none); spurious proposals are counted apart."""
    gmap = {(a.get("earlier"), a.get("later")): a["change"] for a in gold_al}
    pmap: dict[tuple, str] = {}
    for a in pred_al:
        pmap[(p2g.get(a.get("earlier")) if a.get("earlier") else None, p2g.get(a.get("later")) if a.get("later") else None)] = a["change"]
    conf: dict[str, dict[str, int]] = {}
    wrong = 0
    for k, ch in gmap.items():
        got = pmap.get(k)
        conf.setdefault(ch, {}).setdefault(str(got), 0)
        conf[ch][str(got)] += 1
        wrong += 0 if got == ch else 1
    return {"gold_alignments": len(gmap), "version_alignment_error": _r(wrong / len(gmap)) if gmap else None, "confusion_gold_by_predicted": conf,
            "spurious_proposals": sum(1 for k in pmap if k not in gmap)}


def unsupported_claim_rate(view: dict[str, Any], source_id: str) -> dict[str, Any]:
    """Of the authors' own findings (empirical or interpretive), the share that rest on nothing beyond their own wording. From the system output alone: no gold needed."""
    need = [c for c in view["claims"] if c.get("source_id") == source_id and c["role"] in ("EMPIRICAL_FINDING", "INTERPRETIVE_FINDING") and c["attribution"].get("value") == "AUTHOR_OWN_CLAIM"
            and c["status"] != "REJECTED_BY_RESEARCHER"]
    bare = [c for c in need if len(c["evidence"]) <= 1]
    return {"own_findings": len(need), "with_nothing_beyond_wording": len(bare), "rate": _r(len(bare) / len(need)) if need else None}


def cost_metrics(view: dict[str, Any]) -> dict[str, Any]:
    cov = view["coverage"]
    n_src = max(1, cov["sources"])
    tokens = cov["input_tokens"] + cov["output_tokens"]
    confirmed = sum(1 for c in view["claims"] if c["status"] in ("CONFIRMED_BY_RESEARCHER", "CORRECTED_BY_RESEARCHER"))
    runs = sum(len(s["extraction"]["run_ids"]) for s in view["sources"])
    reused = sum(s["extraction"].get("reused_runs") or 0 for s in view["sources"])
    return {"sources": cov["sources"], "model_calls": cov["model_calls"], "calls_per_source": round(cov["model_calls"] / n_src, 2), "tokens": tokens,
            "tokens_per_source": round(tokens / n_src, 1), "claims": cov["claims"], "tokens_per_claim": round(tokens / cov["claims"], 1) if cov["claims"] else None,
            "confirmed_claims": confirmed, "tokens_per_confirmed_claim": round(tokens / confirmed, 1) if confirmed else None,
            "cache_reuse_rate": round(reused / runs, 4) if runs else None, "usage_source": next((s["extraction"]["usage"].get("usage_source") for s in view["sources"]), None)}


def evaluate_source(view: dict[str, Any], source_id: str, gold: dict[str, Any], *, mode: str = "overlap", threshold: float = 0.5) -> tuple[dict[str, Any], dict[str, str], list[dict], list[dict]]:
    """Every per-source task the gold supports. Returns the result, the map from the system's claim ids to gold claim ids, and the two claim lists."""
    pred = claims_from_view(view, source_id)
    g = gold["claims"]
    det = claim_detection(pred, g, mode=mode, threshold=threshold)
    pairs = det.pop("_pairs")
    res: dict[str, Any] = {"gold_kind": gold["kind"], "source_id": source_id, "mode": mode, "threshold": threshold if mode != "exact" else None, "claims_system": len(pred), "claims_gold": len(g),
                           "1_claim_detection": det}
    res["2_claim_type_classification"] = label_eval(pred, g, pairs, "role") if any(c.get("role") for c in g) else {"status": "DATA_UNAVAILABLE"}
    res["3_attribution"] = label_eval(pred, g, pairs, "attribution") if any(c.get("attribution") for c in g) else {"status": "DATA_UNAVAILABLE"}
    has_ev = any(c.get("evidence") for c in g)
    res["4_evidence_span_identification"] = evidence_eval(pred, g, pairs, mode=mode, threshold=threshold) if has_ev else {"status": "DATA_UNAVAILABLE"}
    res["5_claim_evidence_linking"] = link_eval(pred, g, pairs, mode=mode, threshold=threshold) if has_ev else {"status": "DATA_UNAVAILABLE"}
    res["6_scope_and_qualifier_preservation"] = ({"qualifier_omission_rate": res["4_evidence_span_identification"]["qualifier_omission_rate"], "qualifiers_in_gold": res["4_evidence_span_identification"]["qualifiers_in_gold"]}
                                                 if has_ev and any(c.get("qualifiers") for c in g) else {"status": "DATA_UNAVAILABLE"})
    res["7_claim_decomposition"] = _decomposition(pred, g, pairs) if any(c.get("components") for c in g) else {"status": "DATA_UNAVAILABLE"}
    res["unsupported_claim_rate"] = unsupported_claim_rate(view, source_id)
    return res, {pred[i]["id"]: g[j]["id"] for i, j, _ in pairs}, pred, g


def evaluate(view: dict[str, Any], golds: dict[str, dict[str, Any]], cross: Optional[dict[str, Any]] = None, *, mode: str = "overlap", threshold: float = 0.5) -> dict[str, Any]:
    """Every task the gold supports. `golds` maps a Source id to its gold (claims, attribution, role, evidence); `cross` holds the gold relations and alignments, written in gold claim ids.
    A task without gold is reported `DATA_UNAVAILABLE`, never computed from something else."""
    per: dict[str, Any] = {}
    p2g: dict[str, str] = {}
    for sid, gold in sorted(golds.items()):
        r, m, _, _ = evaluate_source(view, sid, gold, mode=mode, threshold=threshold)
        per[sid] = r
        p2g.update(m)
    out: dict[str, Any] = {"gold_kinds": sorted({g["kind"] for g in golds.values()} | ({cross["kind"]} if cross and cross.get("kind") else set())), "mode": mode,
                           "threshold": threshold if mode != "exact" else None, "sources": per}
    pred_rel = [{"kind": r["kind"], "from": r["from"]["claim"], "to": r["to"].get("claim")} for r in view["relations"]
                if r["to"].get("claim") and r["confirmation"]["state"] != "DISMISSED_BY_RESEARCHER"]
    if cross and cross.get("relations") is not None:
        # the gold relation endpoints are gold claim ids; the system's are mapped to them through the claim matching
        g2 = {gid: gid for gid in p2g.values()}
        mapped = [{"kind": r["kind"], "from": p2g.get(r["from"]), "to": p2g.get(r["to"])} for r in pred_rel]
        pk = {(m_["kind"], *(sorted([str(m_["from"]), str(m_["to"])]) if m_["kind"] in SYMMETRIC else [str(m_["from"]), str(m_["to"])])) for m_ in mapped if m_["from"] and m_["to"]}
        unmapped = sum(1 for m_ in mapped if not (m_["from"] and m_["to"]))
        gk = {(r["kind"], *(sorted([str(r["from"]), str(r["to"])]) if r["kind"] in SYMMETRIC else [str(r["from"]), str(r["to"])])) for r in cross["relations"]}
        out["8_cross_paper_relations"] = {**prf(len(pk & gk), len(pk - gk) + unmapped, len(gk - pk)), "unmapped_predicted": unmapped, "gold_claims_matched_by_system": len(g2)}
    else:
        out["8_cross_paper_relations"] = {"status": "DATA_UNAVAILABLE"}
    pred_al = [{"change": a["change"], "earlier": (a["earlier"] or {}).get("claim"), "later": (a["later"] or {}).get("claim")} for a in view["alignments"]
               if a["confirmation"]["state"] != "DISMISSED_BY_RESEARCHER"]
    out["9_claim_version_alignment"] = alignment_eval(pred_al, cross["alignments"], p2g) if cross and cross.get("alignments") is not None else {"status": "DATA_UNAVAILABLE"}
    out["10_recognition_of_addressed_tensions"] = {"status": "DATA_UNAVAILABLE", "note": "judged through inspection records (discovery/inspection.py) against the researcher's own list of tensions and the papers that address them"}
    out["cost"] = cost_metrics(view)
    out["no_composite_score"] = True
    return out


def _decomposition(pred: list[dict[str, Any]], gold: list[dict[str, Any]], pairs: list[tuple]) -> dict[str, Any]:
    tp = fp = fn = 0
    for i, j, _ in pairs:
        gc = [{"text": x["text"] if isinstance(x, dict) else x} for x in gold[j].get("components") or []]
        pc = [{"text": x} for x in pred[i].get("components") or []]
        m = match(pc, gc, "lexical", 0.5)
        tp, fp, fn = tp + m["tp"], fp + m["fp"], fn + m["fn"]
    return {**prf(tp, fp, fn), "mode": "lexical", "note": "components matched by word overlap, which is not a judgement of meaning"}


# ------------------------------------------------------------------------------------------------ several annotators

def compare_annotators(a: dict[str, Any], b: dict[str, Any], *, mode: str = "overlap", threshold: float = 0.5) -> dict[str, Any]:
    """Agreement of two annotators: claim detection treated as P/R between them, attribution and role kappa on the claims both found, and the list of disagreements to adjudicate."""
    ga, gb = a["claims"], b["claims"]
    m = match(ga, gb, mode, threshold)
    pairs = m["pairs"]
    dis: list[dict[str, Any]] = []
    for i, j, _ in pairs:
        for key in ("attribution", "role"):
            if ga[i].get(key) != gb[j].get(key):
                dis.append({"kind": f"{key}_differs", "a": ga[i]["id"], "b": gb[j]["id"], "a_value": ga[i].get(key), "b_value": gb[j].get(key)})
    matched_a, matched_b = {i for i, _, _ in pairs}, {j for _, j, _ in pairs}
    dis += [{"kind": "only_annotator_a", "a": ga[i]["id"], "b": None} for i in range(len(ga)) if i not in matched_a]
    dis += [{"kind": "only_annotator_b", "a": None, "b": gb[j]["id"]} for j in range(len(gb)) if j not in matched_b]
    return {"claim_agreement": prf(m["tp"], m["fp"], m["fn"]), "attribution_kappa": cohen_kappa([ga[i].get("attribution") for i, _, _ in pairs], [gb[j].get("attribution") for _, j, _ in pairs]),
            "role_kappa": cohen_kappa([ga[i].get("role") for i, _, _ in pairs], [gb[j].get("role") for _, j, _ in pairs]), "disagreements": dis, "mode": mode}


def adjudicate(a: dict[str, Any], b: dict[str, Any], decisions: list[dict[str, Any]], *, adjudicator: str, mode: str = "overlap", threshold: float = 0.5) -> dict[str, Any]:
    """Gold from two annotators and a third person's decisions. Agreement is taken as it stands; each disagreement needs a decision (`take`: a, b or neither) with a rationale, and one without
    stays UNRESOLVED and is left out of the gold, counted. Nothing is decided by the system."""
    cmp = compare_annotators(a, b, mode=mode, threshold=threshold)
    if not adjudicator or adjudicator.lower().startswith(("model:", "system:")):
        raise KnowledgeError("ADJUDICATOR_REQUIRED", "name the person who adjudicates")
    ga, gb = a["claims"], b["claims"]
    m = match(ga, gb, mode, threshold)
    chosen = {(d["kind"], d.get("a"), d.get("b")): d for d in decisions}
    out, unresolved = [], []
    for i, j, _ in m["pairs"]:
        c = dict(ga[i])
        for key in ("attribution", "role"):
            if ga[i].get(key) != gb[j].get(key):
                d = chosen.get((f"{key}_differs", ga[i]["id"], gb[j]["id"]))
                if not d or not str(d.get("rationale") or "").strip() or d.get("take") not in ("a", "b", "neither"):
                    unresolved.append({"kind": f"{key}_differs", "a": ga[i]["id"], "b": gb[j]["id"]})
                    c[key] = None
                else:
                    c[key] = ga[i].get(key) if d["take"] == "a" else gb[j].get(key) if d["take"] == "b" else None
        out.append(c)
    matched_a, matched_b = {i for i, _, _ in m["pairs"]}, {j for _, j, _ in m["pairs"]}
    for side, claims, used in (("a", ga, matched_a), ("b", gb, matched_b)):
        for idx, c in enumerate(claims):
            if idx in used:
                continue
            d = chosen.get((f"only_annotator_{side}", c["id"] if side == "a" else None, c["id"] if side == "b" else None))
            if d and d.get("take") == side and str(d.get("rationale") or "").strip():
                out.append(dict(c))
            elif not d or not str(d.get("rationale") or "").strip():
                unresolved.append({"kind": f"only_annotator_{side}", "id": c["id"]})
    return {"gold_version": "1", "kind": "HUMAN_ANNOTATION", "claims": out, "relations": a.get("relations"), "alignments": a.get("alignments"),
            "annotation": {"annotators": sorted({*a["annotation"]["annotators"], *b["annotation"]["annotators"]}), "independent_of_system_output": True, "adjudicator": adjudicator,
                           "agreement": {k: cmp[k] for k in ("claim_agreement", "attribution_kappa", "role_kappa")}, "disagreements": cmp["disagreements"], "decisions": decisions,
                           "unresolved": unresolved, "kind": "HUMAN_ANNOTATION"}}


def blank_form(source_id: str, text_sha256: str, sections: list[dict[str, Any]], *, annotator: Optional[str] = None) -> dict[str, Any]:
    """The empty annotation form for one source. The suite never fills it: the annotator marks claims, evidence, relations and alignments by character offset in the stored text, before seeing any
    system output (the form is built from the source and the vocabularies only)."""
    return {"form_version": "1", "status": "BLANK", "source_id": source_id, "text_sha256": text_sha256, "annotator": annotator,
            "instructions": ["Mark every claim the paper makes or reports, once, by character offsets (start inclusive, end exclusive) into the stored text of this source.",
                             "For each claim give who holds it (attribution) and what kind of claim it is (role). A sentence that describes earlier work is AUTHOR_REPORTS_PRIOR_WORK even if the authors agree with it.",
                             "List the passages that state the claim's conditions (qualifiers) and the passages offered as evidence; a claim may have several, or none.",
                             "Do this before looking at any system output. Record disagreements; an adjudicator decides them, not the system."],
            "vocabularies": {k: list(taxonomy.vocabulary(k)) for k in ("claim_attribution", "claim_role", "claim_evidence_link_role", "claim_relation_kind", "claim_version_change")},
            "sections": [{"heading": s.get("heading"), "label": s.get("label"), "char_start": s["char_start"], "char_end": s["char_end"]} for s in sections],
            "annotation": {"annotators": [annotator] if annotator else [], "independent_of_system_output": None, "guidelines_version": "claim-annotation-1"},
            "claims": [], "relations": [], "alignments": [],
            "claim_template": {"id": "G001", "start": None, "end": None, "attribution": None, "role": None, "evidence": [{"start": None, "end": None, "role": None}], "qualifiers": [{"start": None, "end": None}],
                               "components": [], "notes": ""}}


def record_results(store: Any, results: dict[str, Any], *, scope: str, note: str = "") -> str:
    """Put a benchmark result into the run registry (a blob, then the row that names it), so an export can include it and `export verify` can reproduce it."""
    body = {"scope": scope, "note": note, "results": results}
    return register_run(store, workflow=BENCHMARK_WORKFLOW, fingerprint=canonical_json_hash(body), executor="claim_benchmark@1", result=body, assessment="NOT_ASSESSED",
                        usage={"llm_calls": 0, "usage_source": "not_applicable"})
