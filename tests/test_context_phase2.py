"""Phase 2 (plan v3): scoped, table-driven, budget-enforced context; paged full text; section-aligned comparison."""
from __future__ import annotations

import pytest

from research_harness import taxonomy
from research_harness.state.store import StateStore
from research_harness.workflows.adapters import ScriptedModelAdapter
from research_harness.workflows.comparison import compare_sources
from research_harness.workflows.context import AmbiguousResearchQuestionError, build_context
from research_harness.workflows.dispatcher import _compute_input_fingerprint, run_workflow
from research_harness.workflows.fulltext import get_fulltext_page, text_sha256
from research_harness.workflows.instrumentation import estimate_tokens
from research_harness.workflows.model_io import build_system_prompt
from research_harness.workflows import registry as reg_mod
from research_harness.workflows.sections import classify_heading, detect_sections, sections_for_dimension


# --------------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------------

@pytest.fixture
def proj(tmp_path):
    return StateStore.create(tmp_path / "home", "PRJ_CTX")


def add(proj, *objs):
    store, token = proj
    with store.write_tx(token=token) as tx:
        for oid, otype, content, edges in objs:
            tx.add_revision(object_id=oid, object_type=otype, content=content, origin={}, edges=list(edges))


def rq(oid="RQ_A", text="Does board independence reduce restatements?"):
    return (oid, "ResearchQuestion", {"text": text}, [])


def dep(target, rel="depends_on"):
    return (rel, target, 1, {})


def claim(oid, text="A claim.", edges=(), tier="associational"):
    return (oid, "Claim", {"text": text, "claim_tier": tier}, list(edges))


def passage(oid, quote="Short passage.", edges=(), **extra):
    return (oid, "EvidencePassage", {"locator": f"{oid} p.1", "quote": quote, **extra}, list(edges))


def request_tokens(ctx, wf=None):
    system = build_system_prompt(wf or ctx.workflow, reg_mod.load_skill_instructions(wf or ctx.workflow))
    return estimate_tokens(system)[0] + estimate_tokens(ctx.formatted_user_prompt)[0]


def ctx_of(proj, wf="contribution", **kw):
    kw.setdefault("research_question_id", "RQ_A")
    return build_context(proj[0], wf, **kw)


# --------------------------------------------------------------------------------------------------
# research-question scoping
# --------------------------------------------------------------------------------------------------

def test_single_research_question_is_resolved_automatically(proj):
    add(proj, rq(), claim("CLAIM_1", edges=[dep("RQ_A")]))
    ctx = build_context(proj[0], "contribution")
    assert ctx.research_question_id == "RQ_A" and ctx.selection_strategy.startswith("PRIMARY_PROJECT_RESEARCH_QUESTION")
    assert ctx.scope_claims == ["CLAIM_1"]


def test_several_research_questions_without_a_choice_is_an_error(proj):
    add(proj, rq("RQ_A"), rq("RQ_B", "Teacher feedback?"))
    with pytest.raises(AmbiguousResearchQuestionError, match=r"AMBIGUOUS_RESEARCH_QUESTION.*RQ_A.*RQ_B"):
        build_context(proj[0], "contribution")


def test_unknown_explicit_ids_raise(proj):
    add(proj, rq(), claim("CLAIM_1", edges=[dep("RQ_A")]))
    with pytest.raises(KeyError, match="RESEARCH_QUESTION_NOT_FOUND"):
        ctx_of(proj, research_question_id="RQ_NOPE")
    with pytest.raises(KeyError, match="CLAIM_NOT_FOUND"):
        ctx_of(proj, focus_claims=["CLAIM_NOPE"])
    with pytest.raises(KeyError, match="CLAIM_NOT_FOUND"):
        ctx_of(proj, focus_claims=["RQ_A"])  # exists, but is not a claim


def test_claims_are_scoped_directly_and_through_a_result_chain_but_no_further(proj):
    add(proj, rq(),
        ("RES_1", "Result", {"summary": "r"}, [dep("RQ_A")]),
        claim("CLAIM_DIRECT", edges=[dep("RQ_A")]),
        claim("CLAIM_VIA_RESULT", edges=[dep("RES_1")]),
        claim("CLAIM_FAR", edges=[dep("CLAIM_VIA_RESULT")]),
        claim("CLAIM_UNLINKED"))
    ctx = ctx_of(proj)
    assert ctx.scope_claims == ["CLAIM_DIRECT", "CLAIM_VIA_RESULT"]
    assert "RQ_SCOPED (2 of 4 project claims)" in ctx.selection_strategy


