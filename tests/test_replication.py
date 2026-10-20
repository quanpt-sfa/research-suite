"""Phase 5b (plan v3): replication packages, entitlement, reproduction recorded as computational reproducibility only."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from research_harness.knowledge import corpus, lifecycle
from research_harness.knowledge.common import KnowledgeError
from research_harness.knowledge.replication import add_replication_package, entitlement_of, record_reproduction, reproduce_package
from research_harness.state.store import StateStore
from tests.library_fixture import PANEL_CSV
from tests.test_playbooks import CTX, assess, confirm_all_elements, world  # noqa: F401  (the playbook fixture is reused)

SAMPLE = Path(__file__).resolve().parents[1] / "sample" / "pu_finance"
PAPER = Path(__file__).resolve().parents[1] / "sample" / "zavitsanos2025positive-unlabeled.pdf"
SHIPPED = SAMPLE / "results" / "PUHRF_baseline_meta_noCIK.csv"
REPRODUCED = SAMPLE / "reproduced" / "PUHRF_reproduced.csv"


@pytest.fixture
def pkgdir(tmp_path):
    d = tmp_path / "pkg"
    (d / "data").mkdir(parents=True)
    (d / "data" / "panel.csv").write_bytes(PANEL_CSV.encode("utf-8"))
    return d


OUT = [{"id": "n_rows", "file": "data/panel.csv", "stat": "count", "expected": 8, "tolerance": 0},
       {"id": "pos", "file": "data/panel.csv", "column": "label", "stat": "positive_rate", "expected": 0.25, "tolerance": 1e-9}]


def test_a_package_is_stored_hashed_and_reproduces(tmp_path, pkgdir):
    store, token = StateStore.create(tmp_path / "home", "PRJ_REPL", owner_id="researcher")
    res = add_replication_package(store, token, package_id="PKG_A", label="Panel package", directory=pkgdir, files=["data/panel.csv"], expected_outputs=OUT)
    assert res["files"] == 1 and res["bytes"] == len(PANEL_CSV.encode())
    body = store.get_object("PKG_A")["content"]["body"]
    assert body["files"][0]["name"] == "data/panel.csv" and store.artifacts.verify(body["files"][0]["sha256"])
    out = reproduce_package(store, "PKG_A")
    assert out["computational_reproducibility"] == "REPRODUCED_WITHIN_TOLERANCE" and [o["status"] for o in out["outputs"]] == ["REPRODUCED"] * 2


def test_entitlement_is_unspecified_until_a_basis_is_stated(tmp_path, pkgdir):
    store, token = StateStore.create(tmp_path / "home", "PRJ_ENT", owner_id="researcher")
    add_replication_package(store, token, package_id="PKG_U", label="no license recorded", directory=pkgdir, files=["data/panel.csv"], expected_outputs=OUT)
    assert entitlement_of(store.get_object("PKG_U")["content"]) == {"status": "unspecified", "license": None, "basis": None, "checked_at": store.get_object("PKG_U")["content"]["body"]["entitlement"]["checked_at"], "findings": [], "established": False}
    # "confirmed" without a basis is not confirmed
    add_replication_package(store, token, package_id="PKG_V", label="claimed", directory=pkgdir, files=["data/panel.csv"], entitlement={"status": "confirmed"})
    assert entitlement_of(store.get_object("PKG_V")["content"])["established"] is False
    add_replication_package(store, token, package_id="PKG_W", label="licensed", directory=pkgdir, files=["data/panel.csv"],
                            entitlement={"status": "confirmed", "license": "stated by the researcher", "basis": "the researcher holds a license for the data and code"})
    assert entitlement_of(store.get_object("PKG_W")["content"])["established"] is True
    # promotion into the library needs the entitlement as well as the other conditions
    blockers = lifecycle.promotion_blockers(store, "PKG_U", scope={"study_design": "predictive_modeling"}, exclusions=None, none_known=True)
    assert any("entitlement" in b for b in blockers)
    assert not any("entitlement" in b for b in lifecycle.promotion_blockers(store, "PKG_W", scope={"study_design": "predictive_modeling"}, exclusions=None, none_known=True))
    with pytest.raises(KnowledgeError, match="ENTITLEMENT_STATUS_INVALID"):
        add_replication_package(store, token, package_id="PKG_X", label="x", directory=pkgdir, files=["data/panel.csv"], entitlement={"status": "probably"})


def test_intake_refuses_bad_inputs(tmp_path, pkgdir):
    store, token = StateStore.create(tmp_path / "home", "PRJ_BAD", owner_id="researcher")
    (tmp_path / "outside.csv").write_text("a\n1\n", encoding="utf-8")
    with pytest.raises(KnowledgeError, match="PACKAGE_FILE_INVALID"):
        add_replication_package(store, token, package_id="PKG_1", label="x", directory=pkgdir, files=["../outside.csv"])
    with pytest.raises(KnowledgeError, match="PACKAGE_FILE_INVALID"):
        add_replication_package(store, token, package_id="PKG_1", label="x", directory=pkgdir, files=["data/missing.csv"])
    with pytest.raises(KnowledgeError, match="EXPECTED_OUTPUT_INVALID"):
        add_replication_package(store, token, package_id="PKG_1", label="x", directory=pkgdir, files=["data/panel.csv"],
                                expected_outputs=[{"id": "o", "file": "data/other.csv", "stat": "count", "expected": 1}])
    with pytest.raises(KnowledgeError, match="EXPECTED_OUTPUT_INVALID"):
        add_replication_package(store, token, package_id="PKG_1", label="x", directory=pkgdir, files=["data/panel.csv"],
                                expected_outputs=[{"id": "o", "file": "data/panel.csv", "stat": "count", "expected": 1, "reported_in": "EP_NONE"}])
    with pytest.raises(KnowledgeError, match="PACKAGE_TOO_LARGE"):
        add_replication_package(store, token, package_id="PKG_1", label="x", directory=pkgdir, files=["data/panel.csv"], max_total_bytes=10)


def test_reproduction_is_recorded_on_the_package_and_its_playbook_elements_and_never_touches_validity(world):  # noqa: F811
    store, token = world["store"], world["token"]
    confirm_all_elements(world)
    assess(world)
    before = store.get_object("PB_PU")["content"]["validation"]["validity_assessments"]
    rec = record_reproduction(store, token, "PKG_PANEL")
    assert rec["computational_reproducibility"] == "REPRODUCED_WITHIN_TOLERANCE" and rec["recorded_in"] == ["PB_PU"]
    pb = store.get_object("PB_PU")["content"]
    linked = {e["category"]: e.get("computational_reproducibility") for e in pb["elements"]}
    assert linked["estimand"]["status"] == "REPRODUCED_WITHIN_TOLERANCE" and linked["estimand"]["outputs"] == ["positive_rate"]
    assert linked["sample_construction"]["status"] == "REPRODUCED_WITHIN_TOLERANCE" and linked["validation_scheme"] is None
    assert store.get_object("PKG_PANEL")["content"]["reproduction"]["status"] == "REPRODUCED_WITHIN_TOLERANCE"
    assert pb["validation"]["validity_assessments"] == before                    # reproducing says nothing about whether the method is valid here
    # a package that no longer reproduces is recorded as such, and the elements say so
    with store.write_tx(token=token) as tx:
        cur = tx.get("PKG_PANEL")
        body = {**cur["content"]["body"], "expected_outputs": [{**cur["content"]["body"]["expected_outputs"][1], "expected": 0.9}, cur["content"]["body"]["expected_outputs"][0]]}
        rows = tx.conn.execute("SELECT relation,target_id,target_revision,provenance FROM edges WHERE source_id='PKG_PANEL'").fetchall()
        tx.add_revision(object_id="PKG_PANEL", object_type="KnowledgeItem", content={**cur["content"], "body": body}, edges=[], origin={})
    bad = record_reproduction(store, token, "PKG_PANEL")
    assert bad["computational_reproducibility"] == "FAILED_TO_REPRODUCE"
    assert {e["category"]: e.get("computational_reproducibility", {}).get("status") for e in store.get_object("PB_PU")["content"]["elements"]}["estimand"] == "FAILED_TO_REPRODUCE"


# --------------------------------------------------------------------------------------------------
# the pilot package (needs sample/, which is never committed)
# --------------------------------------------------------------------------------------------------

REAL = pytest.mark.skipif(not (SHIPPED.exists() and PAPER.exists()), reason="the pilot paper and replication package are not in this checkout (sample/ is not committed)")


@REAL
def test_pilot_shipped_results_reproduce_the_numbers_reported_in_the_paper(tmp_path):
    pytest.importorskip("pypdf")
    store, token = StateStore.create(tmp_path / "home", "PRJ_PILOT", owner_id="researcher")
    ref = corpus.import_source_file(store, token, PAPER, citation_key="zavitsanos2025", year=2025, license_note="CC BY 4.0 (ICAIF 2025)")
    row = corpus.add_passage(store, token, source_id=ref["object_id"], quote="HRF PU 0.1342 0.0920 0.1822 0.1281", passage_id="EP_TABLE1_HRF")
    assert store.get_object(row["object_id"])["content"]["section"]                       # the table row sits in a detected section
    files = ["results/PUHRF_baseline_meta_noCIK.csv"] + (["reproduced/PUHRF_reproduced.csv"] if REPRODUCED.exists() else [])
    outputs = []
    for f in files:
        tag = "shipped" if f.startswith("results") else "reproduced"
        outputs += [
            {"id": f"ap_{tag}", "file": f, "column": "average_precision", "stat": "mean", "expected": 0.1281, "tolerance": 5e-5, "reported_in": "EP_TABLE1_HRF"},
            {"id": f"rprec_{tag}", "file": f, "column": "rec-precision", "stat": "mean", "expected": 0.1822, "tolerance": 1e-4, "reported_in": "EP_TABLE1_HRF"}]
    add_replication_package(store, token, package_id="PKG_PUHRF", label="pu_finance PU-HRF results", directory=SAMPLE, files=files, expected_outputs=outputs,
                            source_id=ref["object_id"],
                            entitlement={"status": "confirmed", "license": "stated by the researcher (the repository declares none on GitHub)",
                                         "basis": "the researcher states that a license exists; its text was not inspected by the assistant"})
    out = reproduce_package(store, "PKG_PUHRF")
    assert out["computational_reproducibility"] == "REPRODUCED_WITHIN_TOLERANCE", out["outputs"]
    assert all(o["status"] == "REPRODUCED" for o in out["outputs"])
    assert entitlement_of(store.get_object("PKG_PUHRF")["content"])["established"] is True


@pytest.mark.skipif(not REPRODUCED.exists(), reason="the model has not been rerun here (scripts/replication/pu_finance_wsl.sh writes sample/pu_finance/reproduced)")
def test_pilot_model_rerun_matches_the_shipped_results_split_by_split():
    def rows(p):
        return {r["split"]: r for r in csv.DictReader(open(p, newline="", encoding="utf-8"))}
    shipped, rerun = rows(SHIPPED), rows(REPRODUCED)
    assert sorted(shipped) == sorted(rerun) and len(shipped) == 12
    for split, a in shipped.items():
        for col in ("num_train", "num_pos_train", "num_test", "num_pos_test", "average_precision", "ndcg-precision", "rec-precision", "f1"):
            assert float(a[col]) == float(rerun[split][col]), (split, col)               # identical, not merely close
    env = (REPRODUCED.parent / "environment.txt").read_text(encoding="utf-8")
    assert "Python 3.8" in env and "scikit-learn==1.0.2" in env and "numpy==1.24.4" in env
