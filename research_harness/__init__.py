"""Research Harness 9.0 (dev): modular research core.

Layering (see docs/ARCHITECTURE.md):

* ``core``        - canonical hashing, clock, ids, errors, enums, authentication
* ``state``       - SQLite versioned object store, content-addressed artifact store
* ``provenance``  - hash-chained event log, claim formation provenance
* ``graph``       - typed dependency graph, cycle detection, invalidation engine
* ``protocols``   - JSON schemas, snapshot export / verification
* ``proposals``   - proposal import gateway (agent proposes, engine validates)
* ``validation``  - integrity verification, project status, release check
* ``legacy``      - adapters over the v8.4.4 harness (vendored primitives, migration)
"""

__version__ = "9.2.0"
PROTOCOL_VERSION = "9.0.0"
SUPPORTED_PROTOCOL_VERSIONS = ("9.0.0",)
SCHEMA_VERSION = "9.0"
