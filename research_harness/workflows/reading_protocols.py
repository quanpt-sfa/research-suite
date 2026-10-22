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
registry (and registered as a reuse). For the protocols that existed before WP6.2 the cache key deliberately omits the model: use `reuse=False` to
re-extract, and read `reuse.model_matches` in the result to see whether a reused extraction came from the model now asked for. A protocol with
`model_scoped: true` (the claim protocols, WP6.2) puts the model identity and the extraction configuration (chunk size, the spans read, the extra
context) into the key, so another model or other settings is another run and is never served another run's records.

Options a protocol may use (all absent from the digest of a protocol that does not use them, so older pins still resolve): `all_sections` with
`exclude_labels` (read every detected section except those), `attribute_schema` (a JSON Schema for an `attributes` object each record may carry;
the suite, not the model, decides the basis of what an attribute says), `extra_quotes` (further verbatim spans of a record, each verified, each
stored as its own passage), `one_record_per_span` (a span is one record whatever its field).
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
    extra_dimensions: tuple[str, ...] = ()   # further comparison dimensions whose sections are read as well
    include_unlabeled: bool = False          # also read sections whose heading matched no label (`other`)
    model_scoped: bool = False               # the cache key includes the model identity and the extraction configuration
    all_sections: bool = False               # read every detected section (except `exclude_labels`) instead of the sections of a dimension
    exclude_labels: tuple[str, ...] = ()
    attribute_schema: Optional[dict[str, Any]] = None   # JSON Schema of the `attributes` object a record may carry
    extra_quotes: bool = False               # a record may carry further verbatim spans (`extra_quotes`: [{quote, role}])
    one_record_per_span: bool = False        # a span is one record whatever its field, and its passage id includes the span's end

    @property
    def dimensions(self) -> tuple[str, ...]:
        return (self.dimension, *self.extra_dimensions)

    @property
    def pin(self) -> str:
        return f"{self.id}@{self.version}"

    @property
    def digest(self) -> str:
        body: dict[str, Any] = {"id": self.id, "version": self.version, "dimension": self.dimension,
                                "extra_dimensions": list(self.extra_dimensions), "include_unlabeled": self.include_unlabeled, "instructions": self.instructions, "fields": list(self.fields)}
        options = {"model_scoped": self.model_scoped or None, "all_sections": self.all_sections or None, "exclude_labels": list(self.exclude_labels) or None,
                   "attribute_schema": self.attribute_schema, "extra_quotes": self.extra_quotes or None, "one_record_per_span": self.one_record_per_span or None}
        body.update({k: v for k, v in options.items() if v is not None})      # an option that is not used is not part of the pin
        return canonical_json_hash(body)

    def field(self, name: str) -> Optional[dict[str, Any]]:
        return next((f for f in self.fields if f["name"] == name), None)

    def validation_schema(self) -> dict[str, Any]:
        return _relaxed(self.output_schema())

    def output_schema(self) -> dict[str, Any]:
        item_props: dict[str, Any] = {"field": {"enum": [f["name"] for f in self.fields]},
                                      "value": {"type": "string", "minLength": 1},
                                      "quote": {"type": "string", "minLength": 1},
                                      "label": {"type": "string"}, "type": {"type": "string"}}
        if self.attribute_schema:
            item_props["attributes"] = self.attribute_schema
        if self.extra_quotes:
            item_props["extra_quotes"] = {"type": "array", "items": {"type": "object", "properties": {"quote": {"type": "string", "minLength": 1}, "role": {"type": "string"},
                                                                                                    "why": {"type": "string"}}, "required": ["quote"]}}
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema", "title": f"{self.id}_extraction", "type": "object",
            "properties": {
                "records": {"type": "array", "items": {
                    "type": "object",
                    "properties": item_props,
                    "required": ["field", "value", "quote"]}},
                "gaps": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["records"], "additionalProperties": True,
        }


def _relaxed(schema: dict[str, Any]) -> dict[str, Any]:
    """The schema the model's answer as a whole is checked against: the shown schema, but with `attributes` and `extra_quotes` left to a per-record check, so that one
    malformed attribute loses that attribute and not the whole answer."""
    items = schema["properties"]["records"]["items"]
    props = {**items["properties"]}
    for name in ("attributes", "extra_quotes"):
        if name in props:
            props[name] = {"type": "object"} if name == "attributes" else {"type": "array"}
    return {**schema, "properties": {**schema["properties"], "records": {**schema["properties"]["records"], "items": {**items, "properties": props}}}}


