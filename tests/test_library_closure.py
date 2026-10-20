"""Phase 4.2 (plan v3): library pins, dependency closure, atomic import, and a project that outlives its library."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from research_harness.knowledge import closure, corpus
from research_harness.knowledge.common import KnowledgeError
from research_harness.knowledge.library import (
    add_object, library_exists, make_pin, open_library, parse_pin, pin, resolve_pin)
from research_harness.knowledge.replication import reproduce_package
from research_harness.protocols.snapshot import export_snapshot
from research_harness.state.store import StateStore, compute_state_digest
from tests.library_fixture import FIXTURE, PANEL_CSV, build_library

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def world(tmp_path):
    home = tmp_path / "home"
    built = build_library(home)
    project, ptoken = StateStore.create(home, "PRJ_USE", owner_id="researcher")
    return {**built, "project": project, "ptoken": ptoken, "home": home}


def state_digest(store):
    with store.read() as conn:
        return compute_state_digest(conn)


def test_pins_name_exact_content_and_a_changed_item_does_not_resolve(world):
    lib, token = world["lib"], world["token"]
    oid, rev, dig = parse_pin(world["item_pin"])
    assert (oid, rev) == ("LIB_KI_PANEL", 1) and len(dig) == 16 and resolve_pin(lib, world["item_pin"])["object_id"] == oid
    with pytest.raises(KnowledgeError, match="PIN_DIGEST_MISMATCH"):
        resolve_pin(lib, make_pin(oid, rev, "0" * 64))
    with pytest.raises(KnowledgeError, match="PIN_MALFORMED"):
        parse_pin("LIB:not-an-id@1#abc")
    with pytest.raises(KnowledgeError, match="LIBRARY_ID_PREFIX"):
        add_object(lib, token, "NOT_LIBRARY_ID", "KnowledgeItem", {"label": "x", "kind": "checklist"})
    old = world["item_pin"]
    add_object(lib, token, "LIB_KI_PANEL", "KnowledgeItem", {**lib.get_object("LIB_KI_PANEL")["content"], "label": "Revised"},
               edges=[("depends_on", "LIB_EP_BALANCE", 1)])
    assert resolve_pin(lib, old)["content"]["label"] == "Balanced-panel requirement"   # revision 1 is still exactly that
    assert pin(lib, "LIB_KI_PANEL") != old


def test_closure_collects_edges_pins_adjudications_history_and_artifacts(world):
    lib = world["lib"]
    m = closure.dependency_closure(lib, world["item_pin"])
    members = {(e["object_id"], e["revision"]) for e in m.entries}
    assert members == {("LIB_KI_PANEL", 1), ("LIB_EP_BALANCE", 1), ("LIB_KI_PKG", 1), ("LIB_DW_ACC_FRAUD_DETECTION", 1),
                       ("LIB_SRC_SYNTH2025", 1), (world["eca_id"], 1)}
    assert world["eca_id"] == "LIB_ADJ_000001"   # library objects all carry the LIB_ prefix
    shas = {a["sha256"] for a in m.artifacts}
    src = lib.get_object("LIB_SRC_SYNTH2025")["content"]
    assert {src["artifact_sha256"], src["sections_sha256"], world["pkg_sha"]} <= shas   # full text, section sidecar, data
    assert m.closure_digest == closure.dependency_closure(lib, world["item_pin"]).closure_digest
    assert closure.ClosureManifest.from_dict(m.to_dict()).closure_digest == m.closure_digest
    with pytest.raises(KnowledgeError, match="MANIFEST_DIGEST_MISMATCH"):
        closure.ClosureManifest.from_dict({**m.to_dict(), "closure_digest": "0" * 64})


def test_use_in_project_imports_the_whole_closure_with_identical_digests(world):
    lib, project, ptoken = world["lib"], world["project"], world["ptoken"]
    res = closure.use_in_project(project, ptoken, lib, world["item_pin"])
    assert res["status"] == "IMPORTED" and res["objects_imported"] == 6 and res["artifacts"] == 3
    for oid in ("LIB_KI_PANEL", "LIB_EP_BALANCE", "LIB_KI_PKG", "LIB_DW_ACC_FRAUD_DETECTION", "LIB_SRC_SYNTH2025", "LIB_ADJ_000001"):
        a, b = lib.get_object(oid, 1), project.get_object(oid, 1)
        assert (a["content_digest"], a["revision_digest"]) == (b["content_digest"], b["revision_digest"]), oid
        assert b["origin"]["kind"] == "LIBRARY_IMPORTED" and b["origin"]["pin"] == world["item_pin"]
    with project.read() as conn:  # internal edges were recreated between the imported copies
        assert {(e["relation"], e["target_id"]) for e in project.edges_from(conn, "LIB_KI_PANEL", 1)} == {
            ("depends_on", "LIB_EP_BALANCE"), ("depends_on", "LIB_KI_PKG"), ("depends_on", "LIB_DW_ACC_FRAUD_DETECTION"), ("derived_from", "LIB_SRC_SYNTH2025")}
    man = project.get_object(res["manifest_id"])
    assert man["content"]["kind"] == "library_import_manifest" and man["content"]["pins"] == [world["item_pin"]]
    assert closure.verify_closure(project)["status"] == "VERIFIED"
    again = closure.use_in_project(project, ptoken, lib, world["item_pin"])
    assert again["status"] == "ALREADY_IMPORTED"
    # the imported full text is readable in the project and its passage still verifies there
    assert corpus.verify_passage(project, "LIB_EP_BALANCE")["source_match"] == "VERIFIED"


def test_closure_import_atomic_on_missing_artifact(world):
    lib, project, ptoken = world["lib"], world["project"], world["ptoken"]
    before = state_digest(project)
    blob = lib.artifacts.path_for(world["pkg_sha"])
    saved = blob.read_bytes()
    blob.unlink()
    with pytest.raises(KnowledgeError, match="CLOSURE_ARTIFACT_MISSING"):
        closure.use_in_project(project, ptoken, lib, world["item_pin"])
    blob.write_bytes(b"corrupted")
    with pytest.raises(KnowledgeError, match="CLOSURE_ARTIFACT_CORRUPT"):
        closure.use_in_project(project, ptoken, lib, world["item_pin"])
    blob.write_bytes(saved)
    assert state_digest(project) == before
    with project.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM object_heads").fetchone()[0] == 0

    # a failure half-way through the write also leaves nothing behind
    with project.write_tx(token=ptoken) as tx:
        tx.add_revision(object_id="LIB_KI_PKG", object_type="KnowledgeItem", origin={}, edges=[],
                        content={"label": "A different thing", "kind": "checklist"})
    after_seed = state_digest(project)
    with pytest.raises(KnowledgeError, match="IMPORT_CONFLICT"):
        closure.use_in_project(project, ptoken, lib, world["item_pin"])
    assert state_digest(project) == after_seed
    with project.read() as conn:
        assert [r[0] for r in conn.execute("SELECT object_id FROM object_heads")] == ["LIB_KI_PKG"]


def test_a_library_item_that_is_no_longer_active_is_not_offered(world):
    lib, token = world["lib"], world["token"]
    item = lib.get_object("LIB_KI_PANEL")
    add_object(lib, token, "LIB_KI_PANEL", "KnowledgeItem", {**item["content"], "lifecycle": "SUPERSEDED"},
               edges=[("depends_on", "LIB_EP_BALANCE", 1), ("depends_on", "LIB_KI_PKG", 1), ("depends_on", "LIB_DW_ACC_FRAUD_DETECTION", 1),
                      ("derived_from", "LIB_SRC_SYNTH2025", 1)])
    with pytest.raises(KnowledgeError, match="ITEM_NOT_ACTIVE"):
        closure.use_in_project(world["project"], world["ptoken"], lib, pin(lib, "LIB_KI_PANEL"))


def test_verify_closure_detects_tampering_without_the_library(world):
    project, ptoken = world["project"], world["ptoken"]
    closure.use_in_project(project, ptoken, world["lib"], world["item_pin"])
    project.artifacts.path_for(world["pkg_sha"]).write_bytes(b"tampered")
    out = closure.verify_closure(project)
    assert out["status"] == "FAILED" and [p["problem"] for p in out["problems"]] == ["ARTIFACT_CORRUPT"]


def test_check_updates_library_unavailable(world, tmp_path):
    project, ptoken, home = world["project"], world["ptoken"], world["home"]
    closure.use_in_project(project, ptoken, world["lib"], world["item_pin"])
    assert closure.check_updates(project, home)["status"] == "CURRENT"
    add_object(world["lib"], world["token"], "LIB_KI_PANEL", "KnowledgeItem",
               {**world["lib"].get_object("LIB_KI_PANEL")["content"], "label": "Revised"}, edges=[("depends_on", "LIB_EP_BALANCE", 1)])
    notice = closure.check_updates(project, home)
    assert notice["status"] == "UPDATES_AVAILABLE" and notice["notices"][0]["notice"] == "NEWER_REVISION"
    os.rename(home / "LIBRARY", home / "LIBRARY_ELSEWHERE")
    gone = closure.check_updates(project, home)
    assert gone["status"] == "LIBRARY_UNAVAILABLE" and gone["notices"] == []     # a status, not an error
    assert not library_exists(home)
    with pytest.raises(KnowledgeError, match="LIBRARY_UNAVAILABLE"):
        open_library(home)


def test_a_project_cannot_create_library_ids_through_a_proposal(world):
    from research_harness.core.auth import authenticate
    from research_harness.proposals.gateway import import_proposal
    from tests.conftest import make_proposal
    project, ptoken = world["project"], world["ptoken"]
    with project.read() as conn:
        actor = authenticate(conn, ptoken)
    snap = export_snapshot(project, actor, scope="full", token=ptoken)
    ops = [{"operation": "propose_object", "proposed_id": "LIB_RQ_FAKE", "object_type": "ResearchQuestion",
            "content": {"label": "x", "text": "A question that pretends to come from the library."}, "relations": []}]
    receipt = import_proposal(project, json.dumps(make_proposal(project.project_id, snap, "PROP_LIB1", ops)).encode(), ptoken)
    assert receipt["decision"] == "REJECTED" and receipt["rule"] == "LIBRARY_ID_RESERVED"


_OFFLINE_SCRIPT = r"""
import json, sys
from pathlib import Path
home, project_id, expected_manifest = sys.argv[1], sys.argv[2], sys.argv[3]
touched = []
import research_harness.knowledge.library as L
from research_harness.state import store as S
_real_open = S.StateStore.open.__func__
def guarded_open(cls, h, pid, clock=None):
    touched.append(pid)
    if pid == "LIBRARY":
        raise AssertionError("the library was opened")
    return _real_open(cls, h, pid) if clock is None else _real_open(cls, h, pid, clock)
