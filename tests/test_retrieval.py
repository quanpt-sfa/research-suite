"""M5 WP5: internal retrieval (R1, R2, R3a, R5), corpus tiers and snapshots, coverage, working-paper versions, and the gold benchmark."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_harness.cli import main
from research_harness.core.errors import AuthorizationError
from research_harness.discovery import citations as C
from research_harness.discovery import problem as P
from research_harness.discovery import retrieval as RT
from research_harness.discovery import text_index as X
from research_harness.discovery.retrieval_eval import evaluate
from research_harness.export.manifest import verify_export, write_export
from research_harness.export.retrieval import build_retrieval_export
from research_harness.knowledge import corpus
from research_harness.knowledge.common import KnowledgeError
from research_harness.protocols import schema
from research_harness.state import workflow_runs
from research_harness.state.actors import register_actor
from research_harness.state.store import StateStore
from research_harness.validation.integrity import audit_project_integrity
from research_harness.workflows.adapters import ModelError, ScriptedModelAdapter
from tests import retrieval_bench as RB

BASELINE = json.loads((Path(__file__).parent / "fixtures" / "retrieval_bench" / "baseline.json").read_text(encoding="utf-8"))
STATEMENT = "Financial reporting fraud is hard to detect, and detection models may change what fraudsters do."


@pytest.fixture(scope="module")
def bench(tmp_path_factory):
    """One imported benchmark corpus shared by the read-only tests."""
    tmp = tmp_path_factory.mktemp("bench")
    store, token = StateStore.create(tmp / "home", "PRJ_BENCH", owner_id="researcher")
    return {"store": store, "token": token, "tmp": tmp, **RB.build(store, token)}


@pytest.fixture
def world(tmp_path):
    store, token = StateStore.create(tmp_path / "home", "PRJ_RET", owner_id="researcher")
    b = RB.build(store, token)
    pid = P.new_problem(store, token, statement=STATEMENT, interpretations=["Detection raises the cost of manipulation, so fraud moves elsewhere."])["problem_id"]
    return {"store": store, "token": token, "tmp": tmp_path, "home": tmp_path / "home", "pid": pid, **b}


def fresh_run(w, **kw):
    kw.setdefault("terms", ["financial reporting fraud detection"])
    return RT.new_run(w["store"], w["token"], w["pid"], **kw)["retrieval_id"]


def expansions(*terms):
    return ScriptedModelAdapter(model_id="scripted-expander", default_response={"terms": [{"term": t, "rationale": "another name"} for t in terms]})


# ------------------------------------------------------------------------------------------------ the index

def test_tokenizer_is_light_and_deterministic():
    assert X.tokenize("Financial Reporting Frauds, the Misstatements of 2019!") == ["financial", "reporting", "fraud", "misstatement"]
    assert X.tokenize("Analysis of accruals and bias") == ["analysis", "accrual", "bias"]      # -is, -as and -ss are not plural endings
    assert X.tokenize("") == [] and X.tokenize("the of and") == []
    assert X.phrase_tokens(["earnings manipulation", "AAER"]) == ["earning", "manipulation", "aaer"]


def test_bm25_prefers_rare_terms_and_is_reproducible():
    docs = {"d1": X.tokenize("fraud detection model"), "d2": X.tokenize("detection of tumours detection detection"), "d3": X.tokenize("bank runs and panics")}
    bm = X.Bm25(docs)
    s1, s2 = bm.score(X.tokenize("fraud detection")), bm.score(X.tokenize("fraud detection"))
    assert s1 == s2 and s1["d1"] > s1["d2"] and "d3" not in s1
    assert X.best_per_source({"u1": 1.0, "u2": 1.0, "u3": 0.5}, {"u1": "B", "u2": "A", "u3": "A"}) == [("A", 1.0, "u2"), ("B", 1.0, "u1")]       # ties by id


# ------------------------------------------------------------------------------------------------ the gold benchmark

def test_benchmark_does_not_drop_below_the_recorded_baseline(bench):
    r = evaluate(bench["store"], bench["gold"])
    for name in ("R1", "R1+R2"):
        for m in ("recall_at_k", "precision_at_k", "reciprocal_rank"):
            assert r["mean"][name][m] >= BASELINE["mean"][name][m] - 1e-9, (name, m, r["mean"][name][m], BASELINE["mean"][name][m])
    for q in r["queries"]:
        for name in ("R1", "R1+R2"):
            for m in ("recall_at_k", "precision_at_k", "reciprocal_rank"):
                assert q[name][m] >= BASELINE["queries"][q["id"]][name][m] - 1e-9


def test_other_names_for_an_idea_are_found_only_with_accepted_expansions(bench):
    q1 = next(q for q in evaluate(bench["store"], bench["gold"])["queries"] if q["id"] == "Q1")
    assert q1["R1+R2"]["recall_at_k"] >= q1["R1"]["recall_at_k"] + 0.25 and q1["R1+R2"]["precision_at_k"] > q1["R1"]["precision_at_k"]
    only = set(q1["found_only_with_expansions"])
    assert {"SRC_B01", "SRC_C01"} <= only and not (only & {s for s in bench["ids"]["A"]})        # family B and C need the other names; family A does not
    assert q1["R1"]["first_relevant_rank"] == 1


def test_relevant_papers_that_are_not_readable_are_reported_apart_from_recall(bench):
    q1 = next(q for q in evaluate(bench["store"], bench["gold"])["queries"] if q["id"] == "Q1")
    assert q1["relevant_not_readable"]["SRC_B04"]["tier"] == "ABSTRACT_ONLY" and q1["relevant_not_readable"]["SRC_C03"]["tier"] == "METADATA_ONLY"
    assert "SRC_B04" not in q1["R1+R2"]["found_in_top_k"] and q1["relevant"] == 12          # the 12 readable ones are the denominator


# ------------------------------------------------------------------------------------------------ tiers, versions, metadata

def test_metadata_only_and_abstract_only_sources_can_never_be_quoted(bench):
    store, token = bench["store"], bench["token"]
    assert corpus.source_tier(store.get_object("SRC_B04")["content"]) == "ABSTRACT_ONLY" and corpus.source_tier(store.get_object("SRC_C03")["content"]) == "METADATA_ONLY"
    assert corpus.source_tier(store.get_object("SRC_A01")["content"]) == "FULL_TEXT"
    for sid in ("SRC_B04", "SRC_C03"):
        with pytest.raises(KnowledgeError, match="FULLTEXT_"):
            corpus.add_passage(store, token, source_id=sid, quote="discretionary accruals")


def test_first_public_date_needs_evidence_and_enums_are_checked(tmp_path):
    store, token = StateStore.create(tmp_path / "h", "PRJ_META", owner_id="researcher")
    for kw, code in ((dict(first_public_date="2025-03-10"), "DATE_EVIDENCE_REQUIRED"), (dict(first_public_date="March 2025", date_evidence="x"), "DATE_INVALID"),
                     (dict(document_type="blog"), "DOCUMENT_TYPE_UNKNOWN"), (dict(review_status="maybe"), "REVIEW_STATUS_UNKNOWN"),
                     (dict(relation_to_researcher="friend"), "RELATION_UNKNOWN"), (dict(nonsense=1), "UNKNOWN_SOURCE_FIELDS")):
        with pytest.raises(KnowledgeError, match=code):
            corpus.import_metadata_only(store, token, citation_key="wp1", title="A working paper title", **kw)
    out = corpus.import_metadata_only(store, token, citation_key="wp1", title="A working paper title", document_type="working_paper", review_status="NOT_REVIEWED",
                                      first_public_date="2025-03-10", date_evidence="repository posting record", work_id="W_X", version_id="v1")
    c = store.get_object(out["object_id"])["content"]
    assert out["tier"] == "METADATA_ONLY" and c["fulltext_status"] == "MISSING" and c["document_type"] == "working_paper" and "artifact_sha256" not in c
    import jsonschema
    jsonschema.validate(c, schema.content_schema("Source"))
    with pytest.raises(KnowledgeError, match="TITLE_REQUIRED"):
        corpus.import_metadata_only(store, token, citation_key="x", title=" ")


def test_versions_of_one_work_are_one_entry_with_the_latest_as_representative(world):
    w = world
    rid = fresh_run(w)
    RT.decide_expansions(w["store"], w["token"], rid, rationale="the other names the literature uses", accept=(), add=("earnings manipulation", "AAER", "accounting irregularities"))
    RT.search(w["store"], w["token"], rid)
    v = RT.run_view(w["store"], rid)
    work = next(g for g in v["reading_list"] if g["work_id"] == "W_B02")
    assert [x["source_id"] for x in work["versions"]] == ["SRC_B02V1", "SRC_B02V2", "SRC_B02J"] and work["representative"] == "SRC_B02J"
    assert [x["review_status"] for x in work["versions"]] == ["NOT_REVIEWED", "NOT_REVIEWED", "PUBLISHED"]
    assert v["coverage"]["corpus"]["works"] == v["coverage"]["corpus"]["sources_total"] - 2          # three versions of one work count once
    assert sum(1 for g in v["reading_list"] if g["work_id"] == "W_B02") == 1


# ------------------------------------------------------------------------------------------------ runs: queries, expansions, search

def test_a_run_fixes_its_queries_and_offers_a_models_terms_instead_of_using_them(world):
    w = world
    out = RT.new_run(w["store"], w["token"], w["pid"], terms=["restatement"])
    c = w["store"].get_object(out["retrieval_id"])["content"]
    assert c["state"] == "DRAFT" and [q["interpretation"] for q in c["queries"]] == ["P0"] and "restatement" in c["queries"][0]["tokens"] and c["hits"] == []
    P.frame(w["store"], w["token"], w["pid"], ScriptedModelAdapter(model_id="scripted-framer", default_response={"interpretations": [
        {"text": "Detection changes which frauds are observed, not which are committed.", "answer_kind": "descriptive", "search_terms": ["partial observability", "detection probability"]}]}))
    P.choose(w["store"], w["token"], w["pid"], ["I1", "I2"], "both readings")
    out2 = RT.new_run(w["store"], w["token"], w["pid"])
    c2 = w["store"].get_object(out2["retrieval_id"])["content"]
    assert [q["interpretation"] for q in c2["queries"]] == ["I1", "I2"] and out2["expansions_offered"] == 2
    assert [(e["status"], e["source"]) for e in c2["expansions"]] == [("PROPOSED", "model:scripted-framer")] * 2           # offered, not used
    with pytest.raises(KnowledgeError, match="INTERPRETATION_UNKNOWN"):
        RT.new_run(w["store"], w["token"], w["pid"], interpretation_ids=["I9"])
    with pytest.raises(KnowledgeError, match="CONFIG_UNKNOWN"):
        RT.new_run(w["store"], w["token"], w["pid"], magic=1)


def test_a_model_proposal_enters_the_search_only_after_the_researcher_accepts_it(world):
    w = world
    store, token = w["store"], w["token"]
    rid = fresh_run(w)
    out = RT.propose_expansions(store, token, rid, expansions("earnings manipulation", "accounting irregularities", "falsified financial statements", "AAER"))
    assert out["status"] == "PROPOSED" and len(out["proposed"]) == 4 and out["model"] == "model:scripted-expander"
    with store.read() as conn:
        (run,) = workflow_runs.list_runs(conn, workflow="retrieval_expansion")
    assert run["execution_status"] == "SUCCEEDED" and run["usage"]["llm_calls"] == 1 and run["skill_digest"] == RT.EXPANSION_SKILL_DIGEST
    RT.search(store, token, rid)
    before = RT.run_view(store, rid)
    assert before["coverage"]["stages"]["R2_enabled"] is False and before["coverage"]["stages"]["R2_only"] == 0 and len(before["coverage"]["stages"]["proposed_not_decided"]) == 4
    assert not any(g["stage"] == "R2" for g in before["reading_list"])
    with pytest.raises(KnowledgeError, match="RATIONALE_REQUIRED"):
        RT.decide_expansions(store, token, rid, rationale=" ", accept=("AAER",))
    with pytest.raises(KnowledgeError, match="TERM_UNKNOWN"):
        RT.decide_expansions(store, token, rid, rationale="x", accept=("not a proposed term",))
    RT.decide_expansions(store, token, rid, rationale="these are the names used in the literature I know", accept=("earnings manipulation", "AAER"), reject=("falsified financial statements",))
    assert RT.run_view(store, rid)["search_outdated"] is True
    RT.search(store, token, rid)
    after = RT.run_view(store, rid)
    assert after["search_outdated"] is False and after["coverage"]["stages"]["R2_enabled"] and set(after["coverage"]["stages"]["accepted_expansions"]) == {"earnings manipulation", "AAER"}
    r2 = [g for g in after["reading_list"] if g["stage"] == "R2"]
    assert r2 and all(g["expansion_terms"] and "accepted expansion" in g["reason"] for g in r2)
    assert {g["work_id"] for g in r2} & {"SRC_B01", "SRC_B03", "W_B02"}
    exps = {e["term"]: e for e in w["store"].get_object(rid)["content"]["expansions"]}
    assert exps["AAER"]["status"] == "ACCEPTED" and exps["AAER"]["decided_by"] == "researcher" and exps["falsified financial statements"]["status"] == "REJECTED"


def test_a_failed_or_unusable_expansion_changes_nothing(world):
    w = world
    rid = fresh_run(w)
    rev = w["store"].get_object(rid)["revision"]

    class Down:
        model_id = "down"

        def generate(self, *a, **k):
            raise ModelError("HTTP 503")

    assert RT.propose_expansions(w["store"], w["token"], rid, Down())["status"] == "FAILED"
    assert RT.propose_expansions(w["store"], w["token"], rid, ScriptedModelAdapter(model_id="bad", default_response={"terms": [{"term": "x"}]}))["reason"] == "invalid_output"
    assert w["store"].get_object(rid)["revision"] == rev
    with w["store"].read() as conn:
        assert [r["execution_status"] for r in workflow_runs.list_runs(conn, workflow="retrieval_expansion")] == ["FAILED", "FAILED"]


def test_every_hit_carries_its_provenance_and_scores_are_never_verdicts(world):
    w = world
    rid = fresh_run(w)
    RT.search(w["store"], w["token"], rid)
    c = w["store"].get_object(rid)["content"]
    top = next(h for h in c["hits"] if h["tier"] == "FULL_TEXT")
    assert top["rank"] == 1 and top["stage"] == "R1" and top["matched_terms"] and top["locator"].startswith(top["source_id"]) and top["reason"].startswith("R1: matched")
    assert top["queries"][0]["query"] == "Q1" and top["source_revision"] == 1
    assert [h["rank"] for h in c["hits"] if h["tier"] == "FULL_TEXT"] == list(range(1, sum(1 for h in c["hits"] if h["tier"] == "FULL_TEXT") + 1))
    assert "never" not in json.dumps(c["coverage"]["limits"]) and any("not shown to be irrelevant" in x for x in c["coverage"]["limits"])
    # repeating the search on the same corpus gives the same hits
    RT.search(w["store"], w["token"], rid)
    assert w["store"].get_object(rid)["content"]["hits"] == c["hits"]


def test_not_read_candidates_are_listed_apart_and_never_on_the_reading_list(world):
    w = world
    rid = fresh_run(w)
    RT.decide_expansions(w["store"], w["token"], rid, rationale="other names", add=("earnings manipulation", "discretionary accruals"))
    RT.search(w["store"], w["token"], rid)
    v = RT.run_view(w["store"], rid)
    listed = {x["source_id"] for g in v["reading_list"] for x in g["versions"]}
    not_read = {g["representative"] for g in v["not_read_candidates"]}
    assert "SRC_B04" in not_read and "SRC_B04" not in listed and all(corpus.source_tier(w["store"].get_object(s)["content"]) == "FULL_TEXT" for s in listed)
    assert "possibly relevant" in json.dumps(v["coverage"]["limits"]) or v["coverage"]["known_gaps"]["not_read_candidates"] >= 1


# ------------------------------------------------------------------------------------------------ R3a

def test_citation_links_come_from_the_stored_reference_lists(world):
    w = world
    papers = {}
    for sid in w["store"].get_object("SRC_A02") and ("SRC_A02", "SRC_B01", "SRC_B03", "SRC_C01", "SRC_D_MEDICAL_1"):
        obj, text = corpus.source_text(w["store"], sid)
        papers[sid] = {"text": text, "sections": corpus.source_sections(w["store"], obj, text), "title": obj["content"].get("title"), "doi": obj["content"].get("doi")}
    links = {(l["citing"], l["cited"], l["match"]) for l in C.find_links(papers)}
    assert ("SRC_A02", "SRC_B01", "title") in links and ("SRC_B03", "SRC_C01", "doi") in links and ("SRC_B03", "SRC_D_MEDICAL_1", "title") in links
    assert not any(c == s for c, s, _ in links)
    one = next(l for l in C.find_links(papers) if l["citing"] == "SRC_A02")
    obj, text = corpus.source_text(w["store"], "SRC_A02")
    assert "Earnings Manipulation" in text[one["chars"][0]:one["chars"][1]] and one["locator"].startswith("SRC_A02 chars")


def test_neighbours_are_listed_apart_with_the_place_where_the_citation_was_found(world):
    w = world
    rid = fresh_run(w, neighbours_from=30)          # the problem statement adds noisy words, so look at more than the top ten as seeds
    RT.search(w["store"], w["token"], rid)
    c = w["store"].get_object(rid)["content"]
    hit_ids = {h["source_id"] for h in c["hits"]}
    assert c["neighbours"] and not ({n["source_id"] for n in c["neighbours"]} & hit_ids)
    for n in c["neighbours"]:
        r = n["relations"][0]
        assert n["stage"] == "R3a" and r["via"] in hit_ids and r["match"] in ("doi", "title") and "(reference list)" in r["locator"] and n["reason"].startswith("R3a:")
    b01_somewhere = "SRC_B01" in hit_ids or "SRC_B01" in {n["source_id"] for n in c["neighbours"]}
    assert b01_somewhere                                         # A02 cites it, so it is reachable even when no query word finds it


# ------------------------------------------------------------------------------------------------ R5

def test_the_researchers_review_is_recorded_and_changes_the_list_for_this_run_only(world):
    w = world
    store, token = w["store"], w["token"]
    rid = fresh_run(w)
    RT.search(store, token, rid)
    v = RT.run_view(store, rid)
    first = v["reading_list"][0]["representative"]
    with pytest.raises(KnowledgeError, match="REASON_REQUIRED"):
        RT.review(store, token, rid, "irrelevant", reason=" ", source_id=first)
    with pytest.raises(KnowledgeError, match="SOURCE_NOT_IN_RUN"):
        RT.review(store, token, rid, "irrelevant", reason="x", source_id="SRC_NOPE")
    RT.review(store, token, rid, "irrelevant", reason="this is about card fraud, not reporting", source_id=first)
    after = RT.run_view(store, rid)
    assert first not in [g["representative"] for g in after["reading_list"]] and after["set_aside"] == [{"source_id": first, "reason": "this is about card fraud, not reporting"}]
    assert first in {h["source_id"] for h in store.get_object(rid)["content"]["hits"]}               # the hit itself stays on record
    RT.review(store, token, rid, "restore", reason="on reflection it is relevant", source_id=first)
    assert first in [g["representative"] for g in RT.run_view(store, rid)["reading_list"]]
    unseen = next(s for s in w["ids"]["D"] if s not in {h["source_id"] for h in store.get_object(rid)["content"]["hits"]})
    RT.review(store, token, rid, "add", reason="I know this paper", source_id=unseen)
    added = next(g for g in RT.run_view(store, rid)["reading_list"] if g["representative"] == unseen)
    assert added["stage"] == "R5_added" and "added by the researcher" in added["reason"]
    with pytest.raises(KnowledgeError, match="SOURCE_NOT_FOUND"):
        RT.review(store, token, rid, "add", reason="x", source_id="SRC_NOT_HELD")
    before = store.get_object(rid)["revision"]
    RT.review(store, token, rid, "acquire", reason="the competing paper everyone cites", title="A Paper I Do Not Hold", doi="10.1234/abc")
    req = RT.run_view(store, rid)["acquisition_requests"]
    assert req == [{"title": "A Paper I Do Not Hold", "doi": "10.1234/abc", "reason": "the competing paper everyone cites", "requested_by": "researcher", "at": req[0]["at"]}]
    assert store.get_object(rid)["revision"] == before + 1
    with store.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM object_heads WHERE object_id LIKE '%NOT_HOLD%'").fetchone()[0] == 0      # a request creates no Source
    imp = register_actor(store, token, actor_id="importer_only", display_name="imp", roles=["importer"])
    with pytest.raises(AuthorizationError):
        RT.review(store, imp["token"], rid, "irrelevant", reason="not mine to decide", source_id=first)
    assert [e["action"] for e in store.get_object(rid)["content"]["edits"]] == ["irrelevant", "restore", "add", "acquire"]


def test_reviewing_needs_a_search_first(world):
    rid = fresh_run(world)
    with pytest.raises(KnowledgeError, match="RETRIEVAL_NOT_SEARCHED"):
        RT.review(world["store"], world["token"], rid, "acquire", reason="x", title="Some paper")


# ------------------------------------------------------------------------------------------------ snapshots, currency, coverage

def test_a_run_is_stale_for_the_corpus_when_the_corpus_changes(world):
    w = world
    store, token = w["store"], w["token"]
    rid = fresh_run(w)
    RT.search(store, token, rid)
    snap = store.get_object(rid)["content"]["corpus_snapshot_id"]
    assert snap == corpus.corpus_snapshot_id(store) and RT.run_view(store, rid)["corpus_currency"]["status"] == "CURRENT"
    corpus.import_source(store, token, citation_key="late", title="A Late Paper on Fraud", text="A Late Paper on Fraud\n\nAbstract\nFinancial reporting fraud again.\n\n1 Introduction\nFraud detection.\n")
    cur = RT.run_view(store, rid)["corpus_currency"]
    assert cur["status"] == "STALE_FOR_CORPUS" and cur["added"] == ["SRC_LATE"] and cur["from"] == snap and cur["to"] != snap
    corpus.set_excluded(store, token, "SRC_A01", "retracted")
    cur2 = RT.run_view(store, rid)["corpus_currency"]
    assert cur2["revised"] == ["SRC_A01"]
    RT.search(store, token, rid)
    assert RT.run_view(store, rid)["corpus_currency"]["status"] == "CURRENT"
    v = RT.run_view(store, rid)
    assert "SRC_A01" not in {x["source_id"] for g in v["reading_list"] for x in g["versions"]}
    assert v["coverage"]["exclusions"] == [{"source_id": "SRC_A01", "reason": "retracted"}] and v["coverage"]["corpus"]["excluded"] == 1
    # excluding is reversible and keeps history
    corpus.set_excluded(store, token, "SRC_A01", None)
    assert store.get_object("SRC_A01")["revision"] == 3 and not store.get_object("SRC_A01")["content"].get("excluded")
    with pytest.raises(KnowledgeError, match="REASON_REQUIRED"):
        corpus.set_excluded(store, token, "SRC_A01", " ")


def test_the_corpus_snapshot_ignores_nothing_that_changes_the_corpus_and_nothing_else(world):
    w = world
    s0 = corpus.corpus_snapshot_id(w["store"])
    assert corpus.corpus_snapshot_id(w["store"]) == s0
    with w["store"].write_tx(token=w["token"]) as tx:
        tx.add_revision(object_id="RQ_UNRELATED", object_type="ResearchQuestion", origin={}, edges=[], content={"text": "an unrelated question"})
    assert corpus.corpus_snapshot_id(w["store"]) == s0
    corpus.import_metadata_only(w["store"], w["token"], citation_key="newmeta", title="Another Paper Held As Metadata")
    assert corpus.corpus_snapshot_id(w["store"]) != s0


def test_coverage_states_what_was_searched_and_when_a_conclusion_would_be_unsafe(world):
    w = world
    rid = RT.new_run(w["store"], w["token"], w["pid"], terms=["financial reporting fraud detection"], min_fulltext_sources=50)["retrieval_id"]
    out = RT.search(w["store"], w["token"], rid)
    assert out["below_floor"] is True
    k = RT.run_view(w["store"], rid)["coverage"]
    assert k["corpus"]["full_text"] == 40 and k["corpus"]["abstract_only"] == 1 and k["corpus"]["metadata_only"] == 1 and k["corpus"]["sources_total"] == 42
    assert k["corpus"]["works"] == 40 and k["floor"]["min_fulltext_sources"] == 50 and k["floor"]["below_floor"] is True and "not a judgement" in k["floor"]["note"]
    assert any("at most 20 sources per tier" in x for x in k["limits"])
    assert RT.run_view(w["store"], rid)["floor_after_your_review"] == {"full_texts_on_the_list": k["floor"]["full_text_retrieved"], "min_fulltext_sources": 50, "below_floor": True}
    assert k["external_calls"]["calls"] == 0 and "not used" in k["external_calls"]["metadata_sources"]
    assert k["assessed"]["retrieved_full_text"] < k["assessed"]["of_full_text_in_corpus"] and "not every source held" in k["assessed"]["note"]
    assert k["known_gaps"]["languages"].startswith("not determined") and k["known_gaps"]["year_range"] == [2018, 2023]
    assert "SRC_A01" in k["known_gaps"]["reference_lists_parsed"]


def test_an_empty_corpus_searches_to_an_empty_list_below_the_floor(tmp_path):
    store, token = StateStore.create(tmp_path / "h", "PRJ_EMPTY", owner_id="researcher")
    pid = P.new_problem(store, token, statement=STATEMENT)["problem_id"]
    rid = RT.new_run(store, token, pid)["retrieval_id"]
    out = RT.search(store, token, rid)
    v = RT.run_view(store, rid)
    assert out["below_floor"] and v["reading_list"] == [] and v["coverage"]["corpus"]["sources_total"] == 0 and v["coverage"]["floor"]["full_text_retrieved"] == 0


# ------------------------------------------------------------------------------------------------ the object, integrity

def test_the_run_is_an_engine_written_object_that_keeps_the_store_consistent(world):
    w = world
    rid = fresh_run(w)
    RT.search(w["store"], w["token"], rid)
    import jsonschema
    jsonschema.validate(w["store"].get_object(rid)["content"], schema.content_schema("RetrievalRun"))
    assert "RetrievalRun" not in schema.PROPOSABLE_TYPES
    with w["store"].read() as conn:
        edges = w["store"].edges_from(conn, rid, 1)
    assert [(e["relation"], e["target_id"], json.loads(e["provenance"])["edge_kind"]) for e in edges] == [("depends_on", w["pid"], "retrieved_for")]
    assert audit_project_integrity(w["store"])["status"] == "PASS"


# ------------------------------------------------------------------------------------------------ export

def test_the_export_is_readable_reproducible_and_never_contains_a_full_text(world):
    w = world
    store, token = w["store"], w["token"]
    rid = fresh_run(w)
    draft = build_retrieval_export(store, rid)
    assert "NOT_AVAILABLE: the search has not been run" in draft.files["reading_list.md"].decode("utf-8") and {n["file"] for n in draft.not_available} == {"reading_list.md", "coverage_report.md"}
    RT.decide_expansions(store, token, rid, rationale="other names", add=("earnings manipulation", "AAER"))
    RT.search(store, token, rid)
    exp = build_retrieval_export(store, rid)
    out = write_export(store, exp, w["tmp"] / "ret_export")
    files = {p.name: p.read_bytes() for p in Path(out["out"]).iterdir()}
    assert {"reading_list.md", "coverage_report.md", "queries_and_expansions.md", "not_read_candidates.csv", "manifest.json", "export_info.json"} == set(files)
    rl = files["reading_list.md"].decode("utf-8")
    assert "SRC_B02V1 (working_paper, NOT_REVIEWED, public 2024-02-01)" in rl and "SRC_B02J (journal_article, PUBLISHED, public 2026-06-30)" in rl and "R1: matched" in rl
    csv_text = files["not_read_candidates.csv"].decode("utf-8")
    assert csv_text.startswith("source_id,title,tier,stage,why,document_type,review_status,first_public_date,status\n") and "SRC_B04" in csv_text and "possibly relevant, not read" in csv_text
    assert "BELOW THE FLOOR" not in files["coverage_report.md"].decode("utf-8") and "external sources: not used" in files["coverage_report.md"].decode("utf-8")
    for sid in ("SRC_A01", "SRC_B01"):
        text = corpus.source_text(store, sid)[1]
        assert text[:400] not in rl and text[:400] not in files["coverage_report.md"].decode("utf-8")
    man = json.loads(files["manifest.json"])
    assert man["kind"] == "retrieval" and man["scope"] == {"retrieval": rid} and man["provenance_counts"]["reading_list.md"]["data_derived"] > 0
    assert verify_export(store, out["out"], lambda scope: build_retrieval_export(store, scope["retrieval"]))["status"] == "VERIFIED"
    again = build_retrieval_export(store, rid)
    assert again.files == exp.files and again.closure_digest == exp.closure_digest
    # a new paper in the corpus changes the file that says the run is current
    corpus.import_source(store, token, citation_key="late", title="A Late Paper on Fraud", text="A Late Paper on Fraud\n\nAbstract\nFraud.\n\n1 Introduction\nFraud detection.\n")
    assert verify_export(store, out["out"], lambda scope: build_retrieval_export(store, scope["retrieval"]))["status"] == "STATE_CHANGED"
    assert "STALE_FOR_CORPUS" in build_retrieval_export(store, rid).files["reading_list.md"].decode("utf-8")


# ------------------------------------------------------------------------------------------------ command line

def test_retrieval_cli_end_to_end(world, monkeypatch, capsys):
    from research_harness import cli_knowledge
    w = world
    tf = w["tmp"] / "token"
    tf.write_text(w["token"], encoding="utf-8")
    monkeypatch.setattr(cli_knowledge, "_adapter", lambda args: expansions("earnings manipulation", "AAER", "accounting irregularities"))
    base = ["--project", "PRJ_RET", "--home", str(w["home"])]
    auth = [*base, "--token-file", str(tf)]

    def j(*argv, rc=0):
        assert main([*map(str, argv), "--json"]) == rc, capsys.readouterr().err
        return json.loads(capsys.readouterr().out)

    meta = j("corpus", "import-metadata", "--citation-key", "wpx", "--title", "A Working Paper On Reporting Fraud", "--document-type", "working_paper", "--review-status", "NOT_REVIEWED",
             "--first-public-date", "2025-03-10", "--date-evidence", "repository posting record", *auth)
    assert meta["tier"] == "METADATA_ONLY"
    assert main(["corpus", "import-metadata", "--citation-key", "wpy", "--title", "Another", "--first-public-date", "2025", *auth]) == 1
    assert "DATE_EVIDENCE_REQUIRED" in capsys.readouterr().err
    rid = j("retrieval", "new", w["pid"], "--term", "financial reporting fraud detection", *auth)["retrieval_id"]
    assert j("retrieval", "expand", rid, "--k", 5, *auth)["proposed"]
    assert j("retrieval", "expansions", rid, "--accept", "earnings manipulation", "--accept", "AAER", "--rationale", "the names I know", *auth)["accepted"] == ["AAER", "earnings manipulation"]
    s = j("retrieval", "search", rid, *auth)
    assert s["reading_list"] > 0 and s["not_read_candidates"] >= 2
    assert j("corpus", "exclude", "SRC_D_CARD_1", "--reason", "card fraud, off topic", *auth)["excluded"] is True
    assert main(["retrieval", "show", rid, *base]) == 0
    text = capsys.readouterr().out
    assert "STALE_FOR_CORPUS" in text and "reading list:" in text and "NOT READ" in text and "R1:" in text
    assert j("retrieval", "search", rid, *auth)["below_floor"] is False
    assert j("retrieval", "review", rid, "acquire", "--title", "A Paper Everyone Cites", "--reason", "the main competitor", *auth)["action"] == "acquire"
    out = w["tmp"] / "cli_export"
    ex = j("export", "retrieval", rid, "--out", out, *base)
    assert "reading_list.md" in ex["files"]
    assert main(["export", "verify", str(out), *base]) == 0 and capsys.readouterr().out.strip() == "VERIFIED"
    gold = w["tmp"] / "gold.json"
    gold.write_text(json.dumps(w["gold"]), encoding="utf-8")
    ev = j("retrieval", "evaluate", "--gold", gold, *base)
    assert ev["mean"]["R1+R2"]["recall_at_k"] >= BASELINE["mean"]["R1+R2"]["recall_at_k"] - 1e-9
    assert main(["retrieval", "evaluate", "--gold", str(gold), *base]) == 0
    assert "found only with expansions" in capsys.readouterr().out


def test_an_r1_hit_is_explained_by_the_query_terms_and_an_r2_hit_by_the_accepted_expansion(world):
    w = world
    rid = fresh_run(w)
    RT.decide_expansions(w["store"], w["token"], rid, rationale="other names", add=("earnings manipulation", "AAER", "accounting irregularities"))
    RT.search(w["store"], w["token"], rid)
    hits = {h["source_id"]: h for h in w["store"].get_object(rid)["content"]["hits"]}
    base = set(w["store"].get_object(rid)["content"]["queries"][0]["tokens"])
    a1 = hits["SRC_A01"]
    assert a1["stage"] == "R1" and set(a1["matched_terms"]) <= base and "aaer" not in a1["matched_terms"]
    c1 = hits["SRC_C01"]
    assert c1["stage"] == "R2" and not (set(c1["matched_terms"]) & base) and c1["expansion_terms"] == ["accounting irregularities"] and "found only through" in c1["reason"]
    v = RT.run_view(w["store"], rid)
    assert [g["position"] for g in v["reading_list"]] == list(range(1, len(v["reading_list"]) + 1))      # positions are consecutive; ranks keep their provenance
    assert v["floor_after_your_review"]["full_texts_on_the_list"] == sum(len(g["versions"]) for g in v["reading_list"] if g["stage"] != "R3a")
