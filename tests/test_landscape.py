"""M5 WP6: the landscape. Entries with passages at Source@revision, three layers, A5 evidence dimensions, suite inferences, versions, currency, export, task goal."""
from __future__ import annotations

import json

import jsonschema
import pytest

from research_harness.cli import main
from research_harness.core.errors import AuthorizationError
from research_harness.discovery import landscape as L
from research_harness.discovery import problem as P
from research_harness.discovery import retrieval as RT
from research_harness.export.landscape import build_landscape_export
from research_harness.export.manifest import verify_export, write_export
from research_harness.knowledge import corpus
from research_harness.knowledge.common import KnowledgeError
from research_harness.knowledge.replication import add_replication_package, record_reproduction
from research_harness.protocols import schema
from research_harness.state.actors import register_actor
from research_harness.state.store import StateStore
from research_harness.tasks import engine as T
from research_harness.validation.integrity import audit_project_integrity
from research_harness.workflows import eca
from research_harness.workflows.adapters import ScriptedModelAdapter
from tests import landscape_fixture as F

ADJ_OK = {"passage_proposition": "As quoted.", "equivalence": "EQUIVALENT", "dropped_conditions": [], "cue_handling": [], "verdict": "SUPPORTED_AS_STATED",
          "required_qualification": [], "rationale": "ok"}


def adjudicator() -> ScriptedModelAdapter:
    return ScriptedModelAdapter(model_id="scripted-adjudicator", default_response=dict(ADJ_OK))


@pytest.fixture
def world(tmp_path):
    w = F.build_world(tmp_path)
    w["calls"] = []
    w["out"] = L.build(w["store"], w["token"], w["rid"], F.reader(calls=w["calls"]))
    w["lid"] = w["out"]["landscape_id"]
    return w


def entry(v, source_id, category, **kw):
    return next(e for e in v["entries"] if e["source_id"] == source_id and e["category"] == category and all(e.get(k) == x for k, x in kw.items()))


# ------------------------------------------------------------------------------------------------ entries and their three layers

def test_every_entry_points_at_a_verified_passage_at_its_source_revision(world):
    w = world
    v = L.view(w["store"], w["lid"])
    assert w["out"]["model_calls"] == 8 and len(w["calls"]) == 8                       # 4 papers x 2 protocols; the abstract-only paper was never read
    assert {e["source_id"] for e in v["entries"]} == {"SRC_PAT", "SRC_QUINN", "SRC_REN1", "SRC_SAM"} and "SRC_ABS" not in json.dumps(v["entries"])
    assert {e["category"] for e in v["entries"]} == {"construct", "mechanism", "design", "measurement", "identifying_assumption", "finding", "boundary",
                                                     "method_limitation", "unresolved_question"}
    for e in v["entries"]:
        ac = e["author_claim"]
        assert ac["status"] == "AUTHOR_ASSERTED" and ac["quote"] and ac["locator"].startswith(e["source_id"])
        p = w["store"].get_object(ac["passage_id"], ac["passage_revision"])
        assert p["object_type"] == "EvidencePassage" and p["content"]["quote_verified"] is True and p["content"]["source_revision"] == e["source_revision"]
        assert p["content"]["quote"] == ac["quote"] and ac["quote"] in corpus.source_text(w["store"], e["source_id"], e["source_revision"])[1]
        assert e["generated_by"] == "model:scripted-landscape-reader" and e["layers"]["author_claim"] == ac


def test_the_three_layers_are_separate_and_a_finding_is_never_shown_as_established(world):
    v = L.view(world["store"], world["lid"])
    f = entry(v, "SRC_PAT", "finding")
    assert f["layers"]["author_claim"]["status"] == "AUTHOR_ASSERTED"
    assert f["layers"]["evidence"] == {"reading": {"value": "AUTHOR_REPORTED", "eca_verdict": None, "disputed": False, "adjudications": [], "reasons": f["layers"]["evidence"]["reading"]["reasons"]},
                                       "inference": {"value": "NOT_ASSESSED"}, "reproduction": {"value": "NOT_ATTEMPTED"}}
    assert f["layers"]["suite_inference"] == ["I001"]
    assert "inference" not in entry(v, "SRC_PAT", "design")["layers"]["evidence"]                       # the dimensions are about findings
    assert "established" not in json.dumps([e["layers"]["evidence"] for e in v["entries"]]).lower()


def test_landscape_validates_and_cannot_be_proposed(world):
    w = world
    jsonschema.validate(w["store"].get_object(w["lid"])["content"], schema.content_schema("Landscape"))
    assert "Landscape" not in schema.PROPOSABLE_TYPES
    assert audit_project_integrity(w["store"])["status"] == "PASS"


