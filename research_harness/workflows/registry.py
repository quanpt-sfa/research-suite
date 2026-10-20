"""Workflow registry with progressive skill disclosure.

Inspired by Deep Agents SkillsMiddleware:
- At initialization, only compact metadata (name, description, input/output contracts)
  is registered.
- Detailed task-specific instructions (from SKILL.md) are loaded dynamically on demand
  only when a specific workflow is requested.
- Never concatenates all five workflow instructions into a single prompt.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

_FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class WorkflowMetadata:
    """Compact metadata for a registered workflow."""
    name: str
    title: str
    description: str
    version: str
    inputs: list[str]
    outputs: list[str]
    skill_path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class WorkflowRegistry:
    """Registry maintaining available research workflows with progressive disclosure."""

    def __init__(self, skills_dir: Optional[Path] = None):
        if skills_dir is None:
            self._skills_dir = Path(__file__).parent / "skills"
        else:
            self._skills_dir = skills_dir

        self._cache: dict[str, WorkflowMetadata] = {}
        # name -> ((mtime_ns, size), instructions). Keyed by the file's stat so an edit is seen at the next call.
        self._instructions_cache: dict[str, tuple[tuple[int, int], str]] = {}
        self._initialize_metadata()

    def _initialize_metadata(self) -> None:
        """Scan skills directory and build lightweight metadata registry without full body load."""
        workflow_dirs = [
            ("contribution", "Contribution Finding Workflow",
             "Identify, challenge, and refine potential scientific contributions against prior literature.",
             ["research_question", "literature_claims", "theory_mechanisms", "contribution_hypotheses", "available_findings"],
             ["candidate_contributions", "closest_competing_contributions", "exact_point_of_differentiation",
              "supporting_evidence", "contradicting_evidence", "necessary_assumptions", "missing_verification",
              "alternative_contribution_formulations", "confidence_explanation"]),
            ("literature", "Literature Review Workflow",
             "Retrieve, assess, and synthesize relevant scientific literature into a structured evidence matrix.",
             ["research_question", "source_collection", "screening_criteria", "relevant_claims", "target_concepts_and_theories"],
             ["evidence_matrix", "source_provenance_and_locators", "methods_and_contexts_reviewed",
              "supported_findings", "unresolved_disagreements", "theoretical_synthesis",
              "evidence_limitations", "questions_requiring_fulltext_verification"]),
            ("methods", "Methods Design & Evaluation Workflow",
             "Design, formalize, and evaluate research methods and identification strategies appropriate to the research question.",
             ["research_question", "constructs_and_proxies", "dataset_descriptions", "identification_assumptions", "candidate_methods", "methodological_literature"],
             ["candidate_research_designs", "assumptions", "identification_or_measurement_requirements",
              "specification_plans", "alternative_methods", "diagnostic_tests", "limitations_and_unresolved_choices"]),
            ("results", "Results Interpretation & Reproducibility Workflow",
             "Organize reproducible empirical analyses, interpret computational outputs, and detect changed results affecting claims.",
             ["dataset_references", "analysis_specifications", "script_or_run_metadata", "tables_figures_and_diagnostics", "previous_result_revisions"],
             ["structured_run_records", "input_and_parameter_references", "output_artifact_references",
              "reproducibility_metadata", "result_comparisons", "robustness_summaries",
              "findings_supported_by_available_outputs", "changed_results_and_affected_claims"]),
            ("discussion", "Discussion & Theoretical Integration Workflow",
             "Interpret empirical findings relative to theory and literature, delineate boundary conditions, and re-examine contributions.",
             ["results_and_robustness_findings", "contribution_candidates", "literature_evidence", "methods_assumptions", "relevant_limitations", "research_question"],
             ["supported_interpretations", "theoretical_implications", "alternative_explanations",
              "contradictions_with_earlier_literature", "scope_and_boundary_conditions",
              "revised_contribution_statements", "unresolved_questions"]),
        ]

        for name, title, desc, inputs, outputs in workflow_dirs:
            skill_file = self._skills_dir / name / "SKILL.md"
            self._cache[name] = WorkflowMetadata(
                name=name,
                title=title,
                description=desc,
                version="9.1.0",
                inputs=inputs,
                outputs=outputs,
                skill_path=str(skill_file),
            )

    def list_workflows(self) -> list[dict[str, Any]]:
        """Return list of compact workflow metadata without loading full instruction texts."""
        return [meta.to_dict() for meta in self._cache.values()]

    def get_metadata(self, name: str) -> WorkflowMetadata:
        """Get compact metadata for a workflow."""
        canonical = name.strip().lower()
        if canonical not in self._cache:
            raise KeyError(f"Unknown workflow '{name}'. Available: {list(self._cache.keys())}")
        return self._cache[canonical]

    def load_skill_instructions(self, name: str) -> str:
        """Progressively load full skill instructions on demand."""
        canonical = name.strip().lower()
        meta = self.get_metadata(canonical)
        p = Path(meta.skill_path)
        if not p.is_file():
            raise FileNotFoundError(f"SKILL.md for workflow '{name}' not found at {p}")
        st = p.stat()
        stamp = (st.st_mtime_ns, st.st_size)
        cached = self._instructions_cache.get(canonical)
        if cached is not None and cached[0] == stamp:
            return cached[1]

        raw = p.read_text(encoding="utf-8")
        # Strip frontmatter if present to get markdown instruction body
        match = _FRONTMATTER_PATTERN.match(raw)
        if match:
            instructions = raw[match.end():].strip()
        else:
            instructions = raw.strip()

        self._instructions_cache[canonical] = (stamp, instructions)
        return instructions

    def get_skill_digest(self, name: str) -> str:
        """Compute SHA-256 digest of active SKILL.md instructions."""
        inst = self.load_skill_instructions(name)
        return hashlib.sha256(inst.encode("utf-8")).hexdigest()

    def get_prompt_for_workflow(self, name: str) -> str:
        """Construct full specialized prompt for this workflow alone."""
        meta = self.get_metadata(name)
        instructions = self.load_skill_instructions(name)
        return (
            f"# Workflow: {meta.title} (v{meta.version})\n\n"
            f"Description: {meta.description}\n\n"
            f"Input Contract: {', '.join(meta.inputs)}\n"
            f"Output Contract: {', '.join(meta.outputs)}\n\n"
            f"## Instructions\n{instructions}"
        )


@dataclass
class WorkflowDefinition:
    """Full workflow definition including loaded instructions."""
    metadata: WorkflowMetadata
    instructions: str

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def description(self) -> str:
        return self.metadata.description

    @property
    def title(self) -> str:
        return self.metadata.title


_DEFAULT_REGISTRY: Optional[WorkflowRegistry] = None


def get_registry() -> WorkflowRegistry:
    """Get the singleton default workflow registry."""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = WorkflowRegistry()
    return _DEFAULT_REGISTRY


def list_workflows() -> list[dict[str, Any]]:
    """List compact metadata for all registered workflows."""
    return get_registry().list_workflows()


def get_workflow_definition(name: str) -> WorkflowDefinition:
    """Get full workflow definition with loaded skill instructions."""
    reg = get_registry()
    meta = reg.get_metadata(name)
    instructions = reg.load_skill_instructions(name)
    return WorkflowDefinition(metadata=meta, instructions=instructions)


def load_skill_instructions(name: str) -> str:
    """Load full skill instructions for a workflow."""
    return get_registry().load_skill_instructions(name)


def get_skill_digest(name: str) -> str:
    """Compute SHA-256 digest of active SKILL.md instructions."""
    return get_registry().get_skill_digest(name)
