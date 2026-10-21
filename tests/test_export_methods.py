"""WP2 (M5): the readable export of the Methods workflow, its manifest, reproducibility and safety rules."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from research_harness.cli import main
from research_harness.core.errors import HarnessError
from research_harness.export import render as R
from research_harness.export.contracts import CLASSES, MANIFEST_NAME
from research_harness.export.manifest import Export, verify_export, write_export
from research_harness.export.methods import FILES, build_methods_export
from research_harness.export.safety import check_out_dir, secret_findings
from research_harness.knowledge import corpus, literature, playbooks
from research_harness.state.store import StateStore
from research_harness.workflows import eca
from research_harness.workflows.adapters import ScriptedModelAdapter
from tests import m3_fixture as fx

CTX = {"domain": "accounting", "subfield": "archival_financial_reporting", "study_design": "predictive_modeling"}


def adjudicator() -> ScriptedModelAdapter:
    return ScriptedModelAdapter(model_id="scripted-adjudicator", default_response={
        "passage_proposition": "As quoted.", "equivalence": "EQUIVALENT", "dropped_conditions": [], "cue_handling": [],
        "verdict": "SUPPORTED_AS_STATED", "required_qualification": [], "rationale": "the quote says it"})


@pytest.fixture
def world(tmp_path):
    home = tmp_path / "home"
    store, token = StateStore.create(home, "PRJ_EXP", owner_id="researcher")
    fx.import_corpus(store, token, sources=(fx.PAPER_A,))
    review = literature.run_literature(store, token, "method", [fx.PAPER_A], fx.extractor())
    playbooks.draft_playbook_from_methods(store, token, review, playbook_id="PB_X", label="Export test playbook",
                                          scope={"study_design": "predictive_modeling"})
    return {"home": home, "store": store, "token": token, "tmp": tmp_path}


def confirm_first(w, n=1):
    store, token = w["store"], w["token"]
    for e in store.get_object("PB_X")["content"]["elements"][:n]:
        res = eca.adjudicate(store, token, eca.resolve_statement(store, "PB_X", element=e["id"]), e["evidence_basis"][0]["ref"], adjudicator())
        eca.confirm(store, token, res["adjudication_id"], verdict="SUPPORTED_AS_STATED", rationale=f"{e['id']} reads its passage correctly")


def read_dir(d: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(d.iterdir())}


# ------------------------------------------------------------------------------------------------ content

def test_export_files_manifest_and_provenance_classes(world):
    w = world
    confirm_first(w, 1)
    exp = build_methods_export(w["store"], "PB_X")
    out = write_export(w["store"], exp, w["tmp"] / "out")
    files = read_dir(Path(out["out"]))
    assert set(files) == set(FILES) | {MANIFEST_NAME, "export_info.json"}
    man = json.loads(files[MANIFEST_NAME])
    assert man["kind"] == "methods" and man["scope"] == {"playbook": "PB_X", "plan": None} and len(man["closure_digest"]) == 64
    assert man["corpus_snapshot_id"] == corpus.corpus_snapshot_id(w["store"])
    assert {f["path"] for f in man["files"]} == set(FILES)
    assert all(set(c) <= set(CLASSES) for c in man["provenance_counts"].values())
    pbk = files["playbook.md"].decode("utf-8")
    assert pbk.startswith("<!-- research-suite export") and "PB_X@1" in pbk
    # one element has a researcher confirmation, the others are the model's statements
    counts = man["provenance_counts"]["playbook.md"]
    assert counts.get("researcher_confirmed", 0) >= 1 and counts.get("model_synthesised", 0) >= 1
    assert "confirmed: SUPPORTED_AS_STATED" in pbk
    ext = files["extraction.md"].decode("utf-8")
    assert "RP_METHOD" in ext and "quote (verbatim)" in ext and "NOT_AVAILABLE" not in ext
    sheet = files["adjudication_sheet.md"].decode("utf-8")
    assert "confirmation" in sheet and "Awaiting the researcher's decision" in sheet
    assert "calls" in files["cost_report.md"].decode("utf-8")
    # no full text: the stored source is never reproduced
    text = corpus.source_text(w["store"], fx.PAPER_A)[1]
    assert all(text[:600] not in b.decode("utf-8") for b in files.values())


def test_missing_upstream_is_not_available_not_absent(world):
    exp = build_methods_export(world["store"], "PB_X")      # no adjudication, no plan
    files = {n: b.decode("utf-8") for n, b in exp.files.items()}
    assert set(files) == set(FILES)
    assert "NOT_AVAILABLE: no analysis plan given" in files["fidelity_report.md"]
    assert "NOT_AVAILABLE: no adjudication recorded" in files["adjudication_sheet.md"]
    assert {n["file"] for n in exp.manifest()["not_available"]} == {"fidelity_report.md", "adjudication_sheet.md"}
    doc = R.Doc("t", "<!-- h -->").unavailable("nothing upstream")
    assert doc.text().decode("utf-8").splitlines()[2].startswith("**NOT_AVAILABLE: nothing upstream")


def test_plan_adds_the_fidelity_report(world):
    w = world
    store, token = w["store"], w["token"]
    els = {e["category"]: e["id"] for e in store.get_object("PB_X")["content"]["elements"]}
    steps = [{"id": f"s_{cat}", "element": el, "fidelity": "omitted", "omit_reason": "not_applicable"} for cat, el in els.items()]
    plan = playbooks.draft_analysis_plan(store, "PB_X", plan_id="PLAN_X", label="plan", context={**CTX, "estimand": "restatement"}, steps=steps)
    assert playbooks.submit_operations(store, token, plan["operations"], proposal_id="PROP_X")["decision"] == "ACCEPTED"
    exp = build_methods_export(store, "PB_X", "PLAN_X")
    fid = exp.files["fidelity_report.md"].decode("utf-8")
    assert "NOT_AVAILABLE" not in fid and "## Omitted" in fid and "s_" in fid
    with pytest.raises(Exception, match="PLAN_NOT_FOR_PLAYBOOK|PLAN_NOT_FOUND"):
        build_methods_export(store, "PB_X", "PB_X")


def test_a_quote_that_no_longer_verifies_is_marked(world, monkeypatch):
    real = corpus.verify_passage
    monkeypatch.setattr(corpus, "verify_passage", lambda store, pid: {"object_id": pid, "source_match": "FAILED", "reason": "span changed"})
    exp = build_methods_export(world["store"], "PB_X")
    assert "QUOTE_NO_LONGER_VERIFIES" in exp.files["playbook.md"].decode("utf-8")
    assert "QUOTE_NO_LONGER_VERIFIES" in exp.files["extraction.md"].decode("utf-8")
    monkeypatch.setattr(corpus, "verify_passage", real)
    assert "QUOTE_NO_LONGER_VERIFIES" not in build_methods_export(world["store"], "PB_X").files["playbook.md"].decode("utf-8")


def test_quotes_and_cells_are_bounded_and_safe():
    long = "word " * 200
    q = R.quote(long)
    assert len(q) < 330 and q.endswith(f"({len(long.strip())} chars)")
    assert "\n" not in R.cell("a|b\nc") and R.cell("a|b") == "a\\|b" and R.cell(None) == "-"


# ------------------------------------------------------------------------------------------------ reproducibility and verify

def test_export_is_reproducible_and_verify_detects_change(world):
    w = world
    store = w["store"]
    confirm_first(w, 1)
    a = write_export(store, build_methods_export(store, "PB_X"), w["tmp"] / "a")
    b = write_export(store, build_methods_export(store, "PB_X"), w["tmp"] / "b")
    fa, fb = read_dir(Path(a["out"])), read_dir(Path(b["out"]))
    assert {k: v for k, v in fa.items() if k != "export_info.json"} == {k: v for k, v in fb.items() if k != "export_info.json"}
    assert a["closure_digest"] == b["closure_digest"]
    rebuild = lambda scope: build_methods_export(store, scope["playbook"], scope.get("plan"))   # noqa: E731
    assert verify_export(store, a["out"], rebuild)["status"] == "VERIFIED"

    # an unrelated write does not touch this closure
    with store.write_tx(token=w["token"]) as tx:
        tx.add_revision(object_id="RQ_OTHER", object_type="ResearchQuestion", origin={}, edges=[], content={"text": "an unrelated question"})
    assert build_methods_export(store, "PB_X").closure_digest == a["closure_digest"]
    assert verify_export(store, a["out"], rebuild)["status"] == "VERIFIED"

    # a new confirmation on this playbook does
    confirm_first(w, 2)
    changed = verify_export(store, a["out"], rebuild)
    assert changed["status"] == "STATE_CHANGED" and changed["closure_digest_recorded"] != changed["closure_digest_now"]

    # a file edited by hand is reported
    (Path(b["out"]) / "playbook.md").write_text("edited", encoding="utf-8")
    assert verify_export(store, b["out"], rebuild)["status"] == "FILES_MODIFIED"


def test_default_output_is_outside_every_store(world):
    store = world["store"]
    exp = build_methods_export(store, "PB_X")
    out = write_export(store, exp)
    p = Path(out["out"])
    assert p.parent.parent.parent.name == "exports" and p.name == exp.closure_digest[:12]
    assert not any((q / "state.sqlite3").exists() for q in [p, *p.parents])
    with pytest.raises(HarnessError, match="EXPORT_DIR_NOT_EMPTY"):
        write_export(store, exp)


# ------------------------------------------------------------------------------------------------ safety

def test_secrets_are_never_written(world, monkeypatch):
    secret = "sk-" + "a1b2c3d4e5" * 4
    monkeypatch.setenv("RESEARCH_API_KEY", "plain-looking-key-value-123")
    assert secret_findings(f"the key is {secret}") == ["api_key_token"]
    assert "value_of_RESEARCH_API_KEY" in secret_findings("plain-looking-key-value-123 appears here")
    assert secret_findings("RESEARCH_API_KEY = abcdefghijkl") == ["key_assignment"]
    exp = Export(kind="methods", scope={"playbook": "P"}, files={"x.md": f"# leak {secret}\n".encode("utf-8")}, closure=[], corpus_snapshot_id="s")
    out = world["tmp"] / "leak"
    with pytest.raises(HarnessError) as e:
        write_export(world["store"], exp, out)
    assert e.value.code == "EXPORT_SECRET_DETECTED" and secret not in str(e.value) and not out.exists()


def test_unsafe_output_paths_are_refused(world, tmp_path):
    store = world["store"]
    for bad in (store.paths.root / "exports_here", store.paths.artifacts / "sub", world["home"] / "PRJ_EXP" / "anywhere"):
        with pytest.raises(HarnessError, match="EXPORT_PATH_UNSAFE"):
            check_out_dir(bad)
    git_dir = tmp_path / "repo" / ".git"
    git_dir.mkdir(parents=True)
    with pytest.raises(HarnessError, match="EXPORT_PATH_UNSAFE"):
        check_out_dir(git_dir / "out")
    full = tmp_path / "full"
    full.mkdir()
    (full / "x").write_text("x", encoding="utf-8")
    with pytest.raises(HarnessError, match="EXPORT_DIR_NOT_EMPTY"):
        check_out_dir(full)
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available here")
    with pytest.raises(HarnessError, match="EXPORT_PATH_UNSAFE"):
        check_out_dir(link)
    inside_store = tmp_path / "inside_store_link"
    inside_store.symlink_to(store.paths.root, target_is_directory=True)
    with pytest.raises(HarnessError, match="EXPORT_PATH_UNSAFE"):
        check_out_dir(inside_store / "out")


@pytest.mark.skipif(shutil.which("git") is None, reason="git is needed for the repository rule")
def test_repository_tree_is_refused_unless_ignored_or_explicit(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("/sample/\n", encoding="utf-8")
    assert check_out_dir(repo / "sample" / "results" / "export") == (repo / "sample" / "results" / "export").resolve()
    with pytest.raises(HarnessError, match="EXPORT_PATH_UNSAFE"):
        check_out_dir(repo / "docs" / "export")
    assert check_out_dir(repo / "docs" / "export", into_repo=True).name == "export"
    assert check_out_dir(tmp_path / "elsewhere" / "export").name == "export"


# ------------------------------------------------------------------------------------------------ CLI

def test_export_cli_round_trip(world, capsys):
    w = world
    confirm_first(w, 1)
    base = ["--project", "PRJ_EXP", "--home", str(w["home"])]
    out = w["tmp"] / "cli_out"
    assert main(["export", "methods", "--playbook", "PB_X", "--out", str(out), *base, "--json"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["out"] == str(out.resolve()) and set(res["files"]) == set(FILES) | {MANIFEST_NAME}
    assert main(["export", "verify", str(out), *base]) == 0
    assert capsys.readouterr().out.strip() == "VERIFIED"
    (out / "cost_report.md").write_text("x", encoding="utf-8")
    assert main(["export", "verify", str(out), *base]) == 1
    assert capsys.readouterr().out.startswith("FILES_MODIFIED")
    assert main(["export", "methods", "--playbook", "PB_X", "--out", str(out), *base]) == 1       # not empty
    assert "EXPORT_DIR_NOT_EMPTY" in capsys.readouterr().err
    assert main(["export", "methods", "--playbook", "NOPE", "--out", str(w["tmp"] / "o2"), *base]) == 1
