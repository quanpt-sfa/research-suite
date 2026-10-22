"""`research problem ...` (M5 WP4). The commands call `discovery.problem`; every act is recorded as a revision with its reason."""
from __future__ import annotations

import argparse
import json
from typing import Any, Callable, Optional


def _inquiry_args(q: argparse.ArgumentParser) -> None:
    q.add_argument("--purpose", action="append", help="Why you are doing this (repeatable): explanation, prediction, description, interpretation, theory_development, "
                   "critical_synthesis, evaluation, methodological_improvement")
    q.add_argument("--design", help="How: a study design (archival, predictive_modeling, conceptual, narrative_review, qualitative_interview, design_based, ...)")
    q.add_argument("--evidence-form", action="append", dest="evidence_form", help="On what evidence (repeatable): empirical_results, textual_evidence, conceptual_argument, "
                   "theoretical_demonstration, qualitative_interpretation")
    q.add_argument("--domain", help="Discipline: domain (accounting, finance, management, education, information_systems, cross_domain)")
    q.add_argument("--subfield")


def _inquiry(a: argparse.Namespace) -> Optional[dict]:
    inq: dict = {}
    if getattr(a, "purpose", None):
        inq["research_purposes"] = list(a.purpose)
    if getattr(a, "design", None):
        inq["research_design"] = a.design
    if getattr(a, "evidence_form", None):
        inq["evidence_forms"] = list(a.evidence_form)
    if getattr(a, "domain", None):
        inq["discipline"] = {"domain": a.domain, "subfield": getattr(a, "subfield", None)}
    return inq or None


def _problem_text(v: dict) -> None:
    print(f"{v['problem_id']} r{v['revision']} [{v['status']}] ({v['kind']})")
    print(f"  {v['statement']}")
    if v["scope"]:
        print(f"  scope: {json.dumps(v['scope'], ensure_ascii=False)}   exclusions: {', '.join(v['exclusions']) or '-'}")
    if v.get("inquiry"):
        q = v["inquiry"]
        d = q.get("discipline") or {}
        print(f"  inquiry: purpose {', '.join(q.get('research_purposes') or []) or '-'}; design {q.get('research_design') or '-'}; evidence {', '.join(q.get('evidence_forms') or []) or '-'}; "
              f"discipline {d.get('domain') or '-'}/{d.get('subfield') or '-'}")
    print("  interpretations:")
    for i in v["interpretations"]:
        mark = "*" if i["id"] in v["chosen"] else " "
        extra = f"  [{i.get('answer_kind')}; terms: {', '.join(i.get('search_terms') or [])}]" if i.get("answer_kind") else ""
        print(f"   {mark} {i['id']:<4}{i['status']:<11}{i['generated_by']:<28}{i['text']}{extra}")
    if v["promoted_to"]:
        print("  questions promoted from this problem:")
        for rid in v["promoted_to"]:
            q = v["questions_to_review"][rid]
            print(f"    {rid}  review status: {q['review_status']}" + (f"  — the problem has moved on since ({len(q['pinned_to_older_problem_revision'])} pinned edge(s))"
                                                                         if q["pinned_to_older_problem_revision"] else ""))
    print(f"  history: {len(v['revisions'])} revision(s); decisions:")
    for d in v["decisions"]:
        why = f" — {d['rationale']}" if d.get("rationale") else ""
        print(f"    {d['at'][:19]}  {d['by']:<14}{d['action']}{why}")


