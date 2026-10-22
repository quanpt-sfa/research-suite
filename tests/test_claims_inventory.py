"""WP6.2: the claim inventory. Synthetic papers and scripted readers: `SCHEMA_TESTED`. What a real model would extract is not tested here."""
from __future__ import annotations

import pytest

from research_harness.knowledge import claims as CL
from research_harness.knowledge.common import KnowledgeError
from research_harness.workflows import eca
from research_harness.workflows import reading_protocols as rp
from tests import claims_fixtures as F


@pytest.fixture()
def w(tmp_path):
    return F.make_store(tmp_path)


def build_cr(w, adapter=None, **kw):
    return CL.build(w["store"], w["token"], [w["ids"]["cr"]], adapter or F.reader(), **kw)


def claim_with(v, fragment):
    return next(c for c in v["claims"] if fragment in c["wording"]["quote"])


# ------------------------------------------------------------------------------------------------ provenance and identity

def test_every_extracted_claim_has_a_verified_wording_a_source_revision_a_model_and_a_protocol(w):
    out = build_cr(w)
    v = CL.view(w["store"], out["inventory_id"])
    obj = w["store"].get_object(out["inventory_id"])
    assert obj["object_type"] == "ClaimInventory" and len(v["claims"]) == len(F.cr_records()) == out["claims"]
    src = w["ids"]["cr"]
    for c in v["claims"]:
        wd = c["wording"]
        assert wd["status"] == "QUOTE_VERIFIED" and wd["quote"] in F.CR_TEXT and F.CR_TEXT[wd["char_start"]:wd["char_end"]] == wd["quote"]
        assert (c["source_id"], c["source_revision"]) == (src, 1) and c["work_id"] == src
        assert c["generated_by"] == "model:scripted-claims-reader" and c["protocol"] == "RP_CLAIMS@1" and c["author_stated"] is True
        passage = w["store"].get_object(wd["passage_id"], wd["passage_revision"])
        assert passage["content"]["quote"] == wd["quote"] and passage["content"]["source_revision"] == 1
    with w["store"].read() as conn:
        edges = {(r[0], r[1]) for r in conn.execute("SELECT relation, target_id FROM edges WHERE source_id=?", (out["inventory_id"],)).fetchall()}
    assert ("derived_from", src) in edges and ("derived_from", v["claims"][0]["wording"]["passage_id"]) in edges


def test_a_claim_whose_quote_is_not_in_the_paper_is_rejected_and_never_becomes_an_author_claim(w):
    recs = F.cr_records() + [F.rec("theoretical_proposition", "Attention is never scarce in any channel anywhere.", F.OWN)]
    out = build_cr(w, F.reader({"cr": {"RP_CLAIMS": recs}}))
    v = CL.view(w["store"], out["inventory_id"])
    assert len(v["claims"]) == len(F.cr_records())
    assert any(r["reason"] == "quote_not_in_text_shown" for r in v["sources"][0]["extraction"]["rejected"])
    assert all(c["author_stated"] and c["wording"]["status"] == "QUOTE_VERIFIED" for c in v["claims"])


def test_two_fields_may_not_claim_the_same_sentence(w):
    recs = F.cr_records() + [F.rec("claimed_contribution", F.CR["own1"], F.OWN)]
    out = build_cr(w, F.reader({"cr": {"RP_CLAIMS": recs}}))
    v = CL.view(w["store"], out["inventory_id"])
    assert sum(1 for c in v["claims"] if c["wording"]["quote"] == F.CR["own1"]) == 1
    assert any(r["reason"] == "span_already_used_by_another_field" for r in v["sources"][0]["extraction"]["rejected"])


# ------------------------------------------------------------------------------------------------ attribution

