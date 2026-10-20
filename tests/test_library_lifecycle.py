"""Phases 4.3 and 4.4 (plan v3): knowledge lifecycle, three validity dimensions, promotion, matching, journal policies."""
from __future__ import annotations

import pytest

from research_harness.core.errors import AuthorizationError
from research_harness.knowledge import closure, journal_policy, lifecycle
from research_harness.knowledge.common import KnowledgeError
from research_harness.knowledge.library import make_pin, pin
from research_harness.state.actors import register_actor
from research_harness.state.store import StateStore
from research_harness.workflows import eca
from tests.library_fixture import build_library

ITEM = "LIB_KI_PANEL"
CTX = {"domain": "accounting", "subfield": "archival_financial_reporting", "study_design": "predictive_modeling",
       "estimand": "probability of a later restatement", "measurement": "label = amended annual report",
       "assumptions": ["balanced panel"], "data_regime": {"jurisdiction": "US"}}
METHOD_BASIS = {"kind": "source_passage", "ref": "LIB_EP_BALANCE", "methodological_literature": True}
APPLICATION_BASIS = {"kind": "source_passage", "ref": "LIB_EP_BALANCE", "methodological_literature": False}
SCOPE = {"study_design": "predictive_modeling", "subfield": "archival_financial_reporting"}


@pytest.fixture
def world(tmp_path):
    return build_library(tmp_path / "home")


def confirm_eca(w):
    return eca.confirm(w["lib"], w["token"], w["eca_id"], verdict="SUPPORTED_AS_STATED", rationale="The element carries the condition.")


def test_promotion_requires_three_dimensions_and_resolved_eca(world):
    lib, token = world["lib"], world["token"]

    # nothing established yet: every missing piece is named, nothing is promoted
    with pytest.raises(KnowledgeError, match="PROMOTION_BLOCKED") as e:
        lifecycle.promote(lib, token, ITEM, scope=SCOPE, exclusions=["unbalanced panels"])
    blockers = " | ".join(e.value.details["blockers"])
    assert "source_fidelity is QUOTE_VERIFIED" in blockers and "adjudication unresolved" in blockers and "no validity assessment" in blockers

    # the model's adjudication is only a proposal: the researcher resolves it
    confirm_eca(world)
    assert lifecycle.source_fidelity(lib, ITEM)["source_fidelity"] == "MEANING_VERIFIED"
    with pytest.raises(KnowledgeError, match="no validity assessment"):
        lifecycle.promote(lib, token, ITEM, scope=SCOPE, exclusions=["unbalanced panels"])

    # an assessment resting only on an application paper is not a methodological basis
    lifecycle.validate(lib, token, ITEM, assessment={"context": CTX, "evidence_basis": [APPLICATION_BASIS]})
    with pytest.raises(KnowledgeError, match="no validity assessment for the promoted scope"):
        lifecycle.promote(lib, token, ITEM, scope=SCOPE, exclusions=["unbalanced panels"])

    # the other conditions of promotion
    lifecycle.validate(lib, token, ITEM, assessment={"context": CTX, "evidence_basis": [METHOD_BASIS]})
    with pytest.raises(KnowledgeError, match="scope is empty"):
        lifecycle.promote(lib, token, ITEM, scope={}, exclusions=["x"])
    with pytest.raises(KnowledgeError, match="exclusions are empty"):
        lifecycle.promote(lib, token, ITEM, scope=SCOPE)
    with pytest.raises(KnowledgeError, match="not in the vocabulary"):
        lifecycle.promote(lib, token, ITEM, scope={"colour": "blue"}, exclusions=["x"])
    importer = register_actor(lib, token, actor_id="library_importer", display_name="imp", roles=["importer"])
    with pytest.raises(AuthorizationError, match="ROLE_REQUIRED"):
        lifecycle.promote(lib, importer["token"], ITEM, scope=SCOPE, exclusions=["unbalanced panels"])

    done = lifecycle.promote(lib, token, ITEM, scope=SCOPE, exclusions=["unbalanced panels"])
    dims = done["dimensions"]
    assert dims["source_fidelity"] == "MEANING_VERIFIED" and dims["computational_reproducibility"] == "REPRODUCED_WITHIN_TOLERANCE"
    assert dims["methodological_validity"][0]["verdict"] == "PROVISIONAL"     # not SUPPORTED: nobody confirmed it
    promoted = lib.get_object(ITEM)["content"]["promotion"]
    assert promoted["promoted"] and promoted["scope"] == SCOPE and promoted["promoted_by"] == "library_owner"


