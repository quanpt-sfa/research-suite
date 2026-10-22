"""End-to-end CLI demo of WP4 (research problems) in a throw-away store. No network, no real model: the proposals come from a scripted adapter.

    python scripts/demos/wp4_problem_demo.py
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research_harness import cli_knowledge  # noqa: E402
from research_harness.cli import main  # noqa: E402
from research_harness.state.store import StateStore  # noqa: E402
from research_harness.workflows.adapters import ScriptedModelAdapter  # noqa: E402

FRAMER = ScriptedModelAdapter(model_id="scripted-framer", default_response={"interpretations": [
    {"text": "Detection raises the cost of accrual manipulation, so reporting fraud moves to channels the models do not weight.",
     "constructs": ["detection", "manipulation channel"], "unit_of_analysis": "firm-year", "answer_kind": "causal",
     "search_terms": ["real activities manipulation", "earnings management", "detection model"]},
    {"text": "Detection changes which frauds are observed, not which are committed: the observed adaptation is a selection effect.",
     "constructs": ["detection", "selection"], "unit_of_analysis": "firm-year", "answer_kind": "descriptive",
     "search_terms": ["detection probability", "partial observability"]}]})


def run(*argv: str) -> None:
    print("$ research " + " ".join(f'"{a}"' if " " in a else a for a in argv))
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = main(list(argv))
    print(out.getvalue().rstrip() or err.getvalue().rstrip())
    if rc:
        print(f"(exit {rc})")
    print()


def main_demo() -> None:
    cli_knowledge._adapter = lambda args: FRAMER          # the demo never calls a provider
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        store, token = StateStore.create(home, "PRJ_DEMO", owner_id="researcher")
        tf = Path(tmp) / "token"
        tf.write_text(token, encoding="utf-8")
        base = ["--project", "PRJ_DEMO", "--home", str(home)]
        auth = [*base, "--token-file", str(tf)]
        print("# WP4 demo: a problem, several readings, the researcher's choice, and a research question\n")
        run("problem", "new", "--statement", "Firms that commit reporting fraud seem to change how they report after detection models become widely known.",
            "--kind", "phenomenon", "--scope", json.dumps({"domain": "accounting", "population": "US listed firms"}), "--exclude", "non-US firms",
            "--interpretation", "Managers shift from accruals to real activities.", *auth)
        run("problem", "frame", "PROB_000001", "--k", "3", *auth)
        run("problem", "add-interpretation", "PROB_000001", "--text", "Auditors, not managers, change what they flag.", *auth)
        run("problem", "choose", "PROB_000001", "--interpretations", "I1", "--rationale", " ", *auth)
        run("problem", "choose", "PROB_000001", "--interpretations", "I1,I3", "--rationale",
            "I can test the channel reading and the selection reading with the same data.", *auth)
        run("problem", "promote-to-rq", "PROB_000001", "--interpretation", "I1", "--text",
            "Do firms shift from accrual-based to real-activities manipulation once detection models spread?", "--rationale", "the testable reading", *auth)
        run("problem", "revise", "PROB_000001", "--rationale", "the problem is about disclosure choices, not manipulation as a whole", "--statement",
            "Firms that commit reporting fraud seem to change how they disclose after detection models become widely known.", *auth)
        run("problem", "show", "PROB_000001", *base)


if __name__ == "__main__":
    main_demo()