def test_a_sentence_that_reports_earlier_work_stays_reported_and_is_not_given_to_the_cited_authors(w):
    from research_harness.knowledge import corpus
    alder = corpus.import_source(w["store"], w["token"], citation_key="alder2018", title="Attention as a Group Resource", year=2018, text="Attention is limited.\n")["object_id"]
    out = build_cr(w)
    v = CL.view(w["store"], out["inventory_id"])
    c = claim_with(v, "Alder (2018) argues")
    assert c["attribution"]["value"] == F.REP and c["attribution_status"] == "MODEL_ASSIGNED"
    assert c["cited_works"] == [{"citation": "Alder (2018)", "state": "REPORTED_BY_CITING_AUTHOR", "source_id": None, "checked_against_cited_source": False}]
    assert all(x["source_id"] != alder for x in v["claims"])                      # the held paper by that name was not read, so nothing is attributed to it
    assert v["sources"] and all(s["source_id"] != alder for s in v["sources"])


def test_a_paragraph_that_moves_from_describing_earlier_work_to_arguing_its_own_gets_different_attributions(w):
    v = CL.view(w["store"], build_cr(w)["inventory_id"])
    got = [claim_with(v, f)["attribution"]["value"] for f in ("Alder (2018) argues", "However, Alder's account", "We argue that communicative participation")]
    assert got == [F.REP, F.CRIT, F.OWN]


def test_a_model_attribution_that_disagrees_with_the_citation_cues_is_flagged_for_review_and_is_not_changed(w):
    recs = F.cr_records()
    recs[1] = F.rec("empirical_finding", F.CR["rep2"], F.OWN, direction="positive")             # a sentence of the form "(Brandt, 2016)" labelled as the authors' own
    v = CL.view(w["store"], build_cr(w, F.reader({"cr": {"RP_CLAIMS": recs}}))["inventory_id"])
    c = claim_with(v, "Previous studies have established")
    assert c["attribution"]["value"] == F.OWN                                                    # never overwritten by a cue
    flags = CL.attribution_audit(v)
    assert any(f["claim"] == c["id"] and f["flag"] == "CITATION_CUE_BUT_LABELLED_OWN" for f in flags)
    assert not any(f["claim"] == claim_with(v, "We argue that communicative")["id"] for f in flags)


def test_the_model_cannot_assign_a_researcher_or_a_model_interpretation_through_extraction(w):
    recs = F.cr_records() + [F.rec("assumption", F.CR["evid1"], "RESEARCHER_INTERPRETATION")]
    v = CL.view(w["store"], build_cr(w, F.reader({"cr": {"RP_CLAIMS": recs}}))["inventory_id"])
    assert all(c["attribution"]["value"] in ("AUTHOR_OWN_CLAIM", "AUTHOR_REPORTS_PRIOR_WORK", "AUTHOR_CRITIQUES_PRIOR_WORK") for c in v["claims"])
    assert any(r["reason"] == "type_not_allowed" for r in v["sources"][0]["extraction"]["rejected"])


def test_a_record_without_an_attribution_is_kept_as_not_assigned_never_defaulted_to_the_authors(w):
    recs = F.cr_records()
    recs[3] = F.rec("theoretical_proposition", F.CR["own1"], None)
    v = CL.view(w["store"], build_cr(w, F.reader({"cr": {"RP_CLAIMS": recs}}))["inventory_id"])
    c = claim_with(v, "We argue that communicative")
    assert c["attribution"]["value"] is None and c["attribution_status"] == "NOT_ASSIGNED"
    assert any(f["claim"] == c["id"] and f["flag"] == "ATTRIBUTION_NOT_ASSIGNED" for f in CL.attribution_audit(v))


def test_a_researcher_correction_changes_what_is_shown_and_keeps_the_models_original(w):
    out = build_cr(w)
    c0 = claim_with(CL.view(w["store"], out["inventory_id"]), "Previous studies have established")
    CL.review_claim(w["store"], w["token"], out["inventory_id"], c0["id"], "CORRECTED", "the sentence only describes earlier studies", corrections={"attribution": F.CRIT})
    c1 = claim_with(CL.view(w["store"], out["inventory_id"]), "Previous studies have established")
    assert c1["attribution"]["value"] == F.CRIT and c1["attribution_status"] == "RESEARCHER_SET"
    assert c1["attribution"]["model_value"] == F.REP and c1["reviews"][-1]["decision"] == "CORRECTED" and c1["reviews"][-1]["by"] == "researcher"
    with pytest.raises(KnowledgeError) as e:
        CL.review_claim(w["store"], w["token"], out["inventory_id"], c0["id"], "CORRECTED", "x", corrections={"attribution": "AUTHOR_VERIFIED_FACT"})
    assert e.value.code == "CORRECTION_NOT_ALLOWED"
    with pytest.raises(KnowledgeError) as e2:
        CL.review_claim(w["store"], w["token"], out["inventory_id"], c0["id"], "CONFIRMED", "")
    assert e2.value.code == "REASON_REQUIRED"


