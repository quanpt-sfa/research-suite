"""ResearchProblem (M5 WP4, decision D16): a phenomenon, puzzle, disagreement or unexpected result that is not yet a question.

A problem is a separate object type, not a `ResearchQuestion` with a flag: a question is answerable and the stage gates of the domain workflow open on
one (`workflows/context.py` refuses a problem id where a question is required); a problem may have several readings and no answerable form yet.

* The researcher states the problem and may write interpretations of it. A model may *propose* more (`frame`): they are stored as PROPOSED, with the
  model and the run that made them, and never change the statement or the scope. Nothing is chosen until the researcher chooses, with a reason.
* Every change is a new revision written against the revision the writer read; the problem is never deleted or rewritten. `decisions` is the readable
  log of the researcher's acts; the revisions are the full history.
* `promote_to_rq` turns one chosen interpretation into a `ResearchQuestion` whose text the researcher writes. The question points back at the problem
  revision that records the promotion (`derived_from`, edge_kind `promoted_from`), so a later reframing is reported by `stale_pinned_edges` and flags
  the question for review (`invalidate_dependents`) instead of silently leaving it behind.
* Needs no corpus, no Introduction and no question.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from jsonschema import Draft202012Validator

from research_harness.core.canonical import canonical_json_hash
from research_harness.graph.dependency import invalidate_dependents, stale_pinned_edges
from research_harness.knowledge.common import KnowledgeError, engine_origin, next_numbered_id
from research_harness.state import workflow_runs
from research_harness.state.store import StateStore
from research_harness.workflows.adapters import ModelError, adapter_identity
from research_harness.workflows.model_io import parse_model_output
from research_harness.workflows.status import Applicability, AssessmentStatus, ContextStatus, ExecutionStatus

ROLES = ("owner", "author")
KINDS = ("phenomenon", "puzzle", "disagreement", "unexpected_result", "practical_problem", "method_gap")
ANSWER_KINDS = ("causal", "predictive", "descriptive", "conceptual", "measurement", "mixed")
MAX_PROPOSALS = 8
EXECUTOR = "problem_framing@1"

FRAMING_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "problem_interpretations", "type": "object",
    "properties": {"interpretations": {"type": "array", "items": {
        "type": "object",
        "properties": {"text": {"type": "string", "minLength": 10}, "constructs": {"type": "array", "items": {"type": "string"}},
                       "unit_of_analysis": {"type": ["string", "null"]}, "answer_kind": {"enum": list(ANSWER_KINDS)},
                       "search_terms": {"type": "array", "items": {"type": "string"}}},
        "required": ["text", "answer_kind"]}}},
    "required": ["interpretations"], "additionalProperties": True}

SYSTEM_PROMPT = (
    "You help a researcher see more than one reasonable reading of a research problem. You do not choose between readings, you do not rewrite the "
    "problem, and you do not claim any reading is new or correct.\n"
    "Give distinct interpretations: each differs from the others in the construct it centres on, the unit of analysis, or the kind of answer it would admit "
    "(causal, predictive, descriptive, conceptual, measurement, mixed). For each, name the constructs involved, the unit of analysis, the kind of answer, "
    "and the terms a literature search for that reading would need (including other names the same idea goes by). Do not repeat an interpretation that is "
    "already listed.\n"
    "Respond with exactly one JSON object and nothing else. It must validate against this JSON Schema:\n" + json.dumps(FRAMING_SCHEMA, indent=2, sort_keys=True))
SKILL_DIGEST = canonical_json_hash(SYSTEM_PROMPT)


# ------------------------------------------------------------------------------------------------ the object

def load_problem(store: StateStore, problem_id: str) -> dict[str, Any]:
    o = store.get_object(problem_id)
    if not o or o["object_type"] != "ResearchProblem":
        raise KnowledgeError("PROBLEM_NOT_FOUND", f"{problem_id} is not a ResearchProblem in this project")
    return o


def _interpretation(iid: str, text: str, generated_by: str, status: str, **more: Any) -> dict[str, Any]:
    return {"id": iid, "text": text.strip(), "generated_by": generated_by, "status": status, **more}


def _next_iid(interpretations: list[dict]) -> int:
    nums = [int(i["id"][1:]) for i in interpretations if str(i.get("id", "")).startswith("I") and str(i["id"])[1:].isdigit()]
    return max(nums, default=0) + 1


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def _log(c: dict[str, Any], tx_at: str, tx_actor: str, action: str, rationale: Optional[str], **more: Any) -> list[dict[str, Any]]:
    return [*c["decisions"], {"at": tx_at, "by": tx_actor, "action": action, "rationale": rationale, "from_revision": None, **more}]


def _write(store: StateStore, token: Optional[str], problem_id: str, build, operation: str, *, expected_head: int,
           invalidate: bool = False, run_row: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """One transaction: the new problem revision (built from the current content inside the transaction), its audit row, optionally the run that
    produced it and the invalidation of what rested on older revisions. `build(content, tx) -> content`."""
    with store.write_tx(token=token, require_roles=ROLES) as tx:
        head = tx.head(problem_id)
        cur = tx.get(problem_id)
        content = build(cur["content"], tx)
        rows = tx.conn.execute("SELECT relation,target_id,target_revision,provenance FROM edges WHERE source_id=? AND source_revision=?",
                               (problem_id, head["head_revision"])).fetchall()
        res = tx.add_revision(object_id=problem_id, object_type="ResearchProblem", content=content, expected_head=expected_head,
                              edges=[(r[0], r[1], r[2], json.loads(r[3])) for r in rows], origin=engine_origin(operation, tx.actor_id))
        invalidated = invalidate_dependents(tx, problem_id, res["revision"]) if invalidate else []
        tx.audit(operation, "RECORDED", {"problem_id": problem_id, "revision": res["revision"]})
        run_id = workflow_runs.insert_run_tx(tx, run_row) if run_row is not None else None
    return {"problem_id": problem_id, "revision": res["revision"], "status": content["status"], "run_id": run_id,
            "needs_review": [f"{x['object_id']}@{x['revision']}" for x in invalidated]}


# ------------------------------------------------------------------------------------------------ create and edit

def new_problem(store: StateStore, token: Optional[str], *, statement: str, kind: str = "phenomenon", scope: Optional[dict[str, Any]] = None,
                exclusions: Optional[list[str]] = None, interpretations: Optional[list[str]] = None, label: Optional[str] = None) -> dict[str, Any]:
    """A problem in the researcher's words. No corpus, Introduction or question is needed; interpretations written here are the researcher's."""
    if kind not in KINDS:
        raise KnowledgeError("PROBLEM_KIND_UNKNOWN", f"kind must be one of {KINDS}")
    if not statement or len(statement.strip()) < 10:
        raise KnowledgeError("STATEMENT_REQUIRED", "state the problem in your own words (at least a sentence)")
    texts = [t for t in (interpretations or []) if t and t.strip()]
    if len({_norm(t) for t in texts}) != len(texts):
        raise KnowledgeError("INTERPRETATION_DUPLICATE", "the same interpretation was given twice")
    with store.read() as conn:
        pid = next_numbered_id(conn, "PROB_", "ResearchProblem")
    content = {"label": (label or statement.strip())[:120], "kind": kind, "statement": statement.strip(), "scope": scope or {},
               "exclusions": list(exclusions or []), "interpretations": [_interpretation(f"I{i}", t, "researcher", "STATED") for i, t in enumerate(texts, 1)],
               "chosen_interpretations": [], "status": "OPEN", "promoted_to": [], "decisions": [], "generated_by": "researcher"}
    with store.write_tx(token=token, require_roles=ROLES) as tx:
        res = tx.add_revision(object_id=pid, object_type="ResearchProblem", content=content, edges=[], expected_head=0,
                              origin=engine_origin("problem_new", tx.actor_id))
        tx.audit("problem_new", "RECORDED", {"problem_id": pid, "revision": res["revision"]})
    return {"problem_id": pid, "revision": res["revision"], "status": "OPEN", "interpretations": [i["id"] for i in content["interpretations"]]}


def add_interpretation(store: StateStore, token: Optional[str], problem_id: str, text: str) -> dict[str, Any]:
    """The researcher adds a reading of their own."""
    if not text or len(text.strip()) < 10:
        raise KnowledgeError("INTERPRETATION_TOO_SHORT", "write the interpretation as a sentence")
    p = load_problem(store, problem_id)
    added: dict[str, Any] = {}

    def build(c: dict, tx: Any) -> dict:
        if any(_norm(i["text"]) == _norm(text) for i in c["interpretations"]):
            raise KnowledgeError("INTERPRETATION_DUPLICATE", "this interpretation is already listed")
        it = _interpretation(f"I{_next_iid(c['interpretations'])}", text, "researcher", "STATED")
        added.update(it)
        return {**c, "interpretations": [*c["interpretations"], it], "decisions": _log(c, tx.at, tx.actor_id, "add_interpretation", None, interpretation=it["id"])}

    out = _write(store, token, problem_id, build, "problem_add_interpretation", expected_head=p["revision"])
    return {**out, "interpretation": added["id"]}


def choose(store: StateStore, token: Optional[str], problem_id: str, interpretation_ids: list[str], rationale: str) -> dict[str, Any]:
    """The researcher chooses the readings to pursue (one or several), with a reason. Only the researcher's act sets this; a model proposal is
    never chosen by being proposed."""
    if not rationale or not rationale.strip():
        raise KnowledgeError("RATIONALE_REQUIRED", "a choice needs the researcher's reason; there is no default")
    if not interpretation_ids:
        raise KnowledgeError("INTERPRETATION_REQUIRED", "choose at least one interpretation")
    p = load_problem(store, problem_id)

    def build(c: dict, tx: Any) -> dict:
        known = {i["id"] for i in c["interpretations"]}
        unknown = [i for i in interpretation_ids if i not in known]
        if unknown:
            raise KnowledgeError("INTERPRETATION_UNKNOWN", f"{', '.join(unknown)} is not an interpretation of {problem_id}")
        if c["status"] == "SET_ASIDE":
            raise KnowledgeError("PROBLEM_SET_ASIDE", "reopen the problem before choosing")
        chosen = sorted(set(interpretation_ids), key=lambda x: int(x[1:]))
        its = [{**i, "status": "CHOSEN" if i["id"] in chosen else "NOT_CHOSEN"} for i in c["interpretations"]]
        return {**c, "interpretations": its, "chosen_interpretations": chosen, "status": "PROMOTED_TO_RQ" if c["promoted_to"] else "FRAMED",
                "decisions": _log(c, tx.at, tx.actor_id, "choose", rationale.strip(), interpretations=chosen)}

    return _write(store, token, problem_id, build, "problem_choose", expected_head=p["revision"])


def revise_problem(store: StateStore, token: Optional[str], problem_id: str, *, rationale: str, statement: Optional[str] = None,
                   scope: Optional[dict[str, Any]] = None, exclusions: Optional[list[str]] = None) -> dict[str, Any]:
    """Reframe the problem: a new revision, never an edit in place. A new statement withdraws the choice of interpretations (what was chosen was a
    reading of the old statement); a change of scope or exclusions keeps it. Questions promoted from earlier revisions are flagged for review."""
    if not rationale or not rationale.strip():
        raise KnowledgeError("RATIONALE_REQUIRED", "say why the problem is being reframed")
    if statement is None and scope is None and exclusions is None:
        raise KnowledgeError("NOTHING_TO_REVISE", "give a new statement, scope or exclusions")
    if statement is not None and len(statement.strip()) < 10:
        raise KnowledgeError("STATEMENT_REQUIRED", "state the problem in your own words (at least a sentence)")
    p = load_problem(store, problem_id)

    def build(c: dict, tx: Any) -> dict:
        new = {**c}
        withdrawn = False
        if statement is not None and statement.strip() != c["statement"]:
            new.update(statement=statement.strip(), label=statement.strip()[:120])
            if c["chosen_interpretations"]:
                withdrawn = True
                new.update(chosen_interpretations=[], interpretations=[{**i, "status": "STATED" if i["generated_by"] == "researcher" else "PROPOSED"}
                                                                       if i["status"] in ("CHOSEN", "NOT_CHOSEN") else i for i in c["interpretations"]])
            new["status"] = "PROMOTED_TO_RQ" if c["promoted_to"] and not withdrawn else ("OPEN" if c["status"] != "SET_ASIDE" else "SET_ASIDE")
        if scope is not None:
            new["scope"] = scope
        if exclusions is not None:
            new["exclusions"] = list(exclusions)
        new["decisions"] = _log(c, tx.at, tx.actor_id, "reframe", rationale.strip(), statement_changed=statement is not None and statement.strip() != c["statement"],
                                scope_changed=scope is not None, exclusions_changed=exclusions is not None, choice_withdrawn=withdrawn)
        return new

    return _write(store, token, problem_id, build, "problem_revise", expected_head=p["revision"], invalidate=True)


def set_aside(store: StateStore, token: Optional[str], problem_id: str, rationale: str) -> dict[str, Any]:
    if not rationale or not rationale.strip():
        raise KnowledgeError("RATIONALE_REQUIRED", "say why the problem is set aside")
    p = load_problem(store, problem_id)
    return _write(store, token, problem_id, lambda c, tx: {**c, "status": "SET_ASIDE", "decisions": _log(c, tx.at, tx.actor_id, "set_aside", rationale.strip())},
                  "problem_set_aside", expected_head=p["revision"])


def reopen(store: StateStore, token: Optional[str], problem_id: str, rationale: str) -> dict[str, Any]:
    if not rationale or not rationale.strip():
        raise KnowledgeError("RATIONALE_REQUIRED", "say why the problem is reopened")
    p = load_problem(store, problem_id)

    def build(c: dict, tx: Any) -> dict:
        if c["status"] != "SET_ASIDE":
            raise KnowledgeError("PROBLEM_NOT_SET_ASIDE", f"{problem_id} is {c['status']}")
        status = "PROMOTED_TO_RQ" if c["promoted_to"] else ("FRAMED" if c["chosen_interpretations"] else "OPEN")
        return {**c, "status": status, "decisions": _log(c, tx.at, tx.actor_id, "reopen", rationale.strip())}

    return _write(store, token, problem_id, build, "problem_reopen", expected_head=p["revision"])


# ------------------------------------------------------------------------------------------------ model proposals

def _framing_prompt(c: dict[str, Any], k: int) -> str:
    listed = "\n".join(f"- {i['text']}" for i in c["interpretations"]) or "(none yet)"
    return (f"PROBLEM ({c['kind']}):\n{c['statement']}\n\nSCOPE (the researcher's, do not change it): {json.dumps(c['scope'], ensure_ascii=False)}\n"
            f"EXCLUSIONS: {json.dumps(c['exclusions'], ensure_ascii=False)}\n\nINTERPRETATIONS ALREADY LISTED (do not repeat):\n{listed}\n\n"
            f"Propose up to {k} further distinct interpretations.")


def frame(store: StateStore, token: Optional[str], problem_id: str, adapter: Any, *, k: int = 4) -> dict[str, Any]:
    """Ask a model for further interpretations (one call). They are stored as PROPOSED, tagged with the model and the run; the statement and the scope are
    untouched; nothing is chosen. A failed or unusable answer is registered as a failed run and stores nothing."""
    k = max(1, min(int(k), MAX_PROPOSALS))
    p = load_problem(store, problem_id)
    c = p["content"]
    model = adapter_identity(adapter)
    fingerprint = canonical_json_hash({"problem": [problem_id, p["revision"], p["revision_digest"]], "k": k, "skill": SKILL_DIGEST, "model": model})
    base = {"workflow": "problem_framing", "input_fingerprint": fingerprint, "executor": EXECUTOR, "applicability": Applicability.APPLICABLE.value,
            "model_descriptor": model, "skill_digest": SKILL_DIGEST,
            "input_revisions": [{"object_id": problem_id, "revision": p["revision"], "content_digest": p["content_digest"]}]}

    def register_failure(reason: str, usage: dict) -> str:
        return workflow_runs.append_run(store, {**base, "execution_status": ExecutionStatus.FAILED.value, "context_status": ContextStatus.COMPLETE.value,
                                                "assessment_status": AssessmentStatus.NOT_ASSESSED.value, "legacy_status": "FAILED",
                                                "status_reason": reason, "usage": usage})

    try:
        raw, usage = adapter.generate(SYSTEM_PROMPT, _framing_prompt(c, k), expected_schema=FRAMING_SCHEMA, temperature=0.0)
    except ModelError as exc:
        run = register_failure(type(exc).__name__, {"llm_calls": 1})
        return {"status": "FAILED", "reason": type(exc).__name__, "message": str(exc), "run_id": run, "proposed": []}
    use = {"llm_calls": 1, "input_tokens": getattr(usage, "input_tokens", None), "output_tokens": getattr(usage, "output_tokens", None),
           "usage_source": getattr(usage, "usage_source", None)}
    parsed, err = parse_model_output(raw)
    problems = [] if parsed is None else sorted(e.message[:120] for e in Draft202012Validator(FRAMING_SCHEMA).iter_errors(parsed))
    if parsed is None or problems:
        run = register_failure("invalid_output", use)
        return {"status": "FAILED", "reason": "invalid_output", "message": err or "; ".join(problems[:3]), "run_id": run, "proposed": []}
    generated_by = f"model:{model.get('model_id') or model.get('provider')}"
    run_row = {**base, "execution_status": ExecutionStatus.SUCCEEDED.value, "context_status": ContextStatus.COMPLETE.value,
               "assessment_status": AssessmentStatus.PROVISIONAL.value, "legacy_status": "SUCCESS", "status_reason": None, "usage": use}
    added: list[str] = []
    dropped = {"duplicates": 0}

    def build(cur: dict, tx: Any) -> dict:
        its = list(cur["interpretations"])
        seen = {_norm(i["text"]) for i in its}
        for prop in parsed["interpretations"][:k]:
            if _norm(prop["text"]) in seen:
                dropped["duplicates"] += 1
                continue
            seen.add(_norm(prop["text"]))
            it = _interpretation(f"I{_next_iid(its)}", prop["text"], generated_by, "PROPOSED", constructs=[str(x) for x in prop.get("constructs") or []],
                                 unit_of_analysis=prop.get("unit_of_analysis"), answer_kind=prop["answer_kind"],
                                 search_terms=[str(x) for x in prop.get("search_terms") or []])
            its.append(it)
            added.append(it["id"])
        return {**cur, "interpretations": its, "decisions": _log(cur, tx.at, tx.actor_id, "model_proposals", None, proposed=list(added), model=generated_by)}

    try:
        out = _write(store, token, problem_id, build, "problem_frame", expected_head=p["revision"], run_row=run_row)
    except Exception as exc:
        try:
            register_failure(f"write_refused:{getattr(exc, 'code', type(exc).__name__)}", use)
        except Exception:  # noqa: BLE001 - the original error is the one to report
            pass
        raise
    return {"status": "PROPOSED", "problem_id": problem_id, "revision": out["revision"], "proposed": added, "duplicates_dropped": dropped["duplicates"],
            "run_id": out["run_id"], "model": generated_by}


# ------------------------------------------------------------------------------------------------ promotion to a question

def promote_to_rq(store: StateStore, token: Optional[str], problem_id: str, interpretation_id: str, text: str, rationale: str, *,
                  rq_id: Optional[str] = None) -> dict[str, Any]:
    """Turn a CHOSEN interpretation into a `ResearchQuestion` whose text the researcher writes. One transaction: the question, and the problem revision that
    records the promotion. The question's `derived_from` edge names that revision; the problem keeps its whole history."""
    if not text or len(text.strip()) < 10:
        raise KnowledgeError("RQ_TEXT_REQUIRED", "write the research question yourself: it is not copied from the interpretation")
    if not rationale or not rationale.strip():
        raise KnowledgeError("RATIONALE_REQUIRED", "say why this reading becomes the question")
    p = load_problem(store, problem_id)
    with store.write_tx(token=token, require_roles=ROLES) as tx:
        head = tx.head(problem_id)
        if head["head_revision"] != p["revision"]:
            raise KnowledgeError("STALE_REVISION", f"{problem_id} is at revision {head['head_revision']}, not {p['revision']}")
        c = tx.get(problem_id)["content"]
        if c["status"] == "SET_ASIDE":
            raise KnowledgeError("PROBLEM_SET_ASIDE", "reopen the problem before promoting it")
        if interpretation_id not in {i["id"] for i in c["interpretations"]}:
            raise KnowledgeError("INTERPRETATION_UNKNOWN", f"{interpretation_id} is not an interpretation of {problem_id}")
        if interpretation_id not in c["chosen_interpretations"]:
            raise KnowledgeError("INTERPRETATION_NOT_CHOSEN", f"{interpretation_id} has not been chosen by the researcher; choose it first")
        rid = rq_id or next_numbered_id(tx.conn, "RQ_", "ResearchQuestion")
        if tx.head(rid):
            raise KnowledgeError("RQ_EXISTS", f"{rid} already exists")
        rows = tx.conn.execute("SELECT relation,target_id,target_revision,provenance FROM edges WHERE source_id=? AND source_revision=?",
                               (problem_id, head["head_revision"])).fetchall()
        new_c = {**c, "status": "PROMOTED_TO_RQ", "promoted_to": [*c["promoted_to"], rid],
                 "decisions": _log(c, tx.at, tx.actor_id, "promote_to_rq", rationale.strip(), interpretation=interpretation_id, research_question=rid)}
        res = tx.add_revision(object_id=problem_id, object_type="ResearchProblem", content=new_c, expected_head=p["revision"],
                              edges=[(r[0], r[1], r[2], json.loads(r[3])) for r in rows], origin=engine_origin("problem_promote", tx.actor_id))
        rq = tx.add_revision(object_id=rid, object_type="ResearchQuestion", expected_head=0, origin=engine_origin("problem_promote", tx.actor_id),
                             content={"label": text.strip()[:120], "text": text.strip(),
                                      "attributes": {"promoted_from": {"problem": problem_id, "revision": res["revision"], "interpretation": interpretation_id},
                                                     "generated_by": "researcher"}},
                             edges=[("derived_from", problem_id, res["revision"], {"edge_kind": "promoted_from", "interpretation": interpretation_id})])
        tx.audit("problem_promote", "RECORDED", {"problem_id": problem_id, "revision": res["revision"], "research_question": rid})
    return {"problem_id": problem_id, "problem_revision": res["revision"], "research_question": rid, "research_question_revision": rq["revision"],
            "interpretation": interpretation_id}