def test_a_failed_extraction_stops_the_build_and_writes_nothing(tmp_path):
    w = F.build_world(tmp_path)
    calls = []
    with pytest.raises(KnowledgeError) as e:
        L.build(w["store"], w["token"], w["rid"], F.reader(fail_for={"pat"}, calls=calls))
    assert e.value.code == "EXTRACTION_FAILED" and "SRC_PAT" in e.value.message and len(calls) == 7
    assert L.list_landscapes(w["store"]) == []
    retry = []
    out = L.build(w["store"], w["token"], w["rid"], F.reader(calls=retry))
    assert out["landscape_id"] == "LAND_000001" and retry == [("pat", "RP_LANDSCAPE"), ("pat", "RP_THEORY")]                               # what was extracted before the failure is reused, not paid for again


def test_a_record_whose_quote_is_not_in_the_text_is_rejected_not_stored(tmp_path):
    w = F.build_world(tmp_path)
    bad = {**F.RECORDS, "sam": {"RP_LANDSCAPE": [{"field": "finding", "quote": "A sentence the paper never wrote at all.", "value": "x", "label": F.FEES, "type": "positive"}], "RP_THEORY": []}}
    out = L.build(w["store"], w["token"], w["rid"], F.reader(records=bad))
    v = L.view(w["store"], out["landscape_id"])
    assert not [e for e in v["entries"] if e["source_id"] == "SRC_SAM"] and v["coverage"]["records_rejected_in_extraction"] == 1


# ------------------------------------------------------------------------------------------------ suite inferences

def test_contradiction_between_works_is_derived_by_rule_and_lists_each_sides_conditions(world):
    v = L.view(world["store"], world["lid"])
    c = next(i for i in v["inferences"] if i["kind"] == "contradiction")
    assert c["subkind"] == "opposite_direction" and c["relationship"] == F.REL and c["generated_by"] == "deterministic:landscape.contradictions@1"
    assert c["status"] == "PROVISIONAL" and c["scope_overlap"] == "NOT_ASSESSED" and c["reviews"] == []
    sides = {s["source_id"]: s for s in c["sides"]}
    assert set(sides) == {"SRC_PAT", "SRC_QUINN"} and sides["SRC_PAT"]["direction"] == "negative" and sides["SRC_QUINN"]["direction"] == "positive"
    assert all(s["source_revision"] == 1 and s["passage_id"].startswith("EP_") for s in c["sides"])
    assert len(sides["SRC_PAT"]["conditions"]) == 1 and len(sides["SRC_QUINN"]["conditions"]) == 1       # the boundary each paper states
    assert not [i for i in v["inferences"] if "audit fees" in i["relationship"]]                         # a different relationship contradicts nothing


def test_the_same_relationship_under_different_wording_is_missed_unless_the_researcher_declares_it(tmp_path):
    w = F.build_world(tmp_path)
    reworded = json.loads(json.dumps(F.RECORDS))
    reworded["quinn"]["RP_LANDSCAPE"][1]["label"] = "detection models -> displaced manipulation"
    out = L.build(w["store"], w["token"], w["rid"], F.reader(records=reworded))
    assert L.view(w["store"], out["landscape_id"])["coverage"]["inferences"]["contradiction"] == 0
    with pytest.raises(KnowledgeError, match="ALIAS_GROUP_TOO_SMALL"):
        L.build(w["store"], w["token"], w["rid"], F.reader(records=reworded), aliases=[["only one name"]])
    out2 = L.build(w["store"], w["token"], w["rid"], F.reader(records=reworded), aliases=[[F.REL, "detection models -> displaced manipulation"]])
    v2 = L.view(w["store"], out2["landscape_id"])
    assert v2["coverage"]["inferences"]["contradiction"] == 1 and v2["construct_aliases"] == [[F.REL, "detection models -> displaced manipulation"]]


def test_constructs_defined_in_different_words_are_flagged_for_the_researcher(world):
    v = L.view(world["store"], world["lid"])
    d = next(i for i in v["inferences"] if i["kind"] == "definitions_differ")
    assert d["relationship"] == "earnings manipulation" and {s["source_id"] for s in d["sides"]} == {"SRC_PAT", "SRC_QUINN"} and "researcher" in d["note"]


