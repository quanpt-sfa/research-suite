"""Phase 4.1 (plan v3): the append-only workflow run registry, registry-based reuse, and legacy 9.1.1 history."""
from __future__ import annotations

import json
import shutil
import sqlite3

import pytest

from research_harness.cli import main as cli_main
from research_harness.core.errors import HarnessError
from research_harness.state import workflow_runs
from research_harness.state.schema import DB_SCHEMA_VERSION, upgrade_schema
from research_harness.state.store import StateStore
from research_harness.workflows import dispatcher as dispatcher_mod
from research_harness.workflows.adapters import ModelRefusal, ScriptedModelAdapter
from research_harness.workflows.context import build_context
from research_harness.workflows.dispatcher import _compute_input_fingerprint, run_workflow, run_workflow_sequence
from research_harness.workflows.model_io import output_schema
from research_harness.workflows.runs import (
    diff_runs, find_run_by_artifact, get_workflow_run, list_workflow_runs, load_run_prompt, load_workflow_output,
    run_history)

WORKFLOWS = ["contribution", "literature", "methods", "results", "discussion"]


@pytest.fixture
def proj(tmp_path):
    store, token = StateStore.create(tmp_path / "home", "PRJ_RUNS")
    with store.write_tx(token=token) as tx:
        add = lambda oid, t, c, e=(): tx.add_revision(object_id=oid, object_type=t, content=c, origin={}, edges=list(e))  # noqa: E731
        add("RQ_A", "ResearchQuestion", {"text": "Does board independence reduce restatements?"})
        add("SRC_A", "Source", {"citation_key": "Klein2002", "abstract": "Audit committees."}, [("depends_on", "RQ_A", 1, {})])
        add("EP_A", "EvidencePassage", {"locator": "p.389", "quote": "Independence is negatively related to accruals."},
            [("derived_from", "SRC_A", 1, {}), ("depends_on", "RQ_A", 1, {})])
        add("RES_A", "Result", {"summary": "Coefficient on independence is -0.12 (p<0.01)."}, [("depends_on", "RQ_A", 1, {})])
        add("CLAIM_A", "Claim", {"text": "Independence reduces restatements.", "claim_tier": "causal"},
            [("depends_on", "RQ_A", 1, {}), ("depends_on", "RES_A", 1, {}), ("supports", "EP_A", 1, {})])
    return store, token


def revise(proj, oid, otype, content, edges=()):
    store, token = proj
    with store.write_tx(token=token) as tx:
        tx.add_revision(object_id=oid, object_type=otype, content=content, origin={}, edges=list(edges))


def run(proj, wf="methods", **kw):
    kw.setdefault("research_question_id", "RQ_A")
    return run_workflow(wf, proj[0], **kw)


def valid_answer(wf):
    schema = output_schema(wf)
    zero = {"array": [], "object": {}, "string": "x", "boolean": True, "integer": 0, "number": 0.0}
    doc = {n: (None if "anyOf" in schema["properties"][n] else zero[schema["properties"][n].get("type", "object")])
           for n in schema["required"]}
    doc["evidence_refs"] = [{"object_id": "RQ_A", "revision": 1}]
    return doc


def rows(proj, **kw):
    return list(reversed(list_workflow_runs(proj[0], **kw)))  # oldest first


# --------------------------------------------------------------------------------------------------
# schema
# --------------------------------------------------------------------------------------------------

def test_fresh_project_has_the_registry_at_schema_9_0_4(proj):
    store, _ = proj
    assert DB_SCHEMA_VERSION == "9.0.4"
    with store.read() as c:
        assert workflow_runs.has_registry(c)
        assert c.execute("SELECT value FROM meta WHERE key='db_schema_version'").fetchone()[0] == "9.0.4"


def test_registry_rows_cannot_be_updated_or_deleted(proj):
    run(proj)
    store, _ = proj
    conn = store.connect()
    try:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("UPDATE workflow_runs SET legacy_status='SUCCESS'")
        with pytest.raises(sqlite3.DatabaseError, match="cannot be deleted"):
            conn.execute("DELETE FROM workflow_runs")
    finally:
        conn.close()


def test_registry_is_not_part_of_the_scientific_state_digest(proj):
    from research_harness.state.store import compute_state_digest
    store, _ = proj
    with store.read() as c:
        before, rev = compute_state_digest(c), store.current_revision(c)
    run(proj)
    with store.read() as c:
        assert compute_state_digest(c) == before and store.current_revision(c) == rev


