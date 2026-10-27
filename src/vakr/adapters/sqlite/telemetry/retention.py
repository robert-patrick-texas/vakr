"""``RetentionManager`` for the SQLite telemetry store (RET-001..003, RET-007, ROLL-T04, E.10).

Retention never runs ``DELETE`` or ``VACUUM`` over observations or rollups.
It works in two layers, and then trims the small ranking projection
(``ranking_projection.prune``, E.28) behind the native horizon:

- **Horizons.** ``apply(now)`` moves the native horizon to
  ``now - policy.native``, aligned down to a 15-minute bucket and never past
  the rollup frontier (``coverage_end``), so native rows expire only behind
  complete rollup coverage (ROLL-T04). It moves the rollup horizon to
  ``now - policy.rollup``. Every read ignores rows before the horizons, so
  data expires exactly when the port says, as if it had been deleted.
- **Partitions.** In the same transaction it drops every day partition
  wholly before the native horizon and every month partition wholly before
  the rollup horizon, with their catalog rows. Disk therefore holds up to
  31 days of native data and 431 days of rollups at the worst point
  (blueprint 27.3), and freed pages are reused by the next partitions.

The report counts the observations and buckets each pass expired, read
with one primary-key seek per series (driven from the dictionaries).

Dependencies:
    vakr.adapters.sqlite.telemetry.*: Catalog, layout, rollups, and the store.
    vakr.domain, vakr.ports.telemetry: Bucket alignment and result types.

Safety:
    Only expired telemetry goes; metadata and dictionaries are untouched (RET-007).

Example:
    >>> SqliteRetentionManager.__name__
    'SqliteRetentionManager'
"""

# Counting SQL interpolates checked partition names only; values are bound parameters.
# ruff: noqa: S608

import sqlite3
from datetime import datetime, timedelta
from typing import Any, Protocol

from vakr.adapters.sqlite.encoding import decode_time, encode_time
from vakr.adapters.sqlite.telemetry.layout import (
    KEPT_FAMILIES,
    KEY_COLUMNS,
    NATIVE_FAMILIES,
    checked,
    hot_table,
)
from vakr.adapters.sqlite.telemetry.partitions import (
    HORIZON,
    ROLLUP_HORIZON,
    drop,
    ending_by,
    set_state,
    state,
    tables,
)
from vakr.adapters.sqlite.telemetry.ranking_projection import prune
from vakr.adapters.sqlite.telemetry.replication_log import purge_log
from vakr.adapters.sqlite.telemetry.rollups import coverage_end_us
from vakr.adapters.sqlite.telemetry.store import SqliteTelemetryStore
from vakr.domain.rollups import ROLLUP_BUCKET_SECONDS, bucket_start
from vakr.domain.timestamps import require_utc
from vakr.ports.telemetry.results import RetentionPolicy, RetentionReport

KEPT_PURGE_ROWS = 100_000
"""Status rows one retention pass deletes at most (E.35); a day holds a few thousand."""

_DRIVERS = {"obs_iface": ("dict_interface", "interface_key"),
            "obs_health": ("dict_collector", "collector_key")}  # fmt: skip
_ROLLUP_DRIVERS = {"roll_iface": ("dict_interface", "interface_key"),
                   "roll_icmp": ("dict_device", "device_key")}  # fmt: skip
_US = 1_000_000


class _Config(Protocol):
    """The part of ``EffectiveConfig`` retention reads."""

    def get(self, dotted_key: str, default: Any = None) -> Any:
        """Return an option's effective value.

        Args:
            dotted_key: Such as ``telemetry.native_retention_days``.
            default: Value when unset.

        Returns:
            Any: The value.

        Raises:
            None
        """
        ...


def retention_policy(config: _Config) -> RetentionPolicy:
    """Build the retention policy from configuration (E.10).

    Args:
        config: Effective configuration with the ``telemetry.*`` options.

    Returns:
        RetentionPolicy: ``native_retention_days`` and ``rollup_retention_days``.

    Raises:
        ValidationError: If a duration is not positive.
    """
    native = int(config.get("telemetry.native_retention_days", 30))
    rollup = int(config.get("telemetry.rollup_retention_days", 400))
    return RetentionPolicy(native=timedelta(days=native), rollup=timedelta(days=rollup))


