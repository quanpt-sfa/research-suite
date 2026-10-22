"""WP6.2: relations between claims, and alignments of claims across versions. Every relation is a proposal until the researcher decides. Synthetic papers, scripted models: `SCHEMA_TESTED`."""
from __future__ import annotations

import pytest

from research_harness.knowledge import claim_relations as CR
from research_harness.knowledge import claims as CL
from research_harness.knowledge import corpus
from research_harness.knowledge.common import KnowledgeError
from research_harness.workflows.adapters import ScriptedModelAdapter
from tests import claims_fixtures as F


@pytest.fixture()
def w(tmp_path):
    return F.make_store(tmp_path)


def inv_of(w, *keys, adapter=None):
    return CL.build(w["store"], w["token"], [w["ids"][k] for k in keys], adapter or F.reader())["inventory_id"]


def cid(v, fragment):
    return next(c["id"] for c in v["claims"] if c.get("wording") and fragment in c["wording"]["quote"])


def view(w, inv):
    return CL.view(w["store"], inv)


def add(w, inv, **p):
    return CR.add_relation(w["store"], w["token"], inv, p)


def code(fn, *a, **k):
    with pytest.raises(KnowledgeError) as e:
        fn(*a, **k)
    return e.value.code


@pytest.fixture()
def two(w):
    """The critical review and the preprint/journal pair: two works in one inventory, plus a second version of one of them."""
    inv = inv_of(w, "cr", "pre", "jour")
    return w, inv, view(w, inv)


# ------------------------------------------------------------------------------------------------ relations

def test_each_of_the_eleven_kinds_is_stored_as_a_proposal_with_its_sources_passages_and_reasons(two):
    w, inv, v = two
    c = lambda f: cid(v, f)                                                                  # noqa: E731
    a, d, s_, p_, k, r, t, g = (c("We argue that communicative"), c("We define communicative"), c("We assume that recipients"), c("We propose that participation"),
                                c("This paper contributes"), c("Alder (2018) argues"), c("However, Alder's"), c("No account yet"))
    e_pre = next(x["id"] for x in v["claims"] if x["source_id"] == w["ids"]["pre"] and "an investment of the self" in x["wording"]["quote"])
    q_pre = next(x["id"] for x in v["claims"] if x["wording"]["quote"] == F.V["q_pre"])
    proposals = [
        dict(kind="SUPPORTS", **{"from": d}, to=a, rationale="the definition is what the proposition builds on"),
        dict(kind="CHALLENGES", **{"from": t}, to=r, rationale="the critique disputes the report"),
        dict(kind="QUALIFIES", **{"from": p_}, to=a, rationale="adds the condition on scarcity"),
        dict(kind="DEFINES", **{"from": d}, to=p_, rationale="the proposition uses the defined construct"),
        dict(kind="ASSUMES", **{"from": a}, to=s_, rationale="the separation needs recipients who cannot see effort"),
        dict(kind="DEPENDS_ON", **{"from": k}, to=a, rationale="the contribution is the proposition"),
        dict(kind="EXTENDS", **{"from": p_}, to=d, rationale="builds a further proposition on the definition"),
        dict(kind="RECONCEPTUALIZES", **{"from": a}, to=r, rationale="recasts the attention account"),
        dict(kind="COMPLEMENTS", **{"from": a}, to=g, rationale="the gap and the proposition belong together"),
        dict(kind="CONTRADICTS_WITHIN_SCOPE", **{"from": a}, to=e_pre, rationale="one separates participation from engagement, the other merges them", shared_scope="engagement in work communication"),
        dict(kind="SAME_IDEA_PROPOSED", **{"from": p_}, to=q_pre, rationale="both say contributing makes the contributor visible",
             meaning_comparison="both treat visibility as the effect of contributing, whatever the wording"),
    ]
    ids = [add(w, inv, **p)["relation"] for p in proposals]
    v2 = view(w, inv)
    assert ids == [f"R{n:03d}" for n in range(1, 12)] and [r_["kind"] for r_ in v2["relations"]] == [p["kind"] for p in proposals]
    assert {r_["kind"] for r_ in v2["relations"]} == set(__import__("research_harness.taxonomy", fromlist=["x"]).vocabulary("claim_relation_kind"))
    for r_ in v2["relations"]:
        assert r_["status"] == "PROPOSED" and r_["confirmation"] == {"state": "UNCONFIRMED"} and r_["derivation"] == "researcher_proposed" and r_["provenance_class"] == "researcher_proposed_unconfirmed"
        assert r_["from"]["source_id"] and r_["from"]["source_revision"] == 1 and r_["from"]["passage_id"] and r_["to"]["passage_revision"] == 1 and r_["rationale"]
        assert r_["scope"]["compatibility"] in ("NOT_ASSESSED", "COMPATIBLE") and r_["needs_reassessment"] == [] and r_["reviews"] == []