def test_a_cited_work_is_linked_to_a_held_source_by_the_researcher_and_is_still_not_checked(w):
    from research_harness.knowledge import corpus
    alder = corpus.import_source(w["store"], w["token"], citation_key="alder2018", title="Attention as a Group Resource", year=2018, text="Attention is limited.\n")["object_id"]
    out = build_cr(w)
    c = claim_with(CL.view(w["store"], out["inventory_id"]), "Alder (2018) argues")
    CL.link_cited_source(w["store"], w["token"], out["inventory_id"], c["id"], "Alder (2018)", alder)
    c2 = claim_with(CL.view(w["store"], out["inventory_id"]), "Alder (2018) argues")
    assert c2["cited_works"][0]["state"] == "CITED_SOURCE_LINKED" and c2["cited_works"][0]["source_id"] == alder
    assert c2["cited_works"][0]["checked_against_cited_source"] is False
    with pytest.raises(KnowledgeError) as e:
        CL.link_cited_source(w["store"], w["token"], out["inventory_id"], c["id"], "Not cited here (1999)", alder)
    assert e.value.code == "CITATION_NOT_IN_CLAIM"


def test_researcher_and_model_interpretations_have_no_wording_and_are_never_author_stated(w):
    out = build_cr(w)
    base = claim_with(CL.view(w["store"], out["inventory_id"]), "We argue that communicative participation")
    CL.add_interpretation(w["store"], w["token"], out["inventory_id"], text="The argument presupposes that effort is the only cue to engagement.", attribution="RESEARCHER_INTERPRETATION",
                          about=[base["id"]], rationale="my reading of section 2")
    CL.add_interpretation(w["store"], w["token"], out["inventory_id"], text="Recipients are assumed to be unable to infer effort from timing.", attribution="MODEL_PROPOSED_INTERPRETATION",
                          about=[base["id"]], rationale="a model's proposal", premises=["the paper gives no timing cue"], missing_information=["whether the paper discusses timing"],
                          generated_by="model:scripted-critic")
    v = CL.view(w["store"], out["inventory_id"])
    extra = [c for c in v["claims"] if c["attribution"]["value"] in ("RESEARCHER_INTERPRETATION", "MODEL_PROPOSED_INTERPRETATION")]
    assert len(extra) == 2
    for c in extra:
        assert c["author_stated"] is False and c["wording"] is None and c["evidence"] == [] and c["about"] == [base["id"]]
    with pytest.raises(KnowledgeError) as e:
        CL.add_interpretation(w["store"], w["token"], out["inventory_id"], text="x", attribution="AUTHOR_OWN_CLAIM", about=[base["id"]], rationale="r")
    assert e.value.code == "ATTRIBUTION_NOT_ALLOWED_FOR_INTERPRETATION"
    with pytest.raises(KnowledgeError) as e2:
        CL.add_interpretation(w["store"], w["token"], out["inventory_id"], text="x", attribution="MODEL_PROPOSED_INTERPRETATION", about=[base["id"]], rationale="r", generated_by="model:m")
    assert e2.value.code == "MISSING_INFORMATION_REQUIRED"                       # a model's proposed interpretation must say what is missing (a list, empty only if nothing is)


# ------------------------------------------------------------------------------------------------ fields: stated versus inferred, nothing defaulted, compound claims

