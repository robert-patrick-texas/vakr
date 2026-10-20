# Table names in SQL here come from fixed prefixes, UTC dates and the writer-filled
# partition catalog, never from input, so string-built SQL cannot be injected.
# ruff: noqa: S608
"""15-minute interface and ICMP rollups computed from native rows (RET-003..006, RET-009).

Interface rollups keep sample, expected, valid-rate and available counts,
in/out maximum, p95 and average bit rates, error sums, and a quality
summary (RET-004). SQLite has no ``bit_or`` aggregate, so ``quality_any``
ORs one ``MAX(flags & bit)`` per defined ``Quality`` bit. The p95 uses the
type-7 rule (linear interpolation, as ``percentile_cont``) computed three
ways so their cost can be compared: the built-in percentile extension,
portable window functions, and Python. Rows are written with
``INSERT OR REPLACE``, so re-running a bucket after a crash is safe.

Dependencies:
    sqlite3: Standard-library driver.
    sqlc_queries: ``fleet_arms`` (one seek per series).
    sqlc_measure: The type-7 quantile.
    sqlc_schema: Rollup partition names.
    vakr.domain.quality: The defined quality bits.

Safety:
    Writes only rollup partitions; native rows are never modified.

Example:
    >>> bit_or_sql("q").count("MAX(")
    23
"""

import sqlite3
from collections import defaultdict

from tests.performance.sqlite_compact.sqlc_measure import quantile
from tests.performance.sqlite_compact.sqlc_queries import fleet_arms
from tests.performance.sqlite_compact.sqlc_schema import Layout
from vakr.domain.quality import Quality

BUCKET_US = 900_000_000
EXPECTED = 15  # 900 s / 60 s polling interval
NATIVE_COLS = [
    "interface_key",
    "collector_key",
    "ts_us",
    "device_key",
    "oper_status",
    "in_bps",
    "out_bps",
    "in_error_delta",
    "out_error_delta",
    "quality_flags",
]


def bit_or_sql(col: str) -> str:
    """Return an aggregate expression equal to the bitwise OR of ``col`` over a group.

    Args:
        col (str): Integer column name.

    Returns:
        str: ``MAX(col & 1) | MAX(col & 2) | ...`` over every defined bit.

    Raises:
        None
    """
    bits = [int(q) for q in Quality if q is not Quality.NONE]
    return " | ".join(f"MAX({col} & {b})" for b in bits)


# COUNT(CASE ...) rather than SUM(oper_status = 1): a bucket inside an outage
# has only NULL statuses, where SUM yields NULL but availability is 0, not unknown.
_COMMON = (
    f"MAX(device_key), COUNT(*), {EXPECTED}, COUNT(in_bps),"
    " COUNT(CASE WHEN oper_status = 1 THEN 1 END),"
    " MAX(in_bps), {p_in}, AVG(in_bps), MAX(out_bps), {p_out}, AVG(out_bps),"
    " SUM(in_error_delta), SUM(out_error_delta), SUM(quality_flags != 0), "
    + bit_or_sql("quality_flags")
)
_BUCKET = f"(:b0 + ((ts_us - :b0) / {BUCKET_US}) * {BUCKET_US})"


def _window_sql(arms: str) -> str:
    """Build the portable window-function rollup query (no percentile extension).

    Args:
        arms (str): Native rows for the window, from ``fleet_arms``.

    Returns:
        str: SQL producing rollup rows in ``IFACE_15M`` order.

    Raises:
        None
    """
    ranked = (
        f"SELECT *, {_BUCKET} AS bk, ROW_NUMBER() OVER (s ORDER BY in_bps NULLS LAST) AS ri,"
        " COUNT(in_bps) OVER s AS ni,"
        " ROW_NUMBER() OVER (s ORDER BY out_bps NULLS LAST) AS ro, COUNT(out_bps) OVER s AS nx"
        f" FROM ({arms}) WINDOW s AS (PARTITION BY interface_key, collector_key, {_BUCKET})"
    )

    def pick(rank: str, n: str, col: str, off: int) -> str:
        """Return the value at the lower (off=1) or upper (off=2) interpolation rank.

        Args:
            rank (str): Row-number column.
            n (str): Valid-count column.
            col (str): Value column.
            off (int): 1 or 2.

        Returns:
            str: Aggregate SQL expression.

        Raises:
            None
        """
        target = f"CAST(({n} - 1) * 0.95 AS INTEGER) + {off}"
        target = target if off == 1 else f"MIN({target}, {n})"
        return f"MAX(CASE WHEN {rank} = {target} THEN {col} END)"

    def p95(rank: str, n: str, col: str) -> str:
        """Return the type-7 p95 expression for one direction.

        Args:
            rank (str): Row-number column.
            n (str): Valid-count column.
            col (str): Value column.

        Returns:
            str: Aggregate SQL expression.

        Raises:
            None
        """
        lo, hi = pick(rank, n, col, 1), pick(rank, n, col, 2)
        frac = f"MAX(({n} - 1) * 0.95 - CAST(({n} - 1) * 0.95 AS INTEGER))"
        return f"({lo} + {frac} * ({hi} - {lo}))"

    body = _COMMON.format(p_in=p95("ri", "ni", "in_bps"), p_out=p95("ro", "nx", "out_bps"))
    return (
        f"SELECT interface_key, collector_key, bk, {body} FROM ({ranked})"
        " GROUP BY interface_key, collector_key, bk"
    )


