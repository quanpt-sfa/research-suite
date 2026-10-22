"""WP5 review follow-up (2026-10-22): R2 must not lose what R1 found; a run knows when its problem changed; versions are judged from everything held, with
evidence kept at Source@revision."""
from __future__ import annotations

import pytest

from research_harness.discovery import problem as P
from research_harness.discovery import retrieval as RT
from research_harness.knowledge import corpus
from research_harness.knowledge.common import KnowledgeError
from research_harness.state.store import StateStore
from tests import retrieval_bench as RB

STATEMENT = "Financial reporting fraud is hard to detect, and detection models may change what fraudsters do."


def put(store, token, key, title, body, **meta):
    corpus.import_source(store, token, citation_key=key, title=title, text=f"{title}\n\nAbstract\n{body}\n\n1 Introduction\n{body}\n\nReferences\n[1] A reference.\n", **meta)


# ------------------------------------------------------------------------------------------------ 1. R2 never removes what R1 found

def test_merge_keeps_every_source_of_either_list_and_says_where_each_stood():
    r1 = [{"source_id": s, "score": 5 - i} for i, s in enumerate("ABC")]
    r2 = [{"source_id": s, "score": 9 - i} for i, s in enumerate("DEA")]
    m = RT.merge_stages(r1, r2)
    assert {h["source_id"] for h in m} == set("ABCDEA")
    by = {h["source_id"]: h for h in m}
    assert (by["A"]["r1_rank"], by["A"]["r2_rank"]) == (1, 3) and (by["B"]["r1_rank"], by["B"]["r2_rank"]) == (2, None) and (by["D"]["r1_rank"], by["D"]["r2_rank"]) == (None, 1)
    # placed by the better of its two ranks; at equal rank the one R1 found comes first
    assert [h["source_id"] for h in m] == ["A", "D", "B", "E", "C"] and [h["rank"] for h in m] == [1, 2, 3, 4, 5]


@pytest.fixture
def pushed(tmp_path):
    """alpha-only paper A is R1's first hit; accepting the expansion 'gamma' lets three other papers outrank it in the R2 list."""
    store, token = StateStore.create(tmp_path / "h", "PRJ_PUSH", owner_id="researcher")
    put(store, token, "a", "Paper Alpha", "alpha alpha alpha alpha study of alpha")
    for n in range(1, 4):
        put(store, token, f"g{n}", f"Paper Gamma {n}", "alpha gamma gamma gamma gamma gamma gamma gamma gamma gamma")
    pid = P.new_problem(store, token, statement="We want to understand the alpha phenomenon in detail.")["problem_id"]
    return store, token, pid


def test_an_accepted_expansion_cannot_push_an_r1_hit_out_of_the_results(pushed):
    """Before: only the R2 list was kept, so the paper R1 ranked first vanished once 'gamma' was accepted."""
    store, token, pid = pushed
    rid = RT.new_run(store, token, pid, terms=["alpha"], top_n=2)["retrieval_id"]
    RT.search(store, token, rid)
    r1_ids = {h["source_id"] for h in store.get_object(rid)["content"]["hits"] if h["tier"] == "FULL_TEXT"}
    assert "SRC_A" in r1_ids
    RT.decide_expansions(store, token, rid, rationale="the literature also says gamma", add=("gamma",))
    RT.search(store, token, rid)
    c = store.get_object(rid)["content"]
    hits = {h["source_id"]: h for h in c["hits"] if h["tier"] == "FULL_TEXT"}
    assert r1_ids <= set(hits)                                                      # nothing R1 found was lost
    a = hits["SRC_A"]
    assert a["stage"] == "R1" and a["queries"][0]["r1_rank"] == 1 and a["queries"][0]["r2_rank"] is None
    assert {"source_id": "SRC_A", "query": "Q1", "tier": "FULL_TEXT", "r1_rank": 1} in c["coverage"]["stages"]["displaced_by_expansion"]
    assert any(h["stage"] == "R2" for h in hits.values()) and "twice that" in " ".join(c["coverage"]["limits"])
    assert hits["SRC_A"]["rank"] <= 2           # placed by its better rank: it does not drop to the bottom because the expansion outscored it


# ------------------------------------------------------------------------------------------------ 2. the problem a run was made for

