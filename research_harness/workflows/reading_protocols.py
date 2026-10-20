"""Reading protocols (plan v3, 3C.0): what to extract from a paper, section by section, and verify before it is kept.

A protocol is data (`protocols_data/reading_protocols.json`): the fields to extract and the comparison dimension
whose sections are read for them. `run_protocol` reads only those sections of a stored Source, asks the model for
one record per finding, and keeps a record only if

1. it names a field of the protocol, and
2. its quote occurs verbatim in the text the model was shown (the span is computed here, never trusted from the
   model), and the same span re-verifies against the whole stored text.

Records that fail are listed under `rejected` with the reason; they are not stored. Accepted records become
`EvidencePassage` objects that point at the Source revision and carry the model-generated `value` apart from the
verbatim `quote` (invariant I9). A verified quote shows that the passage exists; it does not show that the `value`
is a fair reading of it, so every record stays `PROVISIONAL` until it is adjudicated (ECA).

The extraction is cached by `(source revision and text, protocol@version)`; a repeat run is served from the run
registry (and registered as a reuse). The cache key deliberately omits the model: use `reuse=False` to re-extract.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from jsonschema import Draft202012Validator

from research_harness.core.canonical import canonical_json_hash
from research_harness.knowledge.common import WRITE_ROLES, KnowledgeError, engine_origin, id_fragment, scope_id
from research_harness.knowledge.corpus import source_sections, source_text
from research_harness.state import workflow_runs
from research_harness.state.store import StateStore
from research_harness.workflows import quotes
from research_harness.workflows.adapters import ModelError, ModelOutputTruncated, ModelRefusal, adapter_identity
from research_harness.workflows.instrumentation import estimate_tokens
from research_harness.workflows.model_io import parse_model_output
from research_harness.workflows.sections import Section, sections_for_dimension
from research_harness.workflows.status import (
    Applicability, AssessmentStatus, ContextStatus, ExecutionStatus, apply_caps)

_DATA = Path(__file__).resolve().parent / "protocols_data" / "reading_protocols.json"
EXECUTOR = "reading_protocol@1"
MIN_QUOTE_CHARS = 12
MIN_TEXT_BUDGET = 50  # tokens of source text that must fit beside the instructions
DEFAULT_MAX_INPUT_TOKENS = 12000


@dataclass(frozen=True)
class ReadingProtocol:
    id: str
    version: str
    label: str
    purpose: str
    dimension: str
    instructions: str
    fields: tuple[dict[str, Any], ...]

    @property
    def pin(self) -> str:
        return f"{self.id}@{self.version}"

    @property
    def digest(self) -> str:
        return canonical_json_hash({"id": self.id, "version": self.version, "dimension": self.dimension,
                                    "instructions": self.instructions, "fields": list(self.fields)})

    def field(self, name: str) -> Optional[dict[str, Any]]:
        return next((f for f in self.fields if f["name"] == name), None)

    def output_schema(self) -> dict[str, Any]:
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema", "title": f"{self.id}_extraction", "type": "object",
            "properties": {
                "records": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"field": {"enum": [f["name"] for f in self.fields]},
                                   "value": {"type": "string", "minLength": 1},
                                   "quote": {"type": "string", "minLength": 1}},
                    "required": ["field", "value", "quote"]}},
                "gaps": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["records"], "additionalProperties": True,
        }


@lru_cache(maxsize=1)
def _load() -> dict[str, ReadingProtocol]:
    raw = json.loads(_DATA.read_text(encoding="utf-8"))
    return {pid: ReadingProtocol(pid, p["version"], p["label"], p["purpose"], p["dimension"], p["instructions"],
                                 tuple(p["fields"])) for pid, p in raw["protocols"].items()}


def list_protocols() -> list[ReadingProtocol]:
    return [_load()[k] for k in sorted(_load())]


def get_protocol(protocol_id: str) -> ReadingProtocol:
    pid = protocol_id.split("@")[0]
    if pid not in _load():
        raise KnowledgeError("PROTOCOL_UNKNOWN", f"{protocol_id}: known protocols are {sorted(_load())}")
    return _load()[pid]


def extraction_key(source_id: str, source_revision: int, text_digest: str, protocol: ReadingProtocol) -> str:
    return f"{source_id}@{source_revision}#{text_digest[:16]}|{protocol.pin}"


def _text_digest(obj: dict[str, Any], text: str) -> str:
    return obj["content"].get("text_sha256") or hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fingerprint(key: str, protocol: ReadingProtocol) -> str:
    return canonical_json_hash({"extraction_key": key, "protocol_digest": protocol.digest})


# ---------------------------------------------------------------------------------------------------
# prompts and chunking
# ---------------------------------------------------------------------------------------------------

def build_system_prompt(protocol: ReadingProtocol) -> str:
    fields = "\n".join(f"- `{f['name']}`: {f['description']}" + (" (may repeat)" if f.get("repeatable") else " (once)")
                       for f in protocol.fields)
    return (
        f"You are extracting facts from one academic paper for a research assistant ({protocol.id}: {protocol.label}).\n\n"
        f"Purpose: {protocol.purpose}\n\n## Instructions\n{protocol.instructions}\n\n## Fields\n{fields}\n\n"
        "## Output contract\nRespond with exactly one JSON object and nothing else. It must validate against this JSON Schema:\n"
        f"{json.dumps(protocol.output_schema(), indent=2, sort_keys=True)}\n\n"
        "Rules:\n- `quote` must be an exact span copied from the text you are shown, long enough to be unambiguous. "
        "Do not paraphrase inside `quote`. A record without a verbatim quote is discarded.\n"
        "- `value` is your concise statement of what the quote says for that field. Do not add anything the quote does not say.\n"
        "- If the text does not state something a field asks for, omit the field and name it in `gaps`. "
        "Do not fill it from general knowledge.\n")


def build_user_prompt(obj: dict[str, Any], section_note: str, text: str, part: int, parts: int) -> str:
    c = obj["content"]
    head = f"Source: {c.get('citation_key', obj['object_id'])} ({obj['object_id']} revision {obj['revision']})"
    if c.get("title"):
        head += f"\nTitle: {c['title']}"
    return f"{head}\nSections shown: {section_note}\nPart {part} of {parts}\n\n--- TEXT START ---\n{text}\n--- TEXT END ---"


def chunk_ranges(spans: list[tuple[int, int]], text: str, budget_tokens: int) -> list[tuple[int, int]]:
    """Split the selected character spans into ranges that each fit `budget_tokens` (paragraph boundaries first)."""
    merged: list[list[int]] = []
    for start, end in sorted(spans):  # touching sections are read as one stretch of text
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    out: list[tuple[int, int]] = []
    for start, end in merged:
        pos = start
        while pos < end:
            hi = end
            while hi - pos > 1 and estimate_tokens(text[pos:hi])[0] > budget_tokens:
                # shrink to the nearest earlier paragraph break, else by a third
                cut = text.rfind("\n\n", pos + 1, hi)
                hi = cut if cut > pos else pos + max(1, (hi - pos) * 2 // 3)
            out.append((pos, hi))
            pos = hi
    return out


# ---------------------------------------------------------------------------------------------------
# validation of one model answer
# ---------------------------------------------------------------------------------------------------

def validate_extraction(protocol: ReadingProtocol, raw: Any, chunk_text: str, chunk_start: int, full_text: str,
                        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], Optional[str]]:
    """`(accepted, rejected, gaps, error)`. `error` is set when the answer as a whole is unusable."""
    parsed, err = parse_model_output(raw)
    if parsed is None:
        return [], [], [], err
    problems = sorted(Draft202012Validator(protocol.output_schema()).iter_errors(parsed), key=lambda e: list(map(str, e.absolute_path)))
    if problems:
        return [], [], [], "schema: " + "; ".join(
            f"{'.'.join(str(p) for p in e.absolute_path) or '$'}: {e.message[:120]}" for e in problems[:5])
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    counts: dict[str, int] = {}
    for rec in parsed["records"]:
        reject = lambda why: rejected.append({**rec, "reason": why})  # noqa: E731
        spec = protocol.field(rec["field"])
        quote = rec["quote"]
        if len(quotes.normalize_whitespace(quote)) < MIN_QUOTE_CHARS:
            reject("quote_too_short")
            continue
        found = quotes.find_quote(chunk_text, quote)
        if not found:
            reject("quote_not_in_text_shown")
            continue
        start, end = chunk_start + found[0], chunk_start + found[1]
        check = quotes.verify_span(full_text, start, end, quote)
        if not check.ok:
            reject("span_does_not_verify_against_stored_text")
            continue
        if (rec["field"], start, end) in seen:
            reject("duplicate_record")
            continue
        if counts.get(rec["field"], 0) >= 1 and not (spec or {}).get("repeatable"):
            reject("field_not_repeatable")
            continue
        seen.add((rec["field"], start, end))
        counts[rec["field"]] = counts.get(rec["field"], 0) + 1
        accepted.append({"field": rec["field"], "value": rec["value"].strip(), "quote": full_text[start:end],
                         "char_start": start, "char_end": end})
    return accepted, rejected, [str(g) for g in parsed.get("gaps", [])], None


# ---------------------------------------------------------------------------------------------------
# running a protocol
# ---------------------------------------------------------------------------------------------------

def _register(store: StateStore, *, fingerprint: str, execution: ExecutionStatus, context: ContextStatus,
              assessment: AssessmentStatus, legacy_status: str, reason: Optional[str], usage: dict, model: Optional[dict],
              artifact: Optional[str], source_id: str, source_revision: int, text_digest: str,
              reused_from: Optional[str] = None, protocol: ReadingProtocol) -> str:
    return workflow_runs.append_run(store, {
        "workflow": f"reading_protocol:{protocol.id}", "input_fingerprint": fingerprint, "executor": EXECUTOR,
        "execution_status": execution.value, "context_status": context.value, "assessment_status": assessment.value,
        "applicability": Applicability.APPLICABLE.value, "legacy_status": legacy_status, "status_reason": reason,
        "usage": usage, "model_descriptor": model, "artifact_sha256": artifact, "reused_from_run_id": reused_from,
        "input_revisions": [{"object_id": source_id, "revision": source_revision, "content_digest": text_digest}]})


def _passage_content(protocol: ReadingProtocol, obj: dict[str, Any], rec: dict[str, Any], key: str, model: dict[str, Any],
                     section: str) -> dict[str, Any]:
    c = {"label": f"{protocol.id} {rec['field']} in {obj['content'].get('citation_key', obj['object_id'])}",
         "locator": f"{obj['object_id']} chars {rec['char_start']}-{rec['char_end']}", "quote": rec["quote"],
         "normalized_claim": rec["value"][:5000], "value": rec["value"][:5000], "source_id": obj["object_id"],
         "source_revision": obj["revision"], "char_start": rec["char_start"], "char_end": rec["char_end"],
         "quote_verified": True, "protocol": protocol.pin, "field": rec["field"],
         "generated_by": f"model:{model.get('model_id') or model.get('provider')}", "extraction_key": key, "model": model}
    if section:
        c["section"] = section
    return c


def _store_records(store: StateStore, token: Optional[str], protocol: ReadingProtocol, obj: dict[str, Any],
                   records: list[dict[str, Any]], key: str, model: dict[str, Any], sections: list[dict[str, Any]],
                   ) -> list[dict[str, Any]]:
    stored: list[dict[str, Any]] = []
    frag = id_fragment(obj["object_id"], 28)
    proto = id_fragment(protocol.id.replace("RP_", ""), 12)
    with store.write_tx(token=token, require_roles=WRITE_ROLES) as tx:
        for rec in records:
            section = next((s for s in sections if s["char_start"] <= rec["char_start"] < s["char_end"]), None)
            content = _passage_content(protocol, obj, rec, key, model, (section or {}).get("heading") or (section or {}).get("label") or "")
            pid = scope_id(store, f"EP_{frag}_{proto}_{rec['char_start']}")
            head = tx.head(pid)
            if head:
                cur = tx.get(pid)
                if cur["content"] == content:
                    stored.append({**rec, "passage_id": pid, "revision": cur["revision"]})
                    continue
            res = tx.add_revision(object_id=pid, object_type="EvidencePassage", content=content,
                                  edges=[("derived_from", obj["object_id"], obj["revision"], {"origin": "reading_protocol"})],
                                  origin=engine_origin("extract", tx.actor_id, protocol=protocol.pin, extraction_key=key))
            stored.append({**rec, "passage_id": pid, "revision": res["revision"]})
        tx.audit("extract", "RECORDED", {"protocol": protocol.pin, "source": obj["object_id"], "records": len(stored)})
    return stored


def run_protocol(store: StateStore, token: Optional[str], source_id: str, protocol_id: str, adapter: Any, *,
                 source_revision: Optional[int] = None, max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS,
                 reuse: bool = True) -> dict[str, Any]:
    """Extract `protocol_id` from the stored text of `source_id`; see the module docstring for what is kept."""
    protocol = get_protocol(protocol_id)
    obj, text = source_text(store, source_id, source_revision)
    text_digest = _text_digest(obj, text)
    key = extraction_key(source_id, obj["revision"], text_digest, protocol)
    fingerprint = _fingerprint(key, protocol)
    wf = f"reading_protocol:{protocol.id}"

    if reuse:
        with store.read() as conn:
            row = workflow_runs.latest_reusable(conn, wf, fingerprint, ["SUCCESS"])
        cached = _load_cached(store, row)
        if cached is not None:
            run_id = _register(store, fingerprint=fingerprint, execution=ExecutionStatus.SUCCEEDED,
                               context=ContextStatus(row["context_status"]), assessment=AssessmentStatus(row["assessment_status"]),
                               legacy_status="SUCCESS", reason="reused", usage={"llm_calls": 0, "usage_source": "not_available"},
                               model=row.get("model_descriptor"), artifact=row["artifact_sha256"], source_id=source_id,
                               source_revision=obj["revision"], text_digest=text_digest, reused_from=row["run_id"],
                               protocol=protocol)
            return {**cached, "reused": True, "run_id": run_id, "reused_from_run_id": row["run_id"]}

    sections_all = source_sections(store, obj, text)
    chosen = sections_for_dimension([Section(**s) for s in sections_all], protocol.dimension)
    fallback = not chosen
    spans = [(s.char_start, s.char_end) for s in chosen] or [(0, len(text))]
    section_note = ", ".join(s.heading or s.label for s in chosen) if chosen else "whole text (no section matched)"
    system = build_system_prompt(protocol)
    budget = max_input_tokens - estimate_tokens(system)[0] - 400
    if budget < MIN_TEXT_BUDGET:
        raise KnowledgeError("BUDGET_TOO_SMALL", f"max_input_tokens={max_input_tokens} leaves no room for text")
    ranges = chunk_ranges(spans, text, budget)
    model = adapter_identity(adapter)

    accepted_all: list[dict[str, Any]] = []
    rejected_all: list[dict[str, Any]] = []
    gaps: list[str] = []
    usage = {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "usage_source": "scripted_or_provider"}
    failure: Optional[tuple[str, str]] = None
    for i, (lo, hi) in enumerate(ranges, 1):
        prompt = build_user_prompt(obj, section_note, text[lo:hi], i, len(ranges))
        try:
            raw, u = adapter.generate(system, prompt, expected_schema=protocol.output_schema(), temperature=0.0)
        except ModelRefusal as exc:
            failure = ("refusal", str(exc))
            break
        except ModelOutputTruncated as exc:
            failure = ("invalid_output", "model output truncated: " + str(exc))
            break
        except ModelError as exc:
            failure = ("exception", str(exc))
            break
        usage["llm_calls"] += 1
        usage["input_tokens"] += getattr(u, "input_tokens", 0) or 0
        usage["output_tokens"] += getattr(u, "output_tokens", 0) or 0
        usage["usage_source"] = getattr(u, "usage_source", usage["usage_source"])
        accepted, rejected, part_gaps, error = validate_extraction(protocol, raw, text[lo:hi], lo, text)
        if error:
            failure = ("invalid_output", error)
            break
        accepted_all += accepted
        rejected_all += rejected
        gaps += part_gaps
    if failure:
        reason, message = failure
        run_id = _register(store, fingerprint=fingerprint, execution=ExecutionStatus.FAILED, context=ContextStatus.PARTIAL,
                           assessment=AssessmentStatus.NOT_ASSESSED, legacy_status="FAILED", reason=reason, usage=usage,
                           model=model, artifact=None, source_id=source_id, source_revision=obj["revision"],
                           text_digest=text_digest, protocol=protocol)
        return {"protocol": protocol.pin, "source_id": source_id, "source_revision": obj["revision"], "status": "FAILED",
                "reason": reason, "message": message, "records": [], "rejected": rejected_all, "run_id": run_id, "reused": False}

    # one record per (field, span) across chunks
    uniq: dict[tuple, dict[str, Any]] = {}
    for rec in accepted_all:
        uniq.setdefault((rec["field"], rec["char_start"], rec["char_end"]), rec)
    stored = _store_records(store, token, protocol, obj, list(uniq.values()), key, model, sections_all)
    context = ContextStatus.PARTIAL if fallback else ContextStatus.COMPLETE
    assessment = apply_caps(AssessmentStatus.PROVISIONAL if stored else AssessmentStatus.NOT_ASSESSED, context=context,
                            generated_by=f"model:{model.get('model_id')}")
    result = {"protocol": protocol.pin, "protocol_digest": protocol.digest, "source_id": source_id,
              "source_revision": obj["revision"], "extraction_key": key, "status": "EXTRACTED" if stored else "NO_RECORDS",
              "records": stored, "rejected": rejected_all, "gaps": sorted(set(gaps)), "chunks": len(ranges),
              "sections_read": [s.heading or s.label for s in chosen], "section_fallback": fallback,
              "context_status": context.value, "assessment": assessment.value, "model": model}
    artifact = store.artifacts.put_bytes(json.dumps(result, sort_keys=True, default=str).encode("utf-8"))
    run_id = _register(store, fingerprint=fingerprint, execution=ExecutionStatus.SUCCEEDED, context=context,
                       assessment=assessment, legacy_status="SUCCESS", reason=None, usage=usage, model=model,
                       artifact=artifact, source_id=source_id, source_revision=obj["revision"], text_digest=text_digest,
                       protocol=protocol)
    return {**result, "reused": False, "run_id": run_id}


def _load_cached(store: StateStore, row: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """The stored extraction of a registry row, if its artifact verifies and its passages are still as recorded."""
    if not row or not row.get("artifact_sha256") or not store.artifacts.has(row["artifact_sha256"]):
        return None
    try:
        result = json.loads(store.artifacts.get_bytes(row["artifact_sha256"]).decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    for rec in result.get("records", []):
        p = store.get_object(rec["passage_id"], rec["revision"])
        if not p or p["content"].get("extraction_key") != result.get("extraction_key"):
            return None
    return result


def extraction_status(store: StateStore, source_id: str, protocol_id: str) -> dict[str, Any]:
    """Is there a verified extraction of this protocol for the current revision of the Source?"""
    protocol = get_protocol(protocol_id)
    obj, text = source_text(store, source_id)
    key = extraction_key(source_id, obj["revision"], _text_digest(obj, text), protocol)
    with store.read() as conn:
        row = workflow_runs.latest_reusable(conn, f"reading_protocol:{protocol.id}", _fingerprint(key, protocol), ["SUCCESS"])
    return {"protocol": protocol.pin, "extraction_key": key, "cached": _load_cached(store, row) is not None,
            "run_id": row["run_id"] if row else None}
