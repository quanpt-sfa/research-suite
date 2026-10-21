"""Which SHA-256 values in stored content name a blob in the content-addressed store (contract C1, WP1).

A digest of a text, a quote or a JSON document identifies content; it is not necessarily a stored file. Only the keys
below name blobs, at any depth of an object's content. The bare key `sha256` names a blob only inside an entry of a
`files` list (replication packages, playbook assets); elsewhere it is a fingerprint (e.g. the digest of a Gate L rubric).

One function serves the library closure, `StateStore.recover` and the integrity check, so the three cannot disagree on
what is referenced (before this, recover and integrity looked at top-level keys only: nested blobs were reported as
orphans and a missing nested blob was not reported).
"""
from __future__ import annotations

import re
from typing import Any

BLOB_KEYS = frozenset({"artifact_sha256", "blob_sha256", "sections_sha256", "source_sha256", "sidecar_sha256", "file_sha256",
                       "snapshot_sha256"})
FILE_ENTRY_KEY = "sha256"   # a blob only inside an entry of a `files` list
FINGERPRINT_KEYS = frozenset({"text_sha256", "passage_norm_sha256", "passage_head_sha256", "passage_tail_sha256", "state_sha256",
                              "quote_sha256", "annotation_sha256", "origin_sha256", "prompt_sha256", "context_manifest_sha256"})
_SHA = re.compile(r"^[0-9a-f]{64}$")


def blob_digests_in(value: Any, _file_entry: bool = False) -> set[str]:
    """Digests of content-addressed blobs referenced anywhere in a content structure."""
    out: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            if (k in BLOB_KEYS or (_file_entry and k == FILE_ENTRY_KEY)) and isinstance(v, str) and _SHA.match(v):
                out.add(v)
            elif k == "files" and isinstance(v, list):
                for entry in v:
                    out |= blob_digests_in(entry, _file_entry=True)
            else:
                out |= blob_digests_in(v)
    elif isinstance(value, list):
        for v in value:
            out |= blob_digests_in(v)
    return out