def test_contradiction_within_scope_needs_the_shared_scope_and_scopes_that_differ_do_not_contradict(two):
    w, inv, v = two
    a, b = cid(v, "Participation always dilutes"), cid(v, "Participation can dilute")
    other = cid(v, "We argue that communicative")
    # two versions of one work are an alignment, not a relation
    assert code(add, w, inv, kind="CONTRADICTS_WITHIN_SCOPE", **{"from": a}, to=b, rationale="r", shared_scope="s") == "VERSIONS_OF_ONE_WORK"
    assert code(add, w, inv, kind="CONTRADICTS_WITHIN_SCOPE", **{"from": a}, to=other, rationale="r") == "FIELD_REQUIRED"
    assert code(add, w, inv, kind="CONTRADICTS_WITHIN_SCOPE", **{"from": a}, to=other, rationale="r", shared_scope="s", compatibility="DIFFERENT") == "SCOPES_DIFFER"
    ok = add(w, inv, kind="CONTRADICTS_WITHIN_SCOPE", **{"from": a}, to=other, rationale="r", shared_scope="attention in a shared channel")["relation"]
    r = next(x for x in view(w, inv)["relations"] if x["id"] == ok)
    assert r["scope"]["compatibility"] == "COMPATIBLE" and r["shared_scope"] == "attention in a shared channel"


def test_the_gate_refuses_what_the_claims_cannot_support(two):
    w, inv, v = two
    own, definition, assumption = cid(v, "We argue that communicative"), cid(v, "We define communicative"), cid(v, "We assume that recipients")
    eng = cid(v, "We argue that engagement is an investment")
    assert code(add, w, inv, kind="SIMILAR_TO", **{"from": own}, to=definition, rationale="r") == "PROPOSAL_KIND_NOT_ALLOWED"
    assert code(add, w, inv, kind="SUPPORTS", **{"from": own}, to=own, rationale="r") == "SAME_CLAIM"
    assert code(add, w, inv, kind="SUPPORTS", **{"from": own}, to="C999", rationale="r") == "CLAIM_UNKNOWN"
    assert code(add, w, inv, kind="SUPPORTS", **{"from": own}, to=definition, rationale="  ") == "FIELD_REQUIRED"
    assert code(add, w, inv, kind="DEFINES", **{"from": own}, to=definition, rationale="r") == "FROM_NOT_A_DEFINITION"
    assert code(add, w, inv, kind="ASSUMES", **{"from": own}, to=definition, rationale="r") == "TO_NOT_AN_ASSUMPTION"
    assert code(add, w, inv, kind="SAME_IDEA_PROPOSED", **{"from": own}, to=definition, rationale="r", meaning_comparison="m") == "SAME_WORK"                 # one paper
    assert code(add, w, inv, kind="SAME_IDEA_PROPOSED", **{"from": own}, to=eng, rationale="the wording is similar") == "FIELD_REQUIRED"                     # a meaning comparison, not similarity
    assert code(add, w, inv, kind="DEFINES", **{"from": definition}, to={"passage": "SRC_NOT_A_PASSAGE"}, rationale="r") == "TARGET_NOT_ALLOWED"
    add(w, inv, kind="DEFINES", **{"from": definition}, to=own, rationale="r")
    assert code(add, w, inv, kind="DEFINES", **{"from": definition}, to=own, rationale="again") == "DUPLICATE"
    add(w, inv, kind="COMPLEMENTS", **{"from": own}, to=assumption, rationale="r")
    assert code(add, w, inv, kind="COMPLEMENTS", **{"from": assumption}, to=own, rationale="the other way round") == "DUPLICATE"            # symmetric kinds


