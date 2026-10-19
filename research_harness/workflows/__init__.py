"""Modular Research Workflows for Research Suite 9.1.

Inspired by Deep Agents isolated contexts and progressive disclosure:
- 5 independently executable research workflows (Contribution Finding, Literature Review, Methods, Results, Discussion).
- Progressive skill disclosure: compact metadata at startup, on-demand task instructions.
- Shared Context Builder with selective retrieval, graph expansion, deduplication, and budget enforcement.
- Token instrumentation and usage tracking per task.
- Selective orchestration, artifact reuse/caching, and result change propagation.
- Discipline research profiles (accounting, finance, education).
"""
from __future__ import annotations

from research_harness.workflows.context import build_context
from research_harness.workflows.contracts import (
    ContextPackage,
    ContributionFindingInput,
    ContributionFindingResult,
    DiscussionInput,
    DiscussionResult,
    LiteratureReviewInput,
    LiteratureReviewResult,
    MethodsEvaluationInput,
    MethodsEvaluationResult,
    ResultsInterpretationInput,
    ResultsInterpretationResult,
    TaskUsage,
    TokenBudget,
    WorkflowOutput,
)
from research_harness.workflows.dispatcher import (
    check_result_change_impact,
    run_workflow,
    run_workflow_sequence,
)
from research_harness.workflows.instrumentation import (
    UsageTracker,
    estimate_tokens,
)
from research_harness.workflows.profiles.base import DisciplineProfile
from research_harness.workflows.profiles.registry import (
    get_profile,
    list_profiles,
    register_profile,
)
from research_harness.workflows.registry import (
    WorkflowMetadata,
    WorkflowRegistry,
    get_registry,
)

__all__ = [
    # Core Context Builder & Workflows
    "build_context",
    "run_workflow",
    "run_workflow_sequence",
    "check_result_change_impact",
    "get_registry",
    "WorkflowRegistry",
    "WorkflowMetadata",
    # Data Contracts
    "ContextPackage",
    "TaskUsage",
    "TokenBudget",
    "WorkflowOutput",
    "ContributionFindingInput",
    "ContributionFindingResult",
    "LiteratureReviewInput",
    "LiteratureReviewResult",
    "MethodsEvaluationInput",
    "MethodsEvaluationResult",
    "ResultsInterpretationInput",
    "ResultsInterpretationResult",
    "DiscussionInput",
    "DiscussionResult",
    # Instrumentation & Caching
    "UsageTracker",
    "estimate_tokens",
    # Discipline Profiles
    "DisciplineProfile",
    "get_profile",
    "list_profiles",
    "register_profile",
]
