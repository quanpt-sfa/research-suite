"""WP6.2 phase E: what three ways of reading a paper cost, measured offline with a rule-based stand-in. These tests check the mechanics of the measurement (calls, re-reading, caps, cache, views).
They do not say which strategy finds more of a paper's claims: that needs a real model and independent gold."""
from __future__ import annotations

import pytest

from research_harness.benchmarks import claim_strategies as CS
from research_harness.knowledge import claim_views as CV
from research_harness.knowledge import claims as CL
from research_harness.knowledge import corpus
from tests import claims_fixtures as F


@pytest.fixture(scope="module")
def measured(tmp_path_factory):
    papers = {"cr": F.CR_TEXT, "edu": F.EDU_TEXT, "fraud": F.FR_TEXT, "long": CS.long_paper("A Long Invented Paper", sections=6, per_section=30)}
    return CS.run_conditions(tmp_path_factory.mktemp("cost"), papers), papers


def test_the_stand_in_reader_is_deterministic_and_reads_only_sentences_not_headings(tmp_path):
    s = CS.sentences("1 Introduction\nWe find that effect one is large in the sample. Previous studies have shown the opposite (Brandt, 2016).\n\n[...]\n\nA heading\n")
    assert s == ["We find that effect one is large in the sample.", "Previous studies have shown the opposite (Brandt, 2016)."]
    w = F.make_store(tmp_path)
    a, b = CL.build(w["store"], w["token"], [w["ids"]["cr"]], CS.cue_reader()), CL.build(w["store"], w["token"], [w["ids"]["cr"]], CS.cue_reader(), reuse=False)
    va, vb = CL.view(w["store"], a["inventory_id"]), CL.view(w["store"], b["inventory_id"])
    assert [c["wording"]["quote"] for c in va["claims"]] == [c["wording"]["quote"] for c in vb["claims"]] and va["claims"]


def test_the_report_says_what_it_does_not_measure_and_that_its_tokens_are_estimates(measured):
    r, _ = measured
    assert "estimates, never provider billing" in r["usage_note"] and r["answers_come_from"] == "a fixed rule-based stand-in, not a model"
    assert set(r["not_measured"]) == {"coverage of the claims a paper really makes", "fidelity of restatements", "latency"}
    assert set(r["conditions"]) == set(CS.CONDITIONS)
    assert r["tokenizer"].startswith(("tiktoken", "heuristic"))


def test_independent_protocols_make_more_calls_and_read_the_same_text_more_than_once(measured):
    r, _ = measured
    a, b = r["conditions"]["A_independent_protocols"]["totals"], r["conditions"]["B_single_pass_inventory"]["totals"]
    assert a["model_calls"] > b["model_calls"] and a["model_calls"] >= 5 * 4                           # five protocols on each of the four papers
    assert a["reread_factor"] > 1.0                                                                     # some characters are read by more than one protocol
    assert b["reread_factor"] == 1.0                                                                    # one reading of the text: nothing is read twice
    assert a["total_tokens"] > 0 and b["total_tokens"] > 0
    long_a = r["conditions"]["A_independent_protocols"]["per_paper"]["long"]
    long_b = r["conditions"]["B_single_pass_inventory"]["per_paper"]["long"]
    assert long_a["model_calls"] > long_b["model_calls"] and long_a["reread_factor"] > 1.0


def test_what_no_run_read_is_reported_for_every_strategy_and_a_claim_count_is_not_taken_as_complete_reading(measured):
    r, _ = measured
    b = r["conditions"]["B_single_pass_inventory"]["per_paper"]
    assert all(p["sections_never_read"] == ["References"] and 0 < p["share_of_text_never_read"] < 0.2 for p in b.values())          # only the references are left out
    a = r["conditions"]["A_independent_protocols"]["per_paper"]
    assert any(p["sections_never_read"] for p in a.values())                                           # the older protocols leave sections unread (a method section no protocol asked for, for one)
    assert all(p["share_of_text_never_read"] >= 0 for p in a.values())


