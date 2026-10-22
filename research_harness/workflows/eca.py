"""Evidence-Claim Adjudication, ECA (plan v3, 3A.2): is this statement a fair reading of this passage?

Quote verification shows that a passage exists in a source. It does not show that a statement built on the passage
says what the passage says: "the model performs well only when the panel is balanced" is quoted correctly by
"the model is suitable for panel data", and the quote check passes. ECA is the separate check for that.

One ECA record is an `Adjudication` object (`content.kind == "evidence_claim"`) bound to a `(statement revision,
passage revision)` pair. It holds

* `source_match`: is the passage verbatim in the stored source text (deterministic);
* `qualifier_cues`: conditional wording in the passage ("only when", "unless", ...), found by a fixed list of
  patterns. A cue is a *flag*, never a verdict (invariant I1);
* `passage_proposition`, `equivalence`, `dropped_conditions`, `cue_handling`, `verdict`: one adversarial model call
  (never a multi-agent loop), or the researcher;
* `guard`: the consistency rule that overrode the model, if any.

The model only proposes. A record created by `adjudicate` is `generated_by: model:...`, unconfirmed, and can lift a
statement no higher than PROVISIONAL. The researcher's `confirm` is a second, append-only record. A
`SUPPORTED_IF_QUALIFIED` verdict does not verify the statement as written: the qualification has to be written into
a new revision of the statement, and the confirmation names that revision (I16).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

from jsonschema import Draft202012Validator

from research_harness import taxonomy
from research_harness.core.canonical import canonical_json_hash
from research_harness.core.errors import AuthorizationError
from research_harness.knowledge.common import (
    WRITE_ROLES, KnowledgeError, engine_origin, head_or_raise, next_numbered_id, scope_id)
from research_harness.knowledge.corpus import verify_passage
from research_harness.state import workflow_runs
from research_harness.state.store import StateStore
from research_harness.workflows import quotes
from research_harness.workflows.adapters import ModelError, adapter_identity
from research_harness.workflows.model_io import parse_model_output
from research_harness.workflows.status import (
    Applicability, AssessmentStatus, ContextStatus, ExecutionStatus, apply_caps)

EXECUTOR = "eca@1"
SUPPORTING = frozenset({"SUPPORTED_AS_STATED", "SUPPORTED_IF_QUALIFIED"})
HIGH_IMPACT_USES = frozenset({"contribution", "novelty", "measurement", "proxy_validity", "identification",
                              "interpretation", "claim_change", "promotion"})
_LEGACY_VERDICT = {"SUPPORTED_AS_STATED": "SUPPORTED", "SUPPORTED_IF_QUALIFIED": "SUPPORTED",
                   "NOT_SUPPORTED": "NOT_SUPPORTED", "UNRESOLVED": "UNCLEAR"}

# ---------------------------------------------------------------------------------------------------
# qualifier cues (flags only)
# ---------------------------------------------------------------------------------------------------

_CUE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("only_when", r"\bonly\s+(?:when|if|in|for|under|among|after|once|where)\b"),
    ("unless", r"\bunless\b"),
    ("provided_that", r"\bprovided\s+that\b"),
    ("conditional_on", r"\bconditional\s+(?:on|upon)\b"),
    ("in_samples_where", r"\bin\s+(?:samples|settings|cases|periods|firms|years)\s+(?:where|in\s+which|with)\b"),
    ("as_long_as", r"\bas\s+long\s+as\b"),
    ("depends_on", r"\bdepend(?:s|ing)?\s+(?:on|upon)\b"),
    ("caution", r"\bwe\s+caution\b|\bcaution\s+(?:that|against)\b|\bshould\s+be\s+interpreted\s+with\s+caution\b"),
    ("except", r"\bexcept\s+(?:when|for|in|where)\b"),
    ("limited_to", r"\b(?:limited|restricted)\s+to\b"),
    ("holds_if", r"\b(?:holds?|valid|applies|applicable)\s+(?:only\s+)?(?:if|when|where)\b"),
)
_CUES = tuple((name, re.compile(rx, re.IGNORECASE)) for name, rx in _CUE_PATTERNS)


def qualifier_cues(text: str) -> list[dict[str, Any]]:
    """Conditional wording in `text`, with spans. Pattern matches are flags for the reviewer, never a verdict."""
    found: list[dict[str, Any]] = []
    for name, rx in _CUES:
        for m in rx.finditer(text or ""):
            found.append({"cue": m.group(0), "pattern": name, "char_start": m.start(), "char_end": m.end()})
    found.sort(key=lambda c: (c["char_start"], c["pattern"]))
    return found


# ---------------------------------------------------------------------------------------------------
# statements
# ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class StatementRef:
    object_id: str
    revision: int
    text: str
    element: Optional[str] = None
    generated_by: str = "legacy"
    risk_tier: str = "STANDARD"

    def to_dict(self) -> dict[str, Any]:
        return {"object_id": self.object_id, "revision": self.revision, "element": self.element,
                "text_sha256": canonical_json_hash(self.text), "generated_by": self.generated_by,
                "risk_tier": self.risk_tier}


def risk_tier(use: Optional[str] = None, *, declared: Optional[str] = None) -> str:
    """HIGH for a statement used in contribution, measurement, identification, interpretation or promotion."""
    if declared:
        taxonomy.validate_term("eca_risk_tier", declared)
        if declared == "HIGH":
            return "HIGH"
    return "HIGH" if use in HIGH_IMPACT_USES else "STANDARD"


def resolve_statement(store: StateStore, object_id: str, *, revision: Optional[int] = None, element: Optional[str] = None,
                      use: Optional[str] = None) -> StatementRef:
    """The statement an ECA is about: the `text` of a Claim/ResearchQuestion, or one element of an object's `elements`
    / `components`. Its recorded `generated_by` is kept; one that was never recorded is `legacy`, not `researcher`."""
    obj = store.get_object(object_id, revision)
    if not obj:
        raise KnowledgeError("OBJECT_UNKNOWN", f"{object_id} does not exist")
    c = obj["content"]
    declared_tier = (c.get("attributes") or {}).get("risk_tier")
    gen = c.get("generated_by") or (c.get("attributes") or {}).get("generated_by") or "legacy"
    if element:
        pool = c.get("elements") or []
        found = next((e for e in pool if isinstance(e, dict) and e.get("id") == element), None)
        if found is None and isinstance(c.get("components"), dict) and element in c["components"]:
            found = c["components"][element]
        if not isinstance(found, dict) or not (found.get("text") or found.get("statement")):
            raise KnowledgeError("ELEMENT_UNKNOWN", f"{object_id} has no statement element {element!r}")
        text = found.get("text") or found.get("statement")
        gen = found.get("generated_by") or gen
        declared_tier = found.get("risk_tier") or declared_tier
    else:
        text = c.get("text") or c.get("summary") or c.get("normalized_claim") or c.get("description")
        if not text:
            raise KnowledgeError("NO_STATEMENT_TEXT", f"{object_id} has no statement text")
    return StatementRef(object_id, obj["revision"], str(text), element, gen, risk_tier(use, declared=declared_tier))


# ---------------------------------------------------------------------------------------------------
# the adjudication request
# ---------------------------------------------------------------------------------------------------

_EQUIVALENCE = list(taxonomy.vocabulary("eca_equivalence"))
_VERDICTS = list(taxonomy.vocabulary("eca_verdict"))

MODEL_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "evidence_claim_adjudication", "type": "object",
    "properties": {
        "passage_proposition": {"type": "string", "minLength": 1},
        "equivalence": {"enum": _EQUIVALENCE},
        "dropped_conditions": {"type": "array", "items": {"type": "string"}},
        "cue_handling": {"type": "array", "items": {
            "type": "object", "properties": {"cue": {"type": "string"}, "handling": {"enum": ["carried", "dropped", "not_a_condition"]},
                                             "note": {"type": "string"}}, "required": ["cue", "handling"]}},
        "verdict": {"enum": _VERDICTS},
        "required_qualification": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
    "required": ["passage_proposition", "equivalence", "dropped_conditions", "verdict"], "additionalProperties": True,
}

SYSTEM_PROMPT = (
    "You are an adversarial checker of one statement against one passage from a source. You do not decide whether the "
    "statement is true. You decide whether the passage supports the statement AS WRITTEN.\n\n"
    "Work in this order:\n"
    "1. State what the passage itself claims, including its population, conditions, hedges and direction "
    "(`passage_proposition`). Do not use outside knowledge.\n"
    "2. List every condition or qualification in the passage that the statement leaves out (`dropped_conditions`).\n"
    "3. Compare the statement with the passage: EQUIVALENT (same scope and strength), NARROWER (the statement says less), "
    "BROADER (the statement says more or drops a condition), DIFFERENT (a different claim), CONTRADICTS, or UNRESOLVED.\n"
    "4. If conditional wording was flagged in the passage, say for each flagged cue whether the statement carries it, drops it, "
    "or whether the wording is not a condition (`cue_handling`).\n"
    "5. Give a verdict: SUPPORTED_AS_STATED only if the statement is EQUIVALENT or NARROWER and drops no condition; "
    "SUPPORTED_IF_QUALIFIED if it would be supported once a stated qualification is added (list it in `required_qualification`); "
    "NOT_SUPPORTED; or UNRESOLVED if you cannot tell.\n\n"
    "Respond with exactly one JSON object and nothing else. It must validate against this JSON Schema:\n"
    + json.dumps(MODEL_SCHEMA, indent=2, sort_keys=True))


def build_user_prompt(statement: StatementRef, passage: dict[str, Any], cues: list[dict[str, Any]]) -> str:
    cue_lines = "\n".join(f"- \"{c['cue']}\"" for c in cues) or "(none flagged)"
    return (f"STATEMENT ({statement.object_id} revision {statement.revision}):\n{statement.text}\n\n"
            f"PASSAGE ({passage['object_id']} revision {passage['revision']}):\n{passage['content'].get('quote', '')}\n\n"
            f"Conditional wording flagged in the passage by a pattern list (flags, not conclusions):\n{cue_lines}")


def consistency_guard(proposal: dict[str, Any], cues: list[dict[str, Any]]) -> tuple[str, Optional[str]]:
    """The verdict to record and the name of the rule that changed it. The model's verdict is overridden only toward
    UNRESOLVED, which forces researcher review; a guard never turns a refusal into support."""
    verdict, eq = proposal["verdict"], proposal["equivalence"]
    dropped = [d for d in proposal.get("dropped_conditions") or [] if d.strip()]
    if verdict == "NOT_SUPPORTED":
        return verdict, None
    if eq == "UNRESOLVED" and verdict != "UNRESOLVED":
        return "UNRESOLVED", "equivalence_unresolved"
    if verdict == "SUPPORTED_AS_STATED":
        if eq in {"BROADER", "DIFFERENT", "CONTRADICTS"}:
            return "UNRESOLVED", "verdict_inconsistent_with_equivalence"
        if dropped:
            return "UNRESOLVED", "dropped_conditions_listed_but_supported_as_stated"
        handled = {quotes.normalize_whitespace(h.get("cue", "")).lower(): h.get("handling") for h in proposal.get("cue_handling") or []}
        unhandled = [c["cue"] for c in cues if quotes.normalize_whitespace(c["cue"]).lower() not in handled]
        if unhandled:
            return "UNRESOLVED", "qualifier_cues_not_accounted_for"
        if any(h == "dropped" for h in handled.values()):
            return "UNRESOLVED", "a_flagged_cue_was_dropped_but_statement_called_equivalent"
    if verdict == "SUPPORTED_IF_QUALIFIED":
        if eq == "CONTRADICTS":
            return "UNRESOLVED", "verdict_inconsistent_with_equivalence"
        if not [q for q in proposal.get("required_qualification") or [] if q.strip()]:
            return "UNRESOLVED", "qualified_verdict_without_a_qualification"
    return verdict, None


def _adjudication_for(store: StateStore, statement_id: str, *, conn: Any = None) -> list[dict[str, Any]]:
    """Every ECA record about a statement object (all revisions), oldest first."""
    own = conn is None
    ctx = store.read() if own else None
    c = ctx.__enter__() if own else conn
    try:
        rows = c.execute("SELECT h.object_id FROM object_heads h WHERE h.object_type='Adjudication' ORDER BY h.object_id").fetchall()
        out = []
        for r in rows:
            obj = store.get_object(r[0], conn=c)
            cc = obj["content"]
            if cc.get("kind") == "evidence_claim" and (cc.get("subject") or {}).get("object_id") == statement_id:
                out.append(obj)
        return out
    finally:
        if own:
            ctx.__exit__(None, None, None)


def _counter_evidence(conn: Any, store: StateStore, statement: StatementRef) -> list[dict[str, Any]]:
    """Objects joined to the statement by a `challenges` edge, in either direction, bound to its current revision.
    (The graph does not enforce a direction for `challenges`.) Read from the graph, not from wording."""
    out, seen = [], set()
    for e in store.edges_to(conn, statement.object_id, statement.revision):
        if e["relation"] == "challenges":
            key = (e["source_id"], e["source_revision"])
            src = store.get_object(e["source_id"], e["source_revision"], conn=conn)
            if src and src["object_type"] != "Adjudication" and key not in seen:
                seen.add(key)
                out.append({"object_id": e["source_id"], "revision": e["source_revision"], "object_type": src["object_type"],
                            "relation": "challenges"})
    for e in store.edges_from(conn, statement.object_id, statement.revision):
        if e["relation"] == "challenges":
            key = (e["target_id"], e["target_revision"])
            tgt = store.get_object(e["target_id"], e["target_revision"], conn=conn)
            if tgt and tgt["object_type"] != "Adjudication" and key not in seen:
                seen.add(key)
                out.append({"object_id": e["target_id"], "revision": e["target_revision"], "object_type": tgt["object_type"],
                            "relation": "challenges"})
    return out


def _next_adj_id(conn: Any, store: StateStore) -> str:
    return next_numbered_id(conn, scope_id(store, "ADJ_"), "Adjudication")


def _write_record(store: StateStore, token: Optional[str], roles: tuple, content: dict[str, Any], statement: StatementRef,
                  passage: dict[str, Any], operation: str, *, run_row: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Write one adjudication record; with `run_row`, the run that produced it is registered in the same transaction (I22)."""
    run_id = None
    with store.write_tx(token=token, require_roles=roles) as tx:
        head_or_raise(tx, statement.object_id)
        sh = tx.head(statement.object_id)
        if sh["head_revision"] != statement.revision and not content.get("confirms"):
            raise KnowledgeError("REVISION_NOT_HEAD", f"{statement.object_id} is at revision {sh['head_revision']}, "
                                 f"not {statement.revision}; adjudicate the current revision")
        ph = head_or_raise(tx, passage["object_id"], "EvidencePassage")
        if ph["head_revision"] != passage["revision"]:
            raise KnowledgeError("STALE_PASSAGE", f"{passage['object_id']} is at revision {ph['head_revision']}")
        verdict = content["eca_verdict"]
        edges = [("depends_on", statement.object_id, statement.revision, {"origin": "eca_statement"}),
                 ("depends_on", passage["object_id"], passage["revision"], {"origin": "eca_passage"})]
        if verdict in SUPPORTING and content.get("confirmed_by_researcher"):
            edges.append(("supports", statement.object_id, statement.revision, {"origin": "eca_confirmed"}))
        elif verdict == "NOT_SUPPORTED":
            edges.append(("challenges", statement.object_id, statement.revision, {"origin": "eca"}))
        aid = _next_adj_id(tx.conn, store)
        subject = tx.get(statement.object_id, statement.revision)
        content = {**content, "subject": {"object_id": statement.object_id, "revision": statement.revision},
                   "subject_revision_digest": subject["revision_digest"], "adjudicated_at": tx.at, "is_historical": False,
                   "adjudicator_actor_id": tx.actor_id, "adjudicator_roles": sorted(tx.actor.roles),
                   "independence_level": "author", "independence_group": tx.actor.independence_group}
        tx.add_revision(object_id=aid, object_type="Adjudication", content=content, edges=edges,
                        origin=engine_origin(operation, tx.actor_id, assessment_kind="semantic_entailment"))
        tx.event("ADJUDICATION_RECORDED", {"adjudication_id": aid, "subject_id": statement.object_id,
                                           "subject_revision": statement.revision, "verdict": content["verdict"],
                                           "assessment_kind": "semantic_entailment", "independence_level": "author",
                                           "adjudicator": tx.actor_id})
        tx.audit(operation, "RECORDED", {"adjudication_id": aid, "eca_verdict": verdict})
        if run_row is not None:
            tx.fault("adjudication_before_run")
            run_id = workflow_runs.insert_run_tx(tx, run_row)
    out = {"adjudication_id": aid, "revision": 1, **{k: content[k] for k in ("eca_verdict", "equivalence")}}
    return {**out, "run_id": run_id} if run_row is not None else out