def test_promotion_is_refused_again_if_an_adjudication_is_overturned_later(world):
    lib, token = world["lib"], world["token"]
    confirm_eca(world)
    lifecycle.validate(lib, token, ITEM, assessment={"context": CTX, "evidence_basis": [METHOD_BASIS]})
    eca.confirm(lib, token, world["eca_id"], verdict="NOT_SUPPORTED", rationale="On rereading, the passage does not say this.")
    assert lifecycle.source_fidelity(lib, ITEM)["source_fidelity"] == "DISPUTED"
    with pytest.raises(KnowledgeError, match="PROMOTION_BLOCKED"):
        lifecycle.promote(lib, token, ITEM, scope=SCOPE, none_known=True)


def test_validating_an_item_does_not_invalidate_its_adjudications(world):
    lib, token = world["lib"], world["token"]
    confirm_eca(world)
    before = lib.get_object(ITEM)["revision"]
    lifecycle.validate(lib, token, ITEM)
    lifecycle.validate(lib, token, ITEM)
    assert lib.get_object(ITEM)["revision"] == before + 2                           # history is kept
    assert lifecycle.source_fidelity(lib, ITEM)["source_fidelity"] == "MEANING_VERIFIED"   # the reading is bound to the words, not the revision


def test_validity_is_per_context_and_needs_every_condition_for_supported(world):
    lib, token = world["lib"], world["token"]
    confirm_eca(world)
    base = {"context": CTX, "evidence_basis": [METHOD_BASIS], "confirmed_by_researcher": True}
    def verdict_of(**kw):
        return lifecycle.assess_validity(lib, **{**base, **kw})
    assert verdict_of()["verdict"] == "SUPPORTED_WITHIN_SCOPE"
    v = verdict_of(known_criticisms=[{"ref": "LIB_EP_BALANCE", "status": "open"}])
    assert v["verdict"] == "PROVISIONAL" and any("criticism" in b for b in v["blockers"])
    assert verdict_of(known_criticisms=[{"ref": "x", "status": "addressed"}, {"ref": "y", "status": "scoped_out"}])["verdict"] == "SUPPORTED_WITHIN_SCOPE"
    assert verdict_of(diagnostics_applicable=True)["verdict"] == "PROVISIONAL"
    assert verdict_of(diagnostics_applicable=True, diagnostics_runs=["RUN_X"])["verdict"] == "SUPPORTED_WITHIN_SCOPE"
    assert verdict_of(confirmed_by_researcher=False)["verdict"] == "PROVISIONAL"
    assert verdict_of(evidence_basis=[APPLICATION_BASIS])["verdict"] == "PROVISIONAL"
    assert verdict_of(evidence_basis=[])["verdict"] == "NOT_ASSESSED"
    assert verdict_of(contested=True)["verdict"] == "CONTESTED"
    with pytest.raises(KnowledgeError, match="CONTEXT_REQUIRED"):
        lifecycle.assess_validity(lib, context={}, evidence_basis=[METHOD_BASIS])
    # a model or an importer cannot confirm validity
    importer = register_actor(lib, token, actor_id="library_importer", display_name="imp", roles=["importer"])
    with pytest.raises(KnowledgeError, match="CONFIRMATION_NEEDS_RESEARCHER"):
        lifecycle.validate(lib, importer["token"], ITEM, assessment=base)

    # two contexts, two assessments: matching is for the project's own context
    lifecycle.validate(lib, token, ITEM, assessment=base)
    other = {**CTX, "estimand": "association with board independence"}
    lifecycle.validate(lib, token, ITEM, assessment={"context": other, "evidence_basis": [APPLICATION_BASIS]})
    rows = {r["item_id"]: r for r in lifecycle.match(lib, CTX)}
    assert rows[ITEM]["status"] == "applicable" and rows[ITEM]["dimensions"]["methodological_validity_for_this_context"] == "SUPPORTED_WITHIN_SCOPE"
    row = {r["item_id"]: r for r in lifecycle.match(lib, other)}[ITEM]
    assert row["status"] == "candidate_validity_not_established_here" and row["dimensions"]["methodological_validity_for_this_context"] == "PROVISIONAL"
    assert len(row["dimensions"]["methodological_validity_all_contexts"]) == 2