class SqliteRetentionManager:
    """Expires native observations and rollups by policy, by horizon and whole partition."""

    def __init__(self, store: SqliteTelemetryStore) -> None:
        """Bind the manager.

        Args:
            store: The open store.

        Returns:
            None

        Raises:
            None
        """
        self._store = store

    @property
    def policy(self) -> RetentionPolicy:
        """The retention policy in force.

        Args:
            None

        Returns:
            RetentionPolicy: The store's policy.

        Raises:
            None
        """
        return self._store.policy

    def native_horizon(self) -> datetime | None:
        """Return the time before which native data has been expired.

        Args:
            None

        Returns:
            datetime | None: The horizon, or ``None`` before the first expiry.

        Raises:
            None
        """
        with self._store.reading() as conn:
            value = state(conn, HORIZON)
        return None if value is None else decode_time(value)

    def apply(self, now: datetime) -> RetentionReport:
        """Expire native data and rollups by policy; repeating changes nothing more.

        Args:
            now: Reference time (UTC).

        Returns:
            RetentionReport: Cutoffs and the observations and buckets expired.

        Raises:
            ValidationError: If ``now`` is not UTC.
            StorageError: If the database is unavailable; nothing changes.
        """
        require_utc(now, "now")
        policy = self._store.policy
        rollup_cut = now - policy.rollup
        with self._store.writing() as conn:
            frontier = coverage_end_us(conn)
            cutoff = None if frontier is None else bucket_start(min(now - policy.native, frontier))
            native = 0 if cutoff is None else _advance_native(conn, encode_time(cutoff))
            rollups = _advance_rollups(conn, encode_time(rollup_cut))
            prune(conn, encode_time(now))  # the ranking projection never outlives native data
            purge_log(conn, state(conn, HORIZON))  # nor the replication log (MPOS-005)
            purge_kept(conn, state(conn, HORIZON))  # status records live outside partitions
        return RetentionReport(now, cutoff, rollup_cut, native, rollups)


def purge_kept(conn: sqlite3.Connection, horizon_us: int | None) -> int:
    """Delete rows of the kept families behind the native horizon, oldest first, bounded.

    Args:
        conn: Connection holding the write lock.
        horizon_us: The native horizon; nothing is removed without one.

    Returns:
        int: Rows removed.

    Raises:
        sqlite3.Error: If the delete fails; the caller rolls back.
    """
    if horizon_us is None:
        return 0
    removed = 0
    for family in KEPT_FAMILIES:
        table, keys = hot_table(family), ", ".join(KEY_COLUMNS)
        removed += conn.execute(
            f"DELETE FROM {table} WHERE ({keys}) IN (SELECT {keys} FROM {table} "
            "WHERE ts_us < ? ORDER BY ts_us LIMIT ?)", (horizon_us, KEPT_PURGE_ROWS),
        ).rowcount  # fmt: skip
    return removed


def _advance_native(conn: sqlite3.Connection, cutoff: int) -> int:
    """Move the native horizon forward to ``cutoff`` and drop whole expired days.

    Args:
        conn: Connection holding the write lock.
        cutoff: The new horizon, microseconds.

    Returns:
        int: Observations expired by this pass.

    Raises:
        sqlite3.Error: If a statement fails; the caller rolls back.
    """
    previous = state(conn, HORIZON)
    expired = 0
    if previous is None or cutoff > previous:
        lo = -(1 << 62) if previous is None else previous
        expired = sum(_count_native(conn, family, lo, cutoff) for family in NATIVE_FAMILIES)
        set_state(conn, HORIZON, cutoff)
    horizon = max(cutoff, previous or cutoff)
    drop(conn, [name for name, _count in ending_by(conn, "obs_", horizon)])
    return expired


