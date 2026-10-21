"""WP1a (M5 contracts C1, C2): a run and the objects it produced commit together (I22); blob references are found at any depth.

Before WP1a, `run_protocol` stored passages and then registered the run in a second transaction, `eca.adjudicate` registered a
SUCCEEDED run and then wrote the record, and `compare_contribution` registered the run before revising the argument. A refused write
(or a crash) between the two left passages without a run, or a SUCCEEDED run without its output.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from research_harness.core.errors import AuthorizationError, HarnessError
from research_harness.knowledge import argument, corpus, literature
from research_harness.protocols import schema
from research_harness.state import workflow_runs
from research_harness.state.blob_refs import BLOB_KEYS, FINGERPRINT_KEYS, blob_digests_in
from research_harness.state.store import StateStore
from research_harness.validation.integrity import audit_project_integrity, verify_database_integrity
from research_harness.workflows import eca
from research_harness.workflows import reading_protocols as rp
from research_harness.workflows.adapters import ScriptedModelAdapter
from tests import m3_fixture as fx

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_paper_misstatement.txt"
SAMPLE_Q = "drop firms with fewer than three consecutive annual observations"
QUOTE = "the estimator performs well only when every firm is observed in every year of the estimation window"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def proj(tmp_path):
    store, token = StateStore.create(tmp_path / "home", "PRJ_ATOM")
    corpus.import_source(store, token, citation_key="synth2025", text=FIXTURE.read_text(encoding="utf-8"))
    return store, token


def extractor():
    return ScriptedModelAdapter(model_id="scripted-extractor", default_response={"records": [
        {"field": "sample_selection", "value": "Firms with fewer than three consecutive years are dropped.", "quote": SAMPLE_Q}], "gaps": []})


def adjudicator():
    return ScriptedModelAdapter(model_id="scripted-adjudicator", default_response={
        "passage_proposition": "p", "equivalence": "EQUIVALENT", "dropped_conditions": [], "cue_handling": [],
        "verdict": "SUPPORTED_AS_STATED", "required_qualification": [], "rationale": "r"})


def runs(store, workflow):
    with store.read() as conn:
        return workflow_runs.list_runs(conn, workflow=workflow)


def passages_with_key(store):
    with store.read() as conn:
        return conn.execute("SELECT count(*) FROM objects WHERE object_type='EvidencePassage' AND "
                            "json_extract(content,'$.extraction_key') IS NOT NULL").fetchone()[0]


def i22_findings(store):
    return [f for f in verify_database_integrity(store) if f["rule"] == "SUCCEEDED_RUN_WITHOUT_OUTPUTS"]


# --------------------------------------------------------------------------------------------------- extraction

def test_extraction_refused_write_leaves_no_passages_and_no_succeeded_run(proj):
    store, _ = proj
    with pytest.raises(AuthorizationError):
        rp.run_protocol(store, "not-a-token", "SRC_SYNTH2025", "RP_METHOD", extractor())
    assert passages_with_key(store) == 0
    (run,) = runs(store, "reading_protocol:RP_METHOD")
    # the model call that was made is not lost from the registry: a FAILED run with its usage
    assert run["execution_status"] == "FAILED" and run["status_reason"] == "write_refused:AUTHENTICATION_FAILED"
    assert run["usage"]["llm_calls"] == 1
    assert i22_findings(store) == []


def test_extraction_fault_before_run_row_rolls_back_everything(proj, monkeypatch):
    store, token = proj
    monkeypatch.setenv("RESEARCH_TEST_FAULT", "raise:extraction_before_run")
    with pytest.raises(RuntimeError, match="injected fault"):
        rp.run_protocol(store, token, "SRC_SYNTH2025", "RP_METHOD", extractor())
    monkeypatch.delenv("RESEARCH_TEST_FAULT")
    assert passages_with_key(store) == 0
    assert [r["execution_status"] for r in runs(store, "reading_protocol:RP_METHOD")] == ["FAILED"]
    # a retry is a clean run; nothing partial is reused
    out = rp.run_protocol(store, token, "SRC_SYNTH2025", "RP_METHOD", extractor())
    assert out["status"] == "EXTRACTED" and out["reused"] is False and passages_with_key(store) == 1
    ok = [r for r in runs(store, "reading_protocol:RP_METHOD") if r["execution_status"] == "SUCCEEDED"]
    assert len(ok) == 1 and ok[0]["run_id"] == out["run_id"] and ok[0]["artifact_sha256"]
    assert i22_findings(store) == []


def test_extraction_killed_before_commit_leaves_only_an_orphan_blob(proj, tmp_path):
    store, token = proj
    script = f"""