def rollup_sql(
    conn: sqlite3.Connection, layout: Layout, window: tuple[int, int], method: str
) -> str:
    """Return the SQL that computes interface rollup rows for a window.

    Args:
        conn (sqlite3.Connection): Connection to the telemetry database.
        layout (Layout): Candidate layout.
        window (tuple[int, int]): Bucket-aligned ``(b0, b1)``.
        method (str): ``"builtin"`` or ``"window"``.

    Returns:
        str: SQL using ``:t0``, ``:t1``, ``:b0``.

    Raises:
        ValueError: If ``method`` is unknown.
    """
    arms = fleet_arms(conn, layout, NATIVE_COLS, window)
    if method == "window":
        return _window_sql(arms)
    if method != "builtin":
        raise ValueError(f"unknown method {method!r}")
    body = _COMMON.format(
        p_in="percentile_cont(in_bps, 0.95)", p_out="percentile_cont(out_bps, 0.95)"
    )
    return (
        f"SELECT interface_key, collector_key, {_BUCKET} AS bk, {body}"
        f" FROM ({arms}) GROUP BY interface_key, collector_key, bk"
    )


def _python_rows(conn: sqlite3.Connection, layout: Layout, window: tuple[int, int]) -> list[tuple]:
    """Compute interface rollup rows in Python from the window's native rows.

    Args:
        conn (sqlite3.Connection): Connection to the telemetry database.
        layout (Layout): Candidate layout.
        window (tuple[int, int]): Bucket-aligned ``(b0, b1)``.

    Returns:
        list[tuple]: Rollup rows in ``IFACE_15M`` order.

    Raises:
        sqlite3.Error: If the read fails.
    """
    groups: dict[tuple[int, int, int], list[tuple]] = defaultdict(list)
    b0 = window[0]
    sql = fleet_arms(conn, layout, NATIVE_COLS, window)
    for r in conn.execute(sql, {"t0": window[0], "t1": window[1]}):
        groups[r[0], r[1], b0 + (r[2] - b0) // BUCKET_US * BUCKET_US].append(r)
    out = []
    for (ik, ck, bk), rows in sorted(groups.items()):
        ins = sorted(r[5] for r in rows if r[5] is not None)
        outs = sorted(r[6] for r in rows if r[6] is not None)
        flags = [r[9] for r in rows]
        q_any = 0
        for f in flags:
            q_any |= f
        e_in = [r[7] for r in rows if r[7] is not None]
        e_out = [r[8] for r in rows if r[8] is not None]
        out.append(
            (
                ik,
                ck,
                bk,
                max(r[3] for r in rows),
                len(rows),
                EXPECTED,
                len(ins),
                sum(1 for r in rows if r[4] == 1),
                ins[-1] if ins else None,
                quantile(ins, 0.95) if ins else None,
                sum(ins) / len(ins) if ins else None,
                outs[-1] if outs else None,
                quantile(outs, 0.95) if outs else None,
                sum(outs) / len(outs) if outs else None,
                sum(e_in) if e_in else None,
                sum(e_out) if e_out else None,
                sum(1 for f in flags if f),
                q_any,
            )
        )
    return out


def rollup_interfaces(
    conn: sqlite3.Connection, layout: Layout, window: tuple[int, int], method: str
) -> int:
    """Compute and store interface rollups for every series in a window (one transaction).

    Args:
        conn (sqlite3.Connection): Writer connection in autocommit mode.
        layout (Layout): Candidate layout.
        window (tuple[int, int]): Bucket-aligned ``(b0, b1)`` within one UTC month.
        method (str): ``"builtin"``, ``"window"``, or ``"python"``.

    Returns:
        int: Rollup rows written.

    Raises:
        ValueError: If ``method`` is unknown.
        sqlite3.Error: If the transaction fails; it is rolled back.
    """
    params = {"t0": window[0], "t1": window[1], "b0": window[0]}
    conn.execute("BEGIN IMMEDIATE")
    try:
        table = layout.ensure(conn, "iface_15m", window[0])
        if method == "python":
            rows = _python_rows(conn, layout, window)
            n = conn.executemany(
                f"INSERT OR REPLACE INTO {table} VALUES ({', '.join('?' * 18)})",
                rows,
            ).rowcount
        else:
            sql = f"INSERT OR REPLACE INTO {table} " + rollup_sql(conn, layout, window, method)
            n = conn.execute(sql, params).rowcount
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return n


def compute(
    conn: sqlite3.Connection, layout: Layout, window: tuple[int, int], method: str
) -> list[tuple]:
    """Compute interface rollup rows without storing them (for cross-checks).

    Args:
        conn (sqlite3.Connection): Connection to the telemetry database.
        layout (Layout): Candidate layout.
        window (tuple[int, int]): Bucket-aligned ``(b0, b1)``.
        method (str): ``"builtin"``, ``"window"``, or ``"python"``.

    Returns:
        list[tuple]: Rollup rows sorted by key.

    Raises:
        ValueError: If ``method`` is unknown.
        sqlite3.Error: If the read fails.
    """
    if method == "python":
        return _python_rows(conn, layout, window)
    params = {"t0": window[0], "t1": window[1], "b0": window[0]}
    return sorted(conn.execute(rollup_sql(conn, layout, window, method), params))