def _count_native(conn: sqlite3.Connection, family: str, lo: int, hi: int) -> int:
    """Count one family's rows with ``lo <= ts_us < hi``, one seek per series.

    Args:
        conn: Connection inside the transaction.
        family: A native family.
        lo: Start, inclusive.
        hi: End, exclusive.

    Returns:
        int: Rows in the range, staging included.

    Raises:
        sqlite3.Error: If a query fails.
    """
    driver, key = _DRIVERS.get(family, ("dict_device", "device_key"))
    total = int(conn.execute(
        f"SELECT count(*) FROM {hot_table(family)} WHERE ts_us >= ? AND ts_us < ?", (lo, hi)
    ).fetchone()[0])  # fmt: skip
    for table in tables(conn, family, lo, hi):
        total += int(conn.execute(
            f"SELECT count(*) FROM {driver} s CROSS JOIN dict_collector c CROSS JOIN {table} t "
            f"ON t.series_key = s.{key} AND t.collector_key = c.collector_key "
            "AND t.ts_us >= ? AND t.ts_us < ?",
            (lo, hi),
        ).fetchone()[0])  # fmt: skip
    return total


def _advance_rollups(conn: sqlite3.Connection, cutoff: int) -> int:
    """Move the rollup horizon forward to ``cutoff`` and drop whole expired months.

    A month is dropped once every bucket it holds ends at or before the
    horizon, which E.10 phrases as "the month's last bucket is more than
    400 days old".

    Args:
        conn: Connection holding the write lock.
        cutoff: Buckets ending at or before this expire, microseconds.

    Returns:
        int: Buckets expired by this pass.

    Raises:
        sqlite3.Error: If a statement fails; the caller rolls back.
    """
    previous = state(conn, ROLLUP_HORIZON)
    expired = 0
    if previous is None or cutoff > previous:
        lo = -(1 << 62) if previous is None else previous
        expired = sum(_count_rollups(conn, family, lo, cutoff) for family in _ROLLUP_DRIVERS)
        set_state(conn, ROLLUP_HORIZON, cutoff)
    horizon = max(cutoff, previous or cutoff)
    done = conn.execute(
        "SELECT table_name FROM partition_catalog WHERE substr(family, 1, 5) = 'roll_' "
        "AND coalesce(max_end_us, end_us) <= ?",
        (horizon,),
    )
    drop(conn, [checked(r[0]) for r in done.fetchall()])
    return expired


def _count_rollups(conn: sqlite3.Connection, family: str, lo: int, hi: int) -> int:
    """Count one family's buckets ending in ``(lo, hi]``, one seek per series.

    Args:
        conn: Connection inside the transaction.
        family: A rollup family.
        lo: Exclusive lower bound on bucket end, microseconds.
        hi: Inclusive upper bound on bucket end, microseconds.

    Returns:
        int: Metric buckets in the range.

    Raises:
        sqlite3.Error: If a query fails.
    """
    driver, key = _ROLLUP_DRIVERS[family]
    months = conn.execute(
        "SELECT table_name, start_us, coalesce(max_width_s, 0) FROM partition_catalog "
        "WHERE family = ? AND start_us < ? AND max_end_us > ?",
        (family, hi, lo),
    ).fetchall()
    total = 0
    for name, month, width in months:
        first = max(0, (lo - month) // _US - width)
        rows = conn.execute(
            "SELECT t.bucket_offset_s, t.width_adj_s, substr(t.payload, 1, 1) "
            f"FROM {driver} s CROSS JOIN dict_collector c CROSS JOIN {checked(name)} t "
            f"ON t.series_key = s.{key} AND t.collector_key = c.collector_key "
            "AND t.bucket_offset_s >= ? AND t.bucket_offset_s < ?",
            (first, -(-(hi - month) // _US)),
        )
        for offset, adj, head in rows:
            end = month + (offset + adj + ROLLUP_BUCKET_SECONDS) * _US
            if lo < end <= hi:
                total += head[0].bit_count()
    return total
