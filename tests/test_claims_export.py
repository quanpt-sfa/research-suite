"""WP6.2: the claim exports. Reproducible from the store, honest about what is a proposal, and bounded in what quotation leaves the store. Synthetic: `SCHEMA_TESTED`."""
from __future__ import annotations

import csv
import io
import json

import pytest

from research_harness.benchmarks import claims as BM
from research_harness.export.claims import build_claims_export
from research_harness.export.contracts import MANIFEST_NAME, QUOTE_MAX
from research_harness.export.manifest import verify_export, write_export
from research_harness.knowledge import claim_relations as CR
from research_harness.knowledge import claims as CL
from research_harness.knowledge import corpus
from tests import claims_fixtures as F


def rows(export, name):
    return list(csv.DictReader(io.StringIO(export.files[name].decode("utf-8"))))


def cid(v, fragment):
    return next(c["id"] for c in v["claims"] if c.get("wording") and fragment in c["wording"]["quote"])


@pytest.fixture()
def world(tmp_path):
    w = F.make_store(tmp_path)
    inv = CL.build(w["store"], w["token"], [w["ids"]["cr"], w["ids"]["pre"], w["ids"]["jour"]], F.reader(), label="export test")["inventory_id"]
    v = CL.view(w["store"], inv)
    a, e = cid(v, "We argue that communicative"), next(x["id"] for x in v["claims"] if x["source_id"] == w["ids"]["pre"] and "investment of the self" in x["wording"]["quote"])
    CR.add_relation(w["store"], w["token"], inv, {"kind": "COMPLEMENTS", "from": a, "to": e, "rationale": "two ways of separating state from behaviour"})
    p = next(x["id"] for x in v["claims"] if x["wording"]["quote"] == F.V["w_pre"])
    j = next(x["id"] for x in v["claims"] if x["wording"]["quote"] == F.V["w_post"])
    CR.add_alignment(w["store"], w["token"], inv, {"change": "WEAKENED", "earlier": p, "later": j, "what_changed": "always became can", "rationale": "a universal became a possibility"})
    CL.add_interpretation(w["store"], w["token"], inv, text="The separation presupposes that effort is the only cue to engagement.", attribution="RESEARCHER_INTERPRETATION", about=[a], rationale="my reading")
    return {**w, "inv": inv, "a": a, "e": e}


def test_the_export_has_every_file_a_manifest_with_the_closure_and_the_same_bytes_every_time(world, tmp_path):
    w = world
    x1, x2 = build_claims_export(w["store"], w["inv"]), build_claims_export(w["store"], w["inv"])
    assert x1.all_files() == x2.all_files() and x1.closure_digest == x2.closure_digest
    assert set(x1.all_files()) == {"claim_inventory.csv", "claim_evidence_links.csv", "claim_argument_relations.csv", "claim_version_history.csv", "claim_coverage_report.md",
                                   "claim_attribution_audit.md", "claim_graph.md", "claim_benchmark_results.json", "token_cost_report.csv", MANIFEST_NAME}
    m = x1.manifest()
    read = {w["ids"][k] for k in ("cr", "pre", "jour")}
    ids = {c[0] for c in x1.closure}
    assert w["inv"] in ids and read <= ids and not ({w["ids"]["edu"], w["ids"]["fraud"]} & ids) and any(c[0] == "run" for c in x1.closure) and any(i.startswith("EP_") for i in ids)
    assert m["kind"] == "claims" and m["scope"] == {"inventory": w["inv"]} and {s["source_id"] for s in m["sources"]} == read


