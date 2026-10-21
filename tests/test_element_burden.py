"""Keeping the researcher's workload proportionate: caps per field, decisive versus supporting elements, one element per passage, and
batch tools for adjudication. None of it lowers what a confirmation means: only decisive elements are required to be meaning-verified,
and nothing is confirmed without the researcher's own verdict."""
from __future__ import annotations

import json

import pytest

from research_harness.cli import main
from research_harness.knowledge import corpus, lifecycle, literature, playbooks
from research_harness.knowledge.common import KnowledgeError
from research_harness.state.actors import register_actor
from research_harness.state.store import StateStore
from research_harness.workflows import eca, reading_protocols as rp
from research_harness.workflows.adapters import ScriptedModelAdapter
from tests import m3_fixture as fx

SAMPLE_Q = "drop firms with fewer than three consecutive annual observations"
LABEL_Q = "The outcome label equals one when the firm later filed an amended annual report for an accounting error"
VALID_Q = "Models are validated by training on the earlier years and testing on the last two years"
VALID_SUB = "training on the earlier years and testing on the last two years"
DESIGN_Q = "We estimate a logistic regression with firm-level accruals and an industry indicator as regressors"
EXTRA = ["We start from all annual reports filed between 2005 and 2015", "firms with fewer than three consecutive annual observations",
         "Because the panel is unbalanced", "the estimator performs well only when every firm is observed"]


def R(field, quote, value="v"):
    return {"field": field, "quote": quote, "value": value}


def scripted(records):
    return ScriptedModelAdapter(model_id="scripted-extractor", default_response={"records": records, "gaps": []})


def eca_ok():
    return ScriptedModelAdapter(model_id="scripted-adjudicator", default_response={
        "passage_proposition": "As quoted.", "equivalence": "EQUIVALENT", "dropped_conditions": [], "cue_handling": [{"cue": "only when", "handling": "carried"}],
        "verdict": "SUPPORTED_AS_STATED", "required_qualification": []})


@pytest.fixture
def proj(tmp_path):
    store, token = StateStore.create(tmp_path / "home", "PRJ_BURDEN", owner_id="researcher")
    corpus.import_source(store, token, citation_key="synth2025", text=(fx.FIX / "synthetic_paper_misstatement.txt").read_text(encoding="utf-8"))
    return store, token


def test_the_protocol_caps_records_per_field_and_lists_what_it_dropped(proj):
    store, token = proj
    ex = scripted([R("sample_selection", SAMPLE_Q), *[R("sample_selection", q) for q in EXTRA], R("label_construction", LABEL_Q)])
    out = rp.run_protocol(store, token, fx.PAPER_A, "RP_METHOD", ex)
    cap = rp.get_protocol("RP_METHOD").field("sample_selection")["max_records"]
    assert cap == 3 and rp.get_protocol("RP_METHOD").field("model_or_estimator")["max_records"] == 6 and [r["field"] for r in out["records"]].count("sample_selection") == 3 and [r["field"] for r in out["records"]].count("label_construction") == 1
    over = [r for r in out["rejected"] if r["reason"] == "over_cap"]
    assert len(over) == 2 and all(r["cap"] == 3 for r in over)                                # the rest is listed, not silently dropped
    assert "At most 3 records, the most decision-relevant first" in ex.recorded_requests[0].system_prompt


def test_overlapping_records_are_one_element_and_context_is_supporting_not_critical(proj):
    store, token = proj
    review = literature.run_literature(store, token, "method", [fx.PAPER_A], scripted([
        R("sample_selection", SAMPLE_Q, "short panels dropped"), R("label_construction", LABEL_Q, "label is a later amended report"),
        R("validation_or_tests", VALID_Q, "temporal split"), R("interpretation_rule", VALID_SUB, "the same split text, extracted again"),
        R("study_design", DESIGN_Q, "logistic regression")]))
    pb = playbooks.draft_playbook_from_methods(store, token, review, playbook_id="PB_B", label="pb", scope={"study_design": "predictive_modeling"})
    els = {e["source_field"]: e for e in store.get_object("PB_B")["content"]["elements"]}
    assert pb["elements"] == 4 and pb["critical"] == 3 and set(els) == {"sample_selection", "label_construction", "validation_or_tests", "study_design"}
    assert els["validation_or_tests"]["also_extracted_as"] == ["interpretation_rule"]               # one element for the same words
    design = els["study_design"]
    assert design["critical"] is False and design["risk_tier"] == "STANDARD" and design["supporting"] is True and design["evidence_basis"][0]["quote_verified"] is True
    assert all(e["critical"] and e["risk_tier"] == "HIGH" for f, e in els.items() if f != "study_design")


