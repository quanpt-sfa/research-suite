"""End-to-end CLI demo of WP5 (internal retrieval) in a throw-away store. No network and no real model: the proposed search terms come from a scripted adapter.
The corpus is the synthetic benchmark of tests/retrieval_bench.py (invented text).

    python scripts/demos/wp5_retrieval_demo.py
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research_harness import cli_knowledge  # noqa: E402
from research_harness.cli import main  # noqa: E402
from research_harness.discovery import problem as P  # noqa: E402
from research_harness.state.store import StateStore  # noqa: E402
from research_harness.workflows.adapters import ScriptedModelAdapter  # noqa: E402
from tests import retrieval_bench as RB  # noqa: E402

EXPANDER = ScriptedModelAdapter(model_id="scripted-expander", default_response={"terms": [
    {"term": "earnings manipulation", "rationale": "the accounting literature's name for it"}, {"term": "AAER", "rationale": "the enforcement label used as the outcome"},
    {"term": "accounting irregularities", "rationale": "regulator wording"}, {"term": "falsified financial statements", "rationale": "legal wording"}]})
SHOWN: dict[str, str] = {}


def run(*argv: str) -> None:
    shown = " ".join(f'"{a}"' if " " in a else a for a in argv)
    for real, alias in SHOWN.items():
        shown = shown.replace(real, alias)
    print("$ research " + shown)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = main(list(argv))
    print(out.getvalue().rstrip() or err.getvalue().rstrip())
    if rc:
        print(f"(exit {rc})")
    print()


def main_demo() -> None:
    cli_knowledge._adapter = lambda args: EXPANDER
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        store, token = StateStore.create(home, "PRJ_DEMO", owner_id="researcher")
        tf = Path(tmp) / "token"
        tf.write_text(token, encoding="utf-8")
        export_dir = Path(tmp) / "export"
        SHOWN.update({str(home): "<home>", str(tf): "<token-file>", str(export_dir): "<export-dir>"})
        base = ["--project", "PRJ_DEMO", "--home", str(home)]
        auth = [*base, "--token-file", str(tf)]
        built = RB.build(store, token)
        gold = Path(tmp) / "gold.json"
        gold.write_text(json.dumps(built["gold"]), encoding="utf-8")
        print("# WP5 demo: from a problem to a reading list, with coverage, over a synthetic 42-source corpus\n")
        pid = P.new_problem(store, token, statement="Financial reporting fraud is hard to detect, and detection models may change what fraudsters do.",
                            interpretations=["Detection raises the cost of manipulation, so fraud moves to other channels."])["problem_id"]
        P.choose(store, token, pid, ["I1"], "the adaptation reading")
        run("corpus", "import-metadata", "--citation-key", "wp2025", "--title", "A Working Paper On Detection And Adaptation", "--document-type", "working_paper",
            "--review-status", "NOT_REVIEWED", "--first-public-date", "2025-03-10", "--date-evidence", "repository posting record (synthetic)", *auth)
        run("retrieval", "new", pid, "--term", "financial reporting fraud detection", *auth)
        run("retrieval", "search", "RET_000001", *auth)
        run("retrieval", "expand", "RET_000001", "--k", "6", *auth)
        run("retrieval", "expansions", "RET_000001", "--accept", "earnings manipulation", "--accept", "AAER", "--accept", "accounting irregularities", "--reject",
            "falsified financial statements", "--rationale", "the names I know from the literature; the legal wording is another field", *auth)
        run("retrieval", "search", "RET_000001", *auth)
        run("retrieval", "show", "RET_000001", *base)
        run("corpus", "exclude", "SRC_D_CARD_1", "--reason", "card fraud, a different problem", *auth)
        run("retrieval", "show", "RET_000001", *base)
        run("retrieval", "search", "RET_000001", *auth)
        run("retrieval", "review", "RET_000001", "irrelevant", "--source", "SRC_D_REPORTING_1", "--reason", "about reporting quality and the cost of capital, not about fraud", *auth)
        run("retrieval", "review", "RET_000001", "acquire", "--title", "The Paper Everyone Cites On Detection And Adaptation", "--reason", "the main competitor I do not hold", *auth)
        run("export", "retrieval", "RET_000001", "--out", str(export_dir), *base)
        run("export", "verify", str(export_dir), *base)
        for name in ("reading_list.md", "coverage_report.md", "not_read_candidates.csv"):
            print(f"----- {name} (first lines) -----")
            print("\n".join((export_dir / name).read_text(encoding="utf-8").splitlines()[:22]))
            print()
        SHOWN[str(gold)] = "<gold.json>"
        run("retrieval", "evaluate", "--gold", str(gold), *base)


if __name__ == "__main__":
    main_demo()
