"""SQLite telemetry adapter for the Compact profile: ADR-021 layout C+H (5.1, 5.3, 10.1-10.6).

It implements every telemetry port on one file, ``/var/lib/vakr/telemetry.db``,
beside the metadata database, and passes the shared contract suites
unchanged. The schema (dictionaries, pinned codes, the partition catalog and
templates, staging tables) is created only by versioned migrations in
``sql/`` (5.6); day and month partitions are created from those templates
as data arrives.

- ``store``: the store (preflight, tuned pool, writer gate), the schema family.
- ``backend``: ``SqliteTelemetry`` (all ports and ``maintain``) and its factory.
- ``writer``, ``maintenance``: batch writes, the staging mover, checkpoints.
- ``series``, ``downsample``, ``state``, ``latest``, ``reads``: queries.
- ``rollups``, ``rollup_reads``, ``rollup_codec``: rollup storage.
- ``retention``: horizons and whole-partition drops, and the policy from config.
- ``layout``, ``partitions``, ``keys``, ``vocab``, ``codec*``: storage details.
- ``health``: the ``telemetry_db`` self-health check.

Dependencies:
    The modules listed above; ``vakr.adapters.sqlite`` for the shared layer.

Safety:
    Synchronous: every call blocks on disk I/O and locks. Async callers must
    use a bounded worker thread and one writer thread (5.3). The store
    refuses any schema but the current one.

Example:
    >>> from vakr.adapters.sqlite.telemetry import TELEMETRY_FILE, telemetry_family
    >>> TELEMETRY_FILE, telemetry_family().supported.oldest
    ('telemetry.db', 0)
"""

from vakr.adapters.sqlite.telemetry.backend import (
    CAPABILITIES,
    SqliteTelemetry,
    sqlite_telemetry_factory,
)
from vakr.adapters.sqlite.telemetry.maintenance import MaintenanceReport
from vakr.adapters.sqlite.telemetry.retention import retention_policy
from vakr.adapters.sqlite.telemetry.store import (
    COMPACT_POLICY,
    TELEMETRY_APPLICATION_ID,
    TELEMETRY_FILE,
    SqliteTelemetryStore,
    telemetry_family,
    telemetry_migrations,
)

__all__ = [
    "CAPABILITIES",
    "COMPACT_POLICY",
    "TELEMETRY_APPLICATION_ID",
    "TELEMETRY_FILE",
    "MaintenanceReport",
    "SqliteTelemetry",
    "SqliteTelemetryStore",
    "retention_policy",
    "sqlite_telemetry_factory",
    "telemetry_family",
    "telemetry_migrations",
]
