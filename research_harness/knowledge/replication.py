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


# ---------------------------------------------------------------------------------------------------
# intake, entitlement and recording (phase 5b)
# ---------------------------------------------------------------------------------------------------

ENTITLEMENT_STATUS = ("unspecified", "declared", "confirmed")
MAX_PACKAGE_BYTES = 512 * 1024 * 1024


def entitlement_of(content: dict[str, Any]) -> dict[str, Any]:
    """Whether the researcher is entitled to use a package's data and code, as recorded. `confirmed` needs a stated basis
    (a license, a data-use agreement, the authors' permission). A repository with no license is `unspecified`: all rights
    stay with its authors, and the package is kept for local use only."""
    e = ((content.get("body") or {}).get("entitlement")) or {}
    status = e.get("status", "unspecified")
    if status == "confirmed" and not e.get("basis"):
        status = "unspecified"
    return {"status": status, "license": e.get("license"), "basis": e.get("basis"), "checked_at": e.get("checked_at"),
            "findings": list(e.get("findings") or []), "established": status == "confirmed"}


def add_replication_package(store: StateStore, token: Optional[str], *, package_id: str, label: str, directory: Any,
                            files: list[str], expected_outputs: Optional[list[dict[str, Any]]] = None, source_id: Optional[str] = None,
                            entitlement: Optional[dict[str, Any]] = None, notes: Optional[str] = None,
                            max_total_bytes: int = MAX_PACKAGE_BYTES) -> dict[str, Any]:
    """Store files of a replication package in the content-addressed store and record them as a `replication_package` item.

    `files` are paths relative to `directory`. Each `expected_outputs` entry is `{id, file, stat, column?, where?, expected,
    tolerance, reported_in?}`; `reported_in` names the passage where the source reports that number, and must exist.
    `entitlement` records the license and the basis on which the researcher uses the data (default: unspecified).
    """
    from pathlib import Path
    from research_harness.knowledge.common import WRITE_ROLES, engine_origin, scope_id
    root = Path(directory).resolve()
    stored, total = [], 0
    for rel in files:
        p = (root / rel).resolve()
        if root not in p.parents or not p.is_file():
            raise KnowledgeError("PACKAGE_FILE_INVALID", f"{rel} is not a file inside the package directory")
        total += p.stat().st_size
        if total > max_total_bytes:
            raise KnowledgeError("PACKAGE_TOO_LARGE", f"the files exceed {max_total_bytes} bytes")
        stored.append({"name": Path(rel).as_posix(), "sha256": store.artifacts.put_bytes(p.read_bytes()), "bytes": p.stat().st_size})
    names = {f["name"] for f in stored}
    outputs = []
    for o in expected_outputs or []:
        if o.get("file") not in names:
            raise KnowledgeError("EXPECTED_OUTPUT_INVALID", f"output {o.get('id')}: file {o.get('file')!r} is not one of the stored files")
        if o.get("stat") not in STATS or "expected" not in o or o.get("id") is None:
            raise KnowledgeError("EXPECTED_OUTPUT_INVALID", f"an expected output needs id, expected and a stat in {STATS}")
        if o.get("reported_in") and not store.get_object(o["reported_in"]):
            raise KnowledgeError("EXPECTED_OUTPUT_INVALID", f"output {o['id']}: the passage {o['reported_in']} does not exist")
        outputs.append(dict(o))
    ent = {"status": "unspecified", "license": None, "basis": None, "findings": [], **(entitlement or {})}
    if ent["status"] not in ENTITLEMENT_STATUS:
        raise KnowledgeError("ENTITLEMENT_STATUS_INVALID", f"status must be one of {ENTITLEMENT_STATUS}")
    pid = scope_id(store, package_id)
    content = {"label": label, "kind": "replication_package", "generated_by": "import", "lifecycle": "ACTIVE",
               "body": {"files": stored, "expected_outputs": outputs, "entitlement": {**ent, "checked_at": store.clock()}, "notes": notes or ""}}
    with store.write_tx(token=token, require_roles=WRITE_ROLES) as tx:
        edges = []
        if source_id:
            edges.append(("derived_from", source_id, tx.head(source_id)["head_revision"], {"origin": "replication_add"}))
        for rid in sorted({o["reported_in"] for o in outputs if o.get("reported_in")}):
            edges.append(("depends_on", rid, tx.head(rid)["head_revision"], {"origin": "replication_add"}))
        res = tx.add_revision(object_id=pid, object_type="KnowledgeItem", content=content, edges=edges,
                              origin=engine_origin("replication_add", tx.actor_id))
        tx.audit("replication_add", "RECORDED", {"object_id": pid, "files": len(stored), "bytes": total})
    return {"package_id": pid, "revision": res["revision"], "files": len(stored), "bytes": total, "entitlement": entitlement_of(content)}