def test_a_shared_source_does_not_pull_in_the_other_questions_claims(proj):
    add(proj, rq("RQ_A"), rq("RQ_B", "Teacher feedback?"),
        ("SRC_SHARED", "Source", {"citation_key": "Shared2020", "abstract": "a"}, [dep("RQ_A"), dep("RQ_B")]),
        claim("CLAIM_A", edges=[dep("RQ_A")]),
        claim("CLAIM_B", edges=[dep("RQ_B"), dep("SRC_SHARED")]))
    ctx = ctx_of(proj)
    assert ctx.scope_claims == ["CLAIM_A"]
    assert "CLAIM_B" not in [o["object_id"] for o in ctx.objects]


def test_explicit_focus_claim_from_another_question_is_honoured(proj):
    add(proj, rq("RQ_A"), rq("RQ_B", "Other?"), claim("CLAIM_B", edges=[dep("RQ_B")]))
    ctx = ctx_of(proj, focus_claims=["CLAIM_B"])
    assert ctx.scope_claims == ["CLAIM_B"] and "CLAIMS: EXPLICIT_FOCUS" in ctx.selection_strategy


def test_project_without_a_question_uses_all_claims(proj):
    add(proj, claim("CLAIM_1"), claim("CLAIM_2"))
    ctx = build_context(proj[0], "contribution")
    assert ctx.scope_claims == ["CLAIM_1", "CLAIM_2"] and "NO_RESEARCH_QUESTION_FOUND" in ctx.selection_strategy


def test_contribution_assesses_every_scoped_claim_and_never_picks_a_primary_one(proj):
    add(proj, rq(), claim("CLAIM_1", "First.", [dep("RQ_A")]), claim("CLAIM_2", "Second.", [dep("RQ_A")]))
    res = run_workflow("contribution", proj[0], research_question_id="RQ_A", reuse_cached=False).result
    assert sorted(c["claim_id"] for c in res["candidate_contributions"]) == ["CLAIM_1", "CLAIM_2"]
    res = run_workflow("contribution", proj[0], research_question_id="RQ_A", focus_claims=["CLAIM_2"],
                       reuse_cached=False).result
    assert [c["claim_id"] for c in res["candidate_contributions"]] == ["CLAIM_2"]


# --------------------------------------------------------------------------------------------------
# relations and contradiction handling
# --------------------------------------------------------------------------------------------------

def test_relations_between_included_objects_are_recorded(proj):
    add(proj, rq(), passage("EP_1", edges=[dep("RQ_A")]),
        claim("CLAIM_1", edges=[dep("RQ_A"), ("supports", "EP_1", 1, {})]))
    rels = {(r["source_id"], r["relation"], r["target_id"]) for r in ctx_of(proj).relations}
    assert ("CLAIM_1", "depends_on", "RQ_A") in rels and ("CLAIM_1", "supports", "EP_1") in rels
    assert all(set(r) == {"source_id", "source_revision", "relation", "target_id", "target_revision"}
               for r in ctx_of(proj).relations)


def test_challenge_links_in_either_direction_are_counted_and_kept_first(proj):
    add(proj, rq(),
        passage("EP_PLAIN_1", "Plain one.", [dep("RQ_A")]),
        passage("EP_PLAIN_2", "Plain two.", [dep("RQ_A")]),
        passage("EP_REVERSED", "Reversed direction.", [dep("RQ_A")]),
        claim("CLAIM_1", edges=[dep("RQ_A"), ("challenges", "EP_REVERSED", 1, {})]))  # claim -> evidence
    add(proj, passage("EP_SPEC", "Spec direction.", [dep("RQ_A"), ("challenges", "CLAIM_1", 1, {})]))  # evidence -> claim
    ctx = ctx_of(proj)
    assert ctx.manifest["contradictory_evidence_count"] == 2
    assert ctx.contradictory_evidence_count == 2


