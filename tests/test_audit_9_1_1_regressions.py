"""Audit regressions for Research Suite 9.1.1 (plan v3, Phase 0.1).

Each test asserts the *correct* behaviour that the independent audit found missing at commit 9728134.
Every test is a strict xfail naming its defect ID. The PR that fixes the defect removes the marker, so a
fix that does not actually work keeps failing, and an unexpected pass (XPASS) fails the suite.

Audit evidence: .tmp/audit_9_1_1/ (repro.py, RESEARCH_SUITE_9_1_1_INDEPENDENT_AUDIT.md).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from research_harness.state.store import StateStore
from research_harness.workflows import registry as reg_mod
from research_harness.workflows.adapters import ScriptedModelAdapter
from research_harness.workflows.context import build_context
from research_harness.workflows.dispatcher import (
    _compute_input_fingerprint,
    run_workflow,
    run_workflow_sequence,
)
from research_harness.workflows.instrumentation import estimate_tokens

WORKFLOWS = ["contribution", "literature", "methods", "results", "discussion"]


def xfail(defect: str):
    return pytest.mark.xfail(strict=True, reason=f"audit {defect}: not yet fixed in refactor/9.2")


# --------------------------------------------------------------------------------------------------
# fixture helpers
# --------------------------------------------------------------------------------------------------

@pytest.fixture
def proj(tmp_path):
    store, token = StateStore.create(tmp_path / "home", "PRJ_AUDIT")
    return store, token


def add(proj, *objs):
    store, token = proj
    with store.write_tx(token=token) as tx:
        for oid, otype, content, edges in objs:
            tx.add_revision(object_id=oid, object_type=otype, content=content,
                            origin={"kind": "audit_fixture"}, edges=list(edges))


def rq(oid="RQ_TEST", text="Does board independence reduce financial restatements?"):
    return (oid, "ResearchQuestion", {"label": oid, "text": text}, [])


def dep(target, rev=1):
    return ("depends_on", target, rev, {})


def run(proj, wf, **kw):
    store, _ = proj
    kw.setdefault("research_question_id", "RQ_TEST")
    kw.setdefault("reuse_cached", False)
    return run_workflow(wf, store, **kw)


def seed_all(proj):
    add(proj,
        rq(),
        ("SRC_A", "Source", {"citation_key": "Klein2002", "abstract": "Audit committee independence.",
                             "full_text": "Section 4. We find a negative association."}, [dep("RQ_TEST")]),
        ("EP_A", "EvidencePassage", {"locator": "Klein2002 p.389",
                                      "quote": "Board independence is negatively related to abnormal accruals."},
         [("derived_from", "SRC_A", 1, {}), dep("RQ_TEST")]),
        ("DS_A", "Dataset", {"label": "Compustat 2000-2020"}, [dep("RQ_TEST")]),
        ("AN_A", "Analysis", {"label": "OLS", "estimator": None}, [dep("DS_A")]),
        ("RES_A", "Result", {"summary": "Coefficient on independence is -0.12 (p<0.01)."}, [("derived_from", "AN_A", 1, {})]),
        ("CLAIM_A", "Claim", {"text": "Board independence reduces restatements.", "claim_tier": "causal"},
         [dep("RQ_TEST"), dep("RES_A"), ("supports", "EP_A", 1, {})]))


def core_references(store, needle):
    """Number of Research Core rows (any table, any column) that mention `needle`."""
    hits = 0
    with store.read() as c:
        for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
            for col in [r[1] for r in c.execute(f"PRAGMA table_info({t})")]:
                hits += c.execute(f"SELECT COUNT(*) FROM {t} WHERE CAST({col} AS TEXT) LIKE ?", (f"%{needle}%",)).fetchone()[0]
    return hits


# ==================================================================================================
# B — model-backed execution (P0-1, P0-2, P1-3, P1-7)
# ==================================================================================================

@pytest.mark.parametrize("wf", WORKFLOWS)
def test_b1_scripted_adapter_reaches_model_through_run_workflow(proj, wf):
    seed_all(proj)
    adapter = ScriptedModelAdapter(default_response={"assessment": "REQUIRES_VERIFICATION", "evidence_refs": []})
    out = run(proj, wf, model_adapter=adapter)
    assert len(adapter.recorded_requests) == 1
    assert isinstance(out.result, dict)


def test_b2_adapter_reported_usage_is_recorded_verbatim(proj):
    seed_all(proj)
    adapter = ScriptedModelAdapter(default_response={"x": 1}, input_token_cost=777, output_token_cost=11)
    out = run(proj, "contribution", model_adapter=adapter)
    assert (out.usage.input_tokens, out.usage.output_tokens) == (777, 11)


def test_b3c_output_schema_is_sent_to_the_model(proj):
    seed_all(proj)
    adapter = ScriptedModelAdapter(default_response={"x": 1})
    run(proj, "contribution", model_adapter=adapter)
    assert adapter.recorded_requests[0].expected_schema is not None


def test_b3e_schema_less_model_output_is_not_a_complete_assessment(proj):
    seed_all(proj)
    adapter = ScriptedModelAdapter(default_response={"assessment": "REQUIRES_VERIFICATION", "evidence_refs": []})
    out = run(proj, "contribution", model_adapter=adapter)
    assert out.status != "SUCCESS"


def test_b3g_over_budget_request_is_not_sent_to_the_model(proj):
    seed_all(proj)
    adapter = ScriptedModelAdapter(default_response={"x": 1})
    out = run(proj, "contribution", model_adapter=adapter, max_input_tokens=200)
    assert adapter.recorded_requests == []
    assert out.status != "SUCCESS"


def test_p13_zero_llm_calls_never_reported_as_provider_usage(proj, monkeypatch):
    """With an exact tokenizer (tiktoken) the executors labelled offline runs 'provider'."""
    from research_harness.workflows import context as ctx_mod
    monkeypatch.setattr(ctx_mod, "estimate_tokens", lambda text: (max(1, len(text) // 4), False))
    seed_all(proj)
    out = run(proj, "methods")
    assert out.usage.llm_calls == 0
    assert out.usage.usage_source != "provider"


# ==================================================================================================
# C1 — Contribution
# ==================================================================================================

@xfail("P0-3 (C1b)")
def test_c1b_adjudication_saying_identification_is_not_credible_does_not_verify_it(proj):
    add(proj, rq(), ("CLAIM_C", "Claim", {"text": "Independence causes fewer restatements.", "claim_tier": "causal"}, [dep("RQ_TEST")]),
        ("ADJ_1", "Adjudication", {"subject": {"object_id": "CLAIM_C"}, "verdict": "SUPPORTED",
                                    "rationale": "Association supported; identification is NOT credible (no exogenous shock)."}, []))
    res = run(proj, "contribution", focus_claims=["CLAIM_C"]).result
    assert res["identification_status"] != "IDENTIFIED_BY_VERIFIED_DESIGN"


@xfail("P0-4 (C1c)")
def test_c1c_full_text_competitor_linked_by_challenges_is_surfaced(proj):
    add(proj, rq(),
        ("SRC_COMP", "Source", {"citation_key": "Prior2019", "abstract": "Shows independence reduces restatements.",
                                "full_text": "We show that board independence reduces restatements."}, []),
        ("CLAIM_N", "Claim", {"text": "First study to show independence reduces restatements.", "claim_tier": "conceptual"},
         [dep("RQ_TEST"), ("challenges", "SRC_COMP", 1, {})]))
    blob = json.dumps(run(proj, "contribution", focus_claims=["CLAIM_N"], fulltext_policy="always").result)
    assert "Prior2019" in blob or "SRC_COMP" in blob


@xfail("P0-3 (C1d)")
def test_c1d_passage_linked_by_challenges_is_not_counted_as_supporting(proj):
    add(proj, rq(),
        ("EP_COMP", "EvidencePassage", {"locator": "Prior2019 p.12",
                                         "quote": "We already document that board independence reduces restatements."}, []),
        ("CLAIM_N", "Claim", {"text": "First study to show independence reduces restatements.", "claim_tier": "conceptual"},
         [dep("RQ_TEST"), ("challenges", "EP_COMP", 1, {})]))
    res = run(proj, "contribution", focus_claims=["CLAIM_N"]).result
    assert "EP_COMP" not in [e.get("passage_id") for e in res["supporting_evidence"]]


@xfail("P0-3 (C1f)")
def test_c1f_all_competing_mechanisms_are_surfaced(proj):
    add(proj, rq(),
        ("MECH_MON", "Mechanism", {"label": "Monitoring"}, []),
        ("MECH_SEL", "Mechanism", {"label": "Selection of better firms"}, []),
        ("CLAIM_M", "Claim", {"text": "Independence reduces restatements.", "claim_tier": "associational"},
         [dep("RQ_TEST"), dep("MECH_MON"), dep("MECH_SEL")]))
    blob = json.dumps(run(proj, "contribution", focus_claims=["CLAIM_M"]).result)
    assert "Monitoring" in blob and "Selection of better firms" in blob


@xfail("P0-5 (C1g)")
def test_c1g_researchers_own_sibling_hypothesis_is_not_a_literature_competitor(proj):
    add(proj, rq(),
        ("CLAIM_H1", "Claim", {"text": "H1: independence reduces restatements.", "claim_tier": "associational"}, [dep("RQ_TEST")]),
        ("CLAIM_H2", "Claim", {"text": "H2: the effect is stronger for small firms.", "claim_tier": "associational"}, [dep("RQ_TEST")]))
    res = run(proj, "contribution", focus_claims=["CLAIM_H1"]).result
    assert "CLAIM_H2" not in [c.get("object_id") for c in res["closest_competing_contributions"]]


# ==================================================================================================
# C2 — Literature
# ==================================================================================================

def _lit_corpus(proj):
    add(proj, rq(),
        ("SRC_3", "Source", {"citation_key": "Gamma2022", "abstract": "c"}, [dep("RQ_TEST")]),
        ("SRC_4", "Source", {"citation_key": "Delta2023", "abstract": "d",
                             "full_text": "7. Limitations: because treatment was self-selected our estimates likely overstate "
                                          "the effect; we cannot exclude that the association reverses with firm fixed effects."},
         [dep("RQ_TEST")]),
        ("EP_3", "EvidencePassage", {"locator": "Gamma2022 p.3",
                                      "quote": "Our estimates are not consistent with the monitoring hypothesis and do not support prior findings."},
         [("derived_from", "SRC_3", 1, {})]),
        ("EP_4", "EvidencePassage", {"locator": "Delta2023 p.4",
                                      "quote": "Board independence is associated with fewer restatements in our panel."},
         [("derived_from", "SRC_4", 1, {})]))


@xfail("P0-3 (C2b)")
def test_c2b_negated_consistency_is_not_support(proj):
    _lit_corpus(proj)
    res = run(proj, "literature", fulltext_policy="always").result
    rel = {r["passage_id"]: r["relationship"] for r in res["evidence_matrix"]}
    assert rel["EP_3"] != "supports_prior"


@xfail("P1-6 (C2c)")
def test_c2c_limitation_in_loaded_full_text_is_surfaced(proj):
    _lit_corpus(proj)
    blob = json.dumps(run(proj, "literature", fulltext_policy="always").result)
    assert "self-selected" in blob or "reverses" in blob or "Limitations" in blob


@xfail("P1-6 (C2f)")
def test_c2f_literature_without_any_evidence_is_not_success(proj):
    add(proj, rq())
    assert run(proj, "literature").status != "SUCCESS"


# ==================================================================================================
# C3 — Methods
# ==================================================================================================

_METHODS_CASES = {
    "cross_sectional": ("Is independence associated with restatements in 2019?", "associational"),
    "conceptual": ("How should earnings quality be conceptualized?", "conceptual"),
    "qualitative_education": ("How do novice teachers experience formative assessment?", "descriptive"),
}


@xfail("P0-7 (C3)")
@pytest.mark.parametrize("name", sorted(_METHODS_CASES))
def test_c3_methods_do_not_impose_econometric_defaults(proj, name):
    q, tier = _METHODS_CASES[name]
    add(proj, rq(text=q), ("CLAIM_1", "Claim", {"text": q, "claim_tier": tier}, [dep("RQ_TEST")]))
    blob = json.dumps(run(proj, "methods").result)
    for forbidden in ("Two-way clustered", "Entity/Firm FE", "Leverage", "Panel Regression with Multi-Way Fixed Effects"):
        assert forbidden not in blob, forbidden


@xfail("P0-7 (C3-competing)")
def test_c3_researcher_proposed_methods_are_evaluated(proj):
    add(proj, rq(), ("CLAIM_1", "Claim", {"text": "x", "claim_tier": "causal"}, [dep("RQ_TEST")]))
    res = run(proj, "methods", candidate_methods=["regression discontinuity", "synthetic control"]).result
    names = " ".join(d["design_name"].lower() for d in res["candidate_research_designs"])
    assert "discontinuity" in names and "synthetic control" in names


# ==================================================================================================
# C4 — Results
# ==================================================================================================

@xfail("P1-8 (C4b)")
def test_c4b_author_typed_summary_is_not_listed_as_supported_by_outputs(proj):
    add(proj, rq(), ("RES_U", "Result", {"summary": "Author stated beta = 0.35, t = 2.45 in draft text."}, [dep("RQ_TEST")]))
    assert run(proj, "results").result["findings_supported_by_available_outputs"] == []


@xfail("P0-8 (C4c)")
def test_c4c_caller_flag_alone_cannot_verify_a_computation(proj):
    add(proj, rq(), ("DS_1", "Dataset", {"label": "anything"}, [dep("RQ_TEST")]),
        ("RES_U", "Result", {"summary": "Author stated beta = 0.35."}, [dep("DS_1")]))
    out = run(proj, "results", run_metadata={"verified_computation": True})
    assert out.result["reproducibility_metadata"]["reproducibility_status"] != "VERIFIED_COMPUTATIONAL_RUN"


# ==================================================================================================
# C5 — Discussion
# ==================================================================================================

def _discussion(proj, summary):
    add(proj, rq(), ("RES_1", "Result", {"summary": summary}, [dep("RQ_TEST")]),
        ("CLAIM_1", "Claim", {"text": "H1: board independence reduces restatements.", "claim_tier": "associational"},
         [dep("RQ_TEST"), dep("RES_1")]))
    return run(proj, "discussion", focus_claims=["CLAIM_1"]).result


@xfail("P0-3 (C5-1)")
def test_c5_negative_coefficient_that_the_theory_predicts_does_not_reopen_contribution(proj):
    res = _discussion(proj, "Independence has a negative and significant coefficient on restatements (beta=-0.12, p<0.01), as H1 predicts.")
    assert res["reopens_contribution_workflow"] is False


@xfail("P0-3 (C5-2)")
def test_c5_nonsignificant_result_is_not_falsification(proj):
    res = _discussion(proj, "The coefficient on independence is not significant (beta=0.01, p=0.41).")
    assert res["reopens_contribution_workflow"] is False
    assert "overturned" not in json.dumps(res["supported_interpretations"])


@xfail("P0-3 (C5-3)")
def test_c5_the_word_regression_is_not_a_contradiction(proj):
    res = _discussion(proj, "An OLS regression with industry controls shows independence is associated with fewer restatements.")
    assert res["reopens_contribution_workflow"] is False


@xfail("P0-3 (C5-5)")
def test_c5_result_without_trigger_words_is_not_declared_corroborated(proj):
    res = _discussion(proj, "Firms adding independent directors increased restatements by 12% relative to controls.")
    assert "Empirically corroborated" not in json.dumps(res["revised_contribution_statements"])


@xfail("P2-4 (C5-4)")
def test_c5_planned_but_unexecuted_robustness_is_never_addressed(proj):
    add(proj, rq(),
        ("RES_1", "Result", {"summary": "Independence associated with fewer restatements."}, [dep("RQ_TEST")]),
        ("CLAIM_1", "Claim", {"text": "H1", "claim_tier": "associational"}, [dep("RQ_TEST"), dep("RES_1")]))
    ctx = build_context(proj[0], "discussion", research_question_id="RQ_TEST", focus_claims=["CLAIM_1"])
    ctx.objects.append({"object_id": "AN_PLAN", "object_type": "Analysis", "revision": 1,
                        "content": {"description": "We plan to add lagged regressors and year FE in a future revision.",
                                    "status": "PLANNED_NOT_RUN"}})
    from research_harness.workflows.skills.discussion import execute_discussion_workflow
    res = execute_discussion_workflow(ctx).result
    assert "ADDRESSED_BY_SPECIFICATION" not in json.dumps(res["alternative_explanations"])


# ==================================================================================================
# D — context and budget
# ==================================================================================================

@xfail("D2")
def test_d2_discussion_without_any_result_is_not_success(proj):
    add(proj, rq(), ("CLAIM_1", "Claim", {"text": "H1", "claim_tier": "associational"}, [dep("RQ_TEST")]))
    out = run(proj, "discussion", focus_claims=["CLAIM_1"])
    assert out.status != "SUCCESS" and "corroborated" not in json.dumps(out.result)


def _passages(proj, n=6):
    add(proj, rq(), *[(f"EP_{i}", "EvidencePassage",
                       {"locator": f"p.{i}", "quote": f"Passage {i} " + "evidence " * 60}, [dep("RQ_TEST")]) for i in range(1, n + 1)])


@pytest.mark.parametrize("budget", [900, 1300])
def test_d3_assembled_prompt_fits_the_budget(proj, budget):
    _passages(proj)
    ctx = build_context(proj[0], "contribution", research_question_id="RQ_TEST", max_input_tokens=budget)
    assert estimate_tokens(ctx.formatted_prompt)[0] <= budget


def test_d3b_large_full_text_never_drops_the_whole_source(proj):
    add(proj, rq(),
        ("SRC_BIG", "Source", {"citation_key": "Big2020", "abstract": "Important competitor.", "full_text": "Lorem ipsum. " * 20000},
         [dep("RQ_TEST")]),
        ("EP_BIG", "EvidencePassage", {"locator": "Big2020 p.2", "quote": "Key competitor finding."}, [("derived_from", "SRC_BIG", 1, {})]))
    ctx = build_context(proj[0], "literature", research_question_id="RQ_TEST", fulltext_policy="always", max_input_tokens=12000)
    assert "SRC_BIG" in [o["object_id"] for o in ctx.objects]


def test_a4_evidence_text_appears_once_in_the_prompt(proj):
    _passages(proj, 2)
    ctx = build_context(proj[0], "contribution", research_question_id="RQ_TEST", max_input_tokens=100000)
    assert ctx.formatted_prompt.count("Passage 1 evidence") == 1


def test_d4b_contribution_sees_rq_linked_sources(proj):
    add(proj, rq(),
        ("SRC_F", "Source", {"citation_key": "Full2021", "abstract": "abs", "full_text": "FULLBODY " * 50}, [dep("RQ_TEST")]))
    ctx = build_context(proj[0], "contribution", research_question_id="RQ_TEST", fulltext_policy="always")
    assert [o for o in ctx.objects if o["object_type"] == "Source"]


def test_d5b_explicit_research_question_scopes_claims(proj):
    add(proj, rq("RQ_A", "Board independence and restatements"), rq("RQ_B", "Teacher feedback and student motivation"),
        ("CLAIM_B", "Claim", {"text": "Feedback raises motivation.", "claim_tier": "associational"}, [dep("RQ_B")]),
        ("CLAIM_Z", "Claim", {"text": "Independence reduces restatements.", "claim_tier": "associational"}, [dep("RQ_A")]))
    ctx = build_context(proj[0], "contribution", research_question_id="RQ_A")
    assert "CLAIM_B" not in [o["object_id"] for o in ctx.claims]
    assert run(proj, "contribution", research_question_id="RQ_A").result["candidate_contributions"][0]["claim_id"] == "CLAIM_Z"


# ==================================================================================================
# E — cache
# ==================================================================================================

def test_e_incomplete_assessment_is_not_reused_as_a_complete_one(proj):
    seed_all(proj)
    first = run(proj, "contribution", max_input_tokens=150, reuse_cached=True)
    second = run(proj, "contribution", max_input_tokens=150, reuse_cached=True)
    assert first.status == "INCOMPLETE_CONTEXT"
    assert second.status == "INCOMPLETE_CONTEXT"


@xfail("P1-4 (E1)")
def test_e1_skill_edit_on_disk_invalidates_cache_in_the_same_process(proj, tmp_path, monkeypatch):
    seed_all(proj)
    skills = tmp_path / "skills"
    shutil.copytree(Path(reg_mod.__file__).parent / "skills", skills, ignore=shutil.ignore_patterns("*.py", "__pycache__"))
    monkeypatch.setattr(reg_mod, "_DEFAULT_REGISTRY", reg_mod.WorkflowRegistry(skills_dir=skills))
    run(proj, "methods", reuse_cached=True)
    p = skills / "methods" / "SKILL.md"
    p.write_text(p.read_text(encoding="utf-8") + "\n\n## NEW RULE: never propose fixed effects for qualitative data.\n", encoding="utf-8")
    assert run(proj, "methods", reuse_cached=True).status != "REUSED"


@xfail("P1-4 (E4)")
def test_e4_budget_value_alone_does_not_invalidate_when_selection_is_identical(proj):
    seed_all(proj)
    run(proj, "literature", reuse_cached=True)
    assert run(proj, "literature", reuse_cached=True, max_input_tokens=12001).status == "REUSED"


def test_e_unrelated_claim_under_another_question_does_not_invalidate_any_workflow(proj):
    seed_all(proj)
    for wf in WORKFLOWS:
        run(proj, wf, reuse_cached=True)
    add(proj, rq("RQ_OTHER", "Unrelated education question"),
        ("CLAIM_OTHER", "Claim", {"text": "Unrelated.", "claim_tier": "descriptive"}, [dep("RQ_OTHER")]))
    assert all(run(proj, wf, reuse_cached=True).status == "REUSED" for wf in WORKFLOWS)


def test_e6_new_contradictory_passage_on_an_rq_linked_source_invalidates_contribution(proj):
    seed_all(proj)
    run(proj, "contribution", reuse_cached=True)
    add(proj, ("EP_CONTRA", "EvidencePassage", {"locator": "Klein2002 p.400", "quote": "In later years the relation reverses sign."},
               [("derived_from", "SRC_A", 1, {})]))
    assert run(proj, "contribution", reuse_cached=True).status != "REUSED"


def test_e9_model_identity_is_a_stable_part_of_the_fingerprint(proj):
    seed_all(proj)
    ctx = build_context(proj[0], "contribution", research_question_id="RQ_TEST")
    a1, a2 = ScriptedModelAdapter(model_id="m-1"), ScriptedModelAdapter(model_id="m-1")
    assert _compute_input_fingerprint("contribution", ctx, {"model_adapter": a1}) == \
        _compute_input_fingerprint("contribution", ctx, {"model_adapter": a2})
    fp_before = _compute_input_fingerprint("contribution", ctx, {"model_adapter": a1})
    a1.model_id = "m-2"
    assert _compute_input_fingerprint("contribution", ctx, {"model_adapter": a1}) != fp_before


@xfail("P1-4 (E10)")
def test_e10_identical_handoff_sequence_is_fully_reused(proj):
    seed_all(proj)
    store, _ = proj
    run_workflow_sequence(store, ["results", "discussion", "contribution"], research_question_id="RQ_TEST")
    again = run_workflow_sequence(store, ["results", "discussion", "contribution"], research_question_id="RQ_TEST")
    assert all(o.status == "REUSED" for o in again)


# ==================================================================================================
# F — persistence in the Research Core
# ==================================================================================================

@xfail("P1-1 (F1)")
def test_f1_research_core_has_a_workflow_run_registry(proj):
    store, _ = proj
    with store.read() as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "workflow_runs" in tables


@xfail("P1-1 (F2)")
def test_f2_workflow_output_is_discoverable_in_the_core_without_cache_files(proj):
    seed_all(proj)
    store, _ = proj
    out = run(proj, "discussion")
    sha = out.metadata["artifact_sha256"]
    shutil.rmtree(store.paths.root / "workflow_cache")
    reopened = StateStore(store.paths)
    assert core_references(reopened, sha) > 0
    assert sha not in reopened.recover()["orphan_artifacts"]


@xfail("P1-1 (F3)")
def test_f3_identical_request_is_reused_after_cache_files_are_deleted(proj):
    seed_all(proj)
    store, _ = proj
    run(proj, "discussion", reuse_cached=True)
    shutil.rmtree(store.paths.root / "workflow_cache")
    assert run(proj, "discussion", reuse_cached=True).status == "REUSED"


# ==================================================================================================
# G — benchmark honesty
# ==================================================================================================

def test_g3_benchmark_required_competitor_source_is_retained(tmp_path):
    from tests.benchmark_fixture import populate_benchmark_project
    store, token = StateStore.create(tmp_path / "bench", "PRJ_BENCH")
    populate_benchmark_project(store, token)
    with store.write_tx(token=token) as tx:
        tx.add_revision(object_id="SRC_COMPETITOR_FULL", object_type="Source",
                        content={"citation_key": "PriorAgent2023", "abstract": "a", "full_text": "Complete disclosure."},
                        origin={}, edges=[("depends_on", "RQ_BENCHMARK_01", 1, {})])
        tx.add_revision(object_id="CLAIM_CASE_2_NOVELTY", object_type="Claim",
                        content={"text": "First architecture to offload context.", "claim_tier": "conceptual"},
                        origin={}, edges=[("depends_on", "RQ_BENCHMARK_01", 1, {}), ("challenges", "SRC_COMPETITOR_FULL", 1, {})])
    pkg = build_context(store, "contribution", research_question_id="RQ_BENCHMARK_01",
                        focus_claims=["CLAIM_01", "CLAIM_CASE_2_NOVELTY"], fulltext_policy="always")
    assert "SRC_COMPETITOR_FULL" in {o["object_id"] for o in pkg.objects}


@xfail("P1-5 (hard-coded metrics)")
def test_g_benchmark_metrics_are_computed_not_literals():
    import inspect
    from tests import benchmark_fixture
    src = inspect.getsource(benchmark_fixture.run_scientific_benchmark_evaluation)
    assert '"provenance_correctness_pct": 100.0' not in src
    assert '"fulltext_inspection_status": "VERIFIED_ON_DEMAND"' not in src
    assert '"cases_evaluated": 8' not in src
