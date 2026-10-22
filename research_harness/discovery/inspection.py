"""Recognition of a theoretical tension: what has been inspected, and nothing more (M5 WP6.1 remediation).

Whether the literature already recognises or resolves a tension cannot be read off a model's proposal. An empty `already_addressed_by` is a model that listed no source, not a
finding that none exists; a BM25 match is a paper to read, not evidence that it addresses anything. So the suite keeps an **inspection record** and states a recognition status only
from it:

* `RECOGNITION_NOT_ASSESSED`: no inspection was recorded (nothing about absence follows).
* `POTENTIAL_ADDRESSING_SOURCES`: sources are named (by a model's hint) but nobody has inspected them.
* `ADDRESSED_IN_INSPECTED_SET`: the researcher inspected the sources and at least one is recorded as addressing the tension, with a verbatim quote that became an EvidencePassage.
* `NOT_FOUND_IN_INSPECTED_SET`: a stated set of candidates (found by the stated terms, in the stated corpus snapshot) was inspected completely, every candidate was readable and
  none addresses the tension. A statement about that set in that snapshot, and about nothing else: not about the literature, not about novelty.
* `INSPECTION_INCOMPLETE`: a candidate was not inspected, or only an abstract or metadata is held for it.

An inspection is stale (`needs_reassessment`) when the corpus changed (for a negative or incomplete statement), when an inspected Source was revised, or when the tension was
redefined. The researcher's decision (who, when, why) is part of the record.
"""
from __future__ import annotations

from typing import Any, Optional

from research_harness.core.canonical import canonical_json_hash

RESULTS = ("ADDRESSES", "MENTIONS_PHENOMENON_ONLY", "DOES_NOT_ADDRESS", "NOT_READABLE")
STATUSES = ("RECOGNITION_NOT_ASSESSED", "POTENTIAL_ADDRESSING_SOURCES", "ADDRESSED_IN_INSPECTED_SET", "NOT_FOUND_IN_INSPECTED_SET", "INSPECTION_INCOMPLETE")
STANDARD_LIMITATIONS = (
    "candidates are held sources that match the search terms by words; a source that uses other words may be missing from the candidates",
    "the inspected scope of each source is what the researcher states they read, not something the suite checked",
    "a negative statement holds for this inspected set in this corpus snapshot only: it says nothing about the literature as a whole and is not a statement about novelty",
)


def tension_digest(inf: dict[str, Any]) -> str:
    """The identity of a tension's definition: what it concerns and what was said about it. A different definition is a different tension."""
    return canonical_json_hash({"kind": inf["kind"], "topic": inf.get("relationship"), "sides": [[s["entry"], s["passage_id"], s["passage_revision"]] for s in inf["sides"]],
                                "shared": inf.get("shared"), "differences": inf.get("differences"), "rationale": inf.get("rationale"), "meaning_difference": inf.get("meaning_difference"),
                                "levels": inf.get("levels"), "text": inf.get("text"), "reasoning": inf.get("reasoning")})


def status_of(insp: dict[str, Any]) -> tuple[str, list[str]]:
    """The recognition status a record supports, and what qualifies it. Pure: reads only the record."""
    res = insp["results"]
    quals: list[str] = []
    by_work: dict[str, dict[str, str]] = {}
    for r in res:
        by_work.setdefault(r.get("work_id") or r["source_id"], {})[r["source_id"]] = r["result"]
    for work, versions in by_work.items():
        if "ADDRESSES" in versions.values() and any(v in ("DOES_NOT_ADDRESS", "MENTIONS_PHENOMENON_ONLY") for v in versions.values()) and len(versions) > 1:
            yes = sorted(k for k, v in versions.items() if v == "ADDRESSES")
            no = sorted(k for k, v in versions.items() if v != "ADDRESSES")
            quals.append(f"work {work}: {', '.join(yes)} addresses the tension but {', '.join(no)} does not; the argument may have been changed or withdrawn between versions")
    if insp["uninspected"]:
        quals.append(f"{len(insp['uninspected'])} candidate(s) were not inspected: {', '.join(insp['uninspected'])}")
    if insp["unreadable"]:
        quals.append(f"{len(insp['unreadable'])} candidate(s) have no full text (abstract or metadata only): {', '.join(insp['unreadable'])}")
    if any(r["result"] == "ADDRESSES" for r in res):
        return "ADDRESSED_IN_INSPECTED_SET", quals
    if insp["uninspected"] or insp["unreadable"] or any(r["result"] == "NOT_READABLE" for r in res):
        return "INSPECTION_INCOMPLETE", quals
    return "NOT_FOUND_IN_INSPECTED_SET", quals


