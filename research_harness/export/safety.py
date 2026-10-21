"""What may not leave the store: secrets, and paths that would write into a store, .git or the tracked tree (WP2, Amendment 1 A9)."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from research_harness.core.errors import HarnessError
from research_harness.export.contracts import SECRET_ENV_NAMES

_PATTERNS = {
    "api_key_token": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "bearer_token": re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{20,}"),
    "key_assignment": re.compile(r"(?i)\b[A-Z_]*API[_-]?KEY\b\s*[:=]\s*\S{8,}"),
    "private_key_block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}


def secret_findings(text: str) -> list[str]:
    """Names of the secret patterns found in `text` (never the values), including the value of a configured API key."""
    found = [name for name, rx in _PATTERNS.items() if rx.search(text)]
    for env in SECRET_ENV_NAMES:
        value = os.environ.get(env)
        if value and len(value) >= 8 and value in text:
            found.append(f"value_of_{env}")
    return found


def assert_no_secrets(files: dict[str, bytes]) -> None:
    for name, data in sorted(files.items()):
        hits = secret_findings(data.decode("utf-8", "replace"))
        if hits:
            raise HarnessError("EXPORT_SECRET_DETECTED", f"{name} contains what looks like a secret ({', '.join(hits)}); nothing was written",
                               file=name, patterns=hits)


def _git_root(path: Path) -> Path | None:
    for p in [path, *path.parents]:
        if (p / ".git").exists():
            return p
    return None


def check_out_dir(out: Path | str, *, into_repo: bool = False) -> Path:
    """The resolved output directory, or EXPORT_PATH_UNSAFE / EXPORT_DIR_NOT_EMPTY.

    Refused: a symlink, a directory inside a store (any directory holding `state.sqlite3`), inside `.git`, or inside a git repository's tracked
    tree (a path the repository ignores, such as `sample/`, is fine; `into_repo` allows the tracked tree for artefacts the tests read)."""
    raw = Path(out)
    if raw.is_symlink():
        raise HarnessError("EXPORT_PATH_UNSAFE", f"{raw} is a symbolic link")
    resolved = raw.resolve()
    for p in [resolved, *resolved.parents]:
        if (p / "state.sqlite3").exists():
            raise HarnessError("EXPORT_PATH_UNSAFE", f"{resolved} is inside the store {p}")
        if p.name == ".git":
            raise HarnessError("EXPORT_PATH_UNSAFE", f"{resolved} is inside .git")
    if resolved.exists() and (not resolved.is_dir() or any(resolved.iterdir())):
        raise HarnessError("EXPORT_DIR_NOT_EMPTY", f"{resolved} exists and is not an empty directory")
    root = _git_root(resolved)
    if root is not None and not into_repo:
        try:
            r = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", "--", str(resolved)], capture_output=True, timeout=30)
            ignored = r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            ignored = False
        if not ignored:
            raise HarnessError("EXPORT_PATH_UNSAFE", f"{resolved} is inside the repository {root}; choose a directory outside it, an ignored one, "
                               "or pass --into-repo for an artefact the repository is meant to hold")
    return resolved
