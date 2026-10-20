#!/usr/bin/env python3
"""Print scoped KnowledgeItem proposals for the static discipline profiles.

    python scripts/migrate_profiles.py [accounting finance education] [--out operations.json]

The output is `{"operations": [...]}` (`propose_object` operations). Wrap them in a proposal with the target project's
snapshot as `base` (`research_harness.workflows.profiles.knowledge.build_proposal`) and import it through the normal
gateway: `research proposal import`. Nothing is written to any project by this script.
"""
from __future__ import annotations

import argparse
import json
import sys

from research_harness.workflows.profiles.knowledge import profile_knowledge_operations
from research_harness.workflows.profiles.registry import list_profiles


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("profiles", nargs="*", help="profile ids (default: all)")
    ap.add_argument("--out", help="write to this file instead of stdout")
    args = ap.parse_args(argv)
    ids = args.profiles or [p["identifier"] if isinstance(p, dict) else p for p in list_profiles()]
    operations = [op for pid in ids for op in profile_knowledge_operations(pid)]
    text = json.dumps({"operations": operations}, indent=2, sort_keys=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"{len(operations)} operation(s) written to {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
