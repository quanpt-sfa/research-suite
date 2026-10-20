"""Workflow 1 - Contribution Finding (deterministic evidence gate).

Structures the contribution candidates and says what stands between each and a defensible contribution. It does not
decide that a contribution is novel, identified or supported:

* **Identification** is verified for a claim only when an `Adjudication` with `scope: identification` and
  `verdict: SUPPORTED` exists for it *and* an `Analysis` linked to it records an `identification_strategy`. A claim
  tier is an assertion about the kind of inference, never evidence of it.
* **Novelty** is never verified here. It is `NOVELTY_CONTESTED` when a source or passage is linked to the claim by a
  `challenges` relation, `NOVELTY_NOT_VERIFIED` when the closest papers' full texts have not been read (or there are
  none: absence of retrieved papers is not novelty), and at best `NOVELTY_PENDING_REVIEW`.
* **Competing contributions** are the question's sources (challenge-linked ones first), with the status of their full
  text. The researcher's other claims are `related_project_claims`, not competitors from the literature.
* **Evidence** is classified by relations, structured classification (`evidence_fields`) or explicit flags; what has
  none is `unclassified_evidence`. Wording classifies nothing.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from research_harness.workflows.contracts import ContextPackage, ContributionFindingResult, WorkflowOutput
from research_harness.workflows.evidence_fields import (
    classify_against_hypothesis,
    parse_hypothesis,
    parse_result,
    passage_relationship,
    result_bears_on_claim,
    review_flag,
)
from research_harness.workflows.skills._common import (
    INSUFFICIENT,
    NEEDS_VERIFICATION,
    assessed_claims,
    budget_gate,
    finish,
    head_objects,
)


def _linked(a: str, b: str, relations: list[dict[str, Any]], kinds: set[str]) -> bool:
    return any({r["source_id"], r["target_id"]} == {a, b} and r["relation"] in kinds for r in relations)


def execute_contribution_workflow(
    context: ContextPackage,
    *,
    task_id: Optional[str] = None,
    contribution_hypotheses: Optional[list[str]] = None,
    upstream_artifacts: Optional[list[Any]] = None,
    **kwargs: Any,
) -> WorkflowOutput:
    """Execute the Contribution Finding evidence gate over structured research context."""
    start_time = time.time()
    tid = task_id or f"TASK_CONTRIB_{uuid.uuid4().hex[:8].upper()}"

    claims = assessed_claims(context)
    assessed_ids = [c["object_id"] for c in claims]
    related = [c for c in head_objects(context, "Claim") if c["object_id"] not in assessed_ids]
    sources = head_objects(context, "Source")
    mechanisms = head_objects(context, "Mechanism")
    theories = head_objects(context, "Theory")
    results = head_objects(context, "Result")
    adjudications = head_objects(context, "Adjudication")
    analyses = head_objects(context, "Analysis")
    relations = context.relations
    hypotheses = {cid: parse_hypothesis(cid, next(c for c in claims if c["object_id"] == cid)["content"]) for cid in assessed_ids}
    fulltext = {ft["source_id"]: ft for ft in context.full_texts}
    passage_content = {(o["object_id"], o["revision"]): o["content"] for o in context.objects if o["object_type"] == "EvidencePassage"}

    # Upstream handoff: the contradictions a Discussion run recorded (structured items, not scanned text).
    tensions: list[dict[str, Any]] = []
    consumed: list[str] = []
    for art in upstream_artifacts or kwargs.get("upstream_artifacts") or []:
        d = art.to_dict() if hasattr(art, "to_dict") else (art if isinstance(art, dict) else {})
        if d.get("task_id"):
            consumed.append(d["task_id"])
        if d.get("workflow") == "discussion":
            for item in (d.get("result", {}).get("contradictions") or []):
                tensions.append({"claim_id": item.get("claim_id"), "finding": item.get("observed_finding"),
                                 "status": item.get("reconciliation_status")})

    # Evidence classification.
    supporting: list[dict[str, Any]] = []
    contradicting: list[dict[str, Any]] = []
    unclassified: list[dict[str, Any]] = []
    review_flags: list[dict[str, Any]] = []
    challenged_sources: dict[str, set[str]] = {}  # source id -> claims it is challenge-linked to
    supporting_sources: dict[str, set[str]] = {}

    for ex in context.excerpts:
        pid, prev, sid = ex.get("passage_id"), ex.get("revision"), ex.get("source_id")
        relationship, links = passage_relationship(pid, passage_content.get((pid, prev), {}), assessed_ids, relations)
        entry = {**ex, "relationship": relationship, "linked_claims": links}
        if relationship == "SUPPORTS_CLAIM":
            supporting.append(entry)
            supporting_sources.setdefault(sid, set()).update(l["claim_id"] for l in links)
        elif relationship in {"CHALLENGES_CLAIM", "CONFLICTING_LINKS"}:
            contradicting.append(entry)
            challenged_sources.setdefault(sid, set()).update(l["claim_id"] for l in links if l["relation"] == "challenges")
        else:
            unclassified.append(entry)
        flag = review_flag(pid, prev, ex.get("excerpt") or "", "excerpt")
        if flag:
            review_flags.append(flag)

    for res in results:
        rid, rev, content = res["object_id"], res["revision"], res["content"]
        fields = parse_result(rid, rev, content)
        base = {"result_id": rid, "revision": rev, "summary": content.get("summary", ""), "structured": fields.structured()}
        placed = False
        for cid in assessed_ids:
            why = result_bears_on_claim(fields, cid, relations)
            if why is None and not fields.contradictory_flag:
                continue
            cls = classify_against_hypothesis(hypotheses[cid], fields)
            entry = {**base, "claim_id": cid, "linked_by": why or "author_flag:contradictory", **cls.to_dict()}
            relation_challenges = _linked(rid, cid, relations, {"challenges"})
            if cls.label == "CHALLENGES" or relation_challenges or fields.contradictory_flag:
                contradicting.append(entry)
            elif cls.label == "CONSISTENT" or _linked(rid, cid, relations, {"supports"}):
                supporting.append(entry)
            else:
                unclassified.append(entry)
            placed = True
        if not placed:
            unclassified.append({**base, "classification": "NOT_ASSESSED", "note": "no claim under assessment is linked to this result"})
        flag = review_flag(rid, rev, content.get("summary", ""), "summary")
        if flag:
            review_flags.append(flag)

    for adj in adjudications:
        subject = (adj["content"].get("subject") or {}).get("object_id")
        verdict = adj["content"].get("verdict")
        entry = {"adjudication_id": adj["object_id"], "scope": adj["content"].get("scope"), "verdict": verdict,
                 "rationale": adj["content"].get("rationale", ""), "claim_id": subject}
        (contradicting if verdict in {"NOT_SUPPORTED", "UNCLEAR"} else supporting).append(entry)

    for s in sources:
        sid = s["object_id"]
        if any(_linked(sid, cid, relations, {"challenges"}) for cid in assessed_ids):
            challenged_sources.setdefault(sid, set()).update(cid for cid in assessed_ids if _linked(sid, cid, relations, {"challenges"}))
        if any(_linked(sid, cid, relations, {"supports"}) for cid in assessed_ids):
            supporting_sources.setdefault(sid, set()).update(cid for cid in assessed_ids if _linked(sid, cid, relations, {"supports"}))

    # Competing contributions: the question's sources, challenge-linked first.
    closest: list[dict[str, Any]] = []
    for s in sorted(sources, key=lambda x: (x["object_id"] not in challenged_sources, x["object_id"])):
        sid = s["object_id"]
        relation = "challenges" if sid in challenged_sources else "supports" if sid in supporting_sources else "rq_linked"
        closest.append({
            "source_id": sid, "revision": s["revision"], "citation_key": s["content"].get("citation_key", sid),
            "relation_to_claim": relation, "claims": sorted(challenged_sources.get(sid, set()) | supporting_sources.get(sid, set())),
            "fulltext_status": fulltext.get(sid, {}).get("fulltext_status", "NOT_VERIFIED"),
            "abstract": (s["content"].get("abstract") or "")[:400],
            "passages": [e["passage_id"] for e in context.excerpts if e.get("source_id") == sid]})
    known_sources = {s["object_id"] for s in sources}
    for ex in context.excerpts:
        if ex.get("source_id") not in known_sources:
            closest.append({"source_id": ex.get("source_id", "UNKNOWN"), "passage_id": ex.get("passage_id"),
                            "locator": ex.get("locator", ""), "relation_to_claim": "unlinked_passage",
                            "competing_claim": ex.get("normalized_claim") or ex.get("excerpt", "")})

    # Novelty and identification (per claim, then overall).
    missing_fulltext = [s["content"].get("citation_key", s["object_id"]) for s in sources
                        if fulltext.get(s["object_id"], {}).get("fulltext_status") != "FULLTEXT_LOADED"]
    all_read = bool(sources) and not missing_fulltext

    def novelty_for(cid: str) -> str:
        if any(cid in cl for cl in challenged_sources.values()):
            return "NOVELTY_CONTESTED"
        return "NOVELTY_PENDING_REVIEW" if all_read else "NOVELTY_NOT_VERIFIED"

    def identification_for(cid: str, tier: str) -> str:
        if tier != "causal":
            return "IDENTIFICATION_NOT_REQUIRED_FOR_TIER"
        adjudicated = any((a["content"].get("subject") or {}).get("object_id") == cid
                          and a["content"].get("scope") == "identification" and a["content"].get("verdict") == "SUPPORTED"
                          for a in adjudications)
        designed = any(a["content"].get("identification_strategy") and (
            _linked(a["object_id"], cid, relations, {"tests", "depends_on", "supports", "estimated_with", "derived_from"})
            or cid in (a["content"].get("hypothesis_ids") or [])) for a in analyses)
        return "IDENTIFIED_BY_VERIFIED_DESIGN" if (adjudicated and designed) else "IDENTIFICATION_NOT_VERIFIED"

    candidates: list[dict[str, Any]] = []
    for c in claims:
        cid, tier = c["object_id"], c["content"].get("claim_tier", "descriptive")
        factors = [f"Claim tier asserted: {tier} (an assertion about the kind of inference, not evidence of it)"]
        factors += [f"Mechanism in play: {m['content'].get('label', m['object_id'])}" for m in mechanisms]
        candidates.append({
            "claim_id": cid, "revision": c["revision"], "proposed_contribution": c["content"].get("text", ""),
            "claim_tier": tier, "differentiation_factors": factors, "stated_hypotheses": contribution_hypotheses or [],
            "identification_status": identification_for(cid, tier), "novelty_status": novelty_for(cid),
            "rival_mechanisms": [m["content"].get("label", m["object_id"]) for m in mechanisms],
            "challenged_by_sources": sorted(s for s, cl in challenged_sources.items() if cid in cl)})

    statuses = [c["novelty_status"] for c in candidates] or ["NOVELTY_NOT_VERIFIED"]
    novelty_status = next((s for s in ("NOVELTY_CONTESTED", "NOVELTY_NOT_VERIFIED", "NOVELTY_PENDING_REVIEW") if s in statuses))
    causal = [c for c in candidates if c["claim_tier"] == "causal"]
    identification_status = ("IDENTIFIED_BY_VERIFIED_DESIGN" if causal and all(c["identification_status"] == "IDENTIFIED_BY_VERIFIED_DESIGN" for c in causal)
                             else "IDENTIFICATION_NOT_VERIFIED")
    if novelty_status == "NOVELTY_CONTESTED":
        plan = "Compare the challenging source(s) with the claim point by point: " + ", ".join(sorted(challenged_sources)) + "."
    elif novelty_status == "NOVELTY_NOT_VERIFIED":
        shown = ", ".join(missing_fulltext[:3]) if missing_fulltext else "prior literature"
        plan = f"Retrieve and read the full texts of the closest competing studies ({shown}) before any novelty claim."
    else:
        plan = "Compare the claim with each loaded competitor full text, point by point."

    assumptions = [f"{a['object_id']}: {x}" for a in analyses for x in (a["content"].get("assumptions") or []) if isinstance(x, str)]
    assumptions += [f"Theoretical framing '{t['content'].get('label', t['object_id'])}' applies to the setting studied" for t in theories]
    assumptions += [f"Mechanism '{m['content'].get('label', m['object_id'])}' operates as proposed" for m in mechanisms]
    assumptions = assumptions or ["NOT_ASSESSED: no assumptions are recorded for the claims under assessment"]

    missing: list[str] = []
    if not results:
        missing.append("No empirical result bearing on the claims under assessment is recorded.")
    if not adjudications:
        missing.append("Independent adjudication of the claims' support is pending.")
    if contradicting:
        missing.append(f"{len(contradicting)} contradicting evidence item(s) require explicit reconciliation.")
    if novelty_status == "NOVELTY_NOT_VERIFIED":
        missing.append(f"Novelty verification incomplete: {len(missing_fulltext)} competing source(s) lack inspected full text."
                       if sources else "No competing source is recorded; absence of retrieved papers is not evidence of novelty.")
    if novelty_status == "NOVELTY_CONTESTED":
        missing.append("Novelty is contested by at least one source linked to the claim.")
    if causal and identification_status != "IDENTIFIED_BY_VERIFIED_DESIGN":
        missing.append("Causal identification is not verified (needs an Analysis with an identification strategy and a supporting identification adjudication).")
    if tensions:
        missing.append(f"{len(tensions)} contradiction(s) from a Discussion run remain to be reconciled.")

    alternatives = [f"Mechanism-centred formulation: frame the contribution around '{m['content'].get('label', m['object_id'])}'" for m in mechanisms]
    alternatives += [f"Provisional reformulation: reconcile the claim with the contradicting finding ({t['finding']})" for t in tensions[:2] if t.get("finding")]

    has_conflict = bool(contradicting or tensions)
    confidence = (f"{len(supporting)} supporting, {len(contradicting)} contradicting and {len(unclassified)} unclassified evidence item(s); "
                  f"novelty: {novelty_status}; identification: {identification_status}. "
                  + ("Unresolved tension remains. " if has_conflict else "") + "Not a verified contribution.")

    result_data = ContributionFindingResult(
        candidate_contributions=candidates, closest_competing_contributions=closest,
        exact_point_of_differentiation="; ".join(candidates[0]["differentiation_factors"]) if candidates else "NOT_ASSESSED: no claim under assessment",
        supporting_evidence=supporting, contradicting_evidence=contradicting, necessary_assumptions=assumptions,
        missing_verification=missing, alternative_contribution_formulations=alternatives, confidence_explanation=confidence,
        is_preliminary=True)
    out = result_data.to_dict()
    out.update({
        "identification_status": identification_status, "novelty_status": novelty_status, "novelty_verification_plan": plan,
        "missing_fulltext_competitors": missing_fulltext, "unclassified_evidence": unclassified,
        "related_project_claims": [{"claim_id": c["object_id"], "revision": c["revision"], "text": c["content"].get("text", "")} for c in related],
        "review_flags": review_flags, "upstream_artifacts_consumed": consumed})

    status = budget_gate(context, INSUFFICIENT if not claims else NEEDS_VERIFICATION)
    return finish("contribution", tid, context, out, status, start_time,
                  ["Contribution candidates structured from the recorded evidence; novelty and identification are not verified here."])