def test_only_decisive_elements_must_be_meaning_verified_and_planned(proj):
    store, token = proj
    review = literature.run_literature(store, token, "method", [fx.PAPER_A], scripted([R("sample_selection", SAMPLE_Q), R("study_design", DESIGN_Q)]))
    playbooks.draft_playbook_from_methods(store, token, review, playbook_id="PB_C", label="pb", scope={"study_design": "predictive_modeling"})
    els = {e["source_field"]: e for e in store.get_object("PB_C")["content"]["elements"]}
    assert lifecycle.source_fidelity(store, "PB_C")["source_fidelity"] == "QUOTE_VERIFIED"
    r = eca.adjudicate(store, token, eca.resolve_statement(store, "PB_C", element=els["sample_selection"]["id"]), els["sample_selection"]["evidence_basis"][0]["ref"], eca_ok())
    eca.confirm(store, token, r["adjudication_id"], verdict="SUPPORTED_AS_STATED", rationale="Reads the passage correctly.")
    fid = lifecycle.source_fidelity(store, "PB_C")
    assert fid["source_fidelity"] == "MEANING_VERIFIED" and [d["element"] for d in fid["elements"]] == [els["sample_selection"]["id"]]    # the supporting one is not asked for
    steps = [{"id": "s", "element": els["sample_selection"]["id"], "fidelity": "inherited", "command": ["python", "x.py"]}]
    assert playbooks.validate_steps(store.get_object("PB_C"), steps) == []                           # a plan need not address supporting context
    assert ("PB_C", els["study_design"]["id"]) in {(x["object_id"], x["element"]) for x in eca.iter_statements(store) if x["risk_tier"] == "STANDARD"}


def test_run_pending_adjudicates_what_the_policy_asks_for_and_nothing_else(proj):
    store, token = proj
    review = literature.run_literature(store, token, "method", [fx.PAPER_A], scripted([R("sample_selection", SAMPLE_Q), R("label_construction", LABEL_Q),
                                                                                       R("study_design", DESIGN_Q)]))
    playbooks.draft_playbook_from_methods(store, token, review, playbook_id="PB_P", label="pb")
    with store.write_tx(token=token) as tx:     # a high-impact claim with no passage: it cannot be adjudicated here
        tx.add_revision(object_id="CLAIM_NP", object_type="Claim", origin={}, edges=[], content={"text": "A claim.", "claim_tier": "descriptive", "attributes": {"risk_tier": "HIGH"}})
    adapter = eca_ok()
    out = eca.run_pending(store, token, adapter, rate=0.2)
    assert out["adjudicated"] == 3 and out["model_calls"] == 3 and out["verdicts"] == {"SUPPORTED_AS_STATED": 3}      # 2 HIGH + the one supporting element (sampled: >= 1 per stratum)
    assert [s["object_id"] for s in out["skipped"]] == ["CLAIM_NP"] and out["failed"] == [] and len(adapter.recorded_requests) == 3
    again = eca.run_pending(store, token, adapter)
    assert again["model_calls"] == 0 and len(adapter.recorded_requests) == 3                        # nothing left to do, nothing re-called
    broken = ScriptedModelAdapter(default_response="not json")
    with store.write_tx(token=token) as tx:
        tx.add_revision(object_id="PB_P", object_type="MethodPlaybook", origin={}, edges=[], content={**store.get_object("PB_P")["content"], "elements": [
            {**e, "text": e["text"] + " (revised)"} for e in store.get_object("PB_P")["content"]["elements"]]})
    assert eca.run_pending(store, token, broken)["failed"][0]["error"] == "invalid_output"


