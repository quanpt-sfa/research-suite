"""Content-addressed artifact store (filesystem).

SQLite cannot make a file write part of its transaction, so the protocol is:

1. stage:   write bytes to ``staging/<random>.tmp``, flush + fsync
2. publish: ``os.replace`` into ``artifacts/sha256/<ab>/<digest>``  (atomic, idempotent: the
            name *is* the content hash)
3. commit:  only then does the SQLite transaction record the digest

A crash between 2 and 3 leaves an orphan blob (harmless, reported and reclaimable by
``project recover``). Metadata can therefore never point at a file that was not fully written.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import time
from pathlib import Path

from research_harness.core.errors import HarnessError

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class ArtifactStore:
    def __init__(self, root: Path, staging: Path):
        self.root = Path(root)
        self.staging = Path(staging)
        self.root.mkdir(parents=True, exist_ok=True)
        self.staging.mkdir(parents=True, exist_ok=True)

    def path_for(self, digest: str) -> Path:
        if not _DIGEST_RE.match(digest or ""):
            raise HarnessError("ARTIFACT_DIGEST_INVALID", "digest must be 64 lowercase hex chars")
        return self.root / "sha256" / digest[:2] / digest

    def path(self, digest: str) -> Path:
        return self.path_for(digest)

    def exists(self, digest: str) -> bool:
        return self.path_for(digest).is_file()

    def has(self, digest: str) -> bool:
        return self.exists(digest)

    def put_bytes(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        dest = self.path_for(digest)
        if dest.is_file():
            return digest
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.staging / f"{secrets.token_hex(8)}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, dest)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        return digest

    def put(self, data: bytes) -> str:
        return self.put_bytes(data)

    def get_bytes(self, digest: str) -> bytes:
        data = self.path_for(digest).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise HarnessError("ARTIFACT_CORRUPT", digest=digest)
        return data

    def verify(self, digest: str) -> bool:
        try:
            self.get_bytes(digest)
            return True
        except (OSError, HarnessError):
            return False

    def blobs(self):
        base = self.root / "sha256"
        if base.is_dir():
            for p in sorted(base.glob("*/*")):
                if p.is_file() and _DIGEST_RE.match(p.name):
                    yield p.name

    def stale_staging(self, max_age_seconds: float = 0.0):
        now = time.time()
        return [p for p in sorted(self.staging.glob("*.tmp")) if now - p.stat().st_mtime >= max_age_seconds]
