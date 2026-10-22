"""Phase 3C.0 (plan v3): real reading protocols run on stored text, section by section, with every quote verified."""
from __future__ import annotations

from pathlib import Path

import pytest

from research_harness.knowledge import corpus
from research_harness.knowledge.common import KnowledgeError
from research_harness.state.store import StateStore
from research_harness.workflows import reading_protocols as rp
from research_harness.workflows.adapters import ModelRefusal, ScriptedModelAdapter

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_paper_misstatement.txt"
SAMPLE_PDF = Path(__file__).resolve().parents[1] / "sample" / "zavitsanos2025positive-unlabeled.pdf"

SAMPLE_Q = "drop firms with fewer than three consecutive annual observations"
LABEL_Q = "The outcome label equals one when the firm later filed an amended annual report for an accounting error"
RESULT_Q = "The pooled model attains an average precision of 0.12 in the test years"


@pytest.fixture
def proj(tmp_path):
    store, token = StateStore.create(tmp_path / "home", "PRJ_RP")
    corpus.import_source(store, token, citation_key="synth2025", text=FIXTURE.read_text(encoding="utf-8"),
                         title="A synthetic paper", year=2025)
    return store, token


def answering(records, gaps=()):
    return ScriptedModelAdapter(model_id="scripted-extractor", default_response={"records": records, "gaps": list(gaps)})


def seen_text(adapter) -> str:
    return "\n".join(r.user_prompt for r in adapter.recorded_requests)


def test_extraction_runs_real_protocols_with_quote_verification(proj):
    store, token = proj
    method = answering([
        {"field": "sample_selection", "value": "Firms with fewer than three consecutive years are dropped.", "quote": SAMPLE_Q},
        {"field": "label_construction", "value": "Label is one for a later amended report.", "quote": LABEL_Q},
        {"field": "model_or_estimator", "value": "A regression that handles all years well.",
         "quote": "the estimator handles every firm in all years"},                       # paraphrase: not in the text
        {"field": "validation_or_tests", "value": "Invented split.", "quote": "models are validated on random folds only"},  # invented
    ], gaps=["interpretation_rule"])
    out = rp.run_protocol(store, token, "SRC_SYNTH2025", "RP_METHOD", method)

    assert out["status"] == "EXTRACTED" and out["reused"] is False
    assert {r["field"] for r in out["records"]} == {"sample_selection", "label_construction"}
    assert {r["reason"] for r in out["rejected"]} == {"quote_not_in_text_shown"} and len(out["rejected"]) == 2
    assert out["gaps"] == ["interpretation_rule"]
    # only the method section was shown to the model: not the results, not the conclusions
    shown = seen_text(method)
    assert "average precision" not in shown and "transfers poorly" not in shown
    assert "fewer than three consecutive annual observations" in shown and out["sections_read"] == ["3 Experimental setup"]

    # the stored passages are verified spans of the stored text, linked to the Source revision
    text = corpus.source_text(store, "SRC_SYNTH2025")[1]
    for rec in out["records"]:
        p = store.get_object(rec["passage_id"])
        assert p["object_type"] == "EvidencePassage" and p["content"]["quote_verified"] is True
        assert text[p["content"]["char_start"]:p["content"]["char_end"]] == p["content"]["quote"]
        assert p["content"]["generated_by"] == "model:scripted-extractor"   # the value's producer, apart from the quote
        assert p["content"]["protocol"] == "RP_METHOD@4" and p["origin"]["operation"] == "extract"
        assert corpus.verify_passage(store, rec["passage_id"])["source_match"] == "VERIFIED"
        with store.read() as conn:
            assert [(e["relation"], e["target_id"]) for e in store.edges_from(conn, rec["passage_id"], rec["revision"])] == [
                ("derived_from", "SRC_SYNTH2025")]
    # a verified quote is not a verified reading: the run is provisional at best (I9, I16)
    assert out["assessment"] == "PROVISIONAL"


