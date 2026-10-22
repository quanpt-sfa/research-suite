"""Relations between studies, implied assumptions, and their gate (M5 WP6.1).

What a rule can find in a landscape is narrow: findings of different works that name the same relationship with opposite direction (`contradiction`, which is an
`empirical_disagreement`), the same between versions of one work (`version_difference`), and constructs defined in different words (`definitions_differ`, an observation
that says nothing about meaning). Everything that needs a reading of meaning is a **proposal**: a model's, the researcher's or a critic's. A proposal is checked here by
a deterministic gate before it is stored, is `UNCONFIRMED` until the researcher decides, and never becomes an entry an author wrote.

A proposal must say which claims it concerns (entries, so Source@revision, passage and place), what they share, how they differ, why they conflict, differ only in scope
or complement each other, and what information is missing. The suite never says a tension is "unrecognised": it states a recognition
status only from an inspection record (`inspection.py`), never from a model's empty list of sources.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from research_harness.core.canonical import canonical_json_hash
from research_harness.knowledge.common import KnowledgeError

PROPOSED_ONLY_KINDS = ("conceptual_divergence", "theoretical_incompatibility", "explanatory_incompleteness", "boundary_tension", "complementarity")
PROPOSABLE_KINDS = PROPOSED_ONLY_KINDS + ("empirical_disagreement", "implied_assumption")
RULE_ONLY_KINDS = ("contradiction", "version_difference", "definitions_differ")
LEVEL_GUARDED = ("theoretical_incompatibility",)
PROVENANCES = ("researcher_proposed", "critic_proposed")
ENTRY_CATEGORIES_IN_CONTEXT = ("theory", "construct", "mechanism", "stated_assumption", "identifying_assumption", "theoretical_claim", "interpretive_finding", "finding", "boundary",
                               "method_limitation", "unresolved_question")

PROPOSAL_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "landscape_relation_proposals", "type": "object",
    "properties": {"proposals": {"type": "array", "items": {
        "type": "object",
        "properties": {"kind": {"type": "string"}, "topic": {"type": "string"}, "entries": {"type": "array", "items": {"type": "string"}},
                       "shared": {"type": "string"}, "differences": {"type": "string"}, "rationale": {"type": "string"}, "missing_information": {"type": "array", "items": {"type": "string"}},
                       "levels": {"type": "object", "additionalProperties": {"type": "string"}}, "meaning_difference": {"type": "string"},
                       "already_addressed_by": {"type": "array", "items": {"type": "object", "properties": {"entry": {"type": "string"}, "how": {"type": "string"}}, "required": ["entry"]}},
                       "search_terms": {"type": "array", "items": {"type": "string"}}, "text": {"type": "string"}, "reasoning": {"type": "string"},
                       "premises": {"type": "array", "items": {"type": "string"}}, "alternative_interpretations": {"type": "array", "items": {"type": "string"}}},
        "required": ["kind", "entries"]}}},
    "required": ["proposals"], "additionalProperties": True}

SYSTEM_PROMPT = (
    "You help a researcher see how the studies in a literature landscape relate to one another. You do not decide who is right and you state nothing as established: every item you "
    "give is a proposal the researcher will decide on. Work only from the numbered entries shown (each is a restatement of a quotation from one paper).\n\n"
    "Propose a relation only where the entries support it. Kinds: conceptual_divergence (the same idea is understood differently: say how the MEANING differs, not only the wording, "
    "in `meaning_difference`); theoretical_incompatibility (two theories cannot both hold on the same object, level and scope: give the level of analysis of each entry in `levels`; "
    "if the levels differ the theories are not incompatible, so propose boundary_tension or complementarity instead); explanatory_incompleteness (a theory leaves a stated "
    "phenomenon unexplained); boundary_tension (claims conflict only at the edge of their scopes); complementarity (claims explain different parts of the same matter); "
    "empirical_disagreement (findings disagree, including where they are worded differently); implied_assumption (an assumption that a stated claim needs but its authors did not "
    "write: give `text`, `premises` (what you start from), `reasoning`, `missing_information` (what you would need to know to tell whether the assumption is really needed; an empty list "
    "only if nothing is missing) and `alternative_interpretations` (other ways to read the passage that would make the assumption unnecessary); it is never presented as the "
    "authors' assumption).\n\n"
    "For every relation give: `entries` (at least two, from at least two different works), `shared` (what the claims share: object, scope, unit of analysis), `differences` "
    "(how they differ: definition, assumption, mechanism or reasoning), `rationale` (why they are incompatible, differ only in scope, or complement each other), "
    "`missing_information` (what you would need to know to be sure; an empty list only if nothing is missing), `topic` (a short name) and `search_terms` (words that would find "
    "papers that already address this). If an entry shown seems to recognise or resolve the tension, list it in `already_addressed_by` with how: this is only a hint that someone will inspect. An empty list "
    "does NOT mean that no paper addresses the tension, and you must not claim that nobody has noticed it.\n\nRespond with exactly one JSON object and nothing else. It must validate against this JSON Schema:\n" + json.dumps(PROPOSAL_SCHEMA, indent=2, sort_keys=True))
SKILL_DIGEST = canonical_json_hash(SYSTEM_PROMPT)


def _norm(text: Any) -> str:
    return " ".join(str(text or "").casefold().split())


def build_prompt(content: dict[str, Any], kinds: Optional[list[str]] = None) -> str:
    from research_harness.workflows import quotes
    lines = []
    for e in content["elements"]:
        if e["category"] not in ENTRY_CATEGORIES_IN_CONTEXT:
            continue
        lines.append(f"{e['id']} [{e['category']}] {e['source_id']}@{e['source_revision']} (work {e['work_id']})"
                     + (f" label: {e['label']}" if e.get("label") else "") + f"\n   says: {e['text']}\n   quote: \"{quotes.normalize_whitespace(e['author_claim']['quote'])[:300]}\"")
    wanted = f"\n\nPropose only these kinds: {', '.join(kinds)}." if kinds else ""
    return "ENTRIES OF THE LANDSCAPE:\n" + "\n".join(lines) + wanted


def _text(p: dict[str, Any], key: str) -> str:
    v = p.get(key)
    if not isinstance(v, str) or not v.strip():
        raise KnowledgeError("FIELD_REQUIRED", f"{key} is required and must say something", field=key)
    return v.strip()


def validate_proposal(content: dict[str, Any], p: dict[str, Any], *, enforce_levels: bool) -> dict[str, Any]:
    """The checked, normalised proposal, or a KnowledgeError whose code says what was missing. Pure: it reads only the landscape content.

    `enforce_levels`: for a model's proposal of a level-guarded kind the declared levels of analysis must be given and must agree. A researcher's proposal is not held to
    the second check (they take responsibility for the judgement, and it is recorded as theirs)."""
    kind = p.get("kind")
    if kind in RULE_ONLY_KINDS or kind not in PROPOSABLE_KINDS:
        raise KnowledgeError("PROPOSAL_KIND_NOT_ALLOWED", f"{kind!r} cannot be proposed: it is derived by rule or unknown (proposable: {PROPOSABLE_KINDS})")
    by_id = {e["id"]: e for e in content["elements"]}
    ids = list(dict.fromkeys(p.get("entries") or []))
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        raise KnowledgeError("ENTRY_UNKNOWN", f"not entries of this landscape: {unknown}")
    need = 1 if kind == "implied_assumption" else 2
    if len(ids) < need:
        raise KnowledgeError("TOO_FEW_ENTRIES", f"{kind} concerns at least {need} entr{'y' if need == 1 else 'ies'}")
    sides = []
    for i in ids:
        e = by_id[i]
        sides.append({"entry": i, "category": e["category"], "source_id": e["source_id"], "source_revision": e["source_revision"], "work_id": e["work_id"], "version_id": e.get("version_id"),
                      "passage_id": e["author_claim"]["passage_id"], "passage_revision": e["author_claim"]["passage_revision"], "locator": e["author_claim"]["locator"]})
    out: dict[str, Any] = {"kind": kind, "relationship": _text(p, "topic") if p.get("topic") else kind, "sides": sides, "search_terms": [str(t).strip() for t in p.get("search_terms") or [] if str(t).strip()]}
    if kind == "implied_assumption":
        if not isinstance(p.get("missing_information"), list):
            raise KnowledgeError("FIELD_REQUIRED", "missing_information is required for an implied assumption (a list; empty only if nothing is missing): "
                                 "say what you would need to know to tell whether the assumption is needed", field="missing_information")
        out.update(text=_text(p, "text"), reasoning=_text(p, "reasoning"), relation="implied_assumption", missing_information=[str(m).strip() for m in p["missing_information"] if str(m).strip()],
                   premises=[str(x).strip() for x in p.get("premises") or [] if str(x).strip()], alternative_interpretations=[str(x).strip() for x in p.get("alternative_interpretations") or [] if str(x).strip()],
                   shared=None, differences=None, rationale=None, levels={}, meaning_difference=None, addressed_by=[])
    else:
        if len({s["work_id"] for s in sides}) < 2:
            raise KnowledgeError("SAME_WORK", "the entries come from one work (versions of it): a difference between versions is a version matter, not a relation between studies")
        for key in ("shared", "differences", "rationale"):
            _text(p, key)
        if not isinstance(p.get("missing_information"), list):
            raise KnowledgeError("FIELD_REQUIRED", "missing_information is required (a list; empty only if nothing is missing)", field="missing_information")
        levels = {k: str(v).strip() for k, v in (p.get("levels") or {}).items() if str(v).strip()}
        if kind in LEVEL_GUARDED and enforce_levels:
            if any(i not in levels for i in ids):
                raise KnowledgeError("LEVELS_REQUIRED", f"{kind} needs the level of analysis of every entry it concerns")
            if len({_norm(levels[i]) for i in ids}) > 1:
                raise KnowledgeError("LEVELS_DIFFER", "the entries are at different levels of analysis, so the theories are not incompatible; they may complement each other or meet at a boundary")
        if kind == "conceptual_divergence" and not (isinstance(p.get("meaning_difference"), str) and p["meaning_difference"].strip()):
            raise KnowledgeError("MEANING_DIFFERENCE_REQUIRED", "conceptual_divergence needs how the meaning differs, not only the wording")
        addressed = []
        for a in p.get("already_addressed_by") or []:
            e = by_id.get(a.get("entry"))
            if e is None:
                raise KnowledgeError("ADDRESSED_BY_UNKNOWN", f"{a.get('entry')!r} is not an entry of this landscape")
            if not (a.get("how") or "").strip():
                raise KnowledgeError("FIELD_REQUIRED", "say how the entry addresses the tension", field="already_addressed_by.how")
            addressed.append({"entry": e["id"], "how": a["how"].strip(), "source_id": e["source_id"], "source_revision": e["source_revision"], "work_id": e["work_id"],
                              "passage_id": e["author_claim"]["passage_id"], "passage_revision": e["author_claim"]["passage_revision"], "quote": e["author_claim"]["quote"],
                              "locator": e["author_claim"]["locator"]})
        out.update(relation=kind, shared=p["shared"].strip(), differences=p["differences"].strip(), rationale=p["rationale"].strip(), missing_information=[str(m).strip() for m in p["missing_information"] if str(m).strip()],
                   levels=levels, meaning_difference=(p.get("meaning_difference") or "").strip() or None, addressed_by=addressed, text=None, reasoning=None)
    dup = next((i for i in content["inferences"] if i["kind"] == kind and {s["entry"] for s in i["sides"]} == set(ids)), None)
    if dup:
        raise KnowledgeError("DUPLICATE", f"{dup['id']} already records this {kind} for these entries")
    if kind == "empirical_disagreement":
        rule = next((i for i in content["inferences"] if i["kind"] == "contradiction" and {s["entry"] for s in i["sides"]} == set(ids)), None)
        if rule:
            raise KnowledgeError("DUPLICATE_OF_RULE", f"{rule['id']} already records this disagreement by rule")
    return out


# ------------------------------------------------------------------------------------------------ what the view shows about an inference

def confirmation(i: dict[str, Any]) -> dict[str, Any]:
    if not i["reviews"]:
        return {"state": "UNCONFIRMED"}
    r = i["reviews"][-1]
    state = {"CONFIRMED": "CONFIRMED_BY_RESEARCHER", "DISMISSED": "DISMISSED_BY_RESEARCHER", "NEEDS_SCOPE": "NEEDS_SCOPE"}[r["decision"]]
    return {"state": state, "by": r["by"], "at": r["at"], "reason": r["reason"], "acknowledged_missing": bool(r.get("acknowledged_missing"))}
