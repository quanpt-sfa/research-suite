"""CLI for the 9.2 knowledge commands: corpus, passage, extract, adjudicate, library, journal-policy.

Every command that writes needs an actor token (`--token`, `--token-file` or RESEARCH_HARNESS_TOKEN). The library has
its own owner token, created by `research library create`. Commands that call a model (`extract`, `adjudicate run`)
need `--model` (or RESEARCH_MODEL) and the optional Anthropic SDK; nothing contacts a model unless one of them is run.
"""
from __future__ import annotations

import argparse
import json
import os
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
    """The model adapter named by `--provider` (or RESEARCH_PROVIDER): `anthropic` (default) or `openai-compatible` (needs --base-url and
    RESEARCH_API_KEY; the key is read from the environment only, never from an argument)."""
    provider = getattr(args, "provider", None) or os.environ.get("RESEARCH_PROVIDER") or "anthropic"
    if provider == "openai-compatible":
        from research_harness.workflows.adapters import OpenAICompatibleAdapter
        return OpenAICompatibleAdapter(getattr(args, "model", None), base_url=getattr(args, "base_url", None))
    if provider != "anthropic":
        raise ValueError(f"unknown provider {provider!r}: use anthropic or openai-compatible")
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

    register_m3(sub)
    register_m4(sub)
    register_publish(sub)
    register_review(sub)
    _add_provider_options(sub)


# --------------------------------------------------------------------------------------------------
# M3: literature, introduction argument, playbooks, plans, execution, replication, claim change
# --------------------------------------------------------------------------------------------------