def test_review_of_an_inference_is_recorded_with_who_and_why_and_does_not_change_what_it_is(world):
    w = world
    with pytest.raises(KnowledgeError, match="REASON_REQUIRED"):
        L.review_inference(w["store"], w["token"], w["lid"], "I001", "CONFIRMED", " ")
    with pytest.raises(KnowledgeError, match="DECISION_UNKNOWN"):
        L.review_inference(w["store"], w["token"], w["lid"], "I001", "SETTLED", "x")
    with pytest.raises(KnowledgeError, match="INFERENCE_UNKNOWN"):
        L.review_inference(w["store"], w["token"], w["lid"], "I099", "CONFIRMED", "x")
    out = L.review_inference(w["store"], w["token"], w["lid"], "I001", "NEEDS_SCOPE", "Different measures; check the populations before calling it a conflict.")
    i = next(x for x in L.view(w["store"], w["lid"])["inferences"] if x["id"] == "I001")
    assert out["revision"] == 2 and i["reviews"][0]["decision"] == "NEEDS_SCOPE" and i["reviews"][0]["by"] and i["status"] == "PROVISIONAL" and i["generated_by"].startswith("deterministic:")
    viewer = register_actor(w["store"], w["token"], actor_id="viewer_1", display_name="a viewer", roles=["importer"])
    with pytest.raises(AuthorizationError):
        L.review_inference(w["store"], viewer["token"], w["lid"], "I001", "DISMISSED", "no")


# ------------------------------------------------------------------------------------------------ versions

def test_default_reading_takes_the_retrieved_version_and_says_a_later_one_was_not_read(world):
    v = L.view(world["store"], world["lid"])
    w = v["works"]["W_REN"]
    assert w["read"] == ["SRC_REN1"] and w["used_as_evidence"] == ["SRC_REN1"] and w["latest_held"] == "SRC_REN2" and w["later_version_not_read"] is True
    assert {x["source_id"]: (x["read"], x["entries"]) for x in w["versions"]} == {"SRC_REN1": (True, 1), "SRC_REN2": (False, 0)}
    assert v["coverage"]["works_with_a_later_version_not_read"] == ["W_REN"]
    assert entry(v, "SRC_REN1", "finding")["direction"] == "positive"                                    # v1's claim, attributed to v1


def test_reading_all_versions_gives_a_version_difference_never_a_contradiction_between_studies(tmp_path):
    w = F.build_world(tmp_path)
    out = L.build(w["store"], w["token"], w["rid"], F.reader(), versions="all")
    v = L.view(w["store"], out["landscape_id"])
    d = [i for i in v["inferences"] if i["relationship"] == F.TONE]
    assert [i["kind"] for i in d] == ["version_difference"] and {s["source_id"] for s in d[0]["sides"]} == {"SRC_REN1", "SRC_REN2"} and len({s["work_id"] for s in d[0]["sides"]}) == 1
    assert v["works"]["W_REN"]["later_version_not_read"] is False and v["works"]["W_REN"]["read"] == ["SRC_REN1", "SRC_REN2"]
    assert {e["source_id"]: e["direction"] for e in v["entries"] if e["label"] == F.TONE} == {"SRC_REN1": "positive", "SRC_REN2": "negative"}
    assert v["coverage"]["inferences"] == {"contradiction": 1, "version_difference": 1, "definitions_differ": 1}


def test_the_latest_version_is_judged_across_the_whole_registry_not_among_the_hits(tmp_path):
    w = F.build_world(tmp_path, search=False)
    corpus.import_metadata_only(w["store"], w["token"], citation_key="ren3", title="Disclosure After Detection Models", year=2026, abstract="The journal version.",
                                document_type="journal_article", review_status="PUBLISHED", first_public_date="2026-06-30", date_evidence="DOI registration (synthetic)",
                                version_id="journal", work_id="W_REN")
    RT.search(w["store"], w["token"], w["rid"])
    out = L.build(w["store"], w["token"], w["rid"], F.reader(), versions="all")
    info = L.view(w["store"], out["landscape_id"])["works"]["W_REN"]
    assert info["latest_held"] == "SRC_REN3" and info["later_version_not_read"] is True and info["read"] == ["SRC_REN1", "SRC_REN2"]       # held as a abstract only: it cannot be read
    ren3 = next(x for x in info["versions"] if x["source_id"] == "SRC_REN3")
    assert ren3["tier"] == "ABSTRACT_ONLY" and ren3["read"] is False and ren3["entries"] == 0


def test_without_public_dates_the_latest_version_cannot_be_told_and_the_landscape_says_so(tmp_path):
    w = F.build_world(tmp_path, search=False)
    for key in ("ren1", "ren2"):                                                            # same work, no dates: replace by undated versions
        corpus.set_excluded(w["store"], w["token"], f"SRC_{key.upper()}", "replaced by an undated copy")
    for key, vid in (("ren1u", "v1"), ("ren2u", "v2")):
        corpus.import_source(w["store"], w["token"], citation_key=key, title="Disclosure After Detection Models", year=2025, text=F.PAPERS["ren2"]["text"], work_id="W_REN", version_id=vid)
    RT.search(w["store"], w["token"], w["rid"])
    out = L.build(w["store"], w["token"], w["rid"], F.reader(records={**F.RECORDS, "ren1u": F.RECORDS["ren1"], "ren2u": F.RECORDS["ren2"]}))
    v = L.view(w["store"], out["landscape_id"])
    assert v["works"]["W_REN"]["later_version_not_read"] == "UNKNOWN" and v["coverage"]["works_where_the_latest_version_cannot_be_told"] == ["W_REN"]
    assert "cannot be told" in v["works"]["W_REN"]["latest_basis"]


