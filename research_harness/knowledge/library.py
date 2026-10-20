"""The knowledge library: a dedicated Research Core store at `<home>/LIBRARY` (plan v3, 2.1 and 4.2).

It is an ordinary `StateStore`, so it has revisions, a content-addressed artifact store, edges, an audit log and the
same token authority as a project. What is different:

* every object in it has an id with the `LIB_` prefix, and a project cannot create such an id through a proposal;
  it can only receive them by importing a library item with its whole dependency closure (`closure.py`);
* items are referred to by a **pin**, `LIB:<object_id>@<revision>#<first 16 hex of the content digest>`. The digest
  makes a pin reproducible: if the item at that revision changed, the pin no longer resolves.

A project never needs the library to run. Import copies everything an item depends on into the project, so a missing
library is `LIBRARY_UNAVAILABLE` for update checks and nothing else.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Optional

from research_harness.knowledge.common import (
    LIB_PREFIX, LIBRARY_ID, WRITE_ROLES, KnowledgeError, digest16, engine_origin)
from research_harness.state.store import ProjectPaths, StateStore, default_home

PIN_RE = re.compile(r"LIB:(?P<id>[A-Z][A-Z0-9_]{1,63})@(?P<rev>[0-9]+)#(?P<digest>[0-9a-f]{16})")
_PIN_FULL = re.compile(r"^" + PIN_RE.pattern + r"$")


def library_paths(home: Path | str) -> ProjectPaths:
    return ProjectPaths(home, LIBRARY_ID)


def library_exists(home: Path | str) -> bool:
    try:
        return library_paths(home).db.is_file()
    except Exception:  # noqa: BLE001
        return False


def create_library(home: Path | str, owner_id: str = "library_owner") -> tuple[StateStore, str]:
    """Create `<home>/LIBRARY`. Returns the store and the owner token (shown once, like a project's)."""
    return StateStore.create(home, LIBRARY_ID, owner_id=owner_id)


def open_library(home: Optional[Path | str] = None) -> StateStore:
    """Open the library under `home`. Raises `LIBRARY_UNAVAILABLE` when it is absent or cannot be opened."""
    root = Path(home) if home is not None else default_home()
    if not library_exists(root):
        raise KnowledgeError("LIBRARY_UNAVAILABLE", f"no library at {library_paths(root).root}")
    try:
        return StateStore.open(root, LIBRARY_ID)
    except Exception as exc:  # noqa: BLE001
        raise KnowledgeError("LIBRARY_UNAVAILABLE", f"the library cannot be opened: {exc}") from exc


def make_pin(object_id: str, revision: int, content_digest: str) -> str:
    return f"LIB:{object_id}@{revision}#{digest16(content_digest)}"


def parse_pin(pin: str) -> tuple[str, int, str]:
    m = _PIN_FULL.match(pin or "")
    if not m:
        raise KnowledgeError("PIN_MALFORMED", f"{pin!r} is not LIB:<id>@<revision>#<16 hex>")
    return m.group("id"), int(m.group("rev")), m.group("digest")


def pin(lib: StateStore, object_id: str, revision: Optional[int] = None) -> str:
    obj = lib.get_object(object_id, revision)
    if not obj:
        raise KnowledgeError("OBJECT_UNKNOWN", f"{object_id}{'@' + str(revision) if revision else ''} is not in the library")
    return make_pin(object_id, obj["revision"], obj["content_digest"])


def resolve_pin(lib: StateStore, pin_text: str) -> dict[str, Any]:
    """The object a pin names, if it is still exactly that content. A changed item does not resolve."""
    oid, rev, dig = parse_pin(pin_text)
    obj = lib.get_object(oid, rev)
    if not obj:
        raise KnowledgeError("PIN_UNRESOLVED", f"{oid}@{rev} is not in the library")
    if digest16(obj["content_digest"]) != dig:
        raise KnowledgeError("PIN_DIGEST_MISMATCH", f"{oid}@{rev} no longer has the content digest the pin recorded")
    return obj


def pins_in(value: Any) -> Iterable[str]:
    """Every library pin written anywhere inside a content structure."""
    if isinstance(value, str):
        for m in PIN_RE.finditer(value):
            yield m.group(0)
    elif isinstance(value, dict):
        for v in value.values():
            yield from pins_in(v)
    elif isinstance(value, list):
        for v in value:
            yield from pins_in(v)


def add_object(lib: StateStore, token: Optional[str], object_id: str, object_type: str, content: dict[str, Any], *,
               edges: Iterable[tuple[str, str, int]] = (), operation: str = "library_add") -> dict[str, Any]:
    """Write one library object (a new revision if it exists). `edges` are `(relation, target_id, target_revision)`.

    Library ids carry the `LIB_` prefix. Targets must exist at the stated revision.
    """
    if not object_id.startswith(LIB_PREFIX):
        raise KnowledgeError("LIBRARY_ID_PREFIX", f"{object_id}: library objects must have ids starting with {LIB_PREFIX}")
    with lib.write_tx(token=token, require_roles=WRITE_ROLES) as tx:
        resolved = []
        for relation, tid, trev in edges:
            if tx.get(tid, trev) is None:
                raise KnowledgeError("EDGE_TARGET_UNKNOWN", f"{object_id} -> {tid}@{trev} does not exist in the library")
            resolved.append((relation, tid, trev, {"origin": operation}))
        res = tx.add_revision(object_id=object_id, object_type=object_type, content=content, edges=resolved,
                              origin=engine_origin(operation, tx.actor_id))
        tx.audit(operation, "RECORDED", {"object_id": object_id, "revision": res["revision"], "object_type": object_type})
    return {**res, "pin": make_pin(object_id, res["revision"], res["content_digest"])}