def test_a_relation_may_end_at_a_passage_for_the_kinds_that_allow_it(two):
    w, inv, v = two
    own = next(c for c in v["claims"] if c["id"] == cid(v, "We argue that communicative"))
    q = own["evidence"][1]
    rid = add(w, inv, kind="SUPPORTS", **{"from": cid(v, "We define communicative")}, to={"passage": q["passage_id"]}, rationale="the qualifier states when the definition matters")["relation"]
    r = next(x for x in view(w, inv)["relations"] if x["id"] == rid)
    assert r["to"]["passage"]["passage_id"] == q["passage_id"] and r["to"]["passage"]["quote"] == F.CR["own1_q"] and r["to"]["passage"]["passage_revision"] == q["passage_revision"]


def test_a_rejected_claim_cannot_be_related_and_a_correction_after_a_proposal_flags_it_for_reassessment(two):
    w, inv, v = two
    a, b = cid(v, "We argue that communicative"), cid(v, "We propose that participation")
    rid = add(w, inv, kind="QUALIFIES", **{"from": b}, to=a, rationale="adds scarcity")["relation"]
    assert [r for r in view(w, inv)["relations"] if r["id"] == rid][0]["needs_reassessment"] == []
    CL.review_claim(w["store"], w["token"], inv, b, "CORRECTED", "the role is a definition after all", corrections={"role": "DEFINITION"})
    r = [r for r in view(w, inv)["relations"] if r["id"] == rid][0]
    assert any(x.startswith(f"{b}: CHANGED_AFTER_PROPOSAL") for x in r["needs_reassessment"])
    CL.review_claim(w["store"], w["token"], inv, a, "REJECTED", "not a claim of this paper")
    assert code(add, w, inv, kind="SUPPORTS", **{"from": cid(v, "We define communicative")}, to=a, rationale="r") == "CLAIM_REJECTED"
    assert any("REJECTED_BY_RESEARCHER" in x for x in [r for r in view(w, inv)["relations"] if r["id"] == rid][0]["needs_reassessment"])


def test_a_revised_source_marks_every_claim_and_relation_that_rests_on_it(two):
    w, inv, v = two
    rid = add(w, inv, kind="COMPLEMENTS", **{"from": cid(v, "We argue that communicative")}, to=cid(v, "We argue that engagement is an investment"), rationale="r")["relation"]
    corpus.import_source(w["store"], w["token"], citation_key="cr", title=F.PAPERS["cr"]["title"], year=2024, text=F.CR_TEXT + "\nA late erratum.\n")
    v2 = view(w, inv)
    assert v2["currency"]["sources"]["status"] == "STALE" and v2["currency"]["all_current"] is False
    assert next(c for c in v2["claims"] if c["id"] == cid(v, "We argue that communicative"))["currency"]["status"] == "SOURCE_REVISED"
    assert any("SOURCE_REVISED" in x for x in next(r for r in v2["relations"] if r["id"] == rid)["needs_reassessment"])


def test_confirming_a_relation_is_the_researchers_decision_and_keeps_the_proposals_provenance(two):
    w, inv, v = two
    a, b = cid(v, "We argue that communicative"), cid(v, "We argue that engagement is an investment")
    rid = add(w, inv, kind="COMPLEMENTS", **{"from": a}, to=b, rationale="two ways of separating state from behaviour", missing_information=["whether the papers share a unit of analysis"])["relation"]
    assert code(CR.review_relation, w["store"], w["token"], inv, rid, "CONFIRMED", "they complement each other") == "MISSING_INFORMATION_NOT_ACKNOWLEDGED"
    assert code(CR.review_relation, w["store"], w["token"], inv, rid, "CONFIRMED", " ", acknowledge_missing=True) == "REASON_REQUIRED"
    assert code(CR.review_relation, w["store"], w["token"], inv, rid, "MAYBE", "x") == "DECISION_UNKNOWN"
    CR.review_relation(w["store"], w["token"], inv, rid, "CONFIRMED", "I read both; they complement each other", acknowledge_missing=True)
    r = next(x for x in view(w, inv)["relations"] if x["id"] == rid)
    assert r["confirmation"]["state"] == "CONFIRMED_BY_RESEARCHER" and r["confirmation"]["acknowledged_missing"] is True
    assert r["derivation"] == "researcher_proposed" and r["status"] == "PROPOSED" and r["provenance_class"] == "researcher_confirmed"          # confirmation changes the class shown, not what it is
    CR.review_relation(w["store"], w["token"], inv, rid, "NEEDS_SCOPE", "the units differ after all")
    assert next(x for x in view(w, inv)["relations"] if x["id"] == rid)["confirmation"]["state"] == "NEEDS_SCOPE"


