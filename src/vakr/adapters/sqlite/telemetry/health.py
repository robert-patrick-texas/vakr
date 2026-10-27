"""Self-health check for the SQLite telemetry database (OPS-001, 16.3, ADR-021, E.21).

``vakr check health`` runs every minute from CheckMK or Nagios, so this
check reads local files only, changes nothing, and stays fast on a 40 GB
file: it reads the schema journal, the WAL file's size, and the oldest
staged row, and runs ``PRAGMA quick_check`` on the small fixed tables
(dictionaries, catalog, templates, state, and staging). Partitions are
checked by ``vakr db status --full``, which can take minutes. Results:

- OK: the schema is current, the WAL is at most 256 MiB, the oldest staged
  row is at most 30 minutes old, and the fixed tables are sound; or the
  host has no ``/var/lib/vakr`` (not an installed node);
- WARNING: no database yet, pending migrations, an interrupted migration
  that is safe to rerun, a WAL above 256 MiB (the truncating checkpoint is
  not keeping up), or mover lag above 30 minutes (``maintain`` is not
  running, so staging grows and queries slow down; data is still correct);
- CRITICAL: a newer schema, a foreign or unreadable file, an interrupted
  migration that needs a restore, or a failed ``quick_check``.

Collector or storage trouble here is vakr's own health, never target state.

Dependencies:
    vakr.adapters.sqlite.*: Connections, errors, and the telemetry family.
    vakr.health.model, vakr.paths: Results and the state directory.

Safety:
    Read-only. Summaries name the path, sizes, ages, and states, never rows.

Example:
    $ vakr check health
    VAKR OK - ... telemetry_db: schema 1 is current; WAL 4.0 MiB; mover lag 312 s ...
"""

# Staging and fixed table names come from layout and FIXED_TABLES only.
# ruff: noqa: S608

import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from vakr.adapters.sqlite.connection import OpenMode, open_connection
from vakr.adapters.sqlite.errors import translated
from vakr.adapters.sqlite.telemetry.layout import MOVED_FAMILIES, NATIVE_FAMILIES, hot_table
from vakr.adapters.sqlite.telemetry.store import TELEMETRY_FILE, telemetry_migrations
from vakr.adapters.sqlite.telemetry.tuning import MOVER_LAG_WARN, WAL_WARN_BYTES
from vakr.domain.errors import StorageError
from vakr.health.model import CheckResult, PerfDatum, Status
from vakr.paths import InstallPaths
from vakr.ports.migrations import SchemaState, SchemaStatus

NAME = "telemetry_db"
DOC = "see docs/operations/database.md"
FIXED_TABLES = (
    "dict_collector", "dict_device", "dict_interface", "vocab_code", "partition_template",
    "partition_catalog", "telemetry_state", "rank_unit",
    *(hot_table(f) for f in NATIVE_FAMILIES),
)  # fmt: skip
"""Tables every telemetry database has, small enough to check every minute."""

_SEVERITY = {
    SchemaState.CURRENT: Status.OK,
    SchemaState.MISSING: Status.WARNING,
    SchemaState.PENDING: Status.WARNING,
    SchemaState.TOO_NEW: Status.CRITICAL,
    SchemaState.MISMATCH: Status.CRITICAL,
}


@dataclass(frozen=True, slots=True)
class Vitals:
    """What the check measures on a current database.

    Attributes:
        wal_bytes: Size of the ``-wal`` file, 0 when absent.
        mover_lag_s: Age of the oldest staged row in seconds, 0 when staging is empty.
        problems: ``quick_check`` problems in the fixed tables; empty when sound.
    """

    wal_bytes: int
    mover_lag_s: float
    problems: tuple[str, ...]