# ------------------------------------------------------------------------------------------------ only from a current run

def test_a_run_that_is_not_current_cannot_feed_a_landscape(tmp_path):
    w = F.build_world(tmp_path, search=False)
    with pytest.raises(KnowledgeError, match="RETRIEVAL_NOT_SEARCHED"):
        L.build(w["store"], w["token"], w["rid"], F.reader())
    RT.search(w["store"], w["token"], w["rid"])
    RT.decide_expansions(w["store"], w["token"], w["rid"], rationale="other names", add=("accruals",))
    with pytest.raises(KnowledgeError, match="RETRIEVAL_CONFIG_STALE"):
        L.build(w["store"], w["token"], w["rid"], F.reader())
    RT.search(w["store"], w["token"], w["rid"])
    P.revise_problem(w["store"], w["token"], w["pid"], rationale="the question is about disclosure only", statement="Disclosure by firms after detection models spread is not understood.")
    with pytest.raises(KnowledgeError, match="RETRIEVAL_PROBLEM_STALE"):
        L.build(w["store"], w["token"], w["rid"], F.reader())
    assert L.list_landscapes(w["store"]) == []


def test_a_changed_corpus_needs_an_explicit_choice_and_a_revised_source_cannot_be_read_as_searched(tmp_path):
    w = F.build_world(tmp_path)
    corpus.import_source(w["store"], w["token"], citation_key="late", title="A Late Paper", text="A Late Paper\n\nAbstract\nDetection.\n\n1 Introduction\nDetection models.\n")
    with pytest.raises(KnowledgeError, match="RETRIEVAL_CORPUS_STALE"):
        L.build(w["store"], w["token"], w["rid"], F.reader())
    out = L.build(w["store"], w["token"], w["rid"], F.reader(), allow_stale_corpus=True)
    cur = L.view(w["store"], out["landscape_id"])["currency"]
    assert cur["corpus"]["status"] == "STALE_FOR_CORPUS" and cur["all_current"] is False
    with pytest.raises(KnowledgeError, match="LANDSCAPE_CORPUS_STALE"):
        L.require_current(w["store"], out["landscape_id"])
    assert L.require_current(w["store"], out["landscape_id"], allow_stale_corpus=True)["landscape_id"] == out["landscape_id"]
    corpus.set_excluded(w["store"], w["token"], "SRC_SAM", "check")
    corpus.set_excluded(w["store"], w["token"], "SRC_SAM", None)                            # a new revision of a source that was read
    with pytest.raises(KnowledgeError, match="LANDSCAPE_SOURCE_CHANGED"):
        L.build(w["store"], w["token"], w["rid"], F.reader(), allow_stale_corpus=True)


def test_the_landscape_knows_when_its_inputs_move_after_it_was_built(world):
    w = world
    assert L.view(w["store"], w["lid"])["currency"]["all_current"] is True
    assert L.require_current(w["store"], w["lid"])["landscape_id"] == w["lid"]
    RT.review(w["store"], w["token"], w["rid"], "irrelevant", source_id="SRC_SAM", reason="not about the problem")
    cur = L.view(w["store"], w["lid"])["currency"]
    assert cur["retrieval"]["status"] == "REVISED" and cur["all_current"] is False
    with pytest.raises(KnowledgeError, match="LANDSCAPE_RETRIEVAL_REVISED"):
        L.require_current(w["store"], w["lid"])
    P.revise_problem(w["store"], w["token"], w["pid"], rationale="reframed", statement="Disclosure by firms after detection models spread is not understood.")
    assert L.view(w["store"], w["lid"])["currency"]["problem"]["status"] == "STALE"
    with pytest.raises(KnowledgeError, match="LANDSCAPE_PROBLEM_STALE"):
        L.require_current(w["store"], w["lid"])


# ------------------------------------------------------------------------------------------------ evidence dimensions (A5)

def confirm_reading(w, entry_id):
    st = eca.resolve_statement(w["store"], w["lid"], element=entry_id)
    r = eca.adjudicate(w["store"], w["token"], st, eca.basis_passage(w["store"], w["lid"], entry_id), adjudicator())
    eca.confirm(w["store"], w["token"], r["adjudication_id"], verdict="SUPPORTED_AS_STATED", rationale="The restatement is a fair reading of the quote.")


