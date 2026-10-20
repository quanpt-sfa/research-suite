"""Milestone M4 exit criteria that only the researcher's real evaluation can meet (plan v3, 6.2 and 6.3).

`test_gate_k_real_pilot_artifacts_present` and `test_gate_l_cold_warm_artifacts_present` check the artefact directories that the real
evaluation exports (`docs/refactor_9_2/evaluation/gate_k` and `.../gate_l`, or `$RESEARCH_EVAL_DIR`). When a directory does not exist the
test SKIPS, and the generated acceptance report counts a skip as not met: Gate K and Gate L are not claimed until the real artefacts exist.
Nothing in this file can be satisfied by synthetic material.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from research_harness.benchmarks import gate_k, gate_l

EVAL_DIR = Path(os.environ.get("RESEARCH_EVAL_DIR") or Path(__file__).resolve().parents[1] / "docs" / "refactor_9_2" / "evaluation")


def test_gate_k_real_pilot_artifacts_present():
    d = EVAL_DIR / "gate_k"
    if not (d / "manifest.json").is_file():
        pytest.skip(f"no Gate K artefacts in {d}: the researcher's blind reference annotation and scores on the real pilot have not been recorded "
                    "(see docs/refactor_9_2/GATE_K_PROTOCOL.md)")
    result = gate_k.validate_artifacts(d)
    assert result["ok"], result["problems"]


def test_gate_l_cold_warm_artifacts_present():
    d = EVAL_DIR / "gate_l"
    if not (d / "manifest.json").is_file():
        pytest.skip(f"no Gate L artefacts in {d}: the real cold/warm run on matched task families and Study C has not been recorded "
                    "(see docs/refactor_9_2/GATE_L_PROTOCOL.md)")
    result = gate_l.validate_artifacts(d)
    assert result["ok"], result["problems"]


def test_the_artifact_validators_reject_anything_that_is_not_a_real_passing_evaluation(tmp_path):
    """The validators are the only thing standing between the acceptance report and a claim, so they are tested on their own."""
    assert gate_l.validate_artifacts(tmp_path / "nothing")["ok"] is False
    rubric = {"tolerance": {f: 0.0 for f in gate_l.QUALITY_FAMILIES}}
    def rec(label, started, t5=0):
        return {"label": label, "started_at": started, "scores": {f: {"score": 1.0} for f in gate_l.FAMILIES} | {"T5": {"score": t5}},
                "effort": {"effort_units": 5 if label == "cold" else 1, "family_units": {"T2": 5 if label == "cold" else 1}}, "reuse": {"T2": label == "warm"}}
    c_ok = {"false_transfers": 0, "offered": 2, "excluded_with_reasons": [{"item": "X", "because": ["scope"]}]}
    real = {"real_paper": True, "real_code": True, "paper_source": "SRC_X", "package": "PKG_X"}
    kw = dict(rubric=rubric, rubric_declared_at="2026-01-01T00:00:00Z", study_c=c_ok, study="pilot")
    gate_l.export_artifacts(tmp_path / "synthetic", cold=rec("cold", "2026-02-01T00:00:00Z"), warm=rec("warm", "2026-02-02T00:00:00Z"), real_evidence=real, real=False, **kw)
    assert "the artefacts are not marked as a real evaluation" in gate_l.validate_artifacts(tmp_path / "synthetic")["problems"]
    gate_l.export_artifacts(tmp_path / "no_real_code", cold=rec("cold", "2026-02-01T00:00:00Z"), warm=rec("warm", "2026-02-02T00:00:00Z"), real_evidence=None, real=True, **kw)
    assert "L5 does not hold" in gate_l.validate_artifacts(tmp_path / "no_real_code")["problems"]
    gate_l.export_artifacts(tmp_path / "early", cold=rec("cold", "2025-12-01T00:00:00Z"), warm=rec("warm", "2026-02-02T00:00:00Z"), real_evidence=real, real=True, **kw)
    assert any("started before the rubric was declared" in p for p in gate_l.validate_artifacts(tmp_path / "early")["problems"])
    gate_l.export_artifacts(tmp_path / "good", cold=rec("cold", "2026-02-01T00:00:00Z"), warm=rec("warm", "2026-02-02T00:00:00Z"), real_evidence=real, real=True, **kw)
    assert gate_l.validate_artifacts(tmp_path / "good") == {"ok": True, "problems": []}
    (tmp_path / "good" / "warm_run.json").write_text("{}", encoding="utf-8")
    assert "warm_run.json is missing or does not match its recorded hash" in gate_l.validate_artifacts(tmp_path / "good")["problems"]
