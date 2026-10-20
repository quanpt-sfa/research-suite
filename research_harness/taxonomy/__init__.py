"""Shared controlled vocabularies for Research Suite 9.2.

The vocabularies are data (`*.json` next to this module), so extending them never needs a code release.
Each file is `{"version", "vocabularies": {name: [terms]}, "hierarchies": {name: {parent: [children]}}}`.

Unknown terms raise `TaxonomyError` with a hint to extend the taxonomy, rather than being accepted
silently (invariant I2: nothing is assumed).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

_DIR = Path(__file__).parent


class TaxonomyError(ValueError):
    """Raised for an unknown vocabulary or an unknown term."""


@lru_cache(maxsize=1)
def _load() -> tuple[dict[str, tuple[str, ...]], dict[str, dict[str, tuple[str, ...]]]]:
    vocabs: dict[str, tuple[str, ...]] = {}
    hierarchies: dict[str, dict[str, tuple[str, ...]]] = {}
    for path in sorted(_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for name, terms in (data.get("vocabularies") or {}).items():
            if name in vocabs:
                raise TaxonomyError(f"Vocabulary '{name}' defined twice (second definition in {path.name})")
            if len(set(terms)) != len(terms):
                raise TaxonomyError(f"Vocabulary '{name}' in {path.name} contains duplicate terms")
            vocabs[name] = tuple(terms)
        for name, tree in (data.get("hierarchies") or {}).items():
            if name in hierarchies:
                raise TaxonomyError(f"Hierarchy '{name}' defined twice (second definition in {path.name})")
            hierarchies[name] = {parent: tuple(children) for parent, children in tree.items()}
    return vocabs, hierarchies


def reload() -> None:
    """Drop the cached vocabularies (used by tests that edit the taxonomy)."""
    _load.cache_clear()


def vocabulary_names() -> list[str]:
    return sorted(_load()[0])


def vocabulary(name: str) -> tuple[str, ...]:
    vocabs, _ = _load()
    if name not in vocabs:
        raise TaxonomyError(f"Unknown vocabulary '{name}'. Available: {sorted(vocabs)}")
    return vocabs[name]


def validate_term(vocab: str, value: str) -> str:
    """Return `value` if it belongs to `vocab`; otherwise raise with a hint."""
    terms = vocabulary(vocab)
    if value not in terms:
        raise TaxonomyError(
            f"'{value}' is not a term of vocabulary '{vocab}' (allowed: {list(terms)}). "
            f"Extend research_harness/taxonomy/*.json if this is a genuinely new term."
        )
    return value


def is_term(vocab: str, value: str) -> bool:
    return value in vocabulary(vocab)


def hierarchy(name: str) -> dict[str, tuple[str, ...]]:
    _, hierarchies = _load()
    if name not in hierarchies:
        raise TaxonomyError(f"Unknown hierarchy '{name}'. Available: {sorted(hierarchies)}")
    return dict(hierarchies[name])


def validate_domain(domain: str, subfield: Optional[str] = None) -> tuple[str, Optional[str]]:
    """Validate a `domain` and optionally a `subfield` that must belong to that domain."""
    tree = hierarchy("domain")
    if domain not in tree:
        raise TaxonomyError(f"Unknown domain '{domain}'. Allowed: {sorted(tree)}")
    if subfield is not None and subfield not in tree[domain]:
        raise TaxonomyError(f"Subfield '{subfield}' does not belong to domain '{domain}'. Allowed: {list(tree[domain])}")
    return domain, subfield


def validate_terms(vocab: str, values: Iterable[str]) -> list[str]:
    return [validate_term(vocab, v) for v in values]
