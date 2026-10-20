"""Phase 0 (plan v3): four-axis status model, shared taxonomy, provenance envelope."""
from __future__ import annotations

import itertools

import pytest

from research_harness import taxonomy
from research_harness.workflows.contracts import WorkflowOutput, TaskUsage
from research_harness.workflows.provenance import Basis, MissingEvidenceBasis, Statement, require_evidence_basis
from research_harness.workflows.status import (
    Applicability,
    AssessmentStatus as A,
    ContextStatus as C,
    ExecutionStatus as E,
    Status,
    apply_caps,
    axes_from_legacy,
    normalize_status,
)

V, P, N, X = A.VERIFIED_WITHIN_SCOPE, A.PROVISIONAL, A.NOT_ASSESSED, A.CONTESTED


# --------------------------------------------------------------------------------------------------
# apply_caps: every rule of plan v3 section 4.2
# --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("execution", [E.FAILED, E.NOT_RUN, E.BLOCKED])
def test_rule1_execution_not_succeeded_means_not_assessed(execution):
    assert apply_caps(V, execution=execution, researcher_confirmed=True) is N


def test_rule1_not_applicable_means_not_assessed():
    assert apply_caps(V, applicability=Applicability.NOT_APPLICABLE, researcher_confirmed=True) is N


def test_rule2_insufficient_context_means_not_assessed():
    assert apply_caps(V, context=C.INSUFFICIENT, researcher_confirmed=True) is N


def test_rule3_counter_evidence_or_eca_not_supported_is_contested():
    assert apply_caps(P, counter_evidence=True) is X
    assert apply_caps(V, eca_verdict="NOT_SUPPORTED", researcher_confirmed=True) is X
    assert apply_caps(X) is X


def test_rule3_does_not_override_failed_execution():
    assert apply_caps(P, execution=E.FAILED, counter_evidence=True) is N


def test_rule4_nothing_requested_stays_not_assessed():
    assert apply_caps(N) is N


def test_rule5_partial_context_caps_at_provisional():
    assert apply_caps(V, context=C.PARTIAL, researcher_confirmed=True) is P


def test_rule6_verified_needs_researcher_confirmation():
    assert apply_caps(V, researcher_confirmed=False) is P
    assert apply_caps(V, researcher_confirmed=True) is V


def test_rule7_model_generated_needs_supporting_eca_for_verified():
    base = dict(generated_by="model:scripted-1", researcher_confirmed=True)
    assert apply_caps(V, **base) is P
    assert apply_caps(V, eca_verdict="UNRESOLVED", **base) is P
    assert apply_caps(V, eca_verdict="SUPPORTED_AS_STATED", **base) is V
    assert apply_caps(V, eca_verdict="SUPPORTED_IF_QUALIFIED", **base) is V


def test_rule7_researcher_generated_does_not_need_eca():
    assert apply_caps(V, generated_by="researcher", researcher_confirmed=True) is V


def test_rule7_unresolved_eca_caps_even_researcher_generated():
    assert apply_caps(V, generated_by="researcher", eca_verdict="UNRESOLVED", researcher_confirmed=True) is P


def test_rule8_downstream_never_exceeds_upstream():
    assert apply_caps(V, researcher_confirmed=True, upstream=[P]) is P
    assert apply_caps(V, researcher_confirmed=True, upstream=[V, P]) is P
    assert apply_caps(V, researcher_confirmed=True, upstream=[N]) is N
    assert apply_caps(V, researcher_confirmed=True, upstream=[V]) is V


def test_rule8_contested_upstream_caps_at_provisional():
    assert apply_caps(V, researcher_confirmed=True, upstream=[X]) is P
    assert apply_caps(P, upstream=[X]) is P


def test_apply_caps_exhaustive_never_verified_without_confirmation():
    """Property: across every combination of inputs, VERIFIED_WITHIN_SCOPE requires researcher confirmation,
    a succeeded execution, applicable step, non-insufficient context and no counter-evidence."""
    for (req, ex, cx, ap, gen, eca, ce, conf, up) in itertools.product(
        list(A), list(E), list(C), list(Applicability),
        [None, "researcher", "model:m"], [None, "SUPPORTED_AS_STATED", "UNRESOLVED", "NOT_SUPPORTED"],
        [False, True], [False, True], [(), (P,), (X,), (N,)],
    ):
        out = apply_caps(req, execution=ex, context=cx, applicability=ap, generated_by=gen, eca_verdict=eca,
                         counter_evidence=ce, researcher_confirmed=conf, upstream=up)
        if out is V:
            assert conf
            assert ex is E.SUCCEEDED and ap is not Applicability.NOT_APPLICABLE
            assert cx is C.COMPLETE
            assert not ce and eca != "NOT_SUPPORTED"
            assert all(u is V for u in up)


