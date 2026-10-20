"""Phase 3D (plan v3): playbooks whose elements carry their own dimensions, selection by conditions, analysis plans, journal alignment."""
from __future__ import annotations

import pytest

from research_harness.knowledge import closure, journal_policy, lifecycle, literature, playbooks
from research_harness.knowledge.common import KnowledgeError
from research_harness.knowledge.library import add_object, create_library, pin
from research_harness.state.store import StateStore
from research_harness.workflows import eca
from research_harness.workflows.adapters import ScriptedModelAdapter
from tests import m3_fixture as fx
from tests.library_fixture import PANEL_CSV

CTX = {"domain": "accounting", "subfield": "archival_financial_reporting", "study_design": "predictive_modeling",
       "estimand": "probability of a later restatement", "measurement": "label = amended annual report", "assumptions": ["balanced panel"]}
SCOPE = {"study_design": "predictive_modeling", "subfield": "archival_financial_reporting"}
METHOD_BASIS = {"kind": "source_passage", "ref": "x", "methodological_literature": True}
APPLICATION_BASIS = {**METHOD_BASIS, "methodological_literature": False}


def eca_ok() -> ScriptedModelAdapter:
    return ScriptedModelAdapter(model_id="scripted-adjudicator", default_response={
        "passage_proposition": "As quoted.", "equivalence": "EQUIVALENT", "dropped_conditions": [], "cue_handling": [], "verdict": "SUPPORTED_AS_STATED",
        "required_qualification": []})


@pytest.fixture
def world(tmp_path):
    store, token = StateStore.create(tmp_path / "home", "PRJ_PB", owner_id="researcher")
    fx.import_corpus(store, token)
    sha = store.artifacts.put_bytes(PANEL_CSV.encode("utf-8"))
    with store.write_tx(token=token) as tx:
        tx.add_revision(object_id="PKG_PANEL", object_type="KnowledgeItem", origin={}, edges=[], content={
            "label": "package", "kind": "replication_package", "body": {"files": [{"name": "panel.csv", "sha256": sha}], "expected_outputs": [
                {"id": "n_rows", "file": "panel.csv", "stat": "count", "expected": 8, "tolerance": 0},
                {"id": "positive_rate", "file": "panel.csv", "column": "label", "stat": "positive_rate", "expected": 0.25, "tolerance": 1e-9}]}})
    review = literature.run_literature(store, token, "method", [fx.PAPER_A], fx.extractor())
    pb = playbooks.draft_playbook_from_methods(store, token, review, playbook_id="PB_PU", label="Temporal validation for restatement prediction",
                                               scope=SCOPE, exclusions=["unbalanced panels"], replication_package="PKG_PANEL",
                                               replication_links={"estimand": ["positive_rate"], "sample_construction": ["n_rows"]})
    return {"store": store, "token": token, "review": review, "pb": pb}


def confirm_all_elements(w, pid="PB_PU"):
    store, token = w["store"], w["token"]
    for e in store.get_object(pid)["content"]["elements"]:
        passage = e["evidence_basis"][0]["ref"]
        res = eca.adjudicate(store, token, eca.resolve_statement(store, pid, element=e["id"]), passage, eca_ok())
        eca.confirm(store, token, res["adjudication_id"], verdict="SUPPORTED_AS_STATED", rationale=f"Element {e['id']} reads the passage correctly.")


def assess(w, *, basis=None, criticisms=None, confirmed=True, pid="PB_PU", **kw):
    ref = w["store"].get_object(pid)["content"]["elements"][0]["evidence_basis"][0]["ref"]
    b = [{**(basis or METHOD_BASIS), "ref": ref}]
    return lifecycle.validate(w["store"], w["token"], pid, assessment={"context": CTX, "evidence_basis": b, "known_criticisms": criticisms or [],
                                                                         "confirmed_by_researcher": confirmed, **kw})


