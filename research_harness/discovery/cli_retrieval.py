"""`research retrieval ...`, `research corpus import-metadata|exclude|include`, `research export retrieval` (M5 WP5)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable, Optional


def _vers(g: dict) -> str:
    return ", ".join(f"{v['source_id']}" + ("*" if len(g["versions"]) > 1 and v["source_id"] == g["representative"] else "") + ("" if v.get("retrieved", True) else " (held, not retrieved)")
                     + (f" [{v['document_type']}, {v['review_status']}, public {v['first_public_date'] or '?'}]" if v.get("document_type") or v.get("review_status") else "")
                     for v in g["versions"])


def _currency_line(cur: dict) -> str:
    c, p, k = cur["corpus"], cur["problem"], cur["search_config"]
    return (f"corpus: {c['status']}" + (f" (+{len(c['added'])} added, {len(c['revised'])} revised, {len(c['removed'])} removed since the search)" if c["status"] == "STALE_FOR_CORPUS" else "")
            + f"  problem: {p['status']}" + (f" ({', '.join(p.get('changed') or [])} changed: r{p['recorded_revision']} -> r{p['current_revision']})" if p["status"] == "STALE" else "")
            + f"  search config: {k['status']}")


def _show_text(v: dict) -> None:
    print(f"{v['retrieval_id']} r{v['revision']} [{v['state']}] for {v['problem_id']}  {_currency_line(v['currency'])}")
    for q in v["queries"]:
        print(f"  {q['id']} ({q['interpretation']}): {', '.join(q['tokens'])}")
    if v["terms"]:
        print(f"  your terms: {', '.join(v['terms'])}")
    for e in v["expansions"]:
        print(f"  expansion {e['status']:<9}{e['term']}   ({e['source']})")
    if v["search_outdated"]:
        print("  ! the accepted expansions or terms changed since the search: run it again")
    if v["currency"]["problem"]["status"] == "STALE":
        print("  ! the problem changed since this run was made: it cannot feed a landscape; start a new run (research retrieval new --parent <this run>)")
    if v["state"] != "SEARCHED":
        return
    k = v["coverage"]
    print(f"  coverage: {k['assessed']['retrieved_full_text']} of {k['assessed']['of_full_text_in_corpus']} full texts retrieved; "
          f"{k['known_gaps']['not_read_candidates']} possibly relevant not read; floor {v['floor_after_your_review']['full_texts_on_the_list']} on your list / "
          f"{v['floor_after_your_review']['min_fulltext_sources']} needed" + ("  BELOW FLOOR" if v["floor_after_your_review"]["below_floor"] else ""))
    print("  reading list:")
    for g in v["reading_list"]:
        print(f"   {g['position']:>3}  {g['stage']:<9}{g['work_id']}  {_vers(g)}\n        {g['reason']}")
    if v["not_read_candidates"]:
        print("  possibly relevant, NOT READ (no full text held):")
        for g in v["not_read_candidates"]:
            print(f"   {g['position']:>3}  {g['stage']:<9}{g['representative']}  {g['reason']}")
    for r in v["acquisition_requests"]:
        print(f"  you want to acquire: {r['title']} ({r['reason']})")
    for s in v["set_aside"]:
        print(f"  set aside: {s['source_id']} ({s['reason']})")
    if v["coverage"]["stages"]["proposed_not_decided"]:
        print(f"  proposed terms not yet decided: {', '.join(v['coverage']['stages']['proposed_not_decided'])}")


def _eval_text(r: dict) -> None:
    print(f"retrieval evaluation (k={r['k']}, tokenizer {r['tokenizer']}, BM25 {r['bm25']})")
    for q in r["queries"]:
        a, b = q["R1"], q["R1+R2"]
        print(f"  {q['id']}: {q['relevant']} relevant full texts")
        print(f"    R1     recall@k {a['recall_at_k']}  precision@k {a['precision_at_k']}  first relevant rank {a['first_relevant_rank']}")
        print(f"    R1+R2  recall@k {b['recall_at_k']}  precision@k {b['precision_at_k']}  first relevant rank {b['first_relevant_rank']}  found only with expansions: {len(q['found_only_with_expansions'])}")
        if q["relevant_not_readable"]:
            print(f"    relevant but not readable: {json.dumps(q['relevant_not_readable'])}")
    print(f"  mean  R1 {r['mean']['R1']}\n        R1+R2 {r['mean']['R1+R2']}")


def register(sub: Any) -> None:
    from research_harness import cli_knowledge as K
    from research_harness.discovery import retrieval as RT
    from research_harness.discovery.retrieval_eval import evaluate
    from research_harness.knowledge import corpus

    def tok(a: argparse.Namespace) -> Optional[str]:
        return K._helpers()._resolve_token(a)

    p = sub.add_parser("retrieval", help="Find what to read in the stored corpus: reading list, coverage, no network")
    s = p.add_subparsers(dest="retrieval_cmd", required=True)

    def add(name: str, help_: str, fn: Callable, text: Optional[Callable] = None, *, token: bool = True, model: bool = False) -> argparse.ArgumentParser:
        q = s.add_parser(name, help=help_)
        K._common(q, token=token)
        if model:
            q.add_argument("--model")
        q.set_defaults(func=lambda a, fn=fn, text=text: K._run(a, lambda: fn(K._store(a), a), text))
        return q

    q = add("new", "Fix the queries of a retrieval run for a problem (DRAFT): one per chosen interpretation; nothing is searched yet",
            lambda st, a: RT.new_run(st, tok(a), a.problem, interpretation_ids=[x.strip() for x in a.interpretations.split(",")] if a.interpretations else None, terms=a.term,
                                     label=a.label, parent=a.parent, **({"min_fulltext_sources": a.min_fulltext} if a.min_fulltext is not None else {})),
            lambda o: print(f"{o['retrieval_id']} created (DRAFT): queries {', '.join(o['queries'])}; {o['expansions_offered']} proposed term(s) offered from the interpretations"))
    q.add_argument("problem")
    q.add_argument("--interpretations", help="Comma-separated ids; default: those you chose")
    q.add_argument("--term", action="append", help="A search term of your own (repeatable)")
    q.add_argument("--label")
    q.add_argument("--parent", help="An earlier run this one refines")
    q.add_argument("--min-fulltext", type=int, dest="min_fulltext", help="Floor: full texts needed before an absence means anything (default 3)")

    q = add("expand", "Ask a model for other names for the idea (one call); they are PROPOSED and used only after you accept them",
            lambda st, a: RT.propose_expansions(st, tok(a), a.run, K._adapter(a), k=a.k),
            lambda o: print(f"{o['status']}: {', '.join(o['proposed']) or 'nothing new'}" if o["status"] == "PROPOSED" else f"FAILED ({o['reason']}): {o['message']}"), model=True)
    q.add_argument("run")
    q.add_argument("--k", type=int, default=12)

    q = add("expansions", "Accept or reject proposed terms, or add your own, with your reason",
            lambda st, a: RT.decide_expansions(st, tok(a), a.run, rationale=a.rationale, accept=tuple(a.accept or ()), reject=tuple(a.reject or ()), add=tuple(a.add or ())),
            lambda o: print(f"{o['retrieval_id']} r{o['revision']}: accepted terms now: {', '.join(o['accepted']) or 'none'}"))
    q.add_argument("run")
    q.add_argument("--accept", action="append")
    q.add_argument("--reject", action="append")
    q.add_argument("--add", action="append", help="A term of your own")
    q.add_argument("--rationale", required=True)

    add("search", "Run R1 (and R2 with the accepted expansions) and R3a over the corpus; record hits, provenance and coverage",
        lambda st, a: RT.search(st, tok(a), a.run),
        lambda o: print(f"{o['retrieval_id']} r{o['revision']} searched on snapshot {o['corpus_snapshot_id'][:12]}: reading list {o['reading_list']} full text(s), "
                        f"{o['not_read_candidates']} possibly relevant not read, {o['neighbours']} citation neighbour(s)" + ("; BELOW THE FLOOR" if o["below_floor"] else ""))
        ).add_argument("run")

    q = add("review", "Your review of the reading list: set a hit aside, restore it, add a held paper you know, or record a paper you want to acquire",
            lambda st, a: RT.review(st, tok(a), a.run, a.action, reason=a.reason, source_id=a.source, title=a.title, doi=a.doi),
            lambda o: print(f"{o['retrieval_id']} r{o['revision']}: {o['action']} recorded"))
    q.add_argument("run")
    q.add_argument("action", choices=["irrelevant", "restore", "add", "acquire"])
    q.add_argument("--source")
    q.add_argument("--title")
    q.add_argument("--doi")
    q.add_argument("--reason", required=True)

    q = add("show", "The run: queries, expansions, reading list with the reason for each paper, not-read candidates, coverage and corpus currency",
            lambda st, a: RT.run_view(st, a.run), _show_text, token=False)
    q.add_argument("run")

    q = add("evaluate", "Recall@k, precision@k and MRR of R1 and R1+R2 against gold relevance labels (JSON file)",
            lambda st, a: evaluate(st, K._json_arg(a.gold, "--gold")), _eval_text, token=False)
    q.add_argument("--gold", required=True, help="Path to a JSON file (or JSON): {k, queries: [{id, text, accepted_expansions, relevant, relevant_not_full_text}]}")

    # corpus: metadata-only sources and exclusions
    grp = sub.choices["corpus"]._subparsers._group_actions[0]
    q = grp.add_parser("import-metadata", help="Record a paper you do not hold: metadata (and an abstract if you have one). It can never support or challenge a claim")
    q.add_argument("--citation-key", required=True, dest="citation_key")
    q.add_argument("--title", required=True)
    q.add_argument("--year", type=int)
    q.add_argument("--authors", help="Semicolon-separated")
    q.add_argument("--venue")
    q.add_argument("--doi")
    q.add_argument("--abstract")
    q.add_argument("--document-type", dest="document_type", choices=list(corpus.DOCUMENT_TYPES))
    q.add_argument("--review-status", dest="review_status", choices=list(corpus.REVIEW_STATUSES))
    q.add_argument("--first-public-date", dest="first_public_date", help="YYYY, YYYY-MM or YYYY-MM-DD")
    q.add_argument("--date-evidence", dest="date_evidence", help="Where that date comes from (a repository posting record, a DOI registration)")
    q.add_argument("--work-id", dest="work_id")
    q.add_argument("--version-id", dest="version_id")
    q.add_argument("--acquisition-note", dest="acquisition_note")
    K._common(q)
    q.set_defaults(func=lambda a: K._run(a, lambda: corpus.import_metadata_only(
        K._store(a), tok(a), citation_key=a.citation_key, title=a.title,
        **{k: v for k, v in {"year": a.year, "authors": [x.strip() for x in a.authors.split(";") if x.strip()] if a.authors else None, "venue": a.venue, "doi": a.doi,
                             "abstract": a.abstract, "document_type": a.document_type, "review_status": a.review_status, "first_public_date": a.first_public_date,
                             "date_evidence": a.date_evidence, "work_id": a.work_id, "version_id": a.version_id, "acquisition_note": a.acquisition_note}.items() if v is not None}),
                                         lambda o: print(f"{o['object_id']} revision {o['revision']}: {o['tier']}{'' if o['created'] else ' (unchanged)'}")))
    q = grp.add_parser("exclude", help="Take a source out of the corpus, with your reason (a new revision; the corpus snapshot changes)")
    q.add_argument("source")
    q.add_argument("--reason", required=True)
    K._common(q)
    q.set_defaults(func=lambda a: K._run(a, lambda: corpus.set_excluded(K._store(a), tok(a), a.source, a.reason),
                                         lambda o: print(f"{o['object_id']} r{o['revision']}: excluded" if o["changed"] else "no change")))
    q = grp.add_parser("include", help="Put an excluded source back")
    q.add_argument("source")
    K._common(q)
    q.set_defaults(func=lambda a: K._run(a, lambda: corpus.set_excluded(K._store(a), tok(a), a.source, None),
                                         lambda o: print(f"{o['object_id']} r{o['revision']}: included" if o["changed"] else "no change")))

    # export of a run
    ex = sub.choices["export"]._subparsers._group_actions[0]
    q = ex.add_parser("retrieval", help="Reading list, not-read candidates, coverage and queries of a retrieval run, with a manifest")
    q.add_argument("run")
    q.add_argument("--out")
    q.add_argument("--into-repo", action="store_true", dest="into_repo")
    K._common(q, token=False)

    def go(a: argparse.Namespace) -> Any:
        from research_harness.export.manifest import write_export
        from research_harness.export.retrieval import build_retrieval_export
        st = K._store(a)
        return write_export(st, build_retrieval_export(st, a.run), Path(a.out) if a.out else None, into_repo=a.into_repo)

    q.set_defaults(func=lambda a: K._run(a, lambda: go(a), lambda o: print(f"exported to {o['out']} (closure {o['closure_digest'][:12]}): {', '.join(o['files'])}"
                                                                            + "".join(f"\n  NOT_AVAILABLE {n['file']}: {n['reason']}" for n in o["not_available"]))))