def test_wording_never_marks_evidence_as_contradictory(proj):
    add(proj, rq(), claim("CLAIM_1", edges=[dep("RQ_A")]),
        passage("EP_WORDS", "This contradicts and refutes the claim; not_supported.", [dep("RQ_A")], contradictory_signal=True),
        passage("EP_FALSE", "Fine.", [dep("RQ_A")], contradictory=False))
    assert ctx_of(proj).manifest["contradictory_evidence_count"] == 0


def test_explicit_structured_flags_are_honoured(proj):
    add(proj, rq(), claim("CLAIM_1", edges=[dep("RQ_A")]),
        passage("EP_FLAG", "A passage.", [dep("RQ_A")], contradictory=True),
        passage("EP_REL", "Another.", [dep("RQ_A")], assessment={"relation": "challenges"}))
    assert ctx_of(proj).manifest["contradictory_evidence_count"] == 2


def test_challenging_evidence_survives_a_tight_budget(proj):
    filler = [passage(f"EP_{i:02d}", f"Filler passage {i}. " + "word " * 120, [dep("RQ_A")]) for i in range(1, 9)]
    add(proj, rq(), *filler,
        passage("EP_ZZ_CHALLENGE", "The one that challenges the claim. " + "word " * 120, [dep("RQ_A")]),
        claim("CLAIM_1", edges=[dep("RQ_A"), ("challenges", "EP_ZZ_CHALLENGE", 1, {})]))
    full = ctx_of(proj, max_input_tokens=100000)
    tight = ctx_of(proj, max_input_tokens=int(request_tokens(full) * 0.55))
    ids = [o["object_id"] for o in tight.objects]
    assert "EP_ZZ_CHALLENGE" in ids and tight.omitted_items  # sorts last by id, yet kept: it is must-have
    assert all(o["tier"] > 0 for o in tight.omitted_items)


# --------------------------------------------------------------------------------------------------
# enforced budget
# --------------------------------------------------------------------------------------------------

def _corpus(proj, n=10):
    add(proj, rq(), claim("CLAIM_1", edges=[dep("RQ_A")]),
        *[("SRC_%02d" % i, "Source", {"citation_key": f"S{i}", "abstract": "abstract " * 30}, [dep("RQ_A")]) for i in range(n)],
        *[passage(f"EP_{i:02d}", f"Passage {i}. " + "evidence " * 40, [("derived_from", "SRC_%02d" % i, 1, {}), dep("RQ_A")])
          for i in range(n)])


@pytest.mark.parametrize("budget", [1800, 2500, 3500, 5000, 8000, 20000])
def test_request_fits_the_budget_or_the_context_is_insufficient(proj, budget):
    _corpus(proj)
    ctx = ctx_of(proj, max_input_tokens=budget)
    if ctx.coverage != "INSUFFICIENT":
        assert request_tokens(ctx) <= budget
    assert ctx.manifest["request_tokens"] == request_tokens(ctx) or ctx.coverage == "INSUFFICIENT"


def test_coverage_levels(proj):
    big = "word " * 3000
    add(proj, rq(), claim("CLAIM_1", edges=[dep("RQ_A")]),
        passage("EP_SMALL", "Small.", [dep("RQ_A")]))
    base = ctx_of(proj, focus_claims=["CLAIM_1"], max_input_tokens=100000)
    assert (base.coverage, base.context_status, base.budget_status, base.omitted_items) == ("FULL", "COMPLETE", "OK", [])
    room = request_tokens(base) + 400

    add(proj, claim("CLAIM_BG", big, [dep("RQ_A")]))  # a claim outside the focus: background (T2)
    t2 = ctx_of(proj, focus_claims=["CLAIM_1"], max_input_tokens=room)
    assert (t2.coverage, t2.context_status, t2.budget_status) == ("PARTIAL_T2", "PARTIAL", "OK")
    assert [o["tier"] for o in t2.omitted_items] == [2]

    add(proj, passage("EP_BIG", big, [dep("RQ_A")]))  # relevant evidence (T1)
    t1 = ctx_of(proj, focus_claims=["CLAIM_1"], max_input_tokens=room)
    assert (t1.coverage, t1.context_status, t1.budget_status) == ("PARTIAL_T1", "PARTIAL", "INCOMPLETE_EXCEEDED")

    add(proj, ("CLAIM_HUGE", "Claim", {"text": big, "claim_tier": "causal"}, [dep("RQ_A")]))  # must-have (T0)
    t0 = ctx_of(proj, focus_claims=["CLAIM_1", "CLAIM_HUGE"], max_input_tokens=room)
    assert (t0.coverage, t0.context_status, t0.budget_status) == ("INSUFFICIENT", "INSUFFICIENT", "INCOMPLETE_EXCEEDED")
    assert any(o["tier"] == 0 and o["object_id"] == "CLAIM_HUGE" for o in t0.omitted_items)


