"""The milestone report must never mark a milestone passed on xfail, skip or missing tests."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "acceptance_report.py"
spec = importlib.util.spec_from_file_location("acceptance_report", SCRIPT)
ar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ar)

JUNIT = """<?xml version="1.0"?><testsuites><testsuite>
<testcase classname="tests.test_a" name="test_ok"/>
<testcase classname="tests.test_a" name="test_param[x]"/>
<testcase classname="tests.test_a" name="test_param[y]"><failure message="boom"/></testcase>
<testcase classname="tests.test_a" name="test_skip"><skipped type="pytest.skip" message="skip"/></testcase>
<testcase classname="tests.test_regress" name="test_known"><skipped type="pytest.xfail" message="audit"/></testcase>
</testsuite></testsuites>"""

MANIFEST = {"milestones": {
    "MA": {"title": "all pass", "phases": ["0"], "required_tests": ["tests/test_a.py::test_ok"], "no_xfail_files": []},
    "MB": {"title": "param failure", "phases": ["0"], "required_tests": ["tests/test_a.py::test_param"], "no_xfail_files": []},
    "MC": {"title": "skipped", "phases": ["0"], "required_tests": ["tests/test_a.py::test_skip"], "no_xfail_files": []},
    "MD": {"title": "missing", "phases": ["0"], "required_tests": ["tests/test_a.py::test_nope"], "no_xfail_files": []},
    "ME": {"title": "leftover xfail", "phases": ["0"], "required_tests": ["tests/test_a.py::test_ok"],
           "no_xfail_files": ["tests/test_regress.py"]},
}}


def test_report_outcomes(tmp_path):
    junit = tmp_path / "j.xml"
    junit.write_text(JUNIT, encoding="utf-8")
    rep = ar.evaluate(MANIFEST, ar.load_results(junit))
    assert rep["MA"]["passed"] is True
    assert rep["MB"]["passed"] is False and rep["MB"]["required"][0]["outcome"] == "FAILED"
    assert rep["MC"]["passed"] is False and rep["MC"]["required"][0]["outcome"] == "SKIPPED"
    assert rep["MD"]["passed"] is False and rep["MD"]["required"][0]["outcome"] == "MISSING"
    assert rep["ME"]["passed"] is False and rep["ME"]["remaining_xfail"] == ["tests/test_regress.py::test_known"]


def test_cli_exit_codes(tmp_path):
    junit = tmp_path / "j.xml"
    junit.write_text(JUNIT, encoding="utf-8")
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps(MANIFEST), encoding="utf-8")
    assert ar.main(["--junit", str(junit), "--manifest", str(manifest), "--milestone", "MA", "--out", str(tmp_path / "r.md")]) == 0
    assert ar.main(["--junit", str(junit), "--manifest", str(manifest), "--milestone", "MD", "--out", str(tmp_path / "r.md")]) == 1


def test_red_ci_fails_every_milestone_and_no_ci_is_local_only(tmp_path):
    junit = tmp_path / "j.xml"
    junit.write_text(JUNIT, encoding="utf-8")
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps(MANIFEST), encoding="utf-8")
    out = tmp_path / "r.md"
    assert ar.main(["--junit", str(junit), "--manifest", str(manifest), "--milestone", "MA", "--out", str(out)]) == 0
    assert "PASS (local only, CI not recorded)" in out.read_text(encoding="utf-8")
    red = tmp_path / "ci_red.json"
    red.write_text(json.dumps({"databaseId": 1, "conclusion": "failure", "headSha": "abc"}), encoding="utf-8")
    assert ar.main(["--junit", str(junit), "--manifest", str(manifest), "--milestone", "MA", "--ci-run", str(red), "--out", str(out)]) == 1
    green = tmp_path / "ci_green.json"
    green.write_text(json.dumps({"databaseId": 2, "conclusion": "success", "headSha": "abc"}), encoding="utf-8")
    assert ar.main(["--junit", str(junit), "--manifest", str(manifest), "--milestone", "MA", "--ci-run", str(green), "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "| MA | all pass | PASS |" in text and "CI run 2 on abc: **success**" in text


def test_shipped_manifest_is_valid():
    manifest = json.loads((SCRIPT.parent.parent / "docs" / "refactor_9_2" / "milestones.json").read_text(encoding="utf-8"))
    assert set(manifest["milestones"]) == {"M1", "M2", "M3", "M4"}
    for spec in manifest["milestones"].values():
        assert spec["required_tests"] and all("::" in t for t in spec["required_tests"])
