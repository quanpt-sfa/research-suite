"""Phase 3C (plan v3): the literature engine. Structure from verified extractions; nothing here judges meaning."""
from __future__ import annotations

import pytest

from research_harness.knowledge import closure, corpus, literature
from research_harness.knowledge.common import KnowledgeError
from research_harness.knowledge.library import create_library, pin
from research_harness.state.store import StateStore
from research_harness.workflows import reading_protocols as rp
from research_harness.workflows.adapters import ScriptedModelAdapter
from tests import m3_fixture as fx


@pytest.fixture
def proj(tmp_path):
    store, token = StateStore.create(tmp_path / "home", "PRJ_LIT")
    fx.import_corpus(store, token)
    return store, token


def test_three_purposes_distinct_extraction_schemas(proj):
    store, token = proj
    protocols = {p: rp.get_protocol(rp_id) for p, rp_id in literature.PURPOSES.items()}
    field_sets = {p: {f["name"] for f in proto.fields} for p, proto in protocols.items()}
    assert field_sets["theory"].isdisjoint(field_sets["method"]) and field_sets["method"].isdisjoint(field_sets["contribution"])
    assert protocols["method"].dimension == "design" and protocols["theory"].dimension == "theory"
    ex = fx.extractor()
    keys = {}
    for purpose in ("theory", "method", "contribution"):
        rev = literature.run_literature(store, token, purpose, [fx.PAPER_A, fx.PAPER_B], ex)
        keys[purpose] = set(rev) - {"purpose", "protocol", "mode", "sources", "feeds", "search_protocol", "extraction", "run_id"}
        assert rev["protocol"] == protocols[purpose].pin
    assert keys["theory"] != keys["method"] != keys["contribution"] and "constructs" in keys["theory"] and "method_records" in keys["method"]


def test_construct_definition_divergence_flagged(proj):
    store, token = proj
    rev = literature.run_literature(store, token, "theory", [fx.PAPER_A, fx.PAPER_B], fx.extractor())
    assert set(rev["divergent_constructs"]) == {"restatement risk"}
    (flag,) = rev["review_flags"]
    assert flag["flag"] == "CONSTRUCT_DEFINITION_TEXT_DIFFERS" and flag["sources"] == sorted([fx.PAPER_A, fx.PAPER_B])
    assert "whether the meanings differ is for the researcher" in flag["note"]            # a flag, not a verdict
    # the divergence is a synthesised statement: deterministic generator, the two definitions as basis, HIGH risk
    st = next(s for s in rev["statements"] if "different words" in s["text"])
    assert st["generated_by"] == "deterministic:literature_engine@1" and st["risk_tier"] == "HIGH" and st["feeds"] == "contribution"
    assert len(st["evidence_basis"]) == 2 and all(b["quote_verified"] for b in st["evidence_basis"]) and st["assessment"] == "PROVISIONAL"
    # a shared label is reported as a label match, and an assumption stated but not recorded as tested is listed
    assert rev["shared_mechanism_labels"] == {"weak monitoring": sorted([fx.PAPER_A, fx.PAPER_B])}
    untested = rev["assumptions_stated_not_recorded_as_tested"]
    assert [(u["source_id"], u["assumption"]) for u in untested] == [(fx.PAPER_B, "monitoring quality is stable")]
    # the same definition in both papers would not be flagged
    same = {k: v for k, v in rev["constructs"].items()}
    assert len(same["restatement risk"]) == 2


def test_method_record_spans_quote_verified(proj):
    store, token = proj
    rev = literature.run_literature(store, token, "method", [fx.PAPER_A, fx.PAPER_B], fx.extractor())
    assert [r["source_id"] for r in rev["method_records"]] == [fx.PAPER_A, fx.PAPER_B]
    for mr in rev["method_records"]:
        _, text = corpus.source_text(store, mr["source_id"])
        assert mr["source_fidelity"] == "QUOTE_VERIFIED"                    # never more than that from extraction alone
        for recs in mr["fields"].values():
            for r in recs:
                assert text[r["char_start"]:r["char_end"]] == r["quote"]
                assert corpus.verify_passage(store, r["passage_id"])["source_match"] == "VERIFIED"
    assert rev["field_coverage"]["sample_selection"] == {fx.PAPER_A: True, fx.PAPER_B: True}
    assert rev["field_coverage"]["interpretation_rule"] == {fx.PAPER_A: False, fx.PAPER_B: False}   # gaps are visible, not filled
    assert "not" in rev["note"]


def test_narrative_mode_no_prisma(proj):
    store, token = proj
    rev = literature.run_literature(store, token, "contribution", [fx.PAPER_A, fx.PAPER_B], fx.extractor())
    assert rev["mode"] == "narrative" and "prisma" not in rev and rev["search_protocol"] == "NOT_RECORDED"
    described = literature.run_literature(store, token, "contribution", [fx.PAPER_A], fx.extractor(), search_protocol={"description": "snowballing from the focus paper"})
    assert described["search_protocol"] == {"description": "snowballing from the focus paper"}