def test_match_applies_the_recommendation_rule_and_shows_dimensions_apart(world):
    lib, token = world["lib"], world["token"]
    lifecycle.validate(lib, token, ITEM, assessment={"context": CTX, "evidence_basis": [METHOD_BASIS], "confirmed_by_researcher": True})
    # source fidelity is only QUOTE_VERIFIED (the adjudication is unconfirmed): validity alone does not make it applicable
    row = {r["item_id"]: r for r in lifecycle.match(lib, CTX)}[ITEM]
    assert row["status"] == "candidate_validity_not_established_here"
    assert row["dimensions"]["source_fidelity"] == "QUOTE_VERIFIED" and row["dimensions"]["computational_reproducibility"] == "REPRODUCED_WITHIN_TOLERANCE"
    assert any("Confirm the evidence-claim adjudication" in q for q in row["questions"])
    confirm_eca(world)
    assert {r["item_id"]: r for r in lifecycle.match(lib, CTX)}[ITEM]["status"] == "applicable"

    # an exclusion hit makes it related, not applicable, whatever else is true
    hit = {r["item_id"]: r for r in lifecycle.match(lib, {**CTX, "conditions": ["unbalanced panels"]})}[ITEM]
    assert hit["status"] == "related_not_applicable" and "exclusion applies here: unbalanced panels" in hit["because"]
    wrong_design = {r["item_id"]: r for r in lifecycle.match(lib, {**CTX, "study_design": "archival"})}[ITEM]
    assert wrong_design["status"] == "related_not_applicable"


def test_usage_never_raises_validity_and_failures_are_shown_at_reuse(world, tmp_path):
    lib, token = world["lib"], world["token"]
    confirm_eca(world)
    lifecycle.validate(lib, token, ITEM, assessment={"context": CTX, "evidence_basis": [METHOD_BASIS]})   # PROVISIONAL
    project, ptoken = StateStore.create(world["home"], "PRJ_REUSE", owner_id="researcher")
    res = closure.use_in_project(project, ptoken, lib, pin(lib, ITEM))
    before = lib.get_object(ITEM)["content_digest"]
    for outcome in ({"result": "worked, no diagnostic concerns"}, {"result": "worked again"}):
        lifecycle.record_usage(project, ptoken, res["manifest_id"], context=CTX, outcome=outcome)
    assert len(project.get_object(res["manifest_id"])["content"]["usage_records"]) == 2
    assert lib.get_object(ITEM)["content_digest"] == before                                  # the library item is untouched
    row = {r["item_id"]: r for r in lifecycle.match(lib, CTX)}[ITEM]
    assert row["dimensions"]["methodological_validity_for_this_context"] == "PROVISIONAL" and row["status"] != "applicable"

    # a recorded failure travels with the item
    lifecycle.revise(lib, token, ITEM, record_failure={"failure": "The estimator broke on an unbalanced panel.", "context": CTX,
                                                       "observed_in": "PRJ_REUSE"})
    row = {r["item_id"]: r for r in lifecycle.match(lib, CTX)}[ITEM]
    assert row["failure_records"][0]["failure"].startswith("The estimator broke")
    assert all(r["item_id"] != "LIB_KI_FAILURE_000001" for r in lifecycle.match(lib, CTX))   # the record itself is not offered as a method


def test_revise_keeps_history_and_projects_get_a_notice(world):
    lib, token = world["lib"], world["token"]
    project, ptoken = StateStore.create(world["home"], "PRJ_NOTICE", owner_id="researcher")
    closure.use_in_project(project, ptoken, lib, world["item_pin"])
    old = world["item_pin"]
    out = lifecycle.revise(lib, token, ITEM, restrict=["panels with fewer than 3 years"])
    assert out["lifecycle"] == "RESTRICTED" and lib.get_object(ITEM)["revision"] == 2 and lib.get_object(ITEM, 1)["content"]["lifecycle"] == "ACTIVE"
    assert "panels with fewer than 3 years" in lib.get_object(ITEM)["content"]["exclusions"]
    notices = closure.check_updates(project, world["home"])["notices"]
    assert {n["notice"] for n in notices} == {"NEWER_REVISION", "ITEM_RESTRICTED"}
    hit = {r["item_id"]: r for r in lifecycle.match(lib, {**CTX, "conditions": ["panels with fewer than 3 years"]})}[ITEM]
    assert hit["status"] == "related_not_applicable"
    # superseded items are not offered for new use
    lifecycle.revise(lib, token, ITEM, supersede_by=make_pin("LIB_KI_PKG", 1, lib.get_object("LIB_KI_PKG")["content_digest"]))
    assert lib.get_object(ITEM)["content"]["lifecycle"] == "SUPERSEDED"
    with pytest.raises(KnowledgeError, match="ITEM_NOT_ACTIVE"):
        fresh, fresh_token = StateStore.create(world["home"], "PRJ_NEW", owner_id="researcher")
        closure.use_in_project(fresh, fresh_token, lib, pin(lib, ITEM))
    assert old.endswith(lib.get_object(ITEM, 1)["content_digest"][:16])                       # the old pin still names revision 1
    with pytest.raises(KnowledgeError, match="ONE_ACTION"):
        lifecycle.revise(lib, token, ITEM)
    ext = lifecycle.revise(lib, token, "LIB_KI_PKG", extend={"period": "2010-2020"})
    assert lib.get_object("LIB_KI_PKG")["content"]["promotion"]["extension_pending"] == {"period": "2010-2020"} and ext["revision"] == 2


