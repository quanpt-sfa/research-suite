"""Versioned JSON schemas (single source of truth; exported to ``schemas/`` by ``research schema export``).

No schema field is ever interpreted as a path, command, SQL or expression: references are
identifiers matching ``ID_PATTERN``, opaque references (``data_ref``) forbid ``/``, ``\\`` and ``..``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from research_harness import PROTOCOL_VERSION, SCHEMA_VERSION
from research_harness.core.model import (CLAIM_TIERS, FORMATION_BASES, ID_PATTERN, OBJECT_TYPES,
                                         PROJECT_ID_PATTERN, SERVER_ONLY_TYPES, SHA256_PATTERN)
from research_harness.graph.relations import RELATIONS

ALLOWED_OPERATIONS = ("propose_object", "propose_claim", "revise_object")
PROPOSABLE_TYPES = tuple(t for t in OBJECT_TYPES if t not in SERVER_ONLY_TYPES)

OPAQUE_REF = r"^[A-Za-z0-9._:\-]{1,200}$"          # no slashes, no spaces => cannot express a path
CITATION_KEY = r"^[A-Za-z0-9_:\-\.]{1,100}$"
DOI = r"^10\.[0-9]{4,9}/[^\s]{1,180}$"


def _obj(props: dict, required: list | None = None) -> dict:
    return {"type": "object", "additionalProperties": False, "properties": props, "required": required or []}


def _str(maximum: int = 300, minimum: int = 1, **kw) -> dict:
    return {"type": "string", "minLength": minimum, "maxLength": maximum, **kw}


ID = {"type": "string", "pattern": ID_PATTERN}
SHA = {"type": "string", "pattern": SHA256_PATTERN}
_COMMON = {
    "label": _str(300),
    "description": _str(20000, 0),
    "sensitivity": {"enum": ["public", "internal", "restricted"]},
    "attributes": {"type": "object", "maxProperties": 50},
}
RELATION_ITEM = _obj({"relation": {"enum": list(RELATIONS)}, "target_id": ID}, ["relation", "target_id"])
RELATIONS_ARRAY = {"type": "array", "maxItems": 100, "items": RELATION_ITEM}
SUBJECT = _obj({"object_id": ID, "revision": {"type": "integer", "minimum": 1}}, ["object_id", "revision"])

_TYPE_PROPS: dict[str, tuple[dict, list]] = {
    "ResearchQuestion": ({"text": _str(20000)}, ["text"]),
    "Theory": ({"text": _str(20000, 0)}, ["label"]),
    "Construct": ({"definition": _str(20000, 0)}, ["label"]),
    "Mechanism": ({"text": _str(20000, 0)}, ["label"]),
    "Proxy": ({"definition": _str(20000, 0), "unit": _str(100)}, ["label"]),
    "Dataset": ({"data_ref": {"type": "string", "pattern": OPAQUE_REF}}, ["label"]),
    "Source": ({"citation_key": {"type": "string", "pattern": CITATION_KEY},
                "bibliographic_identity": _str(1000), "doi": {"type": "string", "pattern": DOI},
                "library_file_id": {"type": "string", "pattern": OPAQUE_REF}}, ["citation_key"]),
    "EvidencePassage": ({"locator": _str(500), "normalized_claim": _str(5000),
                         "passage_fingerprint_policy_version": _str(20),
                         "passage_norm_sha256": SHA, "passage_head_sha256": SHA, "passage_tail_sha256": SHA,
                         "passage_token_count": {"type": "integer", "minimum": 1}}, ["locator"]),
    "Analysis": ({"method_family": _str(200)}, ["label"]),
    "Result": ({"summary": _str(20000)}, ["label"]),
    "Claim": ({"text": _str(20000), "claim_tier": {"enum": list(CLAIM_TIERS) + ["unclassified"]}},
              ["text", "claim_tier"]),
    "ManuscriptArtifact": ({"section": _str(300), "artifact_sha256": SHA}, ["label"]),
    "Adjudication": ({"subject": SUBJECT,
                      "assessment_kind": {"enum": ["source_integrity", "semantic_entailment", "scientific_judgment"]},
                      "verdict": {"enum": ["SUPPORTED", "NOT_SUPPORTED", "UNCLEAR"]},
                      "rationale": _str(20000)}, ["subject", "assessment_kind", "verdict", "rationale"]),
    # Approval content is written only by the engine (review.py); schema documents the stored shape.
    "Approval": ({"subject": SUBJECT, "kind": {"enum": ["author_approval", "independent_review"]},
                  "decision": {"enum": ["approve", "reject"]}, "rationale": _str(20000, 0),
                  "actor_id": _str(64), "actor_roles": {"type": "array", "items": _str(64)},
                  "independence_group": {"type": ["string", "null"]},
                  "acknowledged_invalidations": {"type": "array", "items": {"type": "integer"}},
                  "subject_revision_digest": SHA, "state_sha256_at_review": SHA}, ["subject", "kind", "decision"]),
}


def content_schema(object_type: str) -> dict:
    props, required = _TYPE_PROPS[object_type]
    s = _obj({**_COMMON, **props}, required)
    s["$id"] = f"urn:research-harness:object:{object_type}:{SCHEMA_VERSION}"
    s["title"] = f"{object_type} content (schema {SCHEMA_VERSION})"
    return s


def _claim_op_fields() -> dict:
    return {
        "depends_on": {"type": "array", "items": ID, "maxItems": 100, "uniqueItems": True},
        "observed_results_at_formation": {"type": "array", "items": ID, "maxItems": 200, "uniqueItems": True},
        "formation_basis": {"enum": list(FORMATION_BASES)},
        "suggested_status": {"enum": ["exploratory", "confirmatory", "proposed"]},
    }


OP_SCHEMAS: dict[str, dict] = {
    "propose_object": _obj({
        "operation": {"const": "propose_object"}, "proposed_id": ID,
        "object_type": {"enum": list(PROPOSABLE_TYPES)},
        "content": {"type": "object"}, "relations": RELATIONS_ARRAY,
    }, ["operation", "proposed_id", "object_type", "content"]),
    "propose_claim": _obj({
        "operation": {"const": "propose_claim"}, "proposed_id": ID,
        "claim_tier": {"enum": list(CLAIM_TIERS)}, "text": _str(20000),
        "label": _str(300), "description": _str(20000, 0), "sensitivity": _COMMON["sensitivity"],
        "attributes": _COMMON["attributes"], "relations": RELATIONS_ARRAY, **_claim_op_fields(),
    }, ["operation", "proposed_id", "claim_tier", "text", "depends_on"]),
    "revise_object": _obj({
        "operation": {"const": "revise_object"}, "object_id": ID,
        "base_revision": {"type": "integer", "minimum": 1},
        "content": {"type": "object"}, "relations": RELATIONS_ARRAY, **_claim_op_fields(),
    }, ["operation", "object_id", "base_revision", "content"]),
}

PROPOSAL_SCHEMA = _obj({
    "protocol_version": {"type": "string", "pattern": r"^[0-9]+\.[0-9]+\.[0-9]+$"},
    "proposal_id": ID,
    "project_id": {"type": "string", "pattern": PROJECT_ID_PATTERN},
    "base": _obj({"snapshot_id": {"type": "string", "pattern": r"^SNAP_[0-9]{5,12}$"},
                  "state_revision": {"type": "integer", "minimum": 0}, "state_sha256": SHA},
                 ["snapshot_id", "state_revision", "state_sha256"]),
    "read_set": {"type": "object", "propertyNames": {"pattern": ID_PATTERN}, "maxProperties": 500,
                 "additionalProperties": {"type": "integer", "minimum": 1}},
    "origin_channel": {"enum": ["chatgpt_web", "codex", "human_cli", "other"]},
    "operations": {"type": "array", "minItems": 1, "maxItems": 100,
                   "items": {"type": "object", "required": ["operation"],
                             "properties": {"operation": {"type": "string", "maxLength": 64}}}},
    "note": _str(2000, 0),
}, ["protocol_version", "proposal_id", "project_id", "base", "read_set", "origin_channel", "operations"])
PROPOSAL_SCHEMA["$id"] = f"urn:research-harness:proposal:{PROTOCOL_VERSION}"
PROPOSAL_SCHEMA["title"] = f"Proposal (protocol {PROTOCOL_VERSION}); operations validated per OP_SCHEMAS"


def schema_findings(schema: dict, instance: Any, limit: int = 20, prefix: str = "") -> list[dict]:
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda e: [str(p) for p in e.absolute_path])[:limit]
    return [{"path": prefix + "/" + "/".join(str(p) for p in e.absolute_path), "message": e.message[:300]}
            for e in errors]


def validate_proposal_structure(doc: Any) -> list[dict]:
    """Top-level + per-operation structural validation (operation names already allow-listed)."""
    findings = schema_findings(PROPOSAL_SCHEMA, doc)
    if findings or not isinstance(doc, dict):
        return findings
    for i, op in enumerate(doc["operations"]):
        findings += schema_findings(OP_SCHEMAS[op["operation"]], op, prefix=f"/operations/{i}")
        if op["operation"] == "propose_object" and not findings:
            findings += schema_findings(content_schema(op["object_type"]), op["content"],
                                        prefix=f"/operations/{i}/content")
    return findings


def snapshot_schema() -> dict:
    return {"$id": f"urn:research-harness:snapshot:{PROTOCOL_VERSION}", "type": "object",
            "title": "Snapshot exported by D13 (read-only context for an agent; HMAC-protected)",
            "required": ["protocol_version", "snapshot_id", "project_id", "scope", "state_revision",
                         "state_sha256", "objects", "permitted_operations", "integrity"]}


def receipt_schema() -> dict:
    return {"$id": f"urn:research-harness:receipt:{PROTOCOL_VERSION}", "type": "object",
            "required": ["proposal_id", "decision", "timestamp"],
            "properties": {"decision": {"enum": ["ACCEPTED", "REJECTED", "CONFLICT", "DUPLICATE",
                                                  "INVALID_SCHEMA", "UNAUTHORIZED"]}}}


def all_schemas() -> dict[str, dict]:
    out = {f"proposal.v{PROTOCOL_VERSION}.schema.json": PROPOSAL_SCHEMA,
           f"snapshot.v{PROTOCOL_VERSION}.schema.json": snapshot_schema(),
           f"receipt.v{PROTOCOL_VERSION}.schema.json": receipt_schema()}
    for name, s in OP_SCHEMAS.items():
        out[f"operation_{name}.v{PROTOCOL_VERSION}.schema.json"] = s
    for t in OBJECT_TYPES:
        out[f"object_{t}.v{SCHEMA_VERSION}.schema.json"] = content_schema(t)
    return out


def export_schemas(directory: Path) -> list[str]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    names = []
    for name, schema in all_schemas().items():
        (directory / name).write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        names.append(name)
    return names