def test_an_attribute_is_stated_only_if_its_words_are_in_the_quote_otherwise_it_is_model_inferred_and_absent_ones_are_absent(w):
    recs = F.cr_records()
    recs[7] = F.rec("theoretical_proposition", F.CR["comp1"], F.OWN, scope={"setting": "small teams", "period": "the last decade"}, constructs=["visibility"],
                    premises=["messages are scarce", "senders want to be noticed"], granularity="COMPOUND", components=[{"text": "a", "conditions": ["small teams"]}])
    v = CL.view(w["store"], build_cr(w, F.reader({"cr": {"RP_CLAIMS": recs}}))["inventory_id"])
    c = claim_with(v, "We propose that participation raises visibility")
    assert c["scope"]["setting"] == {"value": "small teams", "basis": "STATED_IN_QUOTE"}
    assert c["scope"]["period"] == {"value": "the last decade", "basis": "MODEL_INFERRED"}
    assert {p["text"]: p["basis"] for p in c["premises"]} == {"messages are scarce": "STATED_IN_QUOTE", "senders want to be noticed": "MODEL_INFERRED"}
    assert "population" not in c["scope"] and c["mechanism"] is None and c["direction"] is None            # nothing was filled in
    plain = claim_with(v, "This paper contributes")
    assert plain["scope"] == {} and plain["premises"] == [] and plain["boundary_conditions"] == [] and plain["constructs"] == []


def test_a_compound_claim_stays_one_claim_and_keeps_its_conditions(w):
    v = CL.view(w["store"], build_cr(w)["inventory_id"])
    comps = [c for c in v["claims"] if "We propose that participation raises visibility" in c["wording"]["quote"]]
    assert len(comps) == 1
    c = comps[0]
    assert c["granularity"] == "COMPOUND" and [x["conditions"] for x in c["components"]] == [[], ["small teams", "messages are scarce"]]
    assert "but only when messages are scarce" in c["wording"]["quote"]                                           # the wording keeps the condition, whatever the restatement does


def test_an_invalid_attribute_loses_that_attribute_and_not_the_record_or_the_run(w):
    recs = F.cr_records()
    recs[4] = F.rec("gap_statement", F.CR["gap1"], F.OWN, granularity="HALF", constructs="not a list")
    out = build_cr(w, F.reader({"cr": {"RP_CLAIMS": recs}}))
    v = CL.view(w["store"], out["inventory_id"])
    c = claim_with(v, "No account yet explains")
    assert c["granularity"] == "ATOMIC" and c["constructs"] == []
    assert any(r["reason"] == "attributes_invalid_dropped" for r in v["sources"][0]["extraction"]["rejected"]) and len(v["claims"]) == len(recs)


def test_the_roles_are_kept_apart_from_the_attribution_and_the_risk_tier_follows_what_the_claim_is_used_for(w):
    v = CL.view(w["store"], build_cr(w)["inventory_id"])
    roles = {claim_with(v, f)["role"] for f in ("This paper contributes", "No account yet", "We define", "We assume", "The argument is conceptual")}
    assert roles == {"CLAIMED_CONTRIBUTION", "GAP_STATEMENT", "DEFINITION", "ASSUMPTION", "LIMITATION"}
    assert claim_with(v, "This paper contributes")["risk_tier"] == "HIGH" and claim_with(v, "We define")["risk_tier"] == "STANDARD"
    stmt = eca.resolve_statement(w["store"], v["inventory_id"], element=claim_with(v, "We define")["id"])        # the restatement is what ECA is asked about
    assert stmt.text == "Participation is contributing a message to a shared channel." and stmt.generated_by == "model:scripted-claims-reader"
    assert any(s["object_id"] == v["inventory_id"] for s in eca.iter_statements(w["store"]))


# ------------------------------------------------------------------------------------------------ several passages for one claim

def test_a_claim_can_rest_on_several_passages_each_with_its_role_and_reason_and_each_verified(w):
    v = CL.view(w["store"], build_cr(w)["inventory_id"])
    c = claim_with(v, "We argue that communicative participation")
    assert [(e["link_role"], e["derivation"]) for e in c["evidence"]] == [("WORDING", "extraction"), ("QUALIFIER", "extraction")]
    q = c["evidence"][1]
    assert q["quote"] == F.CR["own1_q"] and q["why_needed"] == "states when the separation holds"
    assert F.CR_TEXT[q["char_start"]:q["char_end"]] == q["quote"] and q["passage_id"] != c["evidence"][0]["passage_id"]
    assert w["store"].get_object(q["passage_id"], q["passage_revision"])["content"]["attributes"]["extra_of"] == c["wording"]["passage_id"]