def test_a_models_proposals_go_through_the_gate_and_stay_proposed_with_their_model_as_provenance(two):
    w, inv, v = two
    a, b, d = cid(v, "We argue that communicative"), cid(v, "We argue that engagement is an investment"), cid(v, "We define communicative")
    answer = {"proposals": [
        {"kind": "COMPLEMENTS", "from": a, "to": b, "rationale": "different ends of the same distinction", "missing_information": ["the unit of analysis of the second paper"]},
        {"kind": "SAME_IDEA_PROPOSED", "from": a, "to": b, "rationale": "the words are similar", "missing_information": []},                       # no meaning comparison: refused
        {"kind": "DEFINES", "from": a, "to": b, "rationale": "x", "missing_information": []},                                                        # from is not a definition: refused
        {"kind": "SUPPORTS", "from": d, "to": a, "rationale": "no missing list given"},                                                              # a model must say what is missing: refused
        {"kind": "CONTRADICTS_WITHIN_SCOPE", "from": a, "to": b, "rationale": "x", "shared_scope": "s", "missing_information": [], "levels": {a: "group", b: "dyad"}},    # levels differ: refused
        {"kind": "CHALLENGES", "from": "C777", "to": a, "rationale": "x", "missing_information": []}]}
    ad = ScriptedModelAdapter(model_id="scripted-critic", default_response=answer)
    out = CR.propose_relations(w["store"], w["token"], inv, ad)
    assert out["status"] == "PROPOSED" and len(out["accepted"]) == 1
    assert sorted(r["code"] for r in out["rejected"]) == ["CLAIM_UNKNOWN", "FIELD_REQUIRED", "FIELD_REQUIRED", "FROM_NOT_A_DEFINITION", "LEVELS_DIFFER"]
    r = view(w, inv)["relations"][0]
    assert r["derivation"] == "model_proposed" and r["generated_by"] == "model:scripted-critic" and r["provenance"] == "suite_proposed:model:scripted-critic"
    assert r["confirmation"]["state"] == "UNCONFIRMED" and r["provenance_class"] == "model_synthesised"
    assert len(ad.recorded_requests) == 1 and "CLAIMS OF THE INVENTORY" in ad.recorded_requests[0].user_prompt
    with w["store"].read() as conn:
        n = conn.execute("SELECT COUNT(*) FROM workflow_runs WHERE workflow='claim_relations'").fetchone()[0]
    assert n == 1


def test_a_model_that_returns_nonsense_changes_nothing_and_the_failure_is_registered(two):
    w, inv, v = two
    before = w["store"].get_object(inv)["revision"]
    out = CR.propose_relations(w["store"], w["token"], inv, ScriptedModelAdapter(model_id="bad", default_response="not json at all"))
    assert out["status"] == "FAILED" and out["reason"] == "invalid_output" and w["store"].get_object(inv)["revision"] == before
    assert code(CR.propose_relations, w["store"], w["token"], inv, ScriptedModelAdapter(), kinds=["MADE_UP"]) == "PROPOSAL_KIND_NOT_ALLOWED"


def test_similar_wording_never_creates_a_relation_a_candidate_is_only_a_claim_to_read(two):
    w, inv, v = two
    before = view(w, inv)["relations"]
    cands = CL.candidates(w["store"], inv, cid(v, "We argue that communicative"), limit=5)
    assert cands["candidates"] and all(x["work_id"] != v["claims"][0]["work_id"] for x in cands["candidates"]) and "not a relation" in cands["note"]
    assert view(w, inv)["relations"] == before == []
    assert code(CL.candidates, w["store"], "CINV_999999", "C001") == "INVENTORY_NOT_FOUND"


