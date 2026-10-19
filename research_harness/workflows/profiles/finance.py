"""Discipline profile for Finance research."""
from __future__ import annotations

from research_harness.workflows.profiles.base import DisciplineProfile

FINANCE_PROFILE = DisciplineProfile(
    identifier="finance",
    title="Finance Research Profile",
    description="Empirical asset pricing, corporate finance, market microstructure, and behavioral finance.",
    research_traditions=[
        "Cross-sectional and time-series asset pricing factor models",
        "Short- and long-horizon event studies with market-adjusted benchmarks",
        "Corporate governance, capital structure, and M&A transactions",
        "Market microstructure, liquidity measures, and high-frequency order book dynamics",
    ],
    preferred_evidence_types=[
        "CRSP stock files and Ken French factor portfolios",
        "Thomson Reuters/Refinitiv institutional holdings (13F)",
        "TAQ (Trade and Quote) tick-level execution data",
        "DealScan loan contracts and SDC Platinum M&A databases",
    ],
    methodological_checklist=[
        {
            "item": "factor_model_adjustment",
            "prompt": "Are abnormal returns benchmarked against standard multi-factor models (CAPM, Fama-French 3/5, Carhart 4, Q-factor)?",
        },
        {
            "item": "cross_sectional_correlation",
            "prompt": "Are panel regressions estimated using Fama-MacBeth with Newey-West lags, or double-clustered standard errors?",
        },
        {
            "item": "event_window_contamination",
            "prompt": "Are short-horizon event windows [-1, +1] free of concurrent earnings announcements or macroeconomic releases?",
        },
    ],
    specialized_terminology={
        "CAR": "Cumulative Abnormal Return across specified event window",
        "Alpha": "Intercept of asset return regression on risk factor portfolios",
        "Bid-Ask Spread": "Effective or quoted spread proxy for illiquidity",
        "IVol": "Idiosyncratic volatility relative to benchmark factor model",
    },
    validity_questions=[
        "Does the anomaly survive microcap exclusions and transaction cost hurdles?",
        "Is factor risk properly distinguished from alpha or mispricing?",
        "Are event dates verified for pre-market or after-hours announcement timing?",
    ],
    common_inferential_limitations=[
        "Look-ahead and survivorship biases in security returns",
        "Data snooping and p-hacking in factor zoo replication",
        "Low statistical power in small sample event studies",
    ],
)
