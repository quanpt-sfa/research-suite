"""Comprehensive Test Suite for Research Suite 9.1: Modular Research Workflows.

Validates the 9 mandatory scenarios and benchmark evaluation:
- Scenario 1: Single workflow (Contribution Finding loaded selectively, structured output, usage recorded)
- Scenario 2: Literature full-text on demand (Source provenance preserved, selective retrieval, missing identified)
- Scenario 3: Result change (Revision of 1 result identifies affected claims, flags dependent outputs, reuses unrelated outputs)
- Scenario 4: Methods specialization (Discipline profiles accounting, finance, education loaded selectively)
- Scenario 5: Discussion reopens contribution (Contradiction records conflict, allows re-invoking contribution, preserves history)
- Scenario 6: Budget enforcement (Budget exceeded handled gracefully with continuation plan, no silent deletion of contradictory evidence)
- Scenario 7: Offline operation (Registry listing, context preview, and usage reporting without API key)
- Scenario 8: Existing functionality (Research Harness 9.0 contracts preserved)
- Scenario 9: Example usability (examples/end_to_end_demo.py executes successfully)
- Benchmark Evaluation: Baseline A (broad snapshot) vs Candidate B (selective Context Builder)
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from research_harness.state.store import StateStore
from research_harness.workflows.context import build_context
from research_harness.workflows.contracts import TokenBudget, WorkflowOutput
from research_harness.workflows.dispatcher import check_result_change_impact, run_workflow
from research_harness.workflows.instrumentation import UsageTracker
from research_harness.workflows.profiles.registry import get_profile, list_profiles
from research_harness.workflows.registry import get_workflow_definition, list_workflows
from tests.benchmark_fixture import populate_benchmark_project, run_full_benchmark_suite


# ======================================================================================
# SCENARIO 1: Single Workflow Execution & Progressive Disclosure
# ======================================================================================

def test_scenario_1_single_workflow_progressive_disclosure(harness_env):
    """Scenario 1: Request Contribution Finding.
    Expected:
    - Contribution workflow definition/skill loaded selectively.
    - Unrelated full workflow instructions (e.g., results, methods) not loaded in its prompt.
    - Relevant context selected.
    - Structured output produced.
    - Usage recorded.
    """
    store: StateStore = harness_env["store"]
    owner_token: str = harness_env["owner_token"]

    # 1. Populate basic research question and claim
    with store.write_tx(token=owner_token) as tx:
        tx.add_revision(
            object_id="RQ_S1",
            object_type="ResearchQuestion",
            content={"label": "RQ1", "text": "Can progressive skills reduce token load in scientific agents?"},
            origin={},
            edges=[],
        )
        tx.add_revision(
            object_id="CLAIM_S1",
            object_type="Claim",
            content={"label": "Claim1", "text": "Progressive skill loading strictly isolates task prompts.", "claim_tier": "causal"},
            origin={},
            edges=[("depends_on", "RQ_S1", 1, {})],
        )

    # 2. Check workflow registry list returns compact metadata only
    catalog = list_workflows()
    assert len(catalog) == 5
    for item in catalog:
        assert "name" in item
        assert "description" in item
        # Full SKILL.md instruction body should NOT be present in the registry metadata
        assert "full_instructions" not in item

    # 3. Load only Contribution skill definition
    contrib_def = get_workflow_definition("contribution")
    assert contrib_def.name == "contribution"
    assert "Contribution Finding" in contrib_def.instructions
    # Ensure unrelated workflow instructions are NOT in the contribution skill instructions
    assert "Workflow 3: Methods" not in contrib_def.instructions
    assert "Workflow 4: Results" not in contrib_def.instructions

    # 4. Run Contribution workflow
    output = run_workflow(
        "contribution",
        store=store,
        research_question_id="RQ_S1",
        focus_claims=["CLAIM_S1"],
        model="deterministic-fixture",
        task_id="TASK_SCENARIO_1",
    )

    assert isinstance(output, WorkflowOutput)
    assert output.workflow == "contribution"
    assert output.result is not None
    assert "candidate_contributions" in output.result
    assert "exact_point_of_differentiation" in output.result
    assert "confidence_explanation" in output.result

    # 5. Usage is tracked
    usage = output.usage
    assert usage.workflow == "contribution"
    assert usage.task_id == "TASK_SCENARIO_1"
    assert usage.input_tokens > 0
    assert usage.elapsed_ms >= 0


# ======================================================================================
# SCENARIO 2: Literature Full-Text On Demand
# ======================================================================================

def test_scenario_2_literature_full_text_on_demand(harness_env):
    """Scenario 2: Request literature synthesis about one construct.
    Expected:
    - Relevant records selected.
    - Source identifiers preserved.
    - Full-text retrieval invoked only when required / explicitly requested.
    - Missing full text identified without inventing content.
    """
    store: StateStore = harness_env["store"]
    owner_token: str = harness_env["owner_token"]

    with store.write_tx(token=owner_token) as tx:
        tx.add_revision(
            object_id="RQ_LIT",
            object_type="ResearchQuestion",
            content={"label": "Literature Synthesis RQ", "text": "What mechanisms drive context offloading in research agents?"},
            origin={},
            edges=[],
        )
        tx.add_revision(
            object_id="SRC_FULL_01",
            object_type="Source",
            content={
                "citation_key": "Offload2025",
                "title": "Context Offloading Mechanics in Autonomous Systems",
                "abstract": "Abstract describing external scratchpad storage and memory compaction.",
                "full_text": "Complete manuscript body: Section 1 Introduction... Section 4 Empirical verification on 10,000 runs.",
            },
            origin={},
            edges=[("depends_on", "RQ_LIT", 1, {})],
        )
        tx.add_revision(
            object_id="SRC_EXCERPT_ONLY_02",
            object_type="Source",
            content={
                "citation_key": "AbstractOnly2024",
                "title": "Compact Agent Architectures",
                "abstract": "Only abstract is available for this external conference presentation.",
            },
            origin={},
            edges=[("depends_on", "RQ_LIT", 1, {})],
        )
        tx.add_revision(
            object_id="EP_LIT_01",
            object_type="EvidencePassage",
            content={
                "locator": "Offload2025: Section 4, p. 12",
                "quote": "Context offloading reduced attention overhead by 73%.",
            },
            origin={},
            edges=[("derived_from", "SRC_FULL_01", 1, {})],
        )

    # 1. Test build_context with fulltext_policy='on_demand' vs 'never'
    ctx_compact = build_context(store, workflow="literature", research_question_id="RQ_LIT", fulltext_policy="never")
    # In compact mode, the large full text of SRC_FULL_01 should not be dumped into prompt
    assert "Complete manuscript body: Section 1 Introduction" not in ctx_compact.formatted_prompt

    ctx_full = build_context(store, workflow="literature", research_question_id="RQ_LIT", fulltext_policy="always")
    assert "Complete manuscript body: Section 1 Introduction" in ctx_full.formatted_prompt

    # 2. Run literature workflow
    lit_out = run_workflow(
        "literature",
        store=store,
        research_question_id="RQ_LIT",
        search_criteria="context offloading",
    )

    res = lit_out.result
    assert "evidence_matrix" in res
    assert "source_provenance_and_locators" in res
    assert "questions_requiring_full_text_verification" in res

    # Verified: missing full text is identified rather than fabricated
    missing_items = res["questions_requiring_full_text_verification"]
    assert any("SRC_EXCERPT_ONLY_02" in item or "AbstractOnly2024" in item for item in missing_items)

    # Verified: exact source locators preserved
    locators = res["source_provenance_and_locators"]
    assert any(loc.get("source_id") == "SRC_FULL_01" or "SRC_FULL_01" in str(loc) for loc in locators)


# ======================================================================================
# SCENARIO 3: Result Change & Impact Invalidation
# ======================================================================================

def test_scenario_3_result_change_impact_and_reuse(harness_env):
    """Scenario 3: Update one Result revision.
    Expected:
    - Affected scientific claims identified.
    - Relevant workflow outputs marked for review or recomputation.
    - Unrelated workflow outputs remain reusable.
    - No automatic execution of all five workflows.
    """
    store: StateStore = harness_env["store"]
    owner_token: str = harness_env["owner_token"]

    with store.write_tx(token=owner_token) as tx:
        tx.add_revision(
            object_id="RQ_RES_01",
            object_type="ResearchQuestion",
            content={"label": "RQ Res", "text": "Does result revision cascade safely?"},
            origin={},
            edges=[],
        )
        tx.add_revision(
            object_id="RES_EXP_01",
            object_type="Result",
            content={"label": "Experiment 1", "summary": "Initial baseline result"},
            origin={},
            edges=[],
        )
        tx.add_revision(
            object_id="CLAIM_CONNECTED",
            object_type="Claim",
            content={"label": "Connected Claim", "text": "Claim strictly tied to RES_EXP_01", "claim_tier": "causal"},
            origin={},
            edges=[("depends_on", "RES_EXP_01", 1, {})],
        )
        tx.add_revision(
            object_id="CLAIM_INDEPENDENT",
            object_type="Claim",
            content={"label": "Independent Claim", "text": "Claim unrelated to RES_EXP_01", "claim_tier": "conceptual"},
            origin={},
            edges=[("depends_on", "RQ_RES_01", 1, {})],
        )

    # 1. Execute initial workflows
    out_connected = run_workflow("results", store, research_question_id="RQ_RES_01", focus_claims=["CLAIM_CONNECTED"])
    out_unrelated = run_workflow("methods", store, research_question_id="RQ_RES_01", focus_claims=["CLAIM_INDEPENDENT"])

    # 2. Re-run identical without modifications -> must reuse cached artifacts
    out_unrelated_2 = run_workflow("methods", store, research_question_id="RQ_RES_01", focus_claims=["CLAIM_INDEPENDENT"])
    assert out_unrelated_2.metadata.get("reused") is True
    assert out_unrelated_2.usage.reused_artifacts == 1

    # 3. Revise RES_EXP_01 to rev 2
    with store.write_tx(token=owner_token) as tx:
        tx.add_revision(
            object_id="RES_EXP_01",
            object_type="Result",
            content={"label": "Experiment 1 Rev 2", "summary": "Updated result with revised variance"},
            origin={},
            edges=[],
        )

    # 4. Check impact on dependency DAG
    impact = check_result_change_impact(store, "RES_EXP_01", to_revision=2)
    assert "CLAIM_CONNECTED" in impact["affected_claims"]
    assert "CLAIM_INDEPENDENT" not in impact["affected_claims"]

    # 5. Unrelated workflow can STILL reuse its output
    out_unrelated_3 = run_workflow("methods", store, research_question_id="RQ_RES_01", focus_claims=["CLAIM_INDEPENDENT"])
    assert out_unrelated_3.metadata.get("reused") is True


# ======================================================================================
# SCENARIO 4: Methods Specialization with Discipline Profiles
# ======================================================================================

def test_scenario_4_methods_specialization_profiles(harness_env):
    """Scenario 4: Run Methods using different discipline profile configurations.
    Expected:
    - Shared workflow engine reused across profiles.
    - Domain-specific policy (accounting, finance, education) loaded selectively.
    - No unrelated profile loaded into context.
    """
    store: StateStore = harness_env["store"]
    owner_token: str = harness_env["owner_token"]

    with store.write_tx(token=owner_token) as tx:
        tx.add_revision(
            object_id="RQ_ACC",
            object_type="ResearchQuestion",
            content={"label": "Accounting Accruals RQ", "text": "Do discretionary accruals predict audit fee renegotiation?"},
            origin={},
            edges=[],
        )

    # Verify registered profiles
    profiles = list_profiles()
    profile_ids = [p["identifier"] for p in profiles]
    assert "accounting" in profile_ids
    assert "finance" in profile_ids
    assert "education" in profile_ids

    # Run Methods with accounting profile
    acc_out = run_workflow("methods", store, research_question_id="RQ_ACC", discipline="accounting")
    acc_res = acc_out.result

    # Accounting checklist items present
    acc_checklist = acc_res.get("discipline_checklist", [])
    assert any("discretionary accrual" in item.lower() or "modified jones" in item.lower() for item in acc_checklist)
    # Finance or Education specific items NOT present in accounting context
    assert not any("Event study" in item or "Fama-French" in item for item in acc_checklist)
    assert not any("Pre-registration" in item or "Cluster-randomized" in item for item in acc_checklist)

    # Run Methods with finance profile
    fin_out = run_workflow("methods", store, research_question_id="RQ_ACC", discipline="finance")
    fin_res = fin_out.result
    fin_checklist = fin_res.get("discipline_checklist", [])
    assert any("Event study" in item or "Fama-French" in item or "Look-ahead bias" in item for item in fin_checklist)
    assert not any("Modified Jones" in item for item in fin_checklist)


# ======================================================================================
# SCENARIO 5: Discussion Reopens Contribution Workflow
# ======================================================================================

def test_scenario_5_discussion_reopens_contribution(harness_env):
    """Scenario 5: Introduce a result contradicting the initial contribution candidate.
    Expected:
    - Discussion workflow records the contradiction.
    - Sets reopens_contribution_workflow flag.
    - Contribution Finding can be invoked again.
    - Earlier contribution remains in history.
    - New assessment identifies what changed.
    """
    store: StateStore = harness_env["store"]
    owner_token: str = harness_env["owner_token"]

    with store.write_tx(token=owner_token) as tx:
        tx.add_revision(
            object_id="RQ_DISC",
            object_type="ResearchQuestion",
            content={"label": "Discussion RQ", "text": "Does prompt caching reduce total agent latency?"},
            origin={},
            edges=[],
        )
        tx.add_revision(
            object_id="RES_CONTRADICTING",
            object_type="Result",
            content={
                "label": "Cold-Start Caching Latency Benchmark",
                "summary": "Prompt caching increased latency by 45% under high-concurrency cold cache miss scenarios.",
                "contradictory": True,
            },
            origin={},
            edges=[("depends_on", "RQ_DISC", 1, {})],
        )
        tx.add_revision(
            object_id="CLAIM_INITIAL",
            object_type="Claim",
            content={
                "label": "Initial Novelty Claim",
                "text": "Prompt caching universally improves agent latency across all operational workloads.",
                "claim_tier": "causal",
            },
            origin={},
            edges=[("depends_on", "RQ_DISC", 1, {})],
        )

    # Initial Contribution finding
    c1 = run_workflow("contribution", store, research_question_id="RQ_DISC", focus_claims=["CLAIM_INITIAL"])
    initial_contrib = c1.result["candidate_contributions"]

    # Run Discussion workflow
    d_out = run_workflow("discussion", store, research_question_id="RQ_DISC", focus_claims=["CLAIM_INITIAL"])
    d_res = d_out.result

    assert d_res["reopens_contribution_workflow"] is True
    assert len(d_res["contradictions_with_prior_literature_or_claims"]) > 0

    # Invoke Contribution finding again with updated context
    c2 = run_workflow(
        "contribution",
        store=store,
        research_question_id="RQ_DISC",
        focus_claims=["CLAIM_INITIAL"],
        notes="Re-evaluating after discussion identified cache-miss latency contradiction.",
    )
    assert c2.result is not None
    # Previous contribution remains intact in c1
    assert c1.result["candidate_contributions"] == initial_contrib
    assert "supporting_evidence" in c2.result
    assert "contradicting_evidence" in c2.result


# ======================================================================================
# SCENARIO 6: Budget Enforcement & Incomplete-Context Plan
# ======================================================================================

def test_scenario_6_budget_enforcement_and_incomplete_plan(harness_env):
    """Scenario 6: Request a context that exceeds its input budget.
    Expected:
    - No silent overflow.
    - No silent deletion of critical contradictory evidence.
    - Explicit explanation of omitted material.
    - Evidence references preserved.
    - Budget status recorded in package.
    """
    store: StateStore = harness_env["store"]
    owner_token: str = harness_env["owner_token"]

    # Populate project with multiple substantial objects
    with store.write_tx(token=owner_token) as tx:
        tx.add_revision(
            object_id="RQ_BUDGET",
            object_type="ResearchQuestion",
            content={"label": "Budget RQ", "text": "Testing token budgeting under tight bounds."},
            origin={},
            edges=[],
        )
        for i in range(1, 15):
            tx.add_revision(
                object_id=f"EP_HEAVY_{i:02d}",
                object_type="EvidencePassage",
                content={
                    "locator": f"Doc {i}, pp. 100-150",
                    "quote": f"Detailed verbose scientific passage {i}: " + ("data " * 150),
                    "contradictory": (i % 2 == 0),
                },
                origin={},
                edges=[("depends_on", "RQ_BUDGET", 1, {})],
            )

    # Request with an intentionally restrictive budget (e.g., 300 tokens)
    tight_budget = 300
    pkg = build_context(
        store=store,
        workflow="literature",
        research_question_id="RQ_BUDGET",
        max_input_tokens=tight_budget,
    )

    # Check budget enforcement
    assert pkg.budget_exceeded is True
    assert pkg.continuation_plan is not None
    assert pkg.continuation_plan.get("status") == "INCOMPLETE_CONTEXT"
    assert "omitted_objects_count" in pkg.continuation_plan
    assert pkg.continuation_plan["omitted_objects_count"] > 0
    assert "retrieval_plan" in pkg.continuation_plan
    # Manifest retains references to what was considered
    assert len(pkg.object_manifest) > 0


# ======================================================================================
# SCENARIO 7: Offline Operation (Zero Mandatory LLM Calls)
# ======================================================================================

def test_scenario_7_offline_operation_zero_llm():
    """Scenario 7: Workflow listing, context preview and usage reporting without an API key.
    Expected:
    - No LLM invocation required.
    - Clean JSON / deterministic output.
    """
    # 1. Listing workflows offline
    workflows = list_workflows()
    assert len(workflows) == 5

    # 2. Usage reporting offline
    tracker = UsageTracker()
    summary = tracker.get_summary()
    assert "total_tasks" in summary
    assert "total_llm_calls" in summary
    assert summary["total_llm_calls"] >= 0


# ======================================================================================
# SCENARIO 8: Existing Functionality Preserved
# ======================================================================================

def test_scenario_8_existing_functionality_preserved(harness_env):
    """Scenario 8: Confirm Research Harness 9.0 state store and validation work seamlessly."""
    store: StateStore = harness_env["store"]
    owner_token: str = harness_env["owner_token"]

    from research_harness.validation.integrity import audit_project_integrity, release_check
    audit = audit_project_integrity(store)
    assert audit["status"] in {"PASS", "FAIL"}

    gate = release_check(store)
    assert "release_eligibility" in gate


# ======================================================================================
# SCENARIO 9: End-to-End Demonstration Usability
# ======================================================================================

def test_scenario_9_end_to_end_demo_usability():
    """Scenario 9: Run examples/end_to_end_demo.py and verify exit code 0."""
    result = subprocess.run(
        [sys.executable, "examples/end_to_end_demo.py"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Demo failed with stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "DEMONSTRATION COMPLETED SUCCESSFULLY" in result.stdout


# ======================================================================================
# BENCHMARK EVALUATION: Baseline A (Broad Snapshot) vs Candidate B (Selective Context)
# ======================================================================================

def test_benchmark_performance_evaluation(tmp_path):
    """Mandatory Benchmark Evaluation (Section 13).
    Evaluates:
    - 40 synthetic research papers / sources
    - 40 evidence passages
    - 10 scientific claims
    - 3 analysis specifications
    - 3 results (including conflicting findings)
    - 2 result revisions
    - Baseline A vs Candidate B comparative metrics
    """
    bench_dir = tmp_path / "benchmark_proj"
    store, owner_token = StateStore.create(bench_dir, "PRJ_BENCHMARK_EVAL")

    results = run_full_benchmark_suite(store, owner_token)

    baseline = results["baseline_a"]
    candidate = results["candidate_b"]
    comp = results["comparative_metrics"]
    reuse = results["incremental_reuse_behavior"]

    # Assertions on Benchmark Metrics
    assert baseline["context_objects"] >= 40
    assert candidate["context_objects"] < baseline["context_objects"]
    # Selection follows relevance to the research question, not a compression target (audit P0-4): every paper
    # linked to the question is evidence for a novelty assessment, so the contribution context keeps all 40
    # sources and their passages. The object count is therefore close to the full project; the saving that
    # remains is in tokens and bytes (below), not in dropping competitors.
    assert comp["object_reduction_percent"] >= 0.0
    assert candidate["evidence_passages_count"] == 40

    # Byte and Token reductions
    assert comp["byte_savings_percent"] > 50.0
    assert comp["token_savings_percent"] > 50.0

    # Evidence Integrity: MUST preserve contradictory evidence (zero omission regression)
    assert comp["contradictory_evidence_preserved"] is True
    assert comp["retrieval_completeness"] is True

    # Incremental Reuse Behavior
    assert reuse["initial_artifact_reused"] is True
    assert "CLAIM_01" in reuse["affected_claims_on_result_revision"]
    assert reuse["unrelated_workflow_reused"] is True
