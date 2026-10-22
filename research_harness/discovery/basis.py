"""What the suite can say about the basis of an assessment, apart from the researcher's verdict (M5 WP6.1 remediation).

A method assessment has two separate parts and the export shows them separately:

* the **researcher's assessment** (`RESEARCHER_ASSESSED`): criteria, threats and a verdict (`INFERENCE_JUDGMENT`: the researcher answers for it);
* the **verification of the basis** the criteria cite, by deterministic operations only:

  * `BASIS_NOT_TRACEABLE`: only a sentence; nothing identifies a source, a passage or an argument. (All assessments recorded before this module existed are shown this way: they are
    not upgraded.)
  * `BASIS_TRACEABLE`: the cited Source@revision / EvidencePassage@revision exists, or the argument record is identified, but nothing says what it supports.
  * `BASIS_CHECKED`: the deterministic checks passed: for a passage, it is a quote-verified EvidencePassage at its Source's current revision, the excerpt the criterion relies on is
    verbatim inside it, and the limit of what it supports is stated; for a logical argument, every inference step cites premises or earlier steps that exist, a conclusion is given
    and rival assumptions are listed (or the absence of rivals is justified). It means those checks passed. It does not mean the passage supports the criterion, that the argument
    is valid, or that the conclusion is right.
  * `BASIS_CHECK_FAILED`: a reference does not resolve, an excerpt is not in its passage, or the argument record has a gap (a missing premise, a step from nothing).
  * `BASIS_STALE`: a cited Source was revised after the reference was recorded.

No score is computed, and nothing here raises the researcher's verdict.
"""
from __future__ import annotations

from typing import Any

from research_harness.workflows import quotes

STATUS_ORDER = ("BASIS_CHECK_FAILED", "BASIS_STALE", "BASIS_NOT_TRACEABLE", "BASIS_TRACEABLE", "BASIS_CHECKED")   # worst first
REF_KINDS = ("source", "passage")


def _norm(t: Any) -> str:
    return quotes.normalize_whitespace(str(t or ""))


def check_shape(criterion: dict[str, Any]) -> list[str]:
    """Problems with how a criterion cites its basis (a malformed reference is refused when the assessment is recorded)."""
    problems = []
    for ref in criterion.get("basis_refs") or []:
        if not isinstance(ref, dict) or ref.get("kind") not in REF_KINDS or not ref.get("ref"):
            problems.append(f"a basis reference needs kind in {REF_KINDS} and a ref: {ref}")
    arg = criterion.get("argument")
    if arg is not None and not isinstance(arg, dict):
        problems.append("argument is an object {premises, steps, conclusion, rival_assumptions}")
    return problems


def _verify_ref(store: Any, ref: dict[str, Any]) -> tuple[str, list[str]]:
    obj = store.get_object(ref["ref"])
    if obj is None:
        return "BASIS_CHECK_FAILED", [f"{ref['ref']} does not exist"]
    if ref["kind"] == "source":
        if obj["object_type"] != "Source":
            return "BASIS_CHECK_FAILED", [f"{ref['ref']} is not a Source"]
        rev = ref.get("revision")
        if rev is not None and obj["revision"] != rev:
            return "BASIS_STALE", [f"{ref['ref']} was revised (r{rev} -> r{obj['revision']}) after it was cited"]
        return "BASIS_TRACEABLE", []
    # a passage
    if obj["object_type"] != "EvidencePassage":
        return "BASIS_CHECK_FAILED", [f"{ref['ref']} is not an EvidencePassage"]
    c = obj["content"]
    pr = ref.get("revision")
    if pr is not None and store.get_object(ref["ref"], pr) is None:
        return "BASIS_CHECK_FAILED", [f"{ref['ref']} has no revision {pr}"]
    pc = (store.get_object(ref["ref"], pr)["content"] if pr is not None else c)
    src = store.get_object(pc.get("source_id") or "")
    if src is None:
        return "BASIS_CHECK_FAILED", [f"the source of {ref['ref']} does not exist"]
    if pc.get("source_revision") is not None and src["revision"] != pc["source_revision"]:
        return "BASIS_STALE", [f"{pc['source_id']} was revised (r{pc['source_revision']} -> r{src['revision']}) after {ref['ref']} was made"]
    if not pc.get("quote_verified"):
        return "BASIS_CHECK_FAILED", [f"{ref['ref']} is not a quote-verified passage"]
    excerpt = ref.get("excerpt")
    if not excerpt:
        return "BASIS_TRACEABLE", []
    if _norm(excerpt) not in _norm(pc.get("quote")):
        return "BASIS_CHECK_FAILED", [f"the excerpt cited from {ref['ref']} is not in that passage: the passage does not contain what the criterion relies on"]
    if not (ref.get("supports_only") or "").strip():
        return "BASIS_TRACEABLE", ["state what the passage supports and what it does not (supports_only); until then it is only traceable"]
    return "BASIS_CHECKED", []


