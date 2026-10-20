"""Model request/response contract for model-backed workflow runs (plan v3, Phase 1).

* `output_schema(workflow)`: JSON Schema derived from the workflow's result dataclass, plus mandatory
  `evidence_refs`.
* `build_system_prompt(...)`: selected skill instructions + output contract (the only skill text a request
  carries; unselected skills never appear).
* `validate_model_output(...)`: three layers that are all required before a model answer is accepted:
    1. it parses and satisfies the schema;
    2. every cited object exists in the request, at a revision that is in the request (no invented citations);
    3. every quoted span exists verbatim in the text shown to the model (offsets are computed here, never
       trusted from the model).
  These establish that the answer is well-formed and cites real evidence. They do NOT establish that the
  answer reads that evidence correctly; that is Evidence-Claim Adjudication (Phase 3A.2), so an accepted
  model answer is still only PROVISIONAL.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import re
import types
import typing
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from jsonschema import Draft202012Validator

from research_harness.workflows import quotes
from research_harness.workflows.contracts import (
    ContextPackage,
    ContributionFindingResult,
    DiscussionResult,
    LiteratureReviewResult,
    MethodsEvaluationResult,
    ResultsInterpretationResult,
)

CONTRACT_VERSION = "1"

RESULT_TYPES: dict[str, type] = {
    "contribution": ContributionFindingResult,
    "literature": LiteratureReviewResult,
    "methods": MethodsEvaluationResult,
    "results": ResultsInterpretationResult,
    "discussion": DiscussionResult,
}

_MAX_REPORTED_ERRORS = 20

REF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "object_id": {"type": "string"},
        "revision": {"type": "integer"},
        "quote": {"type": "string"},
        "char_start": {"type": "integer", "minimum": 0},
        "char_end": {"type": "integer", "minimum": 0},
    },
    "required": ["object_id", "revision"],
}


# ---------------------------------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------------------------------

def _json_type(tp: Any) -> dict[str, Any]:
    if tp is Any:
        return {}
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin in (list, typing.List):  # noqa: UP006
        return {"type": "array", "items": _json_type(args[0]) if args else {}}
    if origin in (dict, typing.Dict):  # noqa: UP006
        return {"type": "object"}
    if origin is typing.Union or (hasattr(types, "UnionType") and origin is getattr(types, "UnionType")):
        non_null = [a for a in args if a is not type(None)]
        inner = [_json_type(a) for a in non_null]
        if len(non_null) != len(args):
            inner.append({"type": "null"})
        return inner[0] if len(inner) == 1 else {"anyOf": inner}
    if tp is bool:
        return {"type": "boolean"}
    if tp is int:
        return {"type": "integer"}
    if tp is float:
        return {"type": "number"}
    if tp is str:
        return {"type": "string"}
    return {}


def output_schema(workflow: str) -> dict[str, Any]:
    """JSON Schema of a model answer: the workflow's result fields, mandatory `evidence_refs`, optional `gaps`."""
    wf = workflow.strip().lower()
    if wf not in RESULT_TYPES:
        raise KeyError(f"Unknown workflow '{workflow}'. Available: {sorted(RESULT_TYPES)}")
    cls = RESULT_TYPES[wf]
    hints = typing.get_type_hints(cls)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for f in dataclasses.fields(cls):
        properties[f.name] = _json_type(hints[f.name])
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:  # type: ignore[misc]
            required.append(f.name)
    properties["evidence_refs"] = {"type": "array", "items": REF_SCHEMA}
    required.append("evidence_refs")
    properties["gaps"] = {"type": "array", "items": {"type": "string"}}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": f"{wf}_assessment",
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": True,
    }


