"""Phase 7 (plan v3): portable bundles and release notes."""
from __future__ import annotations

import json
import shutil

import pytest

from research_harness.knowledge import closure, lifecycle, publish
from research_harness.knowledge.common import KnowledgeError
from research_harness.knowledge.library import create_library, pin, resolve_pin
from research_harness.state.store import StateStore, compute_state_digest
from research_harness.workflows import eca
from tests.library_fixture import build_library

CTX = {"subfield": "archival_financial_reporting", "study_design": "predictive_modeling", "estimand": "restatement"}


@pytest.fixture
def world(tmp_path):
    w = build_library(tmp_path / "home")
    out = tmp_path / "bundle"
    exported = publish.export_bundle(w["lib"], w["item_pin"], out)
    return {**w, "bundle": out, "exported": exported, "tmp": tmp_path}


def digest(store):
    with store.read() as conn:
        return compute_state_digest(conn)


def test_a_bundle_carries_the_whole_closure_into_another_library(world):
    other, otoken = create_library(world["tmp"] / "elsewhere")
    res = publish.import_bundle(other, otoken, world["bundle"])
    assert res["status"] == "IMPORTED" and res["closure_digest"] == world["exported"]["closure_digest"] and res["objects_imported"] == 6
    assert resolve_pin(other, world["item_pin"])["object_id"] == "LIB_KI_PANEL"                   # the same pin resolves in the new library
    assert closure.dependency_closure(other, world["item_pin"]).closure_digest == world["exported"]["closure_digest"]
    for oid in ("LIB_KI_PANEL", "LIB_EP_BALANCE", "LIB_ADJ_000001", "LIB_SRC_SYNTH2025"):
        a, b = world["lib"].get_object(oid, 1), other.get_object(oid, 1)
        assert (a["content_digest"], a["revision_digest"]) == (b["content_digest"], b["revision_digest"])
    assert other.get_object("LIB_KI_PANEL")["origin"]["kind"] == "LIBRARY_IMPORTED"
    assert lifecycle.source_fidelity(other, "LIB_KI_PANEL")["source_fidelity"] == lifecycle.source_fidelity(world["lib"], "LIB_KI_PANEL")["source_fidelity"]
    again = publish.import_bundle(other, otoken, world["bundle"])
    assert again["objects_imported"] == 0 and again["objects_already_present"] == 6                 # idempotent
    # a project can take a bundle too, and then verify it without the library
    project, ptoken = StateStore.create(world["tmp"] / "elsewhere", "PRJ_BUNDLE", owner_id="researcher")
    assert publish.import_bundle(project, ptoken, world["bundle"])["objects_imported"] == 6
    assert project.get_object("LIB_DW_ACC_FRAUD_DETECTION")["object_type"] == "DomainWorkflow"


def test_an_altered_bundle_imports_nothing(world):
    other, otoken = create_library(world["tmp"] / "elsewhere")
    before = digest(other)
    blob = next((world["bundle"] / "blobs").iterdir())
    blob.write_bytes(b"altered in transit")
    with pytest.raises(KnowledgeError, match="BUNDLE_VERIFICATION_FAILED") as e:
        publish.import_bundle(other, otoken, world["bundle"])
    assert any("does not match its hash" in p for p in e.value.details["problems"]) and digest(other) == before
    blob.write_bytes(world["lib"].artifacts.get_bytes(blob.name))                                       # restore, then alter an object instead
    doc = json.loads((world["bundle"] / "bundle.json").read_text(encoding="utf-8"))
    doc["objects"][0]["content"]["label"] = "tampered"
    (world["bundle"] / "bundle.json").write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(KnowledgeError, match="BUNDLE_VERIFICATION_FAILED") as e:
        publish.import_bundle(other, otoken, world["bundle"])
    assert any("content does not match its digest" in p for p in e.value.details["problems"]) and digest(other) == before
    doc["objects"].pop()
    (world["bundle"] / "bundle.json").write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(KnowledgeError, match="not the objects its manifest lists|BUNDLE_VERIFICATION_FAILED"):
        publish.import_bundle(other, otoken, world["bundle"])
    shutil.rmtree(world["bundle"] / "blobs")
    with pytest.raises(KnowledgeError, match="BUNDLE_VERIFICATION_FAILED"):
        publish.import_bundle(other, otoken, world["bundle"])
    assert digest(other) == before
    with pytest.raises(KnowledgeError, match="BUNDLE_UNREADABLE"):
        publish.import_bundle(other, otoken, world["tmp"] / "no_such_bundle")


