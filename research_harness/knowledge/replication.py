"""Checking a replication package's declared outputs against its stored data (computational reproducibility).

A `KnowledgeItem{kind: replication_package}` carries `body.files` (name, sha256: the data, stored in the
content-addressed store) and `body.expected_outputs`: descriptive statistics of those files that the package's
source reports, each with a tolerance. `reproduce_package` recomputes them with the standard library and compares.

This runs no code from the package. It answers one narrow question: do the stored data give the numbers the package
says they give, within the stated tolerance? Re-running a package's model is a separate, later step. The result is the
`computational_reproducibility` dimension and nothing else: it never changes methodological validity (plan v3, 4.1).
"""
from __future__ import annotations

import csv
import io
from typing import Any, Optional

from research_harness.knowledge.common import KnowledgeError
from research_harness.state.store import StateStore

STATS = ("count", "sum", "mean", "positive_rate")


def _rows(store: StateStore, files: dict[str, dict[str, Any]], name: str) -> tuple[Optional[list[dict[str, str]]], Optional[str]]:
    f = files.get(name)
    if not f:
        return None, f"{name} is not listed in the package files"
    sha = f.get("sha256")
    if not sha or not store.artifacts.has(sha):
        return None, f"{name} is not stored"
    if not store.artifacts.verify(sha):
        return None, f"{name} does not match its digest"
    text = store.artifacts.get_bytes(sha).decode("utf-8", errors="replace")
    return list(csv.DictReader(io.StringIO(text))), None


def _compute(rows: list[dict[str, str]], spec: dict[str, Any]) -> tuple[Optional[float], Optional[str]]:
    stat, col = spec["stat"], spec.get("column")
    where = spec.get("where")
    if where:
        rows = [r for r in rows if r.get(where["column"]) == str(where["equals"])]
    if stat == "count":
        return float(len(rows)), None
    if col is None or (rows and col not in rows[0]):
        return None, f"column {col!r} is not in the file"
    try:
        values = [float(r[col]) for r in rows if r[col] != ""]
    except ValueError:
        return None, f"column {col!r} has non-numeric values"
    if not values:
        return None, "no values"
    if stat == "sum":
        return sum(values), None
    if stat == "mean":
        return sum(values) / len(values), None
    if stat == "positive_rate":
        return sum(1 for v in values if v > 0) / len(values), None
    return None, f"unknown statistic {stat!r}"


def reproduce_package(store: StateStore, item_id: str, revision: Optional[int] = None) -> dict[str, Any]:
    obj = store.get_object(item_id, revision)
    if not obj or obj["object_type"] != "KnowledgeItem" or obj["content"].get("kind") != "replication_package":
        raise KnowledgeError("NOT_A_REPLICATION_PACKAGE", f"{item_id} is not a replication_package item")
    body = obj["content"].get("body") or {}
    files = {f["name"]: f for f in body.get("files") or []}
    expected = body.get("expected_outputs") or []
    if not files:
        return {"item": item_id, "computational_reproducibility": "NO_ASSETS", "outputs": []}
    cache: dict[str, Any] = {}
    outputs = []
    for spec in expected:
        if spec.get("stat") not in STATS:
            outputs.append({"id": spec.get("id"), "status": "NOT_COMPUTABLE", "reason": f"statistic must be one of {STATS}"})
            continue
        if spec["file"] not in cache:
            cache[spec["file"]] = _rows(store, files, spec["file"])
        rows, err = cache[spec["file"]]
        if rows is None:
            outputs.append({"id": spec["id"], "status": "NOT_COMPUTABLE", "reason": err})
            continue
        observed, err = _compute(rows, spec)
        if observed is None:
            outputs.append({"id": spec["id"], "status": "NOT_COMPUTABLE", "reason": err})
            continue
        tol = float(spec.get("tolerance", 0.0))
        if spec.get("rel_tolerance") is not None:
            tol = max(tol, abs(float(spec["expected"])) * float(spec["rel_tolerance"]))
        ok = abs(observed - float(spec["expected"])) <= tol
        outputs.append({"id": spec["id"], "status": "REPRODUCED" if ok else "FAILED", "observed": observed,
                        "expected": spec["expected"], "tolerance": tol})
    if not expected:
        status = "RAN"
    elif any(o["status"] == "FAILED" for o in outputs):
        status = "FAILED_TO_REPRODUCE"
    elif any(o["status"] == "NOT_COMPUTABLE" for o in outputs):
        status = "RAN"
    else:
        status = "REPRODUCED_WITHIN_TOLERANCE"
    return {"item": item_id, "revision": obj["revision"], "computational_reproducibility": status, "outputs": outputs}
