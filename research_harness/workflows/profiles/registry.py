"""Registry for discipline-specific research profiles."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from research_harness.workflows.profiles.accounting import ACCOUNTING_PROFILE
from research_harness.workflows.profiles.base import DisciplineProfile
from research_harness.workflows.profiles.education import EDUCATION_PROFILE
from research_harness.workflows.profiles.finance import FINANCE_PROFILE

_PROFILES: dict[str, DisciplineProfile] = {
    "accounting": ACCOUNTING_PROFILE,
    "finance": FINANCE_PROFILE,
    "education": EDUCATION_PROFILE,
}


def list_profiles() -> list[dict[str, Any]]:
    """Return list of available discipline profiles with summary metadata."""
    return [
        {
            "identifier": p.identifier,
            "title": p.title,
            "description": p.description,
            "traditions_count": len(p.research_traditions),
            "checklist_items_count": len(p.methodological_checklist),
        }
        for p in _PROFILES.values()
    ]


def get_profile(identifier: str) -> DisciplineProfile:
    """Retrieve discipline profile by identifier."""
    key = identifier.strip().lower()
    if key not in _PROFILES:
        raise KeyError(f"Discipline profile '{identifier}' not found. Available: {list(_PROFILES.keys())}")
    return _PROFILES[key]


def register_profile(profile: DisciplineProfile) -> None:
    """Register a new custom discipline profile."""
    _PROFILES[profile.identifier.lower()] = profile
