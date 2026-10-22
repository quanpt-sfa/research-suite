"""Score the lexical evidence selector on SciFact dev and write the result as JSON (no model, no network).

    python scripts/benchmarks/run_scifact_selector.py --data <dir with claims_dev.jsonl and corpus.jsonl> --archive <data.tar.gz> --out docs/refactor_9_2/evaluation/claims/scifact_selector_dev.json

The data are CC BY 4.0 (claims) and ODC-By 1.0 (abstracts): download the archive named in research_harness/benchmarks/scifact.py yourself; it is not part of this repository.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research_harness.benchmarks import scifact as SF  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--archive")
    ap.add_argument("--split", default="dev", choices=SF.SPLITS)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    res = SF.evaluate(a.data, a.split, archive=a.archive)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("split", "claims_in_split", "corpus_abstracts")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