def _run_row(*, fingerprint: str, execution: ExecutionStatus, assessment: AssessmentStatus, reason: Optional[str], usage: dict,
             model: Optional[dict], artifact: Optional[str], statement: StatementRef, passage: dict[str, Any],
             reused_from: Optional[str] = None) -> dict[str, Any]:
    return {
        "workflow": "eca_adjudication", "input_fingerprint": fingerprint, "executor": EXECUTOR,
        "execution_status": execution.value, "context_status": ContextStatus.COMPLETE.value,
        "assessment_status": assessment.value, "applicability": Applicability.APPLICABLE.value,
        "legacy_status": "SUCCESS" if execution is ExecutionStatus.SUCCEEDED else "FAILED", "status_reason": reason,
        "usage": usage, "model_descriptor": model, "artifact_sha256": artifact, "reused_from_run_id": reused_from,
        "input_revisions": [{"object_id": statement.object_id, "revision": statement.revision},
                            {"object_id": passage["object_id"], "revision": passage["revision"]}]}


def _register(store: StateStore, **kw: Any) -> str:
    """A run that writes no record (failed, reused) is registered on its own."""
    return workflow_runs.append_run(store, _run_row(**kw))


def _record_with_run(store: StateStore, token: Optional[str], content: dict[str, Any], statement: StatementRef, passage: dict[str, Any],
                     run: dict[str, Any]) -> dict[str, Any]:
    """Record and SUCCEEDED run in one transaction. If the write is refused, the run is registered as FAILED (its model call, if any,
    is not lost from the registry) and the error is raised; no SUCCEEDED run is left without its record."""
    try:
        return _write_record(store, token, WRITE_ROLES, content, statement, passage, "adjudicate",
                             run_row=_run_row(execution=ExecutionStatus.SUCCEEDED, **run))
    except Exception as exc:
        try:
            _register(store, **{**run, "execution": ExecutionStatus.FAILED, "assessment": AssessmentStatus.NOT_ASSESSED,
                                "reason": f"write_refused:{getattr(exc, 'code', type(exc).__name__)}", "artifact": None})
        except Exception:  # noqa: BLE001 - the original error is the one to report
            pass
        raise


