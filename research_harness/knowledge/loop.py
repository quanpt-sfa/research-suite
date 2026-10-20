"""Results → Discussion → Contribution loop (plan v3, 5a): claim change and the check of the Introduction's promises.

`assess_claim_change` reads the **scientific-interpretation layer** (claim effects) and the **divergences** of the robustness
steps those effects rest on. It never reads a raw diagnostic signal and never turns non-significance into `NOT_SUPPORTED`:

| What the effects show                                                    | Claim change |
|---|---|
| nothing assessed                                                         | none: `INSUFFICIENT_EVIDENCE` |
| only `NO_BEARING`                                                        | `UNCHANGED` |
| only `STRENGTHENS`                                                       | `STRENGTHENED` |
| `LIMITS`, with a robustness divergence beyond its declared tolerance     | `NARROWED` (the dimensions that diverged are named) |
| `LIMITS` otherwise                                                       | `QUALIFIED` |
| `CHANGES`                                                                | `WEAKENED` |
| `CHANGES` that reverses the claim, from structured evidence              | `REVERSED` |
| `CHANGES` with a recorded structured contradiction of the claim          | `NOT_SUPPORTED` |

Every claim-change statement is HIGH risk and `PROVISIONAL` until the researcher confirms it; the claim and the statements
the change rests on become HIGH (D15). `promise_check` compares what the Introduction promised with the claim changes and
produces manuscript revision *suggestions*, each with its basis. None is applied: the manuscript is the researcher's.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from research_harness import taxonomy
from research_harness.knowledge.common import KnowledgeError, engine_origin
from research_harness.state.store import StateStore
from research_harness.workflows.eca import mark_high
from research_harness.workflows.status import AssessmentStatus, apply_caps

PROMISE_STATUS = ("KEPT", "AT_RISK", "NOT_KEPT", "NOT_TESTED")
EXECUTOR = "claim_loop@1"


def _interpretation_effects(store: StateStore, claim_id: str, interpretation_ids: Optional[list[str]]) -> tuple[list[dict], list[str]]:
    effects, sources = [], []
    with store.read() as conn:
        ids = [r[0] for r in conn.execute("SELECT object_id FROM object_heads WHERE object_type='Result' ORDER BY object_id")]
        for oid in ids:
            if interpretation_ids is not None and oid not in interpretation_ids:
                continue
            obj = store.get_object(oid, conn=conn)
            if obj["content"].get("layer") != "scientific_interpretation":
                continue
            for i, e in enumerate(obj["content"].get("claim_effects") or []):
                if e["claim_id"] == claim_id:
                    effects.append({**e, "interpretation": oid, "index": i, "interpretation_revision": obj["revision"]})
                    sources.append(oid)
    return effects, sorted(set(sources))


def _divergences(store: StateStore, plan_steps: set[tuple[str, str]]) -> list[dict[str, Any]]:
    out = []
    with store.read() as conn:
        ids = [r[0] for r in conn.execute("SELECT object_id FROM object_heads WHERE object_type='Result' ORDER BY object_id")]
        for oid in ids:
            c = store.get_object(oid, conn=conn)["content"]
            if (c.get("plan_id"), c.get("step_id")) in plan_steps:
                for d in c.get("divergences") or []:
                    out.append({**d, "result": oid, "step": c["step_id"]})
    return out


def derive_change(effects: list[dict[str, Any]], divergences: list[dict[str, Any]]) -> tuple[Optional[str], list[str], dict[str, Any]]:
    """The claim change the effects support, why, and the details. Pure: no store access."""
    assessed = [e for e in effects if e["effect"] != "NOT_ASSESSED"]
    kinds = {e["effect"] for e in assessed}
    why: list[str] = []
    detail: dict[str, Any] = {}
    if not assessed:
        return None, ["no claim effect is assessed" + (f" ({len(effects)} not assessed because a step it rests on did not succeed)" if effects else "")], detail
    if "CHANGES" in kinds:
        changing = [e for e in assessed if e["effect"] == "CHANGES"]
        structured = [e for e in changing if e.get("evidence_kind") == "structured"]
        if any(e.get("reverses") for e in structured):
            return "REVERSED", ["a claim effect from structured evidence reverses the claim"], detail
        if any(e.get("contradiction") for e in structured):
            detail["contradiction"] = [e["contradiction"] for e in structured if e.get("contradiction")]
            return "NOT_SUPPORTED", ["structured evidence records a contradiction of the claim (not non-significance)"], detail
        return "WEAKENED", ["a claim effect changes what the evidence says about the claim"], detail
    if "LIMITS" in kinds:
        beyond = sorted({d["dimension"] for d in divergences if d.get("outcome") == "EXCEEDS_DECLARED_TOLERANCE"})
        if beyond:
            detail["narrowed_on"] = beyond
            return "NARROWED", [f"the claim is limited and robustness diverged beyond its declared tolerance on: {', '.join(beyond)}"], detail
        return "QUALIFIED", ["the claim is limited by the evidence; no robustness divergence beyond a declared tolerance is on record"], detail
    if kinds == {"STRENGTHENS"} or kinds == {"STRENGTHENS", "NO_BEARING"}:
        return "STRENGTHENED", ["the assessed effects strengthen the claim"], detail
    return "UNCHANGED", ["no assessed effect bears on the claim"], detail


def assess_claim_change(store: StateStore, token: Optional[str], claim_id: str, *, change_id: str,
                        interpretation_ids: Optional[list[str]] = None) -> dict[str, Any]:
    """Derive and record the change of a claim (HIGH risk, PROVISIONAL until confirmed). Marks the claim HIGH."""
    claim = store.get_object(claim_id)
    if not claim or claim["object_type"] != "Claim":
        raise KnowledgeError("CLAIM_NOT_FOUND", f"{claim_id} is not a Claim")
    effects, sources = _interpretation_effects(store, claim_id, interpretation_ids)
    steps: set[tuple[str, str]] = set()
    with store.read() as conn:
        for oid in sources:
            plan = store.get_object(oid, conn=conn)["content"].get("plan_id")
            for e in effects:
                if e["interpretation"] == oid:
                    steps |= {(plan, s) for s in e.get("depends_on") or []}
    divergences = _divergences(store, steps)
    change, why, detail = derive_change(effects, divergences)
    if change:
        taxonomy.validate_term("claim_change", change)
    status = "INSUFFICIENT_EVIDENCE" if change is None else "REQUIRES_VERIFICATION"
    assessment = apply_caps(AssessmentStatus.PROVISIONAL if change else AssessmentStatus.NOT_ASSESSED, generated_by="deterministic:claim_loop@1")
    content = {"label": f"Change of {claim_id}", "summary": f"{claim_id}: {change or 'no change can be derived'}. " + " ".join(why), "layer": "claim_change",
               "claim_id": claim_id, "claim_revision": claim["revision"], "change": change, "because": why, "detail": detail, "risk_tier": "HIGH",
               "generated_by": "deterministic:claim_loop@1", "assessment": assessment.value, "researcher_confirmed": False, "status": status,
               "effects": [{"interpretation": e["interpretation"], "index": e["index"], "effect": e["effect"], "assessment": e["assessment"]} for e in effects],
               "divergences": divergences, "provenance": "NO_OUTPUT"}
    with store.write_tx(token=token, require_roles=("importer", "owner", "author")) as tx:
        edges = [("depends_on", claim_id, claim["revision"], {"origin": "claim_change"})]
        for oid in sources:
            edges.append(("depends_on", oid, tx.head(oid)["head_revision"], {"origin": "claim_change"}))
        res = tx.add_revision(object_id=change_id, object_type="Result", content=content, edges=edges, origin=engine_origin("assess_claim_change", tx.actor_id))
        mark_high(tx, claim_id)
        tx.audit("assess_claim_change", "RECORDED", {"change_id": change_id, "claim_id": claim_id, "change": change})
    return {"change_id": change_id, "revision": res["revision"], "claim_id": claim_id, "change": change, "because": why, "status": status,
            "assessment": assessment.value, "detail": detail, "divergences": divergences}


def confirm_claim_change(store: StateStore, token: Optional[str], change_id: str, *, rationale: str) -> dict[str, Any]:
    """The researcher confirms a claim change (author or owner). Only then does it reach VERIFIED_WITHIN_SCOPE."""
    if len((rationale or "").strip()) < 10:
        raise KnowledgeError("RATIONALE_REQUIRED", "give the reason (10 characters at least)")
    with store.write_tx(token=token, require_roles=("author", "owner")) as tx:
        cur = tx.get(change_id)
        if not cur or cur["content"].get("layer") != "claim_change":
            raise KnowledgeError("CHANGE_NOT_FOUND", f"{change_id} is not a claim change")
        if cur["content"]["change"] is None:
            raise KnowledgeError("NOTHING_TO_CONFIRM", "no change was derived; there is nothing to confirm")
        rows = tx.conn.execute("SELECT relation,target_id,target_revision,provenance FROM edges WHERE source_id=? AND source_revision=?",
                               (change_id, cur["revision"])).fetchall()
        content = {**cur["content"], "researcher_confirmed": True, "researcher_rationale": rationale.strip(), "generated_by": "researcher",
                   "status": "CONFIRMED", "assessment": apply_caps(AssessmentStatus.VERIFIED_WITHIN_SCOPE, generated_by="researcher", researcher_confirmed=True).value}
        res = tx.add_revision(object_id=change_id, object_type="Result", content=content, edges=[(r[0], r[1], r[2], json.loads(r[3])) for r in rows],
                              origin=engine_origin("confirm_claim_change", tx.actor_id))
    return {"change_id": change_id, "revision": res["revision"], "assessment": content["assessment"]}


# --------------------------------------------------------------------------------------------------
# the Introduction's promises (direction 2)
# --------------------------------------------------------------------------------------------------

def link_promises(store: StateStore, token: Optional[str], argument_id: str, links: dict[str, list[str]]) -> dict[str, Any]:
    """Say which claims carry each `proposed_contribution` element of an argument. The elements' text is unchanged, so
    their adjudications stay valid."""
    with store.write_tx(token=token, require_roles=("importer", "owner", "author")) as tx:
        cur = tx.get(argument_id)
        if not cur or cur["object_type"] != "ContributionArgument":
            raise KnowledgeError("ARGUMENT_NOT_FOUND", f"{argument_id} is not a ContributionArgument")
        els = [dict(e) for e in cur["content"]["elements"]]
        for el_id, claims in links.items():
            el = next((e for e in els if e["id"] == el_id), None)
            if el is None or el["component"] != "proposed_contribution":
                raise KnowledgeError("ELEMENT_UNKNOWN", f"{el_id} is not a proposed_contribution element of {argument_id}")
            for cid in claims:
                if not (tx.head(cid) and tx.head(cid)["object_type"] == "Claim"):
                    raise KnowledgeError("CLAIM_NOT_FOUND", f"{cid} is not a Claim")
            el["claim_ids"] = list(claims)
        rows = tx.conn.execute("SELECT relation,target_id,target_revision,provenance FROM edges WHERE source_id=? AND source_revision=?",
                               (argument_id, cur["revision"])).fetchall()
        res = tx.add_revision(object_id=argument_id, object_type="ContributionArgument", content={**cur["content"], "elements": els},
                              edges=[(r[0], r[1], r[2], json.loads(r[3])) for r in rows], origin=engine_origin("link_promises", tx.actor_id))
    return {"argument_id": argument_id, "revision": res["revision"]}


def _latest_changes(store: StateStore) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    with store.read() as conn:
        for r in conn.execute("SELECT object_id FROM object_heads WHERE object_type='Result' ORDER BY object_id"):
            o = store.get_object(r[0], conn=conn)
            if o["content"].get("layer") == "claim_change":
                out[o["content"]["claim_id"]] = {"change_id": o["object_id"], "revision": o["revision"], **{k: o["content"][k] for k in (
                    "change", "because", "assessment", "researcher_confirmed", "detail")}}
    return out


def promise_check(store: StateStore, token: Optional[str], argument_id: str, *, check_id: str) -> dict[str, Any]:
    """Check the Introduction's `proposed_contribution` promises against the claim changes. Suggestions are proposals with
    their basis; nothing is applied to the manuscript."""
    arg = store.get_object(argument_id)
    if not arg or arg["object_type"] != "ContributionArgument":
        raise KnowledgeError("ARGUMENT_NOT_FOUND", f"{argument_id} is not a ContributionArgument")
    changes = _latest_changes(store)
    promises, suggestions = [], []
    for el in arg["content"]["elements"]:
        if el["component"] != "proposed_contribution":
            continue
        linked = el.get("claim_ids") or []
        seen = [changes[c] for c in linked if c in changes and changes[c]["change"]]
        if not linked or not seen:
            status = "NOT_TESTED"
            why = "no claim is linked to this promise" if not linked else "none of its claims has a derived change yet"
        else:
            kinds = {c["change"] for c in seen}
            if kinds & {"REVERSED", "NOT_SUPPORTED"}:
                status = "NOT_KEPT"
            elif kinds & {"NARROWED", "QUALIFIED", "WEAKENED"}:
                status = "AT_RISK"
            else:
                status = "KEPT"
            why = "; ".join(f"{c['change_id']}: {c['change']}" for c in seen)
        promises.append({"element": el["id"], "text": el["text"], "claims": linked, "status": status, "because": why,
                         "changes": [{"change_id": c["change_id"], "change": c["change"], "assessment": c["assessment"]} for c in seen]})
        if status in {"AT_RISK", "NOT_KEPT"}:
            suggestions.append({"target": f"{argument_id}#{el['id']}", "kind": "revise_stated_contribution", "applied": False,
                                "suggestion": ("Narrow, qualify or withdraw the stated contribution so that it matches what the results support: " + why),
                                "basis": [{"kind": "project_run", "ref": c["change_id"]} for c in seen], "needs_researcher_decision": True})
    content = {"label": f"Promise check of {argument_id}", "summary": f"{sum(1 for p in promises if p['status'] == 'KEPT')} of {len(promises)} promise(s) kept.",
               "layer": "promise_check", "argument_id": argument_id, "promises": promises, "manuscript_revision_proposal": {"status": "PROPOSED", "suggestions": suggestions},
               "provenance": "NO_OUTPUT", "generated_by": "deterministic:claim_loop@1"}
    with store.write_tx(token=token, require_roles=("importer", "owner", "author")) as tx:
        edges = [("depends_on", argument_id, arg["revision"], {"origin": "promise_check"})]
        for c in {c["change_id"] for p in promises for c in p["changes"]}:
            edges.append(("depends_on", c, tx.head(c)["head_revision"], {"origin": "promise_check"}))
        tx.add_revision(object_id=check_id, object_type="Result", content=content, edges=sorted(edges), origin=engine_origin("promise_check", tx.actor_id))
    return {"check_id": check_id, "promises": promises, "manuscript_revision_proposal": content["manuscript_revision_proposal"]}
