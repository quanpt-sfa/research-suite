"""Discipline research profiles package."""
from __future__ import annotations

from research_harness.workflows.profiles.base import DisciplineProfile
from research_harness.workflows.profiles.registry import get_profile, list_profiles, register_profile

__all__ = [
    "DisciplineProfile",
    "get_profile",
    "list_profiles",
    "register_profile",
]
