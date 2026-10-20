# Table names in SQL here come from fixed prefixes, UTC dates and the writer-filled
# partition catalog, never from input, so string-built SQL cannot be injected.
# ruff: noqa: S608
"""Graph, ranking, and current-state queries over the candidate layouts.

Every query resolves the tables a window needs from ``partition_catalog``
and joins them with ``UNION ALL``, so a 24-hour graph under layout C reads
one or two day tables and never touches the rest (blueprint 10.6). Series
queries seek on the clustered key ``(series, collector, ts)``. Whole-fleet
queries (top utilization, DASH-003; top errors, DASH-004; current state)
drive from the small dictionary tables with ``CROSS JOIN``, which fixes
the join order so SQLite seeks once per series instead of scanning a day.

Dependencies:
    sqlite3: Standard-library driver; window functions and, optionally,
        ``percentile_cont`` from SQLite's percentile extension.
    sqlc_schema: Table resolution and naming.
    sqlc_measure: The type-7 quantile used by the Python method.

Safety:
    Table names come from the catalog, which only the writer fills with
    names built from fixed prefixes and dates, hence the ``S608`` notes.

Example:
    >>> choose_width(0, 7 * 86_400_000_000)
    600000000
"""

import sqlite3
from collections import defaultdict

from tests.performance.sqlite_compact.sqlc_measure import quantile
from tests.performance.sqlite_compact.sqlc_schema import Layout, tables_for

LADDER_S = (60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 14400, 21600, 28800, 43200, 86400)
MAX_POINTS = 1200  # blueprint 10.6
SERIES_COLS = (
    "ts_us, in_bps, out_bps, in_pps, out_pps, in_error_delta, out_error_delta, quality_flags"
)


def choose_width(t0_us: int, t1_us: int, max_points: int = MAX_POINTS) -> int:
    """Pick the narrowest ladder bucket that keeps a window within ``max_points``.

    Args:
        t0_us (int): Window start.
        t1_us (int): Window end.
        max_points (int): Point cap per series.

    Returns:
        int: Bucket width in microseconds.

    Raises:
        None
    """
    span_s = (t1_us - t0_us) / 1e6
    return next((w for w in LADDER_S if span_s / w <= max_points), LADDER_S[-1]) * 1_000_000


def _series_arms(
    conn: sqlite3.Connection, layout: Layout, kind: str, cols: str, window: tuple[int, int]
) -> str:
    """Build one ``SELECT`` per table a series window touches, joined by ``UNION ALL``.

    Args:
        conn (sqlite3.Connection): Reader connection.
        layout (Layout): Candidate layout.
        kind (str): ``"iface"``, ``"icmp"``, ``"iface_15m"``, ...
        cols (str): Column list each arm selects.
        window (tuple[int, int]): ``(t0_us, t1_us)``.

    Returns:
        str: SQL using ``:s``, ``:c``, ``:t0``, ``:t1``; a window with no
        tables yields an always-empty ``SELECT``.

    Raises:
        sqlite3.Error: If the catalog cannot be read.
    """
    series = "interface_key" if kind.startswith("iface") else "device_key"
    ts = "bucket_start_us" if kind.endswith("15m") else "ts_us"
    tables = tables_for(conn, kind, *window)
    if layout.hot and kind in ("iface", "icmp", "snmp"):
        tables.append(Layout.hot_table(kind))
    where = f"{series} = :s AND collector_key = :c AND {ts} >= :t0 AND {ts} < :t1"
    arms = [f"SELECT {cols} FROM {t} WHERE {where}" for t in tables]
    return " UNION ALL ".join(arms) or f"SELECT {cols} FROM (SELECT 1) WHERE 0"


