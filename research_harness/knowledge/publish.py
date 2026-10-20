"""Publishing library knowledge: portable bundles and release notes (plan v3, phase 7).

`export_bundle` writes an item with its whole dependency closure to a directory: every object revision with its edges, every stored file,
and a manifest with the digests. `import_bundle` verifies *everything* (every file against its hash, every object against its content
and revision digest, the closure against its own digest) before it writes anything, and then writes all of it in one transaction or none
of it. A bundle that was altered in transit therefore imports nothing.

`release_notes` states, for one item, each validity dimension separately and for each context it was assessed in. It reports what is
recorded; a dimension nobody assessed is shown as not assessed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from research_harness.core.canonical import canonical_json_hash
from research_harness.knowledge import lifecycle
from research_harness.knowledge.closure import ClosureManifest, dependency_closure
from research_harness.knowledge.common import WRITE_ROLES, KnowledgeError
from research_harness.knowledge.library import LIB_PREFIX, parse_pin, resolve_pin
from research_harness.knowledge.replication import entitlement_of
from research_harness.state.store import StateStore, revision_digest

BUNDLE_VERSION = "1"


def export_bundle(lib: StateStore, pin_text: str, out_dir: Path | str) -> dict[str, Any]:
    out = Path(out_dir)
    if out.exists() and any(out.iterdir()):
        raise KnowledgeError("BUNDLE_DIR_NOT_EMPTY", f"{out} is not empty; a bundle is written to a new directory")
    manifest = dependency_closure(lib, pin_text)
    (out / "blobs").mkdir(parents=True, exist_ok=True)
    objects = []
    with lib.read() as conn:
        for e in manifest.entries:
            obj = lib.get_object(e["object_id"], e["revision"], conn=conn)
            objects.append({"object_id": e["object_id"], "revision": e["revision"], "object_type": obj["object_type"], "content": obj["content"],
                            "content_digest": obj["content_digest"], "revision_digest": obj["revision_digest"],
                            "edges": [{"relation": x["relation"], "target_id": x["target_id"], "target_revision": x["target_revision"],
                                       "provenance": json.loads(x["provenance"])} for x in lib.edges_from(conn, e["object_id"], e["revision"])]})
    for a in manifest.artifacts:
        (out / "blobs" / a["sha256"]).write_bytes(lib.artifacts.get_bytes(a["sha256"]))
    doc = {"bundle_version": BUNDLE_VERSION, "closure": manifest.to_dict(), "objects": objects}
    raw = json.dumps(doc, sort_keys=True, ensure_ascii=False).encode("utf-8")
    (out / "bundle.json").write_bytes(raw)
    return {"root_pin": manifest.root_pin, "closure_digest": manifest.closure_digest, "objects": len(objects), "artifacts": len(manifest.artifacts),
            "bundle_sha256": hashlib.sha256(raw).hexdigest()}


def verify_bundle(bundle_dir: Path | str) -> tuple[dict[str, Any], list[str]]:
    d = Path(bundle_dir)
    try:
        doc = json.loads((d / "bundle.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise KnowledgeError("BUNDLE_UNREADABLE", f"{d}: {exc}") from exc
    problems: list[str] = []
    if doc.get("bundle_version") != BUNDLE_VERSION:
        problems.append(f"bundle_version must be {BUNDLE_VERSION}")
    try:
        manifest = ClosureManifest.from_dict(doc["closure"])
    except (KnowledgeError, KeyError) as exc:
        return doc, problems + [f"closure manifest: {exc}"]
    listed = {(e["object_id"], e["revision"]): e for e in manifest.entries}
    present = {(o["object_id"], o["revision"]): o for o in doc.get("objects", [])}
    if set(listed) != set(present):
        problems.append("the objects in the bundle are not the objects its manifest lists")
    for key, o in present.items():
        e = listed.get(key)
        if e is None:
            continue
        if canonical_json_hash(o["content"]) != o["content_digest"] or o["content_digest"] != e["content_digest"]:
            problems.append(f"{key[0]}@{key[1]}: content does not match its digest")
        edges = [(x["relation"], x["target_id"], x["target_revision"]) for x in o["edges"]]
        if revision_digest(o["content_digest"], edges) != o["revision_digest"] or o["revision_digest"] != e["revision_digest"]:
            problems.append(f"{key[0]}@{key[1]}: edges do not match the revision digest")
        if o["object_id"] and not o["object_id"].startswith(LIB_PREFIX):
            problems.append(f"{o['object_id']}: bundle objects must carry the {LIB_PREFIX} prefix")
    for a in manifest.artifacts:
        p = d / "blobs" / a["sha256"]
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != a["sha256"]:
            problems.append(f"file {a['sha256'][:16]} is missing or does not match its hash")
    return doc, problems


def import_bundle(target: StateStore, token: Optional[str], bundle_dir: Path | str) -> dict[str, Any]:
    """Import a verified bundle into a library (or a project) in one transaction. Nothing is written if anything is wrong."""
    doc, problems = verify_bundle(bundle_dir)
    if problems:
        raise KnowledgeError("BUNDLE_VERIFICATION_FAILED", "; ".join(problems[:6]), problems=problems)
    manifest = ClosureManifest.from_dict(doc["closure"])
    objs = {(o["object_id"], o["revision"]): o for o in doc["objects"]}
    deps = {k: {(k[0], k[1] - 1)} if k[1] > 1 else set() for k in objs}
    for k, o in objs.items():
        for e in o["edges"]:
            deps[k].add((e["target_id"], e["target_revision"]))
    order, done = [], set()
    while len(order) < len(objs):
        ready = sorted(k for k in objs if k not in done and deps[k] <= done)
        if not ready:
            raise KnowledgeError("BUNDLE_CYCLE", "the bundle's objects depend on each other in a cycle")
        order.append(ready[0])
        done.add(ready[0])
    for a in manifest.artifacts:
        if target.artifacts.put_bytes((Path(bundle_dir) / "blobs" / a["sha256"]).read_bytes()) != a["sha256"]:
            raise KnowledgeError("ARTIFACT_COPY_MISMATCH", f"file {a['sha256'][:16]} changed while being copied")
    imported = skipped = 0
    with target.write_tx(token=token, require_roles=WRITE_ROLES) as tx:
        for key in order:
            o = objs[key]
            head = tx.head(o["object_id"])
            if head and head["head_revision"] >= o["revision"]:
                have = tx.get(o["object_id"], o["revision"])
                if have["revision_digest"] != o["revision_digest"]:
                    raise KnowledgeError("IMPORT_CONFLICT", f"{o['object_id']}@{o['revision']} already exists with different content")
                skipped += 1
                continue
            if (head["head_revision"] if head else 0) != o["revision"] - 1:
                raise KnowledgeError("IMPORT_CONFLICT", f"{o['object_id']} is at revision {head['head_revision'] if head else 0}; cannot import {o['revision']}")
            res = tx.add_revision(object_id=o["object_id"], object_type=o["object_type"], content=o["content"],
                                  edges=[(e["relation"], e["target_id"], e["target_revision"], e["provenance"]) for e in o["edges"]],
                                  origin={"kind": "LIBRARY_IMPORTED", "authenticated_actor": tx.actor_id, "bundle_closure_digest": manifest.closure_digest,
                                          "pin": manifest.root_pin})
            if res["revision_digest"] != o["revision_digest"]:
                raise KnowledgeError("IMPORT_DIGEST_MISMATCH", f"{o['object_id']}@{o['revision']} was imported with a different digest")
            imported += 1
        tx.audit("library_import_bundle", "IMPORTED", {"root_pin": manifest.root_pin, "objects_imported": imported, "objects_already_present": skipped})
    return {"status": "IMPORTED", "root_pin": manifest.root_pin, "closure_digest": manifest.closure_digest, "objects_imported": imported,
            "objects_already_present": skipped, "artifacts": len(manifest.artifacts)}


# --------------------------------------------------------------------------------------------------
# release notes
# --------------------------------------------------------------------------------------------------

def release_notes(store: StateStore, pin_text: str) -> dict[str, Any]:
    """What is established about one item, dimension by dimension and context by context, from what is recorded now."""
    obj = resolve_pin(store, pin_text)
    c = obj["content"]
    notes: dict[str, Any] = {"item": obj["object_id"], "revision": obj["revision"], "pin": pin_text, "object_type": obj["object_type"], "label": c.get("label"),
                             "lifecycle": c.get("lifecycle") or "ACTIVE", "promotion": c.get("promotion") or None}
    if obj["object_type"] == "DomainWorkflow":
        spec = c.get("spec") or {}
        notes.update(validation_status=spec.get("validation_status", "EXTRACTED"), scope=spec.get("scope"), exclusions=spec.get("exclusions"),
                     dimensions="not assessed: a domain workflow's content is validated through the knowledge lifecycle, and none is recorded for this one",
                     statement="Nothing here is verified; the workflow is an execution contract that has not been validated.")
        return notes
    fid = lifecycle.source_fidelity(store, obj["object_id"])
    comp = lifecycle.computational_reproducibility(store, obj["object_id"])
    assessments = (c.get("validation") or {}).get("validity_assessments", [])
    notes["dimensions"] = {
        "source_fidelity": {"status": fid["source_fidelity"], "elements": [{"element": d["element"], "quote_verified": d["quote_verified"], "meaning_verified": d["meaning_verified"],
                                                                           "eca_verdict": d["eca_verdict"], "disputed": d["disputed"]} for d in fid["elements"]]},
        "computational_reproducibility": {"status": comp["computational_reproducibility"], "packages": [
            {"package": p["item"], "status": p["computational_reproducibility"], "outputs": p["outputs"]} for p in comp["packages"]]},
        "methodological_validity": [{"context": a["context"], "elements": a.get("elements"), "verdict": a["verdict"], "confirmed_by_researcher": a["confirmed_by_researcher"],
                                     "methodological_basis": any(b.get("methodological_literature") for b in a["evidence_basis"]),
                                     "known_criticisms": a.get("known_criticisms", []), "blockers": a.get("blockers", [])} for a in assessments]
        or "not assessed in any context"}
    notes["known_criticisms_open"] = [k for a in assessments for k in a.get("known_criticisms", []) if k.get("status") == "open"]
    for p in comp["packages"]:
        pkg = store.get_object(p["item"])
        if pkg:
            notes.setdefault("entitlement", {})[p["item"]] = {k: v for k, v in entitlement_of(pkg["content"]).items() if k != "checked_at"}
    notes["statement"] = "This lists what is recorded. A context that is not listed has not been assessed, and nothing is claimed for it."
    return notes


def render_notes(notes: dict[str, Any]) -> str:
    lines = [f"# {notes['item']} (revision {notes['revision']})", "", f"{notes.get('label')}  -  lifecycle {notes['lifecycle']}", ""]
    if "validation_status" in notes:
        lines += [f"Domain workflow, validation status **{notes['validation_status']}**.", "", notes["dimensions"], "", notes["statement"]]
        return "\n".join(lines) + "\n"
    d = notes["dimensions"]
    lines += ["## Source fidelity", f"**{d['source_fidelity']['status']}**"] + [
        f"- {e['element']}: quote verified {e['quote_verified']}, meaning verified {e['meaning_verified']}, adjudication {e['eca_verdict']}" for e in d["source_fidelity"]["elements"]]
    lines += ["", "## Computational reproducibility", f"**{d['computational_reproducibility']['status']}**"] + [
        f"- {p['package']}: {p['status']}" for p in d["computational_reproducibility"]["packages"]]
    lines += ["", "## Methodological validity (per context)"]
    mv = d["methodological_validity"]
    if isinstance(mv, str):
        lines.append(mv)
    for a in (mv if isinstance(mv, list) else []):
        lines.append(f"- {json.dumps(a['context'], sort_keys=True)}: **{a['verdict']}** (confirmed by researcher: {a['confirmed_by_researcher']}; methodological basis: {a['methodological_basis']})")
    if notes.get("known_criticisms_open"):
        lines += ["", "## Open known criticisms"] + [f"- {k.get('ref')}" for k in notes["known_criticisms_open"]]
    lines += ["", notes["statement"]]
    return "\n".join(lines) + "\n"