def test_system_prompt_is_part_of_the_budget(proj):
    _corpus(proj, 3)
    ctx = ctx_of(proj, max_input_tokens=20000)
    system = estimate_tokens(build_system_prompt("contribution", reg_mod.load_skill_instructions("contribution")))[0]
    assert ctx.manifest["system_prompt_tokens"] == system and ctx.manifest["reserved_tokens"] > system
    assert ctx.manifest["request_tokens"] == ctx.estimated_tokens == request_tokens(ctx)


def test_evidence_text_is_rendered_once_and_extra_fields_are_kept(proj):
    add(proj, rq(), passage("EP_1", "A unique passage sentence.", [dep("RQ_A")],
                            normalized_claim="Normalized claim text.", assessment={"relation": "supports"}))
    prompt = ctx_of(proj).formatted_prompt
    assert prompt.count("A unique passage sentence.") == 1
    assert "Normalized claim text." in prompt and '"relation": "supports"' in prompt
    assert "[EvidencePassage] EP_1" not in prompt  # not duplicated as an object line


def test_continuation_plan_describes_what_is_missing(proj):
    _corpus(proj)
    full = ctx_of(proj, max_input_tokens=100000)
    ctx = ctx_of(proj, max_input_tokens=int(request_tokens(full) * 0.6))  # relative: independent of the tokenizer
    plan = ctx.continuation_plan
    assert plan and plan["status"] == "INCOMPLETE_CONTEXT" and plan["coverage"] == ctx.coverage
    assert plan["omitted_objects_count"] == len(ctx.omitted_items) > 0
    assert plan["estimated_missing_tokens"] > 0 and plan["recommended_budget"] > plan["estimated_missing_tokens"]


def test_model_is_not_called_for_insufficient_context_even_with_allow_incomplete(proj):
    big = "word " * 3000
    add(proj, rq(), ("CLAIM_HUGE", "Claim", {"text": big, "claim_tier": "causal"}, [dep("RQ_A")]))
    adapter = ScriptedModelAdapter(default_response={"x": 1})
    out = run_workflow("contribution", proj[0], research_question_id="RQ_A", model_adapter=adapter,
                       max_input_tokens=2600, allow_incomplete=True, reuse_cached=False)
    assert adapter.recorded_requests == [] and out.status == "INCOMPLETE_CONTEXT"
    assert "must-have" in out.result["reason"]


# --------------------------------------------------------------------------------------------------
# full text: policies, paging, deferral
# --------------------------------------------------------------------------------------------------

BODY = "Introduction\nWe study X.\n\nData and Sample\n" + "We use Compustat firm-years. " * 400 + "\n\nResults\nIt works.\n"


def _source(oid="SRC_F", body=BODY, **extra):
    return (oid, "Source", {"citation_key": "Full2021", "abstract": "Abstract text.", "full_text": body, **extra}, [dep("RQ_A")])


def test_full_text_status_by_policy(proj):
    add(proj, rq(), _source("SRC_F"), ("SRC_ABS", "Source", {"citation_key": "Abs", "abstract": "only"}, [dep("RQ_A")]),
        ("SRC_NONE", "Source", {"citation_key": "None"}, [dep("RQ_A")]))
    status = lambda **kw: {f["source_id"]: f["fulltext_status"] for f in ctx_of(proj, "literature", max_input_tokens=100000, **kw).full_texts}  # noqa: E731
    assert status(fulltext_policy="never") == {"SRC_F": "FULLTEXT_AVAILABLE", "SRC_ABS": "ABSTRACT_ONLY", "SRC_NONE": "FULLTEXT_MISSING"}
    assert status(fulltext_policy="on_demand") == status(fulltext_policy="never")
    assert status(fulltext_policy="on_demand", fulltext_sources=["SRC_F"])["SRC_F"] == "FULLTEXT_LOADED"
    always = status(fulltext_policy="always")
    assert always["SRC_F"] == "FULLTEXT_LOADED" and always["SRC_ABS"] == "ABSTRACT_ONLY"  # abstract is never relabelled


