"""Workflow 5 - Discussion & Theoretical Integration (deterministic evidence gate).

Reports what the structured evidence shows and keeps the layers of a discussion apart:

* observed findings: the result as recorded, verbatim, with its structured values and provenance;
* statistical inference: the classification of each result against each hypothesis it bears on
  (`evidence_fields.classify_against_hypothesis`), from structured fields only;
* theoretical interpretation: NOT_ASSESSED here (it needs a model step or the researcher);
* speculation: none is generated;
* unverified alternatives: the mechanisms recorded in the project, each NOT_VERIFIED unless an *executed* analysis
  that addresses it is recorded. A planned analysis never counts.

A contribution is reopened only for a stated reason: a result classified as challenging a hypothesis, a result the
author flagged `contradictory: true`, or a `challenges` relation between a result or passage and a claim. The wording
of a summary never reopens anything; lexical cues are listed under `review_flags` and change nothing.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from research_harness.workflows.contracts import ContextPackage, DiscussionResult, WorkflowOutput
from research_harness.workflows.evidence_fields import (
    classify_against_hypothesis,
    parse_hypothesis,
    parse_result,
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


def _upstream_result_records(upstream: list[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    records, consumed = [], []
    for art in upstream:
        d = art.to_dict() if hasattr(art, "to_dict") else (art if isinstance(art, dict) else {})
        if d.get("task_id"):
            consumed.append(d["task_id"])
        if d.get("workflow") == "results":
            records.extend(d.get("result", {}).get("structured_run_records", []))
    return records, consumed


def execute_discussion_workflow(
    context: ContextPackage,
    *,
    task_id: Optional[str] = None,
    limitations: Optional[list[str]] = None,
    upstream_artifacts: Optional[list[Any]] = None,
    **kwargs: Any,
) -> WorkflowOutput:
    """Execute the Discussion evidence gate over structured research context."""
    start_time = time.time()
    tid = task_id or f"TASK_DISC_{uuid.uuid4().hex[:8].upper()}"

    claims = assessed_claims(context)
    claim_text = {c["object_id"]: c["content"].get("text", "") for c in claims}
    theories = head_objects(context, "Theory")
    mechanisms = head_objects(context, "Mechanism")
    analyses = head_objects(context, "Analysis")
    relations = context.relations
    hypotheses = {c["object_id"]: parse_hypothesis(c["object_id"], c["content"]) for c in claims}

    # Results: those in the context (head revision) plus the structured records of upstream Results runs.
    results: list[dict[str, Any]] = []
    for r in head_objects(context, "Result"):
        results.append({"id": r["object_id"], "revision": r["revision"], "label": r["content"].get("label", r["object_id"]),
                        "summary": r["content"].get("summary", ""), "content": r["content"], "origin": "context"})
    upstream_records, consumed = _upstream_result_records(upstream_artifacts or kwargs.get("upstream_artifacts") or [])
    known = {(r["id"], r["revision"]) for r in results}
    for rec in upstream_records:
        if (rec.get("result_id"), rec.get("revision")) not in known:
            content = dict(rec.get("structured") or {})
            if rec.get("contradictory"):
                content["contradictory"] = True
            results.append({"id": rec["result_id"], "revision": rec["revision"], "label": rec.get("label", rec["result_id"]),
                            "summary": rec.get("summary", ""), "content": content, "origin": "upstream"})

    observed: list[dict[str, Any]] = []
    interpretations: list[dict[str, Any]] = []
    contradictions: list[dict[str, Any]] = []
    unresolved: list[str] = []
    review_flags: list[dict[str, Any]] = []
    classifications: list[dict[str, Any]] = []
    reopened_claims: dict[str, list[str]] = {}
    consistent_claims: dict[str, int] = {}
    bearing: dict[str, int] = {}

    for res in results:
        rid, rev = res["id"], res["revision"]
        fields = parse_result(rid, rev, res["content"])
        observed.append({"result_id": rid, "revision": rev, "label": res["label"], "summary": res["summary"],
                         "structured": fields.structured(), "provenance": fields.provenance or "NOT_RECORDED",
                         "origin": res["origin"]})
        flag = review_flag(rid, rev, res["summary"], "summary")
        if flag:
            review_flags.append(flag)

        linked = [(c["object_id"], result_bears_on_claim(fields, c["object_id"], relations)) for c in claims]
        linked = [(cid, why) for cid, why in linked if why]
        statistical: list[dict[str, Any]] = []
        for cid, why in linked:
            bearing[cid] = bearing.get(cid, 0) + 1
            cls = classify_against_hypothesis(hypotheses[cid], fields)
            statistical.append({"claim_id": cid, "linked_by": why, **cls.to_dict()})
            classifications.append({"result_id": rid, "revision": rev, "claim_id": cid, "linked_by": why, **cls.to_dict()})
            if cls.label == "CHALLENGES":
                reopened_claims.setdefault(cid, []).append(rid)
                contradictions.append({
                    "result_id": rid, "revision": rev, "claim_id": cid, "observed_finding": res["summary"],
                    "contradicted_expectation": f"{cid} predicts '{hypotheses[cid].predicted_direction}'",
                    "reconciliation_status": "CHALLENGES_CLAIM", "basis": {"kind": "classification", **cls.basis}})
            elif cls.label == "CONSISTENT":
                consistent_claims[cid] = consistent_claims.get(cid, 0) + 1
            elif cls.label == "NOT_ASSESSED":
                unresolved.append(f"{rid}@{rev} cannot be classified against {cid}: missing {', '.join(cls.missing)}")
            elif cls.label == "INCONCLUSIVE":
                unresolved.append(f"{rid}@{rev} is inconclusive for {cid}: {cls.note}")

        # An explicit author flag, or a `challenges` relation, also reopens (never wording).
        challenge_targets = [cid for cid, _ in linked if any(
            {rel["source_id"], rel["target_id"]} == {rid, cid} and rel["relation"] == "challenges" for rel in relations)]
        flagged_targets = [cid for cid, _ in linked] or [c["object_id"] for c in claims]
        reasons: list[tuple[str, str, str]] = [(cid, "LINKED_AS_CHALLENGE", "relation:challenges") for cid in challenge_targets]
        if fields.contradictory_flag:
            reasons += [(cid, "FLAGGED_BY_AUTHOR_AS_CONTRADICTORY", "author_flag:contradictory")
                        for cid in flagged_targets if cid not in challenge_targets]
        for cid, status, basis in reasons:
            if rid not in reopened_claims.get(cid, []):
                reopened_claims.setdefault(cid, []).append(rid)
                contradictions.append({
                    "result_id": rid, "revision": rev, "claim_id": cid, "observed_finding": res["summary"],
                    "contradicted_expectation": f"{cid} as stated: '{claim_text.get(cid, '')}'",
                    "reconciliation_status": status, "basis": {"kind": basis}})

        if statistical:
            bits = ", ".join(f"{s['claim_id']}: {s['classification']}" for s in statistical)
            supported = f"Statistical relation to the hypotheses (from structured fields): {bits}."
        else:
            supported = "No structured basis relates this result to a hypothesis under assessment; not assessed."
        interpretations.append({
            "result_id": rid, "revision": rev,
            "direct_data_observation": res["summary"],
            "statistical_inference": statistical,
            "theoretical_interpretation": "NOT_ASSESSED: requires interpretation by a model step or the researcher",
            "speculation": [],
            "supported_interpretation": supported,
            "scholarly_inference": "NOT_ASSESSED: no scholarly inference is generated by the deterministic gate",
            "distinction_note": "Observed data, statistical inference, theoretical interpretation and speculation are kept apart.",
        })

    # Passages that challenge a claim by relation or by an explicit structured flag.
    for ex in context.excerpts:
        pid, prev = ex.get("passage_id"), ex.get("revision")
        content = next((o["content"] for o in context.objects if o["object_id"] == pid and o["revision"] == prev), {})
        flag = review_flag(pid, prev, ex.get("excerpt") or ex.get("quote") or "", "excerpt")
        if flag:
            review_flags.append(flag)
        for cid in claim_text:
            challenged = any({rel["source_id"], rel["target_id"]} == {pid, cid} and rel["relation"] == "challenges"
                             for rel in relations)
            if challenged or content.get("contradictory") is True:
                reopened_claims.setdefault(cid, []).append(pid)
                contradictions.append({
                    "literature_passage_id": pid, "source_id": ex.get("source_id"), "claim_id": cid,
                    "observed_finding": ex.get("excerpt") or ex.get("normalized_claim"),
                    "contradicted_expectation": f"{cid} as stated: '{claim_text[cid]}'",
                    "reconciliation_status": "LITERATURE_CHALLENGES_CLAIM",
                    "basis": {"kind": "relation:challenges" if challenged else "author_flag:contradictory"}})

    # Theoretical implications are open questions, not conclusions.
    implications = [f"NOT_ASSESSED: whether {kind} '{o['content'].get('label', o['object_id'])}' is supported, bounded or "
                    f"challenged by the findings requires interpretation"
                    for kind, objs in (("theory", theories), ("mechanism", mechanisms)) for o in objs]

    # Unverified alternatives: mechanisms, unless an EXECUTED analysis that addresses them is recorded.
    alternatives = []
    for m in mechanisms:
        mid = m["object_id"]
        addressed = [a["object_id"] for a in analyses if a["content"].get("status") == "EXECUTED"
                     and a["content"].get("purpose") in {"robustness", "placebo"} and mid in (a["content"].get("addresses") or [])]
        alternatives.append({
            "rival_explanation": m["content"].get("label", mid), "mechanism_id": mid, "plausibility": "NOT_ASSESSED",
            "evaluative_status": ("An executed analysis that addresses it is recorded (its results are not verified here): "
                                  + ", ".join(addressed)) if addressed else "No executed analysis recorded that addresses this explanation.",
            "verification_status": "ANALYSIS_RECORDED_NOT_VERIFIED" if addressed else "NOT_VERIFIED"})

    boundary: list[str] = []
    for c in claims:
        for key in ("scope", "boundary_conditions"):
            val = c["content"].get(key)
            boundary.extend(val if isinstance(val, list) else [val] if isinstance(val, str) and val else [])
    boundary = boundary or ["NOT_ASSESSED: no scope or boundary conditions are recorded for the claims under assessment"]

    revised: list[str] = []
    for c in claims:
        cid, rev = c["object_id"], c["revision"]
        if cid in reopened_claims:
            items = sorted(set(reopened_claims[cid]))
            revised.append(f"REOPEN {cid}@{rev}: {len(items)} item(s) challenge it ({', '.join(items)}); the contribution "
                           f"statement needs revision or qualification: '{claim_text[cid]}'")
        elif consistent_claims.get(cid):
            revised.append(f"No revision indicated for {cid}@{rev} by the classified results ({consistent_claims[cid]} consistent); "
                           f"interpretation and robustness are not assessed.")
        elif bearing.get(cid):
            revised.append(f"NOT_ASSESSED: the results bearing on {cid}@{rev} are inconclusive or cannot be classified; "
                           f"no revision can be indicated.")
        else:
            revised.append(f"NOT_ASSESSED: no result bears on {cid}@{rev}.")

    if not analyses:
        unresolved.append("No executed robustness analysis is recorded; alternative explanations remain unverified.")
    unresolved.extend(limitations or [])

    reopens = bool(reopened_claims)
    result_data = DiscussionResult(
        supported_interpretations=interpretations,
        theoretical_implications=implications,
        alternative_explanations=alternatives,
        contradictions_with_earlier_literature=contradictions,
        scope_and_boundary_conditions=boundary,
        revised_contribution_statements=revised,
        unresolved_questions=unresolved,
        reopens_contribution_workflow=reopens,
    )
    out = result_data.to_dict()
    out.update({
        "observed_findings": observed, "statistical_classifications": classifications, "review_flags": review_flags,
        "contradictions": contradictions, "contradictions_identified": contradictions, "revised_contributions": revised,
        "reopens_contribution_assessment": reopens, "plausible_alternative_explanations": alternatives,
        "reopened_claims": {k: sorted(set(v)) for k, v in reopened_claims.items()},
        "upstream_artifacts_consumed": consumed,
    })

    status = budget_gate(context, INSUFFICIENT if not results else NEEDS_VERIFICATION)
    notes = ["Evidence gate: results are classified against hypotheses from structured fields only; interpretation, "
             "speculation and verification of alternatives are not generated here."]
    return finish("discussion", tid, context, out, status, start_time, notes)
