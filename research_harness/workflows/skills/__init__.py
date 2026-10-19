"""Progressive skill definitions and workflow executors."""
from __future__ import annotations

from research_harness.workflows.skills.contribution import execute_contribution_workflow
from research_harness.workflows.skills.discussion import execute_discussion_workflow
from research_harness.workflows.skills.literature import execute_literature_workflow
from research_harness.workflows.skills.methods import execute_methods_workflow
from research_harness.workflows.skills.results import execute_results_workflow

__all__ = [
    "execute_contribution_workflow",
    "execute_discussion_workflow",
    "execute_literature_workflow",
    "execute_methods_workflow",
    "execute_results_workflow",
]