# ------------------------------------------------------------------------------------------------ reading

def problem_view(store: StateStore, problem_id: str) -> dict[str, Any]:
    p = load_problem(store, problem_id)
    c = p["content"]
    with store.read() as conn:
        revs = [dict(r) for r in conn.execute("SELECT revision, created_at, submitted_by FROM objects WHERE object_id=? ORDER BY revision", (problem_id,))]
        stale = {}
        for rid in c["promoted_to"]:
            rq = store.get_object(rid, conn=conn)
            if rq:
                stale[rid] = stale_pinned_edges(conn, rid, rq["revision"])
        review = {rid: (conn.execute("SELECT scientific_review_status FROM object_status WHERE object_id=? AND revision=?",
                                     (rid, store.get_object(rid, conn=conn)["revision"])).fetchone() or [None])[0] for rid in c["promoted_to"]}
    return {"problem_id": problem_id, "revision": p["revision"], "kind": c["kind"], "status": c["status"], "statement": c["statement"], "scope": c["scope"],
            "exclusions": c["exclusions"], "interpretations": c["interpretations"], "chosen": c["chosen_interpretations"], "promoted_to": c["promoted_to"],
            "questions_to_review": {rid: {"review_status": review[rid], "pinned_to_older_problem_revision": stale[rid]} for rid in c["promoted_to"]},
            "decisions": c["decisions"], "revisions": revs}