def _latest(inspections: list[dict[str, Any]], inference_id: str) -> Optional[dict[str, Any]]:
    mine = [i for i in inspections if i["inference"] == inference_id]
    return mine[-1] if mine else None


def recognition_view(inf: dict[str, Any], inspections: list[dict[str, Any]], snapshot_now: str, head_revision: Any) -> Optional[dict[str, Any]]:
    """The recognition block of a proposed relation, with the reasons it must be reassessed. `head_revision(source_id) -> int|None`."""
    if inf.get("derivation", "rule") == "rule" or inf["kind"] == "implied_assumption":
        return None
    hints = [{"entry": a["entry"], "source_id": a["source_id"], "source_revision": a["source_revision"], "how": a["how"], "verified": False} for a in inf.get("addressed_by") or []]
    insp = _latest(inspections, inf["id"])
    if insp is None:
        return {"status": "POTENTIAL_ADDRESSING_SOURCES" if hints else "RECOGNITION_NOT_ASSESSED", "inspection_id": None, "model_hints": hints, "addressed_by": [], "qualifications": [],
                "needs_reassessment": [], "note": ("sources are named but nobody has inspected them: " if hints else "no inspection is recorded; an empty list of sources from a model is not a finding that none exists. ")
                + "Nothing about whether the literature addresses this tension follows. Use `landscape candidates` and `landscape inspect`."}
    status, quals = status_of(insp)
    why: list[str] = []
    if insp["tension_digest"] != tension_digest(inf):
        why.append("the tension was redefined after it was inspected")
    for r in insp["results"]:
        head = head_revision(r["source_id"])
        if head is None:
            why.append(f"{r['source_id']} no longer exists")
        elif head != r["source_revision"]:
            why.append(f"{r['source_id']} was revised (r{r['source_revision']} -> r{head}) after it was inspected")
    if status != "ADDRESSED_IN_INSPECTED_SET" and insp["corpus_snapshot_id"] != snapshot_now:
        why.append(f"the corpus changed since this inspection ({insp['corpus_snapshot_id'][:12]} -> {snapshot_now[:12]}): a source that addresses the tension may now be held")
    scope = (f"{len(insp['results'])} source(s) inspected of {len(insp['candidates'])} candidate(s), corpus snapshot {insp['corpus_snapshot_id'][:12]}, "
             f"terms: {', '.join(insp['search']['terms']) or '-'}")
    return {"status": status, "inspection_id": insp["id"], "scope": scope, "model_hints": hints, "qualifications": quals, "needs_reassessment": why,
            "addressed_by": [{"source_id": r["source_id"], "source_revision": r["source_revision"], "note": r.get("note"), "passages": r.get("passages") or []} for r in insp["results"] if r["result"] == "ADDRESSES"],
            "mentions_only": [r["source_id"] for r in insp["results"] if r["result"] == "MENTIONS_PHENOMENON_ONLY"],
            "not_addressing": [r["source_id"] for r in insp["results"] if r["result"] == "DOES_NOT_ADDRESS"],
            "decision": insp["decision"], "limitations": insp["limitations"],
            "note": {"ADDRESSED_IN_INSPECTED_SET": "at least one inspected source addresses the tension (verbatim evidence recorded); read the qualifications",
                     "NOT_FOUND_IN_INSPECTED_SET": "none of the inspected candidates addresses it: a statement about this inspected set in this corpus snapshot, not about the literature and not about novelty",
                     "INSPECTION_INCOMPLETE": "the inspection is not complete: no absence statement is made"}[status]}


def dependencies(store: Any, inf: dict[str, Any], rec: Optional[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """The Source@revisions an inference rests on and why it needs reassessment now: a revised, removed or excluded source it cites, and the recognition block's own reasons."""
    deps: dict[tuple[str, int], dict[str, Any]] = {}
    for s in list(inf["sides"]) + list(inf.get("addressed_by") or []):
        deps[(s["source_id"], s["source_revision"])] = {"source_id": s["source_id"], "source_revision": s["source_revision"]}
    reasons: list[str] = []
    for (sid, rev) in sorted(deps):
        head = store.get_object(sid)
        if head is None:
            reasons.append(f"{sid} no longer exists")
        elif head["revision"] != rev:
            reasons.append(f"{sid} was revised (r{rev} -> r{head['revision']}) after this was made")
        elif head["content"].get("excluded"):
            reasons.append(f"{sid} is excluded from the corpus")
    reasons += (rec or {}).get("needs_reassessment") or []
    return list(deps.values()), reasons