def searched_run(w, **kw):
    rid = RT.new_run(w["store"], w["token"], w["pid"], terms=["financial reporting fraud detection"], **kw)["retrieval_id"]
    RT.search(w["store"], w["token"], rid)
    return rid


@pytest.fixture
def world(tmp_path):
    store, token = StateStore.create(tmp_path / "home", "PRJ_CUR", owner_id="researcher")
    RB.build(store, token)
    pid = P.new_problem(store, token, statement=STATEMENT, interpretations=["Detection raises the cost of manipulation, so fraud moves elsewhere."])["problem_id"]
    return {"store": store, "token": token, "pid": pid}


def test_a_changed_problem_makes_the_run_stale_for_the_problem_not_for_the_corpus(world):
    """Before: only the corpus and the accepted terms were compared, so a run for the old statement still showed as current."""
    w = world
    rid = searched_run(w)
    cur = RT.run_view(w["store"], rid)["currency"]
    assert cur["corpus"]["status"] == "CURRENT" and cur["problem"]["status"] == "CURRENT" and cur["search_config"]["status"] == "CURRENT" and cur["all_current"]
    P.revise_problem(w["store"], w["token"], w["pid"], rationale="the problem is about disclosure, not fraud as a whole",
                     statement="Firms seem to change how they disclose after detection models become widely known.")
    v = RT.run_view(w["store"], rid)["currency"]
    assert v["corpus"]["status"] == "CURRENT" and v["problem"]["status"] == "STALE" and v["problem"]["changed"] == ["statement"] and v["all_current"] is False
    assert v["problem"]["recorded_revision"] == 1 and v["problem"]["current_revision"] == 2
    with pytest.raises(KnowledgeError) as e:
        RT.search(w["store"], w["token"], rid)
    assert e.value.code == "RETRIEVAL_PROBLEM_STALE" and "--parent" in e.value.message
    with pytest.raises(KnowledgeError, match="RETRIEVAL_PROBLEM_STALE"):
        RT.require_current(w["store"], rid)
    # a new run, made from the new statement, is current again and says which run it refines
    new = RT.new_run(w["store"], w["token"], w["pid"], terms=["disclosure"], parent=rid)["retrieval_id"]
    RT.search(w["store"], w["token"], new)
    assert RT.require_current(w["store"], new)["currency"]["all_current"] is True


def test_a_revision_that_touches_nothing_the_queries_were_made_from_does_not_make_a_run_stale(world):
    w = world
    rid = searched_run(w)
    P.add_interpretation(w["store"], w["token"], w["pid"], "Auditors, not managers, change what they flag.")
    p = RT.run_view(w["store"], rid)["currency"]["problem"]
    assert p["status"] == "CURRENT" and p["current_revision"] == 2 and "not in anything these queries were made from" in p["note"]
    P.revise_problem(w["store"], w["token"], w["pid"], rationale="narrow the population", scope={"population": "US listed firms"})
    assert RT.run_view(w["store"], rid)["currency"]["problem"]["changed"] == ["scope"]


def test_a_run_made_from_a_chosen_reading_is_stale_when_the_choice_changes(world):
    w = world
    P.choose(w["store"], w["token"], w["pid"], ["I1"], "this reading")
    rid = RT.new_run(w["store"], w["token"], w["pid"])["retrieval_id"]
    RT.search(w["store"], w["token"], rid)
    P.add_interpretation(w["store"], w["token"], w["pid"], "Auditors, not managers, change what they flag.")
    P.choose(w["store"], w["token"], w["pid"], ["I1", "I2"], "both readings now")
    assert RT.run_view(w["store"], rid)["currency"]["problem"]["changed"] == ["chosen"]