@lru_cache(maxsize=1)
def _load() -> dict[str, ReadingProtocol]:
    raw = json.loads(_DATA.read_text(encoding="utf-8"))
    return {pid: ReadingProtocol(pid, p["version"], p["label"], p["purpose"], p["dimension"], p["instructions"],
                                 tuple(p["fields"]), tuple(p.get("extra_dimensions", ())), bool(p.get("include_unlabeled", False)),
                                 bool(p.get("model_scoped", False)), bool(p.get("all_sections", False)), tuple(p.get("exclude_labels", ())), p.get("attribute_schema"),
                                 bool(p.get("extra_quotes", False)), bool(p.get("one_record_per_span", False))) for pid, p in raw["protocols"].items()}


def list_protocols() -> list[ReadingProtocol]:
    return [_load()[k] for k in sorted(_load())]


def get_protocol(protocol_id: str) -> ReadingProtocol:
    pid = protocol_id.split("@")[0]
    if pid not in _load():
        raise KnowledgeError("PROTOCOL_UNKNOWN", f"{protocol_id}: known protocols are {sorted(_load())}")
    return _load()[pid]


def config_digest(max_input_tokens: int, spans: Optional[list[tuple[int, int]]], context: Optional[str]) -> str:
    """What besides the text and the protocol shapes a run: how the text was cut, which spans were read, what else the model was told."""
    return canonical_json_hash({"max_input_tokens": max_input_tokens, "spans": [list(x) for x in spans] if spans else None, "context": context, "chunking": "paragraph-v1"})


def model_digest(model: dict[str, Any]) -> str:
    return canonical_json_hash({k: model.get(k) for k in ("provider", "model_id", "adapter_version", "script")})


def extraction_key(source_id: str, source_revision: int, text_digest: str, protocol: ReadingProtocol, *, config: Optional[str] = None, model: Optional[dict[str, Any]] = None) -> str:
    key = f"{source_id}@{source_revision}#{text_digest[:16]}|{protocol.pin}"
    if protocol.model_scoped:                       # the other protocols keep the key they always had
        if config is None or model is None:
            raise KnowledgeError("EXTRACTION_KEY_NEEDS_MODEL", f"{protocol.pin} is model-scoped: its cache key needs the model identity and the configuration")
        key += f"|cfg:{config[:16]}|model:{model_digest(model)[:16]}"
    return key


def _text_digest(obj: dict[str, Any], text: str) -> str:
    return obj["content"].get("text_sha256") or hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fingerprint(key: str, protocol: ReadingProtocol) -> str:
    return canonical_json_hash({"extraction_key": key, "protocol_digest": protocol.digest})


# ---------------------------------------------------------------------------------------------------
# prompts and chunking
# ---------------------------------------------------------------------------------------------------

def build_system_prompt(protocol: ReadingProtocol) -> str:
    fields = "\n".join(f"- `{f['name']}`: {f['description']}" + (" (may repeat)" if f.get("repeatable") else " (once)")
                       + (f" Allowed types: {', '.join(f['types'])}." if f.get("types") else "")
                       + (f" At most {f['max_records']} records, the most decision-relevant first." if f.get("max_records") else "") for f in protocol.fields)
    return (
        f"You are extracting facts from one academic paper for a research assistant ({protocol.id}: {protocol.label}).\n\n"
        f"Purpose: {protocol.purpose}\n\n## Instructions\n{protocol.instructions}\n\n## Fields\n{fields}\n\n"
        "## Output contract\nRespond with exactly one JSON object and nothing else. It must validate against this JSON Schema:\n"
        f"{json.dumps(protocol.output_schema(), indent=2, sort_keys=True)}\n\n"
        "Rules:\n- `quote` must be an exact span copied from the text you are shown, long enough to be unambiguous. "
        "Do not paraphrase inside `quote`. A record without a verbatim quote is discarded.\n"
        "- `label` (optional) names what the record is about, for example the construct a definition defines. `type` (optional) "
        "is one of the allowed types listed for a field, when it has any.\n"
        "- `value` is your concise statement of what the quote says for that field. Do not add anything the quote does not say.\n"
        "- If the text does not state something a field asks for, omit the field and name it in `gaps`. "
        "Do not fill it from general knowledge.\n")


def build_user_prompt(obj: dict[str, Any], section_note: str, text: str, part: int, parts: int, context: Optional[str] = None) -> str:
    c = obj["content"]
    head = f"Source: {c.get('citation_key', obj['object_id'])} ({obj['object_id']} revision {obj['revision']})"
    if c.get("title"):
        head += f"\nTitle: {c['title']}"
    if context:
        head += f"\n{context}"
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

