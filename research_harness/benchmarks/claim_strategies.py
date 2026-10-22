"""Cost and coverage mechanics of three ways to read a paper for claims, measured offline (M5 WP6.2, phase E).

A  the reading protocols that exist (`RP_INTRO_ARGUMENT`, `RP_THEORY`, `RP_METHOD`, `RP_CONTRIBUTION`, `RP_LANDSCAPE`) run independently, each on the sections of its own dimension;
B  `RP_CLAIMS` once over every non-reference section, and the other protocols' records derived from the stored claims (`claim_views`), with no further call;
C  `RP_CLAIMS` per section group, then `RP_CLAIM_EVIDENCE` only for the claims flagged for a closer look, over spans chosen by words alone.

What is measured here, exactly: model calls; input and output tokens as the adapter reports them (a scripted adapter counts the prompt and the answer with the tokenizer named in the result, so
the figures are **estimates and never provider billing**); chunks; how many times the same characters are read; which characters and sections no run read; records by field or by role; caps
reached; claims flagged for follow-up; and how many fields of the older protocols a view can fill. What is not measured: coverage of the claims a paper really makes, fidelity of a restatement,
or latency. Those need a real model and independent human gold, and the answers here come from a fixed rule-based stand-in (`cue_reader`), not from a model: the counts of records it returns
follow its rules, so they must not be read as what a model would extract.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from research_harness.knowledge import claim_views as CV
from research_harness.knowledge import claims as CL
from research_harness.knowledge import corpus
from research_harness.state import workflow_runs
from research_harness.state.store import StateStore
from research_harness.workflows import reading_protocols as rp
from research_harness.workflows.adapters import ScriptedModelAdapter
from research_harness.workflows.instrumentation import estimate_tokens

BASELINE = ("RP_INTRO_ARGUMENT", "RP_THEORY", "RP_METHOD", "RP_CONTRIBUTION", "RP_LANDSCAPE")
CONDITIONS = ("A_independent_protocols", "B_single_pass_inventory", "C_section_aware_with_followup")
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\[0-9])")


def tokenizer_name() -> str:
    try:
        import tiktoken  # noqa: F401
        return "tiktoken:cl100k_base"
    except ImportError:
        return "heuristic: about 3.8 characters per token"


def sentences(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("[...]") or line[-1] not in ".!?)":
            continue                                           # a heading, a marker between spans, or a fragment
        out += [s.strip() for s in _SENT.split(line) if len(s.strip()) >= 20]
    return out


def _has(s: str, *words: str) -> bool:
    low = s.lower()
    return any(w in low for w in words)


def _claim_field(s: str) -> Optional[str]:
    if _has(s, "we define", "is defined as"):
        return "definition"
    if _has(s, "we assume"):
        return "assumption"
    if _has(s, "not been tested", "limitation", "do not show", "does not show"):
        return "limitation"
    if _has(s, "no account", "remains unclear", "open question"):
        return "gap_statement"
    if _has(s, "contribut"):
        return "claimed_contribution"
    if _has(s, "only when", "does not hold", "holds when"):
        return "boundary_condition"
    if _has(s, "students described", "themes"):
        return "interpretive_finding"
    if _has(s, "we find", "reaches", "produced", "thanked", "average precision", "effect number"):
        return "empirical_finding"
    if _has(s, "we argue", "we propose", "argues", "account", "should be", "proposition", "we add", "we also claim"):
        return "theoretical_proposition"
    if _has(s, "training used", "labels derive", "interviewed", "split", "sample"):
        return "methodological_claim"
    return None


def _attribution(s: str) -> str:
    chk = CL.cue_check(s, "AUTHOR_OWN_CLAIM")
    if chk["citation_cues"] and not chk["own_cues"]:
        return "AUTHOR_CRITIQUES_PRIOR_WORK" if _has(s, "however", "does not hold", "fails") else "AUTHOR_REPORTS_PRIOR_WORK"
    return "AUTHOR_OWN_CLAIM"


def cue_reader(model_id: str = "rule-based-stand-in") -> ScriptedModelAdapter:
    """A fixed set of rules that answers each protocol from the text it is shown. It is a stand-in so that prompts and answers have a realistic size; it is not a model."""
    def handler(system: str, user: str) -> Any:
        proto = re.search(r"\((RP_[A-Z_]+):", system).group(1)
        body = user.split("--- TEXT START ---\n", 1)[1].rsplit("\n--- TEXT END ---", 1)[0] if "--- TEXT START ---" in user else ""
        recs: list[dict[str, Any]] = []
        if proto == "RP_CLAIM_EVIDENCE":
            claim = next((ln for ln in user.splitlines() if ln.startswith("Claim:")), "")
            want = {w for w in re.findall(r"[a-z]{5,}", claim.lower())}
            for s in sentences(body):
                if len(want & set(re.findall(r"[a-z]{5,}", s.lower()))) >= 2:
                    recs.append({"field": "supporting_evidence", "value": "bears on the claim", "quote": s})
            return {"records": recs[:3], "gaps": []}
        for s in sentences(body):
            if proto == "RP_CLAIMS":
                f = _claim_field(s)
                if f:
                    r: dict[str, Any] = {"field": f, "value": s, "quote": s, "type": _attribution(s)}
                    cites = sorted({m.group(0) for p in CL._CITE[:2] for m in p.finditer(s)})
                    attrs: dict[str, Any] = {}
                    if cites:
                        attrs["cited_works"] = cites
                    if f == "empirical_finding":
                        attrs["direction"] = "descriptive"
                    if _has(s, "but only when") or (f == "theoretical_proposition" and _has(s, " and that ")):
                        attrs.update(granularity="COMPOUND", components=[{"text": s[:60], "conditions": []}])
                    if attrs:
                        r["attributes"] = attrs
                    recs.append(r)
            elif proto == "RP_LANDSCAPE":
                f = _claim_field(s)
                m = {"empirical_finding": ("finding", "descriptive"), "theoretical_proposition": ("theoretical_claim", "proposition"), "interpretive_finding": ("interpretive_finding", None),
                     "boundary_condition": ("boundary_condition", None), "assumption": ("identifying_assumption", "testing_not_stated"), "limitation": ("stated_limitation", None),
                     "gap_statement": ("open_question", None), "methodological_claim": ("design", None)}.get(f or "")
                if m:
                    r = {"field": m[0], "value": s, "quote": s, "label": "relationship"}
                    if m[1]:
                        r["type"] = m[1]
                    recs.append(r)
            elif proto == "RP_THEORY":
                if _has(s, "we define"):
                    recs.append({"field": "construct_definition", "value": s, "quote": s, "label": "construct"})
                elif _has(s, "we assume"):
                    recs.append({"field": "stated_assumption", "value": s, "quote": s})
            elif proto == "RP_INTRO_ARGUMENT":
                if CL.cue_check(s, "AUTHOR_OWN_CLAIM")["citation_cues"]:
                    recs.append({"field": "established_knowledge", "value": s, "quote": s})
                elif _has(s, "no account", "however"):
                    recs.append({"field": "unresolved_tension", "value": s, "quote": s})
                elif _has(s, "contribut"):
                    recs.append({"field": "proposed_contribution", "value": s, "quote": s})
            elif proto == "RP_CONTRIBUTION":
                if _has(s, "contribut"):
                    recs.append({"field": "claimed_contribution", "value": s, "quote": s})
            elif proto == "RP_METHOD":
                if _has(s, "training used", "interviewed", "sample", "labels derive"):
                    recs.append({"field": "sample_selection", "value": s, "quote": s})
                elif _has(s, "split", "testing", "average precision"):
                    recs.append({"field": "validation_or_tests", "value": s, "quote": s})
        return {"records": recs, "gaps": []}
    return ScriptedModelAdapter(model_id=model_id, custom_handler=handler)


def long_paper(title: str, sections: int = 6, per_section: int = 30) -> str:
    """An invented paper long enough to need real reading: numbered sections of invented findings, a few reports of earlier work, a definition, an assumption, a limitation."""
    parts = [f"{title}\n\nAbstract\nAn invented paper used to measure reading cost.\n"]
    for s in range(1, sections + 1):
        sents = []
        for i in range(per_section):
            k = s * 1000 + i
            if i == 0 and s == 1:
                sents.append("Alder (2018) argues that attention is a finite resource shared among the members of a group.")
            elif i == 1 and s == 1:
                sents.append("Previous studies have established that frequent messaging raises perceived responsiveness (Brandt, 2016; Corvo, 2019).")
            elif i == 2:
                sents.append(f"We define construct {k} as the degree to which members rely on shared channels in invented setting {s}.")
            elif i == 3:
                sents.append(f"We assume that assumption {k} holds for every member of the invented groups in section {s}.")
            elif i == 4 and s == sections:
                sents.append("The argument has not been tested with data beyond the invented groups.")
            else:
                sents.append(f"We find that effect number {k} of factor {i} is larger in invented sample {s} than in the comparison group.")
        parts.append(f"{s} {'Introduction' if s == 1 else 'Results' if s > 2 else 'Method' if s == 2 else 'Discussion'} of part {s}\n" + " ".join(sents) + "\n")
    parts.append("References\n[1] Invented Author. An Invented Reference. Journal of Invented Studies.\n")
    return "\n".join(parts)


# ------------------------------------------------------------------------------------------------ measuring a store

def _union_length(ranges: list[list[int]]) -> int:
    total, end = 0, -1
    for lo, hi in sorted(ranges):
        lo = max(lo, end)
        if hi > lo:
            total += hi - lo
            end = hi
    return total


def measure_store(store: StateStore, sources: dict[str, str]) -> dict[str, Any]:
    """From the run registry and the stored results: what every successful model run of a reading protocol cost and read, per source. `sources` maps Source id to a label."""
    per: dict[str, dict[str, Any]] = {sid: {"label": label, "calls": 0, "input_tokens": 0, "output_tokens": 0, "runs": 0, "reused_runs": 0, "chunks": 0, "ranges": [], "by_protocol": {},
                                              "records": Counter(), "rejected": Counter(), "cap_saturated": [], "over_cap": 0, "usage_source": None} for sid, label in sources.items()}
    with store.read() as conn:
        rows = [r for r in workflow_runs.list_runs(conn) if r["workflow"].startswith("reading_protocol:") and r["execution_status"] == "SUCCEEDED"]
    for r in sorted(rows, key=lambda r: r["run_id"]):
        sid = (r["input_revisions"] or [{}])[0].get("object_id")
        if sid not in per:
            continue
        p = per[sid]
        u = r.get("usage") or {}
        p["runs"] += 1
        proto = r["workflow"].split(":", 1)[1]
        if r.get("reused_from_run_id"):
            p["reused_runs"] += 1
            continue
        p["calls"] += u.get("llm_calls") or 0
        p["input_tokens"] += u.get("input_tokens") or 0
        p["output_tokens"] += u.get("output_tokens") or 0
        p["usage_source"] = u.get("usage_source") or p["usage_source"]
        res = json.loads(store.artifacts.get_bytes(r["artifact_sha256"]).decode("utf-8")) if r.get("artifact_sha256") else {}
        p["chunks"] += res.get("chunks") or 0
        p["ranges"] += res.get("ranges_read") or []
        bp = p["by_protocol"].setdefault(proto, {"calls": 0, "records": 0, "chars_read": 0})
        bp["calls"] += u.get("llm_calls") or 0
        bp["records"] += len(res.get("records") or [])
        bp["chars_read"] += res.get("chars_read") or 0
        for rec in res.get("records") or []:
            p["records"][f"{proto}.{rec['field']}"] += 1
        for rej in res.get("rejected") or []:
            p["rejected"][rej["reason"]] += 1
            p["over_cap"] += 1 if rej["reason"] == "over_cap" else 0
        p["cap_saturated"] += [f"{proto}.{f}" for f in res.get("cap_saturated") or []]
    out = {}
    for sid, p in per.items():
        obj, text = corpus.source_text(store, sid)
        secs = corpus.source_sections(store, obj, text)
        total = len(text)
        read_sum = sum(hi - lo for lo, hi in p["ranges"])
        uniq = _union_length(p["ranges"])
        covered = [s for s in secs if _union_length([[max(lo, s["char_start"]), min(hi, s["char_end"])] for lo, hi in p["ranges"] if hi > s["char_start"] and lo < s["char_end"]]) >= max(1, (s["char_end"] - s["char_start"]) // 2)]
        out[sid] = {"label": p["label"], "chars_total": total, "model_calls": p["calls"], "input_tokens": p["input_tokens"], "output_tokens": p["output_tokens"], "chunks": p["chunks"],
                    "runs": p["runs"], "reused_runs": p["reused_runs"], "chars_read_summed_over_runs": read_sum, "chars_read_at_least_once": uniq,
                    "reread_factor": round(read_sum / uniq, 2) if uniq else None, "share_of_text_never_read": round(1 - uniq / total, 4) if total else None,
                    "sections_never_read": [s.get("heading") or s["label"] for s in secs if s not in covered], "by_protocol": p["by_protocol"], "records": dict(sorted(p["records"].items())),
                    "rejected": dict(sorted(p["rejected"].items())), "records_set_aside_for_caps": p["over_cap"], "fields_at_cap": sorted(set(p["cap_saturated"])), "usage_source": p["usage_source"]}
    return out


def _fresh(tmp_dir: Path, name: str, papers: dict[str, str]) -> tuple[StateStore, str, dict[str, str]]:
    store, token = StateStore.create(Path(tmp_dir) / name, "PRJ_COST", owner_id="researcher")
    ids = {}
    for key, text in papers.items():
        ids[corpus.import_source(store, token, citation_key=key, title=key, year=2025, text=text)["object_id"]] = key
    return store, token, ids


def run_conditions(tmp_dir: Path | str, papers: dict[str, str], *, max_input_tokens: int = rp.DEFAULT_MAX_INPUT_TOKENS, model_id: str = "rule-based-stand-in") -> dict[str, Any]:
    """Conditions A, B and C on the same papers, each in a store of its own (so no cache leaks across them), then each repeated to measure what the cache saves."""
    out: dict[str, Any] = {"tokenizer": tokenizer_name(), "usage_note": "tokens are counted by the scripted adapter from the prompt and the answer with the tokenizer named here: estimates, never provider billing",
                           "answers_come_from": "a fixed rule-based stand-in, not a model", "not_measured": ["coverage of the claims a paper really makes", "fidelity of restatements", "latency"],
                           "papers": {k: len(t) for k, t in papers.items()}, "conditions": {},
                           "fixed_prompt_tokens_per_call": {pid: estimate_tokens(rp.build_system_prompt(rp.get_protocol(pid)))[0] for pid in (*BASELINE, "RP_CLAIMS", "RP_CLAIM_EVIDENCE")}}
    for cond in CONDITIONS:
        store, token, ids = _fresh(Path(tmp_dir), cond, papers)
        ad = cue_reader(model_id)
        flagged: dict[str, int] = {}
        derived: dict[str, Any] = {}
        inventory_id = None

        def go() -> Any:
            nonlocal inventory_id
            if cond == "A_independent_protocols":
                for sid in ids:
                    for pid in BASELINE:
                        rp.run_protocol(store, token, sid, pid, ad, max_input_tokens=max_input_tokens)
            else:
                b = CL.build(store, token, list(ids), ad, strategy="single_pass" if cond.startswith("B") else "section_aware", max_input_tokens=max_input_tokens)
                inventory_id = b["inventory_id"]
                if not flagged:                                      # claims flagged for a closer look, counted before the follow-up clears the flag
                    v0 = CL.view(store, inventory_id)
                    for sid in ids:
                        flagged[ids[sid]] = sum(1 for c in v0["claims"] if c.get("source_id") == sid and c["needs_followup"])
                if cond.startswith("C"):
                    CL.followup(store, token, inventory_id, ad, max_input_tokens=max_input_tokens)
        go()
        first = measure_store(store, ids)
        if inventory_id:
            v = CL.view(store, inventory_id)
            roles = Counter(c["role"] for c in v["claims"])
            attrs = Counter((c["attribution"].get("value") or "NOT_ASSIGNED") for c in v["claims"])
            derived = {p: {"records": len(CV.derive(v, p)["records"]), "fields_not_derivable": CV.derive(v, p)["fields_not_derivable"], "fields_partial": CV.derive(v, p)["fields_partial"]} for p in CV.VIEWS}
        else:
            roles, attrs = Counter(), Counter()
        go()                                                                      # the same work again: what the cache serves
        second = measure_store(store, ids)
        agg = lambda m, k: sum(x[k] for x in m.values())                          # noqa: E731
        out["conditions"][cond] = {
            "per_paper": {ids[sid]: first[sid] for sid in first},
            "totals": {"model_calls": agg(first, "model_calls"), "input_tokens": agg(first, "input_tokens"), "output_tokens": agg(first, "output_tokens"), "chunks": agg(first, "chunks"),
                       "chars_read_summed_over_runs": agg(first, "chars_read_summed_over_runs"), "chars_read_at_least_once": agg(first, "chars_read_at_least_once"),
                       "records_set_aside_for_caps": agg(first, "records_set_aside_for_caps")},
            "second_identical_pass": {"model_calls_added": agg(second, "model_calls") - agg(first, "model_calls"), "runs_served_from_cache": agg(second, "reused_runs"),
                                      "tokens_added": (agg(second, "input_tokens") + agg(second, "output_tokens")) - (agg(first, "input_tokens") + agg(first, "output_tokens"))},
            "claims_by_role": dict(sorted(roles.items())), "claims_by_attribution": dict(sorted(attrs.items())), "claims_flagged_for_followup": flagged, "views_derivable_without_a_call": derived}
        t = out["conditions"][cond]["totals"]
        t["reread_factor"] = round(t["chars_read_summed_over_runs"] / t["chars_read_at_least_once"], 2) if t["chars_read_at_least_once"] else None
        t["total_tokens"] = t["input_tokens"] + t["output_tokens"]
    return out