def test_draft_playbook_elements_have_their_own_basis_and_dimensions(world):
    store, pb = world["store"], world["pb"]
    c = store.get_object("PB_PU")["content"]
    assert store.get_object("PB_PU")["object_type"] == "MethodPlaybook" and c["validation_status"] == "EXTRACTED"
    assert {e["category"] for e in c["elements"]} == {"sample_construction", "estimand", "validation_scheme"} and pb["critical"] == 3
    for e in c["elements"]:
        assert e["evidence_basis"][0]["kind"] == "source_passage" and e["generated_by"] == "model:scripted-extractor" and e["risk_tier"] == "HIGH"
    rep = {r["element"]: r for r in playbooks.element_report(store, "PB_PU")}
    by_cat = {r["category"]: r for r in rep.values()}
    assert all(r["source_fidelity"] == "QUOTE_VERIFIED" and r["validity_assessments"] == [] for r in rep.values())
    assert by_cat["estimand"]["computational_reproducibility"] == "REPRODUCED_WITHIN_TOLERANCE"
    assert by_cat["sample_construction"]["computational_reproducibility"] == "REPRODUCED_WITHIN_TOLERANCE"
    assert by_cat["validation_scheme"]["computational_reproducibility"] == "NO_ASSETS"           # no replication evidence for this element
    assert c["playbook"]["lineage"] == [{"source_id": fx.PAPER_A, "revision": 1}] and c["playbook"]["replication_assets"]["package"] == "PKG_PANEL"
    with pytest.raises(KnowledgeError, match="NOT_A_METHOD_REVIEW"):
        playbooks.draft_playbook_from_methods(store, world["token"], {"purpose": "theory"}, playbook_id="PB_X", label="x")


def test_extracted_and_reproduced_playbook_not_valid_by_default(world):
    store = world["store"]
    # quote-verified (fidelity) and reproduced (reproducibility), yet no validity assessment exists for this context
    rows = {r["item_id"]: r for r in playbooks.select_playbooks(store, CTX)}
    row = rows["PB_PU"]
    assert row["status"] == "candidate_validity_not_established_here"
    d = row["dimensions"]
    assert d["source_fidelity"] == "QUOTE_VERIFIED" and d["computational_reproducibility"] == "REPRODUCED_WITHIN_TOLERANCE"
    assert d["methodological_validity_for_this_context"] == "NOT_ASSESSED" and d["methodological_validity_all_contexts"] == []
    assert any("Confirm the evidence-claim adjudication" in q for q in row["questions"]) and any("valid here" in q for q in row["questions"])
    # even with a confirmed assessment, a model's reading that nobody adjudicated keeps it a candidate
    assess(world)
    assert {r["item_id"]: r for r in playbooks.select_playbooks(store, CTX)}["PB_PU"]["status"] == "candidate_validity_not_established_here"
    confirm_all_elements(world)
    assert {r["item_id"]: r for r in playbooks.select_playbooks(store, CTX)}["PB_PU"]["status"] == "applicable"


def test_open_known_criticism_blocks_supported_within_scope(world):
    confirm_all_elements(world)
    out = assess(world, criticisms=[{"ref": "EP_CRITIC", "status": "open"}])
    a = out["validity_assessments"][0]
    assert a["verdict"] == "PROVISIONAL" and any("criticism" in b for b in a["blockers"])
    assert {r["item_id"]: r for r in playbooks.select_playbooks(world["store"], CTX)}["PB_PU"]["status"] == "candidate_validity_not_established_here"
    out = assess(world, criticisms=[{"ref": "EP_CRITIC", "status": "addressed"}])
    assert out["validity_assessments"][0]["verdict"] == "SUPPORTED_WITHIN_SCOPE" and len(out["validity_assessments"]) == 1   # same context: replaced
    assert {r["item_id"]: r for r in playbooks.select_playbooks(world["store"], CTX)}["PB_PU"]["status"] == "applicable"


def test_validity_requires_methodological_basis_not_only_application(world):
    confirm_all_elements(world)
    a = assess(world, basis=APPLICATION_BASIS)["validity_assessments"][0]
    assert a["verdict"] == "PROVISIONAL" and any("methodological literature" in b for b in a["blockers"])
    assert {r["item_id"]: r for r in playbooks.select_playbooks(world["store"], CTX)}["PB_PU"]["status"] == "candidate_validity_not_established_here"
    with pytest.raises(KnowledgeError) as e:
        lifecycle.promote(world["store"], world["token"], "PB_PU", scope=SCOPE, exclusions=["unbalanced panels"])
    assert "no validity assessment for the promoted scope" in str(e.value)


