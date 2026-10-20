"""Phase 6.1 (plan v3): the synthetic logic benchmark, and proof that it can fail."""
from __future__ import annotations

import json

import pytest

from research_harness.benchmarks import logic
from research_harness.workflows import eca, status


def test_synthetic_logic_benchmark(tmp_path):
    report = logic.run_logic_benchmark(tmp_path)
    assert report["total"] == 7 and report["computed_only"] is True and "not evidence" in report["statement"]
    failed = [(c["id"], c["error"], c["observed"]) for c in report["cases"] if not c["passed"]]
    assert failed == [], failed
    assert [c["id"] for c in report["cases"] if not c["perturbation_changes_outcome"]] == []     # no answer is hard-coded
    assert report["all_passed"] is True and report["passed"] == 7
    caps = next(c for c in report["cases"] if c["id"] == "status_axis_caps")["observed"]
    assert caps["combinations"] > 10000 and caps["violations"] == 0                              # counted, not typed in
    json.dumps(report, default=str)                                                               # the report is plain data


def test_the_benchmark_fails_when_the_logic_is_broken(tmp_path, monkeypatch):
    """A benchmark that cannot fail proves nothing: break the guard, then the caps, and it must notice."""
    monkeypatch.setattr(eca, "consistency_guard", lambda proposal, cues: (proposal["verdict"], None))
    broken = logic.run_logic_benchmark(tmp_path / "a")
    assert broken["all_passed"] is False and "eca_correct_quote_wrong_interpretation" in {c["id"] for c in broken["cases"] if not c["passed"]}
    monkeypatch.undo()
    real = status.apply_caps

    def lax(requested, **kw):
        kw["researcher_confirmed"] = True
        return real(requested, **kw)
    monkeypatch.setattr(logic, "apply_caps", lax)
    lax_report = logic.run_logic_benchmark(tmp_path / "b")
    caps = next(c for c in lax_report["cases"] if c["id"] == "status_axis_caps")
    assert caps["passed"] is False and caps["observed"]["violations"] > 0


def test_a_crashing_case_is_a_failed_case(tmp_path, monkeypatch):
    def boom(root):
        raise RuntimeError("boom")
    desc, _, expect = logic.CASES["eca_counter_evidence"]
    monkeypatch.setitem(logic.CASES, "eca_counter_evidence", (desc, boom, expect))
    report = logic.run_logic_benchmark(tmp_path)
    case = next(c for c in report["cases"] if c["id"] == "eca_counter_evidence")
    assert case["passed"] is False and case["error"] == "RuntimeError: boom" and report["all_passed"] is False