S.StateStore.open = classmethod(guarded_open)
from research_harness.knowledge import closure
from research_harness.knowledge.replication import reproduce_package
from research_harness.workflows.domain_engine import DomainWorkflowSpec, evaluate, facts_from_store, next_steps
project = S.StateStore.open(home, project_id)
verified = closure.verify_closure(project)
spec = DomainWorkflowSpec.from_dict(project.get_object("LIB_DW_ACC_FRAUD_DETECTION")["content"]["spec"])
facts = facts_from_store(project, study_design="predictive_modeling")
steps = next_steps(spec, facts)
evals = [e.to_dict() for e in evaluate(spec, facts)]
repro = reproduce_package(project, "LIB_KI_PKG")
from research_harness.workflows.reading_protocols import extraction_status
from research_harness.knowledge.corpus import verify_passage, source_text
print(json.dumps({"verified": verified["status"], "manifests": verified["manifests"], "spec": spec.pin, "next": steps,
                  "n_steps": len(evals), "repro": repro["computational_reproducibility"], "outputs": repro["outputs"],
                  "passage": verify_passage(project, "LIB_EP_BALANCE")["source_match"],
                  "text_chars": len(source_text(project, "LIB_SRC_SYNTH2025")[1]), "opened": touched,
                  "updates": closure.check_updates(project, home)["status"]}))
