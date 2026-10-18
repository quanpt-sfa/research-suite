"""Claim formation provenance (HARKing-aware, never retroactively flattering).

Research is allowed to loop: Results -> Theory -> a new hypothesis. The engine therefore does not
forbid that loop; it *records* when a claim was first recorded in the ledger, what the proposer
declared about results exposure, what the ledger itself shows, and every later change.

Rules (all computed server-side):

* ``inference_status`` is one of EXPLORATORY | CONFIRMATORY_DECLARED | UNKNOWN.
* Missing information is UNKNOWN. It is never promoted to "a priori" or "confirmatory".
* CONFIRMATORY_DECLARED requires: declared confirmatory, an explicit *empty*
  ``observed_results_at_formation``, and no Result object in the ledger when the claim was first
  recorded. It is still only a *declaration backed by ledger order*, not proof of pre-registration.
* A later revision can keep or lower the status, never raise it (no silent relabelling of a
  post-hoc claim as pre-specified). A confirmatory claim whose text/tier changes after Results
  exist is downgraded to EXPLORATORY.
"""
from __future__ import annotations

import hashlib
import sqlite3
from typing import Optional

from research_harness.core.errors import ProposalRejection
from research_harness.core.model import Decision

EXPLORATORY = "EXPLORATORY"
CONFIRMATORY = "CONFIRMATORY_DECLARED"
UNKNOWN = "UNKNOWN"
# How strongly a status *claims* a-priori formation. A revision may keep or lower it, never raise it.
_APRIORI = {EXPLORATORY: 0, UNKNOWN: 1, CONFIRMATORY: 2}


def text_sha(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


def ledger_result_ids(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT object_id FROM object_heads WHERE object_type='Result' ORDER BY object_id")]


def build_formation(*, conn: sqlite3.Connection, at: str, state_revision: int, claim_text: str, claim_tier: str,
                    op: dict, previous: Optional[dict], revision: int) -> dict:
    declared_basis = op.get("formation_basis", "unknown")
    declared_obs = op.get("observed_results_at_formation")  # None == not declared == UNKNOWN
    ledger_results = ledger_result_ids(conn)
    tsha = text_sha(claim_text)

    if previous is None:
        first = {"first_recorded_at": at, "first_recorded_state_revision": state_revision,
                 "first_recorded_text_sha256": tsha, "ledger_results_at_first_recording": ledger_results}
        history: list = []
        prior_status = None
    else:
        first = {
            "first_recorded_at": previous.get("first_recorded_at", at),
            "first_recorded_state_revision": previous.get("first_recorded_state_revision", state_revision),
            "first_recorded_text_sha256": previous.get("first_recorded_text_sha256") or tsha,
            "ledger_results_at_first_recording": list(previous.get("ledger_results_at_first_recording") or []),
        }
        history = list(previous.get("history", []))
        prior_status = previous.get("inference_status", UNKNOWN)

    if declared_obs is None:
        exposure = "UNKNOWN"
    elif declared_obs or first["ledger_results_at_first_recording"]:
        exposure = "EXPOSED"
    else:
        exposure = "NONE_DECLARED_AND_NONE_IN_LEDGER"
    undeclared = sorted(set(first["ledger_results_at_first_recording"]) - set(declared_obs or []))
    undeclared = undeclared if declared_obs is not None else []

    if declared_basis == "exploratory" or exposure == "EXPOSED":
        status = EXPLORATORY
    elif declared_basis == "confirmatory" and exposure == "NONE_DECLARED_AND_NONE_IN_LEDGER":
        status = CONFIRMATORY
    else:
        status = UNKNOWN

    downgrade = None
    if declared_basis == "confirmatory" and exposure == "EXPOSED":
        downgrade = "DECLARED_CONFIRMATORY_BUT_RESULTS_EXPOSED"
    if previous is not None:
        if status == CONFIRMATORY and prior_status != CONFIRMATORY:
            raise ProposalRejection(
                Decision.REJECTED, "FORMATION_RELABEL_FORBIDDEN",
                "a claim first recorded as exploratory/unknown cannot become confirmatory by revision; "
                "register a new claim derived from it instead", previous_status=prior_status)
        if prior_status == CONFIRMATORY and status == CONFIRMATORY:
            last_hist = previous.get("history", [])[-1] if previous.get("history") else {}
            changed = tsha != last_hist.get("text_sha256", tsha) or claim_tier != last_hist.get("claim_tier", claim_tier)
            if changed and ledger_results:
                status, downgrade = EXPLORATORY, "CONTENT_CHANGED_AFTER_RESULTS_RECORDED"
        # never raise a-priori-ness (e.g. EXPLORATORY -> UNKNOWN because a revision omitted declarations)
        if _APRIORI[status] > _APRIORI.get(prior_status, 1):
            status = prior_status
            downgrade = downgrade or "RETAINED_PRIOR_LOWER_APRIORI_STATUS"

    history.append({"revision": revision, "text_sha256": tsha, "claim_tier": claim_tier,
                    "inference_status": status, "recorded_at": at, "state_revision": state_revision,
                    "declared_formation_basis": declared_basis})
    return {**first, "declared_formation_basis": declared_basis, "declared_observed_results": declared_obs,
            "suggested_status": op.get("suggested_status"), "results_exposure": exposure,
            "undeclared_ledger_results": undeclared, "inference_status": status, "downgrade_reason": downgrade,
            "history": history}