def test_execution_success_does_not_imply_verified():
    """A successful run, with complete context and a requested VERIFIED, is only PROVISIONAL, and the
    legacy 'SUCCESS' status maps to NOT_ASSESSED, never to a verified conclusion."""
    assert apply_caps(V, execution=E.SUCCEEDED, context=C.COMPLETE) is P
    for legacy in ("SUCCESS", "REUSED", "REQUIRES_VERIFICATION", "INCOMPLETE_CONTEXT"):
        st = axes_from_legacy(legacy)
        assert st.assessment is not V, legacy
    assert axes_from_legacy("SUCCESS").assessment is N


# --------------------------------------------------------------------------------------------------
# normalize_status / legacy adapter / serialization
# --------------------------------------------------------------------------------------------------

def test_normalize_not_applicable_is_not_run():
    st = normalize_status(Status(E.SUCCEEDED, C.COMPLETE, P, Applicability.NOT_APPLICABLE, scope={"why": "conceptual"}))
    assert st.execution is E.NOT_RUN and st.reason == "not_applicable" and st.assessment is N
    assert st.scope == {"why": "conceptual"}


def test_normalize_failed_execution_clears_assessment():
    st = normalize_status(Status(E.FAILED, C.COMPLETE, P, Applicability.APPLICABLE, reason="exception"))
    assert st.assessment is N


def test_normalize_leaves_consistent_status_untouched():
    st = Status(E.SUCCEEDED, C.PARTIAL, P, Applicability.APPLICABLE, omissions=("EP_9",))
    assert normalize_status(st) == st


def test_status_roundtrip_dict():
    st = Status(E.SUCCEEDED, C.PARTIAL, X, Applicability.UNKNOWN, reason="r", scope={"k": 1}, omissions=("a", "b"))
    assert Status.from_dict(st.to_dict()) == st


def test_legacy_mapping_table():
    assert axes_from_legacy("REQUIRES_VERIFICATION").assessment is P
    assert axes_from_legacy("INCOMPLETE_CONTEXT").context is C.PARTIAL
    assert axes_from_legacy("FAILED").execution is E.FAILED
    assert axes_from_legacy("BLOCKED").execution is E.BLOCKED
    with pytest.raises(ValueError):
        axes_from_legacy("CONTEXT_PREPARED")


def test_workflow_output_exposes_axes():
    out = WorkflowOutput(workflow="methods", task_id="T", project_id="P", status="REQUIRES_VERIFICATION", result={},
                         usage=TaskUsage(workflow="methods", task_id="T"))
    assert out.axes.execution is E.SUCCEEDED and out.axes.assessment is P


# --------------------------------------------------------------------------------------------------
# taxonomy
# --------------------------------------------------------------------------------------------------

def test_taxonomy_loads_expected_vocabularies():
    names = set(taxonomy.vocabulary_names())
    for n in ("study_design", "generated_by", "evidence_basis_kind", "journal_policy_type", "scope_dimension",
              "claim_change", "claim_effect", "eca_equivalence", "eca_verdict", "diagnostic_signal",
              "source_fidelity", "computational_reproducibility", "methodological_validity", "knowledge_lifecycle",
              "intro_component_status"):
        assert n in names, n


def test_taxonomy_seed_values_from_plan():
    assert "predictive_modeling" in taxonomy.vocabulary("study_design")
    assert taxonomy.hierarchy("domain")["accounting"] == (
        "archival_financial_reporting", "auditing", "management_accounting", "accounting_information_systems")
    assert taxonomy.vocabulary("journal_policy_type") == (
        "official_requirement", "reporting_standard", "published_precedent", "editorial_feedback")


def test_taxonomy_validate_term_and_hint():
    assert taxonomy.validate_term("generated_by", "model") == "model"
    with pytest.raises(taxonomy.TaxonomyError, match="Extend research_harness/taxonomy"):
        taxonomy.validate_term("generated_by", "oracle")
    with pytest.raises(taxonomy.TaxonomyError, match="Unknown vocabulary"):
        taxonomy.validate_term("nope", "x")