def test_each_protocol_reads_its_own_sections(proj):
    store, token = proj
    intro = answering([{"field": "unresolved_tension", "value": "Unclear whether indicators transfer across industries.",
                        "quote": "it remains unclear whether these indicators remain informative once firms from different industries are pooled"}])
    theory = answering([{"field": "construct_definition", "value": "Restatement risk.",
                         "quote": "We define restatement risk as the probability that a firm's annual report is later amended for an accounting error"}])
    contribution = answering([{"field": "claimed_contribution", "value": "Tests transferability of the model.",
                               "quote": "We contribute by testing the transferability of the model on a pooled panel"}])
    for pid, adapter in (("RP_INTRO_ARGUMENT", intro), ("RP_THEORY", theory), ("RP_CONTRIBUTION", contribution)):
        out = rp.run_protocol(store, token, "SRC_SYNTH2025", pid, adapter)
        assert out["status"] == "EXTRACTED" and not out["rejected"], (pid, out["rejected"])
    assert "Agency theory" in seen_text(theory) and "We start from all annual reports" not in seen_text(theory)
    assert "We start from all annual reports" not in seen_text(intro) and "Financial restatements impose" in seen_text(intro)
    assert {p.id for p in rp.list_protocols()} == {"RP_INTRO_ARGUMENT", "RP_THEORY", "RP_METHOD", "RP_CONTRIBUTION", "RP_LANDSCAPE", "RP_CLAIMS", "RP_CLAIM_EVIDENCE"}   # the last two were added by WP6.2


def test_extraction_is_cached_by_source_revision_and_protocol_version(proj):
    store, token = proj
    adapter = answering([{"field": "sample_selection", "value": "Short panels dropped.", "quote": SAMPLE_Q}])
    first = rp.run_protocol(store, token, "SRC_SYNTH2025", "RP_METHOD", adapter)
    again = rp.run_protocol(store, token, "SRC_SYNTH2025", "RP_METHOD", adapter)
    assert again["reused"] is True and again["reused_from_run_id"] == first["run_id"] and len(adapter.recorded_requests) == 1
    assert [r["passage_id"] for r in again["records"]] == [r["passage_id"] for r in first["records"]]
    assert rp.extraction_status(store, "SRC_SYNTH2025", "RP_METHOD")["cached"] is True
    with store.read() as conn:
        rows = conn.execute("SELECT reused_from_run_id FROM workflow_runs WHERE workflow='reading_protocol:RP_METHOD' ORDER BY rowid").fetchall()
    assert [r[0] for r in rows] == [None, first["run_id"]]  # the reuse is registered too (I5)

    rp.run_protocol(store, token, "SRC_SYNTH2025", "RP_METHOD", adapter, reuse=False)
    assert len(adapter.recorded_requests) == 2

    # a revised text is a different source revision: no reuse
    text = FIXTURE.read_text(encoding="utf-8").replace("2005 and 2015", "2006 and 2016")
    corpus.import_source(store, token, citation_key="synth2025", text=text, title="A synthetic paper", year=2025)
    assert rp.extraction_status(store, "SRC_SYNTH2025", "RP_METHOD")["cached"] is False
    third = rp.run_protocol(store, token, "SRC_SYNTH2025", "RP_METHOD", adapter)
    assert third["reused"] is False and third["source_revision"] == 2


def test_an_unusable_answer_is_a_failed_run_and_stores_nothing(proj):
    store, token = proj
    for bad in ("not json at all", {"records": [{"field": "sample_selection", "value": "x"}]},
                {"records": [{"field": "made_up_field", "value": "x", "quote": SAMPLE_Q}]}):
        out = rp.run_protocol(store, token, "SRC_SYNTH2025", "RP_METHOD", ScriptedModelAdapter(default_response=bad))
        assert out["status"] == "FAILED" and out["reason"] == "invalid_output" and out["records"] == []
    with store.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM object_heads WHERE object_type='EvidencePassage'").fetchone()[0] == 0
        assert {r[0] for r in conn.execute("SELECT execution_status FROM workflow_runs WHERE workflow LIKE 'reading_protocol:%'")} == {"FAILED"}

    def refuse(system, user):
        raise ModelRefusal("declined", category="x")
    out = rp.run_protocol(store, token, "SRC_SYNTH2025", "RP_METHOD", ScriptedModelAdapter(custom_handler=refuse))
    assert out["status"] == "FAILED" and out["reason"] == "refusal"
    assert rp.extraction_status(store, "SRC_SYNTH2025", "RP_METHOD")["cached"] is False  # a failure is never reused


