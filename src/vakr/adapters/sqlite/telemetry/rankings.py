"""``RankingQuery`` for the SQLite telemetry store: one grouped scan per partition (M4, 12.9).

A ranking needs every interface's samples from one collector over a window.
The day partitions are clustered on ``(series_key, collector_key, ts_us)``,
so each partition arm drives its primary key with ``series_key IN (every
interface key)`` and groups in key order without a sort; the staging table
is time-first and small. Each arm returns one row per interface: counts,
error sums, packet sums, and the available ``in_bps`` / ``out_bps`` values
as text (SQLite 3.53 writes a double with 17 significant digits, so the
text round-trips exactly). Python merges the arms and takes the
nearest-rank p95 with ``vakr.logic.stats.p95``, the function rollups use
(E.17 #13). Neither ``percentile_disc`` nor a window-function sort matches
nearest-rank cheaply: the window-function form measured about four times
slower at MVP scale (``docs/architecture/queries.md``).

Values are held as ``array('d')`` (8 bytes each), so a 24-hour window at
MVP scale (about 2.3 million samples per collector) needs about 37 MB
while it is ranked.

Dependencies:
    sqlite3, array: Statements and compact value buffers.
    vakr.adapters.sqlite.telemetry.*: Read helpers, layout, and keys.
    vakr.logic.stats: The shared nearest-rank p95.
    vakr.ports.telemetry.rankings: Result types and bounds.

Safety:
    Bounded by the window (at most one day) and the row limit; table names
    come from the catalog and every value is a bound parameter.

Example:
    >>> merged = _Merged()
    >>> merged.add((3, "1.5,2.5", None, 2, 7, 0, 2, 120000000.0))
    >>> merged.ins, merged.samples, merged.in_errors
    (array('d', [1.5, 2.5]), 3, 7)
"""

# Arm SQL interpolates checked partition names only; values are bound parameters.
# ruff: noqa: S608

import sqlite3
from array import array
from dataclasses import dataclass, field

from vakr.adapters.sqlite.telemetry.keys import find_key, find_uuid
from vakr.adapters.sqlite.telemetry.layout import hot_table
from vakr.adapters.sqlite.telemetry.reads import native_start, sources
from vakr.adapters.sqlite.telemetry.store import SqliteTelemetryStore
from vakr.domain.ids import CollectorId, InterfaceId
from vakr.domain.series import TimeWindow
from vakr.domain.timestamps import to_epoch_us
from vakr.logic.stats import p95
from vakr.ports.telemetry.rankings import (
    MAX_RANKED_INTERFACES,
    RankingScan,
    WindowSummary,
    check_ranking_bounds,
)

_US = 1_000_000
_FAMILY = "obs_iface"
_BOTH = "in_error_delta IS NOT NULL AND out_error_delta IS NOT NULL"
_PACKETS = (
    f"{_BOTH} AND in_pps IS NOT NULL AND out_pps IS NOT NULL AND sample_elapsed_us IS NOT NULL"
)
_EVERY_INTERFACE = "series_key IN (SELECT interface_key FROM dict_interface) AND "
_AGGREGATES = (
    "series_key, count(*), group_concat(in_bps), group_concat(out_bps), "
    f"count(CASE WHEN {_BOTH} THEN 1 END), "
    f"coalesce(sum(CASE WHEN {_BOTH} THEN in_error_delta END), 0), "
    f"coalesce(sum(CASE WHEN {_BOTH} THEN out_error_delta END), 0), "
    f"count(CASE WHEN {_PACKETS} THEN 1 END), "
    f"total(CASE WHEN {_PACKETS} THEN (in_pps + out_pps) * sample_elapsed_us END)"
)