def record_reproduction(store: StateStore, token: Optional[str], package_id: str) -> dict[str, Any]:
    """Recompute a package's outputs and write the result into the package and into the playbook elements linked to it, as
    their `computational_reproducibility`. That is the only effect: it never touches methodological validity."""
    import json as _json
    from research_harness.knowledge.common import WRITE_ROLES, engine_origin
    result = reproduce_package(store, package_id)
    stamp = {"status": result["computational_reproducibility"], "package": package_id, "outputs": result["outputs"], "checked_at": store.clock()}
    touched = []
    with store.write_tx(token=token, require_roles=WRITE_ROLES) as tx:
        cur = tx.get(package_id)
        rows = tx.conn.execute("SELECT relation,target_id,target_revision,provenance FROM edges WHERE source_id=? AND source_revision=?",
                               (package_id, cur["revision"])).fetchall()
        tx.add_revision(object_id=package_id, object_type="KnowledgeItem", content={**cur["content"], "reproduction": stamp},
                        edges=[(r[0], r[1], r[2], _json.loads(r[3])) for r in rows], origin=engine_origin("replication_reproduce", tx.actor_id))
        for r in tx.conn.execute("SELECT object_id FROM object_heads WHERE object_type='MethodPlaybook' ORDER BY object_id").fetchall():
            pb = tx.get(r[0])
            els = [dict(e) for e in pb["content"].get("elements") or []]
            hit = False
            ids = {o["id"] for o in result["outputs"]}
            for e in els:
                linked = [o for o in e.get("replication_outputs") or [] if o in ids]
                if linked:
                    sts = [o["status"] for o in result["outputs"] if o["id"] in linked]
                    e["computational_reproducibility"] = {"status": "FAILED_TO_REPRODUCE" if "FAILED" in sts else "REPRODUCED_WITHIN_TOLERANCE"
                                                          if all(s == "REPRODUCED" for s in sts) else "RAN", "package": package_id,
                                                          "outputs": linked, "checked_at": stamp["checked_at"]}
                    hit = True
            if hit:
                prows = tx.conn.execute("SELECT relation,target_id,target_revision,provenance FROM edges WHERE source_id=? AND source_revision=?",
                                        (r[0], pb["revision"])).fetchall()
                tx.add_revision(object_id=r[0], object_type="MethodPlaybook", content={**pb["content"], "elements": els},
                                edges=[(x[0], x[1], x[2], _json.loads(x[3])) for x in prows], origin=engine_origin("replication_reproduce", tx.actor_id))
                touched.append(r[0])
    return {**result, "recorded_in": touched, "entitlement": entitlement_of(store.get_object(package_id)["content"])}


def set_entitlement(store: StateStore, token: Optional[str], package_id: str, *, status: str, license: Optional[str] = None,
                    basis: Optional[str] = None, findings: Optional[list[str]] = None) -> dict[str, Any]:
    """Record or change the entitlement of a package (author or owner). `confirmed` needs a stated basis: it is the researcher's
    statement that they may use the data and code, and the record says whose statement it is."""
    import json as _json
    from research_harness.knowledge.common import engine_origin
    if status not in ENTITLEMENT_STATUS:
        raise KnowledgeError("ENTITLEMENT_STATUS_INVALID", f"status must be one of {ENTITLEMENT_STATUS}")
    if status == "confirmed" and not (basis or "").strip():
        raise KnowledgeError("ENTITLEMENT_BASIS_REQUIRED", "confirming an entitlement needs a stated basis (a license, an agreement, the authors' permission, or the researcher's statement)")
    with store.write_tx(token=token, require_roles=("author", "owner")) as tx:
        cur = tx.get(package_id)
        if not cur or cur["content"].get("kind") != "replication_package":
            raise KnowledgeError("NOT_A_REPLICATION_PACKAGE", f"{package_id} is not a replication_package item")
        body = dict(cur["content"].get("body") or {})
        old = body.get("entitlement") or {}
        body["entitlement"] = {**old, "status": status, "license": license if license is not None else old.get("license"), "basis": basis,
                               "findings": findings if findings is not None else old.get("findings", []), "checked_at": tx.at,
                               "history": (old.get("history") or []) + [{"status": old.get("status", "unspecified"), "until": tx.at, "by": tx.actor_id}]}
        rows = tx.conn.execute("SELECT relation,target_id,target_revision,provenance FROM edges WHERE source_id=? AND source_revision=?",
                               (package_id, cur["revision"])).fetchall()
        res = tx.add_revision(object_id=package_id, object_type="KnowledgeItem", content={**cur["content"], "body": body},
                              edges=[(r[0], r[1], r[2], _json.loads(r[3])) for r in rows], origin=engine_origin("entitlement", tx.actor_id))
        tx.audit("entitlement", "RECORDED", {"package": package_id, "status": status})
    return {"package_id": package_id, "revision": res["revision"], "entitlement": entitlement_of({"body": body})}
