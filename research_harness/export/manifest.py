"""The export container: files, a deterministic manifest, a variable info file, writing and verifying (WP2)."""
from __future__ import annotations

import hashlib
import json
import platform
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from research_harness import __version__
from research_harness.core.canonical import canonical_json_hash
from research_harness.core.errors import HarnessError
from research_harness.export.contracts import EXPORTER_VERSION, INFO_NAME, MANIFEST_NAME
from research_harness.export.safety import assert_no_secrets, check_out_dir
from research_harness.state.store import StateStore, compute_state_digest


def digest_of(closure: list[list[Any]]) -> str:
    return canonical_json_hash(sorted(closure, key=lambda e: [str(x) for x in e]))


@dataclass
class Export:
    """What an exporter returns: content files (name -> bytes) and the facts the manifest records. No file system access, no clock."""
    kind: str
    scope: dict[str, Any]
    files: dict[str, bytes]
    closure: list[list[Any]]                 # [object_id, revision, revision_digest] / ["run", run_id, artifact] entries
    corpus_snapshot_id: str
    provenance_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    not_available: list[dict[str, str]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    runs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def closure_digest(self) -> str:
        return digest_of(self.closure)

    def manifest(self) -> dict[str, Any]:
        return {"exporter_version": EXPORTER_VERSION, "kind": self.kind, "scope": self.scope, "closure_digest": self.closure_digest,
                "corpus_snapshot_id": self.corpus_snapshot_id,
                "files": [{"path": n, "sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b)} for n, b in sorted(self.files.items())],
                "provenance_counts": {k: dict(sorted(v.items())) for k, v in sorted(self.provenance_counts.items())},
                "not_available": sorted(self.not_available, key=lambda e: e["file"]),
                "sources": sorted(self.sources, key=lambda e: e["source_id"]), "runs": sorted(self.runs, key=lambda e: e["run_id"])}

    def manifest_bytes(self) -> bytes:
        return (json.dumps(self.manifest(), indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")

    def all_files(self) -> dict[str, bytes]:
        return {**self.files, MANIFEST_NAME: self.manifest_bytes()}


def default_out(store: StateStore, export: Export) -> Path:
    """`<home>/exports/<project>/<kind>-<scope>/<closure digest, 12>/`: outside every store, named by what it contains."""
    scope = "+".join(str(v) for v in export.scope.values() if v)
    return store.paths.home / "exports" / store.project_id / f"{export.kind}-{scope}" / export.closure_digest[:12]


def write_export(store: StateStore, export: Export, out: Path | str | None = None, *, into_repo: bool = False) -> dict[str, Any]:
    files = export.all_files()
    assert_no_secrets(files)
    target = check_out_dir(out if out is not None else default_out(store, export), into_repo=into_repo)
    target.mkdir(parents=True, exist_ok=True)
    for name, data in sorted(files.items()):
        (target / name).write_bytes(data)
    with store.read() as conn:
        state = compute_state_digest(conn)
    info = {"exported_at": datetime.now(timezone.utc).isoformat(), "exporter_version": EXPORTER_VERSION, "suite_version": __version__,
            "python": sys.version.split()[0], "platform": platform.system(), "project_id": store.project_id, "state_sha256": state,
            "note": "variable data: excluded from the reproducibility of the export"}
    (target / INFO_NAME).write_bytes((json.dumps(info, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return {"out": str(target), "closure_digest": export.closure_digest, "files": sorted(files), "not_available": export.not_available,
            "provenance_counts": export.provenance_counts}


def verify_export(store: StateStore, directory: Path | str, rebuild: Callable[[dict[str, Any]], Export]) -> dict[str, Any]:
    """Are the files as written, and does the current store still produce exactly them?

    VERIFIED: the files match their recorded digests and re-rendering from the store gives the same bytes.
    FILES_MODIFIED: a file differs from the manifest (or is missing / unlisted).
    STATE_CHANGED: the files are intact but the store no longer produces them (the closure moved on)."""
    d = Path(directory)
    mpath = d / MANIFEST_NAME
    if not mpath.is_file():
        raise HarnessError("EXPORT_MANIFEST_MISSING", f"{d} has no {MANIFEST_NAME}")
    recorded = json.loads(mpath.read_text(encoding="utf-8"))
    modified, missing = [], []
    for f in recorded["files"]:
        p = d / f["path"]
        if not p.is_file():
            missing.append(f["path"])
        elif hashlib.sha256(p.read_bytes()).hexdigest() != f["sha256"]:
            modified.append(f["path"])
    listed = {f["path"] for f in recorded["files"]} | {MANIFEST_NAME, INFO_NAME}
    unlisted = sorted(p.name for p in d.iterdir() if p.name not in listed)
    if modified or missing or unlisted:
        return {"status": "FILES_MODIFIED", "modified": modified, "missing": missing, "unlisted": unlisted}
    now = rebuild(recorded["scope"])
    differs = sorted(n for n, b in now.all_files().items() if (d / n).read_bytes() != b) if set(now.all_files()) == {f["path"] for f in recorded["files"]} | {MANIFEST_NAME} else ["(file set differs)"]
    if differs:
        return {"status": "STATE_CHANGED", "differs": differs, "closure_digest_recorded": recorded["closure_digest"],
                "closure_digest_now": now.closure_digest, "corpus_snapshot_recorded": recorded["corpus_snapshot_id"],
                "corpus_snapshot_now": now.corpus_snapshot_id}
    return {"status": "VERIFIED", "closure_digest": recorded["closure_digest"], "files": len(recorded["files"])}
