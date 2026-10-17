"""Typed edges and their invalidation semantics.

An edge ``(source@rev) --relation--> (target@rev)`` is bound to *specific revisions* of both ends
and is immutable. ``dependent`` says which end depends on the other, so the engine knows which
end must be re-checked when the *other* end gets a new revision. ``propagates`` says whether a
re-check of the dependent end should cascade to its own dependents.

These semantics are policy data, not scientific verdicts: they only decide *what must be looked
at again*, never whether a claim is true.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

ANY = None  # any object type except server-only types


@dataclass(frozen=True)
class RelationSpec:
    name: str
    sources: Optional[frozenset]
    targets: Optional[frozenset]
    dependent: str          # "source" | "target": the end that depends on the other end
    propagates: bool
    meaning: str


def _s(*names):
    return frozenset(names)


_EVIDENCE = _s("Result", "EvidencePassage", "Source", "Analysis", "Adjudication")
_HYPOTHESIS = _s("Claim", "Theory", "Mechanism")

SPECS: dict[str, RelationSpec] = {x.name: x for x in [
    RelationSpec("defines", _s("Theory", "Construct", "Source", "Mechanism"),
                 _s("Construct", "Theory", "Mechanism"), "target", True,
                 "source supplies the definition that target relies on"),
    RelationSpec("operationalizes", _s("Proxy", "Dataset"), _s("Construct"), "source", True,
                 "source is an operational stand-in for target construct"),
    RelationSpec("measures", _s("Proxy", "Dataset"), _s("Construct", "Mechanism"), "source", True,
                 "source measures target"),
    RelationSpec("tests", _s("Analysis", "Result"),
                 _s("Theory", "Mechanism", "Claim", "ResearchQuestion"), "source", True,
                 "source is an empirical test of target"),
    RelationSpec("estimated_with", _s("Analysis", "Result"),
                 _s("Dataset", "Proxy", "Analysis"), "source", True,
                 "source was estimated using target data/proxy/method"),
    RelationSpec("supports", _EVIDENCE, _HYPOTHESIS, "target", True,
                 "source is offered as support for target"),
    RelationSpec("challenges", _EVIDENCE, _HYPOTHESIS, "target", False,
                 "source is offered as a challenge to target"),
    RelationSpec("derived_from", ANY, ANY, "source", True,
                 "source was derived from target"),
    RelationSpec("depends_on", ANY, ANY, "source", True,
                 "source's validity depends on target"),
    RelationSpec("cited_by", _s("Source", "EvidencePassage", "Claim"),
                 _s("ManuscriptArtifact"), "target", False,
                 "source is cited by target manuscript artifact"),
]}
RELATIONS = tuple(SPECS)


def edge_type_error(relation: str, source_type: str, target_type: str) -> Optional[str]:
    spec = SPECS.get(relation)
    if spec is None:
        return f"unknown relation {relation!r}"
    if spec.sources is not None and source_type not in spec.sources:
        return f"relation {relation!r} not allowed from {source_type} (allowed: {sorted(spec.sources)})"
    if spec.targets is not None and target_type not in spec.targets:
        return f"relation {relation!r} not allowed to {target_type} (allowed: {sorted(spec.targets)})"
    return None


def orient(relation: str, source: tuple, target: tuple) -> tuple[tuple, tuple]:
    """Return ``(dependent_node, upstream_node)`` for an edge."""
    if SPECS[relation].dependent == "source":
        return source, target
    return target, source
