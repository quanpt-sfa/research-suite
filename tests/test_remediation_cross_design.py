"""WP6.1 remediation, phase C: one Research Core, three independent inquiries, and no design's rules applied to another. Synthetic acceptance only."""
from __future__ import annotations

import pytest

from research_harness.discovery import landscape as L
from research_harness.workflows.domain_engine import facts_from_store, list_builtin_specs, load_builtin_spec, stage_status, suggest_contracts
from research_harness.workflows.status import Applicability
from tests import inquiry_fixtures as F

WORLDS = {
    "critical_review": dict(papers=F.A_PAPERS, statement=F.A_STATEMENT, terms=F.A_TERMS, inquiry=F.A_INQUIRY, records=F.A_RECORDS, contract="LIB_DW_CRITICAL_SYNTHESIS",
                            kinds={"conceptual", "empirical"}, versions="all"),
    "education": dict(papers=F.B_PAPERS, statement=F.B_STATEMENT, terms=F.B_TERMS, inquiry=F.B_INQUIRY, records=F.B_RECORDS, contract="LIB_DW_CONCEPTUAL_ARGUMENT",
                      kinds={"conceptual", "interpretive"}, versions="retrieved"),
    "archival_fraud": dict(papers=F.C_PAPERS, statement=F.C_STATEMENT, terms=F.C_TERMS, inquiry=F.C_INQUIRY, records=F.C_RECORDS, contract="LIB_DW_ACC_FRAUD_DETECTION",
                           kinds={"empirical"}, versions="retrieved"),
}
ACCEPTED_BASIS = {"empirical": "methodological_literature", "interpretive": "textual_evidence", "conceptual": "logical_analysis"}
ARG = {"premises": ["p"], "steps": [{"from": ["P1"], "infers": "q"}], "conclusion": "q", "rival_assumptions": ["r"]}


@pytest.fixture(params=sorted(WORLDS))
def world(request, tmp_path):
    cfg = WORLDS[request.param]
    w = F.build_world(tmp_path, name="PRJ_X", papers=cfg["papers"], statement=cfg["statement"], terms=cfg["terms"], inquiry=cfg["inquiry"])
    w["lid"] = L.build(w["store"], w["token"], w["rid"], F.reader(cfg["records"]), versions=cfg["versions"])["landscape_id"]
    w["cfg"] = cfg
    return w


def test_each_inquiry_gets_its_own_contract_its_own_kinds_of_claim_and_its_own_standard(world):
    cfg, store = world["cfg"], world["store"]
    facts = facts_from_store(store, problem_id=world["pid"])
    assert suggest_contracts(facts)["candidates"] == [cfg["contract"]]
    v = L.view(store, world["lid"])
    assert {e["claim_kind"] for e in v["entries"] if e["claim_kind"]} == cfg["kinds"]
    with store.read() as conn:
        types = {r[0] for r in conn.execute("SELECT object_type FROM object_heads").fetchall()}
    assert "Dataset" not in types and "Analysis" not in types                                                       # none of the three needed a dataset to be supported
    # every claim kind is judged on its own basis kind and by no other
    for kind in cfg["kinds"]:
        eid = next(e["id"] for e in v["entries"] if e["claim_kind"] == kind)
        right = ACCEPTED_BASIS[kind]
        crit = {"criterion": "c", "basis": "b", "basis_kind": right, "met": "yes", **({"argument": ARG} if right == "logical_analysis" else {})}
        out = L.assess_method(store, world["token"], world["lid"], eid, verdict="SUPPORTED_WITHIN_SCOPE", criteria=[crit], threats=[{"threat": "t", "status": "addressed"}], rationale="r")
        assert out["verdict"] == "SUPPORTED_WITHIN_SCOPE" and out["route"] == kind, (kind, out)
        for other, basis in ACCEPTED_BASIS.items():
            if other == kind or (kind == "interpretive" and basis == "methodological_literature"):
                continue                                                                                            # an interpretive claim also accepts methodological literature
            wrong = L.assess_method(store, world["token"], world["lid"], eid, verdict="SUPPORTED_WITHIN_SCOPE", threats=[{"threat": "t", "status": "addressed"}], rationale="r",
                                    criteria=[{"criterion": "c", "basis": "b", "basis_kind": basis, "met": "yes", **({"argument": ARG} if basis == "logical_analysis" else {})}])
            assert wrong["verdict"] == "LIMITED", (kind, other)                                                      # another design's standard does not support this claim


def test_no_inquiry_is_governed_by_a_contract_made_for_another(world):
    cfg = world["cfg"]
    facts = facts_from_store(world["store"], problem_id=world["pid"])
    own = load_builtin_spec(cfg["contract"])
    assert {stage_status(own, facts, st).applicability for st in own.stages} == {Applicability.APPLICABLE}
    for sid in list_builtin_specs():
        if sid in (cfg["contract"], "LIB_DW_GENERIC"):
            continue
        other = load_builtin_spec(sid)
        fits = [stage_status(other, facts, st).applicability is Applicability.APPLICABLE for st in other.stages]
        assert not all(fits), f"{sid} applies to the {cfg['contract']} inquiry"                                      # at least one stage says it does not cover this inquiry
    assert all(isinstance(e["claim_kind"], (str, type(None))) for e in L.view(world["store"], world["lid"])["entries"])
