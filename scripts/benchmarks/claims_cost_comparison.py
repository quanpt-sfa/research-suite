"""Measure what three ways of reading papers for claims cost, offline, and write the result as JSON and CSV.

    python scripts/benchmarks/claims_cost_comparison.py --out docs/refactor_9_2/evaluation/claims

The papers are the invented fixtures of tests/claims_fixtures.py plus one long invented paper; the answers come from a fixed rule-based stand-in (not a model); the tokens are estimates made by a
scripted adapter. See research_harness/benchmarks/claim_strategies.py for what is and is not measured. No network, no model, no provider billing.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from research_harness.benchmarks import claim_strategies as CS  # noqa: E402
from tests import claims_fixtures as F  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    papers = {"cr": F.CR_TEXT, "edu": F.EDU_TEXT, "fraud": F.FR_TEXT, "long": CS.long_paper("A Long Invented Paper", sections=6, per_section=30)}
    with tempfile.TemporaryDirectory() as tmp:
        res = CS.run_conditions(tmp, papers)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "strategy_cost_comparison.json").write_text(json.dumps(res, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    rows = []
    for cond, v in res["conditions"].items():
        for paper, p in v["per_paper"].items():
            rows.append([cond, paper, p["chars_total"], p["model_calls"], p["input_tokens"], p["output_tokens"], p["chunks"], p["reread_factor"], p["share_of_text_never_read"],
                         "; ".join(p["sections_never_read"]), p["records_set_aside_for_caps"], "; ".join(p["fields_at_cap"]), v["second_identical_pass"]["model_calls_added"]])
        t = v["totals"]
        rows.append([cond, "(all)", sum(res["papers"].values()), t["model_calls"], t["input_tokens"], t["output_tokens"], t["chunks"], t["reread_factor"], "", "", t["records_set_aside_for_caps"], "",
                     v["second_identical_pass"]["model_calls_added"]])
    with (out / "strategy_cost_comparison.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["condition", "paper", "chars", "model_calls", "input_tokens_estimated", "output_tokens_estimated", "chunks", "reread_factor", "share_never_read", "sections_never_read",
                    "records_set_aside_for_caps", "fields_at_cap", "calls_added_by_an_identical_second_pass"])
        w.writerows(rows)
    print(json.dumps({c: v["totals"] for c, v in res["conditions"].items()}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
