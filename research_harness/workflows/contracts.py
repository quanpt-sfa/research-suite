"""Structured input and output contracts for modular research workflows.

Inspired by Deep Agents isolated contexts and progressive disclosure:
Each workflow has explicit typed input and output contracts, ensuring
clean separation of concerns and reproducible artifact derivation.
"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Sequence

from research_harness.workflows.status import (  # noqa: F401  (re-exported four-axis status model)
    Applicability,
    AssessmentStatus,
    ContextStatus,
    ExecutionStatus,
    Status,
    apply_caps,
    axes_from_legacy,
    normalize_status,
)


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
    usage_source: Literal["provider", "scripted", "estimated", "offline_cache", "not_available"] = "not_available"
    # Provider prompt-cache writes (billed above the base input rate). None when the provider reports none.
    cache_creation_tokens: Optional[int] = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskUsage":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

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
            "cache_creation_tokens": self.cache_creation_tokens,
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


# --------------------------------------------------------------------------------------------------
# Prompt rendering primitives. The budgeter (context.py) costs exactly these strings, so what is counted is
# what the model receives.
# --------------------------------------------------------------------------------------------------

def render_object_line(obj: dict[str, Any]) -> str:
    content_str = json.dumps(obj.get("content", {}), sort_keys=True, ensure_ascii=False)
    return f"- [{obj.get('object_type')}] {obj.get('object_id')} (rev {obj.get('revision')}): {content_str}"


def render_excerpt_block(exc: dict[str, Any]) -> str:
    pid = exc.get("passage_id") or exc.get("object_id") or "UNKNOWN_PASSAGE"
    rev = exc.get("revision", 1)
    sid = exc.get("source_id", "UNKNOWN_SOURCE")
    loc = exc.get("locator") or "MISSING_LOCATOR"
    quote = exc.get("excerpt") or exc.get("quote") or exc.get("normalized_claim") or ""
    text = f"- [{pid}:rev{rev}] (Source: {sid}, Locator: {loc}):\n  \"{quote}\""
    claim = exc.get("normalized_claim")
    if claim and claim != quote:
        text += f"\n  Normalized claim: {claim}"
    other = exc.get("other_fields")
    if other:
        text += f"\n  Other fields: {json.dumps(other, sort_keys=True, ensure_ascii=False)}"
    return text


def render_fulltext_block(ft: dict[str, Any]) -> str:
    oid = ft.get("object_id") or ft.get("source_id") or "UNKNOWN_DOC"
    status = ft.get("fulltext_status", "FULLTEXT_LOADED")
    text = ft.get("full_text")
    if text:
        return f"### {oid} (Status: {status})\n{text}"
    if status == "FULLTEXT_DEFERRED":
        return (f"### {oid} (Status: {status})\n[Full text not loaded into context: {ft.get('chars')} characters "
                f"(sha256 {str(ft.get('sha256'))[:16]}); read it in pages with get_fulltext_page('{oid}', offset=..., limit=...)]")
    return f"### {oid} (Status: {status})\n[Full text not loaded into context: {status}]"


def render_comparison_block(entries: Sequence[dict[str, Any]]) -> str:
    """Side-by-side block for a prompt: one sub-block per source, each span with its locator."""
    if not entries:
        return ""
    lines = [f"\n## Comparison: {entries[0]['dimension']}"]
    for e in entries:
        lines.append(f"### {e['source_id']}@{e['revision']} ({e['citation_key']}) - {e['status']}")
        if e["status"] == "NO_FULLTEXT":
            lines.append(f"[No stored full text: {e.get('reason')}]")
        elif e["status"] == "NO_MATCHING_SECTION":
            labels = ", ".join(sorted({a['label'] for a in e["available_sections"]})) or "none"
            lines.append(f"[No section matches this dimension. Sections found: {labels}]")
        for b in e["blocks"]:
            tail = f" [TRUNCATED; resume at char {b['next_offset']}]" if b["truncated"] else ""
            lines.append(f"[{b['locator']}] {b['heading'] or b['label']}{tail}\n{b['text']}")
        for u in e["unread_sections"]:
            lines.append(f"[Not read: {u['label']} '{u['heading']}' chars {u['char_start']}:{u['char_end']}]")
    return "\n".join(lines)


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
    # Edges between the objects in this package ({source_id, source_revision, relation, target_id, target_revision}).
    # Executors read relations from here instead of inferring them from wording.
    relations: list[dict[str, Any]] = field(default_factory=list)
    # FULL | PARTIAL_T2 (background omitted) | PARTIAL_T1 (relevant evidence omitted) | INSUFFICIENT (must-have omitted).
    coverage: str = "FULL"
    # COMPLETE | PARTIAL | INSUFFICIENT (values of ContextStatus).
    context_status: str = "COMPLETE"
    # Section-aligned reads of several sources for one comparison dimension (comparison.compare_sources).
    comparisons: list[dict[str, Any]] = field(default_factory=list)
    # Claims the package is about: the explicit focus claims, else the claims connected to the research question.
    # (`focus_claims` stays the explicit list only.)
    scope_claims: list[str] = field(default_factory=list)
    # Execution runs that really succeeded, `run_id -> output digest` (from the run registry).
    verified_runs: dict[str, str] = field(default_factory=dict)

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

    def _prompt_sections(self) -> tuple[list[str], list[str], list[str]]:
        """(header, skill block, body). `formatted_prompt` is their concatenation; a model request carries the
        skill block in the system prompt and the header + body in the user prompt."""
        header = [f"# Research Workflow: {self.workflow.upper()} (Project: {self.project_id}, Skill Digest: {self.skill_digest[:16] if self.skill_digest else 'none'})"]
        if self.research_question_id:
            header.append(f"Research Question: {self.research_question_id}")
        if self.selection_strategy:
            header.append(f"Selection Strategy: {self.selection_strategy}")
        if self.focus_claims:
            header.append(f"Focus Claims: {', '.join(self.focus_claims)}")
        if self.budget_status == "INCOMPLETE_EXCEEDED":
            header.append("\n> [!WARNING]\n> Context budget exceeded. Low priority context omitted.")

        skill: list[str] = []
        if self.skill_instructions:
            skill.append(f"\n## Operational Skill Instructions:\n{self.skill_instructions.strip()}")

        excerpt_keys = {(e.get("passage_id"), e.get("revision")) for e in self.excerpts}
        parts = ["\n## Selected Objects:"]
        for obj in self.objects:
            if obj.get("object_type") == "EvidencePassage" and (obj.get("object_id"), obj.get("revision")) in excerpt_keys:
                continue  # rendered once, in the evidence excerpts below
            parts.append(render_object_line(obj))

        if self.excerpts:
            parts.append("\n## Evidence Excerpts:")
            parts.extend(render_excerpt_block(exc) for exc in self.excerpts)

        if self.full_texts:
            parts.append("\n## Expanded Full Texts:")
            parts.extend(render_fulltext_block(ft) for ft in self.full_texts)

        if self.comparisons:
            parts.append(render_comparison_block(self.comparisons))
        return header, skill, parts

    @property
    def formatted_prompt(self) -> str:
        """Render formatted markdown prompt representation of the context package and active skill instructions."""
        header, skill, body = self._prompt_sections()
        return "\n".join(header + skill + body)

    @property
    def formatted_user_prompt(self) -> str:
        """Evidence-only part of the request (no skill text). Model-backed runs send the skill in the system prompt."""
        header, _skill, body = self._prompt_sections()
        return "\n".join(header + body)


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
    status: Literal["SUCCESS", "REUSED", "REQUIRES_VERIFICATION", "INCOMPLETE_CONTEXT", "INSUFFICIENT_EVIDENCE",
                    "INVALID_OUTPUT", "BLOCKED", "FAILED"]
    result: dict[str, Any]
    usage: TaskUsage
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    input_fingerprint: Optional[str] = None
    provenance_refs: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    # Authoritative four-axis status when the producer knows it. `status` stays as the legacy combined string.
    status_axes: Optional[Status] = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowOutput":
        """Rebuild an output from its stored JSON (`to_dict` is the inverse)."""
        axes = data.get("status_axes")
        return cls(
            workflow=data["workflow"], task_id=data["task_id"], project_id=data["project_id"], status=data["status"],
            result=data.get("result", {}), usage=TaskUsage.from_dict(data.get("usage") or {}),
            timestamp=data.get("timestamp", ""), input_fingerprint=data.get("input_fingerprint"),
            provenance_refs=data.get("provenance_refs", []), notes=data.get("notes", []),
            metadata=data.get("metadata", {}), status_axes=Status.from_dict(axes) if axes else None)

    @property
    def axes(self) -> Status:
        """Four-axis status: the producer's own when set, otherwise the legacy mapping (`axes_from_legacy`)."""
        return self.status_axes if self.status_axes is not None else axes_from_legacy(self.status)

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
            "status_axes": self.axes.to_dict(),
        }
