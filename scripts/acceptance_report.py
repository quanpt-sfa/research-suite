#!/usr/bin/env python3
"""Generate the milestone acceptance report from pytest results (plan v3, milestone rule).

No PASS is written by hand. A milestone passes only if every required integration test PASSED (an xfail,
a skip or a missing test does not count) and no test of a `no_xfail_files` entry is still xfail.

Usage:
    pytest --junitxml=reports/junit.xml
    python scripts/acceptance_report.py --junit reports/junit.xml [--milestone M1] [--out report.md]

Exit code 0 only if every selected milestone passes.
"""
from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "docs" / "refactor_9_2" / "milestones.json"


def _outcome(case: ET.Element) -> str:
    for child in case:
        tag = child.tag
        if tag in {"failure", "error"}:
            return "FAILED"
        if tag == "skipped":
            kind = (child.get("type") or "") + " " + (child.get("message") or "")
            return "XFAIL" if "xfail" in kind.lower() else "SKIPPED"
    return "PASSED"


def load_results(junit_path: Path) -> dict[str, str]:
    """Return {'tests/test_x.py::test_name[param]': outcome}."""
    root = ET.parse(junit_path).getroot()
    out: dict[str, str] = {}
    for case in root.iter("testcase"):
        classname = case.get("classname") or ""
        name = case.get("name") or ""
        parts = classname.split(".")
        module = "/".join(parts) + ".py" if parts and parts[0] else ""
        out[f"{module}::{name}"] = _outcome(case)
    return out


def _matches(required: str, result_id: str) -> bool:
    return result_id == required or result_id.startswith(required + "[")


def evaluate(manifest: dict, results: dict[str, str], selected: list[str] | None = None) -> dict[str, dict]:
    report: dict[str, dict] = {}
    for mid, spec in manifest["milestones"].items():
        if selected and mid not in selected:
            continue
        rows = []
        ok = True
        for req in spec.get("required_tests", []):
            hits = {rid: o for rid, o in results.items() if _matches(req, rid)}
            if not hits:
                outcome = "MISSING"
            elif all(o == "PASSED" for o in hits.values()):
                outcome = "PASSED"
            else:
                outcome = sorted(set(o for o in hits.values() if o != "PASSED"))[0]
            rows.append({"test": req, "outcome": outcome})
            ok &= outcome == "PASSED"
        leftover = []
        for f in spec.get("no_xfail_files", []):
            leftover += [rid for rid, o in results.items() if rid.startswith(f + "::") and o == "XFAIL"]
        ok &= not leftover
        report[mid] = {"title": spec["title"], "phases": spec["phases"], "passed": ok, "required": rows,
                       "remaining_xfail": sorted(leftover)}
    return report


def render(report: dict[str, dict]) -> str:
    lines = ["# Research Suite 9.2 — milestone acceptance report", "",
             "_Generated from pytest results; no status is written by hand._", ""]
    lines += ["| Milestone | Title | Result |", "|---|---|---|"]
    for mid, r in report.items():
        lines.append(f"| {mid} | {r['title']} | {'PASS' if r['passed'] else 'FAIL'} |")
    for mid, r in report.items():
        lines += ["", f"## {mid} — {r['title']} (phases {', '.join(r['phases'])})", "",
                  "| Required integration test | Outcome |", "|---|---|"]
        lines += [f"| `{row['test']}` | {row['outcome']} |" for row in r["required"]]
        if r["remaining_xfail"]:
            lines += ["", f"Audit regressions still xfail ({len(r['remaining_xfail'])}):"]
            lines += [f"- `{x}`" for x in r["remaining_xfail"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--junit", required=True, type=Path)
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--milestone", action="append", help="limit to this milestone (repeatable)")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    report = evaluate(manifest, load_results(args.junit), args.milestone)
    if not report:
        print("no milestone selected", file=sys.stderr)
        return 2
    text = render(report)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0 if all(r["passed"] for r in report.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