def raw(
    conn: sqlite3.Connection, layout: Layout, key: tuple[int, int], window: tuple[int, int]
) -> list[tuple]:
    """Return native interface samples for one series, oldest first.

    Args:
        conn (sqlite3.Connection): Reader connection.
        layout (Layout): Candidate layout.
        key (tuple[int, int]): ``(interface_key, collector_key)``.
        window (tuple[int, int]): ``(t0_us, t1_us)``.

    Returns:
        list[tuple]: Rows of ``SERIES_COLS``.

    Raises:
        sqlite3.Error: If the query fails.
    """
    sql = _series_arms(conn, layout, "iface", SERIES_COLS, window) + " ORDER BY 1"
    return conn.execute(
        sql, {"s": key[0], "c": key[1], "t0": window[0], "t1": window[1]}
    ).fetchall()


def downsampled(
    conn: sqlite3.Connection, layout: Layout, key: tuple[int, int], window: tuple[int, int]
) -> list[tuple]:
    """Return min/max/avg envelopes per bucket for one series (at most 1,200 points).

    Args:
        conn (sqlite3.Connection): Reader connection.
        layout (Layout): Candidate layout.
        key (tuple[int, int]): ``(interface_key, collector_key)``.
        window (tuple[int, int]): ``(t0_us, t1_us)``.

    Returns:
        list[tuple]: ``(bucket, samples, valid, in min/max/avg, out
        min/max/avg, flagged)``; an all-gap bucket has ``NULL`` envelopes.

    Raises:
        sqlite3.Error: If the query fails.
    """
    arms = _series_arms(conn, layout, "iface", "ts_us, in_bps, out_bps, quality_flags", window)
    sql = (
        "SELECT (ts_us - :t0) / :w AS b, COUNT(*), COUNT(in_bps), MIN(in_bps), MAX(in_bps),"
        " AVG(in_bps), MIN(out_bps), MAX(out_bps), AVG(out_bps), SUM(quality_flags != 0)"
        f" FROM ({arms}) GROUP BY b ORDER BY b"
    )
    params = {
        "s": key[0],
        "c": key[1],
        "t0": window[0],
        "t1": window[1],
        "w": choose_width(*window),
    }
    return conn.execute(sql, params).fetchall()


def rollup_range(
    conn: sqlite3.Connection, layout: Layout, key: tuple[int, int], window: tuple[int, int]
) -> list[tuple]:
    """Return a long-range graph from 15-minute rollups, re-bucketed to the point cap.

    The bucket average is weighted by valid samples, and the maximum keeps
    peaks (blueprint 10.6). A p95 of p95s is not a true p95, so the series
    returned is the highest bucket p95, labelled as such in the UI.

    Args:
        conn (sqlite3.Connection): Reader connection.
        layout (Layout): Candidate layout.
        key (tuple[int, int]): ``(interface_key, collector_key)``.
        window (tuple[int, int]): ``(t0_us, t1_us)``.

    Returns:
        list[tuple]: ``(bucket, samples, expected, valid, in max, in max-p95,
        in avg, out max, out max-p95, out avg)``.

    Raises:
        sqlite3.Error: If the query fails.
    """
    cols = (
        "bucket_start_us, sample_count, expected_count, valid_count, in_bps_max, in_bps_p95,"
        " in_bps_avg, out_bps_max, out_bps_p95, out_bps_avg"
    )
    arms = _series_arms(conn, layout, "iface_15m", cols, window)
    sql = (
        "SELECT (bucket_start_us - :t0) / :w AS b, SUM(sample_count), SUM(expected_count),"
        " SUM(valid_count), MAX(in_bps_max), MAX(in_bps_p95),"
        " SUM(in_bps_avg * valid_count) / NULLIF(SUM(valid_count), 0), MAX(out_bps_max),"
        " MAX(out_bps_p95), SUM(out_bps_avg * valid_count) / NULLIF(SUM(valid_count), 0)"
        f" FROM ({arms}) GROUP BY b ORDER BY b"
    )
    params = {
        "s": key[0],
        "c": key[1],
        "t0": window[0],
        "t1": window[1],
        "w": choose_width(*window),
    }
    return conn.execute(sql, params).fetchall()


