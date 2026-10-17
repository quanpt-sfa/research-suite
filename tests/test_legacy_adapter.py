"""Tests for Legacy v8.4.4 Migration Adapter."""
from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import pytest

from research_harness.core.auth import authenticate
from research_harness.core.model import ReviewStatus
from research_harness.legacy.adapter import migrate_legacy_project
from research_harness.state.store import StateStore


@pytest.fixture
def populated_legacy_project(tmp_path: Path) -> Path:
    dest = tmp_path / "legacy_latex_project"
    master = dest / "research_harness" / "00_master"
    master.mkdir(parents=True, exist_ok=True)
    (dest / "main.tex").write_text("% Legacy LaTeX root\n", encoding="utf-8")

    # Populate sample rows into CSV files
    with (master / "research_questions.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rq_id", "rq_text", "rq_label"])
        w.writerow(["RQ_0001", "Does modularity improve research velocity?", "Modularity RQ"])

    with (master / "construct_registry.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["construct_id", "construct_label", "construct_definition"])
        w.writerow(["CONST_0001", "Modularity", "Structural decoupling of components"])

    with (master / "source_registry.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["source_id", "citation_key", "bibliographic_identity", "doi", "library_file_id"])
        w.writerow(["SRC_0001", "Parnas1972", "D. Parnas on Information Hiding", "10.1145/361598.361623", "lib_parnas"])

    with (master / "claim_source_registry.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["claim_source_id", "source_id", "normalized_claim", "locator_type", "evidence_locator"])
        w.writerow(["CS_0001", "SRC_0001", "Decoupling reduces cascading change", "page", "p. 1054"])

    with (master / "proposition_registry.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["proposition_id", "proposition_text", "claim_tier"])
        w.writerow(["PROP_0001", "Modular boundaries prevent regression cascades", "causal"])

    with (master / "proposition_source_binding.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["proposition_id", "claim_source_id", "relation"])
        w.writerow(["PROP_0001", "CS_0001", "supports"])

    with (master / "semantic_adjudication_registry.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["adjudication_id", "proposition_id", "assessment_kind", "verdict", "rationale"])
        w.writerow(["ADJ_0001", "PROP_0001", "semantic_entailment", "SUPPORTED", "Source directly implies proposition"])

    return dest


def test_legacy_migration_dry_run(harness_env, populated_legacy_project):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)

    res = migrate_legacy_project(populated_legacy_project, store, actor, dry_run=True)
    assert res["decision"] == "DRY_RUN"
    assert "detected_registries" in res
    assert res["detected_registries"]["research_questions"] == 1
    assert res["detected_registries"]["propositions"] == 1

    # Ensure target store remained empty
    with store.read() as conn:
        count = conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
        assert count == 0


def test_legacy_migration_full_and_idempotent(harness_env, populated_legacy_project):
    store = harness_env["store"]
    owner_token = harness_env["owner_token"]

    with store.read() as conn:
        actor = authenticate(conn, owner_token)

    # Run full migration
    res1 = migrate_legacy_project(populated_legacy_project, store, actor, dry_run=False)
    assert res1["decision"] == "ACCEPTED"
    assert "backup_zip" in res1
    backup_file = Path(res1["backup_zip"])
    assert backup_file.is_file()

    with store.read() as conn:
        rev_count1 = conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
        assert rev_count1 > 0
        assert "PROP_0001" in res1["created_objects"]

        # Verify fail-closed rule: migrated claims MUST start as UNASSESSED and UNKNOWN
        claim = store.get_object("PROP_0001", conn=conn)
        assert claim is not None
        assert claim["scientific_review_status"] == ReviewStatus.UNASSESSED.value
        assert claim["origin"]["kind"] == "LEGACY_MIGRATED"
        assert claim["origin"]["formation"]["inference_status"] == "UNKNOWN"

    # Run migration a second time -> idempotent (no duplicate objects)
    res2 = migrate_legacy_project(populated_legacy_project, store, actor, dry_run=False)
    assert res2["decision"] == "ALREADY_MIGRATED"

    with store.read() as conn:
        rev_count2 = conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
        assert rev_count2 == rev_count1


def test_legacy_files_remain_intact(populated_legacy_project):
    master = populated_legacy_project / "research_harness" / "00_master"
    assert master.is_dir()
    rq_file = master / "research_questions.csv"
    assert rq_file.is_file()
    assert rq_file.stat().st_size > 0
