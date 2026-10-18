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
            actor,
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
        snapshot = export_snapshot(store, actor, args.scope)
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