def schema_digest(workflow: str) -> str:
    raw = json.dumps(output_schema(workflow), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((CONTRACT_VERSION + raw).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------------------------------
# prompts
# ---------------------------------------------------------------------------------------------------

def build_system_prompt(workflow: str, skill_instructions: str, schema: Optional[dict[str, Any]] = None) -> str:
    schema = schema if schema is not None else output_schema(workflow)
    contract = (
        "## Output contract\n"
        "Respond with exactly one JSON object and nothing else (no prose, no code fence). It must validate "
        "against this JSON Schema:\n"
        f"{json.dumps(schema, indent=2, sort_keys=True)}\n\n"
        "Rules:\n"
        "- Cite evidence only as objects with `object_id` and `revision` that appear in the request "
        "(`evidence_refs`). To cite a passage of an object's text, add `quote` with the exact words; do not "
        "estimate character offsets. Do not cite anything that is not in the request.\n"
        "- Every substantive statement must be supported by cited evidence. Where the request lacks evidence "
        "that an assessment needs, say so in `gaps` and do not assert the conclusion.\n"
        "- An author's own statement of novelty or contribution is not independent evidence.\n"
        "- A missing input is unknown, not absent: do not fill it with a plausible default.\n"
    )
    return (
        f"You are executing the {workflow.upper()} workflow of a research assistant that supports a single "
        f"academic researcher.\n\n## Operational Skill Instructions\n{skill_instructions.strip()}\n\n{contract}"
    )


# ---------------------------------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------------------------------

@dataclass
class ValidationOutcome:
    result: Optional[dict[str, Any]]
    errors: list[str] = field(default_factory=list)
    refs_checked: int = 0
    spans_verified: int = 0
    raw_text: Optional[str] = None

    @property
    def ok(self) -> bool:
        return not self.errors and self.result is not None


_FENCE = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n(.*?)\n?```\s*$", re.DOTALL)


def parse_model_output(raw: Any) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Return `(object, None)` or `(None, error)`. Strings are parsed as JSON (a single code fence is tolerated)."""
    if isinstance(raw, dict):
        return copy.deepcopy(raw), None
    if isinstance(raw, str):
        text = raw.strip()
        m = _FENCE.match(text)
        if m:
            text = m.group(1).strip()
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            return None, f"output is not valid JSON ({exc.msg} at char {exc.pos})"
        if not isinstance(value, dict):
            return None, f"top-level JSON must be an object, got {type(value).__name__}"
        return value, None
    return None, f"unsupported output type {type(raw).__name__}"


def iter_evidence_refs(node: Any, path: str = "$") -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield `(path, ref)` for every reference in an `evidence_refs` / `evidence_basis` list, at any depth."""
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}"
            if key in ("evidence_refs", "evidence_basis") and isinstance(value, list):
                for i, item in enumerate(value):
                    if isinstance(item, dict):
                        yield f"{child}[{i}]", item
            else:
                yield from iter_evidence_refs(value, child)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            yield from iter_evidence_refs(item, f"{path}[{i}]")


_TEXT_FIELDS = ("quote", "excerpt", "text", "abstract", "summary", "normalized_claim")


def canonical_text(context: ContextPackage, object_id: str) -> Optional[str]:
    """The text of an object as shown to the model: loaded full text if any, else its first text field."""
    for ft in context.full_texts:
        if ft.get("object_id") == object_id or ft.get("source_id") == object_id:
            if ft.get("full_text"):
                return ft["full_text"]
    for obj in context.objects:
        if obj.get("object_id") == object_id:
            content = obj.get("content") or {}
            for name in _TEXT_FIELDS:
                value = content.get(name)
                if isinstance(value, str) and value:
                    return value
    return None


def validate_model_output(workflow: str, raw: Any, context: ContextPackage,
                          schema: Optional[dict[str, Any]] = None) -> ValidationOutcome:
    schema = schema if schema is not None else output_schema(workflow)
    raw_text = raw if isinstance(raw, str) else None
    parsed, parse_error = parse_model_output(raw)
    if parsed is None:
        return ValidationOutcome(None, [parse_error or "unparseable output"], raw_text=raw_text)

    errors: list[str] = []
    for err in sorted(Draft202012Validator(schema).iter_errors(parsed), key=lambda e: list(map(str, e.absolute_path))):
        where = ".".join(str(p) for p in err.absolute_path) or "$"
        errors.append(f"schema: {where}: {err.message[:200]}")
        if len(errors) >= _MAX_REPORTED_ERRORS:
            break

    included = {(o.get("object_id"), o.get("revision")) for o in context.objects}
    refs_checked = spans_verified = 0
    for path, ref in iter_evidence_refs(parsed):
        oid, rev = ref.get("object_id"), ref.get("revision")
        if oid is None or rev is None:
            continue  # reported by the schema check when the ref sits under `evidence_refs`
        refs_checked += 1
        if (oid, rev) not in included:
            errors.append(f"reference: {path}: {oid}@{rev} is not in the request")
            continue
        quote, start, end = ref.get("quote"), ref.get("char_start"), ref.get("char_end")
        if quote is None and start is None and end is None:
            continue
        text = canonical_text(context, oid)
        check = quotes.verify_span(text, start, end, quote)
        if not check.ok:
            errors.append(f"quote: {path}: {oid}@{rev}: {check.reason}")
        else:
            ref["char_start"], ref["char_end"] = check.start, check.end
            ref["quote_verified"] = True
            spans_verified += 1
        if len(errors) >= _MAX_REPORTED_ERRORS:
            break

    return ValidationOutcome(parsed if not errors else None, errors, refs_checked, spans_verified, raw_text)


def detect_gate_conflicts(model_result: dict[str, Any], gates: dict[str, Any]) -> list[str]:
    """A model answer may not report a more favourable status than a deterministic gate allows."""
    conflicts = []
    for key in ("identification_status", "novelty_status"):
        mine, theirs = model_result.get(key), gates.get(key)
        if isinstance(mine, str) and isinstance(theirs, str) and mine != theirs and theirs.endswith("NOT_VERIFIED"):
            conflicts.append(f"{key}: model reports '{mine}' but the evidence gate is '{theirs}'")
    return conflicts