def test_a_text_larger_than_the_budget_is_read_in_chunks_and_never_truncated(proj):
    store, token = proj
    adapter = answering([])
    system_tokens = rp.estimate_tokens(rp.build_system_prompt(rp.get_protocol("RP_METHOD")))[0]
    out = rp.run_protocol(store, token, "SRC_SYNTH2025", "RP_METHOD", adapter, max_input_tokens=system_tokens + 400 + 60)  # 60 tokens of text per request
    assert out["chunks"] > 1 and len(adapter.recorded_requests) == out["chunks"]
    joined = "".join(r.user_prompt.split("--- TEXT START ---\n")[1].split("\n--- TEXT END ---")[0] for r in adapter.recorded_requests)
    assert "validated by training on the earlier years" in joined  # last sentence of the section was shown
    with pytest.raises(KnowledgeError, match="BUDGET_TOO_SMALL"):
        rp.run_protocol(store, token, "SRC_SYNTH2025", "RP_METHOD", adapter, max_input_tokens=100, reuse=False)


def test_a_paper_without_recognisable_headings_is_read_whole_and_says_so(tmp_path):
    store, token = StateStore.create(tmp_path / "home", "PRJ_RP2")
    body = "We drop firms with fewer than three consecutive annual observations. Nothing else is said here."
    corpus.import_source(store, token, citation_key="flat2025", text=body)
    out = rp.run_protocol(store, token, "SRC_FLAT2025", "RP_METHOD",
                          answering([{"field": "sample_selection", "value": "Short panels dropped.", "quote": SAMPLE_Q}]))
    assert out["section_fallback"] is True and out["context_status"] == "PARTIAL" and out["sections_read"] == []
    assert out["status"] == "EXTRACTED"


def test_a_field_that_may_not_repeat_keeps_only_the_first_record(proj):
    store, token = proj
    out = rp.run_protocol(store, token, "SRC_SYNTH2025", "RP_INTRO_ARGUMENT", answering([
        {"field": "phenomenon", "value": "Restatements are costly.", "quote": "Financial restatements impose large costs on investors"},
        {"field": "phenomenon", "value": "Again.", "quote": "and on the firms that issue them"}]))
    assert [r["field"] for r in out["records"]] == ["phenomenon"]
    assert [r["reason"] for r in out["rejected"]] == ["field_not_repeatable"]


def test_unknown_protocol_and_unreadable_source_are_errors(proj):
    store, token = proj
    with pytest.raises(KnowledgeError, match="PROTOCOL_UNKNOWN"):
        rp.run_protocol(store, token, "SRC_SYNTH2025", "RP_NOPE", answering([]))
    with pytest.raises(KnowledgeError, match="SOURCE_NOT_FOUND"):
        rp.run_protocol(store, token, "SRC_MISSING", "RP_METHOD", answering([]))


def test_passage_add_refuses_a_quote_that_is_not_in_the_text(proj):
    store, token = proj
    with pytest.raises(KnowledgeError, match="QUOTE_NOT_IN_SOURCE"):
        corpus.add_passage(store, token, source_id="SRC_SYNTH2025", quote="a sentence the paper never contains")
    ok = corpus.add_passage(store, token, source_id="SRC_SYNTH2025", quote=RESULT_Q)
    p = store.get_object(ok["object_id"])
    assert p["content"]["section"] == "4 Results" and p["content"]["source_revision"] == 1


