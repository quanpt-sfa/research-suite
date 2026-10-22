"""`research landscape ...` and `research export landscape` (M5 WP6)."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Callable, Optional

DIMENSION_NOTE = "reading / inference / reproduction are separate; none of them says a finding is established"


def _currency_line(cur: dict) -> str:
    def s(k: str) -> str:
        return cur[k]["status"]
    return (f"retrieval run: {s('retrieval')}  corpus: {s('corpus')}  problem: {s('problem')}  search config: {s('search_config')}  sources read: {s('sources_read')}")


def _ev(e: dict) -> str:
    ev = e["layers"]["evidence"]
    if "inference" not in ev:
        return f"reading {ev['reading']['value']}"
    i = ev["inference"]
    inf = i["value"] + (f" (researcher's verdict {i['verdict']}, {i['route']} route; suite's check of the basis: {i['verification']['status']})" if i.get("verdict") else "")
    return f"reading {ev['reading']['value']} · inference {inf} · reproduction {ev['reproduction']['value']}"


def _show_text(v: dict) -> None:
    print(f"{v['landscape_id']} r{v['revision']} \"{v['label']}\" from {v['retrieval_id']} for {v['problem_id']}")
    print(f"  {_currency_line(v['currency'])}")
    if not v["currency"]["all_current"]:
        print("  ! this landscape no longer matches what it was built from: do not build on it; rebuild from a current run (research landscape build RUN --carry-from " + v["landscape_id"] + ")")
    inq = v.get("inquiry")
    if inq:
        d = inq.get("discipline") or {}
        print(f"  inquiry: purpose {', '.join(inq.get('research_purposes') or []) or '-'}; design {inq.get('research_design') or '-'}; evidence {', '.join(inq.get('evidence_forms') or []) or '-'}; "
              f"discipline {d.get('domain') or '-'}/{d.get('subfield') or '-'}")
    k = v["coverage"]
    print(f"  {k['works_read']} of {k['works_on_reading_list']} works read ({k['sources_read']} sources, versions: {v['versions_policy']}); {k['entries']} entries "
          f"{k['entries_by_type']}; {k['possibly_relevant_not_read']} possibly relevant papers not read")
    for wid in k["works_with_a_later_version_not_read"]:
        w = v["works"][wid]
        print(f"  ! {wid}: {w['latest_held']} is held and later than what was read ({', '.join(w['read'])}); its claims are not in this landscape")
    for wid in k["works_where_the_latest_version_cannot_be_told"]:
        print(f"  ! {wid}: several versions are held and no public date says which is latest")
    by: dict[str, list[dict]] = {}
    for e in v["entries"]:
        by.setdefault(e["category"], []).append(e)
    for cat, es in by.items():
        print(f"  {cat}:")
        for e in es:
            tag = f" [{e['direction']}]" if e.get("direction") else (f" [{e['claim_type']}]" if e.get("claim_type") else (f" [{e['item_type']}]" if e.get("item_type") else ""))
            lab = f" ({e['label']})" if e.get("label") else ""
            print(f"    {e['id']} {e['source_id']}@{e['source_revision']}{lab}{tag}: {e['text']}")
            print(f"        quote: \"{e['author_claim']['quote']}\"  at {e['author_claim']['locator']} (passage {e['author_claim']['passage_id']})")
            if e.get("claim_kind"):
                print(f"        {e['claim_kind']} claim · {_ev(e)}")
    rule = [i for i in v["inferences"] if i["derivation"] == "rule"]
    prop = [i for i in v["inferences"] if i["derivation"] != "rule"]
    if rule:
        print("  suite inferences (PROVISIONAL; derived by the suite, not by a model):")
        for i in rule:
            who = " vs ".join(f"{s['entry']} ({s['source_id']}@{s['source_revision']}" + (f", {s['direction']}" if s.get("direction") else "") + ")" for s in i["sides"])
            rev = f"; your decision: {i['reviews'][-1]['decision']}" if i["reviews"] else "; not reviewed by you"
            print(f"    {i['id']} {i['kind']} ({i['subkind']}) on \"{i['relationship']}\": {who}{rev}" + (f"; NEEDS REASSESSMENT: {'; '.join(i['needs_reassessment'])}" if i["needs_reassessment"] else ""))
    if prop:
        print("  proposals (not derived by rule; none is established until you decide):")
        for i in prop:
            who = " vs ".join(f"{s['entry']} ({s['source_id']}@{s['source_revision']})" for s in i["sides"])
            body = f"{i['text']} (reasoning: {i['reasoning']})" if i["kind"] == "implied_assumption" else f"{who}"
            extra = ""
            if i["recognition"]:
                rc = i["recognition"]
                extra += f"; recognition {rc['status']}" + (": " + ", ".join(f"{a['source_id']}@{a['source_revision']}" for a in rc["addressed_by"]) if rc["addressed_by"] else "") \
                    + (f" (unverified model hint: {', '.join(h['source_id'] for h in rc['model_hints'])})" if rc["model_hints"] and rc["status"] == "POTENTIAL_ADDRESSING_SOURCES" else "")
            if i["missing_information"]:
                extra += f"; missing: {'; '.join(i['missing_information'])}"
            print(f"    {i['id']} {i['kind']} on \"{i['relationship']}\" [{i['derivation']}; {i['provenance']}]: {body}; {i['confirmation']['state']}{extra}"
                  + (f"; NEEDS REASSESSMENT: {'; '.join(i['needs_reassessment'])}" if i["needs_reassessment"] else ""))
    print(f"  {DIMENSION_NOTE}")


def _build_text(o: dict) -> None:
    print(f"{o['landscape_id']} built: {o['entries']} entries from {o['works_read']} works ({o['sources_read']} sources), {o['model_calls']} model call(s); "
          f"inferences {o['inferences']}")


def _alias_groups(items: Optional[list[str]]) -> list[list[str]]:
    return [[x.strip() for x in g.split("=") if x.strip()] for g in items or []]


def register(sub: Any) -> None:
    from research_harness import cli_knowledge as K
    from research_harness.discovery import landscape as L

    def tok(a: argparse.Namespace) -> Optional[str]:
        return K._helpers()._resolve_token(a)

    p = sub.add_parser("landscape", help="The landscape of the literature on a problem: entries with their passages, evidence dimensions and suite inferences")
    s = p.add_subparsers(dest="landscape_cmd", required=True)

    def add(name: str, help_: str, fn: Callable, text: Optional[Callable] = None, *, token: bool = True, model: bool = False) -> argparse.ArgumentParser:
        q = s.add_parser(name, help=help_)
        K._common(q, token=token)
        if model:
            q.add_argument("--model")
        q.set_defaults(func=lambda a, fn=fn, text=text: K._run(a, lambda: fn(K._store(a), a), text))
        return q

    q = add("build", "Read the reading list of a current retrieval run (model calls, quote-verified) and build the landscape",
            lambda st, a: L.build(st, tok(a), a.run, K._adapter(a), label=a.label, versions=a.versions, works=a.work or None, aliases=_alias_groups(a.same),
                                  carry_from=a.carry_from, allow_stale_corpus=a.allow_stale_corpus),
            _build_text, model=True)
    q.add_argument("run", help="A searched retrieval run whose problem, terms and corpus are current")
    q.add_argument("--label")
    q.add_argument("--versions", choices=list(L.VERSION_POLICIES), default="retrieved",
                   help="retrieved: read the version the search found (default); all: every held full-text version of each work")
    q.add_argument("--work", action="append", help="Read only this work (repeatable)")
    q.add_argument("--same", action="append", metavar="A=B", help="Two relationship or construct names you declare the same (repeatable)")
    q.add_argument("--carry-from", dest="carry_from", help="An earlier landscape whose assessments, reproduction links and decisions are kept where nothing they concern changed")
    q.add_argument("--allow-stale-corpus", action="store_true", dest="allow_stale_corpus", help="Build on the corpus snapshot of the search although the corpus has changed since")

    add("show", "Entries by type (three layers each), suite inferences, versions and currency",
        lambda st, a: L.view(st, a.landscape), _show_text, token=False).add_argument("landscape")
    add("list", "Landscapes in this project", lambda st, a: {"landscapes": L.list_landscapes(st)},
        lambda o: print("\n".join(f"{x['landscape_id']} r{x['revision']} {x['label']}: {x['entries']} entries, {x['inferences']} inference(s), {x['works_read']} works read (from {x['retrieval_id']})"
                                   for x in o["landscapes"]) or "no landscapes"), token=False)

    q = add("propose", "A model proposes relations between studies and implied assumptions (one call); every proposal is checked by a gate and stays UNCONFIRMED until you decide",
            lambda st, a: L.propose_relations(st, tok(a), a.landscape, K._adapter(a), kinds=a.kind or None),
            lambda o: print(f"{o['status']}: {len(o['accepted'])} proposal(s) recorded ({', '.join(o['accepted']) or 'none'}), {len(o['rejected'])} rejected" +
                            "".join(f"\n  rejected {r['proposal'].get('kind')} {r['proposal'].get('entries')}: {r['code']}: {r['reason']}" for r in o["rejected"])) if o["status"] == "PROPOSED"
            else print(f"FAILED ({o['reason']}): {o['message']}"), model=True)
    q.add_argument("landscape")
    q.add_argument("--kind", action="append", choices=list(L.REL.PROPOSABLE_KINDS), help="Ask only for this kind (repeatable)")

    q = add("add-proposal", "Record your own proposal, or one a critic made, through the same gate (JSON file): a relation between studies or an implied assumption",
            lambda st, a: L.add_proposal(st, tok(a), a.landscape, K._json_arg(a.proposal, "--proposal"), provenance=a.provenance, critic=a.critic),
            lambda o: print(f"{o['landscape_id']} r{o['revision']}: {o['inference']} recorded (UNCONFIRMED)"))
    q.add_argument("landscape")
    q.add_argument("--proposal", required=True, help="JSON file or JSON: {kind, topic, entries: [E..], shared, differences, rationale, missing_information, levels, meaning_difference, "
                   "already_addressed_by: [{entry, how}], search_terms} or {kind: implied_assumption, topic, entries, text, reasoning}")
    q.add_argument("--provenance", choices=list(L.REL.PROVENANCES), default="researcher_proposed")
    q.add_argument("--critic", help="With critic_proposed: whose proposal it is")

    q = add("candidates", "Held sources that may already address a proposed relation (by words alone, no model): papers to read, not evidence",
            lambda st, a: L.tension_candidates(st, a.landscape, a.inference, limit=a.limit, terms=a.term or None),
            lambda o: print(o["note"] + "\n" + "\n".join(f"  {c['source_id']}@{c['source_revision']} [{c['tier']}{'' if c['readable'] else ', cannot be inspected'}] score {c['score']} matched {', '.join(c['matched_terms'])}"
                                                           f"{'  (read for this landscape)' if c['read_in_landscape'] else '  (not read for this landscape)'}{'  (model hint)' if c['model_hint'] else ''}  {c['title']}"
                                                           for c in o["candidates"]) or "  none"),
            token=False)
    q.add_argument("landscape")
    q.add_argument("inference")
    q.add_argument("--limit", type=int, default=10)
    q.add_argument("--term", action="append", help="Search terms of your own instead of the proposal's (repeatable)")

    q = add("inspect", "Record your inspection of the sources that may already address a proposed tension (JSON of results). The status that follows is about the inspected set only",
            lambda st, a: L.inspect_tension(st, tok(a), a.landscape, a.inference, results=K._json_arg(a.results, "--results"), rationale=a.rationale, terms=a.term or None,
                                            extra_candidates=a.extra_candidate or None, candidate_limit=a.limit),
            lambda o: print(f"{o['landscape_id']} r{o['revision']}: {o['inspection']} on {o['inference']}: {o['status']} ({o['inspected']} of {o['candidates']} candidate(s) inspected)"
                            + "".join(f"\n  ! {q}" for q in o["qualifications"])))
    q.add_argument("landscape")
    q.add_argument("inference")
    q.add_argument("--results", required=True, help="JSON file or JSON: [{source_id, result: ADDRESSES|MENTIONS_PHENOMENON_ONLY|DOES_NOT_ADDRESS|NOT_READABLE, inspected, note, quotes: [...]}]")
    q.add_argument("--rationale", required=True)
    q.add_argument("--term", action="append")
    q.add_argument("--extra-candidate", action="append", dest="extra_candidate", help="A held source to add to the candidates (repeatable)")
    q.add_argument("--limit", type=int, default=10)

    q = add("review", "Your decision on an inference or proposal (contradiction, version difference, relation, implied assumption), with your reason",
            lambda st, a: L.review_inference(st, tok(a), a.landscape, a.inference, a.decision, a.reason, acknowledge_missing=a.acknowledge_missing),
            lambda o: print(f"{o['landscape_id']} r{o['revision']}: {o['inference']} {o['decision']}"))
    q.add_argument("landscape")
    q.add_argument("inference")
    q.add_argument("decision", choices=["CONFIRMED", "DISMISSED", "NEEDS_SCOPE"])
    q.add_argument("--reason", required=True)
    q.add_argument("--acknowledge-missing", action="store_true", dest="acknowledge_missing", help="Confirming a proposal that lists missing information needs you to say you read it")

    q = add("assess", "Your method assessment of a finding: does the design permit the inference? (criteria with basis, threats, rationale). A model never writes one",
            lambda st, a: L.assess_method(st, tok(a), a.landscape, a.entry, verdict=a.verdict, criteria=K._json_arg(a.criteria, "--criteria"),
                                          threats=K._json_arg(a.threats, "--threats") if a.threats else [], rationale=a.rationale,
                                          code_evidence=K._json_arg(a.code_evidence, "--code-evidence") if a.code_evidence else None),
            lambda o: print(f"{o['landscape_id']} r{o['revision']}: {o['entry']} method assessment {o['verdict']}" + (f" (capped: {'; '.join(o['blockers'])})" if o["blockers"] else "")))
    q.add_argument("landscape")
    q.add_argument("entry")
    q.add_argument("--verdict", required=True, choices=list(L.METHOD_VERDICTS))
    q.add_argument("--criteria", required=True, help="JSON: [{criterion, basis, basis_kind (methodological_literature|theoretical_literature|logical_analysis|textual_evidence), "
                   "met: yes|no|unclear}] (methodological_literature: true still works for an empirical claim)")
    q.add_argument("--threats", help="JSON: [{threat, status: addressed|scoped_out|open}] (a rival explanation or alternative interpretation counts as a threat)")
    q.add_argument("--code-evidence", dest="code_evidence", help="JSON: [{file_sha256, lines}]")
    q.add_argument("--rationale", required=True)

    q = add("link-reproduction", "Tie a finding to a replication-package reproduction, or record a new-data replication you know of",
            lambda st, a: L.link_reproduction(st, tok(a), a.landscape, a.entry, kind=a.kind, reason=a.reason, package_id=a.package,
                                              outputs=[x.strip() for x in a.outputs.split(",")] if a.outputs else None, reference=a.reference),
            lambda o: print(f"{o['landscape_id']} r{o['revision']}: {o['entry']} reproduction link recorded"))
    q.add_argument("landscape")
    q.add_argument("entry")
    q.add_argument("--kind", required=True, choices=list(L.REPRODUCTION_KINDS))
    q.add_argument("--package", help="package_run: the replication package (KnowledgeItem id)")
    q.add_argument("--outputs", help="package_run: comma-separated output ids of the package that concern this finding")
    q.add_argument("--reference", help="new_data: where the replication is reported")
    q.add_argument("--reason", required=True)

    ex = sub.choices["export"]._subparsers._group_actions[0]
    q = ex.add_parser("landscape", help="research_landscape.md, evidence table, works and versions, with a manifest")
    q.add_argument("landscape")
    q.add_argument("--out")
    q.add_argument("--into-repo", action="store_true", dest="into_repo")
    K._common(q, token=False)

    def go(a: argparse.Namespace) -> Any:
        from research_harness.export.landscape import build_landscape_export
        from research_harness.export.manifest import write_export
        st = K._store(a)
        return write_export(st, build_landscape_export(st, a.landscape), Path(a.out) if a.out else None, into_repo=a.into_repo)

    q.set_defaults(func=lambda a: K._run(a, lambda: go(a), lambda o: print(f"exported to {o['out']} (closure {o['closure_digest'][:12]}): {', '.join(o['files'])}"
                                                                            + "".join(f"\n  NOT_AVAILABLE {n['file']}: {n['reason']}" for n in o["not_available"]))))