def adjudicate(store: StateStore, token: Optional[str], statement: StatementRef, passage_id: str, adapter: Any, *,
               reuse: bool = True) -> dict[str, Any]:
    """Create a model-proposed ECA record for `(statement, passage)`. One model call; none if the source match fails
    or an unconfirmed record for these revisions already exists."""
    passage = store.get_object(passage_id)
    if not passage or passage["object_type"] != "EvidencePassage":
        raise KnowledgeError("PASSAGE_NOT_FOUND", f"{passage_id} is not an EvidencePassage")
    pref = {"object_id": passage_id, "revision": passage["revision"], "content": passage["content"]}
    fingerprint = canonical_json_hash({"statement": statement.to_dict(), "passage": [passage_id, passage["revision"],
                                                                                    passage["content_digest"]]})
    if reuse:
        for adj in _adjudication_for(store, statement.object_id):
            c = adj["content"]
            if (not c.get("confirms") and (c.get("statement") or {}).get("text_sha256") == statement.to_dict()["text_sha256"]
                    and (c.get("statement") or {}).get("element") == statement.element
                    and (c.get("passage") or {}).get("object_id") == passage_id and c["passage"].get("revision") == passage["revision"]):
                with store.read() as conn:
                    row = conn.execute("SELECT run_id FROM workflow_runs WHERE workflow='eca_adjudication' AND input_fingerprint=? "
                                       "ORDER BY rowid LIMIT 1", (fingerprint,)).fetchone()
                _register(store, fingerprint=fingerprint, execution=ExecutionStatus.SUCCEEDED, assessment=AssessmentStatus.PROVISIONAL,
                          reason="reused", usage={"llm_calls": 0}, model=None, artifact=None, statement=statement, passage=pref,
                          reused_from=row[0] if row else None)
                return {"adjudication_id": adj["object_id"], "eca_verdict": c["eca_verdict"], "equivalence": c["equivalence"],
                        "guard": c.get("guard"), "reused": True}

    match = verify_passage(store, passage_id)
    source_match = {"status": match["source_match"], "reason": match.get("reason", "")}
    cues = qualifier_cues(passage["content"].get("quote", ""))
    base = {"kind": "evidence_claim", "assessment_kind": "semantic_entailment", "statement": statement.to_dict(),
            "passage": {"object_id": passage_id, "revision": passage["revision"], "quote_sha256": canonical_json_hash(passage["content"].get("quote", ""))},
            "risk_tier": statement.risk_tier, "source_match": source_match, "qualifier_cues": cues,
            "confirmed_by_researcher": False, "confirms": None, "cited_evidence": [], "counter_evidence": []}
    with store.read() as conn:
        base["counter_evidence"] = _counter_evidence(conn, store, statement)

    if source_match["status"] != "VERIFIED":
        content = {**base, "generated_by": "deterministic:eca@1", "passage_proposition": "", "equivalence": "UNRESOLVED",
                   "dropped_conditions": [], "cue_handling": [], "eca_verdict": "UNRESOLVED", "required_qualification": [],
                   "verdict": "UNCLEAR", "rationale": f"The passage does not verify against the stored source text ({source_match['reason']}); "
                   "no model reading was requested.", "guard": "source_match_not_verified", "model": None}
        rec = _record_with_run(store, token, content, statement, pref, dict(
            fingerprint=fingerprint, assessment=AssessmentStatus.NOT_ASSESSED, reason="source_match_not_verified", usage={"llm_calls": 0},
            model=None, artifact=None, statement=statement, passage=pref))
        return {**rec, "guard": "source_match_not_verified", "reused": False, "llm_calls": 0}

    model = adapter_identity(adapter)
    try:
        raw, usage = adapter.generate(SYSTEM_PROMPT, build_user_prompt(statement, pref, cues), expected_schema=MODEL_SCHEMA, temperature=0.0)
    except ModelError as exc:
        run = _register(store, fingerprint=fingerprint, execution=ExecutionStatus.FAILED, assessment=AssessmentStatus.NOT_ASSESSED,
                        reason=type(exc).__name__, usage={"llm_calls": 1}, model=model, artifact=None, statement=statement, passage=pref)
        return {"status": "FAILED", "reason": type(exc).__name__, "message": str(exc), "run_id": run}
    parsed, err = parse_model_output(raw)
    problems = [] if parsed is None else sorted(e.message[:120] for e in Draft202012Validator(MODEL_SCHEMA).iter_errors(parsed))
    if parsed is None or problems:
        run = _register(store, fingerprint=fingerprint, execution=ExecutionStatus.FAILED, assessment=AssessmentStatus.NOT_ASSESSED,
                        reason="invalid_output", usage={"llm_calls": 1}, model=model, artifact=None, statement=statement, passage=pref)
        return {"status": "FAILED", "reason": "invalid_output", "message": err or "; ".join(problems[:3]), "run_id": run}

    verdict, guard = consistency_guard(parsed, cues)
    content = {**base, "generated_by": f"model:{model.get('model_id') or model.get('provider')}",
               "passage_proposition": parsed["passage_proposition"], "equivalence": parsed["equivalence"],
               "dropped_conditions": [d for d in parsed["dropped_conditions"] if d.strip()],
               "cue_handling": parsed.get("cue_handling") or [], "eca_verdict": verdict, "verdict": _LEGACY_VERDICT[verdict],
               "required_qualification": [q for q in parsed.get("required_qualification") or [] if q.strip()],
               "rationale": (parsed.get("rationale") or "Model-proposed evidence-claim adjudication; unconfirmed.")[:20000],
               "guard": guard, "model": {**model, "proposed_verdict": parsed["verdict"]}}
    assessment = apply_caps(AssessmentStatus.PROVISIONAL, generated_by=content["generated_by"], eca_verdict=verdict)
    artifact = store.artifacts.put_bytes(json.dumps({"raw": parsed, "recorded_verdict": verdict, "guard": guard}, sort_keys=True).encode("utf-8"))
    rec = _record_with_run(store, token, content, statement, pref, dict(
        fingerprint=fingerprint, assessment=assessment, reason=guard,
        usage={"llm_calls": 1, "input_tokens": getattr(usage, "input_tokens", None),
               "output_tokens": getattr(usage, "output_tokens", None), "usage_source": getattr(usage, "usage_source", None)},
        model=model, artifact=artifact, statement=statement, passage=pref))
    return {**rec, "guard": guard, "model_verdict": parsed["verdict"], "reused": False, "llm_calls": 1}