def test_landscape_entries_are_statements_eca_can_adjudicate_and_it_moves_only_the_reading(world):
    w = world
    assert any(s["object_id"] == w["lid"] and s["stratum"] == "Landscape:finding" for s in eca.iter_statements(w["store"]))
    fid = entry(L.view(w["store"], w["lid"]), "SRC_PAT", "finding")["id"]
    confirm_reading(w, fid)
    ev = entry(L.view(w["store"], w["lid"]), "SRC_PAT", "finding")["layers"]["evidence"]
    assert ev["reading"]["value"] == "READING_CONFIRMED" and ev["reading"]["eca_verdict"] == "SUPPORTED_AS_STATED"
    assert ev["inference"] == {"value": "NOT_ASSESSED"} and ev["reproduction"] == {"value": "NOT_ATTEMPTED"}        # a correct reading says nothing about the design
    other = entry(L.view(w["store"], w["lid"]), "SRC_QUINN", "finding")["layers"]["evidence"]["reading"]["value"]
    assert other == "AUTHOR_REPORTED"


def test_a_model_proposal_alone_does_not_confirm_the_reading(world):
    w = world
    fid = entry(L.view(w["store"], w["lid"]), "SRC_PAT", "finding")["id"]
    eca.adjudicate(w["store"], w["token"], eca.resolve_statement(w["store"], w["lid"], element=fid), eca.basis_passage(w["store"], w["lid"], fid), adjudicator())
    assert entry(L.view(w["store"], w["lid"]), "SRC_PAT", "finding")["layers"]["evidence"]["reading"]["value"] == "AUTHOR_REPORTED"


CRIT_OK = [{"criterion": "Parallel pre-trends hold in an event study", "basis": "Roth et al. on difference-in-differences with staggered adoption", "methodological_literature": True, "met": "yes"}]
THREAT_OK = [{"threat": "Concurrent regulatory changes", "status": "addressed"}]


def test_a_method_assessment_is_the_researchers_and_its_verdict_is_capped_by_the_rule(world):
    w = world
    fid = entry(L.view(w["store"], w["lid"]), "SRC_PAT", "finding")["id"]
    with pytest.raises(KnowledgeError, match="REASON_REQUIRED"):
        L.assess_method(w["store"], w["token"], w["lid"], fid, verdict="LIMITED", criteria=CRIT_OK, threats=[], rationale=" ")
    with pytest.raises(KnowledgeError, match="CRITERION_BASIS_REQUIRED"):
        L.assess_method(w["store"], w["token"], w["lid"], fid, verdict="LIMITED", criteria=[{"criterion": "x", "met": "yes"}], threats=[], rationale="r")
    with pytest.raises(KnowledgeError, match="NOT_SUPPORTED_NEEDS_A_FAILED_CRITERION"):
        L.assess_method(w["store"], w["token"], w["lid"], fid, verdict="NOT_SUPPORTED", criteria=CRIT_OK, threats=[], rationale="r")
    not_a_finding = entry(L.view(w["store"], w["lid"]), "SRC_PAT", "design")["id"]
    with pytest.raises(KnowledgeError, match="ENTRY_NOT_A_FINDING"):
        L.assess_method(w["store"], w["token"], w["lid"], not_a_finding, verdict="LIMITED", criteria=CRIT_OK, threats=[], rationale="r")
    # no methodological-literature basis: asked for SUPPORTED, stored as LIMITED with the blocker named
    weak = [{**CRIT_OK[0], "methodological_literature": False}]
    capped = L.assess_method(w["store"], w["token"], w["lid"], fid, verdict="SUPPORTED_WITHIN_SCOPE", criteria=weak, threats=THREAT_OK, rationale="Looks fine to me.")
    assert capped["verdict"] == "LIMITED" and any("methodological literature" in b for b in capped["blockers"])
    open_threat = L.assess_method(w["store"], w["token"], w["lid"], fid, verdict="SUPPORTED_WITHIN_SCOPE", criteria=CRIT_OK,
                                  threats=[{"threat": "Anticipation", "status": "open"}], rationale="One threat still open.")
    assert open_threat["verdict"] == "LIMITED" and any("open" in b for b in open_threat["blockers"])
    ok = L.assess_method(w["store"], w["token"], w["lid"], fid, verdict="SUPPORTED_WITHIN_SCOPE", criteria=CRIT_OK, threats=THREAT_OK, rationale="Design permits the inference within listed US firms.")
    assert ok["verdict"] == "SUPPORTED_WITHIN_SCOPE" and ok["blockers"] == []
    ev = entry(L.view(w["store"], w["lid"]), "SRC_PAT", "finding")["layers"]["evidence"]
    assert ev["inference"]["value"] == "METHOD_CRITICALLY_ASSESSED" and ev["inference"]["verdict"] == "SUPPORTED_WITHIN_SCOPE" and ev["inference"]["by"]
    assert ev["reading"]["value"] == "AUTHOR_REPORTED" and ev["reproduction"]["value"] == "NOT_ATTEMPTED"       # the dimensions do not follow from one another
    flawed = L.assess_method(w["store"], w["token"], w["lid"], fid, verdict="NOT_SUPPORTED", threats=[],
                             criteria=[{"criterion": "Treatment timing is exogenous", "basis": "Goodman-Bacon decomposition", "methodological_literature": True, "met": "no"}],
                             rationale="Adoption timing responds to prior manipulation.")
    assert flawed["verdict"] == "NOT_SUPPORTED"
    viewer = register_actor(w["store"], w["token"], actor_id="viewer_2", display_name="a viewer", roles=["importer"])
    with pytest.raises(AuthorizationError):
        L.assess_method(w["store"], viewer["token"], w["lid"], fid, verdict="LIMITED", criteria=CRIT_OK, threats=[], rationale="r")