def test_an_extra_quote_that_is_not_in_the_paper_is_dropped_and_listed_but_the_claim_stays(w):
    recs = F.cr_records()
    recs[3] = F.rec("theoretical_proposition", F.CR["own1"], F.OWN, extra=[{"quote": "A sentence the paper never wrote at all.", "role": "QUALIFIER", "why": "invented"},
                                                                          {"quote": F.CR["own1_q"], "role": "QUALIFIER", "why": "real"}])
    v = CL.view(w["store"], build_cr(w, F.reader({"cr": {"RP_CLAIMS": recs}}))["inventory_id"])
    c = claim_with(v, "We argue that communicative participation")
    assert [e["link_role"] for e in c["evidence"]] == ["WORDING", "QUALIFIER"] and c["evidence"][1]["why_needed"] == "real"
    assert any(r["reason"] == "extra_quote_not_in_text_shown" for r in v["sources"][0]["extraction"]["rejected"])


def test_a_selective_follow_up_reads_only_for_claims_that_need_it_and_adds_links_with_their_own_derivation(w):
    recs = F.cr_records()
    recs[7] = F.rec("theoretical_proposition", F.CR["comp1"], F.OWN, "compound", granularity="COMPOUND", needs_deeper_reading=True, needs_deeper_reading_reason="the support for the second part is elsewhere",
                    components=[{"text": "a", "conditions": []}, {"text": "b", "conditions": ["messages are scarce"]}])
    calls: list = []
    ad = F.reader({"cr": {"RP_CLAIMS": recs}}, calls=calls, evidence={"participation raises visibility": [{"field": "supporting_evidence", "value": "cases in which less writing was thanked as often", "quote": F.CR["evid1"]}]})
    out = build_cr(w, ad)
    inv = out["inventory_id"]
    c0 = claim_with(CL.view(w["store"], inv), "We propose that participation raises visibility")
    assert "NEEDS_DEEPER_READING" in c0["needs_followup"] and "COMPOUND_CLAIM" in c0["needs_followup"]
    before = len(calls)
    fu = CL.followup(w["store"], w["token"], inv, ad)
    assert fu["claims_followed_up"] == 1 and len(calls) - before == 1 and calls[-1][1] == "RP_CLAIM_EVIDENCE"
    c1 = claim_with(CL.view(w["store"], inv), "We propose that participation raises visibility")
    assert [(e["link_role"], e["derivation"]) for e in c1["evidence"]] == [("WORDING", "extraction"), ("SUPPORTING_EVIDENCE", "followup")]
    assert c1["evidence"][1]["quote"] == F.CR["evid1"] and c1["needs_followup"] == []
    fu2 = CL.followup(w["store"], w["token"], inv, ad)                                                         # nothing is left to follow up: no model call
    assert fu2["claims_followed_up"] == 0 and len(calls) - before == 1


def test_the_researcher_can_add_an_evidence_link_but_only_with_a_quote_that_is_in_the_papers_text(w):
    out = build_cr(w)
    inv = out["inventory_id"]
    c = claim_with(CL.view(w["store"], inv), "We define communicative participation")
    CL.add_evidence_link(w["store"], w["token"], inv, c["id"], quote=F.CR["evid1"], role="SUPPORTING_EVIDENCE", why="the case that illustrates the definition")
    c2 = claim_with(CL.view(w["store"], inv), "We define communicative participation")
    assert [e["derivation"] for e in c2["evidence"]] == ["extraction", "researcher"] and c2["evidence"][1]["why_needed"].startswith("the case")
    with pytest.raises(KnowledgeError) as e:
        CL.add_evidence_link(w["store"], w["token"], inv, c["id"], quote="Not in the paper at all, said here.", role="CONTEXT", why="x")
    assert e.value.code == "QUOTE_NOT_IN_SOURCE"
    with pytest.raises(KnowledgeError) as e2:
        CL.add_evidence_link(w["store"], w["token"], inv, c["id"], quote=F.CR["evid1"], role="WORDING", why="x")
    assert e2.value.code == "WORDING_LINK_IS_FIXED"