def fleet_arms(
    conn: sqlite3.Connection,
    layout: Layout,
    cols: list[str],
    window: tuple[int, int],
    kind: str = "iface",
) -> str:
    """Select ``cols`` for every series of ``kind`` in a window, one seek per series.

    Args:
        conn (sqlite3.Connection): Reader connection.
        layout (Layout): Candidate layout.
        cols (list[str]): Columns to select.
        window (tuple[int, int]): ``(t0_us, t1_us)``.
        kind (str): ``"iface"`` (driven by interfaces) or ``"icmp"``/``"snmp"``
            (driven by devices).

    Returns:
        str: ``UNION ALL`` SQL using ``:t0`` and ``:t1``.

    Raises:
        sqlite3.Error: If the catalog cannot be read.
    """
    pick = ", ".join(f"t.{c}" for c in cols)
    series, dictionary = (
        ("interface_key", "dict_interface") if kind == "iface" else ("device_key", "dict_device")
    )
    arms = [
        f"SELECT {pick} FROM {dictionary} d CROSS JOIN dict_collector c CROSS JOIN {t} t"
        f" ON t.{series} = d.{series} AND t.collector_key = c.collector_key"
        " AND t.ts_us >= :t0 AND t.ts_us < :t1"
        for t in tables_for(conn, kind, *window)
    ]
    if layout.hot:
        arms.append(
            f"SELECT {', '.join(cols)} FROM {Layout.hot_table(kind)} t"
            " WHERE ts_us >= :t0 AND ts_us < :t1"
        )
    return " UNION ALL ".join(arms)


def top_utilization(
    conn: sqlite3.Connection,
    layout: Layout,
    window: tuple[int, int],
    method: str = "builtin",
    limit: int = 25,
) -> list[tuple]:
    """Rank series by the higher of inbound/outbound p95 utilization (DASH-003).

    Args:
        conn (sqlite3.Connection): Reader connection.
        layout (Layout): Candidate layout.
        window (tuple[int, int]): ``(t0_us, t1_us)``, 15 minutes by default.
        method (str): ``"builtin"`` (``percentile_cont``), ``"window"``
            (portable window functions), or ``"python"``.
        limit (int): Rows to return.

    Returns:
        list[tuple]: ``(interface_key, collector_key, samples, valid, p95 in,
        p95 out, utilization)``, highest first; series with no valid rate
        are excluded (DASH-005).

    Raises:
        ValueError: If ``method`` is unknown.
        sqlite3.Error: If the query fails.
    """
    arms = fleet_arms(conn, layout, ["interface_key", "collector_key", "in_bps", "out_bps"], window)
    params = {"t0": window[0], "t1": window[1], "n": limit}
    if method == "python":
        return _top_util_python(conn, arms, params, limit)
    if method == "builtin":
        body = (
            "SELECT interface_key, collector_key, COUNT(*) AS n, COUNT(in_bps) AS valid,"
            " percentile_cont(in_bps, 0.95) AS p_in, percentile_cont(out_bps, 0.95) AS p_out"
            " FROM w GROUP BY 1, 2"
        )
    elif method == "window":
        body = _P95_WINDOW
    else:
        raise ValueError(f"unknown method {method!r}")
    sql = (
        f"WITH w AS ({arms}), p AS ({body})"
        " SELECT p.interface_key, p.collector_key, n, valid, p_in, p_out,"
        " max(p_in, p_out) / cap.capacity_bps AS util FROM p"
        " JOIN bench_capacity cap ON cap.interface_key = p.interface_key WHERE valid > 0"
        " ORDER BY util DESC, p.interface_key, p.collector_key LIMIT :n"
    )
    return conn.execute(sql, params).fetchall()


