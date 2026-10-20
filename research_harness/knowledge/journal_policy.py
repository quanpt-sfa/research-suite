"""Journal Policy Registry (plan v3, 4.4): versioned, sourced policies of one of four types.

A policy is a `JournalPolicy` library object with the journal, a type (`official_requirement`, `reporting_standard`,
`published_precedent`, `editorial_feedback`), the date range it was in force, and the stored snapshot it was read from.
The excerpt must occur verbatim in that snapshot: the registry holds what a source says, not what someone remembers.

Only a matching `official_requirement` in force on the date asked about is binding (I12). A published precedent shows
what a journal has accepted; it never becomes a requirement because it was accepted.
"""
from __future__ import annotations

from typing import Any, Optional

from research_harness import taxonomy
from research_harness.knowledge.common import KnowledgeError, id_fragment, scope_id
from research_harness.knowledge.library import add_object, make_pin
from research_harness.state.store import StateStore
from research_harness.workflows import quotes

BINDING_TYPES = frozenset({"official_requirement"})


def add_policy(lib: StateStore, token: Optional[str], *, journal: str, policy_type: str, excerpt: str, effective_from: str,
               retrieved_at: str, snapshot: bytes | str, source_url: Optional[str] = None, applies_to: Optional[dict] = None,
               policy_id: Optional[str] = None, label: Optional[str] = None, reporting_items: Optional[list[str]] = None) -> dict[str, Any]:
    """Record a policy with its snapshot. Refused if the excerpt is not in the snapshot, or an official requirement has
    no source reference."""
    taxonomy.validate_term("journal_policy_type", policy_type)
    data = snapshot.encode("utf-8") if isinstance(snapshot, str) else snapshot
    text = data.decode("utf-8", errors="replace")
    if not quotes.find_quote(text, excerpt):
        raise KnowledgeError("EXCERPT_NOT_IN_SNAPSHOT", "the excerpt does not occur in the stored snapshot of the policy source")
    if policy_type == "official_requirement" and not source_url:
        raise KnowledgeError("SOURCE_REFERENCE_REQUIRED", "an official requirement needs the source it comes from (source_url)")
    sha = lib.artifacts.put_bytes(data)
    with lib.read() as conn:
        n = conn.execute("SELECT COUNT(*) FROM object_heads WHERE object_type='JournalPolicy'").fetchone()[0] + 1
    pid = scope_id(lib, policy_id or f"JP_{id_fragment(journal, 24)}_{id_fragment(policy_type, 20)}_{n:03d}")
    content = {"label": label or f"{journal}: {policy_type}", "journal": journal, "policy_type": policy_type,
               "effective_from": effective_from, "retrieved_at": retrieved_at, "source_sha256": sha, "excerpt": excerpt[:5000],
               "applies_to": applies_to or {}}
    if source_url:
        content["source_url"] = source_url
    if reporting_items:
        content["reporting_items"] = list(reporting_items)
    res = add_object(lib, token, pid, "JournalPolicy", content, operation="journal_policy_add")
    return {"policy_id": pid, **res}


def supersede(lib: StateStore, token: Optional[str], policy_id: str, *, effective_to: str, superseded_by: Optional[str] = None) -> dict[str, Any]:
    """End a policy's period of force (new revision; the old revision and its pin remain)."""
    obj = lib.get_object(policy_id)
    if not obj or obj["object_type"] != "JournalPolicy":
        raise KnowledgeError("POLICY_NOT_FOUND", f"{policy_id} is not a JournalPolicy")
    if effective_to < obj["content"]["effective_from"]:
        raise KnowledgeError("PERIOD_INVALID", "effective_to is before effective_from")
    content = {**obj["content"], "effective_to": effective_to}
    if superseded_by:
        content["superseded_by"] = superseded_by
    return add_object(lib, token, policy_id, "JournalPolicy", content, operation="journal_policy_supersede")


def _in_force(c: dict[str, Any], as_of: Optional[str]) -> bool:
    if as_of is None:
        return "effective_to" not in c
    return c["effective_from"] <= as_of and ("effective_to" not in c or as_of <= c["effective_to"])


