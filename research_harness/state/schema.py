"""SQLite schema for the versioned research state.

Design notes
* Object revisions, edges, events, status history, proposals, snapshots and the audit log are
  append-only (enforced by triggers, not only by convention).
* Revision numbering is enforced by a trigger as well as by application code.
* Foreign keys make dangling references impossible; the two FKs that point at
  ``state_revisions`` are DEFERRED because a state revision row is written last in a transaction
  (its digest depends on the rows written before it).
"""
from __future__ import annotations

from research_harness.graph.relations import RELATIONS

DB_SCHEMA_VERSION = "9.0.2"

# 9.0.2: an edge may only be inserted in the state revision that created its source revision.
# (Revision digests cover the outgoing edge set; late edges would silently change it.)
EDGE_SEAL_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS edges_sealed_at_revision_creation BEFORE INSERT ON edges
BEGIN
  SELECT CASE
    WHEN NEW.created_state_revision IS NOT
         (SELECT created_state_revision FROM objects
           WHERE object_id = NEW.source_id AND revision = NEW.source_revision)
      THEN RAISE(ABORT, 'edge set of a revision is sealed when the revision is created')
  END;
END;
"""

_REL_CHECK = ",".join(f"'{r}'" for r in RELATIONS)

_TABLES = f"""
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE actors(
  actor_id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL DEFAULT '',
  roles TEXT NOT NULL,
  independence_group TEXT,
  token_sha256 TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  created_by TEXT,
  disabled_at TEXT
);

CREATE TABLE state_revisions(
  revision INTEGER PRIMARY KEY CHECK(revision>=0),
  state_sha256 TEXT NOT NULL,
  committed_at TEXT NOT NULL,
  event_seq INTEGER NOT NULL,
  actor_id TEXT NOT NULL REFERENCES actors(actor_id)
);

CREATE TABLE objects(
  object_id TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK(revision>=1),
  object_type TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  content TEXT NOT NULL,
  content_digest TEXT NOT NULL,
  revision_digest TEXT NOT NULL,
  origin TEXT NOT NULL,
  origin_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  created_state_revision INTEGER NOT NULL
      REFERENCES state_revisions(revision) DEFERRABLE INITIALLY DEFERRED,
  submitted_by TEXT NOT NULL REFERENCES actors(actor_id),
  PRIMARY KEY(object_id, revision)
);

CREATE TABLE object_heads(
  object_id TEXT PRIMARY KEY,
  object_type TEXT NOT NULL,
  head_revision INTEGER NOT NULL,
  first_created_at TEXT NOT NULL,
  first_state_revision INTEGER NOT NULL,
  updated_at TEXT NOT NULL,
  lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN ('ACTIVE','WITHDRAWN')),
  FOREIGN KEY(object_id, head_revision) REFERENCES objects(object_id, revision)
);

CREATE TABLE object_status(
  object_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  scientific_review_status TEXT NOT NULL CHECK(scientific_review_status IN
      ('UNASSESSED','NEEDS_REVIEW','AUTHOR_APPROVED','INDEPENDENTLY_REVIEWED','REJECTED')),
  prior_review_status TEXT,
  updated_at TEXT NOT NULL,
  updated_state_revision INTEGER NOT NULL,
  PRIMARY KEY(object_id, revision),
  FOREIGN KEY(object_id, revision) REFERENCES objects(object_id, revision)
);

CREATE TABLE status_log(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  object_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  dimension TEXT NOT NULL,
  old_value TEXT,
  new_value TEXT NOT NULL,
  cause TEXT NOT NULL,
  state_revision INTEGER NOT NULL,
  actor_id TEXT NOT NULL,
  at TEXT NOT NULL,
  FOREIGN KEY(object_id, revision) REFERENCES objects(object_id, revision)
);

CREATE TABLE edges(
  edge_id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id TEXT NOT NULL,
  source_revision INTEGER NOT NULL,
  target_id TEXT NOT NULL,
  target_revision INTEGER NOT NULL,
  relation TEXT NOT NULL CHECK(relation IN ({_REL_CHECK})),
  provenance TEXT NOT NULL,
  created_state_revision INTEGER NOT NULL,
  UNIQUE(source_id, source_revision, relation, target_id, target_revision),
  CHECK(NOT (source_id = target_id AND source_revision = target_revision)),
  FOREIGN KEY(source_id, source_revision) REFERENCES objects(object_id, revision),
  FOREIGN KEY(target_id, target_revision) REFERENCES objects(object_id, revision)
);
CREATE INDEX edges_by_target ON edges(target_id, target_revision);
CREATE INDEX edges_by_source ON edges(source_id, source_revision);

CREATE TABLE invalidations(
  inval_id INTEGER PRIMARY KEY AUTOINCREMENT,
  object_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  cause_object_id TEXT NOT NULL,
  cause_from_revision INTEGER NOT NULL,
  cause_to_revision INTEGER NOT NULL,
  via_relation TEXT NOT NULL,
  hops INTEGER NOT NULL,
  path TEXT NOT NULL,
  created_state_revision INTEGER NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('OPEN','RESOLVED','SUPERSEDED')),
  resolved_state_revision INTEGER,
  resolved_by TEXT,
  resolution TEXT,
  FOREIGN KEY(object_id, revision) REFERENCES objects(object_id, revision)
);
CREATE INDEX invalidations_open ON invalidations(object_id, revision, status);

