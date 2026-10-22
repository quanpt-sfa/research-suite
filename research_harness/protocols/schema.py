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
from research_harness import taxonomy
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
                "library_file_id": {"type": "string", "pattern": OPAQUE_REF},
                # 9.2 corpus fields: the text is held in the content-addressed store, never inline
                "title": _str(1000), "authors": {"type": "array", "items": _str(200), "maxItems": 100},
                "year": {"type": "integer", "minimum": 1000, "maximum": 3000}, "venue": _str(500),
                "abstract": _str(20000, 0), "artifact_sha256": SHA, "text_sha256": SHA, "sections_sha256": SHA,
                "fulltext_status": {"enum": ["AVAILABLE", "ABSTRACT_ONLY", "MISSING"]},
                "text_extraction": {"type": "object"}, "license_note": _str(1000)}, ["citation_key"]),
    "EvidencePassage": ({"locator": _str(500), "normalized_claim": _str(5000),
                         # 9.2 verified extraction: a passage is a span of a stored text, never a paraphrase alone
                         "quote": _str(20000), "source_id": ID, "source_revision": {"type": "integer", "minimum": 1},
                         "char_start": {"type": "integer", "minimum": 0}, "char_end": {"type": "integer", "minimum": 0},
                         "section": _str(300), "protocol": _str(100), "field": _str(100), "value": _str(5000),
                         "quote_verified": {"type": "boolean"}, "generated_by": _str(120), "extraction_key": _str(300),
                         "item_label": _str(300), "item_type": _str(100),
                         "model": {"type": "object"},
                         "passage_fingerprint_policy_version": _str(20),
                         "passage_norm_sha256": SHA, "passage_head_sha256": SHA, "passage_tail_sha256": SHA,
                         "passage_token_count": {"type": "integer", "minimum": 1}}, ["locator"]),
    "Analysis": ({"method_family": _str(200)}, ["label"]),
    "Result": ({"summary": _str(20000)}, ["label"]),
    "Claim": ({"text": _str(20000), "claim_tier": {"enum": list(CLAIM_TIERS) + ["unclassified"]}},
              ["text", "claim_tier"]),
    "ManuscriptArtifact": ({"section": _str(300), "artifact_sha256": SHA}, ["label"]),
    "ResearchTask": ({"goal": _str(100), "goal_version": _str(20), "params": {"type": "object"}, "statement": {"type": "object"},
                      "parent_task": {"type": ["object", "null"]}, "continued_from": {"type": ["object", "null"]},
                      "domain_workflow": {"type": ["string", "null"]}, "plan": {"type": "object"}, "budget": {"type": "object"},
                      "state": {"enum": ["PLANNED", "APPROVED", "RUNNING", "WAITING_FOR_RESEARCHER", "BLOCKED", "FAILED", "COMPLETED", "CANCELLED"]},
                      "state_reason": {"type": ["string", "null"]}, "checkpoints": {"type": "array", "items": {"type": "object"}},
                      "lease": {"type": ["object", "null"]}, "generated_by": _str(120)},
                     ["goal", "plan", "state"]),
    "ResearchProblem": ({"kind": {"enum": ["phenomenon", "puzzle", "disagreement", "unexpected_result", "practical_problem", "method_gap"]},
                         "statement": _str(20000), "scope": {"type": "object"}, "exclusions": {"type": "array", "items": _str(500), "maxItems": 50},
                         "interpretations": {"type": "array", "items": {"type": "object"}, "maxItems": 100},
                         "chosen_interpretations": {"type": "array", "items": _str(40), "maxItems": 100},
                         "status": {"enum": ["OPEN", "FRAMED", "PROMOTED_TO_RQ", "SET_ASIDE"]},
                         "promoted_to": {"type": "array", "items": ID, "maxItems": 50}, "decisions": {"type": "array", "items": {"type": "object"}},
                         "generated_by": _str(120)},
                        ["kind", "statement", "status"]),
    "Adjudication": ({"subject": SUBJECT,
                      "assessment_kind": {"enum": ["source_integrity", "semantic_entailment", "scientific_judgment"]},
                      "verdict": {"enum": ["SUPPORTED", "NOT_SUPPORTED", "UNCLEAR"]},
                      "rationale": _str(20000),
                      "adjudicator_actor_id": {"type": ["string", "null"]},
                      "adjudicator_roles": {"type": "array", "items": _str(64)},
                      "independence_level": {"enum": ["author", "independent"]},
                      "independence_group": {"type": ["string", "null"]},
                      "cited_evidence": {"type": "array", "items": {"type": "object"}},
                      "subject_revision_digest": {"type": ["string", "null"]},
                      "adjudicated_at": {"type": ["string", "null"]},
                      "is_historical": {"type": "boolean"},
                      "raw_legacy_verdict": {"type": ["string", "null"]},
                      "source_registry": {"type": ["string", "null"]},
                      "legacy_record_id": {"type": ["string", "null"]},
                      # Evidence-Claim Adjudication (plan v3 3A.2): kind="evidence_claim"
                      "kind": {"enum": ["evidence_claim"]}, "statement": {"type": "object"}, "passage": {"type": "object"},
                      "risk_tier": {"enum": list(taxonomy.vocabulary("eca_risk_tier"))},
                      "source_match": {"type": "object"}, "passage_proposition": {"type": "string"},
                      "qualifier_cues": {"type": "array", "items": {"type": "object"}},
                      "equivalence": {"enum": list(taxonomy.vocabulary("eca_equivalence"))},
                      "dropped_conditions": {"type": "array", "items": {"type": "string"}},
                      "cue_handling": {"type": "array", "items": {"type": "object"}},
                      "counter_evidence": {"type": "array", "items": {"type": "object"}},
                      "eca_verdict": {"enum": list(taxonomy.vocabulary("eca_verdict"))},
                      "required_qualification": {"type": "array", "items": {"type": "string"}},
                      "generated_by": {"type": "string"}, "confirms": {"type": ["string", "null"]},
                      "confirmed_by_researcher": {"type": "boolean"}, "model": {"type": ["object", "null"]},
                      "guard": {"type": ["string", "null"]}},
                     ["subject", "assessment_kind", "verdict", "rationale"]),
    # 9.2 knowledge types. Validation dimensions are recorded separately and contextually (plan v3 section 4.1).
    "DomainWorkflow": ({"spec": {"type": "object"}, "spec_digest": SHA, "validation": {"type": "object"}}, ["label"]),
    "MethodPlaybook": ({"playbook": {"type": "object"}, "validation": {"type": "object"},
                        "elements": {"type": "array", "items": {"type": "object"}, "maxItems": 500},
                        "conditions": {"type": "array", "items": {"type": "object"}, "maxItems": 200},
                        "lifecycle": {"enum": list(taxonomy.vocabulary("knowledge_lifecycle"))}, "promotion": {"type": "object"},
                        "scope": {"type": "object"}, "exclusions": {"type": "array", "items": _str(2000)},
                        "pins": {"type": "array", "items": _str(300)}, "generated_by": _str(120),
                        "validation_status": _str(60), "usage_records": {"type": "array", "items": {"type": "object"}}}, ["label"]),
    "JournalPolicy": ({"journal": _str(300), "policy_type": {"enum": list(taxonomy.vocabulary("journal_policy_type"))},
                       "effective_from": _str(40), "retrieved_at": _str(40), "source_url": _str(2000), "source_sha256": SHA,
                       "excerpt": _str(5000), "applies_to": {"type": "object"}, "effective_to": _str(40), "superseded_by": _str(300),
                       "reporting_items": {"type": "array", "items": _str(1000), "maxItems": 200}}, ["label", "policy_type"]),
    "KnowledgeItem": ({"kind": {"enum": list(taxonomy.vocabulary("knowledge_kind"))}, "group": _str(100),
                       "scope": {"type": "object"}, "exclusions": {"type": "array", "items": _str(2000)},
                       "validation": {"type": "object"}, "generated_by": _str(120),
                       "evidence_basis": {"type": "array", "items": {"type": "object"}},
                       # 9.2 lifecycle and content of library items (plan v3 4.1, 4.3)
                       "elements": {"type": "array", "items": {"type": "object"}, "maxItems": 500},
                       "lifecycle": {"enum": list(taxonomy.vocabulary("knowledge_lifecycle"))},
                       "promotion": {"type": "object"}, "reproduction": {"type": "object"},
                       "pins": {"type": "array", "items": _str(300)}, "body": {"type": "object"},
                       "usage_records": {"type": "array", "items": {"type": "object"}},
                       "known_criticisms": {"type": "array", "items": {"type": "object"}}}, ["label", "kind"]),
    "ContributionArgument": ({"components": {"type": "object"}, "subject": SUBJECT,
                              "elements": {"type": "array", "items": {"type": "object"}, "maxItems": 500},
                              "paper": {"type": "object"}, "status": _str(60), "review_flags": {"type": "array", "items": {"type": "object"}},
                              "protocol": _str(100), "generated_by": _str(120), "differentiation": {"type": "array", "items": {"type": "object"}},
                              "comparison_run": _str(100)}, ["label"]),
    "AnalysisPlan": ({"playbook_pins": {"type": "array", "items": _str(300)}, "steps": {"type": "array", "items": {"type": "object"}, "maxItems": 200},
                      "declared": {"type": "object"}, "context": {"type": "object"}, "conditions_evaluation": {"type": "array", "items": {"type": "object"}},
                      "fidelity_summary": {"type": "object"}, "domain_workflow": _str(300), "diagnostics": {"type": "array", "items": {"type": "object"}},
                      "robustness": {"type": "array", "items": {"type": "object"}}}, ["label"]),
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