# ------------------------------------------------------------------------------------------------ cache identity

def test_the_same_model_and_settings_hit_the_cache_another_model_or_other_settings_do_not(w):
    calls: list = []
    a1 = F.reader(calls=calls, model_id="model-one")
    build_cr(w, a1)
    n = len(calls)
    build_cr(w, F.reader(calls=calls, model_id="model-one"))
    assert len(calls) == n                                                                  # same identity, same settings: served from the run registry
    build_cr(w, F.reader(calls=calls, model_id="model-two"))
    assert len(calls) > n                                                                   # another model is another run
    n2 = len(calls)
    build_cr(w, F.reader(calls=calls, model_id="model-one"), max_input_tokens=6000)
    assert len(calls) > n2                                                                  # other chunking is another run
    n3 = len(calls)
    build_cr(w, F.reader(calls=calls, model_id="model-one"))
    assert len(calls) == n3                                                                 # and the first run is still there


def test_an_inventory_states_which_model_produced_each_source_and_never_mixes_two_models_silently(w):
    out = build_cr(w, F.reader(model_id="model-one"))
    out2 = build_cr(w, F.reader(model_id="model-two"))
    v1, v2 = CL.view(w["store"], out["inventory_id"]), CL.view(w["store"], out2["inventory_id"])
    assert v1["sources"][0]["extraction"]["model"]["model_id"] == "model-one" and v2["sources"][0]["extraction"]["model"]["model_id"] == "model-two"
    assert {c["generated_by"] for c in v1["claims"]} == {"model:model-one"} and {c["generated_by"] for c in v2["claims"]} == {"model:model-two"}
    assert out["inventory_id"] != out2["inventory_id"]


def test_a_reused_extraction_of_an_older_protocol_says_whether_the_model_is_the_one_now_asked_for(w):
    ids = w["ids"]
    recs = {"cr": {"RP_THEORY": [{"field": "construct_definition", "value": "participation", "quote": F.CR["def1"], "label": "communicative participation"}]}}

    def adapter(model_id):
        import re

        from research_harness.workflows.adapters import ScriptedModelAdapter

        def handler(system, user):
            return {"records": recs["cr"]["RP_THEORY"] if "RP_THEORY" in system else [], "gaps": []}
        return ScriptedModelAdapter(model_id=model_id, custom_handler=handler)
    first = rp.run_protocol(w["store"], w["token"], ids["cr"], "RP_THEORY", adapter("model-one"))
    same = rp.run_protocol(w["store"], w["token"], ids["cr"], "RP_THEORY", adapter("model-one"))
    other = rp.run_protocol(w["store"], w["token"], ids["cr"], "RP_THEORY", adapter("model-two"))
    assert first["reused"] is False and same["reuse"]["model_matches"] is True
    assert other["reused"] is True and other["reuse"] == {"model_matches": False, "cached_model": "model-one", "requested_model": "model-two"}


# ------------------------------------------------------------------------------------------------ coverage: no cap, and what was not read

def test_the_claims_protocol_has_no_cap_so_twenty_claims_are_twenty_claims(w):
    sentences = [f"We find that effect number {i} holds in the invented sample." for i in range(20)]
    text = F.paper("Many Findings", "x", [("1 Results", sentences)])
    from research_harness.knowledge import corpus
    sid = corpus.import_source(w["store"], w["token"], citation_key="many", title="Many Findings", year=2025, text=text)["object_id"]
    ad = F.reader({"many": {"RP_CLAIMS": [F.rec("empirical_finding", s, F.OWN) for s in sentences]}})
    out = CL.build(w["store"], w["token"], [sid], ad)
    assert out["claims"] == 20


