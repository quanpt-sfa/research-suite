"""Discipline profile for Accounting research."""
from __future__ import annotations

from research_harness.workflows.profiles.base import DisciplineProfile

ACCOUNTING_PROFILE = DisciplineProfile(
    identifier="accounting",
    title="Accounting Research Profile",
    description="Archival and empirical financial reporting, auditing, managerial control, and regulatory disclosure.",
    research_traditions=[
        "Positive Accounting Theory and capital markets research",
        "Earnings management, discretionary accruals, and real activities manipulation",
        "Auditor incentives, audit fees, and financial statement restatements",
        "Contracting theory (executive compensation, debt covenants)",
    ],
    preferred_evidence_types=[
        "SEC 10-K/10-Q disclosures and EDGAR filings",
        "Compustat and CRSP financial panels",
        "Audit Analytics restatement and internal control feeds",
        "IBES analyst earnings forecasts and earnings announcements",
    ],
    methodological_checklist=[
        {
            "item": "discretionary_accruals_specification",
            "prompt": "Are accrual models (e.g. Modified Jones) estimated by two-digit SIC and year with performance matching?",
        },
        {
            "item": "standard_error_clustering",
            "prompt": "Are standard errors two-way clustered by firm and year/quarter to address cross-sectional and serial correlation?",
        },
        {
            "item": "treatment_selection_endogeneity",
            "prompt": "Does the design address self-selection into voluntary disclosure or accounting choice (e.g. Heckman, entropy balance, DID)?",
        },
    ],
    specialized_terminology={
        "ERC": "Earnings Response Coefficient (abnormal return sensitivity to unexpected earnings)",
        "DA": "Discretionary Accruals (proxy for accounting discretion)",
        "ICW": "Internal Control Weakness under SOX 404",
        "Restatement": "Material correction of prior period financial reporting",
    },
    validity_questions=[
        "Is the observed relation driven by economic performance rather than accounting manipulation?",
        "Do regulatory regime shifts (e.g. SOX, ASC 606) confound the historical sample period?",
        "Is survivor or delisting bias accounted for in financial statement panels?",
    ],
    common_inferential_limitations=[
        "Measurement error in discretionary accrual proxies",
        "Confounding macro shocks occurring during earnings release windows",
        "Endogeneity in managerial reporting choices",
    ],
)