CREATE TABLE events(
  seq INTEGER PRIMARY KEY,
  event_id TEXT NOT NULL UNIQUE,
  state_revision INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  at TEXT NOT NULL,
  payload TEXT NOT NULL,
  prev_sha256 TEXT NOT NULL,
  event_sha256 TEXT NOT NULL UNIQUE
);

CREATE TABLE proposals(
  proposal_id TEXT PRIMARY KEY,
  content_sha256 TEXT NOT NULL,
  state_revision INTEGER NOT NULL,
  actor_id TEXT NOT NULL REFERENCES actors(actor_id),
  at TEXT NOT NULL,
  artifact_sha256 TEXT NOT NULL,
  receipt TEXT NOT NULL
);

CREATE TABLE snapshots(
  snapshot_id TEXT PRIMARY KEY,
  state_revision INTEGER NOT NULL REFERENCES state_revisions(revision),
  state_sha256 TEXT NOT NULL,
  scope TEXT NOT NULL,
  permitted_operations TEXT NOT NULL,
  object_revisions TEXT NOT NULL,
  exported_at TEXT NOT NULL,
  exported_by TEXT NOT NULL REFERENCES actors(actor_id),
  hmac_sha256 TEXT NOT NULL
);

CREATE TABLE audit_log(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  actor_id TEXT,
  action TEXT NOT NULL,
  decision TEXT NOT NULL,
  proposal_id TEXT,
  detail TEXT NOT NULL
);
"""

_APPEND_ONLY = ["objects", "edges", "events", "status_log", "state_revisions",
                "proposals", "snapshots", "audit_log"]
_NO_DELETE = ["object_heads", "object_status", "invalidations", "actors", "meta"]


def _triggers() -> str:
    out = []
    for t in _APPEND_ONLY:
        out.append(f"CREATE TRIGGER {t}_no_update BEFORE UPDATE ON {t} "
                   f"BEGIN SELECT RAISE(ABORT,'{t} is append-only'); END;")
    for t in _APPEND_ONLY + _NO_DELETE:
        out.append(f"CREATE TRIGGER {t}_no_delete BEFORE DELETE ON {t} "
                   f"BEGIN SELECT RAISE(ABORT,'{t} rows cannot be deleted'); END;")
    out.append("""
CREATE TRIGGER objects_revision_sequence BEFORE INSERT ON objects
BEGIN
  SELECT CASE
    WHEN NEW.revision = 1 AND EXISTS(SELECT 1 FROM objects WHERE object_id = NEW.object_id)
      THEN RAISE(ABORT, 'object already exists')
    WHEN NEW.revision > 1 AND
         (SELECT MAX(revision) FROM objects WHERE object_id = NEW.object_id) IS NOT (NEW.revision - 1)
      THEN RAISE(ABORT, 'non-sequential revision')
    WHEN NEW.revision > 1 AND
         (SELECT object_type FROM objects WHERE object_id = NEW.object_id AND revision = 1) IS NOT NEW.object_type
      THEN RAISE(ABORT, 'object type cannot change')
  END;
END;
CREATE TRIGGER invalidations_immutable_columns
BEFORE UPDATE OF object_id, revision, cause_object_id, cause_from_revision, cause_to_revision,
                 via_relation, hops, path, created_state_revision ON invalidations
BEGIN SELECT RAISE(ABORT, 'invalidation identity is immutable'); END;
CREATE TRIGGER heads_identity_immutable BEFORE UPDATE OF object_id, object_type, first_created_at,
                 first_state_revision ON object_heads
BEGIN SELECT RAISE(ABORT, 'object head identity is immutable'); END;
CREATE TRIGGER heads_monotonic BEFORE UPDATE OF head_revision ON object_heads
BEGIN SELECT CASE WHEN NEW.head_revision <> OLD.head_revision + 1
  THEN RAISE(ABORT, 'head revision must advance by exactly one') END; END;
""")
    out.append(EDGE_SEAL_TRIGGER)
    return "\n".join(out)


SQL_SCHEMA = _TABLES + _triggers()


def upgrade_schema(store, token: str | None = None) -> dict:
    """Explicitly upgrade an existing database schema to DB_SCHEMA_VERSION.

    Requires an authenticated owner token. Re-applies missing triggers (such as EDGE_SEAL_TRIGGER),
    attests the actor registry into the event log, and bumps meta.db_schema_version.
    """
    from research_harness.state.actors import attest_registry_in_tx
    with store.write_tx(token=token, require_roles=("owner",), bump=False) as tx:
        conn = tx.conn
        row = conn.execute("SELECT value FROM meta WHERE key='db_schema_version'").fetchone()
        current_version = row[0] if row else "9.0.1"
        if current_version == DB_SCHEMA_VERSION:
            return {"status": "NOOP", "version": current_version, "message": "schema already at latest version"}
        # Upgrade to 9.0.2: apply edge seal trigger if missing and attest actors
        conn.executescript(EDGE_SEAL_TRIGGER)
        conn.execute("UPDATE meta SET value=? WHERE key='db_schema_version'", (DB_SCHEMA_VERSION,))
        attest_registry_in_tx(tx)
        return {"status": "UPGRADED", "from_version": current_version, "to_version": DB_SCHEMA_VERSION}

