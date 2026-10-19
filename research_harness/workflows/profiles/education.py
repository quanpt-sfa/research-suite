"""Discipline profile for Education research."""
from __future__ import annotations

from research_harness.workflows.profiles.base import DisciplineProfile

EDUCATION_PROFILE = DisciplineProfile(
    identifier="education",
    title="Education Research Profile",
    description="Quantitative policy evaluation, school district interventions, learning analytics, and psychometrics.",
    research_traditions=[
        "Education policy evaluation and school reform interventions",
        "Teacher value-added modeling (VAM) and classroom observation metrics",
        "Psychometric assessment, item response theory (IRT), and standardized test scaling",
        "Early childhood, socioeconomic achievement gaps, and higher education retention",
    ],
    preferred_evidence_types=[
        "State longitudinal administrative student databases (SLDS)",
        "NCES national datasets (ECLS, HSLS, IPEDS, NAEP)",
        "District-level administrative registries with student-teacher links",
        "Standardized psychometric test score batteries and survey responses",
    ],
    methodological_checklist=[
        {
            "item": "hierarchical_clustering",
            "prompt": "Are standard errors clustered at the school, district, or classroom level to account for nested student grouping?",
        },
        {
            "item": "student_attrition_and_mobility",
            "prompt": "Is student transfer, dropout, or non-random missingness bounded through Lee bounds or attrition sensitivity checks?",
        },
        {
            "item": "spillover_effects",
            "prompt": "Does the design evaluate potential classroom or peer spillover effects from treated students to untreated peers?",
        },
    ],
    specialized_terminology={
        "VAM": "Value-Added Model estimating teacher or school contributions to student achievement growth",
        "IRT": "Item Response Theory modeling probability of correct responses given latent ability",
        "SLDS": "State Longitudinal Data System tracking educational trajectories",
        "IEP": "Individualized Education Program for students receiving special education",
    },
    validity_questions=[
        "Is test-score metric manipulation or teaching to the test distorting observed gains?",
        "Do student-teacher non-random sorting patterns bias value-added estimates?",
        "Are findings generalizable beyond the specific urban/suburban district context studied?",
    ],
    common_inferential_limitations=[
        "Non-random student sorting across classrooms and schools",
        "Ceiling or floor effects in standardized assessment scales",
        "Attrition bias due to student mobility across school district boundaries",
    ],
)
