"""The capabilities a task plan can call: thin, named wrappers over what the suite already does (M5 WP3).

A capability takes resolved inputs and returns a JSON-serialisable output. It does not decide anything scientific: the model-backed ones
produce proposals exactly as they do from the command line, and the researcher's decisions are separate `decide` steps.
`pins` lists the stored objects (id, revision, digest) a result rests on, so a step is stale when one of them changes; `estimate` gives the
expected cost with its basis (never a promise).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any, Callable, Optional

from research_harness.core.canonical import canonical_json_hash
from research_harness.knowledge import corpus, literature, playbooks
from research_harness.knowledge.common import KnowledgeError
from research_harness.state import workflow_runs
from research_harness.state.store import StateStore
from research_harness.workflows import eca
from research_harness.workflows import reading_protocols as rp
from research_harness.workflows.instrumentation import estimate_tokens

DEFAULT_OUTPUT_TOKENS = 4000          # a reading-protocol answer, when nothing better is known
ADJUDICATION_TOKENS = (900, 500)      # input, output of one adjudication, when the registry has no history


@dataclass
class Ctx:
    store: StateStore
    token: Optional[str]
    adapter: Any
    task_id: str
    step_id: str


@dataclass(frozen=True)
class Capability:
    name: str
    version: str
    model: bool
    run: Callable[[Ctx, dict], dict]
    pins: Callable[[StateStore, dict], list]
    estimate: Callable[[StateStore, dict], dict]
    required: tuple = ()


def _need(inputs: dict, *names: str) -> None:
    missing = [n for n in names if inputs.get(n) in (None, "", [])]
    if missing:
        raise KnowledgeError("STEP_INPUT_MISSING", f"missing input(s): {', '.join(missing)}")


def _head_pin(store: StateStore, object_id: Optional[str]) -> list:
    o = store.get_object(object_id) if object_id else None
    return [[o["object_id"], o["revision"], o["revision_digest"]]] if o else []


def _est(calls: Optional[int], tin: Optional[int], tout: Optional[int], basis: str) -> dict:
    return {"llm_calls": calls, "input_tokens": tin, "output_tokens": tout, "basis": basis}


def _median_usage(store: StateStore, workflow: str) -> Optional[tuple[int, int]]:
    with store.read() as conn:
        rows = [r for r in workflow_runs.list_runs(conn, workflow=workflow, legacy=False) if r["execution_status"] == "SUCCEEDED"
                and (r.get("usage") or {}).get("llm_calls")]
    if len(rows) < 3:
        return None
    return (int(statistics.median(int((r["usage"].get("input_tokens") or 0)) for r in rows)),
            int(statistics.median(int((r["usage"].get("output_tokens") or 0)) for r in rows)))


# ------------------------------------------------------------------------------------------------ literature review

def _lit_run(ctx: Ctx, inp: dict) -> dict:
    _need(inp, "purpose", "sources")
    review = literature.run_literature(ctx.store, ctx.token, inp["purpose"], list(inp["sources"]), ctx.adapter)
    return {"purpose": inp["purpose"], "sources": list(inp["sources"]), "run_id": review.get("run_id"), "review": review}


def _lit_pins(store: StateStore, inp: dict) -> list:
    out = []
    for sid in inp.get("sources") or []:
        o = store.get_object(sid)
        if not o or o["object_type"] != "Source":
            raise KnowledgeError("STEP_INPUT_MISSING", f"{sid} is not a Source in this store")
        out.append([sid, o["revision"], o["content"].get("text_sha256")])
    return out


def _lit_estimate(store: StateStore, inp: dict) -> dict:
    protocol = literature.PURPOSES.get(inp.get("purpose") or "")
    if not protocol:
        return _est(None, None, None, "unknown_purpose")
    calls = tin = 0
    for sid in inp.get("sources") or []:
        if rp.extraction_status(store, sid, protocol)["cached"]:
            continue
        calls += 1
        tin += estimate_tokens(corpus.source_text(store, sid)[1])[0]
    return _est(calls, tin, calls * DEFAULT_OUTPUT_TOKENS, "heuristic: whole source text as input, a typical answer as output; cached extractions cost 0")


# ------------------------------------------------------------------------------------------------ playbook draft

def _pb_run(ctx: Ctx, inp: dict) -> dict:
    _need(inp, "method_review", "playbook_id", "label")
    review = inp["method_review"]
    review = review.get("review", review) if isinstance(review, dict) else review
    res = playbooks.draft_playbook_from_methods(ctx.store, ctx.token, review, playbook_id=inp["playbook_id"], label=inp["label"],
                                                scope=inp.get("scope"), exclusions=inp.get("exclusions"))
    return {"playbook_id": res["object_id"], "revision": res["revision"], "elements": res["elements"], "critical": res["critical"]}


# ------------------------------------------------------------------------------------------------ adjudication

def _adj_objects(inp: dict) -> Optional[set]:
    objs = inp.get("objects") or ([inp["playbook"]] if inp.get("playbook") else None)
    return set(objs) if objs else None


def _adj_run(ctx: Ctx, inp: dict) -> dict:
    res = eca.run_pending(ctx.store, ctx.token, ctx.adapter, rate=float(inp.get("rate", eca.STANDARD_SAMPLE_RATE)), limit=inp.get("limit"),
                          only_objects=_adj_objects(inp))
    return res


def _adj_pins(store: StateStore, inp: dict) -> list:
    return [p for oid in sorted(_adj_objects(inp) or []) for p in _head_pin(store, oid)]


def _adj_estimate(store: StateStore, inp: dict) -> dict:
    cov = eca.coverage_report(store, rate=float(inp.get("rate", eca.STANDARD_SAMPLE_RATE)))
    objs = _adj_objects(inp)
    todo = [s for s in cov["high_not_adjudicated"] + cov["sample_not_adjudicated"] if objs is None or s["object_id"] in objs]
    n = min(len(todo), inp["limit"]) if inp.get("limit") else len(todo)
    median = _median_usage(store, "eca_adjudication")
    tin, tout = median or ADJUDICATION_TOKENS
    return _est(n, n * tin, n * tout, "from_registry: median of past adjudications" if median else "heuristic: a typical adjudication")


# ------------------------------------------------------------------------------------------------ export, coverage

def _export_run(ctx: Ctx, inp: dict) -> dict:
    from pathlib import Path
    from research_harness.export.manifest import write_export
    from research_harness.export.methods import build_methods_export
    _need(inp, "playbook")
    exp = build_methods_export(ctx.store, inp["playbook"], inp.get("plan"))
    out = write_export(ctx.store, exp, Path(inp["out"]) if inp.get("out") else None, into_repo=bool(inp.get("into_repo")))
    return {k: out[k] for k in ("out", "closure_digest", "files", "not_available")}


def _export_pins(store: StateStore, inp: dict) -> list:
    if not inp.get("playbook") or not store.get_object(inp["playbook"]):
        return []
    from research_harness.export.methods import build_methods_export
    return [["closure", build_methods_export(store, inp["playbook"], inp.get("plan")).closure_digest]]


def _cov_run(ctx: Ctx, inp: dict) -> dict:
    return eca.coverage_report(ctx.store, rate=float(inp.get("rate", eca.STANDARD_SAMPLE_RATE)))


def _cov_pins(store: StateStore, inp: dict) -> list:
    return [["coverage", canonical_json_hash(eca.coverage_report(store, rate=float(inp.get("rate", eca.STANDARD_SAMPLE_RATE))))]]


_ZERO = lambda store, inp: _est(0, 0, 0, "deterministic")   # noqa: E731

CAPABILITIES: dict[str, Capability] = {c.name: c for c in (
    Capability("literature_review", "1", True, _lit_run, _lit_pins, _lit_estimate, ("purpose", "sources")),
    Capability("playbook_draft", "1", False, _pb_run, lambda s, i: [], _ZERO, ("method_review", "playbook_id", "label")),
    Capability("adjudicate_pending", "1", True, _adj_run, _adj_pins, _adj_estimate),
    Capability("export_methods", "1", False, _export_run, _export_pins, _ZERO, ("playbook",)),
    Capability("coverage_check", "1", False, _cov_run, _cov_pins, _ZERO),
)}