@dataclass(slots=True)
class _Merged:
    """One interface's partial aggregates, merged across partition arms.

    Attributes:
        samples: Observations seen.
        ins: Available inbound rates.
        outs: Available outbound rates.
        counted: Observations with both error deltas.
        in_errors: Their summed inbound deltas.
        out_errors: Their summed outbound deltas.
        with_packets: Counted observations that also have packet rates and elapsed time.
        packet_us: Sum of ``(in_pps + out_pps) x elapsed_us`` over those.
    """

    samples: int = 0
    ins: array = field(default_factory=lambda: array("d"))
    outs: array = field(default_factory=lambda: array("d"))
    counted: int = 0
    in_errors: int = 0
    out_errors: int = 0
    with_packets: int = 0
    packet_us: float = 0.0

    def add(self, row: tuple) -> None:
        """Fold one arm's row (without its series key) in.

        Args:
            row: ``(count, in text, out text, counted, in errors, out errors,
                with packets, packet sum)``.

        Returns:
            None

        Raises:
            ValueError: If a stored rate is not a number (damaged storage).
        """
        n, ins, outs, counted, in_err, out_err, with_packets, packet_us = row
        self.samples += n
        if ins:
            self.ins.extend(map(float, ins.split(",")))
        if outs:
            self.outs.extend(map(float, outs.split(",")))
        self.counted += counted
        self.in_errors += in_err
        self.out_errors += out_err
        self.with_packets += with_packets
        self.packet_us += packet_us

    def summary(self, interface_id: InterfaceId) -> WindowSummary:
        """Return the port's summary of the merged aggregates.

        Args:
            interface_id: The interface the series key names.

        Returns:
            WindowSummary: Counts, p95 rates, and error totals.

        Raises:
            None
        """
        packets = None
        if self.counted and self.with_packets == self.counted:
            packets = round(self.packet_us / _US)
        return WindowSummary(
            interface_id, self.samples, len(self.ins), len(self.outs),
            p95(self.ins.tolist()) if self.ins else None,
            p95(self.outs.tolist()) if self.outs else None,
            self.counted, self.in_errors, self.out_errors, packets,
        )  # fmt: skip


def _scan(conn: sqlite3.Connection, collector: int, bounds: tuple[int, int]) -> dict[int, _Merged]:
    """Run one grouped statement per partition arm and merge by series key.

    Args:
        conn: Connection inside the read transaction.
        collector: The collector's key.
        bounds: ``(start_us, end_us)``, already clamped to the native horizon.

    Returns:
        dict[int, _Merged]: Merged aggregates per interface key.

    Raises:
        sqlite3.Error: If a query fails.
        ValueError: If a stored rate is not a number.
    """
    merged: dict[int, _Merged] = {}
    staging = hot_table(_FAMILY)
    for name in sources(conn, _FAMILY, *bounds):
        # Day partitions lead with series_key; naming every key turns the scan into key ranges.
        keyed = "" if name == staging else _EVERY_INTERFACE
        rows = conn.execute(
            f"SELECT {_AGGREGATES} FROM {name} WHERE {keyed}collector_key = ? AND ts_us >= ? "
            "AND ts_us < ? GROUP BY series_key",
            (collector, *bounds),
        )
        for key, *rest in rows:
            merged.setdefault(key, _Merged()).add(tuple(rest))
    return merged


def window_summaries(
    store: SqliteTelemetryStore, collector_id: CollectorId, window: TimeWindow,
    limit: int = MAX_RANKED_INTERFACES,
) -> RankingScan:  # fmt: skip
    """Return every interface's p95 rates and error totals; see ``RankingQuery``.

    Args:
        store: The open store.
        collector_id: The one collector whose samples count.
        window: Bounded window, at most one day.
        limit: Most rows, 1 to ``MAX_RANKED_INTERFACES``.

    Returns:
        RankingScan: Rows ordered by interface UUID text.

    Raises:
        ValidationError: If the window or limit is out of bounds.
        StorageError: If a dictionary key is missing (damaged storage).
        sqlite3.Error: If a query fails.
    """
    check_ranking_bounds(window.duration.total_seconds(), limit)
    with store.reading() as conn:
        collector = find_key(conn, store.keys, "collector", collector_id)
        start, end = native_start(conn, to_epoch_us(window.start)), to_epoch_us(window.end)
        if collector is None or start >= end:
            return RankingScan((), False)
        merged = _scan(conn, collector, (start, end))
        named = [(InterfaceId(find_uuid(conn, store.keys, "interface", k)), m)
                 for k, m in merged.items()]  # fmt: skip
    named.sort(key=lambda pair: str(pair[0]))
    items = tuple(m.summary(iface) for iface, m in named[:limit])
    return RankingScan(items, len(named) > limit)