def test_validity_assessment_can_cover_chosen_elements_only(world):
    store = world["store"]
    ids = [e["id"] for e in store.get_object("PB_PU")["content"]["elements"]]
    assess(world, elements=[ids[0]])
    rep = {r["element"]: r for r in playbooks.element_report(store, "PB_PU")}
    assert len(rep[ids[0]]["validity_assessments"]) == 1 and rep[ids[1]]["validity_assessments"] == []


def test_playbook_element_without_basis_blocks_promotion(world):
    store, token = world["store"], world["token"]
    with pytest.raises(KnowledgeError, match="ELEMENT_WITHOUT_BASIS"):
        playbooks.add_element(store, token, "PB_PU", category="interpretation", text="Read AP against the base rate.")
    playbooks.add_element(store, token, "PB_PU", category="interpretation", text="Read AP against the base rate.", allow_unbased=True)
    assert any(e.get("basis_missing") for e in store.get_object("PB_PU")["content"]["elements"])
    assess(world)
    with pytest.raises(KnowledgeError) as e:
        lifecycle.promote(store, token, "PB_PU", scope=SCOPE, exclusions=["unbalanced panels"])
    assert "elements without an evidence basis: E04" in str(e.value)


def test_usage_record_does_not_raise_validity(tmp_path):
    home = tmp_path / "home"
    lib, ltoken = create_library(home)
    fx.import_corpus(lib, ltoken, sources=(fx.PAPER_A,))
    review = literature.run_literature(lib, ltoken, "method", ["LIB_" + fx.PAPER_A], fx.extractor())
    pb = playbooks.draft_playbook_from_methods(lib, ltoken, review, playbook_id="PB_LIBPU", label="library playbook", scope=SCOPE)
    assert pb["object_id"] == "LIB_PB_LIBPU"
    project, ptoken = StateStore.create(home, "PRJ_USE", owner_id="researcher")
    res = closure.use_in_project(project, ptoken, lib, pb["pin"])
    before = {r["item_id"]: r["status"] for r in playbooks.select_playbooks(project, CTX)}
    for i in range(3):
        lifecycle.record_usage(project, ptoken, res["manifest_id"], context=CTX, outcome={"result": f"worked well, run {i}", "diagnostics_concerns": 0})
    after = {r["item_id"]: r for r in playbooks.select_playbooks(project, CTX)}
    assert before == {k: v["status"] for k, v in after.items()} and after["LIB_PB_LIBPU"]["status"] == "candidate_validity_not_established_here"
    assert after["LIB_PB_LIBPU"]["dimensions"]["methodological_validity_for_this_context"] == "NOT_ASSESSED"
    assert len(project.get_object(res["manifest_id"])["content"]["usage_records"]) == 3
    assert lib.get_object("LIB_PB_LIBPU")["revision"] == 1                       # the library playbook itself never changed


def test_selection_requires_conditions_not_data_shape(world):
    store, token = world["store"], world["token"]
    playbooks.set_conditions(store, token, "PB_PU", [{"id": "C1", "text": "The panel is balanced", "requires": {"fact": "panel_balanced", "equals": True},
                                                      "basis": [{"kind": "source_passage", "ref": "EP_X"}]}])
    confirm_all_elements(world)
    assess(world)                                                  # validity is supported for this context and fidelity is meaning-verified
    shape = {**CTX, "facts": {"data_shape": "panel", "n_firms": 3000}}   # data shape alone says nothing about the condition
    row = {r["item_id"]: r for r in playbooks.select_playbooks(store, shape)}["PB_PU"]
    assert row["status"] == "candidate_validity_not_established_here" and row["conditions"][0] == {"id": "C1", "status": "UNKNOWN", "text": "The panel is balanced", "needs": "panel_balanced"}
    assert any("panel_balanced" in q for q in row["questions"])
    bad = {r["item_id"]: r for r in playbooks.select_playbooks(store, {**CTX, "facts": {"panel_balanced": False}})}["PB_PU"]
    assert bad["status"] == "related_not_applicable" and bad["conditions"][0]["status"] == "NOT_SATISFIED" and "condition C1 is not satisfied" in bad["because"][0]
    good = {r["item_id"]: r for r in playbooks.select_playbooks(store, {**CTX, "facts": {"panel_balanced": True}})}["PB_PU"]
    assert good["status"] == "applicable" and good["conditions"][0]["evidence"] == {"fact": "panel_balanced", "value": True}
    # a context that only describes the data (no scope, no estimand) can never make a playbook applicable
    assert {r["item_id"]: r for r in playbooks.select_playbooks(store, {"facts": {"panel_balanced": True, "data_shape": "panel"}})}["PB_PU"]["status"] != "applicable"
    with pytest.raises(KnowledgeError, match="CONDITION_INVALID"):
        playbooks.set_conditions(store, token, "PB_PU", [{"id": "C2", "text": "t", "requires": {"fact": "x", "equals": 1}}])