def test_the_three_currencies_are_independent_and_a_later_step_must_pass_them(world):
    w = world
    rid = searched_run(w)
    assert RT.require_current(w["store"], rid)["state"] == "SEARCHED"
    RT.decide_expansions(w["store"], w["token"], rid, rationale="other names", add=("earnings manipulation",))
    cur = RT.run_view(w["store"], rid)["currency"]
    assert (cur["corpus"]["status"], cur["problem"]["status"], cur["search_config"]["status"]) == ("CURRENT", "CURRENT", "STALE")
    with pytest.raises(KnowledgeError, match="RETRIEVAL_CONFIG_STALE"):
        RT.require_current(w["store"], rid)
    RT.search(w["store"], w["token"], rid)
    corpus.import_source(w["store"], w["token"], citation_key="late", title="A Late Paper", text="A Late Paper\n\nAbstract\nFraud.\n\n1 Introduction\nFraud detection.\n")
    cur2 = RT.run_view(w["store"], rid)["currency"]
    assert (cur2["corpus"]["status"], cur2["problem"]["status"], cur2["search_config"]["status"]) == ("STALE_FOR_CORPUS", "CURRENT", "CURRENT")
    with pytest.raises(KnowledgeError, match="RETRIEVAL_CORPUS_STALE"):
        RT.require_current(w["store"], rid)
    assert RT.require_current(w["store"], rid, allow_stale_corpus=True)["state"] == "SEARCHED"          # an explicit choice to build on the old snapshot
    draft = RT.new_run(w["store"], w["token"], w["pid"])["retrieval_id"]
    with pytest.raises(KnowledgeError, match="RETRIEVAL_NOT_SEARCHED"):
        RT.require_current(w["store"], draft)


# ------------------------------------------------------------------------------------------------ 3. versions of a work

def test_the_representative_comes_from_every_held_version_and_evidence_stays_at_source_revision(tmp_path):
    """Before: the versions listed, and the representative, came only from the hits."""
    store, token = StateStore.create(tmp_path / "h", "PRJ_VER", owner_id="researcher")
    common = dict(document_type="working_paper", review_status="NOT_REVIEWED", date_evidence="repository posting record (synthetic)", work_id="W_X")
    put(store, token, "x1", "Disclosure After Detection", "disclosure disclosure disclosure after detection of reporting fraud disclosure", first_public_date="2024-02-01", version_id="v1", **common)
    put(store, token, "x2", "Disclosure After Detection", "a rewritten version about something else entirely: auditors", first_public_date="2025-01-15", version_id="v2", **common)
    put(store, token, "y1", "An Unrelated Paper", "bank runs and deposit insurance")
    pid = P.new_problem(store, token, statement="Disclosure choices after the detection of reporting fraud are not well understood.")["problem_id"]
    rid = RT.new_run(store, token, pid, terms=["disclosure detection"], top_n=1)["retrieval_id"]
    RT.search(store, token, rid)
    g = next(x for x in RT.run_view(store, rid)["reading_list"] if x["work_id"] == "W_X")
    assert [v["source_id"] for v in g["versions"]] == ["SRC_X1", "SRC_X2"]                       # both held versions, not only the retrieved one
    assert {v["source_id"]: v["retrieved"] for v in g["versions"]} == {"SRC_X1": True, "SRC_X2": False}
    assert g["representative"] == "SRC_X2" and "latest first_public_date among all held versions" in g["representative_basis"]
    assert g["best_hit"] == {"source_id": "SRC_X1", "source_revision": 1} and "Source@revision" in g["provenance_note"]
    hit = next(h for h in store.get_object(rid)["content"]["hits"] if h["work_id"] == "W_X")
    assert (hit["source_id"], hit["source_revision"]) == ("SRC_X1", 1) and hit["locator"].startswith("SRC_X1")         # the evidence is at the version that was read


def test_without_public_dates_the_best_ranked_version_is_shown_and_says_so(tmp_path):
    store, token = StateStore.create(tmp_path / "h", "PRJ_VER2", owner_id="researcher")
    put(store, token, "x1", "Disclosure After Detection", "disclosure disclosure after detection of reporting fraud", work_id="W_X", version_id="v1")
    put(store, token, "x2", "Disclosure After Detection", "a different version about auditors", work_id="W_X", version_id="v2")
    pid = P.new_problem(store, token, statement="Disclosure choices after the detection of reporting fraud are not well understood.")["problem_id"]
    rid = RT.new_run(store, token, pid, terms=["disclosure detection"], top_n=1)["retrieval_id"]
    RT.search(store, token, rid)
    g = RT.run_view(store, rid)["reading_list"][0]
    assert g["representative"] == "SRC_X1" and g["representative_basis"].startswith("no public date recorded")
    assert [v["source_id"] for v in g["versions"]] == ["SRC_X1", "SRC_X2"]