def _ids(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def cmd_literature_run(args: argparse.Namespace) -> int:
    from research_harness.knowledge import literature
    cli = _helpers()

    def go():
        return literature.run_literature(_store(args), cli._resolve_token(args), args.purpose, _ids(args.sources), _adapter(args), mode=args.mode,
                                         search_protocol=_json_arg(args.search_protocol, "--search-protocol"),
                                         screening_log=_json_arg(args.screening_log, "--screening-log"), max_input_tokens=args.budget, reuse=not args.no_cache)
    return _run(args, go, lambda o: print(f"{o['purpose']} review of {len(o['sources'])} source(s) [{o['mode']}]: {o['extraction']['model_calls']} model call(s), "
                                          f"{o['extraction']['reused']} reused  (run {o['run_id']})"))


def cmd_intro_analyze(args: argparse.Namespace) -> int:
    from research_harness.knowledge import argument
    return _run(args, lambda: argument.analyze_introduction(_store(args), _helpers()._resolve_token(args), args.source, _adapter(args),
                                                            max_input_tokens=args.budget),
                lambda o: print(f"{o['argument_id']}: {o['status']}" + (f"; missing: {', '.join(o['missing_components'])}" if o["missing_components"] else "")))


def cmd_playbook_draft(args: argparse.Namespace) -> int:
    from research_harness.knowledge import literature, playbooks
    cli = _helpers()

    def go():
        store, tok = _store(args), cli._resolve_token(args)
        review = literature.run_literature(store, tok, "method", _ids(args.sources), _adapter(args), max_input_tokens=args.budget)
        return playbooks.draft_playbook_from_methods(store, tok, review, playbook_id=args.id, label=args.label, scope=_json_arg(args.scope, "--scope"),
                                                     exclusions=_json_arg(args.exclusions, "--exclusions"), replication_package=args.replication,
                                                     replication_links=_json_arg(args.links, "--links"))
    return _run(args, go, lambda o: print(f"{o['object_id']}: {o['elements']} element(s), {o['critical']} critical  {o['pin']}"))


def cmd_playbook_select(args: argparse.Namespace) -> int:
    from research_harness.knowledge import playbooks

    def text(rows):
        for r in rows:
            d = r["dimensions"]
            print(f"  [{r['status']}] {r['item_id']}  fidelity={d['source_fidelity']} reproducibility={d['computational_reproducibility']} "
                  f"validity here={d['methodological_validity_for_this_context']}")
            for b in r["because"]:
                print(f"      - {b}")
            for q in r.get("questions", []):
                print(f"      ? {q}")
    return _run(args, lambda: playbooks.select_playbooks(_store(args), _json_arg(args.context, "--context") or {}), text)


def cmd_plan_draft(args: argparse.Namespace) -> int:
    from research_harness.knowledge import playbooks
    cli = _helpers()

    def go():
        store = _store(args)
        plan = playbooks.draft_analysis_plan(store, args.playbook, plan_id=args.id, label=args.label, context=_json_arg(args.context, "--context") or {},
                                             steps=_json_arg(args.steps, "--steps"), declared=_json_arg(args.declared, "--declared"))
        if args.do_import:   # accepting the proposal is the researcher's act
            return {"imported": playbooks.submit_operations(store, cli._resolve_token(args), plan["operations"], proposal_id=f"PROP_{args.id}"), **plan}
        return plan
    return _run(args, go)


def cmd_plan_run(args: argparse.Namespace) -> int:
    from research_harness.execution import dag
    cli = _helpers()

    def text(o):
        for sid in o["order"]:
            s = o["steps"].get(sid)
            if s:
                print(f"  {sid:<20} {s['execution']}" + (f"  blocked by {', '.join(s['blocked_by'])}" if s.get("blocked_by") else "")
                      + (f"  signals {s['signals']}" if s.get("signals") else ""))
    return _run(args, lambda: dag.run_plan(_store(args), cli._resolve_token(args), args.plan, args.workdir, only=args.only, from_step=args.from_step,
                                           timeout=args.timeout), text)


def cmd_plan_fidelity(args: argparse.Namespace) -> int:
    from research_harness.knowledge import playbooks
    return _run(args, lambda: playbooks.fidelity_report(_store(args), args.plan))


def cmd_replication_add(args: argparse.Namespace) -> int:
    from research_harness.knowledge.replication import add_replication_package
    return _run(args, lambda: add_replication_package(
        _store(args), _helpers()._resolve_token(args), package_id=args.id, label=args.label, directory=args.directory, files=_ids(args.files),
        expected_outputs=_json_arg(args.expected, "--expected"), source_id=args.source, entitlement=_json_arg(args.entitlement, "--entitlement")),
        lambda o: print(f"{o['package_id']}: {o['files']} file(s), {o['bytes']} bytes; entitlement {o['entitlement']['status']}"))


def cmd_replication_reproduce(args: argparse.Namespace) -> int:
    from research_harness.knowledge.replication import record_reproduction
    return _run(args, lambda: record_reproduction(_store(args), _helpers()._resolve_token(args), args.package),
                lambda o: print(f"{o['item']}: {o['computational_reproducibility']} (recorded in: {', '.join(o['recorded_in']) or 'the package only'})"))


def cmd_claim_assess(args: argparse.Namespace) -> int:
    from research_harness.knowledge import loop
    return _run(args, lambda: loop.assess_claim_change(_store(args), _helpers()._resolve_token(args), args.claim, change_id=args.id,
                                                       interpretation_ids=_ids(args.interpretations) if args.interpretations else None),
                lambda o: print(f"{o['claim_id']}: {o['change'] or 'no change derived'} ({o['assessment']}) - {' '.join(o['because'])}"))


def cmd_claim_confirm(args: argparse.Namespace) -> int:
    from research_harness.knowledge import loop
    return _run(args, lambda: loop.confirm_claim_change(_store(args), _helpers()._resolve_token(args), args.change, rationale=args.rationale))


def cmd_adjudicate_coverage(args: argparse.Namespace) -> int:
    from research_harness.workflows import eca

    def text(o):
        print(f"HIGH: {o['high_adjudicated']}/{o['high_total']} adjudicated; STANDARD sample: {o['standard_sampled'] - len(o['sample_not_adjudicated'])}/{o['standard_sampled']}; "
              f"{'complete' if o['complete'] else 'incomplete'}")
        for s in o["high_not_adjudicated"] + o["sample_not_adjudicated"]:
            print(f"  open: {s['object_id']}{'#' + s['element'] if s['element'] else ''} [{s['risk_tier']}]")
    return _run(args, lambda: eca.coverage_report(_store(args), rate=args.rate), text)


def register_m3(sub: Any) -> None:
    p = sub.add_parser("literature", help="Literature engine: theory, method or contribution review of stored sources (calls a model)")
    s = p.add_subparsers(dest="literature_cmd", required=True)
    q = s.add_parser("run")
    q.add_argument("--purpose", required=True, choices=["theory", "method", "contribution"])
    q.add_argument("--sources", required=True, help="Comma-separated Source ids")
    q.add_argument("--mode", default="narrative", choices=["narrative", "systematic"])
    q.add_argument("--search-protocol", help="JSON or file (systematic mode)")
    q.add_argument("--screening-log", help="JSON list or file (systematic mode)")
    q.add_argument("--model")
    q.add_argument("--budget", type=int, default=12000)
    q.add_argument("--no-cache", action="store_true")
    _common(q)
    q.set_defaults(func=cmd_literature_run)

    p = sub.add_parser("intro", help="Introduction argument analysis")
    s = p.add_subparsers(dest="intro_cmd", required=True)
    q = s.add_parser("analyze")
    q.add_argument("--source", required=True)
    q.add_argument("--model")
    q.add_argument("--budget", type=int, default=12000)
    _common(q)
    q.set_defaults(func=cmd_intro_analyze)

    p = sub.add_parser("playbook", help="Method playbooks")
    s = p.add_subparsers(dest="playbook_cmd", required=True)
    q = s.add_parser("draft", help="Method review of the sources, then a draft playbook (EXTRACTED: nothing validated)")
    q.add_argument("--sources", required=True)
    q.add_argument("--id", required=True)
    q.add_argument("--label", required=True)
    q.add_argument("--scope", help="JSON or file")
    q.add_argument("--exclusions", help="JSON list or file")
    q.add_argument("--replication", help="Replication package item id")
    q.add_argument("--links", help="JSON {category: [output ids]}")
    q.add_argument("--model")
    q.add_argument("--budget", type=int, default=12000)
    _common(q)
    q.set_defaults(func=cmd_playbook_draft)
    q = s.add_parser("select", help="Playbooks for a project context, ranked and justified")
    q.add_argument("--context", help="JSON or file: scope, estimand, measurement, assumptions, conditions, facts")
    _common(q, token=False)
    q.set_defaults(func=cmd_playbook_select)

    p = sub.add_parser("plan", help="Analysis plans: draft as a proposal, run the execution DAG, report fidelity")
    s = p.add_subparsers(dest="plan_cmd", required=True)
    q = s.add_parser("draft")
    q.add_argument("--playbook", required=True)
    q.add_argument("--id", required=True)
    q.add_argument("--label", required=True)
    q.add_argument("--context", required=True, help="JSON or file")
    q.add_argument("--steps", required=True, help="JSON list or file")
    q.add_argument("--declared", help="JSON or file: sesoi, alpha, basis")
    q.add_argument("--import", dest="do_import", action="store_true", help="Import the proposal (the researcher accepting it)")
    _common(q)
    q.set_defaults(func=cmd_plan_draft)
    q = s.add_parser("run", help="Run the plan's commands in a working directory (owner token)")
    q.add_argument("plan")
    q.add_argument("--workdir", required=True)
    q.add_argument("--only")
    q.add_argument("--from-step")
    q.add_argument("--timeout", type=int, default=600)
    _common(q)
    q.set_defaults(func=cmd_plan_run)
    q = s.add_parser("fidelity", help="What the plan rests on, what was inherited/adapted/added/omitted, and how the runs went")
    q.add_argument("plan")
    _common(q, token=False)
    q.set_defaults(func=cmd_plan_fidelity)

    p = sub.add_parser("replication", help="Replication packages")
    s = p.add_subparsers(dest="replication_cmd", required=True)
    q = s.add_parser("add")
    q.add_argument("--id", required=True)
    q.add_argument("--label", required=True)
    q.add_argument("--directory", required=True)
    q.add_argument("--files", required=True, help="Comma-separated paths relative to the directory")
    q.add_argument("--expected", help="JSON list or file of expected outputs")
    q.add_argument("--source")
    q.add_argument("--entitlement", help="JSON: status (unspecified|declared|confirmed), license, basis")
    _common(q)
    q.set_defaults(func=cmd_replication_add)
    q = s.add_parser("reproduce")
    q.add_argument("package")
    _common(q)
    q.set_defaults(func=cmd_replication_reproduce)

    p = sub.add_parser("claim-change", help="Claim change from the interpretation layer (HIGH risk; provisional until confirmed)")
    s = p.add_subparsers(dest="claim_cmd", required=True)
    q = s.add_parser("assess")
    q.add_argument("--claim", required=True)
    q.add_argument("--id", required=True)
    q.add_argument("--interpretations", help="Comma-separated interpretation ids (default: all)")
    _common(q)
    q.set_defaults(func=cmd_claim_assess)
    q = s.add_parser("confirm")
    q.add_argument("change")
    q.add_argument("--rationale", required=True)
    _common(q)
    q.set_defaults(func=cmd_claim_confirm)

    # attached to the existing `adjudicate` group
    for name, action in sub.choices.items():
        if name == "adjudicate":
            for sp in action._subparsers._group_actions:
                q = sp.add_parser("coverage", help="Where the 100%% HIGH / stratified 20%% STANDARD policy stands")
                q.add_argument("--rate", type=float, default=0.2)
                _common(q, token=False)
                q.set_defaults(func=cmd_adjudicate_coverage)


# --------------------------------------------------------------------------------------------------
# M4: benchmarks and gates
# --------------------------------------------------------------------------------------------------

def cmd_benchmark_logic(args: argparse.Namespace) -> int:
    from research_harness.benchmarks.logic import run_logic_benchmark

    def text(o):
        for c in o["cases"]:
            print(f"  [{'ok' if c['passed'] and c['perturbation_changes_outcome'] else 'FAIL'}] {c['id']}" + (f"  {c['error']}" if c["error"] else ""))
        print(f"{o['passed']}/{o['total']} cases pass. {o['statement']}")
    return _run(args, run_logic_benchmark, text)


def cmd_benchmark_gate_l_synthetic(args: argparse.Namespace) -> int:
    from research_harness.benchmarks.gate_l import run_synthetic_gate_l

    def text(o):
        r = o["report"]
        print("  ".join(f"{k}={'ok' if r[k]['passed'] else 'NO'}" for k in ("L1", "L2", "L3", "L4", "L5")))
        print(o["statement"])
    return _run(args, run_synthetic_gate_l, text)


def cmd_gate_k_form(args: argparse.Namespace) -> int:
    from research_harness.benchmarks.gate_k import blank_annotation_form

    def go():
        form = blank_annotation_form(_ids(args.sources), args.package)
        Path(args.out).write_text(json.dumps(form, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"form": str(args.out), "elements": [e["id"] for e in form["elements"]]}
    return _run(args, go, lambda o: print(f"blank form written to {o['form']} ({len(o['elements'])} elements). Fill it before looking at any system output."))


def cmd_gate_k_annotate(args: argparse.Namespace) -> int:
    from research_harness.benchmarks.gate_k import record_reference_annotation
    return _run(args, lambda: record_reference_annotation(_store(args), _helpers()._resolve_token(args), _json_arg(args.file, "--file"),
                                                          annotation_id=args.id), lambda o: print(f"{o['annotation_id']} stored (sha256 {o['sha256'][:16]})"))


def cmd_gate_k_blindness(args: argparse.Namespace) -> int:
    from research_harness.benchmarks.gate_k import blindness
    return _run(args, lambda: blindness(_store(args), args.annotation),
                lambda o: print("blind" if o["blind"] else f"NOT BLIND: system output existed before the annotation: {', '.join(o['system_outputs_before_annotation'])}"))


def cmd_gate_k_sheet(args: argparse.Namespace) -> int:
    from research_harness.benchmarks.gate_k import comparison_sheet

    def go():
        sheet = comparison_sheet(_store(args), args.annotation, args.playbook, args.plan)
        if args.out:
            Path(args.out).write_text(json.dumps(sheet, indent=2, ensure_ascii=False), encoding="utf-8")
        return sheet
    return _run(args, go)


def cmd_gate_k_score(args: argparse.Namespace) -> int:
    from research_harness.benchmarks.gate_k import record_scores
    return _run(args, lambda: record_scores(_store(args), _helpers()._resolve_token(args), args.annotation, args.playbook, args.plan, _json_arg(args.file, "--file"),
                                            scorer=args.scorer, scores_id=args.id), lambda o: print(f"{o['scores_id']} stored"))


def cmd_gate_k_evaluate(args: argparse.Namespace) -> int:
    from research_harness.benchmarks.gate_k import evaluate_gate_k

    def text(o):
        print("  ".join(f"{k}={'ok' if o[k]['passed'] else 'NO'}" for k in ("K1", "K2", "K3", "K4")) + f"  -> {'PASSED' if o['passed'] else 'NOT PASSED'}")
    return _run(args, lambda: evaluate_gate_k(_store(args), args.scores, package_id=args.package, real_pilot=args.real), text)


def cmd_gate_k_export(args: argparse.Namespace) -> int:
    from research_harness.benchmarks.gate_k import export_artifacts
    return _run(args, lambda: export_artifacts(_store(args), args.scores, args.out, package_id=args.package, real_pilot=args.real),
                lambda o: print(f"artefacts written to {args.out}; gate K {'passed' if o['passed'] else 'not passed'}"))


def cmd_gate_validate(args: argparse.Namespace) -> int:
    from research_harness.benchmarks import gate_k, gate_l
    fn = gate_k.validate_artifacts if args.gate == "K" else gate_l.validate_artifacts

    def text(o):
        print("OK" if o["ok"] else "NOT OK")
        for p in o["problems"]:
            print(f"  - {p}")
    res: dict[str, Any] = {}
    rc = _run(args, lambda: res.update(fn(args.directory)) or res, text)
    return rc if rc else (0 if res.get("ok") else 1)


def register_m4(sub: Any) -> None:
    p = sub.add_parser("benchmark", help="Benchmarks that run without a model")
    s = p.add_subparsers(dest="benchmark_cmd", required=True)
    q = s.add_parser("logic", help="Synthetic logic benchmark: the software follows its designed logic (not evidence of usefulness)")
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_benchmark_logic)
    q = s.add_parser("gate-l-synthetic", help="The Gate L procedure on invented material (never passes Gate L)")
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_benchmark_gate_l_synthetic)

    p = sub.add_parser("gate-k", help="Gate K: method fidelity against a blind reference annotation")
    s = p.add_subparsers(dest="gate_k_cmd", required=True)
    q = s.add_parser("form", help="Write the blank annotation form")
    q.add_argument("--sources", required=True)
    q.add_argument("--package")
    q.add_argument("--out", required=True)
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_gate_k_form)
    q = s.add_parser("annotate", help="Store the filled form (author or owner token): before any system output")
    q.add_argument("--file", required=True)
    q.add_argument("--id", default="REFANN_PILOT")
    _common(q, library=False)
    q.set_defaults(func=cmd_gate_k_annotate)
    q = s.add_parser("blindness")
    q.add_argument("annotation")
    _common(q, library=False, token=False)
    q.set_defaults(func=cmd_gate_k_blindness)
    q = s.add_parser("sheet", help="Reference and system side by side, scores left empty")
    q.add_argument("--annotation", required=True)
    q.add_argument("--playbook", required=True)
    q.add_argument("--plan", required=True)
    q.add_argument("--out")
    _common(q, library=False, token=False)
    q.set_defaults(func=cmd_gate_k_sheet)
    q = s.add_parser("score", help="Store your element scores (author or owner token)")
    q.add_argument("--annotation", required=True)
    q.add_argument("--playbook", required=True)
    q.add_argument("--plan", required=True)
    q.add_argument("--file", required=True, help="JSON list: [{element, score, note}]")
    q.add_argument("--scorer", required=True)
    q.add_argument("--id", default="GATEK_SCORES_PILOT")
    _common(q, library=False)
    q.set_defaults(func=cmd_gate_k_score)
    q = s.add_parser("evaluate")
    q.add_argument("--scores", required=True)
    q.add_argument("--package")
    q.add_argument("--real", action="store_true", help="Mark the evaluation as the real pilot")
    _common(q, library=False, token=False)
    q.set_defaults(func=cmd_gate_k_evaluate)
    q = s.add_parser("export", help="Write the artefact directory that the acceptance test checks")
    q.add_argument("--scores", required=True)
    q.add_argument("--package")
    q.add_argument("--out", required=True)
    q.add_argument("--real", action="store_true")
    _common(q, library=False, token=False)
    q.set_defaults(func=cmd_gate_k_export)

    q = sub.add_parser("gate-validate", help="Check an exported Gate K or Gate L artefact directory")
    q.add_argument("gate", choices=["K", "L"])
    q.add_argument("directory")
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_gate_validate)