def test_run_workflow_requires_the_registry(proj):
    store, _ = proj
    conn = store.connect()
    try:
        conn.execute("DROP TABLE workflow_runs")
    finally:
        conn.close()
    with pytest.raises(HarnessError) as exc:
        run(proj)
    assert exc.value.code == "SCHEMA_UPGRADE_REQUIRED" and "upgrade-schema" in str(exc.value)


# --------------------------------------------------------------------------------------------------
# what a row records
# --------------------------------------------------------------------------------------------------

def test_deterministic_run_is_registered_with_everything_needed_to_find_it_again(proj):
    store, _ = proj
    out = run(proj, "contribution")
    row = get_workflow_run(store, out.metadata["run_id"])
    assert (row["workflow"], row["research_question_id"], row["executor"]) == ("contribution", "RQ_A", "deterministic@9.2.0")
    assert row["legacy"] is False and row["reused_from_run_id"] is None
    assert row["input_fingerprint"] == out.input_fingerprint and row["skill_digest"]
    assert {i["object_id"] for i in row["input_revisions"]} >= {"RQ_A", "CLAIM_A"}
    assert (row["execution_status"], row["legacy_status"]) == ("SUCCEEDED", out.status)
    assert row["assessment_status"] == out.axes.assessment.value
    assert row["usage"]["workflow"] == "contribution" and row["usage"]["llm_calls"] == 0
    assert row["artifact_sha256"] == out.metadata["artifact_sha256"] and store.artifacts.verify(row["artifact_sha256"])
    manifest = json.loads(store.artifacts.get_bytes(row["context_manifest_sha256"]))
    assert manifest["manifest"]["workflow"] == "contribution" and manifest["objects"]
    assert row["prompt_sha256"] is None and row["model_descriptor"] is None


def test_model_run_keeps_the_exact_request_and_model_identity(proj):
    store, _ = proj
    adapter = ScriptedModelAdapter(default_response=valid_answer("contribution"))
    out = run(proj, "contribution", model_adapter=adapter, reuse_cached=False)
    row = get_workflow_run(store, out.metadata["run_id"])
    sent = adapter.recorded_requests[0]
    prompt = load_run_prompt(store, row["run_id"])
    assert (prompt["system_prompt"], prompt["user_prompt"]) == (sent.system_prompt, sent.user_prompt)
    assert row["model_descriptor"] == adapter.describe() and row["executor"].startswith("model@")
    assert row["assessment_status"] == "PROVISIONAL"


def test_refused_invalid_and_failed_runs_are_recorded_and_never_reusable(proj):
    store, _ = proj
    run(proj, "contribution", model_adapter=ScriptedModelAdapter(default_response=valid_answer("contribution")),
        max_input_tokens=200)  # refused: over budget
    run(proj, "contribution", model_adapter=ScriptedModelAdapter(default_response={"nope": 1}))  # invalid
    class Refuses(ScriptedModelAdapter):
        def generate(self, *a, **k):
            raise ModelRefusal("no", category="bio")
    run(proj, "contribution", model_adapter=Refuses())  # failed
    assert [r["legacy_status"] for r in rows(proj)] == ["INCOMPLETE_CONTEXT", "INVALID_OUTPUT", "FAILED"]
    assert [r["execution_status"] for r in rows(proj)] == ["NOT_RUN", "FAILED", "FAILED"]
    with store.read() as c:
        for r in rows(proj):
            assert workflow_runs.latest_reusable(c, "contribution", r["input_fingerprint"], ["SUCCESS", "REQUIRES_VERIFICATION"]) is None


# --------------------------------------------------------------------------------------------------
# reuse through the registry
# --------------------------------------------------------------------------------------------------

