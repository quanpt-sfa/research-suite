"""Dependency closure, atomic import, offline verification (plan v3, 4.2).

A library item is useless to a project that cannot run without the library. `dependency_closure` therefore collects
everything the item rests on:

1. objects reachable by outgoing edges, at the revisions the edges name;
2. objects named by a library pin written in any content field (playbook steps, protocols, policies, criticisms...);
3. adjudication records about any member (the readings that justify it travel with it);
4. content-addressed artifacts referenced anywhere (full texts, section sidecars, policy snapshots, replication
   files, expected outputs), each with its digest;
5. all earlier revisions of every member, so that revision numbers and digests are identical in the project.

`use_in_project` writes all of it in one transaction or none of it. The manifest it stores lets `verify_closure`
check the project against the recorded digests without opening the library.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

from research_harness.core.canonical import canonical_json_hash
from research_harness.knowledge.common import WRITE_ROLES, KnowledgeError, engine_origin
from research_harness.knowledge.library import make_pin, open_library, parse_pin, pins_in, resolve_pin
from research_harness.state.store import StateStore

ARTIFACT_KEYS = frozenset({"artifact_sha256", "blob_sha256", "sections_sha256", "source_sha256", "sidecar_sha256",
                           "file_sha256", "snapshot_sha256", "sha256"})
_SHA = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_KIND = "library_import_manifest"
LIBRARY_IMPORTED = "LIBRARY_IMPORTED"


def artifact_digests_in(value: Any) -> set[str]:
    """Digests of content-addressed blobs referenced by a content structure (whitelisted keys only: a hash of a
    quote or of a text is not necessarily a stored blob)."""
    out: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            if k in ARTIFACT_KEYS and isinstance(v, str) and _SHA.match(v):
                out.add(v)
            else:
                out |= artifact_digests_in(v)
    elif isinstance(value, list):
        for v in value:
            out |= artifact_digests_in(v)
    return out


@dataclass(frozen=True)
class ClosureManifest:
    root_pin: str
    entries: tuple[dict[str, Any], ...]
    artifacts: tuple[dict[str, Any], ...]

    @property
    def closure_digest(self) -> str:
        return canonical_json_hash({"root": self.root_pin,
                                    "entries": [[e["object_id"], e["revision"], e["content_digest"], e["revision_digest"]] for e in self.entries],
                                    "artifacts": [a["sha256"] for a in self.artifacts]})

    def to_dict(self) -> dict[str, Any]:
        return {"root_pin": self.root_pin, "closure_digest": self.closure_digest, "entries": list(self.entries),
                "artifacts": list(self.artifacts)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ClosureManifest":
        m = cls(d["root_pin"], tuple(d["entries"]), tuple(d["artifacts"]))
        if d.get("closure_digest") != m.closure_digest:
            raise KnowledgeError("MANIFEST_DIGEST_MISMATCH", "the stored closure manifest does not match its own digest")
        return m


def dependency_closure(lib: StateStore, pin_text: str) -> ClosureManifest:
    """The closure of a pinned library item (see the module docstring). Raises if anything it needs is missing."""
    root = resolve_pin(lib, pin_text)
    seen: dict[tuple[str, int], dict[str, Any]] = {}
    queue: list[tuple[str, int, str]] = [(root["object_id"], root["revision"], "root")]
    blobs: set[str] = set()
    with lib.read() as conn:
        while queue:
            oid, rev, via = queue.pop()
            if (oid, rev) in seen:
                continue
            obj = lib.get_object(oid, rev, conn=conn)
            if obj is None:
                raise KnowledgeError("CLOSURE_OBJECT_MISSING", f"{oid}@{rev} is required but is not in the library (via {via})")
            seen[(oid, rev)] = {"object_id": oid, "revision": rev, "object_type": obj["object_type"],
                                "content_digest": obj["content_digest"], "revision_digest": obj["revision_digest"], "via": via}
            if rev > 1:
                queue.append((oid, rev - 1, "history"))
            for e in lib.edges_from(conn, oid, rev):
                queue.append((e["target_id"], e["target_revision"], f"edge:{e['relation']} from {oid}@{rev}"))
            for p in pins_in(obj["content"]):
                pid, prev, _ = parse_pin(p)
                resolve_pin(lib, p)  # a pin that no longer resolves breaks the closure
                queue.append((pid, prev, f"pin in {oid}@{rev}"))
            for e in lib.edges_to(conn, oid, rev):
                src = lib.get_object(e["source_id"], e["source_revision"], conn=conn)
                if src and src["object_type"] == "Adjudication":
                    queue.append((e["source_id"], e["source_revision"], f"adjudication of {oid}@{rev}"))
                elif (src and src["object_type"] == "EvidencePassage" and e["relation"] == "derived_from"
                      and src["content"].get("extraction_key")):
                    queue.append((e["source_id"], e["source_revision"], f"extraction of {oid}@{rev}"))
            blobs |= artifact_digests_in(obj["content"])
    artifacts = []
    for sha in sorted(blobs):
        if not lib.artifacts.has(sha):
            raise KnowledgeError("CLOSURE_ARTIFACT_MISSING", f"artifact {sha[:16]} is referenced by the closure but is not stored")
        if not lib.artifacts.verify(sha):
            raise KnowledgeError("CLOSURE_ARTIFACT_CORRUPT", f"artifact {sha[:16]} does not match its digest")
        artifacts.append({"sha256": sha, "size": lib.artifacts.path_for(sha).stat().st_size})
    entries = tuple(seen[k] for k in sorted(seen))
    return ClosureManifest(make_pin(root["object_id"], root["revision"], root["content_digest"]), entries, tuple(artifacts))


def _import_order(lib: StateStore, entries: tuple[dict[str, Any], ...]) -> list[tuple[str, int]]:
    """Members ordered so that every revision follows its previous revision and the targets of its edges."""
    nodes = {(e["object_id"], e["revision"]) for e in entries}
    deps: dict[tuple[str, int], set[tuple[str, int]]] = {n: set() for n in nodes}
    with lib.read() as conn:
        for oid, rev in nodes:
            if rev > 1:
                deps[(oid, rev)].add((oid, rev - 1))
            for e in lib.edges_from(conn, oid, rev):
                t = (e["target_id"], e["target_revision"])
                if t not in nodes:
                    raise KnowledgeError("CLOSURE_INCOMPLETE", f"{oid}@{rev} points at {t[0]}@{t[1]}, which is not in the closure")
                deps[(oid, rev)].add(t)
    order: list[tuple[str, int]] = []
    ready = sorted(n for n, d in deps.items() if not d)
    done: set[tuple[str, int]] = set()
    while ready:
        n = ready.pop(0)
        order.append(n)
        done.add(n)
        for m in sorted(deps):
            if m not in done and m not in ready and deps[m] <= done:
                ready.append(m)
        ready.sort()
    if len(order) != len(nodes):
        raise KnowledgeError("CLOSURE_CYCLE", "the closure contains a dependency cycle")
    return order


def use_in_project(project: StateStore, token: Optional[str], lib: StateStore, pin_text: str, *,
                   allow_inactive: bool = False) -> dict[str, Any]:
    """Import a library item with its whole closure into `project`, in one transaction. All or nothing."""
    manifest = dependency_closure(lib, pin_text)
    root = resolve_pin(lib, pin_text)
    state = (root["content"].get("lifecycle") or "ACTIVE")
    if (lib.get_object(root["object_id"]) or {}).get("lifecycle_status") == "WITHDRAWN":
        state = "WITHDRAWN"
    if state in {"WITHDRAWN", "SUPERSEDED"} and not allow_inactive:
        raise KnowledgeError("ITEM_NOT_ACTIVE", f"{root['object_id']} is {state}; it is not offered for new use")
    manifest_id = "IMPORT_" + manifest.closure_digest[:12].upper()

    # Everything that can fail on the library side fails before the project is touched.
    payload: dict[tuple[str, int], dict[str, Any]] = {}
    with lib.read() as conn:
        for e in manifest.entries:
            obj = lib.get_object(e["object_id"], e["revision"], conn=conn)
            payload[(e["object_id"], e["revision"])] = {
                "obj": obj, "edges": [(x["relation"], x["target_id"], x["target_revision"], json.loads(x["provenance"]))
                                      for x in lib.edges_from(conn, e["object_id"], e["revision"])]}
    order = _import_order(lib, manifest.entries)
    blobs = {a["sha256"]: lib.artifacts.get_bytes(a["sha256"]) for a in manifest.artifacts}

    for sha, data in blobs.items():
        if project.artifacts.put_bytes(data) != sha:
            raise KnowledgeError("ARTIFACT_COPY_MISMATCH", f"artifact {sha[:16]} changed while being copied")

    origin_extra = {"pin": manifest.root_pin, "library_closure_digest": manifest.closure_digest}
    imported = skipped = 0
    with project.write_tx(token=token, require_roles=WRITE_ROLES) as tx:
        if tx.head(manifest_id):
            return {"status": "ALREADY_IMPORTED", "manifest_id": manifest_id, "root_pin": manifest.root_pin,
                    "closure_digest": manifest.closure_digest}
        for oid, rev in order:
            entry = payload[(oid, rev)]
            obj = entry["obj"]
            head = tx.head(oid)
            if head and head["head_revision"] >= rev:
                have = tx.get(oid, rev)
                if have["content_digest"] != obj["content_digest"] or have["revision_digest"] != obj["revision_digest"]:
                    raise KnowledgeError("IMPORT_CONFLICT", f"the project already has {oid}@{rev} with different content")
                skipped += 1
                continue
            if (head["head_revision"] if head else 0) != rev - 1:
                raise KnowledgeError("IMPORT_CONFLICT", f"the project has {oid} at revision {head['head_revision'] if head else 0}; "
                                     f"cannot import revision {rev}")
            res = tx.add_revision(object_id=oid, object_type=obj["object_type"], content=obj["content"], edges=entry["edges"],
                                  origin={"kind": LIBRARY_IMPORTED, "authenticated_actor": tx.actor_id, **origin_extra,
                                          "library_revision_digest": obj["revision_digest"]})
            if res["content_digest"] != obj["content_digest"] or res["revision_digest"] != obj["revision_digest"]:
                raise KnowledgeError("IMPORT_DIGEST_MISMATCH", f"{oid}@{rev} was imported with a different digest")
            imported += 1
        root_id, root_rev = root["object_id"], root["revision"]
        tx.add_revision(
            object_id=manifest_id, object_type="KnowledgeItem",
            content={"label": f"Library import of {root_id}@{root_rev}", "kind": MANIFEST_KIND, "generated_by": "import",
                     "pins": [manifest.root_pin], "lifecycle": "ACTIVE", "body": {"manifest": manifest.to_dict(), "imported_at": tx.at}},
            edges=[("derived_from", root_id, root_rev, {"origin": "library_import"})],
            origin=engine_origin("library_use", tx.actor_id, **origin_extra))
        tx.audit("library_use", "IMPORTED", {"manifest_id": manifest_id, "root_pin": manifest.root_pin,
                                              "objects_imported": imported, "objects_already_present": skipped,
                                              "artifacts": len(blobs)})
    return {"status": "IMPORTED", "manifest_id": manifest_id, "root_pin": manifest.root_pin, "root_id": root_id,
            "root_revision": root_rev, "closure_digest": manifest.closure_digest, "objects_imported": imported,
            "objects_already_present": skipped, "artifacts": len(blobs)}


def manifests_in(project: StateStore) -> list[dict[str, Any]]:
    out = []
    with project.read() as conn:
        for r in conn.execute("SELECT object_id FROM object_heads WHERE object_type='KnowledgeItem' ORDER BY object_id"):
            obj = project.get_object(r[0], conn=conn)
            if obj["content"].get("kind") == MANIFEST_KIND:
                out.append(obj)
    return out


def verify_closure(project: StateStore, manifest_id: Optional[str] = None) -> dict[str, Any]:
    """Check that everything a stored manifest lists is present in the project and matches its digest.

    Reads only the project. The library is never opened, so this works after the library has gone.
    """
    problems: list[dict[str, Any]] = []
    checked: list[str] = []
    manifests = [m for m in manifests_in(project) if manifest_id in (None, m["object_id"])]
    if manifest_id and not manifests:
        raise KnowledgeError("MANIFEST_UNKNOWN", f"{manifest_id} is not an import manifest of this project")
    for m in manifests:
        try:
            man = ClosureManifest.from_dict(m["content"]["body"]["manifest"])
        except KnowledgeError as exc:
            problems.append({"manifest": m["object_id"], "problem": exc.code})
            continue
        checked.append(m["object_id"])
        for e in man.entries:
            have = project.get_object(e["object_id"], e["revision"])
            if have is None:
                problems.append({"manifest": m["object_id"], "object": f"{e['object_id']}@{e['revision']}", "problem": "MISSING"})
            elif have["content_digest"] != e["content_digest"] or have["revision_digest"] != e["revision_digest"]:
                problems.append({"manifest": m["object_id"], "object": f"{e['object_id']}@{e['revision']}", "problem": "DIGEST_MISMATCH"})
        for a in man.artifacts:
            if not project.artifacts.has(a["sha256"]):
                problems.append({"manifest": m["object_id"], "artifact": a["sha256"][:16], "problem": "ARTIFACT_MISSING"})
            elif not project.artifacts.verify(a["sha256"]):
                problems.append({"manifest": m["object_id"], "artifact": a["sha256"][:16], "problem": "ARTIFACT_CORRUPT"})
    return {"status": "FAILED" if problems else ("VERIFIED" if checked else "NO_IMPORTS"), "manifests": checked, "problems": problems}


def check_updates(project: StateStore, home: Any = None) -> dict[str, Any]:
    """Compare what the project imported with the library's current state. A missing library is a status, not an error."""
    try:
        lib = open_library(home)
    except KnowledgeError as exc:
        return {"status": "LIBRARY_UNAVAILABLE", "reason": exc.message or exc.code, "notices": []}
    notices: list[dict[str, Any]] = []
    for m in manifests_in(project):
        pin_text = m["content"]["body"]["manifest"]["root_pin"]
        oid, rev, _ = parse_pin(pin_text)
        head = lib.get_object(oid)
        if head is None:
            notices.append({"manifest": m["object_id"], "pin": pin_text, "notice": "REMOVED_FROM_LIBRARY"})
            continue
        if head["revision"] > rev:
            notices.append({"manifest": m["object_id"], "pin": pin_text, "notice": "NEWER_REVISION", "library_revision": head["revision"]})
        state = "WITHDRAWN" if head.get("lifecycle_status") == "WITHDRAWN" else (head["content"].get("lifecycle") or "ACTIVE")
        if state != "ACTIVE":
            notices.append({"manifest": m["object_id"], "pin": pin_text, "notice": f"ITEM_{state}", "library_revision": head["revision"]})
    return {"status": "UPDATES_AVAILABLE" if notices else "CURRENT", "notices": notices}