def test_a_capped_field_of_the_landscape_protocol_is_reported_as_possibly_cut_short(w):
    from research_harness.workflows.adapters import ScriptedModelAdapter
    sentences = [f"We find that effect number {i} holds in the invented sample." for i in range(8)]
    text = F.paper("Eight Findings", "x", [("1 Results", sentences)])
    from research_harness.knowledge import corpus
    sid = corpus.import_source(w["store"], w["token"], citation_key="eight", title="Eight Findings", year=2025, text=text)["object_id"]
    ad = ScriptedModelAdapter(model_id="m", custom_handler=lambda s, u: {"records": [{"field": "finding", "value": x, "quote": x, "type": "positive", "label": f"r{i}"} for i, x in enumerate(sentences)], "gaps": []})
    res = rp.run_protocol(w["store"], w["token"], sid, "RP_LANDSCAPE", ad)
    assert len(res["records"]) == 8 and res["cap_saturated"] == ["finding"]                  # the cap was reached: eight is not evidence that the paper has eight findings
    assert rp.get_protocol("RP_LANDSCAPE").field("finding")["max_records"] == 8


def test_the_coverage_lists_what_was_read_and_what_was_not_and_never_offers_a_count_as_proof_of_reading(w):
    out = build_cr(w)
    v = CL.view(w["store"], out["inventory_id"])
    ex = v["sources"][0]["extraction"]
    assert ex["chars_read"] < ex["chars_total"] and ex["sections_not_read"] == ["References"] and "Introduction" in " ".join(ex["sections_read"])
    cov = v["coverage"]
    assert cov["claims"] == len(v["claims"]) and cov["sources"] == 1 and cov["claims_by_attribution"][F.OWN] > 0
    assert any("a count of claims is not evidence that the paper was read completely" in lim for lim in v["limits"])
    assert cov["sources_read_completely"] in (0, 1) and "model_calls" in cov and cov["model_calls"] >= 1


# ------------------------------------------------------------------------------------------------ the follow-up costs a call only where a call is needed

def many_findings(n=40):
    sentences = [f"We find that effect number {i} holds in the invented sample number {i}." for i in range(n)]
    text = F.paper("Many Findings", "x", [("1 Results", sentences[: n // 2]), ("2 More results", sentences[n // 2:])])
    return sentences, text


def test_a_finding_with_nothing_beyond_its_wording_is_flagged_for_the_report_but_does_not_cost_a_follow_up_call(w):
    from research_harness.knowledge import corpus
    sentences, text = many_findings()
    sid = corpus.import_source(w["store"], w["token"], citation_key="many", title="Many Findings", year=2025, text=text)["object_id"]
    calls: list = []
    ad = F.reader({"many": {"RP_CLAIMS": [F.rec("empirical_finding", s, F.OWN) for s in sentences]}}, calls=calls)
    inv = CL.build(w["store"], w["token"], [sid], ad)["inventory_id"]
    v = CL.view(w["store"], inv)
    assert all(c["needs_followup"] == ["NO_EVIDENCE_BEYOND_WORDING"] for c in v["claims"]) and v["coverage"]["claims_needing_follow_up"] == 40       # flagged: the report says so
    n = len(calls)
    fu = CL.followup(w["store"], w["token"], inv, ad)
    assert fu["claims_followed_up"] == 0 and len(calls) == n                                                                                      # but no call is made for them
    fu2 = CL.followup(w["store"], w["token"], inv, ad, reasons=("NO_EVIDENCE_BEYOND_WORDING",), max_claims=3)                                   # a budget, asked for explicitly
    assert fu2["claims_followed_up"] == 3 and len(fu2["claims_left_for_budget"]) == 37 and len(calls) - n == 3


def test_the_follow_up_reads_a_few_sentences_not_whole_sections(w):
    from research_harness.knowledge import corpus
    sentences, text = many_findings(60)
    sid = corpus.import_source(w["store"], w["token"], citation_key="many", title="Many Findings", year=2025, text=text)["object_id"]
    inv = CL.build(w["store"], w["token"], [sid], F.reader({"many": {"RP_CLAIMS": [F.rec("empirical_finding", s, F.OWN) for s in sentences]}}))["inventory_id"]
    c = CL.view(w["store"], inv)["claims"][10]
    spans = CL._candidate_spans(w["store"], c)
    total = sum(b - a for a, b in spans)
    assert 0 < total <= CL.SPAN_CHARS + 200 and total < len(text) / 4
    assert all(not (a < c["wording"]["char_end"] and c["wording"]["char_start"] < b) for a, b in spans)                  # the claim's own sentence is never among them
