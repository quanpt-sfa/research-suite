"""Tests for Claim Formation Provenance (HARKing awareness and Non-monotonicity)."""
from __future__ import annotations

import json
import pytest

from research_harness.core.auth import authenticate
from research_harness.core.model import Decision
from research_harness.proposals.gateway import import_proposal
from research_harness.protocols.snapshot import export_snapshot
from tests.conftest import make_proposal


def test_confirmatory_claim_declared_prior_to_results(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap0 = export_snapshot(store, actor, scope="full", token=owner_token)

    # Propose confirmatory claim when no results exist in ledger
    ops = [
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_CONF_01",
            "claim_tier": "causal",
            "text": "Pre-specified hypothesis: Test-driven refactoring reduces defect density by 40%.",
            "formation_basis": "confirmatory",
            "observed_results_at_formation": [],
            "depends_on": [],
            "relations": [],
        }
    ]
    prop = make_proposal(store.project_id, snap0, "PROP_CONF_01", ops)
    rec = import_proposal(store, json.dumps(prop), bob_token)
    assert rec["decision"] == Decision.ACCEPTED.value

    # Verify stored inference status
    with store.read() as conn:
        claim_obj = store.get_object("CLAIM_CONF_01", conn=conn)
        formation = claim_obj["origin"]["formation"]
        assert formation["inference_status"] == "CONFIRMATORY_DECLARED"
        assert formation["results_exposure"] == "NONE_DECLARED_AND_NONE_IN_LEDGER"


def test_exploratory_claim_declared_as_exploratory(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap0 = export_snapshot(store, actor, scope="full", token=owner_token)

    ops = [
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_EXP_01",
            "claim_tier": "associational",
            "text": "Exploratory claim: Agent token count correlates with modularity score.",
            "formation_basis": "exploratory",
            "depends_on": [],
            "relations": [],
        }
    ]
    prop = make_proposal(store.project_id, snap0, "PROP_EXP_01", ops)
    rec = import_proposal(store, json.dumps(prop), bob_token)
    assert rec["decision"] == Decision.ACCEPTED.value

    with store.read() as conn:
        claim_obj = store.get_object("CLAIM_EXP_01", conn=conn)
        formation = claim_obj["origin"]["formation"]
        assert formation["inference_status"] == "EXPLORATORY"


def test_claim_downgraded_to_exploratory_when_results_exist_in_ledger(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap0 = export_snapshot(store, actor, scope="full", token=owner_token)

    # First, record a Result in the ledger
    ops_res = [
        {
            "operation": "propose_object",
            "proposed_id": "RES_HARNESS_01",
            "object_type": "Result",
            "content": {"label": "Benchmark Run 1", "summary": "Observed 30% speedup"},
            "relations": [],
        }
    ]
    prop_res = make_proposal(store.project_id, snap0, "PROP_RES_REC", ops_res)
    import_proposal(store, json.dumps(prop_res), bob_token)

    snap1 = export_snapshot(store, actor, scope="full", token=owner_token)

    # Proposer falsely declares 'confirmatory' after Result exists in ledger
    ops_claim = [
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_HARK_01",
            "claim_tier": "causal",
            "text": "HARKed claim: Architecture causes speedup.",
            "formation_basis": "confirmatory",
            "observed_results_at_formation": [],
            "depends_on": [],
            "relations": [],
        }
    ]
    prop_claim = make_proposal(store.project_id, snap1, "PROP_HARK_01", ops_claim)
    rec = import_proposal(store, json.dumps(prop_claim), bob_token)
    assert rec["decision"] == Decision.ACCEPTED.value

    # System detects ledger results existed prior to claim recording -> classified as EXPLORATORY
    with store.read() as conn:
        claim_obj = store.get_object("CLAIM_HARK_01", conn=conn)
        formation = claim_obj["origin"]["formation"]
        assert formation["inference_status"] == "EXPLORATORY"
        assert formation["downgrade_reason"] == "DECLARED_CONFIRMATORY_BUT_RESULTS_EXPOSED"


def test_relabel_forbidden_exploratory_cannot_elevate_to_confirmatory(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap0 = export_snapshot(store, actor, scope="full", token=owner_token)

    # Create exploratory claim initially
    ops1 = [
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_STABLE_EXP",
            "claim_tier": "associational",
            "text": "Exploratory finding A.",
            "formation_basis": "exploratory",
            "depends_on": [],
            "relations": [],
        }
    ]
    prop1 = make_proposal(store.project_id, snap0, "PROP_ELEV_01", ops1)
    import_proposal(store, json.dumps(prop1), bob_token)

    snap1 = export_snapshot(store, actor, scope="full", token=owner_token)

    # Propose revision attempting to relabel as confirmatory
    ops2 = [
        {
            "operation": "revise_object",
            "object_id": "CLAIM_STABLE_EXP",
            "base_revision": 1,
            "formation_basis": "confirmatory",
            "observed_results_at_formation": [],
            "content": {
                "text": "Revised claim attempting confirmatory status.",
                "claim_tier": "associational",
            },
            "relations": [],
        }
    ]
    prop2 = make_proposal(store.project_id, snap1, "PROP_ELEV_02", ops2, read_set={"CLAIM_STABLE_EXP": 1})
    rec2 = import_proposal(store, json.dumps(prop2), bob_token)

    # Engine must reject with FORMATION_RELABEL_FORBIDDEN
    assert rec2["decision"] == Decision.REJECTED.value
    assert rec2["rule"] == "FORMATION_RELABEL_FORBIDDEN"


def test_unknown_formation_basis_on_unspecified_declaration(harness_env):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]
    bob_token = harness_env["actors"]["bob"]["token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)
    snap0 = export_snapshot(store, actor, scope="full", token=owner_token)

    # Propose claim without specifying formation_basis
    ops = [
        {
            "operation": "propose_claim",
            "proposed_id": "CLAIM_UNKNOWN_01",
            "claim_tier": "conceptual",
            "text": "Unspecified provenance claim.",
            "depends_on": [],
            "relations": [],
        }
    ]
    prop = make_proposal(store.project_id, snap0, "PROP_UNK_01", ops)
    rec = import_proposal(store, json.dumps(prop), bob_token)
    assert rec["decision"] == Decision.ACCEPTED.value

    with store.read() as conn:
        claim_obj = store.get_object("CLAIM_UNKNOWN_01", conn=conn)
        formation = claim_obj["origin"]["formation"]
        assert formation["inference_status"] == "UNKNOWN"