def test_the_review_sheet_and_the_batch_apply_only_what_the_researcher_decided(proj, tmp_path):
    store, token = proj
    review = literature.run_literature(store, token, "method", [fx.PAPER_A], scripted([R("sample_selection", SAMPLE_Q), R("label_construction", LABEL_Q), R("validation_or_tests", VALID_Q)]))
    playbooks.draft_playbook_from_methods(store, token, review, playbook_id="PB_S", label="pb")
    els = store.get_object("PB_S")["content"]["elements"]
    odd = ScriptedModelAdapter(default_response={"passage_proposition": "p", "equivalence": "BROADER", "dropped_conditions": ["x"], "cue_handling": [], "verdict": "SUPPORTED_AS_STATED",
                                                 "required_qualification": []})
    eca.adjudicate(store, token, eca.resolve_statement(store, "PB_S", element=els[0]["id"]), els[0]["evidence_basis"][0]["ref"], odd)    # guard fires
    for e in els[1:]:
        eca.adjudicate(store, token, eca.resolve_statement(store, "PB_S", element=e["id"]), e["evidence_basis"][0]["ref"], eca_ok())
    rows = eca.review_sheet(store)
    assert len(rows) == 3 and rows[0]["needs_attention"] is True and rows[0]["model"]["guard"] == "verdict_inconsistent_with_equivalence"
    assert all(r["decision"] == {"verdict": "", "rationale": "", "qualification_revision": None} for r in rows)           # nothing is pre-filled
    assert rows[0]["passage"]["quote"] and rows[0]["statement"]["text"] and "qualifier_cues" in rows[0]

    ids = [r["adjudication_id"] for r in rows]
    other = register_actor(store, token, actor_id="importer_only", display_name="i", roles=["importer"])
    assert {f["error"] for f in eca.confirm_batch(store, other["token"], [{"adjudication_id": ids[0], "verdict": "NOT_SUPPORTED", "rationale": "a long enough reason"}])["failed"]} == {"ROLE_REQUIRED"}
    res = eca.confirm_batch(store, token, [
        {"adjudication_id": ids[0], "verdict": "NOT_SUPPORTED", "rationale": "The statement drops the panel condition."},
        {"adjudication_id": ids[1], "verdict": "SUPPORTED_AS_STATED", "rationale": "Carries the condition exactly."},
        {"adjudication_id": ids[2], "rationale": "no verdict given"},
        {"adjudication_id": ids[1], "verdict": "SUPPORTED_AS_STATED", "rationale": "duplicate entry in the file"},
        {"adjudication_id": "ADJ_NOPE", "verdict": "SUPPORTED_AS_STATED", "rationale": "an id that does not exist"},
        {"adjudication_id": ids[2], "verdict": "SUPPORTED_AS_STATED", "rationale": "ok"}])
    assert [c["adjudication_id"] for c in res["confirmed"]] == ids[:2]
    assert [(f["adjudication_id"], f["error"].split(":")[0]) for f in res["failed"]] == [
        (ids[2], "NO_VERDICT"), (ids[1], "MISSING_OR_DUPLICATE_ID"), ("ADJ_NOPE", "ECA_NOT_FOUND"), (ids[2], "MISSING_OR_DUPLICATE_ID")]
    assert [r["adjudication_id"] for r in eca.review_sheet(store)] == [ids[2]]                       # only the undecided item is still waiting
    assert eca.statement_assessment(store, eca.resolve_statement(store, "PB_S", element=els[0]["id"]))["assessment"] == "CONTESTED"