def steps_for(world, **override):
    els = {e["category"]: e["id"] for e in world["store"].get_object("PB_PU")["content"]["elements"]}
    steps = [
        {"id": "sample", "element": els["sample_construction"], "fidelity": "inherited", "command": ["python", "sample.py"], "outputs": ["sample.csv"]},
        {"id": "label", "element": els["estimand"], "fidelity": "adapted", "command": ["python", "label.py"], "depends_on_steps": ["sample"],
         "changes": "label window shortened to two years", "rationale": "the data end earlier than the source's"},
        {"id": "validate", "element": els["validation_scheme"], "fidelity": "omitted", "omit_reason": "replaced_by", "replaced_by": "rolling"},
        {"id": "rolling", "fidelity": "added", "command": ["python", "rolling.py"], "depends_on_steps": ["label"],
         "rationale": "rolling-origin validation suits the shorter window", "basis": [{"kind": "researcher_judgement", "ref": "owner"}]},
    ]
    return override.get("steps", steps)


def test_plan_step_fidelity_and_omission_reason(world):
    store, token = world["store"], world["token"]
    confirm_all_elements(world)
    assess(world)
    plan = playbooks.draft_analysis_plan(store, "PB_PU", plan_id="PLAN_1", label="Plan", context=CTX, steps=steps_for(world), declared={"alpha": 0.05, "basis": "owner"})
    assert plan["fidelity_summary"] == {"inherited": ["sample"], "adapted": ["label"], "added": ["rolling"], "omitted": ["validate"]}
    assert plan["playbook_status"] == "applicable"
    bad = steps_for(world)
    bad[1] = {k: v for k, v in bad[1].items() if k not in {"changes"}}
    bad[2] = {**bad[2], "omit_reason": "tired"}
    bad[3] = {k: v for k, v in bad[3].items() if k != "basis"}
    with pytest.raises(KnowledgeError, match="PLAN_INVALID") as e:
        playbooks.draft_analysis_plan(store, "PB_PU", plan_id="PLAN_2", label="Plan", context=CTX, steps=bad)
    probs = " | ".join(e.value.details["problems"])
    assert "adapted step records what changed" in probs and "omit_reason in" in probs and "added step needs a rationale and a basis" in probs
    with pytest.raises(KnowledgeError, match="PLAN_INVALID") as e:          # a critical element left unaddressed
        playbooks.draft_analysis_plan(store, "PB_PU", plan_id="PLAN_3", label="Plan", context=CTX, steps=steps_for(world)[:1])
    assert "is not addressed" in " ".join(e.value.details["problems"])

    # the plan is a proposal: importing it is the researcher's act, through the gateway
    receipt = playbooks.submit_operations(store, token, plan["operations"], proposal_id="PROP_PLAN1")
    assert receipt["decision"] == "ACCEPTED", receipt
    stored = store.get_object("PLAN_1")
    assert stored["object_type"] == "AnalysisPlan" and stored["content"]["playbook_pins"][0].startswith("LIB:PB_PU@")
    rep = playbooks.fidelity_report(store, "PLAN_1")
    assert rep["inherited"] == ["sample"] and rep["adapted"][0]["changes"].startswith("label window") and rep["omitted"][0]["reason"] == "replaced_by"
    assert rep["added"][0]["step"] == "rolling" and rep["rests_on"][0]["lineage"] == [{"source_id": fx.PAPER_A, "revision": 1}]
    assert {e["source_fidelity"] for e in rep["rests_on"][0]["elements"]} == {"MEANING_VERIFIED"}


