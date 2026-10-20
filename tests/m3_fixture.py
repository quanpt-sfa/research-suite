"""Shared synthetic corpus and scripted extractors for the M3 tests (every text is invented; see the fixture files)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from research_harness.knowledge import corpus
from research_harness.workflows.adapters import ScriptedModelAdapter

FIX = Path(__file__).parent / "fixtures"
FOCUS, PAPER_A, PAPER_B = "SRC_FOCUS2026", "SRC_SYNTH2025", "SRC_PAPERB2025"
TEXTS = {FOCUS: ("focus2026", "synthetic_focus_manuscript.txt"), PAPER_A: ("synth2025", "synthetic_paper_misstatement.txt"),
         PAPER_B: ("paperb2025", "synthetic_paper_b.txt")}


def R(field: str, quote: str, value: str, **extra: str) -> dict[str, str]:
    return {"field": field, "quote": quote, "value": value, **extra}


SCRIPT: dict[tuple[str, str], list[dict[str, str]]] = {
    ("RP_INTRO_ARGUMENT", FOCUS): [
        R("phenomenon", "Restatements of annual reports are costly for investors and for the firms involved", "Restatements are costly."),
        R("established_knowledge", "Prior studies show that accrual-based indicators predict restatements within a single industry",
          "Accrual indicators predict restatements within one industry."),
        R("unresolved_tension", "It remains unclear whether a model estimated on some industries still works for firms in industries it has not seen",
          "Transfer across industries is unclear."),
        R("proposed_contribution", "We contribute by evaluating out-of-industry transfer of a restatement model with out-of-time folds",
          "Evaluate out-of-industry transfer with out-of-time folds.", type="extends"),
        R("evidence_needed", "We would need a leave-one-industry-out evaluation and a comparison against a constant predictor to defend this claim",
          "Leave-one-industry-out evaluation against a constant predictor.")],
    ("RP_INTRO_ARGUMENT", PAPER_A): [
        R("phenomenon", "Financial restatements impose large costs on investors", "Restatements are costly."),
        R("established_knowledge", "Prior work has shown that accrual-based indicators predict restatements in samples drawn from a single industry",
          "Accrual indicators predict restatements within one industry."),
        R("unresolved_tension", "it remains unclear whether these indicators remain informative once firms from different industries are pooled in one sample",
          "Unclear whether indicators hold when industries are pooled."),
        R("proposed_contribution", "We contribute by testing the transferability of the model on a pooled panel",
          "Test transferability on a pooled panel.", type="new_setting_only")],
    ("RP_THEORY", PAPER_A): [
        R("construct_definition", "We define restatement risk as the probability that a firm's annual report is later amended for an accounting error",
          "Restatement risk is the probability of a later amendment.", label="restatement risk"),
        R("assumed_mechanism", "Agency theory predicts that managers with weak monitoring have stronger incentives to misreport earnings",
          "Weak monitoring raises misreporting incentives.", label="weak monitoring"),
        R("stated_assumption", "Earlier studies assume that the mechanism linking monitoring quality to misreporting is the same in every industry",
          "The mechanism is the same in every industry.", label="same mechanism in every industry"),
        R("assumption_tested", "We test this assumption directly in the empirical section", "The same-mechanism assumption is tested.",
          label="same mechanism in every industry")],
    ("RP_THEORY", PAPER_B): [
        R("construct_definition", "We define restatement risk as the share of firm-years followed by a restated report within three years",
          "Restatement risk is the share of firm-years with a restatement within three years.", label="restatement risk"),
        R("assumed_mechanism", "Managers facing weak monitoring misreport more often", "Weak monitoring raises misreporting.", label="weak monitoring"),
        R("stated_assumption", "We assume that monitoring quality is stable over the sample window", "Monitoring quality is stable.",
          label="monitoring quality is stable")],
    ("RP_METHOD", PAPER_A): [
        R("sample_selection", "drop firms with fewer than three consecutive annual observations", "Firms with short panels are dropped."),
        R("label_construction", "The outcome label equals one when the firm later filed an amended annual report for an accounting error",
          "Label is one for a later amended report."),
        R("validation_or_tests", "Models are validated by training on the earlier years and testing on the last two years", "Temporal split.")],
    ("RP_METHOD", PAPER_B): [
        R("sample_selection", "keep firms with at least five years of data", "Firms with at least five years are kept."),
        R("label_construction", "The outcome label equals one when a restated report follows within three years", "Label is one for a restatement within three years."),
        R("validation_or_tests", "Models are validated by training on years before the test year only", "Out-of-time validation.")],
    ("RP_CONTRIBUTION", PAPER_A): [
        R("research_question", "This paper asks whether a single restatement model can be applied across industries without re-estimation",
          "Can one model be applied across industries?"),
        R("claimed_contribution", "We contribute by testing the transferability of the model on a pooled panel and by documenting where it fails",
          "Tests transferability and documents failures.")],
    ("RP_CONTRIBUTION", PAPER_B): [
        R("claimed_contribution", "Our contribution is an out-of-time validation design for restatement prediction that never trains on later years than it tests",
          "An out-of-time validation design.")],
}


def extractor(model_id: str = "scripted-extractor") -> ScriptedModelAdapter:
    def handler(system: str, user: str) -> dict[str, Any]:
        proto = re.search(r"\((RP_[A-Z_]+):", system).group(1)
        source = re.search(r"\((?:LIB_)?(SRC_[A-Z0-9_]+) revision", user).group(1)
        return {"records": SCRIPT.get((proto, source), []), "gaps": []}
    return ScriptedModelAdapter(model_id=model_id, custom_handler=handler)


def import_corpus(store: Any, token: str, *, id_prefix: str = "SRC_", sources: tuple = (FOCUS, PAPER_A, PAPER_B)) -> None:
    for sid in sources:
        key, fname = TEXTS[sid]
        corpus.import_source(store, token, citation_key=key, text=(FIX / fname).read_text(encoding="utf-8"), title=key, id_prefix=id_prefix)