def test_the_cap_of_the_landscape_protocol_cuts_findings_and_the_claims_protocol_does_not(measured):
    r, papers = measured
    a_long = r["conditions"]["A_independent_protocols"]["per_paper"]["long"]
    b_long = r["conditions"]["B_single_pass_inventory"]["per_paper"]["long"]
    n_find = sum(1 for s in CS.sentences(papers["long"]) if CS._claim_field(s) == "empirical_finding")
    assert n_find > 100
    assert a_long["records"]["RP_LANDSCAPE.finding"] == 8 and a_long["records_set_aside_for_caps"] > 0 and "RP_LANDSCAPE.finding" in a_long["fields_at_cap"]
    assert b_long["records"]["RP_CLAIMS.empirical_finding"] == n_find                                  # no configuration limit: every sentence the reader reported is a claim
    assert "RP_CLAIMS.empirical_finding" not in b_long["fields_at_cap"]


def test_a_second_identical_pass_is_served_from_the_cache_in_every_strategy(measured):
    r, _ = measured
    for cond, v in r["conditions"].items():
        s = v["second_identical_pass"]
        assert s["model_calls_added"] == 0 and s["tokens_added"] == 0 and s["runs_served_from_cache"] > 0, cond


def test_section_aware_reading_follows_up_only_the_claims_that_need_it(measured):
    r, _ = measured
    c, b = r["conditions"]["C_section_aware_with_followup"], r["conditions"]["B_single_pass_inventory"]
    flagged = sum(c["claims_flagged_for_followup"].values())
    assert flagged > 0
    calls_c, calls_b = c["totals"]["model_calls"], b["totals"]["model_calls"]
    assert calls_c > calls_b                                                                              # more calls than one reading, because of the section groups and the follow-ups
    sections = {k: v for k, v in c["per_paper"].items()}
    assert all(v["by_protocol"].get("RP_CLAIMS", {}).get("calls", 0) >= 1 for v in sections.values())
    assert any("RP_CLAIM_EVIDENCE" in v["by_protocol"] for v in sections.values()) or flagged == 0


def test_merging_the_protocols_into_one_reading_loses_what_a_claim_cannot_fill(measured, tmp_path):
    r, _ = measured
    d = r["conditions"]["B_single_pass_inventory"]["views_derivable_without_a_call"]
    assert set(d) == set(CV.VIEWS) and d["RP_THEORY"]["records"] > 0 and d["RP_LANDSCAPE"]["records"] > 0
    assert "theory_invoked" in d["RP_THEORY"]["fields_not_derivable"] and "phenomenon" in d["RP_INTRO_ARGUMENT"]["fields_not_derivable"]
    assert set(d["RP_METHOD"]["fields_partial"]) == {f["name"] for f in __import__("research_harness.workflows.reading_protocols", fromlist=["x"]).get_protocol("RP_METHOD").fields}


def test_a_view_separates_the_authors_own_findings_from_what_they_report_of_earlier_work(tmp_path):
    w = F.make_store(tmp_path)
    inv = CL.build(w["store"], w["token"], [w["ids"]["cr"]], F.reader())["inventory_id"]
    v = CL.view(w["store"], inv)
    land = CV.derive(v, "RP_LANDSCAPE")
    quotes_ = {r["quote"] for r in land["records"]}
    assert F.CR["rep2"] not in quotes_ and F.CR["rep1"] not in quotes_                                  # reported prior work is not this paper's finding or claim
    assert F.CR["own1"] in quotes_ and all(r["attribution"] == "AUTHOR_OWN_CLAIM" for r in land["records"])
    intro = CV.derive(v, "RP_INTRO_ARGUMENT")
    ek = [r for r in intro["records"] if r["field"] == "established_knowledge"]
    assert {r["quote"] for r in ek} == {F.CR["rep1"], F.CR["rep2"]} and any(r["field"] == "unresolved_tension" and r["quote"] == F.CR["gap1"] for r in intro["records"])
    assert all(r["claim"] and r["source_id"] and r["passage_id"] for r in land["records"] + intro["records"])
    assert land["no_model_call"] is True and "identifying_assumption" in {r["field"] for r in land["records"]}
    th = CV.derive(v, "RP_THEORY")
    assert {r["field"] for r in th["records"]} == {"construct_definition", "stated_assumption"}
    assert "theory_invoked" in th["unfilled"] and th["unfilled"]["theory_invoked"]["status"] == "NOT_DERIVABLE"
    with pytest.raises(KeyError):
        CV.derive(v, "RP_NOT_A_PROTOCOL")
