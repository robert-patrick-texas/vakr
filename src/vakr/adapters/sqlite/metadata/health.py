"""Self-health check for the SQLite metadata database (OPS-001, 16.3, MIG-003/004).

``vakr check health`` runs every minute from CheckMK or Nagios, so this
check reads local files only and changes nothing: it opens
``metadata.db`` read-only, reads the schema journal, and runs
``PRAGMA quick_check``. Results:

- OK: the schema is current and sound, or the host has no
  ``/var/lib/vakr`` and so is not an installed vakr node (the package's
  tmpfiles.d entry always creates it);
- WARNING: no database yet (a fresh install), pending migrations, or an
  interrupted transactional migration, which ``vakr db migrate`` reruns;
- CRITICAL: a schema newer than this release supports, a failed
  ``quick_check``, an unreadable file, a file that is not a vakr metadata
  database, or an interrupted migration that needs a restore.

Dependencies:
    vakr.adapters.sqlite.metadata.store: The schema family and manager.
    vakr.health.model: Check result types.
    vakr.paths: Locates the state directory.

Safety:
    Read-only. Summaries name the path, versions, and states, never rows.

Example:
    $ vakr check health
    VAKR WARNING - metadata_db: no metadata database at /var/lib/vakr/metadata.db yet ...
"""

from vakr.adapters.sqlite.metadata.store import METADATA_FILE, metadata_migrations
from vakr.domain.errors import StorageError
from vakr.health.model import CheckResult, PerfDatum, Status
from vakr.paths import InstallPaths
from vakr.ports.migrations import SchemaState, SchemaStatus

NAME = "metadata_db"

_SEVERITY = {
    SchemaState.CURRENT: Status.OK,
    SchemaState.MISSING: Status.WARNING,
    SchemaState.PENDING: Status.WARNING,
    SchemaState.TOO_NEW: Status.CRITICAL,
    SchemaState.MISMATCH: Status.CRITICAL,
}


def check_metadata_db() -> CheckResult:
    """Check the metadata database's schema and integrity.

    Args:
        None

    Returns:
        CheckResult: As the module docstring describes, with the schema
        version as performance data.

    Raises:
        None
    """
    state_dir = InstallPaths.from_environment().state_dir
    if not state_dir.exists(follow_symlinks=False):
        return CheckResult(NAME, Status.OK, f"no {state_dir}; not an installed vakr node")
    manager = metadata_migrations(state_dir / METADATA_FILE)
    try:
        status = manager.status()
        integrity = None if status.state is SchemaState.MISSING else manager.check_integrity()
    except StorageError as exc:
        return CheckResult(NAME, Status.CRITICAL, f"cannot read {manager.path}: {exc}")
    perf = (PerfDatum("metadata_schema", float(status.version)),)
    if integrity is not None and not integrity.ok:
        first = integrity.problems[0] if integrity.problems else "unknown problem"
        summary = (
            f"{manager.path} fails PRAGMA quick_check: {first}; see docs/operations/database.md"
        )
        return CheckResult(NAME, Status.CRITICAL, summary, perf)
    return CheckResult(NAME, _severity(status), status.detail, perf)


def _severity(status: SchemaStatus) -> Status:
    """Rank a schema state for monitoring.

    Args:
        status: The schema status.

    Returns:
        Status: ``WARNING`` for an interrupted migration that is safe to
        rerun, ``CRITICAL`` for one that needs a restore, otherwise by state.

    Raises:
        None
    """
    if status.state is SchemaState.INTERRUPTED:
        return Status.WARNING if status.can_migrate else Status.CRITICAL
    return _SEVERITY[status.state]