def register(sub: Any) -> None:
    from research_harness import cli_knowledge as K
    from research_harness.discovery import problem as P

    p = sub.add_parser("problem", help="Research problems: a phenomenon or puzzle that is not yet a question, with several readings kept side by side")
    s = p.add_subparsers(dest="problem_cmd", required=True)

    def tok(a: argparse.Namespace) -> Optional[str]:
        return K._helpers()._resolve_token(a)

    def add(name: str, help_: str, fn: Callable[[Any, argparse.Namespace], Any], text: Optional[Callable[[Any], None]] = None, *,
            token: bool = True, model: bool = False) -> argparse.ArgumentParser:
        q = s.add_parser(name, help=help_)
        K._common(q, token=token)
        if model:
            q.add_argument("--model")
        q.set_defaults(func=lambda a, fn=fn, text=text: K._run(a, lambda: fn(K._store(a), a), text))
        return q

    q = add("new", "State a problem in your own words (no corpus, Introduction or question needed)",
            lambda st, a: P.new_problem(st, tok(a), statement=a.statement, kind=a.kind, scope=K._json_arg(a.scope, "--scope"), exclusions=a.exclude,
                                        interpretations=a.interpretation, label=a.label, inquiry=_inquiry(a)),
            lambda o: print(f"{o['problem_id']} created (OPEN); interpretations: {', '.join(o['interpretations']) or 'none yet'}"))
    q.add_argument("--statement", required=True)
    q.add_argument("--kind", choices=list(P.KINDS), default="phenomenon")
    q.add_argument("--scope", help="JSON, e.g. {\"domain\": \"accounting\", \"population\": \"US listed firms\"}, or a path to a JSON file")
    q.add_argument("--exclude", action="append", help="Something outside the problem (repeatable)")
    q.add_argument("--interpretation", action="append", help="A reading of the problem in your words (repeatable)")
    q.add_argument("--label")
    _inquiry_args(q)

    q = add("add-interpretation", "Add a reading of your own", lambda st, a: P.add_interpretation(st, tok(a), a.problem, a.text),
            lambda o: print(f"{o['interpretation']} added to {o['problem_id']}"))
    q.add_argument("problem")
    q.add_argument("--text", required=True)

    q = add("frame", "Ask a model for further readings (one call); they are PROPOSED, never chosen, and never change your statement or scope",
            lambda st, a: P.frame(st, tok(a), a.problem, K._adapter(a), k=a.k),
            lambda o: print(f"{o['status']}: {', '.join(o['proposed']) or 'nothing new'} (run {o['run_id']})" if o["status"] == "PROPOSED"
                            else f"FAILED ({o['reason']}): {o['message']}"), model=True)
    q.add_argument("problem")
    q.add_argument("--k", type=int, default=4, help="At most this many proposals")

    q = add("choose", "Choose the readings to pursue, with your reason (author or owner token)",
            lambda st, a: P.choose(st, tok(a), a.problem, [x.strip() for x in a.interpretations.split(",") if x.strip()], a.rationale),
            lambda o: print(f"{o['problem_id']} r{o['revision']}: {o['status']}"))
    q.add_argument("problem")
    q.add_argument("--interpretations", required=True, help="Comma-separated ids, e.g. I1,I3")
    q.add_argument("--rationale", required=True)

    q = add("revise", "Reframe the problem: a new revision; questions promoted earlier are flagged for review",
            lambda st, a: P.revise_problem(st, tok(a), a.problem, rationale=a.rationale, statement=a.statement, scope=K._json_arg(a.scope, "--scope"),
                                           exclusions=a.exclude, inquiry=_inquiry(a)),
            lambda o: print(f"{o['problem_id']} r{o['revision']}: {o['status']}" + (f"; to review: {', '.join(o['needs_review'])}" if o["needs_review"] else "")))
    q.add_argument("problem")
    q.add_argument("--rationale", required=True)
    q.add_argument("--statement")
    q.add_argument("--scope")
    q.add_argument("--exclude", action="append")
    _inquiry_args(q)

    q = add("set-aside", "Set the problem aside, with your reason", lambda st, a: P.set_aside(st, tok(a), a.problem, a.rationale),
            lambda o: print(f"{o['problem_id']} r{o['revision']}: {o['status']}"))
    q.add_argument("problem")
    q.add_argument("--rationale", required=True)
    q = add("reopen", "Reopen a problem that was set aside", lambda st, a: P.reopen(st, tok(a), a.problem, a.rationale),
            lambda o: print(f"{o['problem_id']} r{o['revision']}: {o['status']}"))
    q.add_argument("problem")
    q.add_argument("--rationale", required=True)

    q = add("promote-to-rq", "Turn a chosen reading into a ResearchQuestion whose text you write",
            lambda st, a: P.promote_to_rq(st, tok(a), a.problem, a.interpretation, a.text, a.rationale, rq_id=a.rq_id),
            lambda o: print(f"{o['research_question']} created from {o['problem_id']} ({o['interpretation']}); the problem is now at r{o['problem_revision']}"))
    q.add_argument("problem")
    q.add_argument("--interpretation", required=True)
    q.add_argument("--text", required=True, help="The research question, in your words")
    q.add_argument("--rationale", required=True)
    q.add_argument("--rq-id", dest="rq_id")

    for name in ("show", "history"):
        q = add(name, "The problem, its readings, what was chosen and why, the questions promoted from it, and its history",
                lambda st, a: P.problem_view(st, a.problem), _problem_text, token=False)
        q.add_argument("problem")
