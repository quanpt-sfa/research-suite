"""Corpus intake: papers the researcher is entitled to use become Sources with stored text and verified passages.

* `import_source` stores the text in the content-addressed store (never inline in the database), records its hash,
  and stores the detected section structure as a sidecar. The paper stays on this machine: nothing here sends it
  anywhere.
* `add_passage` creates an `EvidencePassage` for a span of that stored text. The span is checked against the text
  (`source_match`), and a passage whose quote is not in the stored text is refused. A passage is a *location in a
  source*, not an interpretation of it; whether a statement is a fair reading of it is Evidence-Claim Adjudication.
* PDF text extraction is optional (`pypdf`); plain text and Markdown need nothing.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from research_harness.knowledge.common import (
    WRITE_ROLES, KnowledgeError, engine_origin, head_or_raise, id_fragment, scope_id)
from research_harness.state.store import StateStore
from research_harness.workflows import quotes
from research_harness.workflows.fulltext import body_of, text_sha256
from research_harness.workflows.sections import detect_sections

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".text"}


def read_text_file(path: Path | str) -> tuple[str, dict[str, Any]]:
    """Text of a file and how it was obtained. PDFs need `pypdf`; its absence is an error, not an empty text."""
    p = Path(path)
    if not p.is_file():
        raise KnowledgeError("FILE_NOT_FOUND", f"{p} is not a file")
    if p.suffix.lower() == ".pdf":
        try:
            import pypdf  # type: ignore
        except ImportError as exc:
            raise KnowledgeError("PDF_SUPPORT_MISSING", "reading a PDF needs `pip install pypdf` (or supply the text)") from exc
        reader = pypdf.PdfReader(str(p))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        return text, {"method": "pypdf", "pypdf_version": getattr(pypdf, "__version__", None), "pages": len(reader.pages),
                      "file_name": p.name}
    if p.suffix.lower() in TEXT_SUFFIXES:
        return p.read_text(encoding="utf-8"), {"method": "text", "file_name": p.name}
    raise KnowledgeError("UNSUPPORTED_FILE_TYPE", f"{p.suffix or p.name}: supply .pdf, .txt or .md")


def source_id_for(citation_key: str, prefix: str = "SRC_") -> str:
    return f"{prefix}{id_fragment(citation_key, 50)}"


BIBLIOGRAPHIC_FIELDS = {"title", "authors", "year", "venue", "doi", "abstract", "license_note", "bibliographic_identity", "document_type", "review_status",
                        "first_public_date", "date_evidence", "version_id", "work_id", "version_relations", "relation_to_researcher", "acquisition_note"}
DOCUMENT_TYPES = ("journal_article", "working_paper", "preprint", "conference_paper", "dissertation", "report", "book_chapter", "other")
REVIEW_STATUSES = ("NOT_REVIEWED", "UNDER_REVIEW", "ACCEPTED", "PUBLISHED", "UNKNOWN")
TIERS = ("FULL_TEXT", "ABSTRACT_ONLY", "METADATA_ONLY")


def _check_bibliographic(b: dict[str, Any]) -> None:
    unknown = set(b) - BIBLIOGRAPHIC_FIELDS
    if unknown:
        raise KnowledgeError("UNKNOWN_SOURCE_FIELDS", f"unsupported Source fields: {sorted(unknown)}")
    if b.get("document_type") not in (None, *DOCUMENT_TYPES):
        raise KnowledgeError("DOCUMENT_TYPE_UNKNOWN", f"document_type must be one of {DOCUMENT_TYPES}")
    if b.get("review_status") not in (None, *REVIEW_STATUSES):
        raise KnowledgeError("REVIEW_STATUS_UNKNOWN", f"review_status must be one of {REVIEW_STATUSES}")
    if b.get("relation_to_researcher") not in (None, "INDEPENDENT", "OWN_PRIOR_WORK"):
        raise KnowledgeError("RELATION_UNKNOWN", "relation_to_researcher must be INDEPENDENT or OWN_PRIOR_WORK")
    d = b.get("first_public_date")
    if d is not None:
        import re
        if not re.match(r"^[0-9]{4}(-[0-9]{2}(-[0-9]{2})?)?$", str(d)):
            raise KnowledgeError("DATE_INVALID", "first_public_date is YYYY, YYYY-MM or YYYY-MM-DD")
        if not b.get("date_evidence"):
            raise KnowledgeError("DATE_EVIDENCE_REQUIRED", "a first public date needs its evidence (the repository posting record, a DOI registration), "
                                 "not the text of the paper itself")


def source_tier(content: dict[str, Any]) -> str:
    """What the suite holds of a Source: its full text, an abstract only, or metadata only. Only the first can support or challenge anything."""
    if content.get("fulltext_status") == "AVAILABLE" or content.get("artifact_sha256"):
        return "FULL_TEXT"
    return "ABSTRACT_ONLY" if content.get("abstract") else "METADATA_ONLY"


def import_metadata_only(store: StateStore, token: Optional[str], *, citation_key: str, title: str, source_id: Optional[str] = None,
                         id_prefix: str = "SRC_", **bibliographic: Any) -> dict[str, Any]:
    """Record a paper the suite does not hold: bibliographic metadata, and an abstract if the researcher has one. It is `MISSING` or `ABSTRACT_ONLY`
    and has no text, so no passage can be made from it and it can never support or challenge a claim; it can be listed as possibly relevant and not read."""
    if not title or not title.strip():
        raise KnowledgeError("TITLE_REQUIRED", "a metadata-only source needs at least a title")
    bibliographic["title"] = title.strip()
    _check_bibliographic(bibliographic)
    oid = scope_id(store, source_id or source_id_for(citation_key, id_prefix))
    content = {"label": title.strip(), "citation_key": citation_key, "fulltext_status": "ABSTRACT_ONLY" if bibliographic.get("abstract") else "MISSING",
               **{k: v for k, v in bibliographic.items() if v is not None}}
    with store.write_tx(token=token, require_roles=WRITE_ROLES) as tx:
        head = tx.head(oid)
        if head and head["object_type"] != "Source":
            raise KnowledgeError("OBJECT_TYPE_MISMATCH", f"{oid} exists and is a {head['object_type']}")
        if head:
            prev = tx.get(oid)
            if prev["content"].get("artifact_sha256"):
                raise KnowledgeError("SOURCE_HAS_TEXT", f"{oid} already has stored text; metadata cannot replace it")
            if prev["content"] == content:
                return {"object_id": oid, "revision": prev["revision"], "created": False, "tier": source_tier(content)}
        res = tx.add_revision(object_id=oid, object_type="Source", content=content, edges=[],
                              origin=engine_origin("corpus_import_metadata", tx.actor_id, citation_key=citation_key))
        tx.audit("corpus_import_metadata", "RECORDED", {"object_id": oid, "revision": res["revision"]})
    return {"object_id": oid, "revision": res["revision"], "created": True, "tier": source_tier(content)}


def set_excluded(store: StateStore, token: Optional[str], source_id: str, reason: Optional[str]) -> dict[str, Any]:
    """Take a source out of the corpus (with the researcher's reason), or put it back (`reason=None`). A new revision: history is kept, and the corpus
    snapshot changes, so conclusions that rest on not finding something are marked stale."""
    if reason is not None and not reason.strip():
        raise KnowledgeError("REASON_REQUIRED", "say why the source is excluded")
    with store.write_tx(token=token, require_roles=("owner", "author")) as tx:
        head = tx.head(source_id)
        if not head or head["object_type"] != "Source":
            raise KnowledgeError("SOURCE_NOT_FOUND", f"{source_id} is not a Source in this store")
        cur = tx.get(source_id)
        c = {k: v for k, v in cur["content"].items() if k != "excluded"}
        if reason is not None:
            c["excluded"] = {"reason": reason.strip(), "by": tx.actor_id, "at": tx.at}
        if (reason is None and not cur["content"].get("excluded")) or (reason is not None and (cur["content"].get("excluded") or {}).get("reason") == reason.strip()):
            return {"object_id": source_id, "revision": cur["revision"], "changed": False, "excluded": reason is not None}
        rows = tx.conn.execute("SELECT relation,target_id,target_revision,provenance FROM edges WHERE source_id=? AND source_revision=?",
                               (source_id, cur["revision"])).fetchall()
        res = tx.add_revision(object_id=source_id, object_type="Source", content=c, expected_head=cur["revision"],
                              edges=[(r[0], r[1], r[2], json.loads(r[3])) for r in rows],
                              origin=engine_origin("corpus_exclude" if reason is not None else "corpus_include", tx.actor_id))
        tx.audit("corpus_exclude" if reason is not None else "corpus_include", "RECORDED", {"object_id": source_id, "revision": res["revision"]})
    return {"object_id": source_id, "revision": res["revision"], "changed": True, "excluded": reason is not None}


def import_source(store: StateStore, token: Optional[str], *, citation_key: str, text: str,
                  source_id: Optional[str] = None, id_prefix: str = "SRC_", extraction: Optional[dict] = None,
                  **bibliographic: Any) -> dict[str, Any]:
    """Store `text` as the full text of a Source (new revision if the Source exists) and return its reference.

    `bibliographic` accepts the optional Source fields (title, authors, year, venue, doi, abstract, license_note).
    """
    if not text or not text.strip():
        raise KnowledgeError("EMPTY_TEXT", "a Source needs non-empty text")
    _check_bibliographic(bibliographic)
    oid = scope_id(store, source_id or source_id_for(citation_key, id_prefix))
    data = text.encode("utf-8")
    artifact = store.artifacts.put_bytes(data)
    sections = [s.to_dict() for s in detect_sections(text)]
    sidecar = store.artifacts.put_bytes(json.dumps(sections, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    content = {
        "label": bibliographic.get("title") or citation_key, "citation_key": citation_key,
        "artifact_sha256": artifact, "text_sha256": text_sha256(text), "sections_sha256": sidecar,
        "fulltext_status": "AVAILABLE", "text_extraction": extraction or {"method": "text"},
        **{k: v for k, v in bibliographic.items() if v is not None},
    }
    with store.write_tx(token=token, require_roles=WRITE_ROLES) as tx:
        head = tx.head(oid)
        if head and head["object_type"] != "Source":
            raise KnowledgeError("OBJECT_TYPE_MISMATCH", f"{oid} exists and is a {head['object_type']}")
        if head:
            prev = tx.get(oid)
            if prev["content"].get("text_sha256") == content["text_sha256"] and all(
                    prev["content"].get(k) == v for k, v in content.items()):
                return {"object_id": oid, "revision": prev["revision"], "created": False, "chars": len(text),
                        "sections": len(sections), "text_sha256": content["text_sha256"]}
        res = tx.add_revision(object_id=oid, object_type="Source", content=content, edges=[],
                              origin=engine_origin("corpus_import", tx.actor_id, citation_key=citation_key))
        tx.audit("corpus_import", "RECORDED", {"object_id": oid, "revision": res["revision"], "chars": len(text)})
    return {"object_id": oid, "revision": res["revision"], "created": True, "chars": len(text),
            "sections": len(sections), "text_sha256": content["text_sha256"]}


def import_source_file(store: StateStore, token: Optional[str], path: Path | str, **kw: Any) -> dict[str, Any]:
    text, extraction = read_text_file(path)
    kw.setdefault("citation_key", Path(path).stem)
    return import_source(store, token, text=text, extraction=extraction, **kw)


def source_text(store: StateStore, source_id: str, revision: Optional[int] = None) -> tuple[dict[str, Any], str]:
    """`(Source object, full text)`. A Source with no readable stored text is an error here, never an empty text."""
    obj = store.get_object(source_id, revision)
    if not obj or obj["object_type"] != "Source":
        raise KnowledgeError("SOURCE_NOT_FOUND", f"{source_id} is not a Source in this store")
    text, status = body_of(store, obj["content"])
    if text is None:
        raise KnowledgeError(f"FULLTEXT_{status}", f"{source_id} has no readable stored text")
    expected = obj["content"].get("text_sha256")
    if expected and text_sha256(text) != expected:
        raise KnowledgeError("FULLTEXT_CHANGED", f"{source_id}: stored text no longer matches its recorded hash")
    return obj, text


def source_sections(store: StateStore, obj: dict[str, Any], text: str) -> list[dict[str, Any]]:
    """The section structure of a Source: the stored sidecar, or detected afresh when the Source has none."""
    sha = obj["content"].get("sections_sha256")
    if sha and store.artifacts.has(sha):
        return json.loads(store.artifacts.get_bytes(sha).decode("utf-8"))
    return [s.to_dict() for s in detect_sections(text)]


def add_passage(store: StateStore, token: Optional[str], *, source_id: str, quote: str, passage_id: Optional[str] = None,
                locator: Optional[str] = None, normalized_claim: Optional[str] = None, id_prefix: str = "EP_",
                **extra: Any) -> dict[str, Any]:
    """Create an EvidencePassage for `quote` in the stored text of `source_id`, with its exact span.

    Refused (`QUOTE_NOT_IN_SOURCE`) when the quote is not in the text. The span and the source revision are
    recorded, so the passage can be re-verified at any time.
    """
    obj, text = source_text(store, source_id)
    found = quotes.find_quote(text, quote)
    if not found:
        raise KnowledgeError("QUOTE_NOT_IN_SOURCE", f"quote is not in the stored text of {source_id}")
    start, end = found
    sections = source_sections(store, obj, text)
    section = next((s for s in sections if s["char_start"] <= start < s["char_end"]), None)
    content = {
        "label": f"Passage in {obj['content'].get('citation_key', source_id)} chars {start}-{end}",
        "locator": locator or f"{source_id} chars {start}-{end}", "quote": text[start:end], "source_id": source_id,
        "source_revision": obj["revision"], "char_start": start, "char_end": end, "quote_verified": True,
        "section": (section or {}).get("heading") or (section or {}).get("label") or "", **extra,
    }
    if normalized_claim:
        content["normalized_claim"] = normalized_claim
    if not content["section"]:
        content.pop("section")
    with store.write_tx(token=token, require_roles=WRITE_ROLES) as tx:
        head_or_raise(tx, source_id, "Source")
        oid = scope_id(store, passage_id or f"{id_prefix}{id_fragment(source_id, 30)}_{start}")
        if tx.head(oid):
            raise KnowledgeError("ID_ALREADY_EXISTS", f"{oid} already exists")
        res = tx.add_revision(object_id=oid, object_type="EvidencePassage", content=content,
                              edges=[("derived_from", source_id, obj["revision"], {"origin": "corpus_passage"})],
                              origin=engine_origin("passage_add", tx.actor_id))
        tx.audit("passage_add", "RECORDED", {"object_id": oid, "source_id": source_id, "span": [start, end]})
    return {"object_id": oid, "revision": res["revision"], "source_id": source_id, "char_start": start, "char_end": end}


def verify_passage(store: StateStore, passage_id: str) -> dict[str, Any]:
    """Re-check an EvidencePassage against the stored text of the Source revision it points to."""
    p = store.get_object(passage_id)
    if not p or p["object_type"] != "EvidencePassage":
        raise KnowledgeError("PASSAGE_NOT_FOUND", f"{passage_id} is not an EvidencePassage")
    c = p["content"]
    if not c.get("source_id") or c.get("char_start") is None:
        return {"object_id": passage_id, "source_match": "NOT_CHECKED", "reason": "passage has no recorded source span"}
    try:
        _, text = source_text(store, c["source_id"], c.get("source_revision"))
    except KnowledgeError as exc:
        return {"object_id": passage_id, "source_match": "FAILED", "reason": exc.code}
    check = quotes.verify_span(text, c["char_start"], c["char_end"], c.get("quote"))
    return {"object_id": passage_id, "source_match": "VERIFIED" if check.ok else "FAILED", "reason": check.reason}


def corpus_snapshot_id(store: StateStore) -> str:
    """Identity of the corpus as it is now: every Source head with its text digest and full-text status.

    Adding, revising (an erratum) or replacing a source changes it; importing an identical text again does not. Conclusions that rest on
    not finding something are bound to it (M5, invariant I20)."""
    from research_harness.core.canonical import canonical_json_hash
    rows = []
    with store.read() as conn:
        for (oid,) in conn.execute("SELECT object_id FROM object_heads WHERE object_type='Source' ORDER BY object_id").fetchall():
            o = store.get_object(oid, conn=conn)
            c = o["content"]
            rows.append([oid, o["revision"], c.get("text_sha256"), c.get("fulltext_status")])
    return canonical_json_hash(rows)