def lookup(lib: StateStore, journal: str, *, as_of: Optional[str] = None, policy_type: Optional[str] = None) -> list[dict[str, Any]]:
    """Policies of a journal with their force on `as_of` (ISO date; default: now in force). `binding` is true only for an
    official requirement in force; the others are context for the researcher, never obligations."""
    out = []
    with lib.read() as conn:
        ids = [r[0] for r in conn.execute("SELECT object_id FROM object_heads WHERE object_type='JournalPolicy' ORDER BY object_id")]
    for pid in ids:
        obj = lib.get_object(pid)
        c = obj["content"]
        if c["journal"].lower() != journal.lower() or (policy_type and c["policy_type"] != policy_type):
            continue
        in_force = _in_force(c, as_of)
        out.append({"policy_id": pid, "pin": make_pin(pid, obj["revision"], obj["content_digest"]), "policy_type": c["policy_type"],
                    "effective_from": c["effective_from"], "effective_to": c.get("effective_to"), "in_force": in_force,
                    "binding": in_force and c["policy_type"] in BINDING_TYPES, "excerpt": c["excerpt"], "source_url": c.get("source_url"),
                    "applies_to": c.get("applies_to") or {}})
    return out


def _days_between(earlier: str, later: str) -> int:
    from datetime import date
    return (date.fromisoformat(later[:10]) - date.fromisoformat(earlier[:10])).days


def alignment(store: StateStore, *, journal: str, article_type: Optional[str] = None, study_design: Optional[str] = None,
              planned_submission: Optional[str] = None, today: Optional[str] = None, stale_days: int = 365,
              policy_ids: Optional[list[str]] = None) -> dict[str, Any]:
    """What a journal's registered policies mean for one planned submission (plan v3, 3D; works in a project or the library).

    A policy binds only if it is an `official_requirement` that applies to the article type and study design and was in
    force on the planned submission date (I12). A reporting standard yields required reporting items. Precedents and
    editorial feedback are context and are never requirements. A policy retrieved more than `stale_days` before `today`
    is marked `RE-VERIFY`: it is shown, and never silently treated as current.
    """
    from datetime import date
    today = today or date.today().isoformat()
    planned = planned_submission or today
    binding, reporting, context, all_entries = [], [], [], []
    with store.read() as conn:
        ids = [r[0] for r in conn.execute("SELECT object_id FROM object_heads WHERE object_type='JournalPolicy' ORDER BY object_id")]
    for pid in ids:
        if policy_ids is not None and pid not in policy_ids:
            continue
        obj = store.get_object(pid)
        c = obj["content"]
        if c["journal"].lower() != journal.lower():
            continue
        ap = c.get("applies_to") or {}
        types = ap.get("article_types") or []
        designs = ap.get("study_designs") or []
        applies = (not types or article_type in types) and (not designs or study_design in designs)
        in_force = c["effective_from"] <= planned and ("effective_to" not in c or planned <= c["effective_to"])
        stale = _days_between(c["retrieved_at"], today) > stale_days
        entry = {"policy_id": pid, "pin": make_pin(pid, obj["revision"], obj["content_digest"]), "policy_type": c["policy_type"],
                 "applies": applies, "in_force": in_force, "status": "RE-VERIFY" if stale else "CURRENT", "retrieved_at": c["retrieved_at"],
                 "excerpt": c["excerpt"], "source_url": c.get("source_url")}
        all_entries.append(entry)
        if not (applies and in_force):
            continue
        if c["policy_type"] in BINDING_TYPES:
            binding.append(entry)
        elif c["policy_type"] == "reporting_standard":
            reporting.append({**entry, "items": c.get("reporting_items") or []})
        else:
            context.append(entry)
    return {"journal": journal, "planned_submission": planned, "binding_requirements": binding,
            "required_reporting_items": [{"item": i, "policy_id": r["policy_id"], "status": r["status"]} for r in reporting for i in r["items"]],
            "context_not_requirements": context, "all": all_entries,
            "needs_reverification": [e["policy_id"] for e in all_entries if e["status"] == "RE-VERIFY" and e["applies"] and e["in_force"]]}
