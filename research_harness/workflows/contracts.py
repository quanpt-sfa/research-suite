"""Structured input and output contracts for modular research workflows.

Inspired by Deep Agents isolated contexts and progressive disclosure:
Each workflow has explicit typed input and output contracts, ensuring
clean separation of concerns and reproducible artifact derivation.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Sequence


@dataclass
class TaskUsage:
    """Standardized usage and instrumentation record for a workflow task execution.
    
    Matches exact required schema:
    {
      "workflow": "contribution",
      "task_id": "TASK_001",
      "model": null,
      "input_tokens": null,
      "output_tokens": null,
      "cache_read_tokens": null,
      "llm_calls": 0,
      "context_objects": 0,
      "reused_artifacts": 0,
      "elapsed_ms": 0,
      "usage_source": "not_available"
    }
    """
    workflow: str
    task_id: str
    model: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cache_read_tokens: Optional[int] = None
    llm_calls: int = 0
    context_objects: int = 0
    reused_artifacts: int = 0
    elapsed_ms: int = 0
    usage_source: Literal["provider", "estimated", "not_available"] = "not_available"

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow": self.workflow,
            "task_id": self.task_id,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "llm_calls": self.llm_calls,
            "context_objects": self.context_objects,
            "reused_artifacts": self.reused_artifacts,
            "elapsed_ms": self.elapsed_ms,
            "usage_source": self.usage_source,
        }


@dataclass
class TokenBudget:
    """Configurable budget limits and tracking for a workflow task."""
    max_input_tokens: Optional[int] = 12000
    max_output_tokens: Optional[int] = 4000
    max_llm_calls: Optional[int] = 3
    input_tokens_used: int = 0
    output_tokens_used: int = 0
    llm_calls_used: int = 0
    is_exceeded: bool = False
    exceeded_dimension: Optional[str] = None
    status: Literal["OK", "EXCEEDED"] = "OK"

    def check_input_limit(self, planned_tokens: int) -> bool:
        if self.max_input_tokens is not None and planned_tokens > self.max_input_tokens:
            self.is_exceeded = True
            self.exceeded_dimension = "max_input_tokens"
            self.status = "EXCEEDED"
            return False
        return True

    def record_usage(self, input_tokens: int = 0, output_tokens: int = 0, llm_calls: int = 0) -> None:
        self.input_tokens_used += input_tokens
        self.output_tokens_used += output_tokens
        self.llm_calls_used += llm_calls
        if self.max_input_tokens and self.input_tokens_used > self.max_input_tokens:
            self.is_exceeded = True
            self.exceeded_dimension = "max_input_tokens"
            self.status = "EXCEEDED"
        elif self.max_output_tokens and self.output_tokens_used > self.max_output_tokens:
            self.is_exceeded = True
            self.exceeded_dimension = "max_output_tokens"
            self.status = "EXCEEDED"
        elif self.max_llm_calls and self.llm_calls_used > self.max_llm_calls:
            self.is_exceeded = True
            self.exceeded_dimension = "max_llm_calls"
            self.status = "EXCEEDED"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ContextPackage:
    """Structured context package produced by the shared Context Builder."""
    workflow: str
    project_id: str
    research_question_id: Optional[str]
    focus_claims: list[str]
    objects: list[dict[str, Any]]
    excerpts: list[dict[str, Any]]
    full_texts: list[dict[str, Any]]
    omitted_items: list[dict[str, Any]]
    continuation_plan: Optional[dict[str, Any]]
    contradictory_evidence_count: int
    context_bytes: int
    estimated_tokens: int
    is_token_estimate: bool
    manifest: dict[str, Any]
    budget_status: Literal["OK", "INCOMPLETE_EXCEEDED"] = "OK"
    skill_instructions: str = ""
    skill_digest: str = ""
    selection_strategy: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def budget_exceeded(self) -> bool:
        """Indicates if the requested token budget was exceeded."""
        return self.budget_status == "INCOMPLETE_EXCEEDED" or bool(self.omitted_items)

    @property
    def object_manifest(self) -> list[dict[str, Any]]:
        """List of active objects retained in the context package."""
        return self.objects

    @property
    def evidence_passages(self) -> list[dict[str, Any]]:
        """EvidencePassage objects present in context."""
        return [o for o in self.objects if o.get("object_type") == "EvidencePassage"]

    @property
    def results(self) -> list[dict[str, Any]]:
        """Result objects present in context."""
        return [o for o in self.objects if o.get("object_type") == "Result"]

    @property
    def sources(self) -> list[dict[str, Any]]:
        """Source objects present in context."""
        return [o for o in self.objects if o.get("object_type") == "Source"]

    @property
    def claims(self) -> list[dict[str, Any]]:
        """Claim objects present in context."""
        return [o for o in self.objects if o.get("object_type") == "Claim"]

    @property
    def formatted_prompt(self) -> str:
        """Render formatted markdown prompt representation of the context package and active skill instructions."""
        parts = [f"# Research Workflow: {self.workflow.upper()} (Project: {self.project_id}, Skill Digest: {self.skill_digest[:16] if self.skill_digest else 'none'})"]
        if self.research_question_id:
            parts.append(f"Research Question: {self.research_question_id}")
        if self.selection_strategy:
            parts.append(f"Selection Strategy: {self.selection_strategy}")
        if self.focus_claims:
            parts.append(f"Focus Claims: {', '.join(self.focus_claims)}")
        if self.budget_status == "INCOMPLETE_EXCEEDED":
            parts.append("\n> [!WARNING]\n> Context budget exceeded. Low priority context omitted.")

        if self.skill_instructions:
            parts.append(f"\n## Operational Skill Instructions:\n{self.skill_instructions.strip()}")

        parts.append("\n## Selected Objects:")
        for obj in self.objects:
            content_str = json.dumps(obj.get("content", {}), sort_keys=True, ensure_ascii=False)
            parts.append(f"- [{obj.get('object_type')}] {obj.get('object_id')} (rev {obj.get('revision')}): {content_str}")

        if self.excerpts:
            parts.append("\n## Evidence Excerpts:")
            for exc in self.excerpts:
                pid = exc.get("passage_id") or exc.get("object_id") or "UNKNOWN_PASSAGE"
                rev = exc.get("revision", 1)
                sid = exc.get("source_id", "UNKNOWN_SOURCE")
                loc = exc.get("locator") or "MISSING_LOCATOR"
                quote = exc.get("excerpt") or exc.get("quote") or exc.get("normalized_claim") or ""
                parts.append(f"- [{pid}:rev{rev}] (Source: {sid}, Locator: {loc}):\n  \"{quote}\"")

        if self.full_texts:
            parts.append("\n## Expanded Full Texts:")
            for ft in self.full_texts:
                oid = ft.get("object_id") or ft.get("source_id") or "UNKNOWN_DOC"
                status = ft.get("fulltext_status", "FULLTEXT_LOADED")
                text = ft.get("full_text")
                if text:
                    parts.append(f"### {oid} (Status: {status})\n{text}")
                else:
                    parts.append(f"### {oid} (Status: {status})\n[Full text not loaded into context: {status}]")
        return "\n".join(parts)


# --------------------------------------------------------------------------------------------------
# Workflow 1 — Contribution Finding Contracts
# --------------------------------------------------------------------------------------------------

@dataclass
class ContributionFindingInput:
    research_question: str
    literature_claims: list[dict[str, Any]] = field(default_factory=list)
    theory_mechanisms: list[dict[str, Any]] = field(default_factory=list)
    contribution_hypotheses: list[str] = field(default_factory=list)
    available_findings: list[dict[str, Any]] = field(default_factory=list)
    focus_claims: list[str] = field(default_factory=list)


@dataclass
class ContributionFindingResult:
    candidate_contributions: list[dict[str, Any]]
    closest_competing_contributions: list[dict[str, Any]]
    exact_point_of_differentiation: str
    supporting_evidence: list[dict[str, Any]]
    contradicting_evidence: list[dict[str, Any]]
    necessary_assumptions: list[str]
    missing_verification: list[str]
    alternative_contribution_formulations: list[str]
    confidence_explanation: str
    is_preliminary: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------------------------------
# Workflow 2 — Literature Review Contracts
# --------------------------------------------------------------------------------------------------

@dataclass
class LiteratureReviewInput:
    research_question: str
    source_collection: list[dict[str, Any]] = field(default_factory=list)
    screening_criteria: dict[str, Any] = field(default_factory=dict)
    relevant_claims: list[dict[str, Any]] = field(default_factory=list)
    target_concepts_and_theories: list[dict[str, Any]] = field(default_factory=list)
    fulltext_policy: Literal["on_demand", "always", "never"] = "on_demand"


@dataclass
class LiteratureReviewResult:
    evidence_matrix: list[dict[str, Any]]
    source_provenance_and_locators: list[dict[str, Any]]
    methods_and_contexts_reviewed: list[dict[str, Any]]
    supported_findings: list[str]
    unresolved_disagreements: list[dict[str, Any]]
    theoretical_synthesis: str
    evidence_limitations: list[str]
    questions_requiring_fulltext_verification: list[str]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["questions_requiring_full_text_verification"] = self.questions_requiring_fulltext_verification
        return data


# --------------------------------------------------------------------------------------------------
# Workflow 3 — Methods Contracts
# --------------------------------------------------------------------------------------------------

@dataclass
class MethodsEvaluationInput:
    research_question: str
    constructs_and_proxies: list[dict[str, Any]] = field(default_factory=list)
    dataset_descriptions: list[dict[str, Any]] = field(default_factory=list)
    identification_assumptions: list[str] = field(default_factory=list)
    candidate_methods: list[str] = field(default_factory=list)
    methodological_literature: list[dict[str, Any]] = field(default_factory=list)
    discipline_profile: Optional[str] = None


@dataclass
class MethodsEvaluationResult:
    candidate_research_designs: list[dict[str, Any]]
    assumptions: list[str]
    identification_or_measurement_requirements: list[str]
    specification_plans: list[dict[str, Any]]
    alternative_methods: list[dict[str, Any]]
    diagnostic_tests: list[dict[str, Any]]
    limitations_and_unresolved_choices: list[str]
    profile_checklist_results: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.profile_checklist_results:
            evals = self.profile_checklist_results.get("checklist_evaluations", [])
            data["discipline_checklist"] = [
                f"{item.get('item', '')}: {item.get('prompt', '')}" for item in evals
            ]
        else:
            data["discipline_checklist"] = []
        return data


# --------------------------------------------------------------------------------------------------
# Workflow 4 — Results Contracts
# --------------------------------------------------------------------------------------------------

@dataclass
class ResultsInterpretationInput:
    dataset_references: list[dict[str, Any]] = field(default_factory=list)
    analysis_specifications: list[dict[str, Any]] = field(default_factory=list)
    script_or_run_metadata: dict[str, Any] = field(default_factory=dict)
    tables_figures_and_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    previous_result_revisions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ResultsInterpretationResult:
    structured_run_records: list[dict[str, Any]]
    input_and_parameter_references: list[dict[str, Any]]
    output_artifact_references: list[dict[str, Any]]
    reproducibility_metadata: dict[str, Any]
    result_comparisons: list[dict[str, Any]]
    robustness_summaries: list[dict[str, Any]]
    findings_supported_by_available_outputs: list[str]
    changed_results_and_affected_claims: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------------------------------
# Workflow 5 — Discussion Contracts
# --------------------------------------------------------------------------------------------------

@dataclass
class DiscussionInput:
    results_and_robustness_findings: list[dict[str, Any]] = field(default_factory=list)
    contribution_candidates: list[dict[str, Any]] = field(default_factory=list)
    literature_evidence: list[dict[str, Any]] = field(default_factory=list)
    methods_assumptions: list[str] = field(default_factory=list)
    relevant_limitations: list[str] = field(default_factory=list)
    research_question: Optional[str] = None


@dataclass
class DiscussionResult:
    supported_interpretations: list[dict[str, Any]]
    theoretical_implications: list[str]
    alternative_explanations: list[dict[str, Any]]
    contradictions_with_earlier_literature: list[dict[str, Any]]
    scope_and_boundary_conditions: list[str]
    revised_contribution_statements: list[str]
    unresolved_questions: list[str]
    reopens_contribution_workflow: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["contradictions_with_prior_literature_or_claims"] = self.contradictions_with_earlier_literature
        return data


# --------------------------------------------------------------------------------------------------
# Universal Workflow Output Wrapper
# --------------------------------------------------------------------------------------------------

@dataclass
class WorkflowOutput:
    """Top-level container for any executed workflow."""
    workflow: str
    task_id: str
    project_id: str
    status: Literal["SUCCESS", "REUSED", "INCOMPLETE_CONTEXT", "BLOCKED", "FAILED"]
    result: dict[str, Any]
    usage: TaskUsage
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    input_fingerprint: Optional[str] = None
    provenance_refs: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow": self.workflow,
            "task_id": self.task_id,
            "project_id": self.project_id,
            "status": self.status,
            "result": self.result,
            "usage": self.usage.to_dict(),
            "timestamp": self.timestamp,
            "input_fingerprint": self.input_fingerprint,
            "provenance_refs": self.provenance_refs,
            "notes": self.notes,
            "metadata": self.metadata,
        }