def test_reproduction_is_read_from_the_package_and_does_not_imply_the_inference(world):
    w = world
    fid = entry(L.view(w["store"], w["lid"]), "SRC_PAT", "finding")["id"]
    pkg = w["tmp"] / "pkg"
    pkg.mkdir()
    (pkg / "panel.csv").write_bytes(b"firm,label\na,1\nb,0\nc,0\nd,0\n")
    add_replication_package(w["store"], w["token"], package_id="PKG_PAT", label="Pat's package", directory=pkg, files=["panel.csv"],
                            expected_outputs=[{"id": "pos", "file": "panel.csv", "column": "label", "stat": "positive_rate", "expected": 0.25, "tolerance": 1e-9}],
                            entitlement={"status": "confirmed", "license": "synthetic", "basis": "test"})
    with pytest.raises(KnowledgeError, match="REPRODUCTION_NOT_RECORDED"):
        L.link_reproduction(w["store"], w["token"], w["lid"], fid, kind="package_run", package_id="PKG_PAT", outputs=["pos"], reason="Pat's code")
    record_reproduction(w["store"], w["token"], "PKG_PAT")
    with pytest.raises(KnowledgeError, match="OUTPUTS_UNKNOWN"):
        L.link_reproduction(w["store"], w["token"], w["lid"], fid, kind="package_run", package_id="PKG_PAT", outputs=["nope"], reason="x")
    with pytest.raises(KnowledgeError, match="NOT_A_REPLICATION_PACKAGE"):
        L.link_reproduction(w["store"], w["token"], w["lid"], fid, kind="package_run", package_id="SRC_PAT", outputs=["pos"], reason="x")
    L.link_reproduction(w["store"], w["token"], w["lid"], fid, kind="package_run", package_id="PKG_PAT", outputs=["pos"], reason="The package reproduces the headline table.")
    ev = entry(L.view(w["store"], w["lid"]), "SRC_PAT", "finding")["layers"]["evidence"]
    assert ev["reproduction"]["value"] == "REPRODUCED_WITHIN_TOLERANCE" and ev["reproduction"]["package"] == "PKG_PAT"
    assert ev["inference"] == {"value": "NOT_ASSESSED"} and ev["reading"]["value"] == "AUTHOR_REPORTED"           # reproducing code is not a method assessment
    other = entry(L.view(w["store"], w["lid"]), "SRC_QUINN", "finding")["id"]
    nd = L.link_reproduction(w["store"], w["token"], w["lid"], other, kind="new_data", reference="Quinn et al. (2026), replication on EU firms", reason="I know of this replication.")
    assert nd["record"]["checked_by_suite"] is False
    assert entry(L.view(w["store"], w["lid"]), "SRC_QUINN", "finding")["layers"]["evidence"]["reproduction"]["value"] == "REPLICATED_NEW_DATA"
    with pytest.raises(KnowledgeError, match="REASON_REQUIRED"):
        L.link_reproduction(w["store"], w["token"], w["lid"], other, kind="new_data", reference=" ", reason="r")