def cmd_library_export(args: argparse.Namespace) -> int:
    from research_harness.knowledge import publish
    return _run(args, lambda: publish.export_bundle(_lib(args), args.pin, args.out),
                lambda o: print(f"bundle written to {args.out}: {o['objects']} object(s), {o['artifacts']} file(s), closure {o['closure_digest'][:16]}"))


def cmd_library_import(args: argparse.Namespace) -> int:
    from research_harness.knowledge import publish
    cli = _helpers()

    def go():
        target = cli._resolve_store(cli._resolve_project_id(args), args.home) if getattr(args, "project", None) else _lib(args)
        return publish.import_bundle(target, cli._resolve_token(args), args.bundle)
    return _run(args, go, lambda o: print(f"{o['status']}: {o['objects_imported']} object(s) imported, {o['objects_already_present']} already present"))


def cmd_library_release_notes(args: argparse.Namespace) -> int:
    from research_harness.knowledge import publish

    def go():
        notes = publish.release_notes(_lib(args), args.pin)
        return {**notes, "markdown": publish.render_notes(notes)} if args.json else notes

    def text(o):
        print(publish.render_notes(o))
    return _run(args, go, text)


def register_publish(sub: Any) -> None:
    lib = sub.choices["library"]._subparsers._group_actions[0]
    q = lib.add_parser("export", help="Write an item with its whole closure to a new directory (a portable bundle)")
    q.add_argument("--pin", required=True)
    q.add_argument("--out", required=True)
    _common(q, project=False, library=False, token=False)
    q.set_defaults(func=cmd_library_export)
    q = lib.add_parser("import", help="Verify a bundle completely, then import it in one transaction (into the library, or a project with --project)")
    q.add_argument("--bundle", required=True)
    _common(q, library=False)
    q.set_defaults(func=cmd_library_import)
    q = lib.add_parser("release-notes", help="Each dimension, per context, as recorded now")
    q.add_argument("--pin", required=True)
    _common(q, project=False, library=False, token=False)
    q.set_defaults(func=cmd_library_release_notes)