def test_qualitative_design_gets_no_econometric_steps(world):
    store = world["store"]
    qualitative = {"domain": "education", "subfield": "qualitative_education", "study_design": "qualitative_interview",
                   "estimand": "teachers' accounts of feedback"}
    row = {r["item_id"]: r for r in playbooks.select_playbooks(store, qualitative)}["PB_PU"]
    assert row["status"] == "related_not_applicable" and any("outside the item's scope" in b and "study_design" in b for b in row["because"])
    with pytest.raises(KnowledgeError, match="PLAYBOOK_NOT_APPLICABLE"):
        playbooks.draft_analysis_plan(store, "PB_PU", plan_id="PLAN_Q", label="Plan", context=qualitative, steps=steps_for(world))


# ------------------------------------------------------------------------------------------------
# journal alignment
# ------------------------------------------------------------------------------------------------

TEXT = ("Submissions must include a data availability statement. Empirical papers should report out-of-sample performance. "
        "Papers following the reporting guideline must state the label source and the split dates.")


@pytest.fixture
def journal(tmp_path):
    lib, token = create_library(tmp_path / "home")
    common = dict(journal="J Accounting", snapshot=TEXT, retrieved_at="2026-01-10")
    journal_policy.add_policy(lib, token, policy_type="official_requirement", source_url="https://example.org/g", effective_from="2025-01-01",
                              excerpt="must include a data availability statement", applies_to={"article_types": ["empirical"]}, **common)
    journal_policy.add_policy(lib, token, policy_type="reporting_standard", effective_from="2024-01-01", excerpt="must state the label source and the split dates",
                              reporting_items=["label source", "split dates"], applies_to={"study_designs": ["predictive_modeling"]}, **common)
    journal_policy.add_policy(lib, token, policy_type="published_precedent", effective_from="2023-01-01", excerpt="should report out-of-sample performance", **common)
    return lib, token


def test_precedent_never_binding(journal):
    lib, _ = journal
    al = journal_policy.alignment(lib, journal="J Accounting", article_type="empirical", study_design="predictive_modeling",
                                  planned_submission="2026-06-01", today="2026-03-01")
    assert [p["policy_type"] for p in al["binding_requirements"]] == ["official_requirement"]
    assert [p["policy_type"] for p in al["context_not_requirements"]] == ["published_precedent"]
    assert [i["item"] for i in al["required_reporting_items"]] == ["label source", "split dates"]


def test_policy_effective_date_and_article_type(journal):
    lib, token = journal
    base = dict(journal="J Accounting", study_design="predictive_modeling", today="2026-03-01")
    early = journal_policy.alignment(lib, article_type="empirical", planned_submission="2024-06-01", **base)
    assert early["binding_requirements"] == []                       # not yet in force on that date
    other = journal_policy.alignment(lib, article_type="review", planned_submission="2026-06-01", **base)
    assert other["binding_requirements"] == []                       # applies to empirical papers only
    pid = journal_policy.lookup(lib, "J Accounting", policy_type="official_requirement")[0]["policy_id"]
    journal_policy.supersede(lib, token, pid, effective_to="2026-12-31")
    assert len(journal_policy.alignment(lib, article_type="empirical", planned_submission="2026-06-01", **base)["binding_requirements"]) == 1
    assert journal_policy.alignment(lib, article_type="empirical", planned_submission="2027-02-01", **base)["binding_requirements"] == []


def test_stale_policy_flagged(journal):
    lib, _ = journal
    fresh = journal_policy.alignment(lib, journal="J Accounting", article_type="empirical", planned_submission="2026-06-01", today="2026-03-01")
    assert fresh["needs_reverification"] == [] and {p["status"] for p in fresh["binding_requirements"]} == {"CURRENT"}
    stale = journal_policy.alignment(lib, journal="J Accounting", article_type="empirical", planned_submission="2026-06-01", today="2027-03-01")
    (req,) = stale["binding_requirements"]
    assert req["status"] == "RE-VERIFY" and stale["needs_reverification"]       # shown, never silently treated as current
    assert journal_policy.alignment(lib, journal="J Accounting", article_type="empirical", planned_submission="2026-06-01", today="2027-03-01",
                                    stale_days=800)["needs_reverification"] == []