def test_a_stored_text_that_was_altered_on_disk_is_not_silently_read(proj):
    store, token = proj
    sha = store.get_object("SRC_SYNTH2025")["content"]["artifact_sha256"]
    store.artifacts.path_for(sha).write_bytes(b"tampered")
    with pytest.raises(KnowledgeError, match="FULLTEXT_RETRIEVAL_FAILED"):
        corpus.source_text(store, "SRC_SYNTH2025")


@pytest.mark.skipif(not SAMPLE_PDF.exists(), reason="the pilot paper is not in this checkout (sample/ is not committed)")
def test_protocols_on_the_real_pilot_paper(tmp_path):
    pytest.importorskip("pypdf")
    store, token = StateStore.create(tmp_path / "home", "PRJ_REAL")
    ref = corpus.import_source_file(store, token, SAMPLE_PDF, citation_key="zavitsanos2025", year=2025,
                                    license_note="CC BY 4.0 (ICAIF 2025)")
    assert ref["sections"] >= 8

    def handler(system, user):
        # pick real sentences from what the model was shown; the adapter never invents text
        body = user.split("--- TEXT START ---\n")[1].split("\n--- TEXT END ---")[0]
        sentences = [s.strip().replace("\n", " ") for s in body.split(". ") if len(s.strip()) > 60]
        recs = [{"field": "claimed_contribution", "value": "as stated", "quote": sentences[0][:140]}] if sentences else []
        return {"records": recs, "gaps": []}

    out = rp.run_protocol(store, token, ref["object_id"], "RP_CONTRIBUTION", ScriptedModelAdapter(custom_handler=handler))
    assert out["status"] == "EXTRACTED" and out["records"] and not out["rejected"]
    for rec in out["records"]:
        assert corpus.verify_passage(store, rec["passage_id"])["source_match"] == "VERIFIED"


def test_method_protocol_also_reads_sample_measurement_and_unlabeled_sections(tmp_path):
    """On the real pilot paper the label and sample construction sit under a heading that matches no label ("3 Misstatements under ..."),
    so a protocol that reads only the method-labelled sections never saw them."""
    store, token = StateStore.create(tmp_path / "home", "PRJ_UNL", owner_id="researcher")
    text = ("Abstract\nA study.\n\n1 Introduction\nWe motivate it.\n\n3 Misstatements under the PU framework\nLabels come from restatement dates in a public database. "
            "We keep firm-years with complete financial data.\n\n4 Experimental setup\nWe train on three earlier years.\n\n4.1 Experimental results\nAverage precision is low.\n\n"
            "Data\nVariables are measured at fiscal year end.\n\nReferences\n[1] x\n")
    corpus.import_source(store, token, citation_key="unl2026", text=text)
    from research_harness.workflows.sections import detect_sections
    labels = {s.heading: s.label for s in detect_sections(text) if s.heading}
    assert labels["3 Misstatements under the PU framework"] == "other" and labels["Data"] == "data_sample"
    method = answering([])
    out = rp.run_protocol(store, token, "SRC_UNL2026", "RP_METHOD", method)
    assert out["sections_read"] == ["3 Misstatements under the PU framework", "4 Experimental setup", "Data"] and out["section_fallback"] is False
    shown = seen_text(method)
    assert "restatement dates" in shown and "complete financial data" in shown and "fiscal year end" in shown and "Average precision is low" not in shown
    contribution = answering([])
    out = rp.run_protocol(store, token, "SRC_UNL2026", "RP_CONTRIBUTION", contribution)
    assert "restatement dates" not in seen_text(contribution)    # protocols that did not ask for unlabeled sections do not get them
    # RP_THEORY asks for them since version 3 (WP6.1): conceptual papers name their sections after their ideas, so a theory protocol that skipped them would miss the theory
    assert rp.get_protocol("RP_METHOD").include_unlabeled is True and rp.get_protocol("RP_THEORY").include_unlabeled is True and rp.get_protocol("RP_CONTRIBUTION").include_unlabeled is False
    theory = answering([])
    rp.run_protocol(store, token, "SRC_UNL2026", "RP_THEORY", theory)
    assert "restatement dates" in seen_text(theory)