def test_a_confirmed_relation_to_a_claim_of_the_cited_source_is_what_counts_as_checking_a_report(w):
    alder_text = F.paper("Attention as a Group Resource", "Attention is limited.", [("1 Argument", ["Collective attention is a finite resource that group members divide among shared topics."])])
    alder = corpus.import_source(w["store"], w["token"], citation_key="alder2018", title="Attention as a Group Resource", year=2018, text=alder_text)["object_id"]
    script = {**F.SCRIPT, "alder2018": {"RP_CLAIMS": [F.rec("theoretical_proposition", "Collective attention is a finite resource that group members divide among shared topics.", F.OWN)]}}
    inv = CL.build(w["store"], w["token"], [w["ids"]["cr"], alder], F.reader(script))["inventory_id"]
    v = view(w, inv)
    reported, theirs = cid(v, "Alder (2018) argues"), cid(v, "Collective attention is a finite resource")
    CL.link_cited_source(w["store"], w["token"], inv, reported, "Alder (2018)", alder)
    assert [x for x in view(w, inv)["claims"] if x["id"] == reported][0]["cited_works"][0]["checked_against_cited_source"] is False
    rid = add(w, inv, kind="SAME_IDEA_PROPOSED", **{"from": reported}, to=theirs, rationale="the report states the cited paper's proposition",
              meaning_comparison="both say attention is a finite pool divided among topics")["relation"]
    assert [x for x in view(w, inv)["claims"] if x["id"] == reported][0]["cited_works"][0]["checked_against_cited_source"] is False       # proposed is not checked
    CR.review_relation(w["store"], w["token"], inv, rid, "CONFIRMED", "I read the cited paper; the report is faithful")
    assert [x for x in view(w, inv)["claims"] if x["id"] == reported][0]["cited_works"][0]["checked_against_cited_source"] is True


# ------------------------------------------------------------------------------------------------ alignments across versions

def al(w, inv, **p):
    return CR.add_alignment(w["store"], w["token"], inv, p)["alignment"]


