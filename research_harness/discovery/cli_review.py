"""`research review-protocol ...`, `research export review`, `research domain suggest` (M5 WP6.1 remediation)."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Callable, Optional


def _audit_text(a: dict) -> None:
    print(f"{a['problem_id']} r{a['problem_revision']}: review protocol {a['protocol_status']}" + (f" (drafted by {a['drafted_by']})" if a.get("drafted_by") and a["protocol_status"] == "DRAFT" else ""))
    for k, v in a["components"].items():
        print(f"  {k:<11}{v['status']:<24}{'; '.join(v['problems'])}")
    if a.get("prisma"):
        print("  flow: " + ", ".join(f"{k} {v}" for k, v in a["prisma"].items() if k != "included"))
    print(f"  included {len(a['included'])} (not in the corpus: {len(a['included_not_in_corpus'])}; without full text: {len(a['included_without_full_text'])}); "
          f"held but never screened: {len(a['held_but_never_screened'])}")
    print("  ready to build a landscape: " + ("yes" if a["readiness"]["can_build_landscape"] else "NO") + "".join(f"\n    - {b}" for b in a["readiness"]["blockers"]))
    for x in a["limits"][:1]:
        print(f"  note: {x}")


def register(sub: Any) -> None:
    from research_harness import cli_knowledge as K
    from research_harness.knowledge import review_protocol as RP

    def tok(a: argparse.Namespace) -> Optional[str]:
        return K._helpers()._resolve_token(a)

    p = sub.add_parser("review-protocol", help="The method of a literature review as a record on the problem: search, screening, appraisal, extraction, synthesis; checked, not judged")
    s = p.add_subparsers(dest="review_cmd", required=True)

    def add(name: str, help_: str, fn: Callable, text: Optional[Callable] = None, *, token: bool = True) -> argparse.ArgumentParser:
        q = s.add_parser(name, help=help_)
        K._common(q, token=token)
        q.set_defaults(func=lambda a, fn=fn, text=text: K._run(a, lambda: fn(K._store(a), a), text))
        return q

    q = add("set", "Record or change the plan of the review (JSON file); a draft until the researcher adopts it",
            lambda st, a: RP.set_review_protocol(st, tok(a), a.problem, K._json_arg(a.protocol, "--protocol"), a.rationale, drafted_by=a.drafted_by),
            lambda o: print(f"{o['problem_id']} r{o['revision']}: review protocol recorded (DRAFT until adopted)"))
    q.add_argument("problem")
    q.add_argument("--protocol", required=True, help="JSON file or JSON: {review_type, streams, search, screening, appraisal, extraction, synthesis, limitations}")
    q.add_argument("--rationale", required=True)
    q.add_argument("--drafted-by", dest="drafted_by", help="Who drafted it, when it is not the researcher (for example: assistant:claude)")

    q = add("adopt", "Adopt the plan as it is (author or owner). Any later edit of the plan voids the adoption",
            lambda st, a: RP.adopt_review_protocol(st, tok(a), a.problem, a.rationale), lambda o: print(f"{o['problem_id']} r{o['revision']}: plan adopted"))
    q.add_argument("problem")
    q.add_argument("--rationale", required=True)

    q = add("screen", "Append your screening decisions to the log (JSON list of {record, stage, decision, reason, stream}); each is stamped with who and when",
            lambda st, a: RP.record_screening(st, tok(a), a.problem, K._json_arg(a.entries, "--entries"), a.rationale), lambda o: print(f"{o['problem_id']} r{o['revision']}: decisions recorded"))
    q.add_argument("problem")
    q.add_argument("--entries", required=True)
    q.add_argument("--rationale", required=True)

    add("audit", "Components, flow counts, included records against the corpus, what is held but never screened, readiness", lambda st, a: RP.audit_review(st, a.problem), _audit_text,
        token=False).add_argument("problem")

    ex = sub.choices["export"]._subparsers._group_actions[0]
    q = ex.add_parser("review", help="search_protocol.md, screening_log.csv and corpus_inventory.csv of a problem's review protocol, with a manifest")
    q.add_argument("problem")
    q.add_argument("--out")
    q.add_argument("--into-repo", action="store_true", dest="into_repo")
    K._common(q, token=False)

    def go(a: argparse.Namespace) -> Any:
        from research_harness.export.manifest import write_export
        from research_harness.export.review import build_review_export
        st = K._store(a)
        return write_export(st, build_review_export(st, a.problem), Path(a.out) if a.out else None, into_repo=a.into_repo)

    q.set_defaults(func=lambda a: K._run(a, lambda: go(a), lambda o: print(f"exported to {o['out']} (closure {o['closure_digest'][:12]}): {', '.join(o['files'])}"
                                                                            + "".join(f"\n  NOT_AVAILABLE {n['file']}: {n['reason']}" for n in o["not_available"]))))