# Hyndman-Fan type 7, the rule percentile_cont uses: h = (n - 1) * 0.95, then
# interpolate between ranks floor(h) + 1 and floor(h) + 2.
_RANKED = """SELECT interface_key, collector_key, in_bps, out_bps, COUNT(*) OVER s AS n,
  ROW_NUMBER() OVER (s ORDER BY in_bps NULLS LAST) AS ri, COUNT(in_bps) OVER s AS ni,
  ROW_NUMBER() OVER (s ORDER BY out_bps NULLS LAST) AS ro, COUNT(out_bps) OVER s AS nx
  FROM w WINDOW s AS (PARTITION BY interface_key, collector_key)"""
_P95_WINDOW = f"""SELECT interface_key, collector_key, n, valid,
  in_lo + in_f * (in_hi - in_lo) AS p_in, out_lo + out_f * (out_hi - out_lo) AS p_out FROM (
 SELECT interface_key, collector_key, MAX(n) AS n, MAX(ni) AS valid,
  MAX(CASE WHEN ri = CAST((ni - 1) * 0.95 AS INTEGER) + 1 THEN in_bps END) AS in_lo,
  MAX(CASE WHEN ri = MIN(CAST((ni - 1) * 0.95 AS INTEGER) + 2, ni) THEN in_bps END) AS in_hi,
  MAX((ni - 1) * 0.95 - CAST((ni - 1) * 0.95 AS INTEGER)) AS in_f,
  MAX(CASE WHEN ro = CAST((nx - 1) * 0.95 AS INTEGER) + 1 THEN out_bps END) AS out_lo,
  MAX(CASE WHEN ro = MIN(CAST((nx - 1) * 0.95 AS INTEGER) + 2, nx) THEN out_bps END) AS out_hi,
  MAX((nx - 1) * 0.95 - CAST((nx - 1) * 0.95 AS INTEGER)) AS out_f
 FROM ({_RANKED}) GROUP BY 1, 2)"""


def _top_util_python(
    conn: sqlite3.Connection, arms: str, params: dict[str, int], limit: int
) -> list[tuple]:
    """Compute the DASH-003 ranking in Python from the window's raw rates.

    Args:
        conn (sqlite3.Connection): Reader connection.
        arms (str): SQL from ``fleet_arms``.
        params (dict[str, int]): ``t0`` and ``t1``.
        limit (int): Rows to return.

    Returns:
        list[tuple]: Same shape as ``top_utilization``.

    Raises:
        sqlite3.Error: If the query fails.
    """
    cap = dict(conn.execute("SELECT interface_key, capacity_bps FROM bench_capacity"))
    groups: dict[tuple[int, int], tuple[list[float], list[float], list[int]]] = defaultdict(
        lambda: ([], [], [0])
    )
    for ik, ck, i, o in conn.execute(arms, params):
        g = groups[ik, ck]
        g[2][0] += 1
        if i is not None:
            g[0].append(i)
        if o is not None:
            g[1].append(o)
    out = []
    for (ik, ck), (ins, outs, n) in groups.items():
        if ins and outs:
            p_in, p_out = quantile(sorted(ins), 0.95), quantile(sorted(outs), 0.95)
            out.append((ik, ck, n[0], len(ins), p_in, p_out, max(p_in, p_out) / cap[ik]))
    out.sort(key=lambda r: (-r[6], r[0], r[1]))
    return out[:limit]


