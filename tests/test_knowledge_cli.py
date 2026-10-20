"""The 9.2 knowledge commands, driven through the real CLI entry point (and once through a separate process)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from research_harness.cli import main
from research_harness.knowledge.library import pin
from research_harness.state.store import StateStore
from research_harness.workflows import eca
from tests.library_fixture import FIXTURE, QUOTE, build_library

ROOT = Path(__file__).resolve().parents[1]


def run(capsys, *argv):
    rc = main([str(a) for a in argv])
    out = capsys.readouterr()
    return rc, out.out, out.err


def jrun(capsys, *argv):
    rc, out, err = run(capsys, *argv, "--json")
    assert rc == 0, err
    return json.loads(out)


def test_library_end_to_end_through_the_cli(tmp_path, capsys):
    home = tmp_path / "home"
    created = jrun(capsys, "library", "create", "--home", home)
    token_file = tmp_path / "lib.token"
    token_file.write_text(created["owner_token"], encoding="utf-8")
    base = ["--home", home, "--token-file", token_file]

    src = jrun(capsys, "corpus", "import", "--library", "--file", FIXTURE, "--citation-key", "synth2025", "--title", "Synthetic", *base)
    assert src["object_id"] == "LIB_SRC_SYNTH2025" and src["sections"] >= 6
    ps = jrun(capsys, "passage", "add", "--library", "--source", src["object_id"], "--quote", QUOTE, "--passage-id", "EP_BALANCE", *base)
    assert ps["object_id"] == "LIB_EP_BALANCE"
    rc, out, err = run(capsys, "passage", "add", "--library", "--source", src["object_id"], "--quote", "A sentence that is not in the paper.", *base)
    assert rc == 1 and "QUOTE_NOT_IN_SOURCE" in err
    assert jrun(capsys, "passage", "verify", "--library", "LIB_EP_BALANCE", "--home", home)["source_match"] == "VERIFIED"

    elements = [{"id": "E1", "text": "The estimator needs every firm observed in every year.", "risk_tier": "HIGH",
                 "evidence_basis": [{"kind": "source_passage", "ref": "LIB_EP_BALANCE"}]}]
    item = jrun(capsys, "library", "acquire", "--kind", "verification_rule", "--label", "Balanced panel", "--from", "source",
                "--item-id", "LIB_KI_BAL", "--elements", json.dumps(elements), *base)
    assert item["revision"] == 1
    jrun(capsys, "library", "formalize", "LIB_KI_BAL", "--as", "verification_rule", *base)

    ctx = tmp_path / "assessment.json"
    ctx.write_text(json.dumps({"context": {"subfield": "archival_financial_reporting", "study_design": "predictive_modeling", "estimand": "restatement"},
                               "evidence_basis": [{"kind": "source_passage", "ref": "LIB_EP_BALANCE", "methodological_literature": True}]}), encoding="utf-8")
    val = jrun(capsys, "library", "validate", "LIB_KI_BAL", "--context", ctx, *base)
    assert val["source_fidelity"]["source_fidelity"] == "QUOTE_VERIFIED"
    assert val["validity_assessments"][0]["verdict"] == "PROVISIONAL"

    rc, out, err = run(capsys, "library", "promote", "LIB_KI_BAL", "--scope", json.dumps({"study_design": "predictive_modeling"}),
                       "--exclusions", json.dumps(["unbalanced panels"]), *base)
    assert rc == 1 and "PROMOTION_BLOCKED" in err and "adjudication unresolved" in err

    # a model-proposed adjudication (scripted here), then the researcher's decision through the CLI
    lib = StateStore.open(home, "LIBRARY")
    from tests.library_fixture import supported_as_stated
    res = eca.adjudicate(lib, created["owner_token"], eca.resolve_statement(lib, "LIB_KI_BAL", element="E1"), "LIB_EP_BALANCE", supported_as_stated())
    queue = jrun(capsys, "adjudicate", "queue", "--library", "--home", home)
    assert [(q["adjudication_id"], q["state"]) for q in queue] == [(res["adjudication_id"], "AWAITING_CONFIRMATION")]
    jrun(capsys, "adjudicate", "confirm", res["adjudication_id"], "--library", "--verdict", "SUPPORTED_AS_STATED",
         "--rationale", "The element states the condition the passage states.", *base)
    assert jrun(capsys, "adjudicate", "status", "--library", "--statement", "LIB_KI_BAL", "--element", "E1", "--home", home)["assessment"] == "VERIFIED_WITHIN_SCOPE"
    promoted = jrun(capsys, "library", "promote", "LIB_KI_BAL", "--scope", json.dumps({"study_design": "predictive_modeling"}),
                    "--exclusions", json.dumps(["unbalanced panels"]), *base)
    assert promoted["dimensions"]["source_fidelity"] == "MEANING_VERIFIED"

    rows = jrun(capsys, "library", "match", "--home", home, "--context",
                json.dumps({"subfield": "archival_financial_reporting", "study_design": "predictive_modeling", "estimand": "restatement"}))
    assert rows[0]["item_id"] == "LIB_KI_BAL" and rows[0]["status"] == "candidate_validity_not_established_here"   # PROVISIONAL only
    assert jrun(capsys, "library", "pin", "LIB_KI_BAL", "--home", home)["pin"].startswith("LIB:LIB_KI_BAL@")


def test_use_verify_and_update_check_through_the_cli_and_a_separate_process(tmp_path, capsys):
    home = tmp_path / "home"
    w = build_library(home)
    project, ptoken = StateStore.create(home, "PRJ_CLI", owner_id="researcher")
    token_file = tmp_path / "proj.token"
    token_file.write_text(ptoken, encoding="utf-8")
    used = jrun(capsys, "library", "use", "--project", "PRJ_CLI", "--pin", w["item_pin"], "--home", home, "--token-file", token_file)
    assert used["status"] == "IMPORTED" and used["objects_imported"] == 6
    assert jrun(capsys, "library", "check-updates", "--project", "PRJ_CLI", "--home", home)["status"] == "CURRENT"

    os.rename(home / "LIBRARY", home / "LIBRARY_GONE")
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8"}

    def cli(*argv):
        return subprocess.run([sys.executable, "-m", "research_harness.cli", *map(str, argv)], capture_output=True, text=True, env=env,
                              cwd=str(tmp_path), timeout=120)
    p = cli("library", "verify-closure", "--project", "PRJ_CLI", "--home", home, "--json")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["status"] == "VERIFIED"
    p = cli("library", "check-updates", "--project", "PRJ_CLI", "--home", home, "--json")
    assert p.returncode == 0 and json.loads(p.stdout)["status"] == "LIBRARY_UNAVAILABLE"
    p = cli("library", "match", "--home", home, "--json")
    assert p.returncode == 1 and "LIBRARY_UNAVAILABLE" in p.stderr          # only library commands need the library
    p = cli("domain", "next", "--project", "PRJ_CLI", "--home", home, "--json")
    assert p.returncode == 0                                               # project commands never do


def test_journal_policy_commands(tmp_path, capsys):
    home = tmp_path / "home"
    created = jrun(capsys, "library", "create", "--home", home)
    token_file = tmp_path / "t"
    token_file.write_text(created["owner_token"], encoding="utf-8")
    snap = tmp_path / "policy.txt"
    snap.write_text("Authors must deposit their data and code with the submission.", encoding="utf-8")
    out = jrun(capsys, "journal-policy", "add", "--journal", "J Test", "--type", "official_requirement", "--excerpt", "must deposit their data and code",
               "--snapshot", snap, "--effective-from", "2024-01-01", "--retrieved-at", "2026-02-01", "--source-url", "https://example.org/policy",
               "--home", home, "--token-file", token_file)
    assert out["policy_id"].startswith("LIB_JP_J_TEST")
    rows = jrun(capsys, "journal-policy", "lookup", "--journal", "J Test", "--as-of", "2025-01-01", "--home", home)
    assert rows[0]["binding"] is True
    rc, _, err = run(capsys, "journal-policy", "add", "--journal", "J Test", "--type", "published_precedent", "--excerpt", "not in the text",
                     "--snapshot", snap, "--effective-from", "2024-01-01", "--retrieved-at", "2026-02-01", "--home", home, "--token-file", token_file)
    assert rc == 1 and "EXCERPT_NOT_IN_SNAPSHOT" in err


def test_a_command_needing_a_model_says_so_instead_of_guessing(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("RESEARCH_MODEL", raising=False)
    home = tmp_path / "home"
    store, token = StateStore.create(home, "PRJ_NOMODEL", owner_id="researcher")
    tf = tmp_path / "t"
    tf.write_text(token, encoding="utf-8")
    rc, _, err = run(capsys, "extract", "--project", "PRJ_NOMODEL", "--source", "SRC_X", "--protocol", "RP_METHOD", "--home", home, "--token-file", tf)
    assert rc == 1 and ("model id" in err or "SOURCE_NOT_FOUND" in err or "anthropic" in err.lower())