def _add_provider_options(sub: Any) -> None:
    """Every command that takes --model also takes --provider and --base-url (the API key is only ever read from RESEARCH_API_KEY)."""
    seen: set[int] = set()

    def walk(parser: Any) -> None:
        if id(parser) in seen:
            return
        seen.add(id(parser))
        if "--model" in parser._option_string_actions and "--provider" not in parser._option_string_actions:
            parser.add_argument("--provider", choices=["anthropic", "openai-compatible"], help="Model provider (default: anthropic, or RESEARCH_PROVIDER)")
            parser.add_argument("--base-url", dest="base_url", help="Base URL of an OpenAI-compatible API, e.g. https://api.deepseek.com (or RESEARCH_BASE_URL)")
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for child in action.choices.values():
                    walk(child)
    for parser in sub.choices.values():
        walk(parser)


def cmd_adjudicate_run_pending(args: argparse.Namespace) -> int:
    from research_harness.workflows import eca

    def text(o):
        print(f"{o['adjudicated']} adjudicated ({o['model_calls']} model call(s)); verdicts {o['verdicts']}; guard fired {o['guard_fired']}; "
              f"skipped {len(o['skipped'])}, failed {len(o['failed'])}, left over {o['remaining_after_limit']}")
    return _run(args, lambda: eca.run_pending(_store(args), _helpers()._resolve_token(args), _adapter(args), rate=args.rate, limit=args.limit), text)