def test_systematic_requires_protocol_and_prisma_counts(proj):
    store, token = proj
    ex = fx.extractor()
    with pytest.raises(KnowledgeError, match="SYSTEMATIC_REQUIRES_PROTOCOL") as e:
        literature.run_literature(store, token, "contribution", [fx.PAPER_A, fx.PAPER_B], ex, mode="systematic")
    assert any("search protocol needs databases" in p for p in e.value.details["problems"]) and any("screening log" in p for p in e.value.details["problems"])
    sp = {"databases": ["Scopus"], "queries": ["restatement AND prediction"], "date_from": "2015-01-01", "date_to": "2025-12-31",
          "records_identified": {"Scopus": 120}}
    log = ([{"record": f"R{i}", "stage": "dedup", "decision": "exclude", "reason": "duplicate"} for i in range(10)]
           + [{"record": f"R{i}", "stage": "screening", "decision": "exclude" if i > 20 else "include", "reason": "off topic" if i > 20 else ""} for i in range(11, 31)]
           + [{"record": fx.PAPER_A, "stage": "eligibility", "decision": "include"}, {"record": fx.PAPER_B, "stage": "eligibility", "decision": "include"},
              {"record": "R12", "stage": "eligibility", "decision": "exclude", "reason": "no full text"}])
    rev = literature.run_literature(store, token, "contribution", [fx.PAPER_A, fx.PAPER_B], ex, mode="systematic", search_protocol=sp, screening_log=log)
    p = rev["prisma"]
    assert (p["identified"], p["duplicates_removed"], p["screened"], p["excluded_at_screening"]) == (120, 10, 20, 10)
    assert p["assessed_for_eligibility"] == 3 and p["excluded_at_eligibility"] == {"no full text": 1} and p["included"] == sorted([fx.PAPER_A, fx.PAPER_B])
    with pytest.raises(KnowledgeError, match="SCREENING_LOG_MISMATCH"):
        literature.run_literature(store, token, "contribution", [fx.PAPER_A], ex, mode="systematic", search_protocol=sp, screening_log=log)
    with pytest.raises(KnowledgeError, match="SCREENING_LOG_INVALID"):
        literature.prisma_counts([{"record": "R1", "stage": "screening", "decision": "exclude"}], sp)


def test_failed_extraction_stops_the_review(proj):
    store, token = proj
    with pytest.raises(KnowledgeError, match="EXTRACTION_FAILED"):
        literature.run_literature(store, token, "theory", [fx.PAPER_A], ScriptedModelAdapter(default_response="not json"))
    with pytest.raises(KnowledgeError, match="PURPOSE_UNKNOWN"):
        literature.run_literature(store, token, "gossip", [fx.PAPER_A], fx.extractor())


def test_extraction_cache_reused_across_projects(tmp_path):
    home = tmp_path / "home"
    lib, ltoken = create_library(home)
    fx.import_corpus(lib, ltoken, sources=(fx.PAPER_A, fx.PAPER_B))
    ex = fx.extractor()
    lib_review = literature.run_literature(lib, ltoken, "method", ["LIB_" + fx.PAPER_A, "LIB_" + fx.PAPER_B], ex)
    calls_in_library = len(ex.recorded_requests)
    assert calls_in_library >= 2 and lib_review["extraction"]["model_calls"] >= 2

    class NoModel:
        model_id = "must-not-be-called"
        def generate(self, *a, **k):
            raise AssertionError("a model was called for an extraction that arrived with the source")
    for name in ("PRJ_ONE", "PRJ_TWO"):                                         # two projects, zero model calls each
        project, ptoken = StateStore.create(home, name, owner_id="researcher")
        for sid in ("LIB_" + fx.PAPER_A, "LIB_" + fx.PAPER_B):
            closure.use_in_project(project, ptoken, lib, pin(lib, sid))
        rev = literature.run_literature(project, ptoken, "method", ["LIB_" + fx.PAPER_A, "LIB_" + fx.PAPER_B], NoModel())
        assert rev["extraction"] == {"runs": rev["extraction"]["runs"], "reused": 2, "model_calls": 0}
        assert [r["fields"].keys() for r in rev["method_records"]] == [r["fields"].keys() for r in lib_review["method_records"]]
        assert closure.verify_closure(project)["status"] == "VERIFIED"
    # a protocol that was never run on the library has no cache: that one does cost a model call
    with pytest.raises(AssertionError, match="a model was called"):
        literature.run_literature(project, ptoken, "theory", ["LIB_" + fx.PAPER_A], NoModel())
