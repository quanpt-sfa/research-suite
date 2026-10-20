"""CLI for the 9.2 knowledge commands: corpus, passage, extract, adjudicate, library, journal-policy.

Every command that writes needs an actor token (`--token`, `--token-file` or RESEARCH_HARNESS_TOKEN). The library has
its own owner token, created by `research library create`. Commands that call a model (`extract`, `adjudicate run`)
need `--model` (or RESEARCH_MODEL) and the optional Anthropic SDK; nothing contacts a model unless one of them is run.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from research_harness.core.errors import AuthorizationError, HarnessError


def _helpers():
    from research_harness import cli
    return cli


def _common(p: argparse.ArgumentParser, *, project: bool = True, library: bool = True, token: bool = True) -> None:
    if project:
        p.add_argument("--project", "--project-id", dest="project", help="Project identifier")
    if library:
        p.add_argument("--library", action="store_true", help="Operate on the knowledge library instead of a project")
    p.add_argument("--home", help="Base directory for research projects and the library")
    if token:
        p.add_argument("--token", help="Actor token")
        p.add_argument("--token-file", help="File containing the actor token")
    p.add_argument("--json", action="store_true", help="Output JSON")


def _store(args: argparse.Namespace):
    from research_harness.knowledge.library import open_library
    cli = _helpers()
    home = cli._resolve_home(getattr(args, "home", None))
    if getattr(args, "library", False):
        return open_library(home)
    return cli._resolve_store(cli._resolve_project_id(args), getattr(args, "home", None))


def _adapter(args: argparse.Namespace) -> Any:
    from research_harness.workflows.adapters import AnthropicMessagesAdapter
    return AnthropicMessagesAdapter(getattr(args, "model", None))


def _run(args: argparse.Namespace, fn: Callable[[], Any], text: Optional[Callable[[Any], None]] = None) -> int:
    cli = _helpers()
    try:
        out = fn()
    except AuthorizationError as e:
        sys.stderr.write(f"Not authorized: {e.code} - {e.message}\n")
        return 2
    except HarnessError as e:
        sys.stderr.write(f"Error: {e.code} - {e.message}\n")
        for k, v in (e.details or {}).items():
            sys.stderr.write(f"  {k}: {json.dumps(v, default=str)}\n")
        return 1
    except (ValueError, KeyError, FileNotFoundError, ImportError) as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1
    if getattr(args, "json", False) or text is None:
        cli._json_out(out)
    else:
        text(out)
    return 0


def _json_arg(value: Optional[str], what: str) -> Any:
    if value is None:
        return None
    path = Path(value)
    raw = path.read_text(encoding="utf-8") if path.is_file() else value
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"{what}: not valid JSON ({e.msg})") from e


# --------------------------------------------------------------------------------------------------
# corpus / passage / extract
# --------------------------------------------------------------------------------------------------

def cmd_corpus_import(args: argparse.Namespace) -> int:
    from research_harness.knowledge import corpus

    def go():
        kw: dict[str, Any] = {k: getattr(args, k) for k in ("title", "year", "doi", "venue", "license_note") if getattr(args, k, None) is not None}
        if args.authors:
            kw["authors"] = [a.strip() for a in args.authors.split(";") if a.strip()]
        return corpus.import_source_file(_store(args), _helpers()._resolve_token(args), args.file, citation_key=args.citation_key or Path(args.file).stem,
                                         source_id=args.source_id, **kw)
    return _run(args, go, lambda o: print(f"{o['object_id']} revision {o['revision']}: {o['chars']} characters, {o['sections']} sections"
                                           f"{'' if o['created'] else ' (unchanged)'}"))


def cmd_passage_add(args: argparse.Namespace) -> int:
    from research_harness.knowledge import corpus

    def go():
        quote = Path(args.quote_file).read_text(encoding="utf-8") if args.quote_file else args.quote
        if not quote:
            raise ValueError("give --quote or --quote-file")
        return corpus.add_passage(_store(args), _helpers()._resolve_token(args), source_id=args.source, quote=quote,
                                  passage_id=args.passage_id, normalized_claim=args.claim)
    return _run(args, go, lambda o: print(f"{o['object_id']}: {o['source_id']} chars {o['char_start']}-{o['char_end']}"))


def cmd_passage_verify(args: argparse.Namespace) -> int:
    from research_harness.knowledge import corpus
    return _run(args, lambda: corpus.verify_passage(_store(args), args.passage),
                lambda o: print(f"{o['object_id']}: source match {o['source_match']} {o.get('reason') or ''}"))


def cmd_extract(args: argparse.Namespace) -> int:
    from research_harness.workflows import reading_protocols as rp

    def go():
        return rp.run_protocol(_store(args), _helpers()._resolve_token(args), args.source, args.protocol, _adapter(args),
                               max_input_tokens=args.budget, reuse=not args.no_cache)

    def text(o):
        print(f"{o['protocol']} on {o['source_id']}: {o['status']}{' (reused)' if o.get('reused') else ''}")
        for r in o.get("records", []):
            print(f"  {r['passage_id']}  {r['field']}: {r['value']}")
        if o.get("rejected"):
            print(f"  rejected {len(o['rejected'])} record(s): " + ", ".join(sorted({r['reason'] for r in o['rejected']})))
        if o.get("status") == "FAILED":
            print(f"  {o['reason']}: {o.get('message')}")
    return _run(args, go, text)


# --------------------------------------------------------------------------------------------------
# adjudicate
# --------------------------------------------------------------------------------------------------

def cmd_adjudicate_run(args: argparse.Namespace) -> int:
    from research_harness.workflows import eca

    def go():
        store = _store(args)
        stmt = eca.resolve_statement(store, args.statement, element=args.element, use=args.use)
        return eca.adjudicate(store, _helpers()._resolve_token(args), stmt, args.passage, _adapter(args), reuse=not args.no_cache)
    return _run(args, go, lambda o: print(o.get("adjudication_id") or o, "->", o.get("eca_verdict"), f"(guard: {o['guard']})" if o.get("guard") else ""))


def cmd_adjudicate_queue(args: argparse.Namespace) -> int:
    from research_harness.workflows import eca

    def text(items):
        if not items:
            print("Nothing is waiting for a decision.")
        for i in items:
            s = i["statement"]
            print(f"  [{i['state']}] {s['object_id']}{'#' + i['element'] if i.get('element') else ''}"
                  f"{'  ' + i['adjudication_id'] + ' proposed ' + i['eca_verdict'] if i.get('adjudication_id') else ''}"
                  f"{'  guard: ' + i['guard'] if i.get('guard') else ''}")
    return _run(args, lambda: eca.queue(_store(args)), text)


def cmd_adjudicate_confirm(args: argparse.Namespace) -> int:
    from research_harness.workflows import eca

    def go():
        applied = None
        if args.qualification_revision is not None:
            store = _store(args)
            adj = store.get_object(args.adjudication_id)
            applied = {"object_id": (adj or {}).get("content", {}).get("subject", {}).get("object_id"), "revision": args.qualification_revision}
        return eca.confirm(_store(args), _helpers()._resolve_token(args), args.adjudication_id, verdict=args.verdict,
                           rationale=args.rationale, qualification_applied_in=applied)
    return _run(args, go, lambda o: print(f"{o['adjudication_id']} confirms {o['confirms']}: {o['eca_verdict']}"))


def cmd_adjudicate_status(args: argparse.Namespace) -> int:
    from research_harness.workflows import eca

    def go():
        store = _store(args)
        return eca.statement_assessment(store, eca.resolve_statement(store, args.statement, element=args.element))
    return _run(args, go, lambda o: print(f"{o['statement']['object_id']}: {o['assessment']} (ECA: {o['eca_verdict']}; "
                                          f"researcher confirmed: {o['researcher_confirmed']})\n  " + "\n  ".join(o["reasons"])))


# --------------------------------------------------------------------------------------------------
# library
# --------------------------------------------------------------------------------------------------

def cmd_library_create(args: argparse.Namespace) -> int:
    from research_harness.knowledge.library import create_library
    cli = _helpers()

    def go():
        store, token = create_library(cli._resolve_home(args.home), owner_id=args.owner or "library_owner")
        return {"library": str(store.paths.root), "owner_token": token, "status": "CREATED"}
    return _run(args, go, lambda o: print(f"Library created at {o['library']}\nOwner Token: {o['owner_token']}\n"
                                          "IMPORTANT: Save this token. It cannot be retrieved later."))


def _lib(args: argparse.Namespace):
    from research_harness.knowledge.library import open_library
    return open_library(_helpers()._resolve_home(args.home))


def cmd_library_pin(args: argparse.Namespace) -> int:
    from research_harness.knowledge.library import pin
    return _run(args, lambda: {"pin": pin(_lib(args), args.item, args.revision)}, lambda o: print(o["pin"]))


def cmd_library_acquire(args: argparse.Namespace) -> int:
    from research_harness.knowledge import lifecycle

    def go():
        return lifecycle.acquire(_lib(args), _helpers()._resolve_token(args), item_id=args.item_id, kind=args.kind, label=args.label,
                                 acquired_from=args.acquired_from, body=_json_arg(args.body, "--body"), elements=_json_arg(args.elements, "--elements"),
                                 generated_by=args.generated_by, pins=_json_arg(args.pins, "--pins"))
    return _run(args, go, lambda o: print(f"{o['item_id']} revision {o['revision']}  {o['pin']}"))


def cmd_library_formalize(args: argparse.Namespace) -> int:
    from research_harness.knowledge import lifecycle
    return _run(args, lambda: lifecycle.formalize(_lib(args), _helpers()._resolve_token(args), args.item, as_kind=args.as_kind),
                lambda o: print(f"formalized: revision {o['revision']}  {o['pin']}"))


def cmd_library_validate(args: argparse.Namespace) -> int:
    from research_harness.knowledge import lifecycle

    def go():
        ctx = _json_arg(args.context, "--context")
        assessment = None
        if ctx:
            assessment = {"context": ctx.get("context", ctx), "evidence_basis": ctx.get("evidence_basis", []),
                          "known_criticisms": ctx.get("known_criticisms", []), "diagnostics_runs": ctx.get("diagnostics_runs", []),
                          "diagnostics_applicable": ctx.get("diagnostics_applicable", False), "contested": ctx.get("contested", False),
                          "confirmed_by_researcher": bool(args.confirm)}
        return lifecycle.validate(_lib(args), _helpers()._resolve_token(args), args.item, assessment=assessment)

    def text(o):
        print(f"{o['item_id']} revision {o['revision']}\n  source_fidelity: {o['source_fidelity']['source_fidelity']}\n"
              f"  computational_reproducibility: {o['computational_reproducibility']['computational_reproducibility']}")
        for a in o["validity_assessments"]:
            print(f"  methodological_validity [{a['context_key'][:8]}]: {a['verdict']}" + (f"  blockers: {'; '.join(a['blockers'])}" if a["blockers"] else ""))
    return _run(args, go, text)


def cmd_library_promote(args: argparse.Namespace) -> int:
    from research_harness.knowledge import lifecycle
    return _run(args, lambda: lifecycle.promote(_lib(args), _helpers()._resolve_token(args), args.item, scope=_json_arg(args.scope, "--scope") or {},
                                                exclusions=_json_arg(args.exclusions, "--exclusions"), none_known=args.none_known),
                lambda o: print(f"promoted {o['item_id']} for {json.dumps(o['scope'])}  {o['pin']}"))


def cmd_library_match(args: argparse.Namespace) -> int:
    from research_harness.knowledge import lifecycle

    def go():
        ctx = _json_arg(args.context, "--context") or {}
        return lifecycle.match(_lib(args), ctx)

    def text(rows):
        for r in rows:
            d = r["dimensions"]
            print(f"  [{r['status']}] {r['item_id']}  {r['label']}\n      fidelity={d['source_fidelity']}  reproducibility={d['computational_reproducibility']}  "
                  f"validity here={d['methodological_validity_for_this_context']}")
            for b in r["because"]:
                print(f"      - {b}")
            for q in r.get("questions", []):
                print(f"      ? {q}")
            for f in r["failure_records"]:
                print(f"      ! failure: {f['failure']}")
    return _run(args, go, text)


def cmd_library_revise(args: argparse.Namespace) -> int:
    from research_harness.knowledge import lifecycle

    def go():
        return lifecycle.revise(_lib(args), _helpers()._resolve_token(args), args.item, restrict=_json_arg(args.restrict, "--restrict"),
                                extend=_json_arg(args.extend, "--extend"), supersede_by=args.supersede_by,
                                record_failure={"failure": args.record_failure, "context": _json_arg(args.failure_context, "--failure-context")}
                                if args.record_failure else None)
    return _run(args, go)


def cmd_library_use(args: argparse.Namespace) -> int:
    from research_harness.knowledge import closure
    cli = _helpers()

    def go():
        project = cli._resolve_store(cli._resolve_project_id(args), args.home)
        return closure.use_in_project(project, cli._resolve_token(args), _lib(args), args.pin, allow_inactive=args.allow_inactive)
    return _run(args, go, lambda o: print(f"{o['status']}: {o['manifest_id']} ({o.get('objects_imported', 0)} objects, {o.get('artifacts', 0)} artifacts)"))


def cmd_library_verify_closure(args: argparse.Namespace) -> int:
    from research_harness.knowledge import closure
    cli = _helpers()

    def go():
        return closure.verify_closure(cli._resolve_store(cli._resolve_project_id(args), args.home), args.manifest)

    def text(o):
        print(f"{o['status']}: {len(o['manifests'])} manifest(s) checked (the library was not opened)")
        for p in o["problems"]:
            print(f"  - {p}")
    rc = _run(args, go, text)
    return rc


def cmd_library_check_updates(args: argparse.Namespace) -> int:
    from research_harness.knowledge import closure
    cli = _helpers()

    def go():
        return closure.check_updates(cli._resolve_store(cli._resolve_project_id(args), args.home), cli._resolve_home(args.home))

    def text(o):
        print(o["status"] + (f": {o['reason']}" if o.get("reason") else ""))
        for n in o["notices"]:
            print(f"  - {n['notice']}: {n['pin']}")
    return _run(args, go, text)


def cmd_library_retrospective(args: argparse.Namespace) -> int:
    from research_harness.knowledge import lifecycle
    cli = _helpers()
    return _run(args, lambda: lifecycle.retrospective(cli._resolve_store(cli._resolve_project_id(args), args.home)))


def cmd_journal_policy_add(args: argparse.Namespace) -> int:
    from research_harness.knowledge import journal_policy

    def go():
        return journal_policy.add_policy(_lib(args), _helpers()._resolve_token(args), journal=args.journal, policy_type=args.type,
                                         excerpt=args.excerpt, effective_from=args.effective_from, retrieved_at=args.retrieved_at,
                                         snapshot=Path(args.snapshot).read_bytes(), source_url=args.source_url)
    return _run(args, go, lambda o: print(f"{o['policy_id']}  {o['pin']}"))


def cmd_journal_policy_lookup(args: argparse.Namespace) -> int:
    from research_harness.knowledge import journal_policy

    def text(rows):
        for r in rows:
            print(f"  [{'BINDING' if r['binding'] else 'context'}] {r['policy_type']:<20} from {r['effective_from']}"
                  f"{' to ' + r['effective_to'] if r['effective_to'] else ''}: {r['excerpt'][:90]}")
    return _run(args, lambda: journal_policy.lookup(_lib(args), args.journal, as_of=args.as_of, policy_type=args.type), text)


# --------------------------------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------------------------------

def register(sub: Any) -> None:
    p = sub.add_parser("corpus", help="Papers: store text, sections and passages")
    s = p.add_subparsers(dest="corpus_cmd", required=True)
    q = s.add_parser("import", help="Store a paper (.pdf needs pypdf, .txt, .md) as a Source")
    q.add_argument("--file", required=True)
    q.add_argument("--citation-key")
    q.add_argument("--source-id")
    q.add_argument("--title")
    q.add_argument("--authors", help="Semicolon-separated")
    q.add_argument("--year", type=int)
    q.add_argument("--doi")
    q.add_argument("--venue")
    q.add_argument("--license-note", help="Where you got the right to use the paper")
    _common(q)
    q.set_defaults(func=cmd_corpus_import)

    p = sub.add_parser("passage", help="Evidence passages: verified spans of a stored source")
    s = p.add_subparsers(dest="passage_cmd", required=True)
    q = s.add_parser("add", help="Create a passage from a quote (refused if the quote is not in the stored text)")
    q.add_argument("--source", required=True)
    q.add_argument("--quote")
    q.add_argument("--quote-file")
    q.add_argument("--claim", help="Optional normalized claim")
    q.add_argument("--passage-id")
    _common(q)
    q.set_defaults(func=cmd_passage_add)
    q = s.add_parser("verify", help="Re-check a passage against the stored text")
    q.add_argument("passage")
    _common(q, token=False)
    q.set_defaults(func=cmd_passage_verify)

    q = sub.add_parser("extract", help="Run a reading protocol on a stored source (calls a model; every quote is verified)")
    q.add_argument("--source", required=True)
    q.add_argument("--protocol", required=True, help="RP_INTRO_ARGUMENT, RP_THEORY, RP_METHOD or RP_CONTRIBUTION")
    q.add_argument("--model", help="Model id (or set RESEARCH_MODEL)")
    q.add_argument("--budget", type=int, default=12000)
    q.add_argument("--no-cache", action="store_true")
    _common(q)
    q.set_defaults(func=cmd_extract)

    p = sub.add_parser("adjudicate", help="Evidence-Claim Adjudication: is a statement a fair reading of a passage?")
    s = p.add_subparsers(dest="adjudicate_cmd", required=True)
    q = s.add_parser("run", help="One model call; the result is a proposal until the researcher confirms")
    q.add_argument("--statement", required=True)
    q.add_argument("--element")
    q.add_argument("--passage", required=True)
    q.add_argument("--use", help="What the statement is used for (contribution, measurement, identification, interpretation, ...)")
    q.add_argument("--model")
    q.add_argument("--no-cache", action="store_true")
    _common(q)
    q.set_defaults(func=cmd_adjudicate_run)
    q = s.add_parser("queue", help="High-impact items waiting for a decision")
    _common(q, token=False)
    q.set_defaults(func=cmd_adjudicate_queue)
    q = s.add_parser("confirm", help="Record the researcher's decision on a proposed adjudication (author or owner token)")
    q.add_argument("adjudication_id")
    q.add_argument("--verdict", required=True, choices=["SUPPORTED_AS_STATED", "SUPPORTED_IF_QUALIFIED", "NOT_SUPPORTED", "UNRESOLVED"])
    q.add_argument("--rationale", required=True)
    q.add_argument("--qualification-revision", type=int, help="Revision of the statement that carries the qualification")
    _common(q)
    q.set_defaults(func=cmd_adjudicate_confirm)
    q = s.add_parser("status", help="What the adjudications allow a statement to claim")
    q.add_argument("--statement", required=True)
    q.add_argument("--element")
    _common(q, token=False)
    q.set_defaults(func=cmd_adjudicate_status)

    p = sub.add_parser("library", help="The shared knowledge library: lifecycle, pins, closure import")
    s = p.add_subparsers(dest="library_cmd", required=True)

    def lib(name, helptext, fn, token=True, project=False):
        q = s.add_parser(name, help=helptext)
        _common(q, project=project, library=False, token=token)
        q.set_defaults(func=fn)
        return q
    q = lib("create", "Create <home>/LIBRARY and print its owner token", cmd_library_create, token=False)
    q.add_argument("--owner", help="Owner actor id (default: library_owner)")
    q = lib("pin", "Print the pin of an item", cmd_library_pin, token=False)
    q.add_argument("item")
    q.add_argument("--revision", type=int)
    q = lib("acquire", "Create a candidate item (nothing checked yet)", cmd_library_acquire)
    q.add_argument("--kind", required=True)
    q.add_argument("--label", required=True)
    q.add_argument("--from", dest="acquired_from", required=True, choices=["source", "run", "policy", "review", "retrospective"])
    q.add_argument("--item-id")
    q.add_argument("--body", help="JSON or a JSON file")
    q.add_argument("--elements", help="JSON list or a JSON file")
    q.add_argument("--pins", help="JSON list of library pins")
    q.add_argument("--generated-by", default="researcher")
    q = lib("formalize", "Check an item against the schema of its kind", cmd_library_formalize)
    q.add_argument("item")
    q.add_argument("--as", dest="as_kind", required=True)
    q = lib("validate", "Compute the three dimensions now; optionally record a validity assessment for one context", cmd_library_validate)
    q.add_argument("item")
    q.add_argument("--context", help="JSON or file: {context, evidence_basis, known_criticisms, diagnostics_runs, ...}")
    q.add_argument("--confirm", action="store_true", help="Confirm the assessment as the researcher (author or owner token)")
    q = lib("promote", "Promote an item for a scope (owner token)", cmd_library_promote)
    q.add_argument("item")
    q.add_argument("--scope", required=True, help="JSON or file")
    q.add_argument("--exclusions", help="JSON list or file")
    q.add_argument("--none-known", action="store_true", help="Record that no exclusions are known")
    q = lib("match", "Which items apply to a project context, with each dimension apart", cmd_library_match, token=False)
    q.add_argument("--context", help="JSON or file: the project's scope, estimand, measurement, assumptions, conditions")
    q = lib("revise", "Restrict, extend, supersede, or record a failure (new revision, history kept)", cmd_library_revise)
    q.add_argument("item")
    q.add_argument("--restrict", help="JSON list of new exclusions")
    q.add_argument("--extend", help="JSON object of scope additions")
    q.add_argument("--supersede-by", help="Pin of the replacing item")
    q.add_argument("--record-failure", help="What failed")
    q.add_argument("--failure-context", help="JSON or file")
    q = lib("use", "Import an item with its whole dependency closure into a project, atomically", cmd_library_use, project=True)
    q.add_argument("--pin", required=True)
    q.add_argument("--allow-inactive", action="store_true")
    q = lib("verify-closure", "Check a project's imports against their manifests (does not open the library)", cmd_library_verify_closure,
            token=False, project=True)
    q.add_argument("--manifest")
    lib("check-updates", "Compare a project's imports with the library; a missing library is a status", cmd_library_check_updates,
        token=False, project=True)
    lib("retrospective", "What this project produced that could become library knowledge", cmd_library_retrospective, token=False, project=True)

    p = sub.add_parser("journal-policy", help="Journal Policy Registry (stored in the library)")
    s = p.add_subparsers(dest="jp_cmd", required=True)
    q = s.add_parser("add", help="Record a policy with its snapshot; the excerpt must be in the snapshot")
    q.add_argument("--journal", required=True)
    q.add_argument("--type", required=True, choices=["official_requirement", "reporting_standard", "published_precedent", "editorial_feedback"])
    q.add_argument("--excerpt", required=True)
    q.add_argument("--snapshot", required=True, help="File with the policy text as retrieved")
    q.add_argument("--effective-from", required=True)
    q.add_argument("--retrieved-at", required=True)
    q.add_argument("--source-url")
    _common(q, project=False, library=False)
    q.set_defaults(func=cmd_journal_policy_add)
    q = s.add_parser("lookup", help="Policies of a journal; only an official requirement in force is binding")
    q.add_argument("--journal", required=True)
    q.add_argument("--as-of")
    q.add_argument("--type")
    _common(q, project=False, library=False, token=False)
    q.set_defaults(func=cmd_journal_policy_lookup)