def cmd_adjudicate_sheet(args: argparse.Namespace) -> int:
    from research_harness.workflows import eca

    def go():
        rows = eca.review_sheet(_store(args))
        if args.out:
            Path(args.out).write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
        return rows

    def text(rows):
        print(f"{len(rows)} proposal(s) await your decision" + (f"; written to {args.out}" if args.out else ""))
        for r in rows:
            print(f"  {'!' if r['needs_attention'] else ' '} {r['adjudication_id']}  {r['model']['verdict']:<24} {r['statement']['object_id']}#{r['statement']['element']}: {r['statement']['text'][:70]}")
    return _run(args, go, text)


def cmd_adjudicate_confirm_batch(args: argparse.Namespace) -> int:
    from research_harness.workflows import eca
    return _run(args, lambda: eca.confirm_batch(_store(args), _helpers()._resolve_token(args), _json_arg(args.file, "--file") or []),
                lambda o: print(f"{len(o['confirmed'])} confirmed, {len(o['failed'])} not applied" + "".join(f"\n  - {f['adjudication_id']}: {f['error']}" for f in o["failed"])))


def register_review(sub: Any) -> None:
    grp = sub.choices["adjudicate"]._subparsers._group_actions[0]
    q = grp.add_parser("run-pending", help="Adjudicate everything the coverage policy still asks for (proposals only; one model call each)")
    q.add_argument("--rate", type=float, default=0.2)
    q.add_argument("--limit", type=int)
    q.add_argument("--model")
    _common(q)
    q.set_defaults(func=cmd_adjudicate_run_pending)
    q = grp.add_parser("sheet", help="Every high-impact proposal awaiting your decision, with what you need to decide it; decisions left empty")
    q.add_argument("--out")
    _common(q, token=False)
    q.set_defaults(func=cmd_adjudicate_sheet)
    q = grp.add_parser("confirm-batch", help="Apply your decisions from one file: [{adjudication_id, verdict, rationale}]; there is no default verdict")
    q.add_argument("--file", required=True)
    _common(q)
    q.set_defaults(func=cmd_adjudicate_confirm_batch)