def _locate(pieces: list[tuple[str, int]], quote: str) -> Optional[tuple[int, int]]:
    """Absolute `(start, end)` of the first piece of text shown that contains the quote."""
    for piece, start in pieces:
        found = quotes.find_quote(piece, quote)
        if found:
            return start + found[0], start + found[1]
    return None


def validate_extraction(protocol: ReadingProtocol, raw: Any, chunk_text: str, chunk_start: int, full_text: str, *, pieces: Optional[list[tuple[str, int]]] = None,
                        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], Optional[str]]:
    """`(accepted, rejected, gaps, error)`. `error` is set when the answer as a whole is unusable. `pieces` (text, start) are the separate stretches of text the model was shown in one
    call (spans read together); a quote must be in one of them."""
    pieces = pieces or [(chunk_text, chunk_start)]
    parsed, err = parse_model_output(raw)
    if parsed is None:
        return [], [], [], err
    problems = sorted(Draft202012Validator(protocol.validation_schema()).iter_errors(parsed), key=lambda e: list(map(str, e.absolute_path)))
    if problems:
        return [], [], [], "schema: " + "; ".join(
            f"{'.'.join(str(p) for p in e.absolute_path) or '$'}: {e.message[:120]}" for e in problems[:5])
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    spans_used: set[tuple] = set()
    counts: dict[str, int] = {}
    for rec in parsed["records"]:
        reject = lambda why: rejected.append({**rec, "reason": why})  # noqa: E731
        spec = protocol.field(rec["field"])
        quote = rec["quote"]
        if len(quotes.normalize_whitespace(quote)) < MIN_QUOTE_CHARS:
            reject("quote_too_short")
            continue
        found = _locate(pieces, quote)
        if not found:
            reject("quote_not_in_text_shown")
            continue
        start, end = found
        check = quotes.verify_span(full_text, start, end, quote)
        if not check.ok:
            reject("span_does_not_verify_against_stored_text")
            continue
        if (rec["field"], start, end) in seen:
            reject("duplicate_record")
            continue
        if protocol.one_record_per_span and (start, end) in spans_used:
            reject("span_already_used_by_another_field")
            continue
        if counts.get(rec["field"], 0) >= 1 and not (spec or {}).get("repeatable"):
            reject("field_not_repeatable")
            continue
        if rec.get("type") and (spec or {}).get("types") and rec["type"] not in spec["types"]:
            reject("type_not_allowed")
            continue
        seen.add((rec["field"], start, end))
        spans_used.add((start, end))
        counts[rec["field"]] = counts.get(rec["field"], 0) + 1
        item = {"field": rec["field"], "value": rec["value"].strip(), "quote": full_text[start:end],
                "char_start": start, "char_end": end}
        for extra in ("label", "type"):
            if rec.get(extra):
                item[extra] = rec[extra].strip()
        if protocol.attribute_schema and isinstance(rec.get("attributes"), dict) and rec["attributes"]:
            if Draft202012Validator(protocol.attribute_schema).is_valid(rec["attributes"]):
                item["attributes"] = rec["attributes"]
            else:                                       # the record is kept; only what it said about itself is dropped, and the run says so
                rejected.append({"field": rec["field"], "quote": item["quote"], "value": "(attributes of: " + rec["value"][:80] + ")", "reason": "attributes_invalid_dropped", "record_kept": True})
        if protocol.extra_quotes and isinstance(rec.get("extra_quotes"), list) and rec["extra_quotes"]:
            extras = []
            for eq in (e for e in rec["extra_quotes"] if isinstance(e, dict) and isinstance(e.get("quote"), str)):
                eq_found = _locate(pieces, eq["quote"]) if len(quotes.normalize_whitespace(eq["quote"])) >= MIN_QUOTE_CHARS else None
                if not eq_found:
                    rejected.append({"field": rec["field"], "quote": eq["quote"], "value": "(extra quote of: " + rec["value"][:80] + ")", "reason": "extra_quote_not_in_text_shown"})
                    continue
                e_start, e_end = eq_found
                if not quotes.verify_span(full_text, e_start, e_end, eq["quote"]).ok:
                    rejected.append({"field": rec["field"], "quote": eq["quote"], "value": "(extra quote of: " + rec["value"][:80] + ")", "reason": "extra_quote_span_does_not_verify"})
                    continue
                extras.append({"quote": full_text[e_start:e_end], "char_start": e_start, "char_end": e_end, "role": str(eq.get("role") or "").strip() or None,
                               "why": str(eq.get("why") or "").strip() or None})
            if extras:
                item["extra_quotes"] = extras
        accepted.append(item)
    return accepted, rejected, [str(g) for g in parsed.get("gaps", [])], None