"""


def test_project_reproduces_with_library_unavailable(world, tmp_path):
    project, ptoken, home = world["project"], world["ptoken"], world["home"]
    res = closure.use_in_project(project, ptoken, world["lib"], world["item_pin"])
    # (3) the library directory goes away; a new process opens the project
    os.rename(home / "LIBRARY", home / "LIBRARY_RENAMED_AWAY")
    script = tmp_path / "offline_check.py"
    script.write_text(_OFFLINE_SCRIPT, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run([sys.executable, str(script), str(home), "PRJ_USE", res["manifest_id"]], capture_output=True, text=True,
                          env=env, cwd=str(tmp_path), timeout=120)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    # (4) closure verifies, (5) domain-engine steps evaluate, the replication data reproduce within tolerance
    assert out["verified"] == "VERIFIED" and out["manifests"] == [res["manifest_id"]]
    assert out["spec"].startswith("LIB_DW_ACC_FRAUD_DETECTION") and out["n_steps"] >= 3 and out["next"]
    assert out["repro"] == "REPRODUCED_WITHIN_TOLERANCE" and {o["id"]: o["status"] for o in out["outputs"]} == {
        "n_rows": "REPRODUCED", "positive_rate": "REPRODUCED"}
    assert out["passage"] == "VERIFIED" and out["text_chars"] == len(FIXTURE.read_text(encoding="utf-8"))
    # (6) the library was never accessed, and the only thing that noticed it was gone is the update check
    assert out["opened"] == ["PRJ_USE"] and out["updates"] == "LIBRARY_UNAVAILABLE"
    assert not (home / "LIBRARY").exists()


def test_reproduction_reports_a_failed_output_and_a_missing_asset(world):
    project, ptoken, lib = world["project"], world["ptoken"], world["lib"]
    closure.use_in_project(project, ptoken, lib, world["item_pin"])
    assert reproduce_package(project, "LIB_KI_PKG")["computational_reproducibility"] == "REPRODUCED_WITHIN_TOLERANCE"
    body = lib.get_object("LIB_KI_PKG")["content"]["body"]
    bad = json.loads(json.dumps(body))
    bad["expected_outputs"][1]["expected"] = 0.5
    with project.write_tx(token=ptoken) as tx:
        tx.add_revision(object_id="PKG_BAD", object_type="KnowledgeItem", origin={}, edges=[],
                        content={"label": "bad", "kind": "replication_package", "body": bad})
        tx.add_revision(object_id="PKG_EMPTY", object_type="KnowledgeItem", origin={}, edges=[],
                        content={"label": "empty", "kind": "replication_package", "body": {"files": [], "expected_outputs": []}})
        tx.add_revision(object_id="PKG_NODATA", object_type="KnowledgeItem", origin={}, edges=[],
                        content={"label": "nodata", "kind": "replication_package",
                                 "body": {"files": [{"name": "x.csv", "sha256": "ab" * 32}],
                                          "expected_outputs": [{"id": "n", "file": "x.csv", "stat": "count", "expected": 1}]}})
    failed = reproduce_package(project, "PKG_BAD")
    assert failed["computational_reproducibility"] == "FAILED_TO_REPRODUCE"
    assert [o["status"] for o in failed["outputs"]] == ["REPRODUCED", "FAILED"]
    assert reproduce_package(project, "PKG_EMPTY")["computational_reproducibility"] == "NO_ASSETS"
    assert reproduce_package(project, "PKG_NODATA")["computational_reproducibility"] == "RAN"   # nothing could be compared
    with pytest.raises(KnowledgeError, match="NOT_A_REPLICATION_PACKAGE"):
        reproduce_package(project, "LIB_KI_PANEL")


def test_the_library_data_file_is_the_expected_one(world):
    assert world["lib"].artifacts.get_bytes(world["pkg_sha"]).decode("utf-8") == PANEL_CSV
