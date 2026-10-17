"""Canonical JSON hashing, HMAC and strict JSON parsing.

SHA-256 digests (``canonical_json_hash``, reused unchanged from the v8.4.4 harness) give
*integrity* only: anyone able to rewrite data can recompute them. Origin authenticity across an
untrusted channel is provided by ``hmac_hex`` with a key that never leaves the D13 host.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

# Reused from v8.4.4 (vendored byte-identical; see legacy/VENDORED.json).
from research_harness.legacy.v8_state_integrity import (  # noqa: F401  (re-exported)
    atomic_write_json,
    atomic_write_text,
    canonical_json_hash,
    project_file_lock,
)

MAX_JSON_DEPTH = 32


def canonical_bytes(obj: Any) -> bytes:
    """Same serialization as ``canonical_json_hash`` (sorted keys, compact, UTF-8)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hmac_hex(key: bytes, obj: Any) -> str:
    return hmac.new(key, canonical_bytes(obj), hashlib.sha256).hexdigest()


def hmac_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(str(a), str(b))


class StrictJSONError(ValueError):
    pass


def _no_duplicates(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise StrictJSONError(f"duplicate JSON key: {k!r}")
        seen[k] = v
    return seen


def _reject_constant(name):
    raise StrictJSONError(f"non-finite number not allowed: {name}")


def _depth(obj, level=0):
    if level > MAX_JSON_DEPTH:
        raise StrictJSONError(f"JSON nesting deeper than {MAX_JSON_DEPTH}")
    if isinstance(obj, dict):
        for v in obj.values():
            _depth(v, level + 1)
    elif isinstance(obj, list):
        for v in obj:
            _depth(v, level + 1)


def strict_json_loads(raw: bytes | str, max_bytes: int = 1_048_576) -> Any:
    """Parse untrusted JSON: size-limited, UTF-8 only, no duplicate keys, no NaN/Infinity."""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if len(raw) > max_bytes:
        raise StrictJSONError(f"input larger than {max_bytes} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise StrictJSONError(f"not valid UTF-8: {e}") from e
    if text.startswith("\ufeff"):
        text = text[1:]
    try:
        obj = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_reject_constant)
    except RecursionError as e:
        raise StrictJSONError("JSON nesting too deep") from e
    except json.JSONDecodeError as e:
        raise StrictJSONError(f"invalid JSON: {e}") from e
    _depth(obj)
    return obj