def test_missing_cas_artifact_is_retrieval_failed_not_abstract_only(proj):
    add(proj, rq(), ("SRC_X", "Source", {"citation_key": "X", "abstract": "a", "artifact_sha256": "0" * 64}, [dep("RQ_A")]))
    ft = ctx_of(proj, "literature", fulltext_policy="always").full_texts[0]
    assert ft["fulltext_status"] == "RETRIEVAL_FAILED" and ft["full_text"] is None


def test_body_stored_in_the_content_addressed_store_is_loaded(proj):
    store, _ = proj
    sha = store.artifacts.put(BODY.encode("utf-8"))
    add(proj, rq(), ("SRC_CAS", "Source", {"citation_key": "Cas", "abstract": "a", "artifact_sha256": sha}, [dep("RQ_A")]))
    ft = ctx_of(proj, "literature", fulltext_policy="always", max_input_tokens=100000).full_texts[0]
    assert ft["fulltext_status"] == "FULLTEXT_LOADED" and ft["full_text"] == BODY


def test_large_full_text_is_deferred_with_a_handle_not_dropped(proj):
    add(proj, rq(), _source())
    ctx = ctx_of(proj, "literature", fulltext_policy="always", max_input_tokens=2500)
    ft = {f["source_id"]: f for f in ctx.full_texts}["SRC_F"]
    assert "SRC_F" in [o["object_id"] for o in ctx.objects]
    assert ft["fulltext_status"] == "FULLTEXT_DEFERRED" and ft["full_text"] is None
    assert ft["chars"] == len(BODY) and ft["sha256"] == text_sha256(BODY)
    assert {"data_sample", "results"} <= {s["label"] for s in ft["sections"]}
    assert "get_fulltext_page('SRC_F'" in ctx.formatted_prompt and "We use Compustat" not in ctx.formatted_prompt
    assert (ctx.coverage, ctx.context_status, ctx.manifest["fulltext_deferred_count"]) == ("PARTIAL_T1", "PARTIAL", 1)
    assert ctx.continuation_plan["deferred_fulltexts"] == ["SRC_F"]
    assert request_tokens(ctx, "literature") <= 2500


def test_full_text_that_fits_is_loaded_and_unrequested_deferral_is_not_a_gap(proj):
    add(proj, rq(), _source(body="Short body."))
    ctx = ctx_of(proj, "literature", fulltext_policy="always", max_input_tokens=100000)
    assert ctx.full_texts[0]["fulltext_status"] == "FULLTEXT_LOADED" and "Short body." in ctx.formatted_prompt
    assert ctx.coverage == "FULL"


def test_loaded_versus_deferred_changes_the_cache_fingerprint(proj):
    add(proj, rq(), _source())
    loaded = ctx_of(proj, "literature", fulltext_policy="always", max_input_tokens=100000)
    deferred = ctx_of(proj, "literature", fulltext_policy="always", max_input_tokens=2500)
    assert _compute_input_fingerprint("literature", loaded) != _compute_input_fingerprint("literature", deferred)


