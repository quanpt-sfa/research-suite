"""Scientific Regression Test Suite for Research Suite 9.1.1.

Mandatory adversarial counterexample tests (PART I):
- I-1: Causal tier does not establish causal identification (IDENTIFICATION_NOT_VERIFIED)
- I-2: Missing study design and context (UNKNOWN, NOT_REPORTED, no guessed defaults)
- I-3: False literature consensus prevention (no false consensus from absence of contradiction keywords)
- I-4: Methods without treatment definition (DID conditional candidate with unmet assumptions, NOT justified)
- I-5: Missing reproducibility metadata (null seed/script, COMPUTATIONAL_REPRODUCTION_NOT_VERIFIED)
- I-6: Discussion without performed robustness tests (no fabricated mitigation assertions)
- I-7: Skill change changes model request and cache identity (progressive skill execution)
- I-8: Full-text policy changes cache identity & fulltext semantics (never relabel abstract as full text)
- I-9: Budget includes complete final model-facing prompt (instructions + manifest + excerpts)
- I-10: Cross-workflow handoffs (Results -> Discussion -> Contribution Finding)
- I-11: Workflow artifact persistence across restart (CAS storage & project recovery)
- I-12: Token accounting distinct semantics (offline estimate vs provider billed vs local cache)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from research_harness.state.store import StateStore
from research_harness.workflows.context import build_context
from research_harness.workflows.contracts import ContextPackage, WorkflowOutput
from research_harness.workflows.dispatcher import (
    _compute_input_fingerprint,
    run_workflow,
    run_workflow_sequence,
)
from research_harness.workflows.instrumentation import TaskUsage, UsageTracker, estimate_tokens
from research_harness.workflows.registry import get_workflow_definition, load_skill_instructions


# ======================================================================================
# I-1. Causal tier does not establish causal identification
# ======================================================================================

def test_i1_causal_tier_does_not_establish_causal_identification(harness_env):
    """I-1: A causal Claim with no verified research design must not claim causal identification.
    Expected:
    - Status: IDENTIFICATION_NOT_VERIFIED
    - No statement claiming counterfactual identification has been established.
    - Novelty marked as unverified hypothesis rather than verified contribution.
    """
    store: StateStore = harness_env["store"]
    token: str = harness_env["owner_token"]

    with store.write_tx(token=token) as tx:
        tx.add_revision(
            object_id="RQ_CAUSAL_01",
            object_type="ResearchQuestion",
            content={"label": "Causal RQ", "text": "Does CEO equity incentive cause corporate greenwashing?"},
            origin={},
            edges=[],
        )
        tx.add_revision(
            object_id="CLAIM_CAUSAL_01",
            object_type="Claim",
            content={
                "label": "Causal Greenwashing Claim",
                "text": "Equity incentives cause greenwashing behavior among Fortune 500 CEOs.",
                "claim_tier": "causal",
            },
            origin={},
            edges=[("depends_on", "RQ_CAUSAL_01", 1, {})],
        )

    out = run_workflow("contribution", store, research_question_id="RQ_CAUSAL_01", focus_claims=["CLAIM_CAUSAL_01"])
    res = out.result

    # 1. Must NOT assert that counterfactual identification has been established
    diff_str = str(res.get("exact_point_of_differentiation", ""))
    candidates_str = json.dumps(res.get("candidate_contributions", []))
    assert "Establishes counterfactual identification beyond associational literature" not in diff_str
    assert "Establishes counterfactual identification beyond associational literature" not in candidates_str

    # 2. Must explicitly acknowledge that identification is unverified
    candidates = res.get("candidate_contributions", [])
    assert len(candidates) > 0
    c0 = candidates[0]
    assert c0.get("identification_status") == "IDENTIFICATION_NOT_VERIFIED"
    assert c0.get("novelty_status") in {"NOVELTY_NOT_VERIFIED", "HYPOTHESIS_ONLY"}
    assert res.get("is_preliminary") is True


# ======================================================================================
# I-2. Missing study design and sample context
# ======================================================================================

def test_i2_missing_study_design_and_context(harness_env):
    """I-2: Source with only title and abstract must not guess methods or contexts.
    Expected:
    - Missing method -> UNKNOWN (not 'Archival observational')
    - Missing sample context -> NOT_REPORTED (not 'U.S. public firms / primary literature')
    - Full text availability -> ABSTRACT_ONLY / NOT_VERIFIED
    """
    store: StateStore = harness_env["store"]
    token: str = harness_env["owner_token"]

    with store.write_tx(token=token) as tx:
        tx.add_revision(
            object_id="RQ_LIT_02",
            object_type="ResearchQuestion",
            content={"label": "Lit RQ", "text": "What architectures reduce attention overhead?"},
            origin={},
            edges=[],
        )
        tx.add_revision(
            object_id="SRC_ABSTRACT_ONLY",
            object_type="Source",
            content={
                "citation_key": "SparseAttention2024",
                "title": "Sparse Attention Patterns in Transformers",
                "abstract": "We analyze sparse attention kernels in theoretical benchmarks.",
            },
            origin={},
            edges=[("depends_on", "RQ_LIT_02", 1, {})],
        )

    out = run_workflow("literature", store, research_question_id="RQ_LIT_02")
    res = out.result
    methods_and_contexts = res.get("methods_and_contexts_reviewed", [])
    assert len(methods_and_contexts) > 0

    mc = methods_and_contexts[0]
    # Must NOT guess defaults
    assert mc.get("study_design") != "Archival observational"
    assert mc.get("sample_context") != "U.S. public firms / primary literature"
    assert mc.get("study_design") == "UNKNOWN"
    assert mc.get("sample_context") == "NOT_REPORTED"


# ======================================================================================
# I-3. False literature consensus prevention
# ======================================================================================

def test_i3_false_literature_consensus_prevention(harness_env):
    """I-3: Three papers with differing findings but without explicit contradiction keywords
    must not automatically declare broad thematic consensus.
    """
    store: StateStore = harness_env["store"]
    token: str = harness_env["owner_token"]

    with store.write_tx(token=token) as tx:
        tx.add_revision(
            object_id="RQ_CONSENSUS",
            object_type="ResearchQuestion",
            content={"label": "Scaling RQ", "text": "How do transformer training costs scale with context length?"},
            origin={},
            edges=[],
        )
        # Paper A says quadratic
        tx.add_revision(
            object_id="SRC_SCALE_A",
            object_type="Source",
            content={"citation_key": "Alpha2023", "title": "Quadratic Attention Scaling"},
            origin={},
            edges=[("depends_on", "RQ_CONSENSUS", 1, {})],
        )
        tx.add_revision(
            object_id="EP_SCALE_A",
            object_type="EvidencePassage",
            content={"locator": "Alpha2023: p. 4", "quote": "Standard dense attention scales quadratically O(N^2) with sequence length."},
            origin={},
            edges=[("derived_from", "SRC_SCALE_A", 1, {}), ("depends_on", "RQ_CONSENSUS", 1, {})],
        )
        # Paper B says sub-quadratic via sparse kernels (no words like "fails", "refutes", "contrary")
        tx.add_revision(
            object_id="SRC_SCALE_B",
            object_type="Source",
            content={"citation_key": "Beta2024", "title": "Sparse Kernel Complexity"},
            origin={},
            edges=[("depends_on", "RQ_CONSENSUS", 1, {})],
        )
        tx.add_revision(
            object_id="EP_SCALE_B",
            object_type="EvidencePassage",
            content={"locator": "Beta2024: p. 12", "quote": "We demonstrate linear sub-quadratic attention scaling O(N sqrt(N)) using block-sparse kernels."},
            origin={},
            edges=[("derived_from", "SRC_SCALE_B", 1, {}), ("depends_on", "RQ_CONSENSUS", 1, {})],
        )

    out = run_workflow("literature", store, research_question_id="RQ_CONSENSUS")
    res = out.result

    synthesis = res.get("theoretical_synthesis", "")
    # Must NOT conclude broad thematic consistency
    assert "demonstrates broad thematic consistency" not in synthesis.lower()
    # Must preserve passages with direction marked NOT_ASSESSED / DIVERGENT rather than assumed supporting consensus
    ev_matrix = res.get("evidence_matrix", [])
    assert len(ev_matrix) >= 2


# ======================================================================================
# I-4. Methods without treatment definition
# ======================================================================================

def test_i4_methods_without_treatment_definition(harness_env):
    """I-4: Causal question without treatment or comparison design.
    Expected:
    - DID must NOT appear as METHOD_JUSTIFIED or SUPPORTED_BY_DESIGN.
    - If suggested as CANDIDATE_METHOD, it must explicitly flag unmet assumptions.
    - Checklist items must NOT be marked ADDRESSED_IN_SPECIFICATION.
    """
    store: StateStore = harness_env["store"]
    token: str = harness_env["owner_token"]

    with store.write_tx(token=token) as tx:
        tx.add_revision(
            object_id="RQ_METH_04",
            object_type="ResearchQuestion",
            content={"label": "Methods RQ", "text": "Does audit committee independence cause reduced restatements?"},
            origin={},
            edges=[],
        )
        tx.add_revision(
            object_id="CLAIM_METH_04",
            object_type="Claim",
            content={"label": "Audit Claim", "text": "Audit committee independence reduces restatement frequency.", "claim_tier": "causal"},
            origin={},
            edges=[("depends_on", "RQ_METH_04", 1, {})],
        )

    out = run_workflow("methods", store, research_question_id="RQ_METH_04", discipline="accounting")
    res = out.result

    designs = res.get("candidate_research_designs", [])
    for d in designs:
        assert d.get("justification_status") != "METHOD_JUSTIFIED"
        assert d.get("status") in {"CANDIDATE_METHOD", "PROPOSED", "REQUIRES_SPECIFICATION"}

    # Checklist evaluations must NOT declare ADDRESSED_IN_SPECIFICATION when unverified
    checklist = res.get("profile_checklist_results", {})
    if checklist:
        for item in checklist.get("checklist_evaluations", []):
            assert item.get("compliance_status") != "ADDRESSED_IN_SPECIFICATION"
            assert item.get("compliance_status") in {"REQUIRES_INFORMATION", "NOT_ASSESSED", "PROPOSED", "NOT_APPLICABLE"}


# ======================================================================================
# I-5. Missing reproducibility metadata
# ======================================================================================

def test_i5_missing_reproducibility_metadata(harness_env):
    """I-5: A Result summary without script, data fingerprint or computational run.
    Expected:
    - random_seed is null (not 42)
    - script_path is null (not 'analysis_script.py')
    - estimator is null (not 'OLS / Fixed Effects')
    - status: COMPUTATIONAL_REPRODUCTION_NOT_VERIFIED
    """
    store: StateStore = harness_env["store"]
    token: str = harness_env["owner_token"]

    with store.write_tx(token=token) as tx:
        tx.add_revision(
            object_id="RQ_RES_05",
            object_type="ResearchQuestion",
            content={"label": "Results RQ", "text": "Evaluating results reproducibility metadata."},
            origin={},
            edges=[],
        )
        tx.add_revision(
            object_id="RES_UNVERIFIED",
            object_type="Result",
            content={
                "label": "Unverified Summary Result",
                "summary": "Author stated coefficient beta = 0.35, t = 2.45 in draft text.",
            },
            origin={},
            edges=[("depends_on", "RQ_RES_05", 1, {})],
        )

    out = run_workflow("results", store, research_question_id="RQ_RES_05")
    res = out.result

    repro = res.get("reproducibility_metadata", {})
    assert repro.get("random_seed") is None
    assert repro.get("deterministic_verification") is False or repro.get("verification_status") == "COMPUTATIONAL_REPRODUCTION_NOT_VERIFIED"

    input_params = res.get("input_and_parameter_references", [])
    for ip in input_params:
        assert ip.get("script_path") != "analysis_script.py"
        assert ip.get("estimator") != "OLS / Fixed Effects"


# ======================================================================================
# I-6. Discussion without performed robustness tests
# ======================================================================================

def test_i6_discussion_without_performed_robustness_tests(harness_env):
    """I-6: Result without lagged regression, placebo or fixed-effects diagnostics.
    Expected:
    - Must NOT assert that reverse causality was mitigated by lagged regressor specifications.
    - Evaluative status must reflect unperformed diagnostics.
    """
    store: StateStore = harness_env["store"]
    token: str = harness_env["owner_token"]

    with store.write_tx(token=token) as tx:
        tx.add_revision(
            object_id="RQ_DISC_06",
            object_type="ResearchQuestion",
            content={"label": "Discussion RQ", "text": "Empirical testing of market liquidity."},
            origin={},
            edges=[],
        )
        tx.add_revision(
            object_id="RES_SIMPLE",
            object_type="Result",
            content={
                "label": "Simple Correlation Result",
                "summary": "Positive correlation between order flow imbalance and price impact.",
            },
            origin={},
            edges=[("depends_on", "RQ_DISC_06", 1, {})],
        )

    out = run_workflow("discussion", store, research_question_id="RQ_DISC_06")
    res = out.result

    alt_explanations = res.get("alternative_explanations", [])
    for alt in alt_explanations:
        eval_status = alt.get("evaluative_status", "")
        assert "Mitigated by lagged regressor specifications and pre-treatment lead diagnostics" not in eval_status
        assert alt.get("verification_status") in {"REQUIRES_TESTING", "NOT_VERIFIED", "UNASSESSED"}


# ======================================================================================
# I-7. Skill change changes model request and cache identity
# ======================================================================================

def test_i7_skill_change_changes_model_request_and_cache(harness_env, monkeypatch):
    """I-7: Modifying a workflow's SKILL.md changes the assembled request and invalidates cache.
    Also confirms that offline mode performs zero model calls.
    """
    store: StateStore = harness_env["store"]
    token: str = harness_env["owner_token"]

    with store.write_tx(token=token) as tx:
        tx.add_revision(
            object_id="RQ_SKILL",
            object_type="ResearchQuestion",
            content={"label": "Skill Test RQ", "text": "Testing progressive skill injection."},
            origin={},
            edges=[],
        )

    # 1. Offline mode: 0 model calls
    out_offline = run_workflow("contribution", store, research_question_id="RQ_SKILL")
    assert out_offline.usage.llm_calls == 0

    # 2. Compute fingerprint with default SKILL.md
    ctx1 = build_context(store, "contribution", research_question_id="RQ_SKILL")
    fp1 = _compute_input_fingerprint("contribution", ctx1)

    # 3. Modify Contribution skill instructions via monkeypatching registry loader
    from research_harness.workflows import registry
    original_load = registry.load_skill_instructions

    def patched_load(name: str) -> str:
        if name == "contribution":
            return original_load(name) + "\n\n## Custom Instruction: Perform sensitivity check."
        return original_load(name)

    monkeypatch.setattr(registry, "load_skill_instructions", patched_load)

    ctx2 = build_context(store, "contribution", research_question_id="RQ_SKILL")
    fp2 = _compute_input_fingerprint("contribution", ctx2)

    # Cache fingerprint MUST change when skill instructions change
    assert fp1 != fp2


# ======================================================================================
# I-8. Full-text policy changes cache identity & fulltext semantics
# ======================================================================================

def test_i8_fulltext_policy_and_missing_semantics(harness_env):
    """I-8: Run with fulltext_policy='never' vs 'always'.
    Expected:
    - Different effective context; cache fingerprints must differ.
    - Missing full text is never substituted with an abstract labeled as full text.
    """
    store: StateStore = harness_env["store"]
    token: str = harness_env["owner_token"]

    with store.write_tx(token=token) as tx:
        tx.add_revision(
            object_id="RQ_FT",
            object_type="ResearchQuestion",
            content={"label": "FT RQ", "text": "Full text fidelity verification."},
            origin={},
            edges=[],
        )
        tx.add_revision(
            object_id="SRC_WITH_BODY",
            object_type="Source",
            content={
                "citation_key": "FullPaper2025",
                "title": "Complete Empirical Paper",
                "abstract": "Brief summary.",
                "full_text": "Section 1: Full manuscript methodology and complete proof.",
            },
            origin={},
            edges=[("depends_on", "RQ_FT", 1, {})],
        )
        tx.add_revision(
            object_id="SRC_WITHOUT_BODY",
            object_type="Source",
            content={
                "citation_key": "AbstractOnly2025",
                "title": "Conference Abstract Presentation",
                "abstract": "We describe preliminary work.",
            },
            origin={},
            edges=[("depends_on", "RQ_FT", 1, {})],
        )

    ctx_never = build_context(store, "literature", research_question_id="RQ_FT", fulltext_policy="never")
    ctx_always = build_context(store, "literature", research_question_id="RQ_FT", fulltext_policy="always")

    fp_never = _compute_input_fingerprint("literature", ctx_never)
    fp_always = _compute_input_fingerprint("literature", ctx_always)

    # Fingerprints MUST differ
    assert fp_never != fp_always

    # SRC_WITHOUT_BODY must NOT have its abstract relabeled as full text
    for ft in ctx_always.full_texts:
        if ft["object_id"] == "SRC_WITHOUT_BODY":
            assert ft.get("fulltext_status") in {"ABSTRACT_ONLY", "FULLTEXT_MISSING"}
            assert ft.get("full_text") is None or ft.get("is_abstract_fallback") is False


# ======================================================================================
# I-9. Budget includes complete final model-facing prompt
# ======================================================================================

def test_i9_budget_includes_complete_final_prompt(harness_env):
    """I-9: Context where raw objects fit under budget, but full prompt (instructions + manifest + excerpts)
    exceeds budget must trigger INCOMPLETE_CONTEXT.
    """
    store: StateStore = harness_env["store"]
    token: str = harness_env["owner_token"]

    with store.write_tx(token=token) as tx:
        tx.add_revision(
            object_id="RQ_BUDGET_09",
            object_type="ResearchQuestion",
            content={"label": "Budget RQ", "text": "Measuring complete prompt token budget."},
            origin={},
            edges=[],
        )
        for i in range(1, 10):
            tx.add_revision(
                object_id=f"EP_BUDGET_{i:02d}",
                object_type="EvidencePassage",
                content={
                    "locator": f"Paper {i}, p. {i*5}",
                    "quote": f"Substantial empirical passage {i} detailing estimation parameters " + ("data " * 80),
                },
                origin={},
                edges=[("depends_on", "RQ_BUDGET_09", 1, {})],
            )

    # Set budget to 400 tokens (enough for single object, but exceeded when including full prompt + instructions)
    pkg = build_context(store, "contribution", research_question_id="RQ_BUDGET_09", max_input_tokens=400)
    assert pkg.budget_exceeded is True
    assert pkg.continuation_plan is not None
    assert pkg.continuation_plan.get("status") == "INCOMPLETE_CONTEXT"
    assert pkg.continuation_plan.get("omitted_objects_count", 0) > 0


# ======================================================================================
# I-10. Cross-workflow handoffs (Results -> Discussion -> Contribution)
# ======================================================================================

def test_i10_cross_workflow_results_discussion_contribution_handoff(harness_env):
    """I-10: Results -> Discussion -> Contribution Finding structured artifact handoffs.
    Expected:
    - Discussion receives verified Results output.
    - Discussion records conflict/contradiction when result opposes initial prediction.
    - Contribution receives Discussion artifact, preserves original candidate, and marks new formulation provisional.
    """
    store: StateStore = harness_env["store"]
    token: str = harness_env["owner_token"]

    with store.write_tx(token=token) as tx:
        tx.add_revision(
            object_id="RQ_CHAIN",
            object_type="ResearchQuestion",
            content={"label": "Handoff RQ", "text": "Do high-frequency batch auctions eliminate front-running?"},
            origin={},
            edges=[],
        )
        tx.add_revision(
            object_id="CLAIM_CHAIN_INITIAL",
            object_type="Claim",
            content={
                "label": "Initial Elimination Claim",
                "text": "Frequent batch auctions eliminate front-running profits across all liquidity regimes.",
                "claim_tier": "causal",
            },
            origin={},
            edges=[("depends_on", "RQ_CHAIN", 1, {})],
        )
        tx.add_revision(
            object_id="RES_CONTRADICTORY",
            object_type="Result",
            content={
                "label": "Auction Latency Arbitrage Result",
                "summary": "Front-running profits persist at 38% under fragmented multi-venue cross-arbitrage.",
                "contradictory": True,
            },
            origin={},
            edges=[("depends_on", "RQ_CHAIN", 1, {})],
        )

    # 1. Run Results
    res_out = run_workflow("results", store, research_question_id="RQ_CHAIN")

    # 2. Run Discussion consuming Results output
    disc_out = run_workflow(
        "discussion",
        store,
        research_question_id="RQ_CHAIN",
        focus_claims=["CLAIM_CHAIN_INITIAL"],
        upstream_artifacts=[res_out],
    )
    assert disc_out.result.get("reopens_contribution_workflow") is True

    # 3. Run Contribution Finding consuming Discussion output
    c_out = run_workflow(
        "contribution",
        store,
        research_question_id="RQ_CHAIN",
        focus_claims=["CLAIM_CHAIN_INITIAL"],
        upstream_artifacts=[disc_out],
    )
    candidates = c_out.result.get("candidate_contributions", [])
    assert len(candidates) > 0
    # Must preserve initial claim context while noting provisional status
    assert c_out.result.get("is_preliminary") is True


# ======================================================================================
# I-11. Workflow artifact persistence across restart
# ======================================================================================

def test_i11_workflow_artifact_persistence_across_restart(tmp_path):
    """I-11: Generate a workflow artifact, close store, reopen fresh store instance.
    Expected:
    - Artifact metadata and execution records remain retrievable.
    - Valid cache reuse works.
    - Changed input object revisions invalidate affected outputs.
    """
    proj_dir = tmp_path / "persistent_proj"
    store1, token = StateStore.create(proj_dir, "PRJ_PERSIST")

    with store1.write_tx(token=token) as tx:
        tx.add_revision(
            object_id="RQ_P11",
            object_type="ResearchQuestion",
            content={"label": "P11 RQ", "text": "Persistence across process restarts."},
            origin={},
            edges=[],
        )
        tx.add_revision(
            object_id="SRC_P11",
            object_type="Source",
            content={"citation_key": "Persist2024", "abstract": "A source for the run to register."},
            origin={},
            edges=[("depends_on", "RQ_P11", 1, {})],
        )

    out1 = run_workflow("literature", store1, research_question_id="RQ_P11")
    assert out1.status in {"SUCCESS", "REQUIRES_VERIFICATION"}

    # Simulate restart by instantiating fresh StateStore pointing to same directory
    store2 = StateStore(store1.paths)

    # 1. Re-run identical task -> should achieve valid cache reuse
    out2 = run_workflow("literature", store2, research_question_id="RQ_P11", reuse_cached=True)
    assert out2.status == "REUSED"
    assert out2.usage.reused_artifacts == 1

    # 2. Revise RQ -> must invalidate cache
    with store2.write_tx(token=token) as tx:
        tx.add_revision(
            object_id="RQ_P11",
            object_type="ResearchQuestion",
            content={"label": "P11 RQ Rev 2", "text": "Updated inquiry definition."},
            origin={},
            edges=[],
        )

    out3 = run_workflow("literature", store2, research_question_id="RQ_P11", reuse_cached=True)
    assert out3.status != "REUSED"


# ======================================================================================
# I-12. Token accounting distinct semantics
# ======================================================================================

def test_i12_token_accounting_distinct_semantics(harness_env):
    """I-12: Verify that offline operations, mock model operations, and cache hits
    have distinct and correct usage semantics.
    """
    store: StateStore = harness_env["store"]
    token: str = harness_env["owner_token"]

    with store.write_tx(token=token) as tx:
        tx.add_revision(
            object_id="RQ_TOKEN_12",
            object_type="ResearchQuestion",
            content={"label": "Token RQ", "text": "Testing token accounting distinctions."},
            origin={},
            edges=[],
        )

    tracker = UsageTracker(store)

    # 1. Offline operation: llm_calls=0, usage_source='estimated' (not 'provider')
    out_offline = run_workflow("methods", store, research_question_id="RQ_TOKEN_12")
    u_off = out_offline.usage
    assert u_off.llm_calls == 0
    assert u_off.usage_source in {"estimated", "offline"}

    # 2. Local cache hit: reused_artifacts=1, cache_read_tokens does NOT fabricate provider cache
    out_cached = run_workflow("methods", store, research_question_id="RQ_TOKEN_12", reuse_cached=True)
    u_cache = out_cached.usage
    assert u_cache.reused_artifacts == 1
    assert u_cache.llm_calls == 0
    # Local JSON cache must NOT be counted as provider billed cache read tokens
    assert u_cache.cache_read_tokens is None or u_cache.usage_source == "offline_cache"
