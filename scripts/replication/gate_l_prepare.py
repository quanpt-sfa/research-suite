#!/usr/bin/env python3
"""Prepare the real material of the Gate L evaluation (intake only: nothing here judges anything).

Reads a directory holding the researcher's papers and replication packages, stores them in an evaluation project, and runs the
computational checks that need no model and no judgement: do the stored data give the numbers the papers report?

    python scripts/replication/gate_l_prepare.py --material "sample/gate L" --study-a-paper sample/zavitsanos2025positive-unlabeled.pdf \
        --study-a-package sample/pu_finance --home "sample/gate L/_eval_home"

Writes `<home>/preparation_report.json` and the owner token to `<home>/owner.token` (the home is local state and must never be committed).
Needs `pypdf`. It makes no model call, writes no rubric, no gold annotation and no confirmation: those are the researcher's.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from research_harness.knowledge import corpus
from research_harness.knowledge.replication import add_replication_package, entitlement_of, reproduce_package
from research_harness.state.store import StateStore


def table1_rows(text: str) -> dict[int, tuple[int, int, str]]:
    """Rows of Bao et al.'s Table 1: year -> (firm-years, fraud firm-years, the row exactly as printed)."""
    rows = {}
    for m in re.finditer(r"^(?P<line>(?P<y>(19|20)\d\d) (?P<n>[\d,]+) (?P<f>\d+) (?P<p>[\d.]+))$", text, re.M):
        rows[int(m.group("y"))] = (int(m.group("n").replace(",", "")), int(m.group("f")), m.group("line"))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--material", required=True, type=Path)
    ap.add_argument("--study-a-paper", required=True, type=Path)
    ap.add_argument("--study-a-package", required=True, type=Path)
    ap.add_argument("--home", required=True, type=Path)
    a = ap.parse_args()
    a.home.mkdir(parents=True, exist_ok=True)
    store, token = StateStore.create(a.home, "GATEL_REAL", owner_id="researcher")
    (a.home / "owner.token").write_text(token, encoding="utf-8")
    report: dict = {"project": "GATEL_REAL", "home": str(a.home), "sources": {}, "packages": {}}

    for key, path, role in (("zavitsanos2025", a.study_a_paper, "study A paper"), ("bao2020", a.material / "bao2020detecting.pdf", "study B paper"),
                            ("bao2022erratum", a.material / "bao2022erratum.pdf", "study B erratum")):
        r = corpus.import_source_file(store, token, path, citation_key=key, license_note=f"supplied by the researcher ({role}); stored locally only")
        report["sources"][r["object_id"]] = {"role": role, "chars": r["chars"], "sections": r["sections"], "text_sha256": r["text_sha256"]}

    # Study A package: the shipped PU-HRF results and the rerun made with the pinned toolchain (scripts/replication/pu_finance_wsl.sh)
    pkg_a = a.study_a_package
    _, z_text = corpus.source_text(store, "SRC_ZAVITSANOS2025")
    row = corpus.add_passage(store, token, source_id="SRC_ZAVITSANOS2025", quote="HRF PU 0.1342 0.0920 0.1822 0.1281", passage_id="EP_TABLE1_HRF")
    files = ["results/PUHRF_baseline_meta_noCIK.csv"] + (["reproduced/PUHRF_reproduced.csv"] if (pkg_a / "reproduced" / "PUHRF_reproduced.csv").is_file() else [])
    outs = []
    for f in files:
        tag = "shipped" if f.startswith("results") else "rerun"
        outs += [{"id": f"ap_{tag}", "file": f, "column": "average_precision", "stat": "mean", "expected": 0.1281, "tolerance": 5e-5, "reported_in": row["object_id"]},
                 {"id": f"rprec_{tag}", "file": f, "column": "rec-precision", "stat": "mean", "expected": 0.1822, "tolerance": 1e-4, "reported_in": row["object_id"]}]
    add_replication_package(store, token, package_id="PKG_PUHRF", label="pu_finance PU-HRF results (Study A)", directory=pkg_a, files=files, expected_outputs=outs,
                            source_id="SRC_ZAVITSANOS2025", entitlement={"status": "confirmed", "license": "stated by the researcher",
                                                                          "basis": "the researcher states that a license exists; its text was not inspected by the assistant"})
    report["packages"]["PKG_PUHRF"] = {k: v for k, v in reproduce_package(store, "PKG_PUHRF").items() if k != "item"}

    # Study B package: Bao et al. (JAR 2020). Check the data against the paper's Table 1 for the years the data cover.
    bao = a.material / "FraudDetection"
    _, b_text = corpus.source_text(store, "SRC_BAO2020")
    table = table1_rows(b_text)
    import csv
    years = sorted({int(r["fyear"]) for r in csv.DictReader(open(bao / "data_FraudDetection_JAR2020.csv", newline="", encoding="utf-8"))})
    outs, skipped = [], []
    for y in years:
        if y not in table:
            skipped.append(y)
            continue
        n, f, line = table[y]
        p = corpus.add_passage(store, token, source_id="SRC_BAO2020", quote=line, passage_id=f"EP_BAO_TABLE1_{y}")
        outs += [{"id": f"firm_years_{y}", "file": "data_FraudDetection_JAR2020.csv", "stat": "count", "where": {"column": "fyear", "equals": y}, "expected": n, "tolerance": 0,
                  "reported_in": p["object_id"]},
                 {"id": f"fraud_{y}", "file": "data_FraudDetection_JAR2020.csv", "column": "misstate", "stat": "sum", "where": {"column": "fyear", "equals": y}, "expected": f,
                  "tolerance": 0, "reported_in": p["object_id"]}]
    add_replication_package(store, token, package_id="PKG_BAO_JAR2020", label="JarFraud/FraudDetection data (Study B)", directory=bao,
                            files=["data_FraudDetection_JAR2020.csv", "AAER_firm_year.csv", "identifiers.csv", "README.md", "run_RUSBoost.m", "tune_RUSBoost.m", "evaluate.m", "data_reader.m"],
                            expected_outputs=outs, source_id="SRC_BAO2020",
                            entitlement={"status": "unspecified", "license": None, "basis": None,
                                         "findings": ["the GitHub repository declares no license; the README says the data and code are public and asks for citation",
                                                      "the data contain COMPUSTAT-derived variables downloaded under a commercial license"]},
                            notes="Code is MATLAB (RUSBoost); no MATLAB or Octave is available here, so the model has not been rerun.")
    rep_b = reproduce_package(store, "PKG_BAO_JAR2020")
    report["packages"]["PKG_BAO_JAR2020"] = {"computational_reproducibility": rep_b["computational_reproducibility"], "years_checked": [y for y in years if y in table], "years_without_table_row": skipped,
                                             "outputs_reproduced": sum(1 for o in rep_b["outputs"] if o["status"] == "REPRODUCED"), "outputs_failed": [o for o in rep_b["outputs"] if o["status"] != "REPRODUCED"],
                                             "entitlement": {k: v for k, v in entitlement_of(store.get_object("PKG_BAO_JAR2020")["content"]).items() if k != "checked_at"}}
    (a.home / "preparation_report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: (v if k != "packages" else {p: {kk: vv for kk, vv in d.items() if kk != "outputs"} for p, d in v.items()}) for k, v in report.items()}, indent=2, default=str)[:6000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
