# Table names in SQL here come from fixed prefixes, UTC dates and the writer-filled
# partition catalog, never from input, so string-built SQL cannot be injected.
# ruff: noqa: S608
"""15-minute ICMP rollups with SNMP outcome counts, computed in Python (RET-006, RET-008).

The RTT distribution comes from every individual reply in the bucket (up
to 75 values: 15 polls x 5 probes), never from per-poll averages, and
``-1`` probes are excluded. Loss is not stored: it is derived from the
summed sent and received counts (RET-008). SNMP success, failure and
restart evidence counts sit in the same row so a 400-day latency graph
can still mark reloads (RET-006). Only 200 series exist, so Python is
fast enough here; interface rollups use SQL (see ``sqlc_rollup``).

Dependencies:
    sqlite3: Standard-library driver.
    sqlc_queries: ``fleet_arms``.
    sqlc_measure: The type-7 quantile.
    sqlc_icmp: Vocabulary codes for SNMP outcomes.

Safety:
    Writes only rollup partitions with ``INSERT OR REPLACE`` (restart-safe).

Example:
    >>> _distribution([300, 100, 200])
    (100, 200, 290, 300)
"""

import sqlite3
from collections import defaultdict

from tests.performance.sqlite_compact.sqlc_icmp import CONTINUITY, SNMP_STATE
from tests.performance.sqlite_compact.sqlc_measure import quantile
from tests.performance.sqlite_compact.sqlc_queries import fleet_arms
from tests.performance.sqlite_compact.sqlc_rollup import BUCKET_US, EXPECTED
from tests.performance.sqlite_compact.sqlc_schema import Layout
from vakr.domain.enums import SnmpState, UptimeContinuity

ICMP_COLS = [
    "device_key",
    "collector_key",
    "ts_us",
    "rtt_1_us",
    "rtt_2_us",
    "rtt_3_us",
    "rtt_4_us",
    "rtt_5_us",
    "sent_count",
    "received_count",
    "lost_count",
    "jitter_us",
    "quality_flags",
]
SNMP_COLS = [
    "device_key",
    "collector_key",
    "ts_us",
    "availability_state",
    "uptime_continuity",
    "quality_flags",
]
OK = SNMP_STATE[SnmpState.RESPONDING]
NOT_TRIED = (SNMP_STATE[SnmpState.SKIPPED], SNMP_STATE[SnmpState.UNKNOWN])
RESET = CONTINUITY[UptimeContinuity.RESET_DETECTED]


def _distribution(values: list[int]) -> tuple[int | None, ...]:
    """Return min, median, p95 and max of RTTs, rounded to whole microseconds.

    Args:
        values (list[int]): Successful RTTs.

    Returns:
        tuple[int | None, ...]: ``(min, median, p95, max)``, all None when empty.

    Raises:
        None
    """
    if not values:
        return None, None, None, None
    s = sorted(values)
    return s[0], round(quantile(s, 0.5)), round(quantile(s, 0.95)), s[-1]


def _bucket_row(key: tuple[int, int, int], icmp: list[tuple], snmp: list[tuple]) -> tuple:
    """Build one ICMP rollup row (``ICMP_15M`` order).

    Args:
        key (tuple[int, int, int]): ``(device_key, collector_key, bucket_start_us)``.
        icmp (list[tuple]): The bucket's ICMP rows (``ICMP_COLS``).
        snmp (list[tuple]): The bucket's SNMP rows (``SNMP_COLS``).

    Returns:
        tuple: The rollup row.

    Raises:
        None
    """
    rtts = [x for r in icmp for x in r[3:8] if x >= 0]
    jit = sorted(r[11] for r in icmp if r[11] is not None)
    q_any = 0
    for r in icmp:
        q_any |= r[12]
    for r in snmp:
        q_any |= r[5]
    return (
        *key,
        len(icmp),
        EXPECTED,
        sum(r[8] for r in icmp),
        sum(r[9] for r in icmp),
        sum(r[10] for r in icmp),
        *_distribution(rtts),
        jit[-1] if jit else None,
        round(quantile(jit, 0.95)) if jit else None,
        sum(1 for r in snmp if r[3] == OK),
        sum(1 for r in snmp if r[3] != OK and r[3] not in NOT_TRIED),
        sum(1 for r in snmp if r[4] == RESET),
        q_any,
    )


def rollup_icmp(conn: sqlite3.Connection, layout: Layout, window: tuple[int, int]) -> int:
    """Compute and store ICMP rollups for every device and position in a window.

    Args:
        conn (sqlite3.Connection): Writer connection in autocommit mode.
        layout (Layout): Candidate layout.
        window (tuple[int, int]): Bucket-aligned ``(b0, b1)`` within one UTC month.

    Returns:
        int: Rollup rows written.

    Raises:
        sqlite3.Error: If the transaction fails; it is rolled back.
    """
    b0, params = window[0], {"t0": window[0], "t1": window[1]}
    groups: dict[tuple[int, int, int], tuple[list, list]] = defaultdict(lambda: ([], []))
    for kind, cols, slot in (("icmp", ICMP_COLS, 0), ("snmp", SNMP_COLS, 1)):
        for r in conn.execute(fleet_arms(conn, layout, cols, window, kind), params):
            groups[r[0], r[1], b0 + (r[2] - b0) // BUCKET_US * BUCKET_US][slot].append(r)
    rows = [_bucket_row(k, *v) for k, v in sorted(groups.items())]
    conn.execute("BEGIN IMMEDIATE")
    try:
        table = layout.ensure(conn, "icmp_15m", b0)
        conn.executemany(
            f"INSERT OR REPLACE INTO {table} VALUES ({', '.join('?' * 18)})",
            rows,
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return len(rows)
