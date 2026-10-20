"""The M4 commands (benchmarks, gates, bundles), driven through the real CLI entry point."""
from __future__ import annotations

import json

from research_harness.cli import main
from research_harness.knowledge.library import pin
from research_harness.state.store import StateStore
from tests import m3_fixture as fx
from tests.library_fixture import build_library
from tests.test_gate_k import filled_form


def jrun(capsys, *argv):
    rc = main([str(a) for a in argv] + ["--json"])
    out = capsys.readouterr()
    assert rc == 0, out.err
    return json.loads(out.out)


def test_benchmark_and_gate_commands(tmp_path, capsys):
    assert jrun(capsys, "benchmark", "logic")["all_passed"] is True
    gl = jrun(capsys, "benchmark", "gate-l-synthetic")
    assert gl["synthetic_machinery_ok"] is True and gl["real_gate_passed"] is False

    home = tmp_path / "home"
    store, token = StateStore.create(home, "PRJ_GK_CLI", owner_id="researcher")
    tf = tmp_path / "t"
    tf.write_text(token, encoding="utf-8")
    fx.import_corpus(store, token, sources=(fx.PAPER_A,))
    form = tmp_path / "form.json"
    out = jrun(capsys, "gate-k", "form", "--sources", fx.PAPER_A, "--package", "PKG_PANEL", "--out", form)
    assert len(out["elements"]) == 7 and json.loads(form.read_text(encoding="utf-8"))["elements"][0]["reference"] == ""
    filled = tmp_path / "filled.json"
    filled.write_text(json.dumps(filled_form()), encoding="utf-8")
    base = ["--project", "PRJ_GK_CLI", "--home", home]
    assert jrun(capsys, "gate-k", "annotate", "--file", filled, "--token-file", tf, *base)["annotation_id"] == "REFANN_PILOT"
    assert jrun(capsys, "gate-k", "blindness", "REFANN_PILOT", *base)["blind"] is True
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({**filled_form(), "annotator": ""}), encoding="utf-8")
    rc = main(["gate-k", "annotate", "--file", str(bad), "--id", "REFANN_BAD", "--token-file", str(tf), "--project", "PRJ_GK_CLI", "--home", str(home)])
    assert rc == 1 and "ANNOTATION_INCOMPLETE" in capsys.readouterr().err

    for gate in ("K", "L"):
        assert main(["gate-validate", gate, str(tmp_path / "nothing")]) == 1
        capsys.readouterr()


def test_library_bundle_commands(tmp_path, capsys):
    home = tmp_path / "home"
    w = build_library(home)
    out = tmp_path / "bundle"
    exp = jrun(capsys, "library", "export", "--pin", w["item_pin"], "--out", out, "--home", home)
    assert exp["objects"] == 6
    other = tmp_path / "other"
    from research_harness.knowledge.library import create_library
    lib2, t2 = create_library(other)
    tf = tmp_path / "t2"
    tf.write_text(t2, encoding="utf-8")
    res = jrun(capsys, "library", "import", "--bundle", out, "--home", other, "--token-file", tf)
    assert res["status"] == "IMPORTED" and res["objects_imported"] == 6
    notes = jrun(capsys, "library", "release-notes", "--pin", w["item_pin"], "--home", other)
    assert notes["item"] == "LIB_KI_PANEL" and notes["dimensions"]["methodological_validity"] == "not assessed in any context" and "markdown" in notes
    rc = main(["library", "import", "--bundle", str(tmp_path / "missing"), "--home", str(other), "--token-file", str(tf)])
    assert rc == 1 and "BUNDLE_UNREADABLE" in capsys.readouterr().err