def test_each_change_between_versions_is_a_proposal_that_names_both_claims_and_copies_nothing(two):
    w, inv, v = two
    pre, jour = w["ids"]["pre"], w["ids"]["jour"]
    n_claims = len(v["claims"])
    made = [
        al(w, inv, change="UNCHANGED", earlier=next(x["id"] for x in v["claims"] if x["source_id"] == pre and "an investment of the self" in x["wording"]["quote"]),
           later=next(x["id"] for x in v["claims"] if x["source_id"] == jour and "an investment of the self" in x["wording"]["quote"]), rationale="same proposition"),
        al(w, inv, change="QUALIFIED", earlier=next(x["id"] for x in v["claims"] if x["wording"]["quote"] == F.V["q_pre"]), later=next(x["id"] for x in v["claims"] if x["wording"]["quote"] == F.V["q_post"]),
           what_changed="a condition was added", conditions_added=["small teams"], rationale="the later version limits the claim to small teams"),
        al(w, inv, change="WEAKENED", earlier=next(x["id"] for x in v["claims"] if x["wording"]["quote"] == F.V["w_pre"]), later=next(x["id"] for x in v["claims"] if x["wording"]["quote"] == F.V["w_post"]),
           what_changed="always became can", rationale="a universal became a possibility"),
        al(w, inv, change="REVERSED", earlier=next(x["id"] for x in v["claims"] if x["wording"]["quote"] == F.V["r_pre"]), later=next(x["id"] for x in v["claims"] if x["wording"]["quote"] == F.V["r_post"]),
           what_changed="increases became reduces", rationale="opposite direction"),
        al(w, inv, change="WITHDRAWN", earlier=next(x["id"] for x in v["claims"] if x["wording"]["quote"] == F.V["only_pre"]), later_source=jour, what_changed="absent from the later version",
           rationale="no counterpart found", missing_information=["whether the journal version states it elsewhere"]),
        al(w, inv, change="NEW_IN_LATER_VERSION", later=next(x["id"] for x in v["claims"] if x["wording"]["quote"] == F.V["only_post"]), earlier_source=pre, what_changed="appears only in the journal version",
           rationale="no counterpart in the preprint", missing_information=["whether the preprint hints at it"]),
    ]
    q_pair = (next(x["id"] for x in v["claims"] if x["wording"]["quote"] == F.V["q_pre"]), next(x["id"] for x in v["claims"] if x["wording"]["quote"] == F.V["q_post"]))
    w_pair = (next(x["id"] for x in v["claims"] if x["wording"]["quote"] == F.V["w_pre"]), next(x["id"] for x in v["claims"] if x["wording"]["quote"] == F.V["w_post"]))
    assert code(al, w, inv, change="NARROWED", earlier=q_pair[0], later=q_pair[1], what_changed="x", rationale="x") == "PAIR_ALREADY_ALIGNED"       # one standing proposal per pair
    CR.review_alignment(w["store"], w["token"], inv, made[1], "DISMISSED", "it is a narrowing, not only a qualification")
    CR.review_alignment(w["store"], w["token"], inv, made[2], "DISMISSED", "the later wording is stronger than I first read")
    al(w, inv, change="NARROWED", earlier=q_pair[0], later=q_pair[1], what_changed="scope narrowed to small teams", rationale="a narrowing")
    al(w, inv, change="STRENGTHENED", earlier=w_pair[0], later=w_pair[1], what_changed="x", rationale="x")
    v2 = view(w, inv)
    assert len(v2["alignments"]) == 8 + 0 and len(v2["claims"]) == n_claims                                                # no claim was created or moved
    for a in v2["alignments"]:
        dismissed = a["id"] in (made[1], made[2])
        assert a["status"] == "PROPOSED" and a["confirmation"]["state"] == ("DISMISSED_BY_RESEARCHER" if dismissed else "UNCONFIRMED") and a["work_id"] == "W_ENG" and a["needs_reassessment"] == []
        for end in (a["earlier"], a["later"]):
            if end:
                assert end["source_id"] in (pre, jour) and end["source_revision"] == 1
    assert {a["change"] for a in v2["alignments"]} == {"UNCHANGED", "QUALIFIED", "WEAKENED", "REVERSED", "NARROWED", "STRENGTHENED", "WITHDRAWN", "NEW_IN_LATER_VERSION"}
    assert {c["source_id"] for c in v2["claims"] if c["wording"]["quote"] == F.V["same"]} == {pre, jour}              # the same sentence stays two claims, one per version
    w_ = next(a for a in v2["alignments"] if a["change"] == "WITHDRAWN")
    assert w_["coverage_caveat"]["source_id"] == jour and "not proof" in w_["coverage_caveat"]["note"] and w_["order_basis"] is None


def test_an_alignment_is_refused_unless_both_claims_are_versions_of_one_work_in_the_right_order(two):
    w, inv, v = two
    pre, jour = w["ids"]["pre"], w["ids"]["jour"]
    p = next(x["id"] for x in v["claims"] if x["wording"]["quote"] == F.V["w_pre"])
    j = next(x["id"] for x in v["claims"] if x["wording"]["quote"] == F.V["w_post"])
    cr_claim = cid(v, "We argue that communicative")
    assert code(al, w, inv, change="WEAKENED", earlier=p, later=cr_claim, what_changed="x", rationale="x") == "DIFFERENT_WORKS"
    assert code(al, w, inv, change="WEAKENED", earlier=p, later=p, what_changed="x", rationale="x") == "SAME_VERSION"
    assert code(al, w, inv, change="WEAKENED", earlier=j, later=p, what_changed="x", rationale="x") == "ORDER_REVERSED"
    assert code(al, w, inv, change="WEAKENED", earlier=p, rationale="x", what_changed="x") == "BOTH_CLAIMS_REQUIRED"
    assert code(al, w, inv, change="QUALIFIED", earlier=p, later=j, what_changed="x", rationale="x") == "CONDITIONS_REQUIRED"
    assert code(al, w, inv, change="WITHDRAWN", earlier=p, later_source=jour, what_changed="x", rationale="x") == "MISSING_INFORMATION_REQUIRED"
    assert code(al, w, inv, change="WITHDRAWN", earlier=p, what_changed="x", rationale="x", missing_information=["m"]) == "OTHER_VERSION_REQUIRED"
    assert code(al, w, inv, change="WITHDRAWN", earlier=p, later_source=w["ids"]["cr"], what_changed="x", rationale="x", missing_information=["m"]) == "NOT_ANOTHER_VERSION"
    assert code(al, w, inv, change="MADE_UP", earlier=p, later=j, rationale="x") == "CHANGE_UNKNOWN"
    ok = al(w, inv, change="WEAKENED", earlier=p, later=j, what_changed="always became can", rationale="r")
    assert code(al, w, inv, change="WEAKENED", earlier=p, later=j, what_changed="again", rationale="r") == "DUPLICATE"
    assert code(al, w, inv, change="REVERSED", earlier=p, later=j, what_changed="again", rationale="r") == "PAIR_ALREADY_ALIGNED"
    assert ok == "A001"