# ---------------------------------------------------------------------------------------------------
# running a protocol
# ---------------------------------------------------------------------------------------------------

def _run_row(*, fingerprint: str, execution: ExecutionStatus, context: ContextStatus, assessment: AssessmentStatus,
             legacy_status: str, reason: Optional[str], usage: dict, model: Optional[dict], artifact: Optional[str], source_id: str,
             source_revision: int, text_digest: str, reused_from: Optional[str] = None, protocol: ReadingProtocol) -> dict[str, Any]:
    return {
        "workflow": f"reading_protocol:{protocol.id}", "input_fingerprint": fingerprint, "executor": EXECUTOR,
        "execution_status": execution.value, "context_status": context.value, "assessment_status": assessment.value,
        "applicability": Applicability.APPLICABLE.value, "legacy_status": legacy_status, "status_reason": reason,
        "usage": usage, "model_descriptor": model, "artifact_sha256": artifact, "reused_from_run_id": reused_from,
        "input_revisions": [{"object_id": source_id, "revision": source_revision, "content_digest": text_digest}]}


def _register(store: StateStore, **kw: Any) -> str:
    """A run that writes no objects (failed, reused) is registered on its own."""
    return workflow_runs.append_run(store, _run_row(**kw))


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
    if rec.get("label"):
        c["item_label"] = rec["label"]
    if rec.get("type"):
        c["item_type"] = rec["type"]
    if rec.get("attributes"):
        c["attributes"] = rec["attributes"]
    return c


