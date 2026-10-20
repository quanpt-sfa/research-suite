"""The M3 commands that do not call a model, driven through the real CLI entry point."""
from __future__ import annotations

import json

import pytest

from research_harness.cli import main
from research_harness.knowledge import literature, playbooks
from research_harness.state.store import StateStore
from tests import m3_fixture as fx
from tests.library_fixture import PANEL_CSV


def jrun(capsys, *argv):
    rc = main([str(a) for a in argv] + ["--json"])
    out = capsys.readouterr()
    assert rc == 0, out.err
    return json.loads(out.out)


@pytest.fixture
def cli_world(tmp_path):
    home = tmp_path / "home"
    store, token = StateStore.create(home, "PRJ_M3CLI", owner_id="researcher")
    tf = tmp_path / "token"
    tf.write_text(token, encoding="utf-8")
    fx.import_corpus(store, token, sources=(fx.PAPER_A,))
    return {"home": home, "store": store, "token": token, "tf": tf, "tmp": tmp_path}


def test_replication_plan_and_coverage_commands(cli_world, capsys):
    w = cli_world
    base = ["--project", "PRJ_M3CLI", "--home", w["home"], "--token-file", w["tf"]]
    pkg = w["tmp"] / "pkg"
    pkg.mkdir()
    (pkg / "panel.csv").write_bytes(PANEL_CSV.encode("utf-8"))
    expected = json.dumps([{"id": "n_rows", "file": "panel.csv", "stat": "count", "expected": 8, "tolerance": 0}])
    added = jrun(capsys, "replication", "add", "--id", "PKG_C", "--label", "package", "--directory", pkg, "--files", "panel.csv", "--expected", expected,
                 "--entitlement", json.dumps({"status": "confirmed", "license": "synthetic", "basis": "generated for the test"}), *base)
    assert added["files"] == 1 and added["entitlement"]["established"] is True
    rep = jrun(capsys, "replication", "reproduce", "PKG_C", *base)
    assert rep["computational_reproducibility"] == "REPRODUCED_WITHIN_TOLERANCE"

    # a playbook (drafted through the API: the CLI route needs a live model), selected and planned through the CLI
    review = literature.run_literature(w["store"], w["token"], "method", [fx.PAPER_A], fx.extractor())
    playbooks.draft_playbook_from_methods(w["store"], w["token"], review, playbook_id="PB_C", label="pb", scope={"study_design": "predictive_modeling"})
    ctx = {"study_design": "predictive_modeling", "estimand": "restatement"}
    rows = jrun(capsys, "playbook", "select", "--context", json.dumps(ctx), "--project", "PRJ_M3CLI", "--home", w["home"])
    assert rows[0]["item_id"] == "PB_C" and rows[0]["status"] == "candidate_validity_not_established_here"
    els = {e["category"]: e["id"] for e in w["store"].get_object("PB_C")["content"]["elements"]}
    steps = [{"id": f"s_{cat}", "element": el, "fidelity": "omitted", "omit_reason": "not_applicable"} for cat, el in els.items()]
    plan = jrun(capsys, "plan", "draft", "--playbook", "PB_C", "--id", "PLAN_C", "--label", "plan", "--context", json.dumps(ctx), "--steps", json.dumps(steps), *base)
    assert plan["fidelity_summary"]["omitted"] and w["store"].get_object("PLAN_C") is None     # a draft is only a proposal
    imported = jrun(capsys, "plan", "draft", "--playbook", "PB_C", "--id", "PLAN_C", "--label", "plan", "--context", json.dumps(ctx), "--steps", json.dumps(steps),
                    "--import", *base)
    assert imported["imported"]["decision"] == "ACCEPTED" and w["store"].get_object("PLAN_C")["object_type"] == "AnalysisPlan"
    fid = jrun(capsys, "plan", "fidelity", "PLAN_C", "--project", "PRJ_M3CLI", "--home", w["home"])
    assert len(fid["omitted"]) == len(els)

    cov = jrun(capsys, "adjudicate", "coverage", "--project", "PRJ_M3CLI", "--home", w["home"])
    assert cov["high_total"] >= len(els) and cov["complete"] is False
    rc = main(["plan", "run", "PLAN_C", "--workdir", str(w["tmp"]), "--project", "PRJ_M3CLI", "--home", str(w["home"]), "--token-file", str(w["tf"])])
    assert rc == 0 and "NOT_RUN" in capsys.readouterr().out


def test_commands_that_call_a_model_say_so(cli_world, capsys, monkeypatch):
    monkeypatch.delenv("RESEARCH_MODEL", raising=False)
    w = cli_world
    rc = main(["intro", "analyze", "--source", fx.PAPER_A, "--project", "PRJ_M3CLI", "--home", str(w["home"]), "--token-file", str(w["tf"])])
    assert rc == 1 and "model" in capsys.readouterr().err.lower()