def test_reuse_returns_the_original_axes_and_links_back(proj):
    adapter = ScriptedModelAdapter(default_response=valid_answer("contribution"))
    first = run(proj, "contribution", model_adapter=adapter)
    again = run(proj, "contribution", model_adapter=adapter)
    assert again.status == "REUSED" and len(adapter.recorded_requests) == 1
    assert again.axes == first.axes and again.axes.assessment.value == "PROVISIONAL"
    assert again.metadata["reused_from_run_id"] == first.metadata["run_id"]
    assert again.metadata["original_status"] == first.status
    assert again.result == first.result and again.usage.llm_calls == 0 and again.usage.usage_source == "offline_cache"
    reuse_row = get_workflow_run(proj[0], again.metadata["run_id"])
    assert reuse_row["reused_from_run_id"] == first.metadata["run_id"] and reuse_row["parent_run_id"] is None
    assert reuse_row["artifact_sha256"] == first.metadata["artifact_sha256"]


def test_deterministic_reuse_keeps_requires_verification(proj):
    first = run(proj, "methods")
    again = run(proj, "methods")
    assert (first.status, again.status) == ("REQUIRES_VERIFICATION", "REUSED")
    assert again.axes.assessment.value == "PROVISIONAL"  # not collapsed to NOT_ASSESSED by the reuse


def test_reuse_survives_a_new_process_and_needs_no_cache_files(proj):
    store, _ = proj
    first = run(proj, "methods")
    shutil.rmtree(store.paths.root / "workflow_cache", ignore_errors=True)
    reopened = StateStore(store.paths)
    again = run_workflow("methods", reopened, research_question_id="RQ_A")
    assert again.status == "REUSED" and again.metadata["reused_from_run_id"] == first.metadata["run_id"]


def test_output_is_found_by_digest_and_nothing_is_an_orphan(proj):
    store, _ = proj
    out = run(proj, "contribution", model_adapter=ScriptedModelAdapter(default_response=valid_answer("contribution")))
    reopened = StateStore(store.paths)
    row = find_run_by_artifact(reopened, out.metadata["artifact_sha256"])
    assert row["run_id"] == out.metadata["run_id"]
    assert load_workflow_output(reopened, row["run_id"]).result == out.result
    orphans = set(reopened.recover()["orphan_artifacts"])
    assert not orphans & {row["artifact_sha256"], row["prompt_sha256"], row["context_manifest_sha256"]}


def test_a_corrupted_stored_output_forces_recomputation(proj):
    store, _ = proj
    first = run(proj, "methods")
    store.artifacts.path_for(first.metadata["artifact_sha256"]).write_bytes(b"garbage")
    again = run(proj, "methods")
    assert again.status != "REUSED" and again.metadata["run_id"] != first.metadata["run_id"]
    with pytest.raises(ValueError, match="FAILED_VERIFICATION"):
        load_workflow_output(store, first.metadata["run_id"])


def test_a_changed_input_misses_and_links_to_the_run_it_supersedes(proj):
    first = run(proj, "contribution")
    revise(proj, "RES_A", "Result", {"summary": "Coefficient is -0.05 (p=0.20) with firm fixed effects."})
    second = run(proj, "contribution")
    assert second.status != "REUSED"
    assert get_workflow_run(proj[0], second.metadata["run_id"])["parent_run_id"] == first.metadata["run_id"]
    third = run(proj, "contribution")  # reuse of the second: its parent link stays with the original run
    assert third.status == "REUSED" and third.metadata["reused_from_run_id"] == second.metadata["run_id"]
    history = run_history(proj[0], third.metadata["run_id"])
    assert [r["run_id"] for r in history] == [third.metadata["run_id"], second.metadata["run_id"], first.metadata["run_id"]]


def test_parent_links_are_per_workflow_question_and_focus(proj):
    a = run(proj, "contribution", focus_claims=["CLAIM_A"])
    revise(proj, "RES_A", "Result", {"summary": "Revised."})
    other_focus = run(proj, "contribution")  # no explicit focus -> different focus than `a`
    assert get_workflow_run(proj[0], other_focus.metadata["run_id"])["parent_run_id"] is None
    assert get_workflow_run(proj[0], a.metadata["run_id"])["parent_run_id"] is None


def test_sequence_records_which_runs_each_step_read(proj):
    outs = run_workflow_sequence(proj[0], ["results", "discussion", "contribution"], research_question_id="RQ_A")
    ids = [o.metadata["run_id"] for o in outs]
    store = proj[0]
    assert get_workflow_run(store, ids[0])["upstream_run_ids"] == []
    assert get_workflow_run(store, ids[1])["upstream_run_ids"] == [ids[0]]
    assert get_workflow_run(store, ids[2])["upstream_run_ids"] == ids[:2]


