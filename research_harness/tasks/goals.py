"""Goals: what a researcher can ask for, and the plan the suite proposes for it (M5 WP3).

A goal is data plus a small function from the researcher's parameters to an ordered list of steps; there is no agent loop. A step is either a
capability call (`run`) or a `decide` checkpoint that only the researcher can clear. A goal that needs capabilities the suite does not have
yet is listed as unavailable with what is missing, never planned with a stand-in (invariant I19: build a minimal version or stop and say so).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from research_harness.knowledge.common import KnowledgeError

GOAL_VERSION = "1"


def _run(step_id: str, capability: str, title: str, inputs: dict, depends_on: list[str], model: bool = False) -> dict[str, Any]:
    return {"step_id": step_id, "kind": "run", "capability": capability, "title": title, "inputs": inputs, "depends_on": depends_on, "model": model}


def _decide(step_id: str, decision: str, title: str, inputs: dict, depends_on: list[str]) -> dict[str, Any]:
    return {"step_id": step_id, "kind": "decide", "decision": decision, "title": title, "inputs": inputs, "depends_on": depends_on, "model": False}


def _need(params: dict, *names: str) -> None:
    missing = [n for n in names if params.get(n) in (None, "", [])]
    if missing:
        raise KnowledgeError("GOAL_PARAMS_MISSING", f"this goal needs: {', '.join(missing)}", missing=missing)


def _review_literature(p: dict) -> dict[str, Any]:
    _need(p, "sources")
    purpose = p.get("purpose", "theory")
    steps = [_decide("S01", "reading_list", "Confirm the sources to be read", {"sources": p["sources"], "purpose": purpose}, []),
             _run("S02", "literature_review", f"{purpose} review of the sources", {"purpose": purpose, "sources": p["sources"]}, ["S01"], model=True)]
    return {"steps": steps, "mandatory_checkpoints": ["S01"]}


def _evaluate_methods(p: dict) -> dict[str, Any]:
    _need(p, "sources", "playbook_id", "label")
    steps = [
        _decide("S01", "reading_list", "Confirm the sources to be read", {"sources": p["sources"], "purpose": "method"}, []),
        _run("S02", "literature_review", "Method review of the sources", {"purpose": "method", "sources": p["sources"]}, ["S01"], model=True),
        _run("S03", "playbook_draft", "Draft a playbook from the method review (EXTRACTED: nothing validated)",
             {"method_review": {"$step": "S02", "key": "review"}, "playbook_id": p["playbook_id"], "label": p["label"],
              "scope": p.get("scope"), "exclusions": p.get("exclusions")}, ["S02"]),
        _run("S04", "adjudicate_pending", "Adjudicate the playbook's statements the coverage policy asks for (proposals only)",
             {"playbook": {"$step": "S03", "key": "playbook_id"}, "limit": p.get("limit")}, ["S03"], model=True),
        _decide("S05", "adjudications", "Confirm or reject each proposed adjudication (research adjudicate sheet / confirm-batch)",
                {"playbook": {"$step": "S03", "key": "playbook_id"}}, ["S04"]),
        _run("S06", "export_methods", "Write the readable Methods export", {"playbook": {"$step": "S03", "key": "playbook_id"}, "plan": p.get("plan"),
                                                                           "out": p.get("out")}, ["S05"]),
    ]
    return {"steps": steps, "mandatory_checkpoints": ["S01", "S05"]}


def _assess_claims(p: dict) -> dict[str, Any]:
    objects = p.get("objects")
    steps = [
        _run("S01", "coverage_check", "Where the adjudication coverage policy stands", {}, []),
        _run("S02", "adjudicate_pending", "Adjudicate what the coverage policy still asks for (proposals only)", {"objects": objects, "limit": p.get("limit")}, ["S01"], model=True),
        _decide("S03", "adjudications", "Confirm or reject each proposed adjudication", {"objects": objects}, ["S02"]),
        _run("S04", "coverage_check", "Coverage after your decisions", {}, ["S03"]),
    ]
    return {"steps": steps, "mandatory_checkpoints": ["S03"]}


def _build_landscape(p: dict) -> dict[str, Any]:
    _need(p, "retrieval_id")
    rid = p["retrieval_id"]
    steps = [
        _decide("S01", "reading_list", "Confirm the reading list of the retrieval run (set aside, add, acquire: research retrieval review)", {"retrieval_id": rid}, []),
        _run("S02", "landscape_build", "Read the reading list and build the landscape (entries are quote-verified; nothing is decided)",
             {"retrieval_id": rid, "versions": p.get("versions", "retrieved"), "works": p.get("works"), "aliases": p.get("aliases"), "label": p.get("label"),
              "allow_stale_corpus": bool(p.get("allow_stale_corpus"))}, ["S01"], model=True),
        _decide("S03", "landscape_review", "Review the suite's inferences (contradictions, version differences, differing definitions) and any later version that was not read",
                {"landscape": {"$step": "S02", "key": "landscape_id"}}, ["S02"]),
        _run("S04", "export_landscape", "Write the readable landscape export", {"landscape": {"$step": "S02", "key": "landscape_id"}, "out": p.get("out")}, ["S03"]),
    ]
    return {"steps": steps, "mandatory_checkpoints": ["S01", "S03"]}


@dataclass(frozen=True)
class Goal:
    name: str
    description: str
    planner: Optional[Callable[[dict], dict[str, Any]]]
    needs: tuple = ()                # parameters the planner requires
    missing: tuple = ()              # capabilities that do not exist yet (for an unavailable goal)


GOALS: dict[str, Goal] = {g.name: g for g in (
    Goal("review_literature", "Review stored sources for one purpose (theory, method or contribution)", _review_literature, ("sources",)),
    Goal("evaluate_methods", "From sources to a drafted playbook, its adjudications and a readable export", _evaluate_methods,
         ("sources", "playbook_id", "label")),
    Goal("assess_claims", "Adjudicate the high-impact statements the coverage policy asks for", _assess_claims),
    Goal("build_landscape", "From a current retrieval run to a landscape of the literature (entries with passages, evidence dimensions, suite inferences) and its export",
         _build_landscape, ("retrieval_id",)),
    Goal("discover_contribution", "From a problem to checked contribution candidates", None,
         missing=("candidate generation (WP7)", "adversarial verification (WP8)")),
    Goal("analyze_results", "From an analysis plan to results and claim changes", None,
         missing=("a packaged plan-run step with a working directory (planned with the Results export)",)),
)}


def plan_for(goal: str, params: dict[str, Any]) -> dict[str, Any]:
    g = GOALS.get(goal)
    if g is None:
        raise KnowledgeError("GOAL_UNKNOWN", f"goal must be one of {sorted(GOALS)}")
    if g.planner is None:
        raise KnowledgeError("GOAL_NOT_AVAILABLE", f"{goal} needs capabilities the suite does not have yet: " + "; ".join(g.missing), missing=list(g.missing))
    plan = g.planner(dict(params))
    plan["planner"] = f"{goal}@{GOAL_VERSION}"
    return plan
