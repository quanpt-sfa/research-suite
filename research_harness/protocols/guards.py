"""Static guards applied to every untrusted document before any state is touched."""
from __future__ import annotations

import re
from typing import Any, Iterator

from research_harness.core.model import FORBIDDEN_KEYS, SECRET_KEY_RE

_SECRET_VALUE_RES = [
    ("PRIVATE_KEY_BLOCK", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("OPENAI_STYLE_KEY", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}")),
    ("AWS_ACCESS_KEY_ID", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GITHUB_TOKEN", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("HARNESS_TOKEN", re.compile(r"\brhk_[A-Za-z0-9_\-]{20,}")),
]


def _walk(obj: Any, path: str = "$") -> Iterator[tuple[str, str | None, Any]]:
    """Yield (path, key, value) for every dict entry and (path, None, value) for list items."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}"
            yield p, k, v
            yield from _walk(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            p = f"{path}[{i}]"
            yield p, None, v
            yield from _walk(v, p)


def forbidden_key_paths(doc: Any) -> list[str]:
    """Keys that only the server may set (identity, time, approval, verification, digests)."""
    paths = []
    for p, k, _ in _walk(doc):
        if k is not None and k in FORBIDDEN_KEYS:
            if p.endswith(".subject.revision") or p.endswith(".base_revision"):
                continue
            paths.append(p)
    return paths



def secret_findings(doc: Any) -> list[dict]:
    out = []
    for p, k, v in _walk(doc):
        if k is not None and SECRET_KEY_RE.search(k):
            out.append({"path": p, "reason": "SECRET_LIKE_KEY"})
        if isinstance(v, str) and len(v) <= 100_000:
            for name, rx in _SECRET_VALUE_RES:
                if rx.search(v):
                    out.append({"path": p, "reason": name})
                    break
    return out