def test_revised_result_reruns_only_the_workflows_that_read_it(proj):
    for wf in WORKFLOWS:
        run(proj, wf)
    revise(proj, "RES_A", "Result", {"summary": "Changed result."})
    status = {wf: run(proj, wf).status for wf in WORKFLOWS}
    assert status["literature"] == "REUSED"
    assert all(status[w] != "REUSED" for w in ("results", "discussion", "contribution", "methods"))


# --------------------------------------------------------------------------------------------------
# fingerprint
# --------------------------------------------------------------------------------------------------

def test_a_budget_that_changes_the_selection_misses_but_a_bigger_one_with_the_same_selection_hits(proj):
    first = run(proj, "literature", max_input_tokens=12000)
    assert run(proj, "literature", max_input_tokens=20000).status == "REUSED"
    full = build_context(proj[0], "literature", research_question_id="RQ_A", max_input_tokens=12000)
    tight = run(proj, "literature", max_input_tokens=max(1, full.manifest["system_prompt_tokens"] + 60))
    assert tight.status != "REUSED" and tight.input_fingerprint != first.input_fingerprint


def test_executor_version_is_part_of_the_fingerprint(proj, monkeypatch):
    run(proj, "methods")
    monkeypatch.setitem(dispatcher_mod.EXECUTOR_VERSIONS, "methods", "9.2.1")
    assert run(proj, "methods").status != "REUSED"


def test_upstream_digest_ignores_timestamps_and_task_ids(proj):
    ctx = build_context(proj[0], "discussion", research_question_id="RQ_A")
    a = run(proj, "results", reuse_cached=False)
    b = run(proj, "results", reuse_cached=False)
    assert a.timestamp != b.timestamp and a.task_id != b.task_id
    assert _compute_input_fingerprint("discussion", ctx, {"upstream_artifacts": [a]}) == \
        _compute_input_fingerprint("discussion", ctx, {"upstream_artifacts": [b]})


# --------------------------------------------------------------------------------------------------
# diff / CLI
# --------------------------------------------------------------------------------------------------

def test_diff_runs_reports_input_status_and_result_changes(proj):
    first = run(proj, "results")
    revise(proj, "RES_A", "Result", {"summary": "Coefficient is -0.05 (p=0.20)."})
    second = run(proj, "results")
    d = diff_runs(proj[0], first.metadata["run_id"], second.metadata["run_id"])
    assert any(c["object_id"] == "RES_A" and (c["before"], c["after"]) == (1, 2) for c in d["input_changes"])
    assert any("summary" in c["path"] for c in d["result_changes"])
    assert diff_runs(proj[0], first.metadata["run_id"], first.metadata["run_id"])["result_changes"] == []