def test_the_researchers_records_are_carried_to_a_rebuild_only_where_nothing_they_concern_changed(world):
    w = world
    v = L.view(w["store"], w["lid"])
    pat, quinn = entry(v, "SRC_PAT", "finding")["id"], entry(v, "SRC_QUINN", "finding")["id"]
    L.assess_method(w["store"], w["token"], w["lid"], pat, verdict="LIMITED", criteria=CRIT_OK, threats=[], rationale="First look.")
    L.assess_method(w["store"], w["token"], w["lid"], quinn, verdict="LIMITED", criteria=CRIT_OK, threats=[], rationale="First look.")
    L.review_inference(w["store"], w["token"], w["lid"], "I001", "CONFIRMED", "They conflict.")
    same = L.build(w["store"], w["token"], w["rid"], F.reader(), carry_from=w["lid"])
    v2 = L.view(w["store"], same["landscape_id"])
    assert entry(v2, "SRC_PAT", "finding")["layers"]["evidence"]["inference"]["value"] == "METHOD_CRITICALLY_ASSESSED"
    assert entry(v2, "SRC_QUINN", "finding")["layers"]["evidence"]["inference"]["value"] == "METHOD_CRITICALLY_ASSESSED"
    assert v2["inferences"][0]["reviews"][0]["decision"] == "CONFIRMED" and v2["inferences"][0]["reviews"][0]["carried_from"] == w["lid"] and v2["carried_from"] == w["lid"]
    changed = json.loads(json.dumps(F.RECORDS))
    changed["quinn"]["RP_LANDSCAPE"][1]["value"] = "Detection model use raises real earnings management (restated wording)."
    out = L.build(w["store"], w["token"], w["rid"], F.reader(records=changed), carry_from=w["lid"], reuse=False)
    v3 = L.view(w["store"], out["landscape_id"])
    assert entry(v3, "SRC_PAT", "finding")["layers"]["evidence"]["inference"]["value"] == "METHOD_CRITICALLY_ASSESSED"
    assert entry(v3, "SRC_QUINN", "finding")["layers"]["evidence"]["inference"]["value"] == "NOT_ASSESSED"           # its wording changed: the old assessment no longer applies
    assert v3["inferences"][0]["reviews"] == []                                                                       # nor does the decision about a pair that includes it


# ------------------------------------------------------------------------------------------------ export

def test_the_export_shows_source_revisions_dimensions_and_inferences_and_verifies(world):
    w = world
    fid = entry(L.view(w["store"], w["lid"]), "SRC_PAT", "finding")["id"]
    confirm_reading(w, fid)
    exp = build_landscape_export(w["store"], w["lid"])
    assert exp.files.keys() == {"research_landscape.md", "evidence_table.csv", "works_and_versions.csv"} and exp.not_available == []
    assert build_landscape_export(w["store"], w["lid"]).files == exp.files and build_landscape_export(w["store"], w["lid"]).closure_digest == exp.closure_digest      # deterministic
    out = write_export(w["store"], exp, w["tmp"] / "land_export")
    md = (w["tmp"] / "land_export" / "research_landscape.md").read_text(encoding="utf-8")
    assert "SRC_PAT@1" in md and "READING_CONFIRMED" in md and "AUTHOR_REPORTED" in md and "NOT_ASSESSED" in md and "NOT_ATTEMPTED" in md
    assert "contradiction (opposite_direction)" in md and "not reviewed" in md and "W_REN" in md and "later than what was read" in md and "SRC_ABS" not in md
    assert "possibly relevant papers were **not read**" in md and "CURRENT" in md
    assert F.S["pat_find"][:40] in md
    csv_text = (w["tmp"] / "land_export" / "evidence_table.csv").read_text(encoding="utf-8")
    assert csv_text.startswith("entry,type,source_id,source_revision,work_id,version_id,relationship,direction,text,quote,where,reading,inference,reproduction,generated_by\n")
    assert verify_export(w["store"], w["tmp"] / "land_export", lambda scope: build_landscape_export(w["store"], scope["landscape"]))["status"] == "VERIFIED"
    assert out["closure_digest"] == exp.closure_digest


def test_a_stale_landscape_says_so_in_its_export(world):
    w = world
    P.revise_problem(w["store"], w["token"], w["pid"], rationale="reframed", statement="Disclosure by firms after detection models spread is not understood.")
    md = build_landscape_export(w["store"], w["lid"]).files["research_landscape.md"].decode("utf-8")
    assert "problem input (r1): **STALE**" in md and "do not build on this landscape" in md


# ------------------------------------------------------------------------------------------------ a task, and the command line

def adapter_both() -> ScriptedModelAdapter:
    read = F.reader().custom_handler

    def handler(system: str, user: str):
        return dict(ADJ_OK) if "adversarial checker" in system else read(system, user)
    return ScriptedModelAdapter(model_id="scripted-both", custom_handler=handler)