def test_acquire_starts_unchecked_and_formalize_needs_an_evidence_basis(world):
    lib, token = world["lib"], world["token"]
    got = lifecycle.acquire(lib, token, kind="checklist", label="Reporting checklist", acquired_from="review",
                            elements=[{"id": "C1", "text": "State the label source."}], generated_by="model:drafting-assistant")
    assert got["item_id"].startswith("LIB_KI_CHECKLIST_")
    c = lib.get_object(got["item_id"])["content"]
    assert c["validation"] == {"source_fidelity": "NOT_CHECKED", "computational_reproducibility": "NOT_ATTEMPTED", "validity_assessments": []}
    assert c["promotion"] == {} and c["generated_by"] == "model:drafting-assistant" and c["attributes"]["acquired_from"] == "review"
    with pytest.raises(KnowledgeError, match="FORMALIZE_REFUSED") as e:
        lifecycle.formalize(lib, token, got["item_id"], as_kind="checklist")
    assert "no evidence basis" in e.value.details["problems"][0]
    lifecycle.acquire(lib, token, item_id="LIB_KI_CHECK2", kind="checklist", label="Checklist 2", acquired_from="policy",
                      elements=[{"id": "C1", "text": "State the label source.", "evidence_basis": [{"kind": "researcher_judgement", "ref": "owner"}]}])
    assert lifecycle.formalize(lib, token, "LIB_KI_CHECK2", as_kind="checklist")["revision"] == 2
    assert lib.get_object("LIB_KI_CHECK2")["content"]["attributes"]["formalized"] is True
    with pytest.raises(KnowledgeError, match="FORMALIZE_REFUSED"):
        lifecycle.formalize(lib, token, "LIB_KI_CHECK2", as_kind="replication_package")
    with pytest.raises(KnowledgeError, match="ACQUIRE_SOURCE_UNKNOWN"):
        lifecycle.acquire(lib, token, kind="checklist", label="x", acquired_from="rumour")
    with pytest.raises(KnowledgeError, match="KIND_NOT_ACQUIRABLE"):
        lifecycle.acquire(lib, token, kind="library_import_manifest", label="x", acquired_from="source")


def test_project_retrospective_reports_dimensions_separately_and_promotes_nothing(world):
    lib, token = world["lib"], world["token"]
    project, ptoken = StateStore.create(world["home"], "PRJ_RETRO", owner_id="researcher")
    res = closure.use_in_project(project, ptoken, lib, world["item_pin"])
    lifecycle.acquire(project, ptoken, item_id="KI_LESSON1", kind="review_lesson", label="Check the panel balance first",
                      acquired_from="retrospective", body={"lesson": "Balance was the binding condition."})
    lifecycle.record_usage(project, ptoken, res["manifest_id"], context=CTX, outcome={"result": "ok"})
    r = lifecycle.retrospective(project)
    assert [c["item_id"] for c in r["candidates"]] == ["KI_LESSON1"] and r["candidates"][0]["promoted"] is False
    assert set(r["candidates"][0]["dimensions"]) == {"source_fidelity", "computational_reproducibility", "methodological_validity"}
    assert r["imported_items_used"][0]["usage_records"][0]["outcome"] == {"result": "ok"}


# --------------------------------------------------------------------------------------------------
# journal policy registry (4.4)
# --------------------------------------------------------------------------------------------------

POLICY_TEXT = ("Author guidelines. Submissions must include a data availability statement. "
               "Papers should report out-of-sample performance on a held-out period. Replication code is encouraged.")