def test_the_sheet_and_batch_through_the_cli(proj, tmp_path, capsys):
    store, token = proj
    review = literature.run_literature(store, token, "method", [fx.PAPER_A], scripted([R("sample_selection", SAMPLE_Q)]))
    playbooks.draft_playbook_from_methods(store, token, review, playbook_id="PB_L", label="pb")
    e = store.get_object("PB_L")["content"]["elements"][0]
    eca.adjudicate(store, token, eca.resolve_statement(store, "PB_L", element=e["id"]), e["evidence_basis"][0]["ref"], eca_ok())
    tf = tmp_path / "t"
    tf.write_text(token, encoding="utf-8")
    base = ["--project", "PRJ_BURDEN", "--home", str(tmp_path / "home"), "--token-file", str(tf)]
    sheet = tmp_path / "sheet.json"
    assert main(["adjudicate", "sheet", "--out", str(sheet), "--project", "PRJ_BURDEN", "--home", str(tmp_path / "home")]) == 0
    rows = json.loads(sheet.read_text(encoding="utf-8"))
    assert len(rows) == 1 and rows[0]["decision"]["verdict"] == ""
    capsys.readouterr()
    rows[0]["decision"] = {"verdict": "SUPPORTED_AS_STATED", "rationale": "Reads the passage correctly."}
    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps([{"adjudication_id": rows[0]["adjudication_id"], **rows[0]["decision"]}]), encoding="utf-8")
    assert main(["adjudicate", "confirm-batch", "--file", str(decisions), *base]) == 0
    assert "1 confirmed, 0 not applied" in capsys.readouterr().out
    assert main(["adjudicate", "confirm-batch", "--file", str(decisions), *base]) == 0 and "not applied" in capsys.readouterr().out


def test_reconsider_rederives_an_extraction_from_the_model_answer_without_a_model_call(proj, monkeypatch):
    store, token = proj
    answer = [R("sample_selection", SAMPLE_Q), *[R("sample_selection", q) for q in EXTRA], R("label_construction", LABEL_Q)]
    real_find = rp.quotes.find_quote
    monkeypatch.setattr(rp.quotes, "find_quote", lambda text, quote: None if quote == LABEL_Q else real_find(text, quote))   # a matcher that was stricter then
    ex = scripted(answer)
    first = rp.run_protocol(store, token, fx.PAPER_A, "RP_METHOD", ex)
    monkeypatch.undo()
    assert [r["field"] for r in first["records"]].count("sample_selection") == 3 and {r["reason"] for r in first["rejected"]} == {"over_cap", "quote_not_in_text_shown"}
    calls = len(ex.recorded_requests)

    out = rp.reconsider(store, token, fx.PAPER_A, "RP_METHOD", caps={"sample_selection": 5})
    assert len(ex.recorded_requests) == calls and out["reused"] is False                       # no model call
    assert [r["field"] for r in out["records"]].count("sample_selection") == 5 and [r["field"] for r in out["records"]].count("label_construction") == 1
    assert out["gained_by_matcher"] == 1 and out["rejected"] == [] and out["caps"]["sample_selection"] == 5 and out["reconsidered_from_run_id"] == first["run_id"]
    for r in out["records"]:
        assert corpus.verify_passage(store, r["passage_id"])["source_match"] == "VERIFIED"      # everything kept is still a verified span of the stored text
    with store.read() as conn:
        row = conn.execute("SELECT status_reason, usage FROM workflow_runs WHERE run_id=?", (out["run_id"],)).fetchone()
    assert row[0] == "reconsidered" and json.loads(row[1])["llm_calls"] == 0
    # later reuse finds the reconsidered result, not the first one
    again = rp.run_protocol(store, token, fx.PAPER_A, "RP_METHOD", ex)
    assert again["reused"] is True and len(again["records"]) == 6 and len(ex.recorded_requests) == calls
    # the default caps apply again when none are given; a quote that is still not in the text stays rejected
    back = rp.reconsider(store, token, fx.PAPER_A, "RP_METHOD")
    assert [r["field"] for r in back["records"]].count("sample_selection") == 3 and sum(1 for r in back["rejected"] if r["reason"] == "over_cap") == 2
    with pytest.raises(KnowledgeError, match="CAP_INVALID"):
        rp.parse_caps(["sample_selection=0"])
    assert rp.parse_caps(["a=2", "b=3"]) == {"a": 2, "b": 3}


def test_reconsider_needs_an_extraction_to_start_from(proj):
    store, token = proj
    with pytest.raises(KnowledgeError, match="NOTHING_TO_RECONSIDER"):
        rp.reconsider(store, token, fx.PAPER_A, "RP_METHOD")