def test_when_the_dates_cannot_order_two_versions_the_researcher_must_say_which_is_earlier(tmp_path):
    undated = {k: {**v, "meta": {"work_id": "W_X", "version_id": k}} for k, v in F.PAPERS.items() if k in ("pre", "jour")}
    w = F.make_store(tmp_path, undated)
    inv = inv_of(w, "pre", "jour")
    v = view(w, inv)
    p = next(x["id"] for x in v["claims"] if x["wording"]["quote"] == F.V["w_pre"])
    j = next(x["id"] for x in v["claims"] if x["wording"]["quote"] == F.V["w_post"])
    assert code(al, w, inv, change="WEAKENED", earlier=p, later=j, what_changed="x", rationale="x") == "ORDER_UNKNOWN"
    aid = al(w, inv, change="WEAKENED", earlier=p, later=j, what_changed="x", rationale="x", order_stated_by_researcher=True)
    assert next(a for a in view(w, inv)["alignments"] if a["id"] == aid)["order_basis"] == "stated_by_researcher"


def test_a_model_proposes_alignments_through_the_same_gate_and_a_work_with_one_version_has_nothing_to_align(two, w):
    w_, inv, v = two
    p = next(x["id"] for x in v["claims"] if x["wording"]["quote"] == F.V["r_pre"])
    j = next(x["id"] for x in v["claims"] if x["wording"]["quote"] == F.V["r_post"])
    answer = {"proposals": [{"change": "REVERSED", "earlier": p, "later": j, "what_changed": "increases became reduces", "rationale": "opposite direction", "missing_information": []},
                            {"change": "WITHDRAWN", "earlier": p, "later_source": w_["ids"]["jour"], "what_changed": "x", "rationale": "x", "missing_information": []},
                            {"change": "UNCHANGED", "earlier": p, "later": j, "rationale": "x", "missing_information": []}]}
    out = CR.propose_alignments(w_["store"], w_["token"], inv, ScriptedModelAdapter(model_id="scripted-aligner", default_response=answer))
    assert len(out["accepted"]) == 1 and sorted(r["code"] for r in out["rejected"]) == ["MISSING_INFORMATION_REQUIRED", "PAIR_ALREADY_ALIGNED"]
    a = view(w_, inv)["alignments"][0]
    assert a["derivation"] == "model_proposed" and a["confirmation"]["state"] == "UNCONFIRMED" and a["provenance_class"] == "model_synthesised"
    CR.review_alignment(w_["store"], w_["token"], inv, a["id"], "CONFIRMED", "I read both versions; the direction did reverse")
    assert next(x for x in view(w_, inv)["alignments"] if x["id"] == a["id"])["provenance_class"] == "researcher_confirmed"
    only_cr = inv_of(w_, "cr")
    assert code(CR.propose_alignments, w_["store"], w_["token"], only_cr, ScriptedModelAdapter()) == "NO_VERSIONS"


def test_alignment_candidates_are_only_words_in_common_and_write_nothing(two):
    w, inv, v = two
    rev = w["store"].get_object(inv)["revision"]
    out = CR.alignment_candidates(w["store"], inv, "W_ENG")
    assert out["pairs"] and "alignment is a proposal" in out["note"]
    reversed_pair = next(p for p in out["pairs"] if any(x["wording"]["quote"] == F.V["r_pre"] and x["id"] == p["earlier"] for x in v["claims"]))
    best = reversed_pair["candidates"][0]["claim"]
    assert next(x for x in v["claims"] if x["id"] == best)["wording"]["quote"] == F.V["r_post"]              # the reversed claim is the nearest by words: similarity is not identity
    assert w["store"].get_object(inv)["revision"] == rev and view(w, inv)["alignments"] == []