from research_harness.state.store import StateStore
from research_harness.workflows import reading_protocols as rp
from research_harness.workflows.adapters import ScriptedModelAdapter
store = StateStore.open(r'{store.paths.home}', '{store.project_id}')
adapter = ScriptedModelAdapter(model_id='scripted-extractor', default_response={{'records': [
    {{'field': 'sample_selection', 'value': 'v', 'quote': {SAMPLE_Q!r}}}], 'gaps': []}})
rp.run_protocol(store, {token!r}, 'SRC_SYNTH2025', 'RP_METHOD', adapter)
"""
    env = {**os.environ, "RESEARCH_TEST_FAULT": "exit:extraction_before_run",
           "PYTHONPATH": str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    blobs_before = set(store.artifacts.blobs())
    proc = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, cwd=str(ROOT))
    assert proc.returncode == 137, proc.stderr.decode("utf-8", "replace")
    assert passages_with_key(store) == 0 and runs(store, "reading_protocol:RP_METHOD") == []
    new_blobs = set(store.artifacts.blobs()) - blobs_before
    assert len(new_blobs) == 1                                  # the result artefact written before the commit
    assert set(store.recover()["orphan_artifacts"]) >= new_blobs
    assert i22_findings(store) == []
    assert rp.run_protocol(store, token, "SRC_SYNTH2025", "RP_METHOD", extractor())["status"] == "EXTRACTED"


def test_reconsider_commits_passages_and_run_together(proj, monkeypatch):
    store, token = proj
    rp.run_protocol(store, token, "SRC_SYNTH2025", "RP_METHOD", extractor())
    before = len(runs(store, "reading_protocol:RP_METHOD"))
    monkeypatch.setenv("RESEARCH_TEST_FAULT", "raise:extraction_before_run")
    with pytest.raises(RuntimeError):
        rp.reconsider(store, token, "SRC_SYNTH2025", "RP_METHOD")
    monkeypatch.delenv("RESEARCH_TEST_FAULT")
    after = runs(store, "reading_protocol:RP_METHOD")
    assert len(after) == before + 1 and after[0]["execution_status"] == "FAILED"
    out = rp.reconsider(store, token, "SRC_SYNTH2025", "RP_METHOD")
    assert out["run_id"] and i22_findings(store) == []


# --------------------------------------------------------------------------------------------------- ECA

def _eca_setup(store, token):
    corpus.add_passage(store, token, source_id="SRC_SYNTH2025", quote=QUOTE, passage_id="EP_BALANCE")
    with store.write_tx(token=token) as tx:
        tx.add_revision(object_id="CLAIM_PANEL", object_type="Claim", origin={}, edges=[],
                        content={"text": "The estimator needs every firm observed in every year.", "claim_tier": "predictive"})
    return eca.resolve_statement(store, "CLAIM_PANEL")


def _adjudications(store):
    with store.read() as conn:
        return conn.execute("SELECT count(*) FROM objects WHERE object_type='Adjudication'").fetchone()[0]


def test_adjudication_refused_write_leaves_failed_run_not_succeeded(proj):
    store, token = proj
    st = _eca_setup(store, token)
    with pytest.raises(AuthorizationError):
        eca.adjudicate(store, "not-a-token", st, "EP_BALANCE", adjudicator())
    assert _adjudications(store) == 0
    (run,) = runs(store, "eca_adjudication")
    assert run["execution_status"] == "FAILED" and run["status_reason"].startswith("write_refused:") and run["usage"]["llm_calls"] == 1
    assert i22_findings(store) == []


def test_adjudication_fault_before_run_row_rolls_back_record(proj, monkeypatch):
    store, token = proj
    st = _eca_setup(store, token)
    monkeypatch.setenv("RESEARCH_TEST_FAULT", "raise:adjudication_before_run")
    with pytest.raises(RuntimeError):
        eca.adjudicate(store, token, st, "EP_BALANCE", adjudicator())
    monkeypatch.delenv("RESEARCH_TEST_FAULT")
    assert _adjudications(store) == 0 and [r["execution_status"] for r in runs(store, "eca_adjudication")] == ["FAILED"]
    res = eca.adjudicate(store, token, st, "EP_BALANCE", adjudicator())
    ok = [r for r in runs(store, "eca_adjudication") if r["execution_status"] == "SUCCEEDED"]
    assert _adjudications(store) == 1 and len(ok) == 1 and res["run_id"] == ok[0]["run_id"]
    assert i22_findings(store) == []


def test_integrity_reports_a_succeeded_run_without_its_record(proj):
    """What the old order could leave behind is reported (never repaired)."""
    store, token = proj
    st = _eca_setup(store, token)
    passage = store.get_object("EP_BALANCE")
    eca._register(store, fingerprint="f" * 64, execution=eca.ExecutionStatus.SUCCEEDED, assessment=eca.AssessmentStatus.PROVISIONAL,
                  reason=None, usage={"llm_calls": 1}, model=None, artifact=None, statement=st,
                  passage={"object_id": "EP_BALANCE", "revision": passage["revision"]})
    (f,) = i22_findings(store)
    assert f["workflow"] == "eca_adjudication" and f["missing"] == ["adjudication record"]
    assert audit_project_integrity(store)["status"] != "PASS"


# --------------------------------------------------------------------------------------------------- contribution comparison

def test_comparison_refused_write_leaves_failed_run(tmp_path):
    store, token = StateStore.create(tmp_path / "home", "PRJ_ATOM_3B")
    fx.import_corpus(store, token)
    focus = argument.analyze_introduction(store, token, fx.FOCUS, fx.extractor())["argument_id"]
    review = literature.run_literature(store, token, "contribution", [fx.PAPER_A, fx.PAPER_B], fx.extractor())
    comparer = ScriptedModelAdapter(model_id="scripted-comparer", default_response={"differentiation": []})
    rev_before = store.get_object(focus)["revision"]
    with pytest.raises(AuthorizationError):
        argument.compare_contribution(store, "not-a-token", focus, review, comparer)
    assert store.get_object(focus)["revision"] == rev_before
    assert [r["execution_status"] for r in runs(store, "contribution_comparison")] == ["FAILED"]
    out = argument.compare_contribution(store, token, focus, review, comparer)
    assert store.get_object(focus)["content"]["comparison_run"] == out["run_id"]
    ok = [r for r in runs(store, "contribution_comparison") if r["execution_status"] == "SUCCEEDED"]
    assert [r["run_id"] for r in ok] == [out["run_id"]] and i22_findings(store) == []


# --------------------------------------------------------------------------------------------------- O17: blob references at any depth

def test_nested_blob_reference_is_neither_an_orphan_nor_unchecked(proj):
    store, token = proj
    digest = store.artifacts.put_bytes(b"nested blob")
    missing = "ab" * 32
    with store.write_tx(token=token) as tx:
        tx.add_revision(object_id="KI_NESTED", object_type="KnowledgeItem", origin={}, edges=[],
                        content={"label": "n", "body": {"assets": [{"artifact_sha256": digest}], "lost": {"blob_sha256": missing},
                                                        "rubric": {"sha256": "cd" * 32}}})
    assert digest not in store.recover()["orphan_artifacts"]
    findings = verify_database_integrity(store)
    assert {"rule": "REFERENCED_ARTIFACT_MISSING", "digest": missing} in findings
    # a bare `sha256` outside a `files` list is a fingerprint (e.g. a rubric digest), not a missing blob
    assert not any(f.get("digest") == "cd" * 32 for f in findings)


def test_files_entries_name_blobs_and_fingerprints_do_not():
    content = {"body": {"files": [{"name": "p.csv", "sha256": "11" * 32}], "sha256": "22" * 32,
                        "text_sha256": "33" * 32, "deep": [{"sections_sha256": "44" * 32}]}}
    assert blob_digests_in(content) == {"11" * 32, "44" * 32}


def test_every_sha256_property_in_the_schema_is_classified():
    text = json.dumps(schema._TYPE_PROPS, default=str)
    keys = {k for k in __import__("re").findall(r'"([a-z_]*sha256)"', text)}
    unclassified = keys - BLOB_KEYS - FINGERPRINT_KEYS
    assert not unclassified, f"decide whether these name CAS blobs or fingerprints: {sorted(unclassified)}"
    assert not (BLOB_KEYS & FINGERPRINT_KEYS)


def test_run_artefacts_are_not_reported_as_orphans(proj):
    store, token = proj
    rp.run_protocol(store, token, "SRC_SYNTH2025", "RP_METHOD", extractor())
    run = next(r for r in runs(store, "reading_protocol:RP_METHOD") if r["execution_status"] == "SUCCEEDED")
    orphans = {f["digest"] for f in verify_database_integrity(store) if f["rule"] == "ORPHAN_ARTIFACT_BLOB"}
    assert run["artifact_sha256"] not in orphans


# --------------------------------------------------------------------------------------------------- expected_head

def test_add_revision_expected_head_refuses_stale(proj):
    store, token = proj
    with store.write_tx(token=token) as tx:
        tx.add_revision(object_id="RQ_X", object_type="ResearchQuestion", origin={}, edges=[], content={"text": "q1"}, expected_head=0)
    with pytest.raises(HarnessError) as e:
        with store.write_tx(token=token) as tx:
            tx.add_revision(object_id="RQ_X", object_type="ResearchQuestion", origin={}, edges=[], content={"text": "q2"}, expected_head=0)
    assert e.value.code == "STALE_REVISION" and e.value.details["actual_revision"] == 1
    with store.write_tx(token=token) as tx:
        tx.add_revision(object_id="RQ_X", object_type="ResearchQuestion", origin={}, edges=[], content={"text": "q2"}, expected_head=1)
    assert store.get_object("RQ_X")["revision"] == 2
