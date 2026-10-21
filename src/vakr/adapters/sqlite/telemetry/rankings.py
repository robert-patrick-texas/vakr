"""``RankingQuery`` for the SQLite telemetry store: projected units plus native scans (M4, 12.9).

A ranking needs every interface's samples from one collector over a window
(E.17 #13). Inside one read transaction (one snapshot) it:

1. lists the projected hours and 15-minute buckets lying wholly inside
   the window, at or after the native horizon (``rank_unit``, E.28);
2. covers the window with them, hours first, and reads their stored
   partials, one ``rank_iface`` key range per run of consecutive units of
   one width, collector first (MPOS-008);
3. scans the rest of the window natively: the head before the first whole
   bucket, the tail after the newest projected one, and any bucket that is
   not projected (``scan``);
4. folds everything into one ``Partial`` per interface and takes the
   nearest-rank p95 with ``vakr.logic.stats.p95``, as rollups do.

A listed unit always equals its native samples in the same snapshot: the
projector writes a bucket in the transaction that read its samples (and an
hour from its four listed buckets), and the writer unlists both in the
transaction that adds a sample to them. So the answer is exactly a native
scan's, only cheaper: at MVP scale a 24-hour ranking reads about 24 hour
rows and a few bucket rows per interface instead of 1,440 samples. Without
a projection (before the collector built one) every bucket is scanned
natively. Neither
``percentile_disc`` nor a window-function sort matches nearest-rank
cheaply: the window-function form measured about four times slower at MVP
scale (``docs/architecture/queries.md``).

Dependencies:
    sqlite3: Statements on the read transaction.
    vakr.adapters.sqlite.telemetry.*: Read helpers, keys, and partials.
    vakr.ports.telemetry.rankings: Result types and bounds.

Safety:
    Bounded by the window (at most one day: 96 buckets, so at most 97
    native intervals and as many runs) and the row limit; every value is a
    bound parameter.

Example:
    >>> hour, quarter = HOUR_US, BUCKET_US
    >>> native, runs = _plan((0, 2 * hour), {0}, {hour + quarter})
    >>> [(a // quarter, b // quarter) for a, b in native]
    [(4, 5), (6, 8)]
    >>> [(w // quarter, a // quarter, b // quarter) for w, a, b in runs]
    [(4, 0, 4), (1, 5, 6)]
"""

# Arm SQL interpolates checked partition names only; values are bound parameters.
# ruff: noqa: S608

import sqlite3

from vakr.adapters.sqlite.telemetry.keys import find_key, find_uuid
from vakr.adapters.sqlite.telemetry.layout import hot_table
from vakr.adapters.sqlite.telemetry.ranking_parts import Partial
from vakr.adapters.sqlite.telemetry.reads import native_start, sources
from vakr.adapters.sqlite.telemetry.store import SqliteTelemetryStore
from vakr.domain.ids import CollectorId, InterfaceId
from vakr.domain.rollups import ROLLUP_BUCKET_SECONDS
from vakr.domain.series import TimeWindow
from vakr.domain.timestamps import to_epoch_us
from vakr.ports.telemetry.rankings import (
    MAX_RANKED_INTERFACES,
    RankingScan,
    check_ranking_bounds,
)

FAMILY = "obs_iface"
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
    f"group_concat(CASE WHEN {_PACKETS} THEN (in_pps + out_pps) * sample_elapsed_us END)"
)

BUCKET_US = ROLLUP_BUCKET_SECONDS * 1_000_000
"""One projected bucket (E.10) in microseconds."""

HOUR_US = 4 * BUCKET_US
"""One projected hour, compacted from its four buckets, in microseconds."""

type Span = tuple[int, int]
type Run = tuple[int, int, int]