def top_errors(
    conn: sqlite3.Connection, layout: Layout, window: tuple[int, int], limit: int = 25
) -> list[tuple]:
    """Rank series by valid in+out error deltas over a window (DASH-004).

    Only deltas from valid baselines count; raw cumulative counters never
    rank. Errors per million packets uses packets derived from rates and
    elapsed time, so it is ``NULL`` when packet data is missing.

    Args:
        conn (sqlite3.Connection): Reader connection.
        layout (Layout): Candidate layout.
        window (tuple[int, int]): ``(t0_us, t1_us)``.
        limit (int): Rows to return.

    Returns:
        list[tuple]: ``(interface_key, collector_key, errors, samples, valid, ppm)``.

    Raises:
        sqlite3.Error: If the query fails.
    """
    cols = [
        "interface_key",
        "collector_key",
        "in_error_delta",
        "out_error_delta",
        "in_pps",
        "out_pps",
        "sample_elapsed_us",
    ]
    sql = (
        f"WITH w AS ({fleet_arms(conn, layout, cols, window)})"
        " SELECT interface_key, collector_key, SUM(in_error_delta + out_error_delta) AS e,"
        " COUNT(*), COUNT(in_error_delta),"
        " SUM(in_error_delta + out_error_delta) * 1e6"
        " / NULLIF(SUM((in_pps + out_pps) * sample_elapsed_us) / 1e6, 0)"
        " FROM w GROUP BY 1, 2 HAVING e > 0 ORDER BY e DESC, 1, 2 LIMIT :n"
    )
    return conn.execute(sql, {"t0": window[0], "t1": window[1], "n": limit}).fetchall()


def icmp_raw(
    conn: sqlite3.Connection, layout: Layout, key: tuple[int, int], window: tuple[int, int]
) -> list[tuple]:
    """Return the raw five-probe RTTs for one device and position (latency "smoke").

    Args:
        conn (sqlite3.Connection): Reader connection.
        layout (Layout): Candidate layout.
        key (tuple[int, int]): ``(device_key, collector_key)``.
        window (tuple[int, int]): ``(t0_us, t1_us)``.

    Returns:
        list[tuple]: ``(ts, rtt_1..rtt_5, received, median, state, quality)``.

    Raises:
        sqlite3.Error: If the query fails.
    """
    cols = (
        "ts_us, rtt_1_us, rtt_2_us, rtt_3_us, rtt_4_us, rtt_5_us, received_count,"
        " median_rtt_us, state, quality_flags"
    )
    sql = _series_arms(conn, layout, "icmp", cols, window) + " ORDER BY 1"
    return conn.execute(
        sql, {"s": key[0], "c": key[1], "t0": window[0], "t1": window[1]}
    ).fetchall()


def icmp_rollup_range(
    conn: sqlite3.Connection, layout: Layout, key: tuple[int, int], window: tuple[int, int]
) -> list[tuple]:
    """Return a long-range latency and loss graph from ICMP rollups.

    Loss is derived from summed counts, never averaged percentages (RET-008).

    Args:
        conn (sqlite3.Connection): Reader connection.
        layout (Layout): Candidate layout.
        key (tuple[int, int]): ``(device_key, collector_key)``.
        window (tuple[int, int]): ``(t0_us, t1_us)``.

    Returns:
        list[tuple]: ``(bucket, polls, sent, lost, loss fraction, min RTT,
        highest p95 RTT, max RTT, restarts)``.

    Raises:
        sqlite3.Error: If the query fails.
    """
    cols = (
        "bucket_start_us, sample_count, sent_sum, lost_sum, rtt_min_us, rtt_p95_us,"
        " rtt_max_us, restart_count"
    )
    arms = _series_arms(conn, layout, "icmp_15m", cols, window)
    sql = (
        "SELECT (bucket_start_us - :t0) / :w AS b, SUM(sample_count), SUM(sent_sum),"
        " SUM(lost_sum), 1.0 * SUM(lost_sum) / NULLIF(SUM(sent_sum), 0), MIN(rtt_min_us),"
        " MAX(rtt_p95_us), MAX(rtt_max_us), SUM(restart_count)"
        f" FROM ({arms}) GROUP BY b ORDER BY b"
    )
    params = {
        "s": key[0],
        "c": key[1],
        "t0": window[0],
        "t1": window[1],
        "w": choose_width(*window),
    }
    return conn.execute(sql, params).fetchall()