def test_a_conflict_half_way_leaves_the_target_untouched(world):
    other, otoken = create_library(world["tmp"] / "elsewhere")
    with other.write_tx(token=otoken) as tx:
        tx.add_revision(object_id="LIB_KI_PKG", object_type="KnowledgeItem", origin={}, edges=[], content={"label": "different", "kind": "checklist"})
    before = digest(other)
    with pytest.raises(KnowledgeError, match="IMPORT_CONFLICT"):
        publish.import_bundle(other, otoken, world["bundle"])
    assert digest(other) == before


def test_a_bundle_goes_to_a_new_directory(world):
    with pytest.raises(KnowledgeError, match="BUNDLE_DIR_NOT_EMPTY"):
        publish.export_bundle(world["lib"], world["item_pin"], world["bundle"])


def test_release_notes_state_each_dimension_and_every_context(world):
    lib, token = world["lib"], world["token"]
    first = publish.release_notes(lib, world["item_pin"])
    d = first["dimensions"]
    assert d["source_fidelity"]["status"] == "QUOTE_VERIFIED" and d["source_fidelity"]["elements"][0]["eca_verdict"] == "SUPPORTED_AS_STATED"
    assert d["computational_reproducibility"]["status"] == "REPRODUCED_WITHIN_TOLERANCE" and d["methodological_validity"] == "not assessed in any context"
    assert first["entitlement"]["LIB_KI_PKG"]["established"] is False                                      # the package says nothing about its license
    eca.confirm(lib, token, world["eca_id"], verdict="SUPPORTED_AS_STATED", rationale="The element carries the condition.")
    ref = lib.get_object("LIB_KI_PANEL")["content"]["elements"][0]["evidence_basis"][0]["ref"]
    other = {**CTX, "estimand": "association with board independence"}
    for ctx, methodological, confirmed in ((CTX, True, True), (other, False, False)):
        lifecycle.validate(lib, token, "LIB_KI_PANEL", assessment={"context": ctx, "confirmed_by_researcher": confirmed,
                                                                   "evidence_basis": [{"kind": "source_passage", "ref": ref, "methodological_literature": methodological}]})
    notes = publish.release_notes(lib, pin(lib, "LIB_KI_PANEL"))
    mv = notes["dimensions"]["methodological_validity"]
    assert notes["dimensions"]["source_fidelity"]["status"] == "MEANING_VERIFIED"
    assert {json.dumps(a["context"]["estimand"]): a["verdict"] for a in mv} == {'"restatement"': "SUPPORTED_WITHIN_SCOPE", '"association with board independence"': "PROVISIONAL"}
    text = publish.render_notes(notes)
    assert "restatement" in text and "SUPPORTED_WITHIN_SCOPE" in text and "PROVISIONAL" in text and "a context that is not listed has not been assessed" in text.lower()


def test_a_domain_workflow_is_not_presented_as_validated(world):
    notes = publish.release_notes(world["lib"], world["dw_pin"])
    assert notes["object_type"] == "DomainWorkflow" and notes["validation_status"] == "EXTRACTED" and "Nothing here is verified" in notes["statement"]
    assert "not assessed" in notes["dimensions"] and "Nothing here is verified" in publish.render_notes(notes)