def listed(conn: sqlite3.Connection, width_us: int, bounds: Span) -> list[int]:
    """List projected units of one width lying wholly inside ``[start, end)``, oldest first.

    Args:
        conn: Connection inside the caller's transaction.
        width_us: ``BUCKET_US`` or ``HOUR_US``.
        bounds: ``(start_us, end_us)``.

    Returns:
        list[int]: Unit starts, microseconds.

    Raises:
        sqlite3.Error: If the manifest cannot be read.
    """
    rows = conn.execute(
        "SELECT start_us FROM rank_unit WHERE width_s = ? AND start_us >= ? AND start_us <= ? "
        "ORDER BY start_us",
        (width_us // 1_000_000, bounds[0], bounds[1] - width_us),
    )
    return [int(r[0]) for r in rows]


def _unit_at(at: int, end: int, hours: set[int], buckets: set[int]) -> int:
    """Return the width of the projected unit starting at ``at`` and ending by ``end``.

    Args:
        at: A time, microseconds.
        end: The window end.
        hours: Listed hour starts.
        buckets: Listed bucket starts.

    Returns:
        int: ``HOUR_US``, ``BUCKET_US``, or 0 when no listed unit fits there.

    Raises:
        None
    """
    if at % HOUR_US == 0 and at + HOUR_US <= end and at in hours:
        return HOUR_US
    if at % BUCKET_US == 0 and at + BUCKET_US <= end and at in buckets:
        return BUCKET_US
    return 0


def _plan(bounds: Span, hours: set[int], buckets: set[int]) -> tuple[list[Span], list[Run]]:
    """Split a window into native intervals and runs of projected units, hours first.

    Args:
        bounds: ``(start_us, end_us)``.
        hours: Listed hour starts inside the window.
        buckets: Listed bucket starts inside the window.

    Returns:
        tuple: Native ``(start, end)`` intervals and ``(width_us, start, end)``
        runs, each ascending and together covering the window exactly once.

    Raises:
        None
    """
    native: list[Span] = []
    runs: list[Run] = []
    at, end = bounds
    while at < end:
        width = _unit_at(at, end, hours, buckets)
        step = width or min(end, at - at % BUCKET_US + BUCKET_US) - at
        if width and runs and runs[-1][0] == width and runs[-1][2] == at:
            runs[-1] = (width, runs[-1][1], at + step)
        elif width:
            runs.append((width, at, at + step))
        elif native and native[-1][1] == at:
            native[-1] = (native[-1][0], at + step)
        else:
            native.append((at, at + step))
        at += step
    return native, runs


def _read_runs(
    conn: sqlite3.Connection, collector: int, runs: list[Run], into: dict[int, Partial]
) -> None:
    """Fold the stored partials of projected runs into ``into``.

    Args:
        conn: Connection inside the caller's transaction.
        collector: The collector's key.
        runs: ``(width_us, start, end)`` runs.
        into: Partials by interface key, extended in place.

    Returns:
        None

    Raises:
        sqlite3.Error: If a query fails.
        StorageError: If a payload is damaged.
    """
    for width, *span in runs:
        rows = conn.execute(
            "SELECT series_key, payload FROM rank_iface WHERE collector_key = ? AND width_s = ? "
            "AND start_us >= ? AND start_us < ?",
            (collector, width // 1_000_000, *span),
        )
        for key, payload in rows:
            part = into.get(key)
            if part is None:
                part = into[key] = Partial()
            part.add_payload(payload)


def scan(
    conn: sqlite3.Connection, collector: int, bounds: tuple[int, int], into: dict[int, Partial]
) -> None:
    """Fold one collector's native samples in ``[start, end)`` into ``into``, by series key.

    One grouped statement per partition arm: a day partition leads with
    ``series_key``, so naming every interface key turns it into key ranges
    grouped without a sort; the staging table is time-first and small.

    Args:
        conn: Connection inside the caller's transaction.
        collector: The collector's key.
        bounds: ``(start_us, end_us)``, already clamped to the native horizon.
        into: Partials by interface key, extended in place.

    Returns:
        None

    Raises:
        sqlite3.Error: If a query fails.
        ValueError: If a stored value is not a number.
    """
    staging = hot_table(FAMILY)
    for name in sources(conn, FAMILY, *bounds):
        keyed = "" if name == staging else _EVERY_INTERFACE
        rows = conn.execute(
            f"SELECT {_AGGREGATES} FROM {name} WHERE {keyed}collector_key = ? AND ts_us >= ? "
            "AND ts_us < ? GROUP BY series_key",
            (collector, *bounds),
        )
        for key, *rest in rows:
            part = into.get(key)
            if part is None:
                part = into[key] = Partial()
            part.add_row(tuple(rest))


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
        StorageError: If a dictionary key is missing or a payload is damaged.
        sqlite3.Error: If a query fails.
    """
    check_ranking_bounds(window.duration.total_seconds(), limit)
    with store.reading() as conn:
        collector = find_key(conn, store.keys, "collector", collector_id)
        start, end = native_start(conn, to_epoch_us(window.start)), to_epoch_us(window.end)
        if collector is None or start >= end:
            return RankingScan((), False)
        hours = set(listed(conn, HOUR_US, (start, end)))
        native, runs = _plan((start, end), hours, set(listed(conn, BUCKET_US, (start, end))))
        merged: dict[int, Partial] = {}
        _read_runs(conn, collector, runs, merged)
        for interval in native:
            scan(conn, collector, interval, merged)
        named = [(InterfaceId(find_uuid(conn, store.keys, "interface", k)), m)
                 for k, m in merged.items()]  # fmt: skip
    named.sort(key=lambda pair: str(pair[0]))
    items = tuple(m.summary(iface) for iface, m in named[:limit])
    return RankingScan(items, len(named) > limit)