def test_cli_runs_show_and_diff(proj, capsys):
    store, _ = proj
    base = ["--project", "PRJ_RUNS", "--home", str(store.paths.home)]
    adapter = ScriptedModelAdapter(default_response=valid_answer("contribution"))
    first = run(proj, "contribution", model_adapter=adapter)
    revise(proj, "RES_A", "Result", {"summary": "Changed."})
    second = run(proj, "contribution", model_adapter=ScriptedModelAdapter(default_response=valid_answer("contribution")))

    assert cli_main(["workflow", "runs", *base, "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["total_count"] == 2 and listed["runs"][0]["run_id"] == second.metadata["run_id"]

    assert cli_main(["workflow", "show", first.metadata["run_id"], *base, "--prompt"]) == 0
    shown = capsys.readouterr().out
    assert "--- system prompt ---" in shown and "Operational Skill Instructions" in shown and "RQ_A" in shown

    assert cli_main(["workflow", "diff", first.metadata["run_id"], second.metadata["run_id"], *base, "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["input_changes"] and d["workflow_a"] == "contribution"
    assert cli_main(["workflow", "show", "RUN_NOPE", *base]) == 1


# --------------------------------------------------------------------------------------------------
# legacy 9.1.1 history
# --------------------------------------------------------------------------------------------------

def _downgrade_to_9_0_2_with_legacy_cache(proj, workflows=("contribution", "methods")):
    """Turn the project into a 9.0.2 database whose workflow_cache holds 9.1.1-style outputs."""
    store, _ = proj
    legacy = {}
    for wf in workflows:
        out = run(proj, wf, reuse_cached=False)
        ctx = build_context(store, wf, research_question_id="RQ_A")
        fp = _compute_input_fingerprint(wf, ctx)
        body = out.to_dict()
        body.update(input_fingerprint=fp, status="SUCCESS" if wf == "methods" else out.status)
        body.pop("status_axes", None)  # the 9.1.1 cache did not have it
        raw = json.dumps(body, indent=2, sort_keys=True).encode("utf-8")  # bytes: no newline translation
        (store.paths.root / "workflow_cache").mkdir(exist_ok=True)
        (store.paths.root / "workflow_cache" / f"{wf}_{fp[:16]}.json").write_bytes(raw)
        legacy[wf] = (fp, raw)
    conn = store.connect()
    try:
        conn.execute("DROP TABLE workflow_runs")
        conn.execute("UPDATE meta SET value='9.0.2' WHERE key='db_schema_version'")
    finally:
        conn.close()
    return legacy


def test_upgrade_imports_the_old_cache_as_legacy_history_and_archives_the_files(proj):
    store, token = proj
    legacy = _downgrade_to_9_0_2_with_legacy_cache(proj)
    result = upgrade_schema(store, token)
    assert (result["status"], result["from_version"], result["to_version"]) == ("UPGRADED", "9.0.2", "9.0.4")
    assert result["legacy_runs_imported"] == 2

    legacy_rows = list_workflow_runs(store, legacy=True)
    assert {r["workflow"] for r in legacy_rows} == {"contribution", "methods"}
    for r in legacy_rows:
        assert r["legacy"] and r["executor"] == "legacy_9_1_1" and r["assessment_status"] == "NOT_ASSESSED"
        assert r["research_question_id"] == "RQ_A" and r["status_reason"] == "legacy_unverified"
        assert r["known_defects"] and "status_lost_on_reuse" in r["known_defects"]
        assert store.artifacts.get_bytes(r["artifact_sha256"]) == legacy[r["workflow"]][1]
    assert "lexical_verdicts" in next(r for r in legacy_rows if r["workflow"] == "contribution")["known_defects"]

    root = store.paths.root
    assert not (root / "workflow_cache").exists()
    archived = sorted(p.name for p in (root / "workflow_cache_legacy").glob("*.json"))
    assert len(archived) == 2
    assert (root / "workflow_cache_legacy" / archived[0]).read_bytes() in {v[1] for v in legacy.values()}
    assert upgrade_schema(store, token)["status"] == "NOOP"


def test_legacy_cache_registered_not_reusable(proj):
    """M1 gate: an old cached output is kept as history but is never served as a result, and the run that
    replaces it links back to it so what was corrected can be shown."""
    store, token = proj
    legacy = _downgrade_to_9_0_2_with_legacy_cache(proj, workflows=("contribution",))
    upgrade_schema(store, token)
    legacy_row = list_workflow_runs(store, legacy=True)[0]
    assert legacy_row["input_fingerprint"] == legacy["contribution"][0]  # same fingerprint as the new run will have

    fresh = run(proj, "contribution", reuse_cached=True)
    assert fresh.status != "REUSED" and fresh.metadata["run_id"] != legacy_row["run_id"]
    new_row = get_workflow_run(store, fresh.metadata["run_id"])
    assert new_row["legacy"] is False and new_row["parent_run_id"] == legacy_row["run_id"]
    assert [r["run_id"] for r in run_history(store, fresh.metadata["run_id"])] == [fresh.metadata["run_id"], legacy_row["run_id"]]
    d = diff_runs(store, legacy_row["run_id"], fresh.metadata["run_id"])
    assert d["legacy_a"] is True and d["legacy_b"] is False
    assert run(proj, "contribution", reuse_cached=True).metadata["reused_from_run_id"] == fresh.metadata["run_id"]
    assert not list_workflow_runs(store, legacy=True)[0]["reused_from_run_id"]


def test_unreadable_cache_files_are_left_in_place(proj):
    store, token = proj
    _downgrade_to_9_0_2_with_legacy_cache(proj, workflows=("methods",))
    bad = store.paths.root / "workflow_cache" / "methods_corrupt.json"
    bad.write_text("{not json", encoding="utf-8")
    assert upgrade_schema(store, token)["legacy_runs_imported"] == 1
    assert bad.exists() and (store.paths.root / "workflow_cache_legacy").is_dir()
