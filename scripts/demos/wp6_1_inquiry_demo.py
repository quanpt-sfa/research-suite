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
from tests import remediation_fixtures as RF  # noqa: E402

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
    RF.add_extra_papers(w)
    RF.add_abstract_only(w)
    a, base = w["auth"], w["base"]
    run("problem", "new", "--statement", F.A_STATEMENT, "--kind", "puzzle", "--purpose", "critical_synthesis", "--purpose", "theory_development", "--design", "systematic_review",
        "--evidence-form", "conceptual_argument", "--evidence-form", "textual_evidence", "--domain", "management", "--subfield", "organizational_communication", *a)
    run("problem", "show", "PROB_000001", *base)
    print("# The suite suggests contracts for the recorded inquiry and chooses none; the regression workflow does not fit\n")
    run("domain", "suggest", "--problem", "PROB_000001", *base)
    print("# The method of the review is a record: a DRAFT (here drafted by an assistant) until the researcher adopts exactly that plan\n")
    proto, log = tmp / "protocol.json", tmp / "screening_log.json"
    SHARED.update({str(proto): "<protocol.json>", str(log): "<screening_log.json>"})
    proto.write_text(json.dumps(RF.PROTOCOL_A), encoding="utf-8")
    log.write_text(json.dumps(RF.screening_log()), encoding="utf-8")
    run("review-protocol", "set", "PROB_000001", "--protocol", str(proto), "--rationale", "a first plan, drafted for the researcher to adopt or change", "--drafted-by", "assistant:demo", *a)
    run("review-protocol", "screen", "PROB_000001", "--entries", str(log), "--rationale", "the researcher's first-pass decisions (invented for this demo)", *a)
    run("review-protocol", "audit", "PROB_000001", *base)
    run("domain", "next", "--spec", "LIB_DW_CRITICAL_SYNTHESIS", "--problem", "PROB_000001", *base)
    run("review-protocol", "adopt", "PROB_000001", "--rationale", "this is the plan I will follow", *a)
    run("review-protocol", "audit", "PROB_000001", *base)
    run("retrieval", "new", "PROB_000001", "--term", F.A_TERMS[0], *a)
    run("retrieval", "search", "RET_000001", *a)
    run("landscape", "build", "RET_000001", "--versions", "all", *a)
    v = L.view(w["store"], "LAND_000001")
    print("# The method steps are declared in the adopted record, reconstruction was executed by a capability; the stage report says so and applies no empirical standard\n")
    run("domain", "next", "--spec", "LIB_DW_CRITICAL_SYNTHESIS", "--problem", "PROB_000001", *base)
    run("landscape", "show", "LAND_000001", *base, brief=True)
    print("# Relations are proposals. The gate refuses what the evidence does not support, and says why\n")
    att, eng = ent(v, "SRC_ATT", "theoretical_claim")["id"], ent(v, "SRC_ENG1", "theoretical_claim")["id"]
    a_as, e_as = ent(v, "SRC_ATT", "stated_assumption")["id"], ent(v, "SRC_ENG1", "stated_assumption")["id"]
    pdef, aidef = ent(v, "SRC_PART", "construct_definition")["id"], ent(v, "SRC_AI", "construct_definition", "communicative participation")["id"]
    e2_as = ent(v, "SRC_ENG2", "stated_assumption")["id"]
    common = {"shared": "Both concern communication in teams.", "rationale": "see differences", "missing_information": ["whether the effects interact"], "search_terms": ["participation", "engagement"], "already_addressed_by": []}
    proposals = [
        {**common, "kind": "theoretical_incompatibility", "topic": "participation and engagement", "entries": [att, eng], "levels": {att: "group", eng: "dyad"},
         "differences": "One explains attention per message in a group; the other explains investment between two colleagues."},
        {**common, "kind": "conceptual_divergence", "topic": "communicative participation", "entries": [pdef, aidef], "levels": {}, "differences": "Different wording.", "rationale": "The words differ."},
        {**common, "kind": "conceptual_divergence", "topic": "versions of one work", "entries": [e_as, e2_as], "levels": {}, "differences": "The later version drops one word.", "meaning_difference": "none claimed"},
        {**common, "kind": "complementarity", "topic": "levels of analysis", "entries": [att, eng], "levels": {att: "group", eng: "dyad"},
         "differences": "One theory is about a group channel, the other about a pair.", "rationale": "They explain different levels and complement each other."},
        {**common, "kind": "conceptual_divergence", "topic": "what participation presupposes", "entries": [a_as, e_as], "levels": {},
         "differences": "Attention theory lets participation draw on a shared pool whatever its origin; engagement theory treats communication as effortful and attributable.",
         "rationale": "If messages can be produced without effort, the second assumption no longer follows from participation, while the first still does.",
         "meaning_difference": "participation is attention use for one, an expression of personal investment for the other", "missing_information": ["whether either theory addresses low-effort messages"]},
        {"kind": "implied_assumption", "topic": "observable effort", "entries": [e_as], "text": "Recipients can tell how much effort a message took.", "premises": ["Engagement is expressed through effortful communication"],
         "reasoning": "If effortful communication signals engagement, recipients must be able to perceive effort; the authors do not say so.",
         "missing_information": ["whether the paper says recipients perceive effort"], "alternative_interpretations": ["effort may signal engagement to the sender only"]}]
    CURRENT["adapter"] = proposer(proposals)
    run("landscape", "propose", "LAND_000001", *a)
    v = L.view(w["store"], "LAND_000001")
    iid = next(i["id"] for i in v["inferences"] if i["kind"] == "conceptual_divergence" and i["relationship"] == "what participation presupposes")
    print("# The model named no source for this tension. That is not a finding that none exists: the recognition status says nothing has been assessed\n")
    run("landscape", "show", "LAND_000001", *base, brief=True)
    print("# The candidates are papers to read, found by words; a match is not evidence, and the abstract-only paper is listed but cannot be inspected\n")
    run("landscape", "candidates", "LAND_000001", iid, "--limit", "3", *base)
    res = tmp / "inspection.json"
    SHARED[str(res)] = "<inspection.json>"
    res.write_text(json.dumps([{"source_id": "SRC_PART", "result": "ADDRESSES", "inspected": "whole text", "quotes": [F.A["part_claim"]], "note": "separates participation from engagement where messages are cheap"},
                               {"source_id": "SRC_ABSONLY", "result": "DOES_NOT_ADDRESS", "inspected": "abstract"}]), encoding="utf-8")
    run("landscape", "inspect", "LAND_000001", iid, "--results", str(res), "--rationale", "I read the paper; I could not read the abstract-only one", "--limit", "3", *a, expect_rc=1)
    res.write_text(json.dumps([{"source_id": "SRC_PART", "result": "ADDRESSES", "inspected": "whole text", "quotes": [F.A["part_claim"]], "note": "separates participation from engagement where messages are cheap"},
                               {"source_id": "SRC_ABSONLY", "result": "NOT_READABLE", "note": "only the abstract is held"}]), encoding="utf-8")
    run("landscape", "inspect", "LAND_000001", iid, "--results", str(res), "--rationale", "I read the paper; the abstract-only paper cannot be read", "--limit", "3", *a)
    run("landscape", "review", "LAND_000001", iid, "CONFIRMED", "--reason", "I read the papers; the tension stands against the paper that separates the two ideas.", *a, expect_rc=1)
    run("landscape", "review", "LAND_000001", iid, "CONFIRMED", "--reason", "I read the papers; the tension stands against the paper that separates the two ideas.", "--acknowledge-missing", *a)
    print("# A conceptual claim is assessed on conceptual terms. The verdict is the researcher's; the suite checks the basis it cites, apart\n")
    crit = tmp / "criteria_a.json"
    SHARED[str(crit)] = "<criteria.json>"
    threats = json.dumps([{"threat": "rival: attention is not one pool", "status": "addressed"}])
    crit.write_text(json.dumps([{"criterion": "The step from participation to dilution is valid given the stated premises", "basis": "the paper's own derivation", "basis_kind": "methodological_literature", "met": "yes"}]), encoding="utf-8")
    run("landscape", "assess", "LAND_000001", att, "--verdict", "SUPPORTED_WITHIN_SCOPE", "--criteria", str(crit), "--threats", threats, "--rationale", "Cited a methods text for a conceptual claim.", *a)
    crit.write_text(json.dumps([{"criterion": "The step from participation to dilution is valid given the stated premises", "basis": "reconstructed argument", "basis_kind": "logical_analysis", "met": "yes",
                                 "argument": {"premises": ["Attention is scarce", "Participation draws on the same pool"], "steps": [{"from": ["P1", "P3"], "infers": "less attention per message"}],
                                              "conclusion": "Participation dilutes collective attention per message", "rival_assumptions": ["attention is not a single pool"]}}]), encoding="utf-8")
    run("landscape", "assess", "LAND_000001", att, "--verdict", "SUPPORTED_WITHIN_SCOPE", "--criteria", str(crit), "--threats", threats, "--rationale", "The derivation, with a gap in my record.", *a)
    crit.write_text(json.dumps([{"criterion": "The step from participation to dilution is valid given the stated premises", "basis": "reconstructed argument", "basis_kind": "logical_analysis", "met": "yes",
                                 "argument": {"premises": ["Attention is scarce", "Participation draws on the same pool"], "steps": [{"from": ["P1", "P2"], "infers": "less attention per message"}],
                                              "conclusion": "Participation dilutes collective attention per message", "rival_assumptions": ["attention is not a single pool"]}}]), encoding="utf-8")
    run("landscape", "assess", "LAND_000001", att, "--verdict", "SUPPORTED_WITHIN_SCOPE", "--criteria", str(crit), "--threats", threats, "--rationale", "The derivation holds within group channels.", *a)
    run("landscape", "link-reproduction", "LAND_000001", att, "--kind", "new_data", "--reference", "x", "--reason", "x", *a, expect_rc=1)
    run("landscape", "show", "LAND_000001", *base, brief=True)
    export = tmp / "export_a"
    SHARED[str(export)] = "<export-a>"
    run("export", "landscape", "LAND_000001", "--out", str(export), *base)
    run("export", "verify", str(export), *base)
    rexp = tmp / "export_a_review"
    SHARED[str(rexp)] = "<export-a-review>"
    run("export", "review", "PROB_000001", "--out", str(rexp), *base)
    print("----- search_protocol.md (excerpt) -----")
    print("\n".join((rexp / "search_protocol.md").read_text(encoding="utf-8").splitlines()[:16]))
    print()
    print("----- research_landscape.md: recognition inspection and proposals (excerpt) -----")
    lines = (export / "research_landscape.md").read_text(encoding="utf-8").splitlines()
    for name, n in (("## Inquiry", 3), ("## Proposed relations", 6), ("## Recognition inspections", 10)):
        i = next(k for k, line in enumerate(lines) if line.startswith(name))
        print("\n".join(lines[i:i + n]))
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