def test_every_row_names_its_source_revision_and_provenance_class_and_a_proposal_is_never_exported_as_established(world):
    w = world
    x = build_claims_export(w["store"], w["inv"])
    for r in rows(x, "claim_inventory.csv"):
        if r["author_stated"] == "True":
            assert r["source_id"] and r["source_revision"] == "1" and r["wording_passage_id"] and r["provenance_class"] in ("model_synthesised", "researcher_confirmed") and r["wording_quote"]
    rel = rows(x, "claim_argument_relations.csv")
    assert len(rel) == 1 and rel[0]["status"] == "PROPOSED" and rel[0]["confirmation"] == "UNCONFIRMED" and rel[0]["provenance_class"] == "researcher_proposed_unconfirmed"
    assert rel[0]["from_source_revision"].endswith("@1") and rel[0]["to_source_revision"].endswith("@1")
    ver = rows(x, "claim_version_history.csv")
    assert len(ver) == 1 and ver[0]["status"] == "PROPOSED" and ver[0]["confirmation"] == "UNCONFIRMED" and ver[0]["change"] == "WEAKENED" and ver[0]["order_basis"] == "first_public_date"
    graph = x.files["claim_graph.md"].decode("utf-8")
    assert "-.->|COMPLEMENTS|" in graph and "-->|COMPLEMENTS|" not in graph                       # dashed: a proposal
    CR.review_relation(w["store"], w["token"], w["inv"], "R001", "CONFIRMED", "I read both; they complement each other")
    x2 = build_claims_export(w["store"], w["inv"])
    assert rows(x2, "claim_argument_relations.csv")[0]["provenance_class"] == "researcher_confirmed" and rows(x2, "claim_argument_relations.csv")[0]["confirmation"] == "CONFIRMED_BY_RESEARCHER"
    assert "-->|COMPLEMENTS|" in x2.files["claim_graph.md"].decode("utf-8")
    assert x2.closure_digest != x.closure_digest


def test_an_interpretation_is_exported_without_a_quotation_and_marked_not_author_stated(world):
    x = build_claims_export(world["store"], world["inv"])
    mine = [r for r in rows(x, "claim_inventory.csv") if r["attribution"] == "RESEARCHER_INTERPRETATION"]
    assert len(mine) == 1 and mine[0]["author_stated"] == "False" and mine[0]["wording_quote"] == "" and mine[0]["wording_passage_id"] == "" and mine[0]["source_id"] == ""
    assert mine[0]["provenance_class"] == "researcher_confirmed"
    audit = x.files["claim_attribution_audit.md"].decode("utf-8")
    assert "Interpretations that are not the authors'" in audit and "The separation presupposes" in audit


def test_a_quotation_leaves_the_store_at_most_the_contract_length_and_never_a_full_text(tmp_path):
    long_sentence = "We argue that " + " ".join(f"word{i}" for i in range(150)) + " matters in invented settings."
    text = F.paper("Long Sentence", "x", [("1 Introduction", [long_sentence])])
    w = F.make_store(tmp_path, {"long": {"title": "Long Sentence", "year": 2025, "meta": {}, "text": text}})
    inv = CL.build(w["store"], w["token"], [w["ids"]["long"]], F.reader({"long": {"RP_CLAIMS": [F.rec("theoretical_proposition", long_sentence, F.OWN)]}}))["inventory_id"]
    x = build_claims_export(w["store"], inv)
    r = rows(x, "claim_inventory.csv")[0]
    assert len(r["wording_quote"]) <= QUOTE_MAX + 20 and "chars)" in r["wording_quote"]
    assert all(len(row["quote"]) <= QUOTE_MAX + 20 for row in rows(x, "claim_evidence_links.csv"))
    assert text not in "".join(b.decode("utf-8") for b in x.files.values())


def test_the_evidence_links_are_verified_again_against_the_stored_text_at_export(world):
    x = build_claims_export(world["store"], world["inv"])
    links = rows(x, "claim_evidence_links.csv")
    assert links and {r["source_match_at_export"] for r in links} == {"VERIFIED"}
    roles = {r["link_role"] for r in links}
    assert {"WORDING", "QUALIFIER"} <= roles