def test_the_build_landscape_goal_stops_at_the_researchers_review_and_exports(tmp_path):
    w = F.build_world(tmp_path)
    store, token = w["store"], w["token"]
    tid = T.start_task(store, token, goal="build_landscape", statement="What does the literature say about detection models?", params={"retrieval_id": w["rid"], "out": str(tmp_path / "task_export")},
                       max_tokens=5_000_000)["task_id"]
    T.approve_task(store, token, tid)
    assert T.run_task(store, token, tid, adapter=adapter_both())["state"] == "WAITING_FOR_RESEARCHER"          # S01: confirm the reading list
    T.decide(store, token, tid, "S01", "This is the reading list I mean to read.")
    second = T.run_task(store, token, tid, adapter=adapter_both())
    assert second["state"] == "WAITING_FOR_RESEARCHER"
    view = T.task_view(store, tid)
    assert {s["step_id"]: s["status"] for s in view["steps"]} == {"S01": "DECIDED", "S02": "DONE", "S03": "AWAITING_DECISION", "S04": "WAITING_ON_UPSTREAM"}
    assert view["spent"]["llm_calls"] == 8
    lid = L.list_landscapes(store)[0]["landscape_id"]
    T.decide(store, token, tid, "S03", "I read the contradiction and the later version note.")
    done = T.run_task(store, token, tid, adapter=adapter_both())
    assert done["state"] == "COMPLETED" and (tmp_path / "task_export" / "research_landscape.md").is_file() and lid == "LAND_000001"


def test_a_stale_run_blocks_the_task_step_instead_of_building_a_landscape(tmp_path):
    w = F.build_world(tmp_path)
    store, token = w["store"], w["token"]
    tid = T.start_task(store, token, goal="build_landscape", statement="x", params={"retrieval_id": w["rid"]}, max_tokens=5_000_000)["task_id"]
    T.approve_task(store, token, tid)
    T.run_task(store, token, tid, adapter=adapter_both())
    T.decide(store, token, tid, "S01", "yes")
    P.revise_problem(store, token, w["pid"], rationale="reframed", statement="Disclosure by firms after detection models spread is not understood.")
    res = T.run_task(store, token, tid, adapter=adapter_both())
    assert res["state"] != "COMPLETED" and L.list_landscapes(store) == []
    assert "RETRIEVAL_PROBLEM_STALE" in json.dumps(T.task_view(store, tid), default=str)


def test_landscape_cli_end_to_end(tmp_path, monkeypatch, capsys):
    from research_harness import cli_knowledge
    w = F.build_world(tmp_path)
    tf = tmp_path / "token"
    tf.write_text(w["token"], encoding="utf-8")
    monkeypatch.setattr(cli_knowledge, "_adapter", lambda args: F.reader())
    base = ["--project", "PRJ_LAND", "--home", str(w["home"])]
    auth = [*base, "--token-file", str(tf)]

    def j(*argv, rc=0):
        assert main([*map(str, argv), "--json"]) == rc, capsys.readouterr().err
        return json.loads(capsys.readouterr().out)

    built = j("landscape", "build", w["rid"], "--same", f"{F.REL}={F.REL.upper()}", *auth)
    lid = built["landscape_id"]
    assert built["entries"] == 15 and built["inferences"]["contradiction"] == 1
    assert main(["landscape", "show", lid, *base]) == 0
    shown = capsys.readouterr().out
    assert "SRC_PAT@1" in shown and "reading AUTHOR_REPORTED · inference NOT_ASSESSED · reproduction NOT_ATTEMPTED" in shown and "contradiction (opposite_direction)" in shown
    assert "W_REN" in shown and "later than what was read" in shown
    assert j("landscape", "list", *base)["landscapes"][0]["landscape_id"] == lid
    assert j("landscape", "review", lid, "I001", "NEEDS_SCOPE", "--reason", "check populations", *auth)["decision"] == "NEEDS_SCOPE"
    fid = next(e["id"] for e in L.view(w["store"], lid)["entries"] if e["source_id"] == "SRC_PAT" and e["category"] == "finding")
    crit = tmp_path / "criteria.json"
    crit.write_text(json.dumps(CRIT_OK), encoding="utf-8")
    a = j("landscape", "assess", lid, fid, "--verdict", "SUPPORTED_WITHIN_SCOPE", "--criteria", str(crit), "--threats", json.dumps(THREAT_OK), "--rationale", "within listed US firms", *auth)
    assert a["verdict"] == "SUPPORTED_WITHIN_SCOPE"
    out_dir = tmp_path / "cli_export"
    exported = j("export", "landscape", lid, "--out", out_dir, *base)
    assert "research_landscape.md" in exported["files"]
    assert main(["export", "verify", str(out_dir), *base]) == 0 and capsys.readouterr().out.strip() == "VERIFIED"
    assert main(["landscape", "build", w["rid"], "--same", "only-one", *auth]) == 1
    assert "ALIAS_GROUP_TOO_SMALL" in capsys.readouterr().err