def _write_passages(tx: Any, store: StateStore, protocol: ReadingProtocol, obj: dict[str, Any], records: list[dict[str, Any]],
                    key: str, model: dict[str, Any], sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stored: list[dict[str, Any]] = []
    frag = id_fragment(obj["object_id"], 28)
    proto = id_fragment(protocol.id.replace("RP_", ""), 12)
    def put(pid: str, content: dict[str, Any]) -> int:
        head = tx.head(pid)
        if head:
            cur = tx.get(pid)
            if cur["content"] == content:
                return cur["revision"]
        res = tx.add_revision(object_id=pid, object_type="EvidencePassage", content=content,
                              edges=[("derived_from", obj["object_id"], obj["revision"], {"origin": "reading_protocol"})],
                              origin=engine_origin("extract", tx.actor_id, protocol=protocol.pin, extraction_key=key))
        return res["revision"]

    def section_of(char_start: int) -> str:
        section = next((s for s in sections if s["char_start"] <= char_start < s["char_end"]), None)
        return (section or {}).get("heading") or (section or {}).get("label") or ""

    for rec in records:
        content = _passage_content(protocol, obj, rec, key, model, section_of(rec["char_start"]))
        span = f"{rec['char_start']}_{rec['char_end']}" if protocol.one_record_per_span else f"{rec['char_start']}"
        pid = scope_id(store, f"EP_{frag}_{proto}_{span}")
        out = {**rec, "passage_id": pid, "revision": put(pid, content)}
        if rec.get("extra_quotes"):                    # further verified spans of the record, each its own passage, named as such
            made = []
            for eq in rec["extra_quotes"]:
                xid = scope_id(store, f"EP_{frag}_{proto}X_{eq['char_start']}_{eq['char_end']}")
                xc = {"label": f"{protocol.id} {rec['field']} {eq.get('role') or 'extra'} span in {obj['content'].get('citation_key', obj['object_id'])}",
                      "locator": f"{obj['object_id']} chars {eq['char_start']}-{eq['char_end']}", "quote": eq["quote"], "normalized_claim": eq["quote"][:5000],
                      "source_id": obj["object_id"], "source_revision": obj["revision"], "char_start": eq["char_start"], "char_end": eq["char_end"], "quote_verified": True,
                      "protocol": protocol.pin, "field": rec["field"], "generated_by": content["generated_by"], "extraction_key": key, "model": model,
                      "item_type": "extra_quote", "attributes": {"extra_of": pid, "role": eq.get("role"), "why": eq.get("why")}}
                if section_of(eq["char_start"]):
                    xc["section"] = section_of(eq["char_start"])
                made.append({**eq, "passage_id": xid, "revision": put(xid, xc)})
            out["extra_quotes"] = made
        stored.append(out)
    tx.audit("extract", "RECORDED", {"protocol": protocol.pin, "source": obj["object_id"], "records": len(stored)})
    return stored


def _commit_extraction(store: StateStore, token: Optional[str], protocol: ReadingProtocol, obj: dict[str, Any],
                       records: list[dict[str, Any]], key: str, model: dict[str, Any], sections: list[dict[str, Any]],
                       finish: Any, failed_row: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    """Passages, the result artefact and the SUCCEEDED run row in one transaction (I22). `finish(stored)` returns the result and the
    run row without its artefact. If the write is refused (authorization, a stale revision, a crash before commit), nothing of it
    remains; the model calls already made are then registered as a FAILED run and the error is raised."""
    try:
        with store.write_tx(token=token, require_roles=WRITE_ROLES) as tx:
            stored = _write_passages(tx, store, protocol, obj, records, key, model, sections)
            result, row = finish(stored)
            artifact = store.artifacts.put_bytes(json.dumps(result, sort_keys=True, default=str).encode("utf-8"))
            tx.fault("extraction_before_run")
            run_id = workflow_runs.insert_run_tx(tx, {**row, "artifact_sha256": artifact})
    except Exception as exc:
        try:
            workflow_runs.append_run(store, {**failed_row, "status_reason": f"write_refused:{getattr(exc, 'code', type(exc).__name__)}"})
        except Exception:  # noqa: BLE001 - the original error is the one to report
            pass
        raise
    return stored, result, run_id


def run_protocol(store: StateStore, token: Optional[str], source_id: str, protocol_id: str, adapter: Any, *,
                 source_revision: Optional[int] = None, max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS,
                 reuse: bool = True, spans: Optional[list[tuple[int, int]]] = None, context: Optional[str] = None) -> dict[str, Any]:
    """Extract `protocol_id` from the stored text of `source_id`; see the module docstring for what is kept.

    `spans` (character ranges) replace the protocol's sections as the text to read, and `context` is a line added to what the model is told (a selective
    follow-up: "link this claim to its evidence"). Both belong to the configuration of a model-scoped protocol's cache key."""
    protocol = get_protocol(protocol_id)
    obj, text = source_text(store, source_id, source_revision)
    text_digest = _text_digest(obj, text)
    model = adapter_identity(adapter)
    cfg = config_digest(max_input_tokens, spans, context)
    key = extraction_key(source_id, obj["revision"], text_digest, protocol, config=cfg, model=model)
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
            cached_model = row.get("model_descriptor") or {}
            matches = protocol.model_scoped or model_digest(cached_model) == model_digest(model)
            return {**cached, "reused": True, "run_id": run_id, "reused_from_run_id": row["run_id"],
                    "reuse": {"model_matches": matches, "cached_model": cached_model.get("model_id"), "requested_model": model.get("model_id")}}
        imported = _cached_from_passages(store, key, protocol, obj)
        if imported is not None:  # the extraction came with the source (a library import): no model call, same result
            run_id = _register(store, fingerprint=fingerprint, execution=ExecutionStatus.SUCCEEDED, context=ContextStatus.COMPLETE,
                               assessment=AssessmentStatus.PROVISIONAL, legacy_status="SUCCESS", reason="reused_imported_passages",
                               usage={"llm_calls": 0, "usage_source": "not_available"}, model=imported.get("model"), artifact=None,
                               source_id=source_id, source_revision=obj["revision"], text_digest=text_digest, protocol=protocol)
            return {**imported, "reused": True, "run_id": run_id, "reused_from": "imported_passages",
                    "reuse": {"model_matches": True if protocol.model_scoped else None, "cached_model": (imported.get("model") or {}).get("model_id"), "requested_model": model.get("model_id")}}

    sections_all = source_sections(store, obj, text)
    typed = [Section(**s) for s in sections_all]
    picked: dict[int, Section] = {}
    if spans is not None:
        pass                                  # the caller named the text to read
    elif protocol.all_sections:
        for sec in typed:
            if sec.label not in protocol.exclude_labels:
                picked[sec.char_start] = sec
    else:
        for dim in protocol.dimensions:
            for sec in sections_for_dimension(typed, dim):
                picked[sec.char_start] = sec
        if protocol.include_unlabeled:   # a heading that matched no label may still hold what the protocol asks for: it is read, and the run says so
            for sec in typed:
                if sec.label == "other" and sec.heading:
                    picked[sec.char_start] = sec
    chosen = [picked[k] for k in sorted(picked)]
    fallback = not chosen and spans is None
    read_spans = list(spans) if spans is not None else ([(s.char_start, s.char_end) for s in chosen] or [(0, len(text))])
    if spans is not None:
        section_note = f"{len(read_spans)} selected span(s) of the text"
    else:
        section_note = ", ".join(s.heading or s.label for s in chosen) if chosen else "whole text (no section matched)"
    system = build_system_prompt(protocol)
    budget = max_input_tokens - estimate_tokens(system)[0] - 400
    if budget < MIN_TEXT_BUDGET:
        raise KnowledgeError("BUDGET_TOO_SMALL", f"max_input_tokens={max_input_tokens} leaves no room for text")
    ranges = chunk_ranges(read_spans, text, budget)
    groups: list[list[tuple[int, int]]] = [[r] for r in ranges]
    if spans is not None:                    # separate spans named by the caller are read together in one call as far as the budget allows, not one call each
        groups, current = [], []
        for r in ranges:
            if current and estimate_tokens("\n\n[...]\n\n".join(text[lo:hi] for lo, hi in [*current, r]))[0] > budget:
                groups.append(current)
                current = []
            current.append(r)
        if current:
            groups.append(current)

    accepted_all: list[dict[str, Any]] = []
    rejected_all: list[dict[str, Any]] = []
    gaps: list[str] = []
    usage = {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "usage_source": "scripted_or_provider"}
    failure: Optional[tuple[str, str]] = None
    for i, group in enumerate(groups, 1):
        shown = "\n\n[...]\n\n".join(text[lo:hi] for lo, hi in group)
        prompt = build_user_prompt(obj, section_note, shown, i, len(groups), context)
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
        accepted, rejected, part_gaps, error = validate_extraction(protocol, raw, shown, group[0][0], text, pieces=[(text[lo:hi], lo) for lo, hi in group])
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

    # one record per (field, span) across chunks, and no more per field than the protocol allows (the rest is listed, not silently dropped)
    uniq: dict[tuple, dict[str, Any]] = {}
    kept_per_field: dict[str, int] = {}
    for rec in accepted_all:
        key_ = (rec["field"], rec["char_start"], rec["char_end"])
        if key_ in uniq:
            continue
        cap = (protocol.field(rec["field"]) or {}).get("max_records")
        if cap and kept_per_field.get(rec["field"], 0) >= cap:
            rejected_all.append({**rec, "reason": "over_cap", "cap": cap})
            continue
        kept_per_field[rec["field"]] = kept_per_field.get(rec["field"], 0) + 1
        uniq[key_] = rec
    saturated = sorted(f for f, n in kept_per_field.items() if (protocol.field(f) or {}).get("max_records") == n)   # a field at its cap may have been cut short by the instruction
    chars_read = sum(hi - lo for lo, hi in ranges)
    not_read = [] if (spans is not None or fallback) else [s.heading or s.label for s in typed if s not in chosen]
    context_status = ContextStatus.PARTIAL if fallback else ContextStatus.COMPLETE
    common = dict(fingerprint=fingerprint, usage=usage, model=model, source_id=source_id, source_revision=obj["revision"],
                  text_digest=text_digest, protocol=protocol)

    def finish(stored: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
        assessment = apply_caps(AssessmentStatus.PROVISIONAL if stored else AssessmentStatus.NOT_ASSESSED, context=context_status,
                                generated_by=f"model:{model.get('model_id')}")
        result = {"protocol": protocol.pin, "protocol_digest": protocol.digest, "source_id": source_id,
                  "source_revision": obj["revision"], "extraction_key": key, "status": "EXTRACTED" if stored else "NO_RECORDS",
                  "records": stored, "rejected": rejected_all, "gaps": sorted(set(gaps)), "chunks": len(groups),
                  "sections_read": [s.heading or s.label for s in chosen], "section_fallback": fallback,
                  "sections_not_read": not_read, "chars_read": chars_read, "chars_total": len(text), "ranges_read": [[lo, hi] for lo, hi in ranges], "cap_saturated": saturated,
                  "context_status": context_status.value, "assessment": assessment.value, "model": model}
        return result, _run_row(execution=ExecutionStatus.SUCCEEDED, context=context_status, assessment=assessment, legacy_status="SUCCESS",
                                reason=None, artifact=None, **common)

    failed = _run_row(execution=ExecutionStatus.FAILED, context=ContextStatus.PARTIAL, assessment=AssessmentStatus.NOT_ASSESSED,
                      legacy_status="FAILED", reason=None, artifact=None, **common)
    _, result, run_id = _commit_extraction(store, token, protocol, obj, list(uniq.values()), key, model, sections_all, finish, failed)
    return {**result, "reused": False, "run_id": run_id}


def _cached_from_passages(store: StateStore, key: str, protocol: ReadingProtocol, obj: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Rebuild an extraction from EvidencePassages that carry this extraction key (they arrive with an imported Source)."""
    with store.read() as conn:
        rows = conn.execute(
            "SELECT o.object_id, o.revision FROM objects o JOIN object_heads h ON h.object_id=o.object_id AND h.head_revision=o.revision "
            "WHERE o.object_type='EvidencePassage' AND json_extract(o.content,'$.extraction_key')=? ORDER BY o.object_id", (key,)).fetchall()
        passages = [store.get_object(r[0], r[1], conn=conn) for r in rows]
    if not passages:
        return None
    records = []
    extras: dict[str, list[dict[str, Any]]] = {}
    for p in passages:
        c = p["content"]
        if c.get("item_type") == "extra_quote":
            extras.setdefault((c.get("attributes") or {}).get("extra_of"), []).append(
                {"quote": c["quote"], "char_start": c["char_start"], "char_end": c["char_end"], "role": (c.get("attributes") or {}).get("role"),
                 "why": (c.get("attributes") or {}).get("why"), "passage_id": p["object_id"], "revision": p["revision"]})
    for p in passages:
        c = p["content"]
        if c.get("item_type") == "extra_quote":
            continue
        rec = {"passage_id": p["object_id"], "revision": p["revision"], "field": c["field"], "value": c["value"], "quote": c["quote"],
               "char_start": c["char_start"], "char_end": c["char_end"]}
        if c.get("item_label"):
            rec["label"] = c["item_label"]
        if c.get("item_type"):
            rec["type"] = c["item_type"]
        if c.get("attributes"):
            rec["attributes"] = c["attributes"]
        if extras.get(p["object_id"]):
            rec["extra_quotes"] = sorted(extras[p["object_id"]], key=lambda e: e["char_start"])
        records.append(rec)
    records.sort(key=lambda r: (r["char_start"], r["field"]))
    return {"protocol": protocol.pin, "protocol_digest": protocol.digest, "source_id": obj["object_id"], "source_revision": obj["revision"],
            "extraction_key": key, "status": "EXTRACTED", "records": records, "rejected": [], "gaps": [], "chunks": 0,
            "sections_read": [], "section_fallback": False, "context_status": "COMPLETE", "assessment": "PROVISIONAL",
            "model": passages[0]["content"].get("model")}


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


def extraction_status(store: StateStore, source_id: str, protocol_id: str, *, adapter: Any = None, max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS,
                      spans: Optional[list[tuple[int, int]]] = None, context: Optional[str] = None, source_revision: Optional[int] = None) -> dict[str, Any]:
    """Is there a verified extraction of this protocol for the current revision of the Source? A model-scoped protocol also needs the adapter and the
    configuration the run would use: an extraction by another model, or with other settings, is not this one."""
    protocol = get_protocol(protocol_id)
    obj, text = source_text(store, source_id, source_revision)
    if protocol.model_scoped and adapter is None:
        raise KnowledgeError("EXTRACTION_KEY_NEEDS_MODEL", f"{protocol.pin} is model-scoped: say which adapter the status is asked for")
    key = extraction_key(source_id, obj["revision"], _text_digest(obj, text), protocol,
                         config=config_digest(max_input_tokens, spans, context), model=adapter_identity(adapter) if adapter is not None else None)
    with store.read() as conn:
        row = workflow_runs.latest_reusable(conn, f"reading_protocol:{protocol.id}", _fingerprint(key, protocol), ["SUCCESS"])
    return {"protocol": protocol.pin, "extraction_key": key, "cached": _load_cached(store, row) is not None,
            "run_id": row["run_id"] if row else None}


def parse_caps(items: Optional[list[str]]) -> dict[str, int]:
    caps: dict[str, int] = {}
    for it in items or []:
        name, _, n = it.partition("=")
        if not name or not n.isdigit() or int(n) < 1:
            raise KnowledgeError("CAP_INVALID", f"{it!r}: use field=N with N >= 1")
        caps[name] = int(n)
    return caps


def reconsider(store: StateStore, token: Optional[str], source_id: str, protocol_id: str, *, caps: Optional[dict[str, int]] = None) -> dict[str, Any]:
    """Re-derive an extraction from the model answer already paid for, under the rules as they are now: no model call.

    The records the last successful run kept, the ones it rejected because their quote was not found, and the ones it set aside for a field's
    cap are checked again against the whole stored text (the quote matcher may have changed) and capped again (`caps` overrides the
    protocol's caps for named fields). Records that now verify become passages; the result is registered as a new run
    (`reason: reconsidered`, zero model calls) and is what later reuse finds. Nothing is invented: every record is one the model produced.
    """
    protocol = get_protocol(protocol_id)
    if protocol.model_scoped:
        raise KnowledgeError("RECONSIDER_NOT_FOR_MODEL_SCOPED", f"{protocol.pin} is model-scoped: re-run it (the result is cached per model and configuration)")
    obj, text = source_text(store, source_id)
    text_digest = _text_digest(obj, text)
    key = extraction_key(source_id, obj["revision"], text_digest, protocol)
    fingerprint = _fingerprint(key, protocol)
    with store.read() as conn:
        row = workflow_runs.latest_reusable(conn, f"reading_protocol:{protocol.id}", fingerprint, ["SUCCESS"])
    if row is None or not row.get("artifact_sha256") or not store.artifacts.has(row["artifact_sha256"]):
        raise KnowledgeError("NOTHING_TO_RECONSIDER", f"no successful {protocol.pin} extraction of {source_id} is on record")
    prev = json.loads(store.artifacts.get_bytes(row["artifact_sha256"]).decode("utf-8"))
    pool: list[dict[str, Any]] = []
    for r in prev["records"]:
        pool.append({k: r[k] for k in ("field", "value", "quote", "char_start", "char_end", "label", "type") if k in r})
    for r in prev["rejected"]:
        if r.get("reason") == "over_cap":
            pool.append({k: r[k] for k in ("field", "value", "quote", "char_start", "char_end", "label", "type") if k in r})
        elif r.get("reason") == "quote_not_in_text_shown":
            found = quotes.find_quote(text, r["quote"]) if len(quotes.normalize_whitespace(r["quote"])) >= MIN_QUOTE_CHARS else None
            if found:
                pool.append({"field": r["field"], "value": r["value"], "quote": text[found[0]:found[1]], "char_start": found[0], "char_end": found[1],
                             **{k: r[k] for k in ("label", "type") if k in r}})
    effective = {f["name"]: (caps or {}).get(f["name"], f.get("max_records")) for f in protocol.fields}
    kept, rejected, seen, counts = [], [r for r in prev["rejected"] if r.get("reason") not in ("over_cap", "quote_not_in_text_shown")], set(), {}
    for rec in pool:
        k = (rec["field"], rec["char_start"], rec["char_end"])
        if k in seen:
            continue
        seen.add(k)
        spec = protocol.field(rec["field"]) or {}
        if rec.get("type") and spec.get("types") and rec["type"] not in spec["types"]:
            rejected.append({**rec, "reason": "type_not_allowed"})
        elif effective.get(rec["field"]) and counts.get(rec["field"], 0) >= effective[rec["field"]]:
            rejected.append({**rec, "reason": "over_cap", "cap": effective[rec["field"]]})
        else:
            counts[rec["field"]] = counts.get(rec["field"], 0) + 1
            kept.append(rec)
    model = prev.get("model") or {}
    sections_all = source_sections(store, obj, text)
    still = [r for r in prev["rejected"] if r.get("reason") == "quote_not_in_text_shown" and not quotes.find_quote(text, r["quote"])]
    common = dict(fingerprint=fingerprint, usage={"llm_calls": 0, "usage_source": "not_available"}, model=model, source_id=source_id,
                  source_revision=obj["revision"], text_digest=text_digest, protocol=protocol)

    def finish(stored: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
        result = {**{k: v for k, v in prev.items() if k not in ("records", "rejected")}, "records": stored, "rejected": rejected + still,
                  "status": "EXTRACTED" if stored else "NO_RECORDS", "reconsidered_from_run_id": row["run_id"],
                  "caps": {k: v for k, v in effective.items() if v},
                  "gained_by_matcher": sum(1 for r in prev["rejected"] if r.get("reason") == "quote_not_in_text_shown") - len(still)}
        return result, _run_row(execution=ExecutionStatus.SUCCEEDED, context=ContextStatus(prev.get("context_status", "COMPLETE")),
                                assessment=AssessmentStatus.PROVISIONAL if stored else AssessmentStatus.NOT_ASSESSED, legacy_status="SUCCESS",
                                reason="reconsidered", artifact=None, **common)

    failed = _run_row(execution=ExecutionStatus.FAILED, context=ContextStatus.PARTIAL, assessment=AssessmentStatus.NOT_ASSESSED,
                      legacy_status="FAILED", reason=None, artifact=None, **common)
    stored, result, run_id = _commit_extraction(store, token, protocol, obj, kept, key, model, sections_all, finish, failed)
    return {**result, "reused": False, "run_id": run_id, "previous_records": len(prev["records"])}