def confirm(store: StateStore, token: Optional[str], adjudication_id: str, *, verdict: str, rationale: str,
            qualification_applied_in: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """The researcher's decision on a proposed ECA record (role author or owner). Append-only: the proposal stays.

    * Overriding a guard or a model verdict needs a rationale.
    * `SUPPORTED_IF_QUALIFIED` verifies nothing by itself: name the revision of the statement that carries the
      qualification (`qualification_applied_in`, a later revision than the one adjudicated).
    """
    taxonomy.validate_term("eca_verdict", verdict)
    if len((rationale or "").strip()) < 10:
        raise KnowledgeError("RATIONALE_REQUIRED", "a confirmation records why: give a rationale of at least 10 characters")
    adj = store.get_object(adjudication_id)
    if not adj or adj["object_type"] != "Adjudication" or adj["content"].get("kind") != "evidence_claim":
        raise KnowledgeError("ECA_NOT_FOUND", f"{adjudication_id} is not an evidence-claim adjudication")
    prop = adj["content"]
    if prop.get("confirms"):
        raise KnowledgeError("ALREADY_A_CONFIRMATION", f"{adjudication_id} is itself a confirmation")
    sid = prop["subject"]["object_id"]
    element = (prop["statement"] or {}).get("element")
    current = resolve_statement(store, sid, element=element)
    text_changed = current.to_dict()["text_sha256"] != prop["statement"]["text_sha256"]
    applied = None
    if verdict == "SUPPORTED_IF_QUALIFIED":
        applied = qualification_applied_in
        if not text_changed or not applied or applied.get("object_id") != sid or not isinstance(applied.get("revision"), int):
            raise KnowledgeError("QUALIFICATION_NOT_APPLIED",
                                 "a SUPPORTED_IF_QUALIFIED verdict verifies nothing until the qualification is written into the "
                                 f"statement: revise {sid} so its text carries it, then pass qualification_applied_in="
                                 "{object_id, revision} naming that revision")
        if applied["revision"] != current.revision:
            raise KnowledgeError("QUALIFICATION_REVISION_NOT_HEAD", f"{sid} is at revision {current.revision}, not {applied['revision']}")
    elif text_changed:
        raise KnowledgeError("STATEMENT_REVISED_SINCE", f"the text of {sid} changed after this adjudication; adjudicate the new text")
    pref = prop["passage"]
    passage = store.get_object(pref["object_id"])
    if not passage or passage["revision"] != pref["revision"]:
        raise KnowledgeError("STALE_PASSAGE", f"{pref['object_id']} was revised after this adjudication; adjudicate again")
    statement = current
    statement_text = current.to_dict()
    content = {
        "kind": "evidence_claim", "assessment_kind": "semantic_entailment", "statement": statement_text, "passage": pref,
        "risk_tier": prop.get("risk_tier", current.risk_tier), "source_match": prop["source_match"],
        "qualifier_cues": prop["qualifier_cues"], "passage_proposition": prop["passage_proposition"],
        "equivalence": prop["equivalence"], "dropped_conditions": prop["dropped_conditions"], "cue_handling": prop["cue_handling"],
        "counter_evidence": prop["counter_evidence"], "eca_verdict": verdict, "verdict": _LEGACY_VERDICT[verdict],
        "required_qualification": prop["required_qualification"], "generated_by": "researcher", "confirms": adjudication_id,
        "confirmed_by_researcher": True, "model": None, "guard": prop.get("guard"), "rationale": rationale.strip()[:20000],
        "cited_evidence": [{"object_id": pref["object_id"], "revision": pref["revision"]}],
        "qualification_applied_in": applied, "overrides": {"proposed": prop["eca_verdict"], "guard": prop.get("guard")}
        if verdict != prop["eca_verdict"] or prop.get("guard") else None,
    }
    rec = _write_record(store, token, ("author", "owner"), content, statement,
                        {"object_id": pref["object_id"], "revision": passage["revision"]}, "confirm_eca")
    return {**rec, "confirms": adjudication_id}


# ---------------------------------------------------------------------------------------------------
# what an ECA allows a statement to claim
# ---------------------------------------------------------------------------------------------------

def statement_assessment(store: StateStore, statement: StatementRef) -> dict[str, Any]:
    """The assessment the current text of a statement may carry (never above what `apply_caps` and I16 allow).

    An adjudication belongs to the exact words it read, not to a revision number: editing something else on the object
    (an attribute, a validation result) leaves it valid, and editing the statement's text makes it not apply.

    CONTESTED: an unresolved `challenges` edge, or any ECA verdict NOT_SUPPORTED for this text.
    VERIFIED_WITHIN_SCOPE: only with a researcher confirmation of a supporting verdict; a `SUPPORTED_IF_QUALIFIED`
    confirmation counts only for the text that carries the qualification. A HIGH-risk statement without such a
    confirmation stays at most PROVISIONAL whoever generated it (I16).
    """
    with store.read() as conn:
        counter = _counter_evidence(conn, store, statement)
        records = _adjudication_for(store, statement.object_id, conn=conn)
    mine = [r for r in records if (r["content"].get("statement") or {}).get("element") == statement.element]
    digest = statement.to_dict()["text_sha256"]
    at_rev = [r for r in mine if (r["content"].get("statement") or {}).get("text_sha256") == digest]
    confirmations = [r for r in at_rev if r["content"].get("confirms")]
    decided = {r["content"]["confirms"] for r in confirmations}
    proposals = [r for r in at_rev if not r["content"].get("confirms")]
    # the effective verdicts for this revision: a researcher's confirmation replaces the proposal it answers
    effective = [r["content"]["eca_verdict"] for r in confirmations] + [
        r["content"]["eca_verdict"] for r in proposals if r["object_id"] not in decided]
    refuted = "NOT_SUPPORTED" in effective
    confirmed = (not refuted) and any(r["content"]["eca_verdict"] in SUPPORTING for r in confirmations)
    if refuted:
        eca_verdict: Optional[str] = "NOT_SUPPORTED"
    elif confirmed:
        eca_verdict = next(r["content"]["eca_verdict"] for r in reversed(confirmations) if r["content"]["eca_verdict"] in SUPPORTING)
    elif effective:
        eca_verdict = "UNRESOLVED" if "UNRESOLVED" in effective else effective[-1]
    else:
        eca_verdict = None
    requested = AssessmentStatus.VERIFIED_WITHIN_SCOPE if confirmed else AssessmentStatus.PROVISIONAL
    assessment = apply_caps(requested, generated_by=statement.generated_by, eca_verdict=eca_verdict,
                            counter_evidence=bool(counter), researcher_confirmed=confirmed)
    reasons: list[str] = []
    if statement.risk_tier == "HIGH" and assessment is AssessmentStatus.VERIFIED_WITHIN_SCOPE and not confirmed:
        assessment = AssessmentStatus.PROVISIONAL
    if counter:
        reasons.append("challenged by " + ", ".join(f"{c['object_id']}@{c['revision']}" for c in counter))
    if refuted:
        reasons.append("an adjudication found the statement not supported by its passage")
    if statement.risk_tier == "HIGH" and not confirmed:
        reasons.append("high-impact statement: needs a researcher-confirmed supporting adjudication to be verified")
    if not mine:
        reasons.append("no adjudication recorded")
    elif not at_rev:
        reasons.append("earlier adjudications read different wording; adjudicate the current text")
    pending_qual = [p for p in mine if p["content"]["eca_verdict"] == "SUPPORTED_IF_QUALIFIED" and not p["content"].get("confirms")]
    if pending_qual and not confirmed:
        reasons.append("supported only if qualified: " + "; ".join(pending_qual[-1]["content"]["required_qualification"]))
    return {"statement": statement.to_dict(), "assessment": assessment.value, "eca_verdict": eca_verdict,
            "researcher_confirmed": confirmed, "counter_evidence": counter, "reasons": reasons,
            "adjudications": [r["object_id"] for r in at_rev]}


def queue(store: StateStore) -> list[dict[str, Any]]:
    """High-impact items waiting for a decision: ECA proposals no researcher has confirmed for the current revision, and
    statements declared HIGH-risk that have no adjudication yet."""
    items: list[dict[str, Any]] = []
    with store.read() as conn:
        adjs = [store.get_object(r[0], conn=conn) for r in conn.execute(
            "SELECT object_id FROM object_heads WHERE object_type='Adjudication' ORDER BY object_id")]
        claims = [store.get_object(r[0], conn=conn) for r in conn.execute(
            "SELECT object_id FROM object_heads WHERE object_type IN ('Claim','KnowledgeItem') ORDER BY object_id")]
    ecas = [a for a in adjs if a["content"].get("kind") == "evidence_claim"]
    confirmed = {a["content"]["confirms"] for a in ecas if a["content"].get("confirms")}
    covered: set[tuple] = set()
    for a in ecas:
        c = a["content"]
        covered.add((c["subject"]["object_id"], (c.get("statement") or {}).get("element")))
        if c.get("confirms") or a["object_id"] in confirmed or c.get("risk_tier") != "HIGH":
            continue
        try:
            now = resolve_statement(store, c["subject"]["object_id"], element=(c.get("statement") or {}).get("element"))
            stale = now.to_dict()["text_sha256"] != (c.get("statement") or {}).get("text_sha256")
        except KnowledgeError:
            stale = True
        items.append({"adjudication_id": a["object_id"], "statement": c["subject"], "element": (c.get("statement") or {}).get("element"),
                      "eca_verdict": c["eca_verdict"], "guard": c.get("guard"), "state": "STALE" if stale else "AWAITING_CONFIRMATION"})
    for obj in claims:
        c = obj["content"]
        if obj["object_type"] == "Claim" and (c.get("attributes") or {}).get("risk_tier") == "HIGH" and (obj["object_id"], None) not in covered:
            items.append({"statement": {"object_id": obj["object_id"], "revision": obj["revision"]}, "element": None,
                          "state": "NOT_ADJUDICATED"})
        if obj["object_type"] == "KnowledgeItem":
            for e in c.get("elements") or []:
                if isinstance(e, dict) and e.get("risk_tier") == "HIGH" and (obj["object_id"], e.get("id")) not in covered:
                    items.append({"statement": {"object_id": obj["object_id"], "revision": obj["revision"]}, "element": e.get("id"),
                                  "state": "NOT_ADJUDICATED"})
    return items


# ---------------------------------------------------------------------------------------------------
# coverage policy (decision D15): every HIGH statement, a stratified 20% of STANDARD ones, more after errors
# ---------------------------------------------------------------------------------------------------

STANDARD_SAMPLE_RATE = 0.20


def mark_high(tx: Any, object_id: str, element: Optional[str] = None) -> bool:
    """Make a statement HIGH risk (a new revision of its object; the statement's words are untouched, so its adjudications
    stay valid). Anything a material conclusion rests on must be HIGH. Returns False if it already was."""
    import json as _json
    cur = tx.get(object_id)
    c = dict(cur["content"])
    if element:
        els = [dict(e) for e in c.get("elements") or []]
        for e in els:
            if e.get("id") == element and e.get("risk_tier") != "HIGH":
                e["risk_tier"] = "HIGH"
                c["elements"] = els
                break
        else:
            return False
    else:
        attrs = dict(c.get("attributes") or {})
        if attrs.get("risk_tier") == "HIGH":
            return False
        attrs["risk_tier"] = "HIGH"
        c["attributes"] = attrs
    rows = tx.conn.execute("SELECT relation,target_id,target_revision,provenance FROM edges WHERE source_id=? AND source_revision=?",
                           (object_id, cur["revision"])).fetchall()
    tx.add_revision(object_id=object_id, object_type=cur["object_type"], content=c, edges=[(r[0], r[1], r[2], _json.loads(r[3])) for r in rows],
                    origin=engine_origin("risk_tier_high", tx.actor_id))
    return True


def use_in_conclusion(store: StateStore, token: Optional[str], statements: list[tuple[str, Optional[str]]]) -> dict[str, Any]:
    """Declare that a material conclusion rests on these statements `(object_id, element)`: they become HIGH."""
    changed = []
    with store.write_tx(token=token, require_roles=WRITE_ROLES) as tx:
        for oid, element in statements:
            head_or_raise(tx, oid)
            if mark_high(tx, oid, element):
                changed.append({"object_id": oid, "element": element})
    return {"made_high": changed, "already_high": len(statements) - len(changed)}


def iter_statements(store: StateStore) -> list[dict[str, Any]]:
    """Every statement ECA can be asked about: Claims, and the elements of knowledge items, playbooks and arguments."""
    out = []
    with store.read() as conn:
        rows = conn.execute("SELECT object_id, object_type FROM object_heads WHERE object_type IN "
                            "('Claim','KnowledgeItem','MethodPlaybook','ContributionArgument') ORDER BY object_id").fetchall()
        objs = [store.get_object(r[0], conn=conn) for r in rows]
    for o in objs:
        c = o["content"]
        if o["object_type"] == "Claim":
            tier = "HIGH" if (c.get("attributes") or {}).get("risk_tier") == "HIGH" else "STANDARD"
            out.append({"object_id": o["object_id"], "element": None, "stratum": "Claim", "risk_tier": tier,
                        "text_sha256": canonical_json_hash(c["text"])})
            continue
        for e in c.get("elements") or []:
            if isinstance(e, dict) and (e.get("text") or e.get("statement")):
                kind = e.get("category") or e.get("component") or "element"
                out.append({"object_id": o["object_id"], "element": e["id"], "stratum": f"{o['object_type']}:{kind}",
                            "risk_tier": "HIGH" if e.get("risk_tier") == "HIGH" else "STANDARD",
                            "text_sha256": canonical_json_hash(e.get("text") or e.get("statement"))})
    return out


def _adjudicated(store: StateStore) -> set[tuple]:
    done = set()
    with store.read() as conn:
        for r in conn.execute("SELECT object_id FROM object_heads WHERE object_type='Adjudication' ORDER BY object_id"):
            c = store.get_object(r[0], conn=conn)["content"]
            if c.get("kind") == "evidence_claim":
                done.add((c["subject"]["object_id"], (c.get("statement") or {}).get("element"), (c.get("statement") or {}).get("text_sha256")))
    return done


def error_counts(store: StateStore) -> dict[str, int]:
    """Adjudication problems per stratum: a guard that fired, or a verdict other than supported-as-stated. Sampling is raised
    where these occur."""
    strata = {(s["object_id"], s["element"]): s["stratum"] for s in iter_statements(store)}
    counts: dict[str, int] = {}
    with store.read() as conn:
        for r in conn.execute("SELECT object_id FROM object_heads WHERE object_type='Adjudication' ORDER BY object_id"):
            c = store.get_object(r[0], conn=conn)["content"]
            if c.get("kind") != "evidence_claim" or c.get("confirms"):
                continue
            stratum = strata.get((c["subject"]["object_id"], (c.get("statement") or {}).get("element")))
            if stratum and (c.get("guard") or c["eca_verdict"] != "SUPPORTED_AS_STATED"):
                counts[stratum] = counts.get(stratum, 0) + 1
    return counts


def escalated_rate(base: float, errors: int) -> float:
    """The sampling rate after `errors` problems in a stratum: doubled for each, up to everything."""
    return min(1.0, base * (2 ** errors))


def sampling_plan(store: StateStore, *, rate: float = STANDARD_SAMPLE_RATE, seed: str = "eca-sample-v1") -> dict[str, Any]:
    """Which statements to adjudicate: all HIGH, and a deterministic stratified sample of STANDARD ones (at least one per
    stratum), at a rate that doubles for each problem found in the stratum."""
    import hashlib
    import math
    stmts = iter_statements(store)
    errors = error_counts(store)
    high = [s for s in stmts if s["risk_tier"] == "HIGH"]
    by: dict[str, list[dict[str, Any]]] = {}
    for s in stmts:
        if s["risk_tier"] == "STANDARD":
            by.setdefault(s["stratum"], []).append(s)
    sampled, strata = [], {}
    for stratum, items in sorted(by.items()):
        r = escalated_rate(rate, errors.get(stratum, 0))
        k = min(len(items), max(1, math.ceil(r * len(items))))
        ranked = sorted(items, key=lambda s: hashlib.sha256(f"{seed}|{s['object_id']}|{s['element']}".encode()).hexdigest())
        sampled += ranked[:k]
        strata[stratum] = {"statements": len(items), "rate": r, "sampled": k, "errors_found": errors.get(stratum, 0)}
    return {"policy": {"high": "every statement", "standard_rate": rate, "escalation": "the rate doubles for each problem found in a stratum", "seed": seed},
            "high": high, "standard_sampled": sampled, "by_stratum": strata}


def coverage_report(store: StateStore, *, rate: float = STANDARD_SAMPLE_RATE) -> dict[str, Any]:
    """Where the policy stands: HIGH statements not yet adjudicated, and sampled STANDARD statements not yet adjudicated."""
    plan = sampling_plan(store, rate=rate)
    done = _adjudicated(store)
    key = lambda s: (s["object_id"], s["element"], s["text_sha256"])  # noqa: E731
    high_missing = [s for s in plan["high"] if key(s) not in done]
    sample_missing = [s for s in plan["standard_sampled"] if key(s) not in done]
    return {"high_total": len(plan["high"]), "high_adjudicated": len(plan["high"]) - len(high_missing), "high_not_adjudicated": high_missing,
            "standard_sampled": len(plan["standard_sampled"]), "sample_not_adjudicated": sample_missing, "by_stratum": plan["by_stratum"],
            "complete": not high_missing and not sample_missing}


# ---------------------------------------------------------------------------------------------------
# working through many statements: run what the coverage policy asks for, review on one sheet, decide in one file
# ---------------------------------------------------------------------------------------------------

def completeness(store: StateStore, objects: Optional[set[str]] = None) -> dict[str, Any]:
    """Where every HIGH statement (in `objects`, or in the whole store) stands for its CURRENT words:

    confirmed (a researcher confirmation exists), awaiting_confirmation (a proposal exists and nobody has confirmed it), or not_adjudicated (no
    proposal at all: the run was limited, the statement has no passage, nobody ran it). The two open states are different work for the researcher."""
    high = [s for s in iter_statements(store) if s["risk_tier"] == "HIGH" and (objects is None or s["object_id"] in objects)]
    records: dict[tuple, dict[str, Any]] = {}
    with store.read() as conn:
        for r in conn.execute("SELECT object_id FROM object_heads WHERE object_type='Adjudication' ORDER BY object_id"):
            obj = store.get_object(r[0], conn=conn)
            c = obj["content"]
            if c.get("kind") != "evidence_claim":
                continue
            key = (c["subject"]["object_id"], (c.get("statement") or {}).get("element"), (c.get("statement") or {}).get("text_sha256"))
            slot = records.setdefault(key, {"proposals": [], "confirmed": False})
            if c.get("confirms"):
                slot["confirmed"] = True
            else:
                slot["proposals"].append(obj["object_id"])
    confirmed, awaiting, missing = 0, [], []
    for s in high:
        slot = records.get((s["object_id"], s["element"], s["text_sha256"]))
        if slot and slot["confirmed"]:
            confirmed += 1
        elif slot and slot["proposals"]:
            awaiting.append({"object_id": s["object_id"], "element": s["element"], "adjudication_id": slot["proposals"][-1]})
        else:
            missing.append({"object_id": s["object_id"], "element": s["element"]})
    return {"high_total": len(high), "confirmed": confirmed, "awaiting_confirmation": awaiting, "not_adjudicated": missing}


def basis_passage(store: StateStore, object_id: str, element: Optional[str]) -> Optional[str]:
    """The passage an element rests on (its first source-passage basis), as an object id; None for a statement with no passage."""
    obj = store.get_object(object_id)
    el = next((e for e in (obj["content"].get("elements") or []) if isinstance(e, dict) and e.get("id") == element), None) if obj and element else None
    for b in (el or {}).get("evidence_basis", []):
        if b.get("kind") == "source_passage" and b.get("ref"):
            return b["ref"].split("@")[0].replace("LIB:", "") if b["ref"].startswith("LIB:") else b["ref"]
    return None


def run_pending(store: StateStore, token: Optional[str], adapter: Any, *, rate: float = STANDARD_SAMPLE_RATE, limit: Optional[int] = None,
                only_objects: Optional[set[str]] = None, reuse: bool = True, redo_unconfirmed: bool = False) -> dict[str, Any]:
    """Adjudicate (one model call each, proposals only) every statement the coverage policy still asks for: all HIGH ones and the
    sampled STANDARD ones, each against the passage it rests on. A statement with no passage is listed, not guessed at."""
    cov = coverage_report(store, rate=rate)
    todo = cov["high_not_adjudicated"] + cov["sample_not_adjudicated"]
    if only_objects is not None:      # a task works on one playbook, not on the whole project
        todo = [s for s in todo if s["object_id"] in only_objects]
    if redo_unconfirmed:     # a different model is asked again for every statement the researcher has not confirmed; confirmations stand
        have = {(s["object_id"], s["element"]) for s in todo}
        todo += [{"object_id": x["object_id"], "element": x["element"], "risk_tier": "HIGH"} for x in completeness(store, only_objects)["awaiting_confirmation"]
                 if (x["object_id"], x["element"]) not in have]
    done: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for s in todo[:limit] if limit else todo:
        passage = basis_passage(store, s["object_id"], s["element"])
        if passage is None:
            skipped.append({"object_id": s["object_id"], "element": s["element"], "why": "no source passage to adjudicate against"})
            continue
        try:
            res = adjudicate(store, token, resolve_statement(store, s["object_id"], element=s["element"]), passage, adapter, reuse=reuse)
        except KnowledgeError as exc:
            failed.append({"object_id": s["object_id"], "element": s["element"], "error": exc.code})
            continue
        if res.get("status") == "FAILED":
            failed.append({"object_id": s["object_id"], "element": s["element"], "error": res.get("reason")})
            continue
        done.append({"object_id": s["object_id"], "element": s["element"], "adjudication_id": res["adjudication_id"], "eca_verdict": res["eca_verdict"],
                     "guard": res.get("guard"), "tier": s["risk_tier"], "reused": bool(res.get("reused"))})
    verdicts: dict[str, int] = {}
    for d in done:
        verdicts[d["eca_verdict"]] = verdicts.get(d["eca_verdict"], 0) + 1
    return {"adjudicated": len(done), "model_calls": sum(1 for d in done if not d["reused"]), "verdicts": verdicts, "guard_fired": sum(1 for d in done if d["guard"]),
            "skipped": skipped, "failed": failed, "remaining_after_limit": max(0, len(todo) - (limit or len(todo))), "items": done}


def review_sheet(store: StateStore) -> list[dict[str, Any]]:
    """Every high-impact proposal awaiting the researcher, with what is needed to decide it on one page: the statement, the passage,
    the model's reading, the flagged cues and any guard. Items that need attention (a guard, or anything but supported as stated) come first.
    `decision` is left empty; nothing is pre-filled."""
    rows = []
    for item in queue(store):
        if item["state"] != "AWAITING_CONFIRMATION":
            continue
        adj = store.get_object(item["adjudication_id"])["content"]
        passage = store.get_object(adj["passage"]["object_id"])
        stmt = resolve_statement(store, item["statement"]["object_id"], element=item.get("element"))
        rows.append({"adjudication_id": item["adjudication_id"], "statement": {"object_id": stmt.object_id, "element": stmt.element, "text": stmt.text, "generated_by": stmt.generated_by},
                     "passage": {"object_id": adj["passage"]["object_id"], "quote": (passage or {}).get("content", {}).get("quote")},
                     "model": {"verdict": adj["eca_verdict"], "equivalence": adj["equivalence"], "proposition": adj["passage_proposition"],
                               "dropped_conditions": adj["dropped_conditions"], "required_qualification": adj["required_qualification"], "guard": adj.get("guard")},
                     "qualifier_cues": [c["cue"] for c in adj["qualifier_cues"]], "needs_attention": bool(adj.get("guard")) or adj["eca_verdict"] != "SUPPORTED_AS_STATED",
                     "decision": {"verdict": "", "rationale": "", "qualification_revision": None}})
    return sorted(rows, key=lambda r: (not r["needs_attention"], r["adjudication_id"]))


def confirm_batch(store: StateStore, token: Optional[str], decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply the researcher's decisions from one file. Each item is its own confirmation (author or owner token), with its own verdict and
    rationale; an item that is incomplete or refused is reported and the others are still applied. There is no default verdict."""
    confirmed, failed, seen = [], [], set()
    for d in decisions:
        aid = d.get("adjudication_id")
        if not aid or aid in seen:
            failed.append({"adjudication_id": aid, "error": "MISSING_OR_DUPLICATE_ID"})
            continue
        seen.add(aid)
        if not d.get("verdict"):
            failed.append({"adjudication_id": aid, "error": "NO_VERDICT: a decision is the researcher's; there is no default"})
            continue
        qual = {"object_id": (store.get_object(aid) or {}).get("content", {}).get("subject", {}).get("object_id"), "revision": d["qualification_revision"]} \
            if d.get("qualification_revision") else None
        try:
            r = confirm(store, token, aid, verdict=d["verdict"], rationale=d.get("rationale") or "", qualification_applied_in=qual)
            confirmed.append({"adjudication_id": aid, "confirmation": r["adjudication_id"], "verdict": d["verdict"]})
        except (KnowledgeError, AuthorizationError, ValueError) as exc:
            failed.append({"adjudication_id": aid, "error": getattr(exc, "code", None) or str(exc)})
    return {"confirmed": confirmed, "failed": failed}