def test_taxonomy_domain_subfield_consistency():
    assert taxonomy.validate_domain("accounting", "auditing") == ("accounting", "auditing")
    with pytest.raises(taxonomy.TaxonomyError):
        taxonomy.validate_domain("accounting", "asset_pricing")
    with pytest.raises(taxonomy.TaxonomyError):
        taxonomy.validate_domain("astrology")


def test_taxonomy_rejects_duplicate_definitions(tmp_path, monkeypatch):
    (tmp_path / "a.json").write_text('{"vocabularies": {"v": ["x"]}}', encoding="utf-8")
    (tmp_path / "b.json").write_text('{"vocabularies": {"v": ["y"]}}', encoding="utf-8")
    monkeypatch.setattr(taxonomy, "_DIR", tmp_path)
    taxonomy.reload()
    try:
        with pytest.raises(taxonomy.TaxonomyError, match="defined twice"):
            taxonomy.vocabulary("v")
    finally:
        monkeypatch.undo()
        taxonomy.reload()


def test_taxonomy_files_are_in_package_data():
    tomllib = pytest.importorskip("tomllib")  # stdlib from Python 3.11
    from pathlib import Path
    cfg = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    assert "taxonomy/*.json" in cfg["tool"]["setuptools"]["package-data"]["research_harness"]


# --------------------------------------------------------------------------------------------------
# provenance envelope: generator is not evidence
# --------------------------------------------------------------------------------------------------

def test_generator_and_basis_are_separate_fields():
    s = Statement(text="X holds only on balanced panels", generated_by="model:scripted-1",
                  evidence_basis=[Basis(kind="source_passage", ref="EP_1@1", char_start=10, char_end=40, quote_verified=True)])
    d = s.to_dict()
    assert d["generated_by"] == "model:scripted-1"
    assert d["evidence_basis"][0]["kind"] == "source_passage"
    assert "model" not in {b["kind"] for b in d["evidence_basis"]}
    assert taxonomy.is_term("generated_by", "model") and not taxonomy.is_term("evidence_basis_kind", "model")


def test_model_generated_statement_capped_at_provisional_even_with_strong_basis():
    basis = [Basis(kind="source_passage", ref="EP_1@1", quote_verified=True)]
    s = Statement(text="t", generated_by="model:m", evidence_basis=basis,
                  requested_assessment=V, researcher_confirmed=True)
    assert s.assessment is P
    s.eca_verdict = "SUPPORTED_AS_STATED"
    assert s.assessment is V


def test_statement_without_basis_is_not_assessed():
    assert Statement(text="t", generated_by="researcher").assessment is N


def test_require_evidence_basis_enforces_i9():
    with pytest.raises(MissingEvidenceBasis):
        require_evidence_basis(Statement(text="use clustered SE", generated_by="model:m"), what="playbook step")
    ok = Statement(text="use clustered SE", generated_by="model:m",
                   evidence_basis=[Basis(kind="researcher_judgement", ref="owner")])
    assert require_evidence_basis(ok) is ok


def test_basis_validation():
    with pytest.raises(taxonomy.TaxonomyError):
        Basis(kind="model_suggestion", ref="x")  # v2 conflated generator with evidence; no longer a basis kind
    with pytest.raises(ValueError):
        Basis(kind="source_passage", ref="")
    with pytest.raises(ValueError):
        Basis(kind="source_passage", ref="EP@1", char_start=5)
    with pytest.raises(ValueError):
        Basis(kind="source_passage", ref="EP@1", char_start=9, char_end=3)


def test_generated_by_validation():
    for ok in ("researcher", "model:claude-x", "deterministic:methods@1", "import", "legacy"):
        Statement(text="t", generated_by=ok)
    with pytest.raises(taxonomy.TaxonomyError):
        Statement(text="t", generated_by="oracle:v1")
    with pytest.raises(taxonomy.TaxonomyError):
        Statement(text="t", generated_by="model:")


def test_statement_roundtrip():
    s = Statement(text="t", generated_by="model:m", evidence_basis=[Basis(kind="project_run", ref="RUN_1")],
                  eca_verdict="SUPPORTED_IF_QUALIFIED", researcher_confirmed=True)
    s2 = Statement.from_dict(s.to_dict())
    assert s2.generated_by == s.generated_by and s2.evidence_basis == s.evidence_basis and s2.eca_verdict == s.eca_verdict