def test_export_verify_tells_intact_files_from_modified_ones_and_from_a_store_that_moved_on(world, tmp_path):
    w = world
    x = build_claims_export(w["store"], w["inv"])
    out = tmp_path / "out"
    info = write_export(w["store"], x, out)
    assert "claim_inventory.csv" in info["files"] and (out / MANIFEST_NAME).is_file()
    rebuild = lambda scope: build_claims_export(w["store"], scope["inventory"])            # noqa: E731
    assert verify_export(w["store"], out, rebuild)["status"] == "VERIFIED"
    (out / "claim_graph.md").write_text("tampered", encoding="utf-8")
    assert verify_export(w["store"], out, rebuild)["status"] == "FILES_MODIFIED"
    out2 = tmp_path / "out2"
    write_export(w["store"], x, out2)
    out = out2
    CR.review_relation(w["store"], w["token"], w["inv"], "R001", "DISMISSED", "on reflection they do not complement each other")
    assert verify_export(w["store"], out, rebuild)["status"] == "STATE_CHANGED"


def test_the_cost_report_says_what_was_spent_what_reuse_saved_and_that_scripted_tokens_are_estimates(tmp_path):
    w = F.make_store(tmp_path)
    ad = F.reader()
    CL.build(w["store"], w["token"], [w["ids"]["cr"]], ad)
    inv = CL.build(w["store"], w["token"], [w["ids"]["cr"]], ad)["inventory_id"]               # the second build is served from the cache
    x = build_claims_export(w["store"], inv)
    r = rows(x, "token_cost_report.csv")
    src, total = r[0], r[-1]
    assert src["source_id"] == w["ids"]["cr"] and src["llm_calls"] == "0" and int(src["reused_runs"]) == 1 and int(src["tokens_saved_by_reuse"]) > 0
    assert "ESTIMATED" in src["token_basis"] and "not billed" in src["token_basis"] and total["source_id"] == "(all)"
    first = CL.view(w["store"], "CINV_000001")
    assert first["coverage"]["input_tokens"] > 0 and total["tokens_per_confirmed_claim"] == ""         # nothing confirmed yet: no figure rather than a made-up one
    cs = rows(build_claims_export(w["store"], "CINV_000001"), "token_cost_report.csv")
    assert int(cs[0]["llm_calls"]) >= 1 and int(cs[0]["input_tokens"]) > 0 and cs[0]["reused_runs"] == "0"


def test_the_benchmark_file_is_not_available_until_a_run_is_recorded_and_then_carries_the_run_with_its_closure_entry(world):
    w = world
    x = build_claims_export(w["store"], w["inv"])
    assert json.loads(x.files["claim_benchmark_results.json"])["status"] == "NOT_AVAILABLE" and x.manifest()["not_available"][0]["file"] == "claim_benchmark_results.json"
    run_id = BM.record_results(w["store"], {"1_claim_detection": {"f1": 0.5}, "gold_kind": "SYNTHETIC_FIXTURE"}, scope=w["inv"], note="metric code only")
    y = build_claims_export(w["store"], w["inv"])
    doc = json.loads(y.files["claim_benchmark_results.json"])
    assert doc["status"] == "RESULTS_RECORDED" and doc["runs"][0]["run_id"] == run_id and doc["runs"][0]["results"]["gold_kind"] == "SYNTHETIC_FIXTURE" and "composite" in doc["note"]
    assert ["run", run_id] == y.closure[-1][:2] or any(c[:2] == ["run", run_id] for c in y.closure)
    assert y.manifest()["not_available"] == []


def test_the_coverage_report_lists_what_was_read_what_was_not_and_never_calls_a_count_complete(world):
    text = build_claims_export(world["store"], world["inv"]).files["claim_coverage_report.md"].decode("utf-8")
    assert "A count of claims is not evidence that a paper was read completely" in text and "References" in text and "What the extraction rejected" in text
    assert "NOT_AVAILABLE" not in text
