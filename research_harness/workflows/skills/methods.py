"""Workflow 3 - Methods (deterministic evidence gate).

States what the project records about its methods and what is missing. It does not choose, justify or fill in a
design: there is no default design, estimator, set of controls, fixed effects or clustering. Those depend on the
question, the measurement, the data and the literature; evaluating them against a method's prerequisites is the job of
a selected methodological playbook (Phase 3D), not of a template.

Reported:
* the designs the researcher proposed (`candidate_methods`) and those recorded on Analysis objects, each
  `justified: False` with its prerequisites marked as not yet evaluated;
* specifications exactly as the researcher recorded them, with their planned/executed status;
* how constructs are (not) operationalized, from the Construct/Proxy objects;
* the structure of each dataset when recorded.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from research_harness.workflows.contracts import ContextPackage, MethodsEvaluationResult, WorkflowOutput
from research_harness.workflows.profiles.registry import get_profile
from research_harness.workflows.skills._common import (
    NEEDS_VERIFICATION,
    assessed_claims,
    budget_gate,
    finish,
    head_objects,
)

_PREREQUISITES_PENDING = ("Design-specific prerequisites, diagnostics and alternatives are not evaluated until a "
                          "methodological playbook is selected; no design is recommended or justified here.")


def execute_methods_workflow(
    context: ContextPackage,
    *,
    task_id: Optional[str] = None,
    candidate_methods: Optional[list[str]] = None,
    discipline_profile: Optional[str] = None,
    **kwargs: Any,
) -> WorkflowOutput:
    """Execute the Methods evidence gate over structured research context."""
    start_time = time.time()
    tid = task_id or f"TASK_METH_{uuid.uuid4().hex[:8].upper()}"

    constructs = head_objects(context, "Construct")
    proxies = head_objects(context, "Proxy")
    datasets = head_objects(context, "Dataset")
    analyses = head_objects(context, "Analysis")
    claims = assessed_claims(context)

    # 1. Designs: those the researcher proposed, and those recorded on analyses. Nothing is added by default.
    designs: list[dict[str, Any]] = []
    for name in dict.fromkeys(candidate_methods or []):
        designs.append({"design_name": name, "type": "researcher_proposed", "status": "CANDIDATE_METHOD", "justified": False,
                        "justification_status": "NOT_ASSESSED", "required_information": [_PREREQUISITES_PENDING],
                        "source": "candidate_methods"})
    for a in analyses:
        c = a["content"]
        name = c.get("identification_strategy") or c.get("design")
        if name:
            executed = c.get("status") == "EXECUTED"
            designs.append({"design_name": name, "type": "recorded_in_analysis", "analysis_id": a["object_id"],
                            "status": "RECORDED_IN_PROJECT" if executed else "CANDIDATE_METHOD", "justified": False,
                            "justification_status": "NOT_ASSESSED", "required_information": [_PREREQUISITES_PENDING],
                            "source": f"Analysis {a['object_id']}:rev{a['revision']}"})

    # 2. Assumptions exactly as recorded.
    assumptions = [f"{a['object_id']}: {x}" for a in analyses for x in (a["content"].get("assumptions") or []) if isinstance(x, str)]

    # 3. Measurement: how each construct is operationalized, from the recorded objects.
    requirements: list[str] = []
    for c in constructs:
        label = c["content"].get("label", c["object_id"])
        linked = [p for p in proxies if p["content"].get("construct_id") == c["object_id"]
                  or any(r["source_id"] == p["object_id"] and r["target_id"] == c["object_id"]
                         and r["relation"] in {"operationalizes", "measures"} for r in context.relations)]
        if linked:
            requirements.append(f"Construct '{label}' is operationalized by: "
                                + ", ".join(p["content"].get("label", p["object_id"]) for p in linked)
                                + " (validity of the proxy is not assessed here)")
        else:
            requirements.append(f"Construct '{label}' has no linked Proxy.")
    for d in datasets:
        structure = d["content"].get("structure")
        requirements.append(f"Dataset '{d['content'].get('label', d['object_id'])}': structure "
                            + (str(structure) if structure else "NOT_RECORDED"))

    # 4. Specifications: only what the researcher recorded.
    plans = [{"specification_name": a["content"].get("label", a["object_id"]), "analysis_id": a["object_id"],
              "purpose": a["content"].get("purpose"), "status": a["content"].get("status") or "NOT_RECORDED",
              "specification": a["content"].get("specification") or {}, "estimator": a["content"].get("estimator"),
              "datasets_used": [d["object_id"] for d in datasets if any(
                  r["source_id"] == a["object_id"] and r["target_id"] == d["object_id"] for r in context.relations)]}
             for a in analyses]

    # 5. Optional discipline checklist (a static list of items to consider; none is marked addressed).
    prof_id = discipline_profile or kwargs.get("discipline") or (context.manifest.get("profile") or {}).get("profile_id")
    profile_results = None
    if prof_id:
        try:
            prof = get_profile(prof_id)
            profile_results = {
                "profile": prof.identifier, "traditions": prof.research_traditions,
                "checklist_evaluations": [{"item": it["item"], "prompt": it["prompt"], "compliance_status": "REQUIRES_INFORMATION"}
                                          for it in prof.methodological_checklist],
                "recommended_validity_questions": prof.validity_questions}
        except KeyError:
            pass

    limitations = [_PREREQUISITES_PENDING]
    if not designs:
        tiers = sorted({c["content"].get("claim_tier", "unspecified") for c in claims})
        limitations.append("No candidate design is recorded" + (f" for claim tier(s) {tiers}" if tiers else "")
                           + "; none is proposed by default. Name one in candidate_methods or record it on an Analysis.")
    if not (datasets or analyses or constructs):
        limitations.append("No dataset, analysis or construct is recorded; the empirical design cannot be described.")

    result_data = MethodsEvaluationResult(
        candidate_research_designs=designs, assumptions=assumptions, identification_or_measurement_requirements=requirements,
        specification_plans=plans, alternative_methods=[], diagnostic_tests=[], limitations_and_unresolved_choices=limitations,
        profile_checklist_results=profile_results)
    out = result_data.to_dict()
    status = budget_gate(context, NEEDS_VERIFICATION)  # the stage reports requirements; it never certifies a design
    return finish("methods", tid, context, out, status, start_time,
                  ["Methods reported as recorded; no design, estimator, control, fixed effect or clustering is assumed."])
