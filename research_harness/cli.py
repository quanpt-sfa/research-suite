"""Command-line interface for Research Harness 9.0-dev.

Provides subcommands for project administration, snapshot exchange, proposal validation/import,
peer review, dependency analysis, integrity audit, and release gating.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

from research_harness import PROTOCOL_VERSION, SCHEMA_VERSION, __version__
from research_harness.core.auth import Actor, authenticate, new_token, token_hash, validate_roles
from research_harness.core.errors import AuthorizationError, HarnessError, ProposalRejection
from research_harness.core.model import Decision, IntegrityStatus, ReleaseEligibility
from research_harness.graph.dependency import traverse
from research_harness.legacy.adapter import migrate_legacy_project
from research_harness.proposals.gateway import import_proposal, static_check
from research_harness.protocols.schema import export_schemas
from research_harness.protocols.snapshot import export_snapshot, verify_snapshot_document
from research_harness.state.actors import _UNSET, disable_actor, register_actor, update_actor
from research_harness.state.schema import upgrade_schema
from research_harness.state.store import StateStore, default_home
from research_harness.validation.integrity import audit_project_integrity, release_check
from research_harness.validation.project_status import get_object_history, get_project_status
from research_harness.validation.review import record_review



def _json_out(data: Any, pretty: bool = True) -> None:
    if pretty:
        print(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, default=str))
    else:
        print(json.dumps(data, sort_keys=True, ensure_ascii=False, default=str))


def _resolve_home(arg_home: Optional[str]) -> Path:
    if arg_home:
        return Path(arg_home).resolve()
    return default_home()


def _resolve_token(args: argparse.Namespace) -> Optional[str]:
    tok = getattr(args, "token", None)
    if tok:
        return tok.strip()
    tok_file = getattr(args, "token_file", None)
    if tok_file:
        return Path(tok_file).read_text(encoding="utf-8").strip()
    env_tok = os.environ.get("RESEARCH_HARNESS_TOKEN")
    if env_tok:
        return env_tok.strip()
    return None


def _resolve_store(project_id: str, arg_home: Optional[str]) -> StateStore:
    home = _resolve_home(arg_home)
    return StateStore.open(home, project_id)


# ---------------------------------------------------------------------------
# Project commands
# ---------------------------------------------------------------------------

def cmd_project_init(args: argparse.Namespace) -> int:
    home = _resolve_home(args.home)
    owner = args.owner or "owner"
    try:
        store, token = StateStore.create(home, args.project_id, owner_id=owner)
    except HarnessError as e:
        sys.stderr.write(f"Error: {e.rule} - {e.message}\n")
        return 1

    res = {
        "project_id": args.project_id,
        "home": str(home),
        "project_root": str(store.paths.root),
        "owner_id": owner,
        "owner_token": token,
        "status": "INITIALIZED",
        "engine_version": __version__,
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": SCHEMA_VERSION,
    }
    if args.json:
        _json_out(res)
    else:
        print(f"Project '{args.project_id}' initialized successfully at {store.paths.root}")
        print(f"Owner: {owner}")
        print(f"Owner Token: {token}")
        print("IMPORTANT: Save this owner token. It grants administrative access and cannot be retrieved later.")
    return 0


def cmd_project_status(args: argparse.Namespace) -> int:
    try:
        store = _resolve_store(args.project_id, args.home)
        status = get_project_status(store)
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1

    if args.json:
        _json_out(status)
    else:
        print(f"Project: {status['project_id']} (State Revision: {status['state_revision']})")
        print(f"State SHA-256: {status['state_sha256']}")
        print(f"Active Objects: {status['active_objects_by_type']}")
        print(f"Head Reviews: {status['head_review_statuses']}")
        print(f"Open Invalidations: {status['open_invalidations']}")
        print(f"Events: {status['event_count']}, Proposals Accepted: {status['proposals_accepted']}, Snapshots: {status['snapshots_exported']}")
    return 0


def cmd_project_audit(args: argparse.Namespace) -> int:
    try:
        store = _resolve_store(args.project_id, args.home)
        audit = audit_project_integrity(store)
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1

    if args.json:
        _json_out(audit)
    else:
        print(f"Project Integrity: {audit['status']}")
        if audit["findings"]:
            print("Findings:")
            for f in audit["findings"]:
                print(f"  - [{f.get('rule')}] {f.get('message', '')} {f}")
        else:
            print("All cryptographic hashes, foreign keys, event chains, and artifacts verified intact.")
    return 0 if audit["status"] == IntegrityStatus.PASS.value else 1


def cmd_project_actor_add(args: argparse.Namespace) -> int:
    token = _resolve_token(args)
    if not token:
        sys.stderr.write("Error: owner token required (--token, --token-file, or RESEARCH_HARNESS_TOKEN)\n")
        return 1
    roles_list = [r.strip() for r in args.roles.split(",") if r.strip()]
    try:
        store = _resolve_store(args.project_id, args.home)
        res = register_actor(
            store,
            token,
            actor_id=args.actor_id,
            roles=roles_list,
            display_name=args.display_name,
            independence_group=args.independence_group,
        )
    except (HarnessError, AuthorizationError) as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1

    out = {
        "project_id": args.project_id,
        "actor_id": res["actor_id"],
        "roles": res["roles"],
        "independence_group": res["independence_group"],
        "display_name": res["display_name"],
        "created_by": res["created_by"],
        "token": res["token"],
    }
    if args.json:
        _json_out(out)
    else:
        print(f"Actor '{res['actor_id']}' added with roles {res['roles']}")
        print(f"Token: {res['token']}")
        print("IMPORTANT: Save this token. It grants access and cannot be retrieved later.")
    return 0


def cmd_project_actor_update(args: argparse.Namespace) -> int:
    token = _resolve_token(args)
    if not token:
        sys.stderr.write("Error: owner token required (--token, --token-file, or RESEARCH_HARNESS_TOKEN)\n")
        return 1
    kwargs = {}
    if args.roles is not None:
        kwargs["roles"] = [r.strip() for r in args.roles.split(",") if r.strip()]
    if args.display_name is not None:
        kwargs["display_name"] = args.display_name
    if args.independence_group is not None:
        kwargs["independence_group"] = args.independence_group
    try:
        store = _resolve_store(args.project_id, args.home)
        res = update_actor(store, token, actor_id=args.actor_id, **kwargs)
    except (HarnessError, AuthorizationError) as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1

    out = {
        "project_id": args.project_id,
        "actor_id": res["actor_id"],
        "changed": res["changed"],
        "roles": res["roles"],
        "independence_group": res["independence_group"],
        "updated_by": res["updated_by"],
    }
    if args.json:
        _json_out(out)
    else:
        print(f"Actor '{res['actor_id']}' updated (changed: {res['changed']})")
    return 0


def cmd_project_actor_disable(args: argparse.Namespace) -> int:
    token = _resolve_token(args)
    if not token:
        sys.stderr.write("Error: owner token required (--token, --token-file, or RESEARCH_HARNESS_TOKEN)\n")
        return 1
    try:
        store = _resolve_store(args.project_id, args.home)
        res = disable_actor(store, token, actor_id=args.actor_id)
    except (HarnessError, AuthorizationError) as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1

    out = {
        "project_id": args.project_id,
        "actor_id": res["actor_id"],
        "disabled_by": res["disabled_by"],
    }
    if args.json:
        _json_out(out)
    else:
        print(f"Actor '{res['actor_id']}' disabled successfully.")
    return 0


def cmd_project_upgrade_schema(args: argparse.Namespace) -> int:
    token = _resolve_token(args)
    if not token:
        sys.stderr.write("Error: owner token required (--token, --token-file, or RESEARCH_HARNESS_TOKEN)\n")
        return 1
    try:
        store = _resolve_store(args.project_id, args.home)
        res = upgrade_schema(store, token)
    except (HarnessError, AuthorizationError) as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1

    if args.json:
        _json_out(res)
    else:
        print(f"Schema upgrade: {res.get('status')} - {res.get('message', res)}")
    return 0



def cmd_project_migrate_v8(args: argparse.Namespace) -> int:
    token = _resolve_token(args)
    if not token:
        sys.stderr.write("Error: Actor token required (--token, --token-file, or RESEARCH_HARNESS_TOKEN)\n")
        return 1
    try:
        store = _resolve_store(args.project_id, args.home)
        with store.read() as conn:
            actor = authenticate(conn, token)
        if not actor or not actor.has_role("owner", "importer"):
            sys.stderr.write("Error: Actor token required with owner or importer role for migration.\n")
            return 1

        res = migrate_legacy_project(
            args.legacy_root,
            store,
            actor=actor,
            token=token,
            dry_run=args.dry_run,
        )
    except Exception as e:
        sys.stderr.write(f"Migration failed: {e}\n")
        return 1

    if args.json:
        _json_out(res)
    else:
        print(f"Migration Status: {res.get('decision')}")
        print(f"Created objects: {len(res.get('created_objects', []))}")
        print(f"Created edges: {res.get('created_edges', 0)}")
        if res.get("backup_zip"):
            print(f"Legacy backup created at: {res['backup_zip']}")
        if res.get("notes"):
            for n in res["notes"]:
                print(f"  * {n}")
    return 0


def cmd_project_recover(args: argparse.Namespace) -> int:
    try:
        store = _resolve_store(args.project_id, args.home)
        res = store.recover()
    except Exception as e:
        sys.stderr.write(f"Recovery failed: {e}\n")
        return 1

    if args.json:
        _json_out(res)
    else:
        print(f"Project '{args.project_id}' recovery completed.")
        print(f"Stale staging files removed: {res['stale_staging_removed']}")
        print(f"Orphan artifacts detected: {len(res['orphan_artifacts'])}")
        if res["orphan_artifacts"]:
            for o in res["orphan_artifacts"]:
                print(f"  - {o}")
    return 0


# ---------------------------------------------------------------------------
# Snapshot commands
# ---------------------------------------------------------------------------

def cmd_snapshot_export(args: argparse.Namespace) -> int:
    token = _resolve_token(args)
    if not token:
        sys.stderr.write("Error: Actor token required (--token, --token-file, or RESEARCH_HARNESS_TOKEN)\n")
        return 1
    try:
        store = _resolve_store(args.project_id, args.home)
        with store.read() as conn:
            actor = authenticate(conn, token)
        if not actor:
            sys.stderr.write("Error: Invalid or disabled actor token.\n")
            return 1
        snapshot = export_snapshot(store, actor, args.scope, token=token)
    except Exception as e:
        sys.stderr.write(f"Snapshot export failed: {e}\n")
        return 1

    if args.output:
        out_path = Path(args.output).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        if not args.json:
            print(f"Snapshot {snapshot['snapshot_id']} exported to {out_path}")
    if args.json or not args.output:
        _json_out(snapshot)
    return 0


def cmd_snapshot_verify(args: argparse.Namespace) -> int:
    try:
        store = _resolve_store(args.project_id, args.home)
        content = Path(args.snapshot).read_bytes()
        res = verify_snapshot_document(store, content)
    except Exception as e:
        sys.stderr.write(f"Verification failed: {e}\n")
        return 1

    if args.json:
        _json_out(res)
    else:
        print(f"Snapshot Verification: {res['status']}")
        if res["findings"]:
            print("Findings:")
            for f in res["findings"]:
                print(f"  - [{f.get('rule')}] {f.get('message', '')}")
        else:
            print("Cryptographic integrity, HMAC authenticity, and project registry check PASSED.")
    return 0 if res["status"] == "PASS" else 1


# ---------------------------------------------------------------------------
# Proposal commands
# ---------------------------------------------------------------------------

def cmd_proposal_validate(args: argparse.Namespace) -> int:
    try:
        content = Path(args.proposal).read_bytes()
        doc = static_check(content)
        res = {
            "status": "VALID",
            "proposal_id": doc.get("proposal_id"),
            "protocol_version": doc.get("protocol_version"),
            "project_id": doc.get("project_id"),
            "operations_count": len(doc.get("operations", [])),
        }
        if args.json:
            _json_out(res)
        else:
            print(f"Proposal {doc.get('proposal_id')} is structurally VALID.")
            print(f"Target Project: {doc.get('project_id')}")
            print(f"Operations: {len(doc.get('operations', []))}")
        return 0
    except ProposalRejection as r:
        res = {
            "status": "INVALID",
            "decision": str(r.decision),
            "rule": r.rule,
            "message": r.message,
            "details": r.details,
        }
        if args.json:
            _json_out(res)
        else:
            print(f"Proposal Validation FAILED: [{r.rule}] {r.message}")
        return 1


def cmd_proposal_import(args: argparse.Namespace) -> int:
    token = _resolve_token(args)
    if not token:
        sys.stderr.write("Error: Actor token required (--token, --token-file, or RESEARCH_HARNESS_TOKEN)\n")
        return 1
    try:
        store = _resolve_store(args.project_id, args.home)
        content = Path(args.proposal).read_bytes()
        receipt = import_proposal(store, content, token, dry_run=args.dry_run)
    except Exception as e:
        sys.stderr.write(f"Import execution error: {e}\n")
        return 1

    if args.json:
        _json_out(receipt)
    else:
        print(f"Import Decision: {receipt.get('decision')}")
        if receipt.get("rule"):
            print(f"Rule: {receipt.get('rule')}")
        if receipt.get("message"):
            print(f"Message: {receipt.get('message')}")
        if receipt.get("new_state_revision"):
            print(f"New State Revision: {receipt.get('new_state_revision')}")
            print(f"State SHA-256: {receipt.get('state_sha256')}")
        if receipt.get("created_objects"):
            print(f"Created Objects: {receipt.get('created_objects')}")
        if receipt.get("invalidated_objects"):
            print(f"Invalidated Objects: {receipt.get('invalidated_objects')}")
        if receipt.get("conflicts"):
            print(f"Conflicts: {receipt.get('conflicts')}")

    accepted = receipt.get("decision") in (Decision.ACCEPTED.value, Decision.DUPLICATE.value)
    return 0 if accepted else 1


# ---------------------------------------------------------------------------
# Review commands
# ---------------------------------------------------------------------------

def cmd_review_record(args: argparse.Namespace) -> int:
    token = _resolve_token(args)
    if not token:
        sys.stderr.write("Error: Reviewer token required (--token, --token-file, or RESEARCH_HARNESS_TOKEN)\n")
        return 1
    try:
        store = _resolve_store(args.project_id, args.home)
        result = record_review(
            store,
            token,
            object_id=args.object_id,
            revision=args.revision,
            kind=args.kind,
            decision=args.decision,
            rationale=args.rationale or "",
            acknowledge_invalidations=args.acknowledge_invalidations,
        )
    except Exception as e:
        sys.stderr.write(f"Review error: {e}\n")
        return 1

    if args.json:
        _json_out(result)
    else:
        print(f"Review Decision: {result.get('decision')}")
        if result.get("approval_id"):
            print(f"Approval ID: {result.get('approval_id')}")
            print(f"Subject: {result.get('object_id')} rev {result.get('revision')}")
            print(f"New Status: {result.get('new_review_status')}")
            print(f"New State Revision: {result.get('new_state_revision')}")
        else:
            print(f"Refusal Rule: {result.get('rule')}")
            print(f"Message: {result.get('message')}")
            if result.get("problems"):
                print(f"Problems: {result.get('problems')}")
            if result.get("open_invalidations"):
                print(f"Open Invalidations: {result.get('open_invalidations')}")

    return 0 if result.get("decision") == "RECORDED" else 1


# ---------------------------------------------------------------------------
# Dependency impact analysis
# ---------------------------------------------------------------------------

def cmd_dependency_impact(args: argparse.Namespace) -> int:
    try:
        store = _resolve_store(args.project_id, args.home)
        with store.read() as conn:
            head = conn.execute("SELECT head_revision FROM object_heads WHERE object_id=?", (args.object_id,)).fetchone()
            if not head:
                sys.stderr.write(f"Error: Object '{args.object_id}' not found.\n")
                return 1
            rev = head[0]
            affected = traverse(conn, [(args.object_id, rev)])
    except Exception as e:
        sys.stderr.write(f"Impact calculation error: {e}\n")
        return 1

    res = {
        "target_object_id": args.object_id,
        "target_head_revision": rev,
        "impacted_dependents_count": len(affected),
        "impacted_dependents": affected,
    }
    if args.json:
        _json_out(res)
    else:
        print(f"Impact analysis for {args.object_id}@{rev}:")
        print(f"Total downstream head dependents affected: {len(affected)}")
        for a in affected:
            print(f"  - {a['object_id']}@{a['revision']} (hops: {a['hops']}, via: {a['via_relation']})")
    return 0


# ---------------------------------------------------------------------------
# History command
# ---------------------------------------------------------------------------

def cmd_history(args: argparse.Namespace) -> int:
    try:
        store = _resolve_store(args.project_id, args.home)
        hist = get_object_history(store, args.object_id)
    except Exception as e:
        sys.stderr.write(f"History query error: {e}\n")
        return 1

    if args.json:
        _json_out(hist)
    else:
        if not hist.get("found"):
            print(f"Object '{args.object_id}' not found.")
            return 1
        print(f"Object: {hist['object_id']} (Type: {hist['object_type']}, Head Revision: {hist['head_revision']})")
        print(f"Lifecycle: {hist['lifecycle_status']}")
        print("Revisions:")
        for r in hist["revisions"]:
            print(f"  rev {r['revision']}: status={r['scientific_review_status']}, submitted_by={r['submitted_by']}, digest={r['content_digest'][:12]}...")
            if r["edges_out"]:
                for e in r["edges_out"]:
                    print(f"    --[{e['relation']}]--> {e['target_id']}@{e['target_revision']}")
        if hist["status_timeline"]:
            print("Status Timeline:")
            for s in hist["status_timeline"]:
                print(f"  seq {s['seq']}: rev {s['revision']} {s['dimension']}: {s['old_value']} -> {s['new_value']} ({s['cause'].get('kind', '')})")
        if hist["invalidations"]:
            print("Invalidations:")
            for inv in hist["invalidations"]:
                print(f"  inval {inv['inval_id']}: rev {inv['revision']} caused by {inv['cause_object_id']} (status: {inv['status']})")
    return 0


# ---------------------------------------------------------------------------
# Release check command
# ---------------------------------------------------------------------------

def cmd_release_check(args: argparse.Namespace) -> int:
    try:
        store = _resolve_store(args.project_id, args.home)
        res = release_check(store)
    except Exception as e:
        sys.stderr.write(f"Release check error: {e}\n")
        return 1

    if args.json:
        _json_out(res)
    else:
        print(f"Release Check for '{res['project_id']}':")
        print(f"  Integrity:        {res['integrity_status']}")
        print(f"  Workflow:         {res['workflow_status']}")
        print(f"  Review:           {res['scientific_review_status']}")
        print(f"  Release Decision: {res['release_eligibility']}")
        if res["block_reasons"]:
            print("Block Reasons:")
            for b in res["block_reasons"]:
                print(f"  - {b}")
        else:
            print("Project is eligible for release.")

    return 0 if res["release_eligibility"] == ReleaseEligibility.ELIGIBLE.value else 1


# ---------------------------------------------------------------------------
# Schema command
# ---------------------------------------------------------------------------

def cmd_schema_export(args: argparse.Namespace) -> int:
    target_dir = Path(args.dir or "schemas").resolve()
    names = export_schemas(target_dir)
    if args.json:
        _json_out({"exported_count": len(names), "directory": str(target_dir), "schemas": names})
    else:
        print(f"Exported {len(names)} JSON schemas to {target_dir}:")
        for n in names:
            print(f"  - {n}")
    return 0


# ---------------------------------------------------------------------------
# Workflow commands
# ---------------------------------------------------------------------------

def _resolve_project_id(args: argparse.Namespace) -> str:
    pid = getattr(args, "project", None) or getattr(args, "project_id", None)
    if not pid:
        raise ValueError("Project identifier must be provided via --project or positional argument")
    return pid


def cmd_workflow_list(args: argparse.Namespace) -> int:
    from research_harness.workflows.registry import get_registry
    registry = get_registry()
    workflows = registry.list_workflows()
    if args.json:
        _json_out({"workflows": workflows, "total_count": len(workflows)})
    else:
        print(f"Registered Research Workflows ({len(workflows)} available):\n")
        for wf in workflows:
            print(f"  * {wf['name']} - {wf['title']}")
            print(f"    Description: {wf['description']}")
            print(f"    Inputs: {', '.join(wf['inputs'])}")
            print(f"    Outputs: {', '.join(wf['outputs'])}")
            print()
    return 0


def _runs_store(args: argparse.Namespace) -> StateStore:
    return _resolve_store(_resolve_project_id(args), getattr(args, "home", None))


def _load_domain_spec(ref: Optional[str]) -> Any:
    from research_harness.workflows.domain_engine import DomainWorkflowSpec, GENERIC_SPEC_ID, load_builtin_spec
    if ref and Path(ref).is_file():
        return DomainWorkflowSpec.from_dict(json.loads(Path(ref).read_text(encoding="utf-8")))
    return load_builtin_spec(ref or GENERIC_SPEC_ID)


def cmd_domain_list(args: argparse.Namespace) -> int:
    from research_harness.workflows.domain_engine import list_builtin_specs, load_builtin_spec
    specs = [load_builtin_spec(i) for i in list_builtin_specs()]
    if args.json:
        _json_out({"domain_workflows": [{"id": s.id, "version": s.version, "domain": s.domain, "subfield": s.subfield,
                                         "study_designs": list(s.study_designs), "validation_status": s.validation_status,
                                         "steps": len(s.steps)} for s in specs]})
    else:
        for s in specs:
            print(f"  {s.id} v{s.version}  [{s.domain}/{s.subfield or '-'}]  designs={list(s.study_designs) or 'any'}  "
                  f"steps={len(s.steps)}  status={s.validation_status}")
    return 0


def cmd_domain_next(args: argparse.Namespace) -> int:
    from research_harness.workflows.domain_engine import evaluate, facts_from_store, next_steps
    try:
        spec = _load_domain_spec(args.spec)
        facts = facts_from_store(_resolve_store(_resolve_project_id(args), getattr(args, "home", None)),
                                 research_question_id=getattr(args, "question", None),
                                 study_design=getattr(args, "study_design", None))
        evals, nxt = evaluate(spec, facts), next_steps(spec, facts)
    except Exception as e:
        sys.stderr.write(f"Domain workflow error: {e}\n")
        return 1
    if args.json:
        _json_out({"workflow": spec.pin, "study_design": facts.study_design, "next_steps": nxt,
                   "steps": [e.to_dict() for e in evals]})
        return 0
    print(f"{spec.id} v{spec.version}  study design: {facts.study_design or 'not recorded'}")
    print(f"Next: {', '.join(nxt) if nxt else '(nothing can run yet)'}\n")
    for e in evals:
        mark = "n/a " if e.applicability.value == "NOT_APPLICABLE" else "done" if e.done else "RUN " if e.runnable else "wait"
        extra = f"  REOPENED by {e.reopened_by['by']}: {e.reopened_by['reason']}" if e.reopened_by else ""
        print(f"  [{mark}] {e.stage:<12} {e.step_id}{extra}")
        for reason in e.blocked_by:
            print(f"           - {reason}")
    return 0


def cmd_workflow_runs(args: argparse.Namespace) -> int:
    from research_harness.workflows.runs import list_workflow_runs
    try:
        legacy = True if getattr(args, "legacy", False) else None
        rows = list_workflow_runs(_runs_store(args), workflow=getattr(args, "workflow", None),
                                  research_question_id=getattr(args, "question", None), legacy=legacy,
                                  limit=getattr(args, "limit", None))
    except Exception as e:
        sys.stderr.write(f"Workflow runs error: {e}\n")
        return 1
    if args.json:
        _json_out({"runs": rows, "total_count": len(rows)})
        return 0
    print(f"{len(rows)} workflow run(s), newest first:\n")
    for r in rows:
        tags = ("legacy " if r["legacy"] else "") + (f"reuse-of:{r['reused_from_run_id']} " if r["reused_from_run_id"] else "")
        print(f"  {r['run_id']}  {r['workflow']:<12} {r['legacy_status']:<22} "
              f"exec={r['execution_status']} ctx={r['context_status']} assess={r['assessment_status']}  {tags}".rstrip())
    return 0


def cmd_workflow_show(args: argparse.Namespace) -> int:
    from research_harness.workflows.runs import get_workflow_run, load_run_prompt, load_workflow_output
    try:
        store = _runs_store(args)
        row = get_workflow_run(store, args.run_id)
        prompt = load_run_prompt(store, args.run_id) if args.prompt else None
        output = load_workflow_output(store, args.run_id) if args.json else None
    except Exception as e:
        sys.stderr.write(f"Workflow show error: {e}\n")
        return 1
    if args.json:
        _json_out({"run": row, "output": output.to_dict() if output else None, "prompt": prompt})
        return 0
    print(f"Run {row['run_id']} ({row['workflow']}, {row['executor']}) at {row['created_at']}")
    print(f"  Status:      execution={row['execution_status']} context={row['context_status']} "
          f"assessment={row['assessment_status']} applicability={row['applicability']} ({row['legacy_status']})")
    print(f"  Question:    {row['research_question_id']}   Focus claims: {row['focus_claims']}")
    print(f"  Fingerprint: {row['input_fingerprint'][:16]}...   Skill digest: {(row['skill_digest'] or '')[:16]}")
    print(f"  Artifact:    {row['artifact_sha256']}")
    print(f"  Parent run:  {row['parent_run_id']}   Reused from: {row['reused_from_run_id']}   Upstream: {row['upstream_run_ids']}")
    if row["legacy"]:
        print(f"  LEGACY (9.1.1, unverified, never reused). Known defects: {', '.join(row['known_defects'])}")
    if args.prompt:
        if prompt is None:
            print("  (this run has no model request)")
        else:
            print(f"\n--- system prompt ---\n{prompt['system_prompt']}\n--- user prompt ---\n{prompt['user_prompt']}")
    return 0


def cmd_workflow_diff(args: argparse.Namespace) -> int:
    from research_harness.workflows.runs import diff_runs
    try:
        d = diff_runs(_runs_store(args), args.run_a, args.run_b)
    except Exception as e:
        sys.stderr.write(f"Workflow diff error: {e}\n")
        return 1
    if args.json:
        _json_out(d)
        return 0
    print(f"Diff {d['run_a']} -> {d['run_b']} ({d['workflow_a']} / {d['workflow_b']})")
    for sc in d["status_changes"]:
        print(f"  status  {sc['path']}: {sc.get('before')} -> {sc.get('after')}")
    for ic in d["input_changes"]:
        print(f"  input   {ic['object_id']}: rev {ic['before']} -> {ic['after']}")
    for rc in d["result_changes"]:
        print(f"  result  {rc['path']} ({rc['change']})")
    if not (d["status_changes"] or d["input_changes"] or d["result_changes"]):
        print("  (no differences)")
    return 0


def cmd_workflow_run(args: argparse.Namespace) -> int:
    from research_harness.workflows.dispatcher import run_workflow
    try:
        pid = _resolve_project_id(args)
        store = _resolve_store(pid, args.home)
        claims = args.claims.split(",") if getattr(args, "claims", None) else None
        budget = getattr(args, "budget", None) or 12000
        output = run_workflow(
            store,
            args.workflow,
            research_question_id=getattr(args, "question", None),
            focus_claims=claims,
            max_input_tokens=budget,
            discipline_profile=getattr(args, "profile", None),
            reuse_cached=not getattr(args, "no_cache", False),
        )
    except Exception as e:
        sys.stderr.write(f"Workflow execution error: {e}\n")
        return 1

    if args.json:
        _json_out(output.to_dict())
    else:
        print(f"Workflow: {output.workflow} [Status: {output.status}]")
        print(f"Task ID: {output.task_id} (Project: {output.project_id})")
        print(f"Usage: {output.usage.input_tokens or 0} in / {output.usage.output_tokens or 0} out tokens, {output.usage.reused_artifacts} reused artifact(s), {output.usage.elapsed_ms}ms")
        print("\nSummary of Results:")
        for k, v in output.result.items():
            if isinstance(v, list):
                print(f"  - {k}: {len(v)} item(s)")
            elif isinstance(v, (str, int, float, bool)):
                print(f"  - {k}: {v}")
    return 0


def cmd_context_preview(args: argparse.Namespace) -> int:
    from research_harness.workflows.context import build_context
    try:
        pid = _resolve_project_id(args)
        store = _resolve_store(pid, args.home)
        claims = args.claims.split(",") if getattr(args, "claims", None) else None
        budget = getattr(args, "budget", None) or 12000
        ctx = build_context(
            store,
            args.workflow,
            research_question_id=getattr(args, "question", None),
            focus_claims=claims,
            max_input_tokens=budget,
            fulltext_policy=getattr(args, "fulltext", "on_demand"),
            discipline_profile=getattr(args, "profile", None),
        )
    except Exception as e:
        sys.stderr.write(f"Context preview error: {e}\n")
        return 1

    if args.json:
        _json_out(ctx.to_dict())
    else:
        m = ctx.manifest
        print(f"Context Preview for Workflow '{ctx.workflow}' in '{ctx.project_id}':")
        print(f"  Budget Limit:           {m['budget_limit']} tokens")
        print(f"  Estimated Tokens:       {m['estimated_tokens']} (estimated: {m['is_estimate']})")
        print(f"  Context Payload Size:   {m['context_bytes']} bytes")
        print(f"  Budget Status:          {m['budget_status']} (coverage {m.get('coverage')}, context {m.get('context_status')})")
        print(f"  Request Tokens:         {m.get('request_tokens')} of {m['budget_limit']} ({m.get('token_count_basis')}), system prompt {m.get('system_prompt_tokens')}")
        print(f"  Full Texts Deferred:    {m.get('fulltext_deferred_count', 0)}")
        print(f"  Included Objects:       {m['included_objects_count']} {m['included_by_type']}")
        print(f"  Deduplicated Excerpts:  {m['deduped_excerpts_count']}")
        print(f"  Full-Text Expansions:   {m['fulltext_expanded_count']}")
        print(f"  Contradictory Evidence: {m['contradictory_evidence_count']}")
        print(f"  Omitted Objects:        {m['omitted_objects_count']}")
        if ctx.continuation_plan:
            print(f"  Continuation Plan:      {ctx.continuation_plan.get('recommendation')}")
    return 0


def cmd_usage_report(args: argparse.Namespace) -> int:
    from research_harness.workflows.instrumentation import UsageTracker
    try:
        pid = _resolve_project_id(args)
        store = _resolve_store(pid, args.home)
        tracker = UsageTracker(store)
        summary = tracker.get_summary(workflow=getattr(args, "workflow", None))
        from research_harness.benchmarks.gate_l import effort_from_registry
        summary["registry"] = effort_from_registry(store)   # runs of the knowledge layer (extraction, adjudication, execution) are registered, not tracked
    except Exception as e:
        sys.stderr.write(f"Usage report error: {e}\n")
        return 1

    if args.json:
        _json_out(summary)
    else:
        print(f"Usage Report for Project '{summary['project_id']}':")
        print(f"  Total Tasks Recorded:   {summary['tasks_recorded']}")
        print(f"  Total Input Tokens:     {summary['total_input_tokens']}")
        print(f"  Total Output Tokens:    {summary['total_output_tokens']}")
        print(f"  Total Cached Reads:     {summary['total_cache_read_tokens']}")
        print(f"  Total Reused Artifacts: {summary['total_reused_artifacts']}")
        print(f"  Total Model Calls:      {summary['total_llm_calls']}")
        print(f"  Total Elapsed Time:     {summary['total_elapsed_ms']}ms")
        r = summary["registry"]
        print(f"  Run registry: {r['registry_runs']} run(s), {r['model_calls']} model call(s), {r['reused_runs']} reused, {r['commands']} command(s); tokens "
              f"{r['tokens']['input']} in / {r['tokens']['output']} out (basis: {', '.join(f'{k}={v}' for k, v in r['tokens']['basis'].items()) or 'none reported'})")
        if summary.get("by_workflow"):
            print("  Breakdown by Workflow:")
            for wf, d in summary["by_workflow"].items():
                print(f"    - {wf}: {d['tasks_count']} tasks, {d['input_tokens']} in / {d['output_tokens']} out, {d['reused_artifacts']} reused")
    return 0


# ---------------------------------------------------------------------------
# CLI Argument Parser Setup
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research",
        description="Research Harness 9.0-dev: Versioned Research Core and Proposal Gateway",
    )
    parser.add_argument("--version", action="version", version=f"research {__version__} (protocol {PROTOCOL_VERSION})")

    sub = parser.add_subparsers(dest="command", required=True)

    # project
    p_proj = sub.add_parser("project", help="Manage research projects")
    s_proj = p_proj.add_subparsers(dest="project_cmd", required=True)

    p_init = s_proj.add_parser("init", help="Initialize a new research project")
    p_init.add_argument("project_id", help="Unique project identifier (e.g. PRJ_2026_ALPHA)")
    p_init.add_argument("--home", help="Base directory for research projects")
    p_init.add_argument("--owner", "--owner-id", dest="owner", help="Owner actor identifier (default: owner)")
    p_init.add_argument("--json", action="store_true", help="Output JSON")
    p_init.set_defaults(func=cmd_project_init)

    p_stat = s_proj.add_parser("status", help="Display project status")
    p_stat.add_argument("project_id", help="Project identifier")
    p_stat.add_argument("--home", help="Base directory for research projects")
    p_stat.add_argument("--json", action="store_true", help="Output JSON")
    p_stat.set_defaults(func=cmd_project_status)

    p_aud = s_proj.add_parser("audit", help="Run cryptographic integrity audit")
    p_aud.add_argument("project_id", help="Project identifier")
    p_aud.add_argument("--home", help="Base directory for research projects")
    p_aud.add_argument("--json", action="store_true", help="Output JSON")
    p_aud.set_defaults(func=cmd_project_audit)

    p_rec = s_proj.add_parser("recover", help="Recover project state after abnormal termination")
    p_rec.add_argument("project_id", help="Project identifier")
    p_rec.add_argument("--home", help="Base directory for research projects")
    p_rec.add_argument("--json", action="store_true", help="Output JSON")
    p_rec.set_defaults(func=cmd_project_recover)

    p_proj_rel = s_proj.add_parser("release-check", help="Evaluate release eligibility gates")
    p_proj_rel.add_argument("project_id", help="Project identifier")
    p_proj_rel.add_argument("--home", help="Base directory for research projects")
    p_proj_rel.add_argument("--json", action="store_true", help="Output JSON")
    p_proj_rel.set_defaults(func=cmd_release_check)

    # project actor subparser group (support 'research project actor add ...')
    p_act_grp = s_proj.add_parser("actor", help="Actor administration commands")
    s_act_grp = p_act_grp.add_subparsers(dest="actor_subcmd", required=True)

    for p in (s_act_grp.add_parser("add", help="Register a new actor"),
              s_proj.add_parser("actor-add", help="Register a new actor")):
        p.add_argument("project_id", help="Project identifier")
        p.add_argument("--actor-id", required=True, help="Actor identifier")
        p.add_argument("--roles", required=True, help="Comma-separated roles (e.g. author,importer)")
        p.add_argument("--display-name", help="Display name")
        p.add_argument("--independence-group", help="Independence group for peer review")
        p.add_argument("--token", help="Authenticated owner token")
        p.add_argument("--token-file", help="File containing owner token")
        p.add_argument("--home", help="Base directory for research projects")
        p.add_argument("--json", action="store_true", help="Output JSON")
        p.set_defaults(func=cmd_project_actor_add)

    for p in (s_act_grp.add_parser("update", help="Update an existing actor's roles, display name, or independence group"),
              s_proj.add_parser("actor-update", help="Update an existing actor's roles, display name, or independence group")):
        p.add_argument("project_id", help="Project identifier")
        p.add_argument("--actor-id", required=True, help="Actor identifier to update")
        p.add_argument("--roles", help="Updated comma-separated roles")
        p.add_argument("--display-name", help="Updated display name")
        p.add_argument("--independence-group", help="Updated independence group")
        p.add_argument("--token", help="Authenticated owner token")
        p.add_argument("--token-file", help="File containing owner token")
        p.add_argument("--home", help="Base directory for research projects")
        p.add_argument("--json", action="store_true", help="Output JSON")
        p.set_defaults(func=cmd_project_actor_update)

    for p in (s_act_grp.add_parser("disable", help="Disable an actor"),
              s_proj.add_parser("actor-disable", help="Disable an actor")):
        p.add_argument("project_id", help="Project identifier")
        p.add_argument("--actor-id", required=True, help="Actor identifier to disable")
        p.add_argument("--token", help="Authenticated owner token")
        p.add_argument("--token-file", help="File containing owner token")
        p.add_argument("--home", help="Base directory for research projects")
        p.add_argument("--json", action="store_true", help="Output JSON")
        p.set_defaults(func=cmd_project_actor_disable)

    p_upg = s_proj.add_parser("upgrade-schema", help="Explicitly upgrade project schema to current engine version")
    p_upg.add_argument("project_id", help="Project identifier")
    p_upg.add_argument("--token", help="Authenticated owner token")
    p_upg.add_argument("--token-file", help="File containing owner token")
    p_upg.add_argument("--home", help="Base directory for research projects")
    p_upg.add_argument("--json", action="store_true", help="Output JSON")
    p_upg.set_defaults(func=cmd_project_upgrade_schema)

    p_mig = s_proj.add_parser("migrate-v8", help="Migrate legacy v8.4.4 project to v9")
    p_mig.add_argument("project_id", help="Project identifier")
    p_mig.add_argument("--legacy-root", required=True, help="Path to legacy LaTeX/research_harness directory")
    p_mig.add_argument("--token", help="Actor token with owner/importer role")
    p_mig.add_argument("--token-file", help="File containing actor token")
    p_mig.add_argument("--dry-run", action="store_true", help="Inspect without modifying")
    p_mig.add_argument("--home", help="Base directory for research projects")
    p_mig.add_argument("--json", action="store_true", help="Output JSON")
    p_mig.set_defaults(func=cmd_project_migrate_v8)


    # snapshot
    p_snap = sub.add_parser("snapshot", help="Export and verify scoped snapshots")
    s_snap = p_snap.add_subparsers(dest="snapshot_cmd", required=True)

    p_sexp = s_snap.add_parser("export", help="Export a scoped snapshot")
    p_sexp.add_argument("project_id", help="Project identifier")
    p_sexp.add_argument("--scope", required=True, help="Scope (literature, theory_novelty, design, analysis, writing, full)")
    p_sexp.add_argument("--token", help="Actor token")
    p_sexp.add_argument("--token-file", help="File containing actor token")
    p_sexp.add_argument("--output", "-o", help="File path to save snapshot JSON")
    p_sexp.add_argument("--home", help="Base directory for research projects")
    p_sexp.add_argument("--json", action="store_true", help="Output JSON to stdout")
    p_sexp.set_defaults(func=cmd_snapshot_export)

    p_sver = s_snap.add_parser("verify", help="Verify snapshot cryptographic integrity and HMAC")
    p_sver.add_argument("project_id", help="Project identifier")
    p_sver.add_argument("--snapshot", required=True, help="Snapshot JSON file path")
    p_sver.add_argument("--home", help="Base directory for research projects")
    p_sver.add_argument("--json", action="store_true", help="Output JSON")
    p_sver.set_defaults(func=cmd_snapshot_verify)

    # proposal
    p_prop = sub.add_parser("proposal", help="Validate and import proposals")
    s_prop = p_prop.add_subparsers(dest="proposal_cmd", required=True)

    p_pval = s_prop.add_parser("validate", help="Static validation of proposal structure and guards")
    p_pval.add_argument("--proposal", required=True, help="Proposal JSON file path")
    p_pval.add_argument("--json", action="store_true", help="Output JSON")
    p_pval.set_defaults(func=cmd_proposal_validate)

    p_pimp = s_prop.add_parser("import", help="Submit proposal to gateway")
    p_pimp.add_argument("project_id", help="Target project identifier")
    p_pimp.add_argument("--proposal", required=True, help="Proposal JSON file path")
    p_pimp.add_argument("--token", help="Actor token with importer role")
    p_pimp.add_argument("--token-file", help="File containing actor token")
    p_pimp.add_argument("--dry-run", action="store_true", help="Validate without committing state")
    p_pimp.add_argument("--home", help="Base directory for research projects")
    p_pimp.add_argument("--json", action="store_true", help="Output JSON")
    p_pimp.set_defaults(func=cmd_proposal_import)

    # review
    p_rev = sub.add_parser("review", help="Record privileged scientific peer review")
    s_rev = p_rev.add_subparsers(dest="review_cmd", required=True)

    p_rrec = s_rev.add_parser("record", help="Record author approval or independent review")
    p_rrec.add_argument("project_id", help="Project identifier")
    p_rrec.add_argument("--object-id", required=True, help="Object identifier")
    p_rrec.add_argument("--revision", type=int, required=True, help="Head revision number")
    p_rrec.add_argument("--kind", choices=["author_approval", "independent_review"], required=True)
    p_rrec.add_argument("--decision", choices=["approve", "reject"], required=True)
    p_rrec.add_argument("--token", help="Reviewer actor token")
    p_rrec.add_argument("--token-file", help="File containing reviewer token")
    p_rrec.add_argument("--rationale", help="Review rationale text")
    p_rrec.add_argument("--acknowledge-invalidations", action="store_true", help="Explicitly acknowledge and clear upstream changes")
    p_rrec.add_argument("--home", help="Base directory for research projects")
    p_rrec.add_argument("--json", action="store_true", help="Output JSON")
    p_rrec.set_defaults(func=cmd_review_record)

    # dependency
    p_dep = sub.add_parser("dependency", help="Inspect dependency graph")
    s_dep = p_dep.add_subparsers(dest="dep_cmd", required=True)

    p_dimp = s_dep.add_parser("impact", help="Simulate cascading impact of changing an object")
    p_dimp.add_argument("project_id", help="Project identifier")
    p_dimp.add_argument("--object-id", required=True, help="Object identifier to trace")
    p_dimp.add_argument("--home", help="Base directory for research projects")
    p_dimp.add_argument("--json", action="store_true", help="Output JSON")
    p_dimp.set_defaults(func=cmd_dependency_impact)

    # history
    p_hist = sub.add_parser("history", help="Show revisions and status timeline of an object")
    p_hist.add_argument("project_id", help="Project identifier")
    p_hist.add_argument("--object-id", required=True, help="Object identifier")
    p_hist.add_argument("--home", help="Base directory for research projects")
    p_hist.add_argument("--json", action="store_true", help="Output JSON")
    p_hist.set_defaults(func=cmd_history)

    # release-check
    p_rel = sub.add_parser("release-check", help="Evaluate release eligibility gates")
    p_rel.add_argument("project_id", help="Project identifier")
    p_rel.add_argument("--home", help="Base directory for research projects")
    p_rel.add_argument("--json", action="store_true", help="Output JSON")
    p_rel.set_defaults(func=cmd_release_check)

    # schema
    p_sch = sub.add_parser("schema", help="Export JSON schemas")
    s_sch = p_sch.add_subparsers(dest="schema_cmd", required=True)

    p_sexp2 = s_sch.add_parser("export", help="Export all schemas to JSON files")
    p_sexp2.add_argument("--dir", help="Target directory (default: schemas)")
    p_sexp2.add_argument("--json", action="store_true", help="Output JSON list of schemas")
    p_sexp2.set_defaults(func=cmd_schema_export)

    # workflow
    p_wf = sub.add_parser("workflow", help="Manage and execute modular research workflows")
    s_wf = p_wf.add_subparsers(dest="workflow_cmd", required=True)

    p_wflist = s_wf.add_parser("list", help="List registered research workflows")
    p_wflist.add_argument("--json", action="store_true", help="Output JSON")
    p_wflist.set_defaults(func=cmd_workflow_list)

    p_wfrun = s_wf.add_parser("run", help="Run a modular research workflow")
    p_wfrun.add_argument("workflow", choices=["contribution", "literature", "methods", "results", "discussion"], help="Workflow identifier")
    p_wfrun.add_argument("--project", "--project-id", dest="project", required=True, help="Project identifier")
    p_wfrun.add_argument("--question", "--question-id", dest="question", help="Target research question ID")
    p_wfrun.add_argument("--claims", help="Comma-separated focus claim IDs")
    p_wfrun.add_argument("--budget", type=int, default=12000, help="Maximum input token budget")
    p_wfrun.add_argument("--profile", help="Discipline profile (accounting, finance, education)")
    p_wfrun.add_argument("--no-cache", action="store_true", help="Bypass artifact cache")
    p_wfrun.add_argument("--home", help="Base directory for research projects")
    p_wfrun.add_argument("--json", action="store_true", help="Output JSON")
    p_wfrun.set_defaults(func=cmd_workflow_run)

    def _run_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--project", "--project-id", dest="project", required=True, help="Project identifier")
        p.add_argument("--home", help="Base directory for research projects")
        p.add_argument("--json", action="store_true", help="Output JSON")

    p_dom = sub.add_parser("domain", help="Domain workflows: execution contracts (steps, conditions, loop-backs)")
    s_dom = p_dom.add_subparsers(dest="domain_cmd", required=True)
    p_domlist = s_dom.add_parser("list", help="List the built-in domain workflows")
    p_domlist.add_argument("--json", action="store_true")
    p_domlist.set_defaults(func=cmd_domain_list)
    p_domnext = s_dom.add_parser("next", help="What can run now under a domain workflow, and what blocks the rest")
    p_domnext.add_argument("--project", "--project-id", dest="project", required=True)
    p_domnext.add_argument("--spec", help="Built-in id or path to a spec JSON (default: the generic workflow)")
    p_domnext.add_argument("--question", "--question-id", dest="question")
    p_domnext.add_argument("--study-design", dest="study_design")
    p_domnext.add_argument("--home")
    p_domnext.add_argument("--json", action="store_true")
    p_domnext.set_defaults(func=cmd_domain_next)

    p_wfruns = s_wf.add_parser("runs", help="List recorded workflow runs (newest first)")
    _run_args(p_wfruns)
    p_wfruns.add_argument("--workflow", choices=["contribution", "literature", "methods", "results", "discussion"])
    p_wfruns.add_argument("--question", "--question-id", dest="question", help="Only runs for this research question")
    p_wfruns.add_argument("--legacy", action="store_true", help="Only legacy 9.1.1 runs (unverified history)")
    p_wfruns.add_argument("--limit", type=int)
    p_wfruns.set_defaults(func=cmd_workflow_runs)

    p_wfshow = s_wf.add_parser("show", help="Show one recorded run; --prompt prints the exact model request")
    p_wfshow.add_argument("run_id")
    _run_args(p_wfshow)
    p_wfshow.add_argument("--prompt", action="store_true", help="Print the system and user prompt that were sent")
    p_wfshow.set_defaults(func=cmd_workflow_show)

    p_wfdiff = s_wf.add_parser("diff", help="Field-level differences between two runs")
    p_wfdiff.add_argument("run_a")
    p_wfdiff.add_argument("run_b")
    _run_args(p_wfdiff)
    p_wfdiff.set_defaults(func=cmd_workflow_diff)

    # context
    p_ctx = sub.add_parser("context", help="Inspect and preview selective workflow contexts")
    s_ctx = p_ctx.add_subparsers(dest="context_cmd", required=True)

    p_ctxprev = s_ctx.add_parser("preview", help="Preview selective context manifest and token budget")
    p_ctxprev.add_argument("--project", "--project-id", dest="project", required=True, help="Project identifier")
    p_ctxprev.add_argument("--workflow", choices=["contribution", "literature", "methods", "results", "discussion"], required=True, help="Target workflow")
    p_ctxprev.add_argument("--question", "--question-id", dest="question", help="Target research question ID")
    p_ctxprev.add_argument("--claims", help="Comma-separated focus claim IDs")
    p_ctxprev.add_argument("--budget", type=int, default=12000, help="Maximum input token budget")
    p_ctxprev.add_argument("--fulltext", choices=["on_demand", "always", "never"], default="on_demand", help="Full-text expansion policy")
    p_ctxprev.add_argument("--profile", help="Discipline profile (accounting, finance, education)")
    p_ctxprev.add_argument("--home", help="Base directory for research projects")
    p_ctxprev.add_argument("--json", action="store_true", help="Output JSON")
    p_ctxprev.set_defaults(func=cmd_context_preview)

    # usage
    p_use = sub.add_parser("usage", help="Inspect token usage and workflow budgeting")
    s_use = p_use.add_subparsers(dest="usage_cmd", required=True)

    p_userep = s_use.add_parser("report", help="Report aggregated token and execution metrics")
    p_userep.add_argument("--project", "--project-id", dest="project", required=True, help="Project identifier")
    p_userep.add_argument("--workflow", help="Optional workflow filter")
    p_userep.add_argument("--home", help="Base directory for research projects")
    p_userep.add_argument("--json", action="store_true", help="Output JSON")
    p_userep.set_defaults(func=cmd_usage_report)

    from research_harness import cli_knowledge
    cli_knowledge.register(sub)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if hasattr(args, "func"):
        return args.func(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
