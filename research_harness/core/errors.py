"""Errors and clock."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from research_harness.core.model import Decision


class HarnessError(Exception):
    """Operational error (not a proposal decision)."""

    def __init__(self, code: str, message: str = "", **details: Any):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message
        self.details = details


class ProposalRejection(Exception):
    """Raised inside the import transaction; rolls back and becomes a receipt."""

    def __init__(self, decision: Decision, rule: str, message: str = "", **details: Any):
        super().__init__(f"{decision}:{rule}: {message}")
        self.decision = decision
        self.rule = rule
        self.message = message
        self.details = details


Clock = Callable[[], str]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
