"""End-to-end CLI demo of WP6.1 (research-design-aware scientific synthesis): three kinds of inquiry on one Research Core, in throw-away stores.
No network and no real model: the reader and the proposer are scripted, and every paper is invented (tests/inquiry_fixtures.py).
Nothing here validates scientific quality on real literature (REAL_CORPUS_VALIDATION_PENDING).

    python scripts/demos/wp6_1_inquiry_demo.py
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research_harness import cli_knowledge  # noqa: E402
from research_harness.cli import main  # noqa: E402
from research_harness.discovery import landscape as L  # noqa: E402
from research_harness.knowledge import corpus  # noqa: E402
from research_harness.state.store import StateStore  # noqa: E402
from research_harness.workflows.adapters import ScriptedModelAdapter  # noqa: E402
from tests import inquiry_fixtures as F  # noqa: E402

SHARED: dict[str, str] = {}
CURRENT: dict[str, object] = {"adapter": None}


def run(*argv: str, brief: bool = False, expect_rc: int = 0) -> str:
    shown = " ".join(f'"{a}"' if " " in a else a for a in argv)
    for real, alias in SHARED.items():
        shown = shown.replace(real, alias)
    print("$ research " + shown)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = main(list(argv))
    text = out.getvalue().rstrip() or err.getvalue().rstrip()
    for real, alias in SHARED.items():
        text = text.replace(real, alias)
    if brief:
        text = "\n".join(line for line in text.splitlines() if "        quote:" not in line)
    print(text)
    if rc != expect_rc:
        print(f"(exit {rc}, expected {expect_rc})")
    elif rc:
        print(f"(exit {rc}: refused, as intended)")
    print()
    return text


def ent(v: dict, source: str, field: str, label: str | None = None) -> dict:
    return next(e for e in v["entries"] if e["source_id"] == source and e["field"] == field and (label is None or e.get("label") == label))


def proposer(proposals: list) -> ScriptedModelAdapter:
    return ScriptedModelAdapter(model_id="scripted-synthesiser", default_response={"proposals": proposals})


def world(tmp: Path, name: str, papers: dict, records: dict) -> dict:
    store, token = StateStore.create(tmp / name / "home", name, owner_id="researcher")
    for key, p in papers.items():
        corpus.import_source(store, token, citation_key=key, title=p["title"], year=p["year"], text=p["text"], **p["meta"])
    tf = tmp / name / "token"
    tf.write_text(token, encoding="utf-8")
    SHARED.update({str(tmp / name / "home"): "<home>", str(tf): "<token-file>"})
    CURRENT["adapter"] = F.reader(records)
    base = ["--project", name, "--home", str(tmp / name / "home")]
    return {"store": store, "token": token, "base": base, "auth": [*base, "--token-file", str(tf)], "tmp": tmp / name}


def part_a(tmp: Path) -> None:
    print("=" * 110 + "\n# PILOT A: critical synthesis (AI-assisted communication at work). Theory papers with their own section headings, no Method or Results section.\n" + "=" * 110 + "\n")
    w = world(tmp, "PRJ_A", F.A_PAPERS, F.A_RECORDS)
    a, base = w["auth"], w["base"]
    run("problem", "new", "--statement", F.A_STATEMENT, "--kind", "puzzle", "--purpose", "critical_synthesis", "--purpose", "theory_development", "--design", "narrative_review",
        "--evidence-form", "conceptual_argument", "--evidence-form", "textual_evidence", "--domain", "management", "--subfield", "organizational_communication", *a)
    run("problem", "show", "PROB_000001", *base)
    print("# The execution contract of this inquiry is not the regression workflow: no data stage, and the steps are the logic of a critical synthesis\n")
    run("domain", "next", "--spec", "LIB_DW_CRITICAL_SYNTHESIS", "--problem", "PROB_000001", *base)
    run("domain", "next", "--spec", "LIB_DW_ACC_FRAUD_DETECTION", "--problem", "PROB_000001", *base)
    run("retrieval", "new", "PROB_000001", "--term", F.A_TERMS[0], *a)
    run("retrieval", "search", "RET_000001", *a)
    run("landscape", "build", "RET_000001", "--versions", "all", *a)
    v = L.view(w["store"], "LAND_000001")
    print("# With a landscape recorded, the first analytic step of the contract can run\n")
    run("domain", "next", "--spec", "LIB_DW_CRITICAL_SYNTHESIS", "--problem", "PROB_000001", *base)
    run("landscape", "show", "LAND_000001", *base)
    print("# Relations are proposals. The gate refuses what the evidence does not support, and says why\n")
    att, eng = ent(v, "SRC_ATT", "theoretical_claim")["id"], ent(v, "SRC_ENG1", "theoretical_claim")["id"]
    a_as, e_as = ent(v, "SRC_ATT", "stated_assumption")["id"], ent(v, "SRC_ENG1", "stated_assumption")["id"]
    part = ent(v, "SRC_PART", "theoretical_claim")["id"]
    pdef, aidef = ent(v, "SRC_PART", "construct_definition")["id"], ent(v, "SRC_AI", "construct_definition", "communicative participation")["id"]
    e1_as, e2_as = e_as, ent(v, "SRC_ENG2", "stated_assumption")["id"]
    common = {"shared": "Both concern communication in teams.", "rationale": "see differences", "missing_information": ["whether the effects interact"], "search_terms": ["participation", "engagement"], "already_addressed_by": []}
    proposals = [
        {**common, "kind": "theoretical_incompatibility", "topic": "participation and engagement", "entries": [att, eng], "levels": {att: "group", eng: "dyad"},
         "differences": "One explains attention per message in a group; the other explains investment between two colleagues."},
        {**common, "kind": "conceptual_divergence", "topic": "communicative participation", "entries": [pdef, aidef], "levels": {}, "differences": "Different wording.", "rationale": "The words differ."},
        {**common, "kind": "conceptual_divergence", "topic": "versions of one work", "entries": [e1_as, e2_as], "levels": {}, "differences": "The later version drops one word.", "meaning_difference": "none claimed"},
        {**common, "kind": "complementarity", "topic": "levels of analysis", "entries": [att, eng], "levels": {att: "group", eng: "dyad"},
         "differences": "One theory is about a group channel, the other about a pair.", "rationale": "They explain different levels and complement each other."},
        {**common, "kind": "conceptual_divergence", "topic": "what participation presupposes", "entries": [a_as, e_as], "levels": {},
         "differences": "Attention theory lets participation draw on a shared pool whatever its origin; engagement theory treats communication as effortful and attributable.",
         "rationale": "If messages can be produced without effort, the second assumption no longer follows from participation, while the first still does.",
         "meaning_difference": "participation is attention use for one, an expression of personal investment for the other",
         "already_addressed_by": [{"entry": part, "how": "it separates participation from relational engagement"}], "missing_information": ["whether either theory addresses low-effort messages"]},
        {"kind": "implied_assumption", "topic": "observable effort", "entries": [e_as], "text": "Recipients can tell how much effort a message took.",
         "reasoning": "If effortful communication signals engagement, recipients must be able to perceive effort; the authors do not say so."}]
    CURRENT["adapter"] = proposer(proposals)
    run("landscape", "propose", "LAND_000001", *a)
    v = L.view(w["store"], "LAND_000001")
    iid = next(i["id"] for i in v["inferences"] if i["kind"] == "conceptual_divergence" and i["relationship"] == "what participation presupposes")
    run("landscape", "candidates", "LAND_000001", iid, *base)
    run("landscape", "review", "LAND_000001", iid, "CONFIRMED", "--reason", "I read both papers; neither addresses low-effort messages.", *a, expect_rc=1)
    run("landscape", "review", "LAND_000001", iid, "CONFIRMED", "--reason", "I read both papers; neither addresses low-effort messages.", "--acknowledge-missing", *a)
    print("# A conceptual claim is assessed on conceptual terms: a methods text is not a basis for it; logical analysis with a rival considered is\n")
    crit = tmp / "criteria_a.json"
    SHARED[str(crit)] = "<criteria.json>"
    crit.write_text(json.dumps([{"criterion": "The step from participation to dilution is valid given the stated premises", "basis": "the paper's own derivation", "basis_kind": "methodological_literature", "met": "yes"}]), encoding="utf-8")
    run("landscape", "assess", "LAND_000001", att, "--verdict", "SUPPORTED_WITHIN_SCOPE", "--criteria", str(crit), "--threats", json.dumps([{"threat": "rival: attention is not one pool", "status": "addressed"}]),
        "--rationale", "Cited a methods text for a conceptual claim.", *a)
    crit.write_text(json.dumps([{"criterion": "The step from participation to dilution is valid given the stated premises", "basis": "the paper's own derivation", "basis_kind": "logical_analysis", "met": "yes"}]), encoding="utf-8")
    run("landscape", "assess", "LAND_000001", att, "--verdict", "SUPPORTED_WITHIN_SCOPE", "--criteria", str(crit), "--threats", json.dumps([{"threat": "rival: attention is not one pool", "status": "addressed"}]),
        "--rationale", "The derivation holds within group channels.", *a)
    run("landscape", "link-reproduction", "LAND_000001", att, "--kind", "new_data", "--reference", "x", "--reason", "x", *a, expect_rc=1)
    run("landscape", "show", "LAND_000001", *base, brief=True)
    export = tmp / "export_a"
    SHARED[str(export)] = "<export-a>"
    run("export", "landscape", "LAND_000001", "--out", str(export), *base)
    run("export", "verify", str(export), *base)
    print("----- research_landscape.md: relations and proposals (excerpt) -----")
    lines = (export / "research_landscape.md").read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.startswith("## Inquiry"):
            print("\n".join(lines[i:i + 3]))
    for name in ("## Suite inferences derived by rule", "## Proposed relations", "## Assumptions proposed"):
        i = next(n for n, line in enumerate(lines) if line.startswith(name))
        print("\n".join(lines[i:i + (6 if "Proposed" in name else 8)]))
        print()


def part_b(tmp: Path) -> None:
    print("=" * 110 + "\n# PILOT B: education (agentic AI). A conceptual paper on assessment validity and a qualitative design-research paper.\n" + "=" * 110 + "\n")
    w = world(tmp, "PRJ_B", F.B_PAPERS, F.B_RECORDS)
    a, base = w["auth"], w["base"]
    run("problem", "new", "--statement", F.B_STATEMENT, "--kind", "puzzle", "--purpose", "evaluation", "--purpose", "methodological_improvement", "--design", "conceptual",
        "--evidence-form", "conceptual_argument", "--evidence-form", "qualitative_interpretation", "--domain", "education", "--subfield", "design_based_research", *a)
    print("# Two directions, two execution contracts. Neither has an estimation or identification step\n")
    run("domain", "next", "--spec", "LIB_DW_CONCEPTUAL_ARGUMENT", "--problem", "PROB_000001", *base)
    run("domain", "next", "--spec", "LIB_DW_QUALITATIVE_INQUIRY", "--study-design", "design_based", "--research-purpose", "interpretation", *base)
    run("retrieval", "new", "PROB_000001", "--term", F.B_TERMS[0], *a)
    run("retrieval", "search", "RET_000001", *a)
    run("landscape", "build", "RET_000001", *a)
    v = L.view(w["store"], "LAND_000001")
    run("landscape", "show", "LAND_000001", *base, brief=True)
    claim = ent(v, "SRC_VAL", "theoretical_claim", "artifact completion -> evaluative judgement")["id"]
    theme = ent(v, "SRC_MON", "interpretive_finding", "checking by execution")["id"]
    CURRENT["adapter"] = proposer([{"kind": "complementarity", "topic": "checking behaviour and validity", "entries": [claim, theme],
                                    "shared": "Both concern what traces of checking tell about the learner.", "differences": "One is an argument about inference, the other an interpretation of interview accounts.",
                                    "rationale": "The interview theme illustrates the gap the argument describes; it does not show it generally.",
                                    "missing_information": ["whether execution-checking predicts evaluative judgement"], "levels": {}, "already_addressed_by": [], "search_terms": []}])
    run("landscape", "propose", "LAND_000001", *a)
    print("# An interpretive claim: credibility, alternative readings and reflexivity are considerations, not identification or robustness\n")
    crit = tmp / "criteria_b.json"
    SHARED[str(crit)] = "<criteria.json>"
    crit.write_text(json.dumps([{"criterion": "Excerpts show the participants' own words and context", "basis": "the paper's reported excerpts", "basis_kind": "textual_evidence", "met": "yes"},
                                {"criterion": "Reflexivity about the first author's teaching role", "basis": "the paper's reflexivity statement", "basis_kind": "textual_evidence", "met": "yes"}]), encoding="utf-8")
    run("landscape", "assess", "LAND_000001", theme, "--verdict", "SUPPORTED_WITHIN_SCOPE", "--criteria", str(crit),
        "--threats", json.dumps([{"threat": "alternative reading: checking by habit rather than by design", "status": "addressed"}]), "--rationale", "Transparent, with reflexivity; one institution only.", *a)
    v = L.view(w["store"], "LAND_000001")
    print("# What the suite will not conclude: a completed artifact is not evidence of learning, and no proposal is established\n")
    for e in (ent(v, "SRC_VAL", "theoretical_claim", "artifact completion -> evaluative judgement"), ent(v, "SRC_MON", "interpretive_finding", "checking by execution")):
        ev = e["layers"]["evidence"]
        print(f"  {e['id']} {e['claim_kind']}: {e['text']}\n     reading {ev['reading']['value']} · inference {ev['inference']['value']}" + (f" ({ev['inference']['verdict']}, {ev['inference']['route']} route)" if ev["inference"].get("verdict") else "")
              + f" · reproduction {ev['reproduction']['value']}")
    print("  proposals: " + "; ".join(f"{i['id']} {i['kind']} {i['confirmation']['state']}" for i in v["inferences"] if i["derivation"] != "rule") + "\n")


def part_c(tmp: Path) -> None:
    print("=" * 110 + "\n# PILOT C: archival / predictive modeling (financial-reporting fraud detection). The empirical gates are kept.\n" + "=" * 110 + "\n")
    w = world(tmp, "PRJ_C", F.C_PAPERS, F.C_RECORDS)
    a, base = w["auth"], w["base"]
    run("problem", "new", "--statement", F.C_STATEMENT, "--purpose", "prediction", "--design", "predictive_modeling", "--evidence-form", "empirical_results",
        "--domain", "accounting", "--subfield", "archival_financial_reporting", *a)
    run("domain", "next", "--spec", "LIB_DW_ACC_FRAUD_DETECTION", "--problem", "PROB_000001", *base)
    run("retrieval", "new", "PROB_000001", "--term", F.C_TERMS[0], *a)
    run("retrieval", "search", "RET_000001", *a)
    run("landscape", "build", "RET_000001", *a)
    v = L.view(w["store"], "LAND_000001")
    run("landscape", "show", "LAND_000001", *base, brief=True)
    fid = ent(v, "SRC_OTS", "finding")["id"]
    crit = tmp / "criteria_c.json"
    SHARED[str(crit)] = "<criteria.json>"
    crit.write_text(json.dumps([{"criterion": "Labels are dated at release, not at the fiscal year", "basis": "the paper's own description", "methodological_literature": False, "met": "yes"}]), encoding="utf-8")
    print("# An empirical finding keeps the empirical rule: the paper's own description is not methodological literature, and an open threat caps the verdict\n")
    run("landscape", "assess", "LAND_000001", fid, "--verdict", "SUPPORTED_WITHIN_SCOPE", "--criteria", str(crit),
        "--threats", json.dumps([{"threat": "undetected fraud is labelled as non-fraud", "status": "open"}]), "--rationale", "Looks fine.", *a)


def main_demo() -> None:
    cli_knowledge._adapter = lambda args: CURRENT["adapter"]
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        part_a(t)
        part_b(t)
        part_c(t)


if __name__ == "__main__":
    main_demo()
