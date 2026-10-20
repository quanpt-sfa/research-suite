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
    WRITE_ROLES, KnowledgeError, engine_origin, head_or_raise, id_fragment)
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


def import_source(store: StateStore, token: Optional[str], *, citation_key: str, text: str,
                  source_id: Optional[str] = None, id_prefix: str = "SRC_", extraction: Optional[dict] = None,
                  **bibliographic: Any) -> dict[str, Any]:
    """Store `text` as the full text of a Source (new revision if the Source exists) and return its reference.

    `bibliographic` accepts the optional Source fields (title, authors, year, venue, doi, abstract, license_note).
    """
    if not text or not text.strip():
        raise KnowledgeError("EMPTY_TEXT", "a Source needs non-empty text")
    allowed = {"title", "authors", "year", "venue", "doi", "abstract", "license_note", "bibliographic_identity"}
    unknown = set(bibliographic) - allowed
    if unknown:
        raise KnowledgeError("UNKNOWN_SOURCE_FIELDS", f"unsupported Source fields: {sorted(unknown)}")
    oid = source_id or source_id_for(citation_key, id_prefix)
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
        oid = passage_id or f"{id_prefix}{id_fragment(source_id, 30)}_{start}"
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
