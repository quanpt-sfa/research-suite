"""Base interface for discipline-specific research profiles.

Keeps generic workflow mechanics discipline-neutral while providing
targeted domain traditions, evidence preferences, checklists, and validity criteria.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class DisciplineProfile:
    """Discipline-specific extension profile for research workflows."""
    identifier: str
    title: str
    description: str
    research_traditions: list[str] = field(default_factory=list)
    preferred_evidence_types: list[str] = field(default_factory=list)
    methodological_checklist: list[dict[str, Any]] = field(default_factory=list)
    specialized_terminology: dict[str, str] = field(default_factory=dict)
    validity_questions: list[str] = field(default_factory=list)
    common_inferential_limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