def verify_argument(arg: dict[str, Any]) -> tuple[str, list[str]]:
    """Structural checks of a logical argument record. Premises are P1..Pn, steps S1..Sm in order."""
    problems = []
    premises = arg.get("premises") or []
    steps = arg.get("steps") or []
    if not premises or not all(isinstance(p, str) and p.strip() for p in premises):
        problems.append("state at least one premise (non-empty text)")
    if not steps:
        problems.append("state at least one inference step")
    known = {f"P{i}" for i in range(1, len(premises) + 1)}
    for i, st in enumerate(steps, 1):
        frm = st.get("from") if isinstance(st, dict) else None
        if not frm or not (isinstance(st.get("infers"), str) and st["infers"].strip()):
            problems.append(f"step S{i} needs `from` (premises or earlier steps) and `infers`")
        else:
            missing = [x for x in frm if x not in known]
            if missing:
                problems.append(f"step S{i} relies on {', '.join(missing)}, which is not a premise or an earlier step")
        known.add(f"S{i}")
    if not (isinstance(arg.get("conclusion"), str) and arg["conclusion"].strip()):
        problems.append("state the conclusion")
    rivals = arg.get("rival_assumptions")
    if not (rivals or (isinstance(arg.get("no_rivals_reason"), str) and arg["no_rivals_reason"].strip())):
        problems.append("list the rival assumptions, or say why there are none (no_rivals_reason)")
    return ("BASIS_CHECK_FAILED", problems) if problems else ("BASIS_CHECKED", [])


def verify_criterion(store: Any, criterion: dict[str, Any]) -> dict[str, Any]:
    """The verification status of one criterion's basis, from what it cites now."""
    parts: list[tuple[str, list[str]]] = []
    for ref in criterion.get("basis_refs") or []:
        parts.append(_verify_ref(store, ref))
    if criterion.get("argument") is not None:
        parts.append(verify_argument(criterion["argument"]))
    if not parts:
        return {"status": "BASIS_NOT_TRACEABLE", "problems": ["the basis is a sentence; no source, passage or argument record is cited"]}
    status = min((p[0] for p in parts), key=STATUS_ORDER.index)
    return {"status": status, "problems": [m for p in parts for m in p[1]]}


def verify_assessment(store: Any, criteria: list[dict[str, Any]], accepted_kinds: tuple[str, ...]) -> dict[str, Any]:
    """Per-criterion statuses, and an overall status over the criteria that rest on a kind of basis the claim's route accepts (the worst of them)."""
    per = []
    for c in criteria:
        v = verify_criterion(store, c)
        kind = c.get("basis_kind") or ("methodological_literature" if c.get("methodological_literature") else None)
        per.append({"criterion": c["criterion"], "basis_kind": kind, "accepted_kind": kind in accepted_kinds, **v})
    relevant = [p for p in per if p["accepted_kind"]]
    overall = min((p["status"] for p in relevant), key=STATUS_ORDER.index) if relevant else "NO_ACCEPTED_BASIS"
    return {"status": overall, "criteria": per,
            "meaning": "the suite's checks of the cited basis; the verdict above is the researcher's judgement, and neither follows from the other"}
