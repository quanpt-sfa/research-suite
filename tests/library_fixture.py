"""A small but complete library item for the M2 tests: a source with its full text and sections, a verified passage, an
adjudication of one statement against it, a replication package with data and expected outputs, and a domain workflow."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from research_harness.knowledge import corpus
from research_harness.knowledge.library import add_object, create_library, pin
from research_harness.workflows import eca
from research_harness.workflows.adapters import ScriptedModelAdapter
from research_harness.workflows.domain_engine import load_builtin_spec

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_paper_misstatement.txt"
QUOTE = "the estimator performs well only when every firm is observed in every year of the estimation window"
STATEMENT = "The estimator needs every firm observed in every year of the estimation window."
PANEL_CSV = "firm,year,label,score\n" + "\n".join(
    f"F{i},{2010 + i % 3},{1 if i in (2, 5) else 0},{0.1 * i:.1f}" for i in range(1, 9)) + "\n"   # 8 rows, 2 positives


def supported_as_stated() -> ScriptedModelAdapter:
    return ScriptedModelAdapter(model_id="scripted-adjudicator", default_response={
        "passage_proposition": "The estimator performs well only for panels in which every firm is observed in every year.",
        "equivalence": "EQUIVALENT", "dropped_conditions": [], "verdict": "SUPPORTED_AS_STATED",
        "cue_handling": [{"cue": "only when", "handling": "carried"}], "required_qualification": [], "rationale": "carries the condition"})


def build_library(home: Path, *, with_eca: bool = True) -> dict[str, Any]:
    lib, token = create_library(home)
    corpus.import_source(lib, token, citation_key="synth2025", text=FIXTURE.read_text(encoding="utf-8"), title="A synthetic paper")
    corpus.add_passage(lib, token, source_id="LIB_SRC_SYNTH2025", quote=QUOTE, passage_id="EP_BALANCE")
    passage_pin = pin(lib, "LIB_EP_BALANCE")

    sha = lib.artifacts.put_bytes(PANEL_CSV.encode("utf-8"))
    pkg = add_object(lib, token, "LIB_KI_PKG", "KnowledgeItem", {
        "label": "Panel replication package", "kind": "replication_package", "generated_by": "import", "lifecycle": "ACTIVE",
        "body": {"files": [{"name": "panel.csv", "sha256": sha}],
                 "expected_outputs": [
                     {"id": "n_rows", "file": "panel.csv", "stat": "count", "expected": 8, "tolerance": 0},
                     {"id": "positive_rate", "file": "panel.csv", "column": "label", "stat": "positive_rate", "expected": 0.25, "tolerance": 1e-9}]}},
        edges=[("derived_from", "LIB_SRC_SYNTH2025", 1)])

    spec = load_builtin_spec("LIB_DW_ACC_FRAUD_DETECTION")
    dw = add_object(lib, token, "LIB_DW_ACC_FRAUD_DETECTION", "DomainWorkflow", {"label": spec.id, "spec": spec.to_dict(), "spec_digest": spec.digest()})

    item = add_object(lib, token, "LIB_KI_PANEL", "KnowledgeItem", {
        "label": "Balanced-panel requirement", "kind": "verification_rule", "generated_by": "researcher", "lifecycle": "ACTIVE",
        "elements": [{"id": "E1", "text": STATEMENT, "risk_tier": "HIGH", "generated_by": "researcher",
                      "evidence_basis": [{"kind": "source_passage", "ref": passage_pin}]}],
        "pins": [passage_pin, pkg["pin"], dw["pin"]], "scope": {"study_design": "predictive_modeling"},
        "exclusions": ["unbalanced panels"]},
        edges=[("depends_on", "LIB_EP_BALANCE", 1), ("depends_on", "LIB_KI_PKG", 1), ("depends_on", "LIB_DW_ACC_FRAUD_DETECTION", 1),
               ("derived_from", "LIB_SRC_SYNTH2025", 1)])
    out = {"lib": lib, "token": token, "item_pin": item["pin"], "item_id": "LIB_KI_PANEL", "pkg_pin": pkg["pin"],
           "passage_pin": passage_pin, "dw_pin": dw["pin"], "pkg_sha": sha, "home": home}
    if with_eca:
        res = eca.adjudicate(lib, token, eca.resolve_statement(lib, "LIB_KI_PANEL", element="E1"), "LIB_EP_BALANCE", supported_as_stated())
        out["eca_id"] = res["adjudication_id"]
    return out
