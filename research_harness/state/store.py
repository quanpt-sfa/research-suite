"""Project-scoped transactional state: SQLite (WAL) + content-addressed artifacts.

Engine code is immutable and shared; everything here lives under ``<home>/<project_id>/`` which is
*outside* the engine repository (default ``/srv/research`` on the D13 host).

Every state change goes through ``StateStore.write_tx`` which opens ``BEGIN IMMEDIATE`` (one writer
at a time, no lock-upgrade deadlocks), makes all checks and writes inside that critical section
(no time-of-check/time-of-use gap) and either commits everything or nothing.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Optional

from research_harness import SCHEMA_VERSION, __version__
from research_harness.core.auth import (
    Actor,
    InternalCapability,
    authenticate,
    load_actor,
    new_token,
    token_fingerprint,
    token_hash,
    validate_roles,
)
from research_harness.core.canonical import canonical_json_hash
from research_harness.core.errors import AuthorizationError, Clock, HarnessError, utc_now
from research_harness.core.model import PROJECT_ID_RE, Lifecycle, ReviewStatus
from research_harness.provenance.events import append_event, chain_head
from research_harness.state.artifacts import ArtifactStore
from research_harness.state.schema import DB_SCHEMA_VERSION, SQL_SCHEMA


def default_home() -> Path:
    env = os.environ.get("RESEARCH_HOME")
    if env:
        return Path(env)
    return Path("/srv/research") if os.name == "posix" else Path.home() / "research_state"


class ProjectPaths:
    def __init__(self, home: Path | str, project_id: str):
        if not PROJECT_ID_RE.match(project_id or ""):
            raise HarnessError("PROJECT_ID_INVALID", "project id must match " + PROJECT_ID_RE.pattern)
        self.home = Path(home).resolve()
        self.project_id = project_id
        self.root = self.home / project_id
        self.db = self.root / "state.sqlite3"
        self.artifacts = self.root / "artifacts"
        self.staging = self.root / "staging"
        self.secrets = self.root / "secrets"
        self.key_path = self.secrets / "snapshot_hmac.key"
        self.backups = self.root / "backups"


def compute_state_digest(conn: sqlite3.Connection) -> str:
    """Deterministic digest of all scientific state (timestamps excluded)."""
    q = lambda sql: [list(r) for r in conn.execute(sql)]  # noqa: E731
    return canonical_json_hash({
        "objects": q("SELECT object_id,revision,revision_digest FROM objects ORDER BY object_id,revision"),
        "edges": q("SELECT source_id,source_revision,relation,target_id,target_revision FROM edges "
                   "ORDER BY source_id,source_revision,relation,target_id,target_revision"),
        "status": q("SELECT object_id,revision,scientific_review_status FROM object_status ORDER BY object_id,revision"),
        "heads": q("SELECT object_id,head_revision,lifecycle_status FROM object_heads ORDER BY object_id"),
    })


def revision_digest(content_digest: str, edges: Iterable[tuple]) -> str:
    return canonical_json_hash({"content": content_digest,
                                "edges": sorted([e[0], e[1], e[2]] for e in edges)})


class Tx:
    """A write transaction. Created only by ``StateStore.write_tx``."""

    def __init__(self, store: "StateStore", conn: sqlite3.Connection, actor_id: str, base_revision: int, bump: bool,
                 actor: Optional[Actor] = None):
        self.store = store
        self.conn = conn
        self.actor_id = actor_id
        self.actor = actor  # server-side identity, re-read inside this transaction
        self.base_revision = base_revision
        self.bump = bump
        self.revision = base_revision + 1 if bump else base_revision
        self.at = store.clock()
        self.changed = False
        self.events: list[dict] = []
        # Revisions created in THIS transaction: the only ones whose edge set may still be written.
        self._open_revisions: set[tuple[str, int]] = set()

    # ---- reads -------------------------------------------------------------------------
    def head(self, object_id: str) -> Optional[dict]:
        r = self.conn.execute("SELECT object_id,object_type,head_revision,lifecycle_status,first_state_revision,"
                              "first_created_at FROM object_heads WHERE object_id=?", (object_id,)).fetchone()
        return dict(r) if r else None

    def get(self, object_id: str, revision: Optional[int] = None) -> Optional[dict]:
        return self.store.get_object(object_id, revision, conn=self.conn)

    # ---- writes ------------------------------------------------------------------------
    def event(self, event_type: str, payload: dict[str, Any]) -> dict:
        ev = append_event(self.conn, event_type=event_type, actor_id=self.actor_id,
                          state_revision=self.revision, at=self.at, payload=payload)
        self.events.append(ev)
        return ev

    def audit(self, action: str, decision: str, detail: Optional[dict] = None, proposal_id: Optional[str] = None) -> None:
        """Audit-log row written atomically with the state change it describes (same transaction)."""
        self.conn.execute("INSERT INTO audit_log(at,actor_id,action,decision,proposal_id,detail) VALUES (?,?,?,?,?,?)",
                          (self.at, self.actor_id, action, decision, proposal_id,
                           json.dumps(detail or {}, sort_keys=True, ensure_ascii=False, default=str)))

    def require_roles(self, *roles: str) -> Actor:
        """Raise unless the authenticated actor of THIS transaction holds one of ``roles``."""
        if self.actor is None or not self.actor.has_role(*roles):
            raise AuthorizationError("ROLE_REQUIRED", "requires role " + " or ".join(roles))
        return self.actor

    def add_revision(self, *, object_id: str, object_type: str, content: dict, origin: dict,
                     edges: list[tuple]) -> dict:
        """Create revision N+1 (or 1). ``edges`` = [(relation, target_id, target_revision, provenance)]."""
        head = self.head(object_id)
        rev = head["head_revision"] + 1 if head else 1
        cdig = canonical_json_hash(content)
        rdig = revision_digest(cdig, [(e[0], e[1], e[2]) for e in edges])
        osha = canonical_json_hash(origin)
        self.conn.execute(
            "INSERT INTO objects(object_id,revision,object_type,schema_version,content,content_digest,revision_digest,"
            "origin,origin_sha256,created_at,created_state_revision,submitted_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (object_id, rev, object_type, SCHEMA_VERSION, json.dumps(content, sort_keys=True, ensure_ascii=False),
             cdig, rdig, json.dumps(origin, sort_keys=True, ensure_ascii=False), osha, self.at, self.revision,
             self.actor_id))
        if head:
            self.conn.execute("UPDATE object_heads SET head_revision=?, updated_at=? WHERE object_id=?",
                              (rev, self.at, object_id))
            self.conn.execute("UPDATE invalidations SET status='SUPERSEDED' WHERE object_id=? AND revision<? "
                              "AND status='OPEN'", (object_id, rev))
        else:
            self.conn.execute("INSERT INTO object_heads(object_id,object_type,head_revision,first_created_at,"
                              "first_state_revision,updated_at,lifecycle_status) VALUES (?,?,?,?,?,?,?)",
                              (object_id, object_type, 1, self.at, self.revision, self.at, Lifecycle.ACTIVE.value))
        self.conn.execute("INSERT INTO object_status(object_id,revision,scientific_review_status,prior_review_status,"
                          "updated_at,updated_state_revision) VALUES (?,?,?,?,?,?)",
                          (object_id, rev, ReviewStatus.UNASSESSED.value, None, self.at, self.revision))
        self._log_status(object_id, rev, "scientific_review", None, ReviewStatus.UNASSESSED.value,
                         {"reason": "new revision starts UNASSESSED"})
        self._open_revisions.add((object_id, rev))
        for relation, tid, trev, prov in edges:
            self.add_edge(object_id, rev, relation, tid, trev, prov)
        self.changed = True
        self.event("OBJECT_REVISION_CREATED", {
            "object_id": object_id, "revision": rev, "object_type": object_type, "content_digest": cdig,
            "revision_digest": rdig, "origin_sha256": osha, "origin_kind": origin.get("kind"),
            "edges": [[e[0], e[1], e[2]] for e in edges]})
        return {"object_id": object_id, "revision": rev, "content_digest": cdig, "revision_digest": rdig}

    def add_edge(self, source_id: str, source_rev: int, relation: str, target_id: str, target_rev: int,
                 provenance: dict) -> None:
        # The revision digest covers the outgoing edge set. A revision's edge set is therefore
        # sealed when the revision is created: late edges would change its logical dependency set
        # without changing its digest (audit finding A3). Attach edges through ``add_revision``.
        if (source_id, source_rev) not in self._open_revisions:
            raise HarnessError("EDGE_SET_SEALED",
                               "edges can only be added while their source revision is being created",
                               source_id=source_id, source_revision=source_rev)
        self.conn.execute(
            "INSERT INTO edges(source_id,source_revision,target_id,target_revision,relation,provenance,"
            "created_state_revision) VALUES (?,?,?,?,?,?,?)",
            (source_id, source_rev, target_id, target_rev, relation,
             json.dumps(provenance, sort_keys=True, ensure_ascii=False), self.revision))
        self.changed = True

    def _log_status(self, object_id, revision, dimension, old, new, cause):
        self.conn.execute(
            "INSERT INTO status_log(object_id,revision,dimension,old_value,new_value,cause,state_revision,actor_id,at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (object_id, revision, dimension, old, new, json.dumps(cause, sort_keys=True, ensure_ascii=False),
             self.revision, self.actor_id, self.at))

    def set_review(self, object_id: str, revision: int, new_status: ReviewStatus, cause: dict) -> bool:
        row = self.conn.execute("SELECT scientific_review_status FROM object_status WHERE object_id=? AND revision=?",
                                (object_id, revision)).fetchone()
        if row is None:
            raise HarnessError("OBJECT_REVISION_UNKNOWN", object_id=object_id, revision=revision)
        old = row[0]
        if old == new_status.value:
            return False
        self.conn.execute("UPDATE object_status SET scientific_review_status=?, prior_review_status=?, updated_at=?,"
                          " updated_state_revision=? WHERE object_id=? AND revision=?",
                          (new_status.value, old, self.at, self.revision, object_id, revision))
        self._log_status(object_id, revision, "scientific_review", old, new_status.value, cause)
        self.changed = True
        self.event("REVIEW_STATUS_CHANGED", {"object_id": object_id, "revision": revision, "from": old,
                                             "to": new_status.value, "cause": cause})
        return True

    def set_lifecycle(self, object_id: str, lifecycle: Lifecycle, cause: dict) -> None:
        head = self.head(object_id)
        if not head:
            raise HarnessError("OBJECT_UNKNOWN", object_id=object_id)
        if head["lifecycle_status"] == lifecycle.value:
            return
        self.conn.execute("UPDATE object_heads SET lifecycle_status=?, updated_at=? WHERE object_id=?",
                          (lifecycle.value, self.at, object_id))
        self._log_status(object_id, head["head_revision"], "lifecycle", head["lifecycle_status"], lifecycle.value, cause)
        self.changed = True
        self.event("LIFECYCLE_CHANGED", {"object_id": object_id, "from": head["lifecycle_status"],
                                         "to": lifecycle.value, "cause": cause})

    def finalize(self) -> None:
        if not (self.bump and self.changed):
            return
        digest = compute_state_digest(self.conn)
        seq, _ = chain_head(self.conn)
        self.conn.execute("INSERT INTO state_revisions(revision,state_sha256,committed_at,event_seq,actor_id)"
                          " VALUES (?,?,?,?,?)", (self.revision, digest, self.at, seq, self.actor_id))
        self.state_sha256 = digest

    def fault(self, point: str) -> None:
        """Crash-injection hook used by recovery tests (env RESEARCH_TEST_FAULT=<point>)."""
        mode = os.environ.get("RESEARCH_TEST_FAULT", "")
        if mode == f"exit:{point}":
            os._exit(137)
        if mode == f"raise:{point}":
            raise RuntimeError(f"injected fault at {point}")


class StateStore:
    def __init__(self, paths: ProjectPaths, clock: Clock = utc_now):
        self.paths = paths
        self.clock = clock
        self.artifacts = ArtifactStore(paths.artifacts, paths.staging)

    # ---- lifecycle ---------------------------------------------------------------------
    @classmethod
    def create(cls, home: Path | str, project_id: str, owner_id: str = "owner",
               clock: Clock = utc_now) -> tuple["StateStore", str]:
        paths = ProjectPaths(home, project_id)
        if paths.root.exists():
            raise HarnessError("PROJECT_EXISTS", "refusing to touch an existing project directory",
                               path=str(paths.root))
        paths.home.mkdir(parents=True, exist_ok=True)
        paths.root.mkdir()
        paths.secrets.mkdir(mode=0o700)
        fd = os.open(paths.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(os.urandom(32))
        store = cls(paths, clock)
        token = new_token()
        conn = store.connect()
        try:
            conn.executescript(SQL_SCHEMA)
            conn.execute("BEGIN IMMEDIATE")
            at = clock()
            for k, v in [("project_id", project_id), ("db_schema_version", DB_SCHEMA_VERSION),
                         ("schema_version", SCHEMA_VERSION), ("engine_version_created", __version__),
                         ("created_at", at)]:
                conn.execute("INSERT INTO meta(key,value) VALUES (?,?)", (k, v))
            conn.execute("INSERT INTO actors(actor_id,display_name,roles,independence_group,token_sha256,created_at,"
                         "created_by) VALUES (?,?,?,?,?,?,?)",
                         (owner_id, "project owner", json.dumps(validate_roles(["owner", "importer", "author"])),
                          None, token_hash(token), at, owner_id))
            ev = append_event(conn, event_type="PROJECT_INITIALIZED", actor_id=owner_id, state_revision=0, at=at,
                              payload={"project_id": project_id, "engine_version": __version__,
                                       "db_schema_version": DB_SCHEMA_VERSION,
                                        "owner": {"actor_id": owner_id, "roles": ["author", "importer", "owner"],
                                                  "independence_group": None, "display_name": "project owner",
                                                  "token_fingerprint": token_fingerprint(token_hash(token))}})
            conn.execute("INSERT INTO state_revisions(revision,state_sha256,committed_at,event_seq,actor_id)"
                         " VALUES (0,?,?,?,?)", (compute_state_digest(conn), at, ev["seq"], owner_id))
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return store, token

    @classmethod
    def open(cls, home: Path | str, project_id: str, clock: Clock = utc_now) -> "StateStore":
        paths = ProjectPaths(home, project_id)
        if not paths.db.is_file():
            raise HarnessError("PROJECT_NOT_FOUND", path=str(paths.root))
        store = cls(paths, clock)
        with store.read() as conn:
            pid = conn.execute("SELECT value FROM meta WHERE key='project_id'").fetchone()
            if not pid or pid[0] != project_id:
                raise HarnessError("PROJECT_ID_MISMATCH", "database belongs to a different project")
        return store

    @property
    def project_id(self) -> str:
        return self.paths.project_id

    @property
    def hmac_key(self) -> bytes:
        try:
            key = self.paths.key_path.read_bytes()
        except OSError as e:
            raise HarnessError("SNAPSHOT_KEY_MISSING", str(e)) from e
        if len(key) < 32:
            raise HarnessError("SNAPSHOT_KEY_INVALID", "key shorter than 32 bytes")
        return key

    def recover(self, max_staging_age_seconds: float = 0.0) -> dict[str, Any]:
        """Clean up stale staging files and inspect storage consistency after an interrupted operation."""
        stale = self.artifacts.stale_staging(max_staging_age_seconds)
        removed_staging = 0
        for p in stale:
            try:
                p.unlink()
                removed_staging += 1
            except OSError:
                pass

        try:
            conn = self.connect()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
        except Exception:
            pass

        with self.read() as conn:
            known_artifacts = set()
            try:
                for row in conn.execute("SELECT artifact_sha256 FROM proposals WHERE artifact_sha256 IS NOT NULL"):
                    if row[0]:
                        known_artifacts.add(row[0])
            except Exception:
                pass

            for row in conn.execute("SELECT content FROM objects"):
                try:
                    c = json.loads(row[0])
                    if isinstance(c, dict):
                        for k in ("artifact_sha256", "blob_sha256"):
                            v = c.get(k)
                            if isinstance(v, str) and len(v) == 64:
                                known_artifacts.add(v)
                except Exception:
                    pass

            from research_harness.state import workflow_runs
            known_artifacts |= workflow_runs.artifact_digests(conn)

        all_blobs = set(self.artifacts.blobs())
        orphans = sorted(all_blobs - known_artifacts)

        return {
            "status": "RECOVERED",
            "project_id": self.project_id,
            "stale_staging_removed": removed_staging,
            "orphan_artifacts": orphans,
        }

    # ---- connections / transactions ----------------------------------------------------
    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.paths.db, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @contextmanager
    def read(self):
        conn = self.connect()
        try:
            conn.execute("BEGIN")  # consistent snapshot for multi-statement reads
            yield conn
        finally:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            conn.close()

    @contextmanager
    def write_tx(self, actor_id: Optional[str] = None, *, token: Optional[str] = None,
                 internal_capability: Optional[Any] = None,
                 require_roles: tuple = (), bump: bool = True, commit: bool = True):
        """Open the single-writer transaction.

        Authorization is evaluated INSIDE ``BEGIN IMMEDIATE`` (no check/use gap): ``token`` is
        strictly authenticated. Plain string actor_ids and unauthenticated internal capabilities
        are NEVER accepted for runtime authorization. ``require_roles`` (any-of) is checked
        against the roles stored *now*, not against a caller-supplied copy.
        Raises ``AuthorizationError`` before any mutation; nothing is written in that case.
        """
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            actor = self._authorize(conn, actor_id, token, tuple(require_roles), internal_capability)
            base = conn.execute("SELECT MAX(revision) FROM state_revisions").fetchone()[0]
            tx = Tx(self, conn, actor.actor_id, base, bump, actor)
            try:
                yield tx
                tx.finalize()
                tx.fault("before_commit")
                conn.execute("COMMIT" if commit else "ROLLBACK")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        finally:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            conn.close()

    @staticmethod
    def _authorize(conn: sqlite3.Connection, actor_id: Optional[str], token: Optional[str],
                   require_roles: tuple,
                   internal_capability: Optional[Any] = None) -> Actor:
        if internal_capability is not None:
            raise AuthorizationError(
                "AUTHENTICATION_FAILED",
                "Internal capabilities are not authorized for runtime mutations; authenticated bearer token required"
            )
        if token is None:
            raise AuthorizationError(
                "AUTHENTICATION_FAILED",
                "Authentication token required; plain actor_id or unauthenticated capability cannot be used for authentication"
            )

        actor = authenticate(conn, token)
        if actor is None:
            raise AuthorizationError("AUTHENTICATION_FAILED", "invalid, unknown or disabled actor token")
        if actor_id is not None and actor.actor_id != actor_id:
            raise AuthorizationError("IDENTITY_MISMATCH", f"authenticated actor '{actor.actor_id}' does not match requested '{actor_id}'")

        if require_roles and not actor.has_role(*require_roles):
            raise AuthorizationError("ROLE_REQUIRED", "requires role " + " or ".join(require_roles))
        return actor


    def audit(self, *, actor_id: Optional[str], action: str, decision: str, proposal_id: Optional[str] = None,
              detail: Optional[dict] = None) -> None:
        """Record an attempt outside the state revision stream (rejections, exports, auth failures)."""
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO audit_log(at,actor_id,action,decision,proposal_id,detail) VALUES (?,?,?,?,?,?)",
                         (self.clock(), actor_id, action, decision, proposal_id,
                          json.dumps(detail or {}, sort_keys=True, ensure_ascii=False, default=str)))
            conn.execute("COMMIT")
        finally:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            conn.close()

    # ---- reads -------------------------------------------------------------------------
    def current_revision(self, conn: Optional[sqlite3.Connection] = None) -> int:
        if conn is not None:
            return conn.execute("SELECT MAX(revision) FROM state_revisions").fetchone()[0]
        with self.read() as c:
            return self.current_revision(c)

    def get_object(self, object_id: str, revision: Optional[int] = None,
                   conn: Optional[sqlite3.Connection] = None) -> Optional[dict]:
        if conn is None:
            with self.read() as c:
                return self.get_object(object_id, revision, c)
        if revision is None:
            r = conn.execute(
                "SELECT o.*, h.lifecycle_status, h.head_revision, s.scientific_review_status, s.prior_review_status "
                "FROM object_heads h JOIN objects o ON o.object_id=h.object_id AND o.revision=h.head_revision "
                "JOIN object_status s ON s.object_id=o.object_id AND s.revision=o.revision WHERE h.object_id=?",
                (object_id,)).fetchone()
        else:
            r = conn.execute(
                "SELECT o.*, h.lifecycle_status, h.head_revision, s.scientific_review_status, s.prior_review_status "
                "FROM objects o JOIN object_heads h ON h.object_id=o.object_id "
                "JOIN object_status s ON s.object_id=o.object_id AND s.revision=o.revision "
                "WHERE o.object_id=? AND o.revision=?", (object_id, revision)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["content"] = json.loads(d["content"])
        d["origin"] = json.loads(d["origin"])
        return d

    def edges_from(self, conn, object_id: str, revision: int) -> list[dict]:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM edges WHERE source_id=? AND source_revision=? ORDER BY relation,target_id,target_revision",
            (object_id, revision))]

    def edges_to(self, conn, object_id: str, revision: Optional[int] = None) -> list[dict]:
        if revision is None:
            q = conn.execute("SELECT * FROM edges WHERE target_id=? ORDER BY source_id,source_revision,relation",
                             (object_id,))
        else:
            q = conn.execute("SELECT * FROM edges WHERE target_id=? AND target_revision=? "
                             "ORDER BY source_id,source_revision,relation", (object_id, revision))
        return [dict(r) for r in q]