def test_a_policy_is_stored_with_its_snapshot_and_must_quote_it(world):
    lib, token = world["lib"], world["token"]
    with pytest.raises(KnowledgeError, match="EXCERPT_NOT_IN_SNAPSHOT"):
        journal_policy.add_policy(lib, token, journal="J Accounting", policy_type="reporting_standard", excerpt="Authors must pre-register.",
                                  effective_from="2024-01-01", retrieved_at="2026-01-05", snapshot=POLICY_TEXT)
    with pytest.raises(KnowledgeError, match="SOURCE_REFERENCE_REQUIRED"):
        journal_policy.add_policy(lib, token, journal="J Accounting", policy_type="official_requirement",
                                  excerpt="must include a data availability statement", effective_from="2024-01-01",
                                  retrieved_at="2026-01-05", snapshot=POLICY_TEXT)
    p = journal_policy.add_policy(lib, token, journal="J Accounting", policy_type="official_requirement", source_url="https://example.org/guidelines",
                                  excerpt="must include a data availability statement", effective_from="2024-01-01", retrieved_at="2026-01-05",
                                  snapshot=POLICY_TEXT)
    assert p["policy_id"].startswith("LIB_JP_J_ACCOUNTING_OFFICIAL_REQUIREMENT") and lib.artifacts.get_bytes(
        lib.get_object(p["policy_id"])["content"]["source_sha256"]).decode("utf-8") == POLICY_TEXT


def test_only_an_official_requirement_in_force_is_binding(world):
    lib, token = world["lib"], world["token"]
    common = dict(journal="J Accounting", retrieved_at="2026-01-05", snapshot=POLICY_TEXT)
    req = journal_policy.add_policy(lib, token, policy_type="official_requirement", source_url="https://example.org/guidelines",
                                    excerpt="must include a data availability statement", effective_from="2024-01-01", **common)
    journal_policy.add_policy(lib, token, policy_type="published_precedent", excerpt="report out-of-sample performance on a held-out period",
                              effective_from="2023-01-01", **common)
    journal_policy.add_policy(lib, token, policy_type="reporting_standard", excerpt="Replication code is encouraged", effective_from="2020-01-01", **common)
    by_type = {p["policy_type"]: p for p in journal_policy.lookup(lib, "j accounting", as_of="2025-06-01")}
    assert by_type["official_requirement"]["binding"] is True
    assert by_type["published_precedent"]["binding"] is False and by_type["reporting_standard"]["binding"] is False   # I12
    assert journal_policy.lookup(lib, "j accounting", as_of="2023-06-01", policy_type="official_requirement")[0]["binding"] is False  # not yet in force
    journal_policy.supersede(lib, token, req["policy_id"], effective_to="2025-12-31")
    assert journal_policy.lookup(lib, "J Accounting", as_of="2025-06-01", policy_type="official_requirement")[0]["binding"] is True
    assert journal_policy.lookup(lib, "J Accounting", as_of="2026-06-01", policy_type="official_requirement")[0]["binding"] is False
    assert journal_policy.lookup(lib, "Another Journal") == []
    with pytest.raises(KnowledgeError, match="PERIOD_INVALID"):
        journal_policy.supersede(lib, token, req["policy_id"], effective_to="2023-01-01")


def test_a_policy_travels_with_a_library_item_that_pins_it(world):
    lib, token = world["lib"], world["token"]
    p = journal_policy.add_policy(lib, token, journal="J Accounting", policy_type="official_requirement", source_url="https://example.org/g",
                                  excerpt="must include a data availability statement", effective_from="2024-01-01", retrieved_at="2026-01-05",
                                  snapshot=POLICY_TEXT)
    item = lib.get_object(ITEM)
    from research_harness.knowledge.library import add_object
    add_object(lib, token, ITEM, "KnowledgeItem", {**item["content"], "pins": item["content"]["pins"] + [p["pin"]]},
               edges=[("depends_on", "LIB_EP_BALANCE", 1), ("depends_on", "LIB_KI_PKG", 1), ("depends_on", "LIB_DW_ACC_FRAUD_DETECTION", 1),
                      ("derived_from", "LIB_SRC_SYNTH2025", 1)])
    project, ptoken = StateStore.create(world["home"], "PRJ_POLICY", owner_id="researcher")
    closure.use_in_project(project, ptoken, lib, pin(lib, ITEM))
    imported = project.get_object(p["policy_id"])
    assert imported["object_type"] == "JournalPolicy" and project.artifacts.has(imported["content"]["source_sha256"])
    assert closure.verify_closure(project)["status"] == "VERIFIED"