def measure(path: Path, now_s: float) -> Vitals:
    """Read the WAL size, the mover lag, and the fixed tables' soundness.

    Args:
        path: The telemetry database.
        now_s: Current Unix time in seconds.

    Returns:
        Vitals: The measurements.

    Raises:
        StorageError: If the database cannot be opened or read.
    """
    wal = path.with_name(f"{path.name}-wal")
    wal_bytes = wal.stat().st_size if wal.exists() else 0
    with closing(open_connection(path, mode=OpenMode.READ_ONLY)) as conn, translated():
        oldest = [_oldest(conn, hot_table(f)) for f in MOVED_FAMILIES]
        problems = tuple(p for t in FIXED_TABLES for p in _check(conn, t))
    staged = [o for o in oldest if o is not None]
    lag = max(0.0, now_s - min(staged) / 1e6) if staged else 0.0
    return Vitals(wal_bytes, lag, problems)


def _oldest(conn: sqlite3.Connection, table: str) -> int | None:
    """Return the oldest staged timestamp of one staging table.

    Args:
        conn: Read-only connection.
        table: A staging table.

    Returns:
        int | None: Microseconds, or ``None`` when empty.

    Raises:
        sqlite3.Error: If the table cannot be read.
    """
    row = conn.execute(f"SELECT min(ts_us) FROM {table}").fetchone()
    return None if row[0] is None else int(row[0])


def _check(conn: sqlite3.Connection, table: str) -> list[str]:
    """Run ``PRAGMA quick_check`` on one table.

    Args:
        conn: Read-only connection.
        table: A fixed table name.

    Returns:
        list[str]: Problems, empty when sound.

    Raises:
        sqlite3.Error: If the check cannot run.
    """
    rows = [str(r[0]) for r in conn.execute(f"PRAGMA quick_check({table})")]
    return [] if rows == ["ok"] else [f"{table}: {line[:160]}" for line in rows[:3]]


def check_telemetry_db() -> CheckResult:
    """Check the telemetry database's schema, WAL, mover lag, and fixed tables.

    Args:
        None

    Returns:
        CheckResult: As the module docstring describes, with performance data.

    Raises:
        None
    """
    state_dir = InstallPaths.from_environment().state_dir
    if not state_dir.exists(follow_symlinks=False):
        return CheckResult(NAME, Status.OK, f"no {state_dir}; not an installed vakr node")
    manager = telemetry_migrations(state_dir / TELEMETRY_FILE)
    try:
        status = manager.status()
        vitals = measure(manager.path, time.time()) if status.state is SchemaState.CURRENT else None
    except StorageError as exc:
        return CheckResult(NAME, Status.CRITICAL, f"cannot read {manager.path}: {exc}")
    perf = [PerfDatum("telemetry_schema", float(status.version))]
    if vitals is None:
        return CheckResult(NAME, _severity(status), status.detail, tuple(perf))
    perf += [PerfDatum("telemetry_wal_bytes", float(vitals.wal_bytes), "B"),
             PerfDatum("telemetry_mover_lag", round(vitals.mover_lag_s, 1), "s")]  # fmt: skip
    return CheckResult(NAME, *_judge(status, vitals, manager.path), tuple(perf))


def _judge(status: SchemaStatus, vitals: Vitals, path: Path) -> tuple[Status, str]:
    """Rank a current database's measurements.

    Args:
        status: The (current) schema status.
        vitals: The measurements.
        path: The database, for messages.

    Returns:
        tuple[Status, str]: The worst finding and its summary.

    Raises:
        None
    """
    wal_mib, lag = vitals.wal_bytes / 2**20, vitals.mover_lag_s
    summary = f"{status.detail}; WAL {wal_mib:.1f} MiB; mover lag {lag:.0f} s"
    if vitals.problems:
        return Status.CRITICAL, f"{path} fails PRAGMA quick_check: {vitals.problems[0]}; {DOC}"
    if vitals.wal_bytes > WAL_WARN_BYTES:
        limit = WAL_WARN_BYTES // 2**20
        return Status.WARNING, f"{summary}: WAL above {limit} MiB, checkpoints lag; {DOC}"
    if lag > MOVER_LAG_WARN.total_seconds():
        limit = MOVER_LAG_WARN.total_seconds() / 60
        return Status.WARNING, f"{summary}: staged rows older than {limit:.0f} min; {DOC}"
    return Status.OK, summary


def _severity(status: SchemaStatus) -> Status:
    """Rank a schema state that is not current.

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