def test_pages_reassemble_the_body_and_report_where_they_end(proj):
    add(proj, rq(), _source())
    store, _ = proj
    text, offset, pages = "", 0, 0
    while offset is not None:
        page = get_fulltext_page(store, "SRC_F", offset=offset, limit=1000)
        assert page["offset"] == len(text) and page["sha256"] == text_sha256(BODY) and page["total_chars"] == len(BODY)
        text, offset, pages = text + page["text"], page["next_offset"], pages + 1
    assert text == BODY and pages == -(-len(BODY) // 1000)


def test_page_errors(proj):
    add(proj, rq(), _source(), ("SRC_ABS", "Source", {"citation_key": "A", "abstract": "only"}, [dep("RQ_A")]))
    store, _ = proj
    with pytest.raises(KeyError, match="SOURCE_NOT_FOUND"):
        get_fulltext_page(store, "RQ_A")
    with pytest.raises(LookupError, match="FULLTEXT_MISSING"):
        get_fulltext_page(store, "SRC_ABS")
    with pytest.raises(ValueError):
        get_fulltext_page(store, "SRC_F", offset=len(BODY) + 1)
    for bad in ({"offset": -1}, {"limit": 0}, {"limit": True}):
        with pytest.raises(ValueError):
            get_fulltext_page(store, "SRC_F", **bad)
    assert get_fulltext_page(store, "SRC_F", offset=len(BODY))["text"] == ""


# --------------------------------------------------------------------------------------------------
# sections
# --------------------------------------------------------------------------------------------------

PAPER = """A Study of Boards

Abstract
We study boards.

1. Introduction
Intro text here.

2 Data and Sample
We use Compustat 2000-2019 and drop financial firms.
2019 was a year of change for lending, as we discuss.

3. Research Design
We estimate a logit model.

4. Results
Independence lowers restatements.

5. Robustness Checks
Results hold.

6. Conclusion
We conclude.

References
[1] Klein 2002.
"""


def test_sections_tile_the_text_and_carry_labels():
    secs = detect_sections(PAPER)
    assert [s.label for s in secs] == ["preamble", "abstract", "introduction", "data_sample", "method", "results",
                                       "robustness", "discussion_conclusion", "references"]
    assert secs[0].char_start == 0 and secs[-1].char_end == len(PAPER)
    assert all(a.char_end == b.char_start for a, b in zip(secs, secs[1:]))
    assert "".join(PAPER[s.char_start:s.char_end] for s in secs) == PAPER
    data = next(s for s in secs if s.label == "data_sample")
    assert "Compustat" in PAPER[data.char_start:data.char_end]


def test_body_sentences_starting_with_a_number_are_not_headings():
    secs = detect_sections(PAPER)
    assert "2019 was a year" in next(s for s in secs if s.label == "data_sample").heading + \
        PAPER[next(s for s in secs if s.label == "data_sample").char_start:next(s for s in secs if s.label == "data_sample").char_end]
    assert len([s for s in secs if s.label == "data_sample"]) == 1


@pytest.mark.parametrize("heading,label,conf", [
    ("3.2 Sample Selection", "data_sample", "high"), ("III. EMPIRICAL RESULTS", "results", "high"),
    ("Robustness checks", "robustness", "high"), ("Threats to Validity", "limitations", "high"),
    ("Why robustness matters here", "robustness", "low"), ("Appendix A", "other", "none"), ("", "other", "none")])
def test_classify_heading(heading, label, conf):
    assert classify_heading(heading) == (label, conf)


def test_unnumbered_title_case_headings_are_found_but_wrapped_sentences_are_not():
    text = "Robustness Checks\nResults hold.\n\nData are collected from\nthe public registry.\n\nData and Sample Selection\nWe use X.\n"
    secs = detect_sections(text)
    assert [(s.label, s.heading) for s in secs] == [("robustness", "Robustness Checks"), ("data_sample", "Data and Sample Selection")]
    assert "Data are collected from" in text[secs[0].char_start:secs[0].char_end]  # stayed in the previous section's body


def test_run_in_headings_need_a_known_heading_word_at_the_start_of_a_line():
    secs = detect_sections("7. Limitations: because treatment was self-selected our estimates may overstate.\n")
    assert [(s.label, s.heading) for s in secs] == [("limitations", "7. Limitations")]
    prose = "The sample is large. Results: the model fits well, but the data are noisy.\nWe find data matter.\n"
    assert [s.label for s in detect_sections(prose)] == ["other"]


def test_unmatched_heading_is_kept_as_other_and_no_text_is_dropped():
    text = "Overview\nSome text.\n\nOur Special Section\nMore text.\n"
    assert "".join(text[s.char_start:s.char_end] for s in detect_sections(text)) == text


def test_dimension_mapping_and_unknown_dimension():
    secs = detect_sections(PAPER)
    assert [s.label for s in sections_for_dimension(secs, "sample_construction")] == ["data_sample"]
    assert [s.label for s in sections_for_dimension(secs, "measurement")] == ["data_sample"]
    assert [s.label for s in sections_for_dimension(secs, "introduction_argument")] == ["abstract", "introduction"]
    with pytest.raises(taxonomy.TaxonomyError):
        sections_for_dimension(secs, "vibes")


# --------------------------------------------------------------------------------------------------
# section-aligned comparison
# --------------------------------------------------------------------------------------------------

def _papers(proj):
    mk = lambda tag, sample: f"Introduction\nIntro {tag}.\n\nData and Sample\n{sample}\n\nResults\nResults {tag}.\n"  # noqa: E731
    add(proj, rq(),
        ("SRC_1", "Source", {"citation_key": "P1", "abstract": "a", "full_text": mk("one", "Sample one: US firms 2000-2010.")}, [dep("RQ_A")]),
        ("SRC_2", "Source", {"citation_key": "P2", "abstract": "a", "full_text": mk("two", "Sample two: " + "EU banks. " * 500)}, [dep("RQ_A")]),
        ("SRC_3", "Source", {"citation_key": "P3", "abstract": "only an abstract"}, [dep("RQ_A")]),
        ("SRC_4", "Source", {"citation_key": "P4", "abstract": "a", "full_text": "Introduction\nJust an intro.\n"}, [dep("RQ_A")]))


def test_comparison_reads_the_same_part_of_each_paper(proj):
    _papers(proj)
    res = {e["source_id"]: e for e in compare_sources(proj[0], ["SRC_1", "SRC_2", "SRC_3", "SRC_4"], "sample_construction", budget_tokens=40000)}
    one = res["SRC_1"]
    assert one["status"] == "MATCHED" and one["complete"] and len(one["blocks"]) == 1
    block = one["blocks"][0]
    assert "US firms" in block["text"] and block["locator"] == f"SRC_1@1[{block['char_start']}:{block['char_end']}]"
    assert res["SRC_3"]["status"] == "NO_FULLTEXT" and res["SRC_3"]["reason"] == "MISSING"
    assert res["SRC_4"]["status"] == "NO_MATCHING_SECTION"
    assert {s["label"] for s in res["SRC_4"]["available_sections"]} == {"introduction"}


def test_comparison_truncation_is_explicit_and_resumable(proj):
    _papers(proj)
    store, _ = proj
    entry = compare_sources(store, ["SRC_2"], "sample_construction", budget_tokens=200)[0]
    block = entry["blocks"][0]
    assert block["truncated"] and entry["complete"] is False and block["next_offset"] == block["char_end"]
    body = store.get_object("SRC_2")["content"]["full_text"]
    section = detect_sections(body)
    data = next(s for s in section if s.label == "data_sample")
    rest = get_fulltext_page(store, "SRC_2", offset=block["next_offset"], limit=data.char_end - block["next_offset"])["text"]
    assert block["text"] + rest == body[data.char_start:data.char_end]


def test_comparison_lists_sections_it_did_not_reach(proj):
    add(proj, rq(), ("SRC_M", "Source", {"citation_key": "M", "abstract": "a", "full_text":
                    "Data\n" + "alpha " * 300 + "\n\nSample Selection\nsecond block\n"}, [dep("RQ_A")]))
    entry = compare_sources(proj[0], ["SRC_M"], "sample_construction", budget_tokens=150)[0]
    assert entry["blocks"][0]["truncated"] and entry["unread_sections"] and entry["complete"] is False
    assert entry["unread_sections"][0]["heading"] == "Sample Selection"


def test_build_context_with_a_comparison_dimension(proj):
    _papers(proj)
    ctx = ctx_of(proj, "literature", comparison_dimension="sample_construction", max_input_tokens=20000)
    assert [c["source_id"] for c in ctx.comparisons] == ["SRC_1", "SRC_2", "SRC_3", "SRC_4"]
    assert "## Comparison: sample_construction" in ctx.formatted_prompt
    assert "Sample one: US firms" in ctx.formatted_prompt and "SRC_1@1[" in ctx.formatted_prompt
    assert "[No stored full text: MISSING]" in ctx.formatted_prompt
    assert request_tokens(ctx) <= 20000


def test_comparison_respects_a_tight_budget(proj):
    _papers(proj)
    base = ctx_of(proj, "literature", max_input_tokens=100000)
    room = request_tokens(base) + 900
    ctx = ctx_of(proj, "literature", comparison_dimension="sample_construction", max_input_tokens=room)
    assert request_tokens(ctx) <= room and ctx.coverage != "INSUFFICIENT"
    two = next(c for c in ctx.comparisons if c["source_id"] == "SRC_2")
    assert two["blocks"][0]["truncated"] and "resume at char" in ctx.formatted_prompt
