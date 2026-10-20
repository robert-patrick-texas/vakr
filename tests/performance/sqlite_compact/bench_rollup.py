# Table names in SQL here come from fixed prefixes, UTC dates and the writer-filled
# partition catalog, never from input, so string-built SQL cannot be injected.
# ruff: noqa: S608
r"""Measure 15-minute rollup computation on an ingested database (RET-004, RET-009).

Three p95 methods are cross-checked for identical output on one bucket,
then timed computing and storing one bucket for all 3,200 series (the
steady-state job every 15 minutes) and one whole UTC day (catch-up after
downtime). ICMP rollups are timed for the same day. The day's coverage
summary shows incomplete buckets stay visible instead of being averaged
away (ROLL-T05).

Dependencies:
    sqlc_rollup, sqlc_rollup_icmp: The rollup computations.
    sqlc_measure: Timing and percentiles.
    sqlc_schema: Layout and connection settings.

Safety:
    Writes only rollup tables of the benchmark database named by ``--db``.

Example:
    $ uv run python -m tests.performance.sqlite_compact.bench_rollup \
        --db /data/bench/scale-CH.db --candidate C --hot --out rollup.json
"""

import argparse
import json
import math
import sqlite3
import time
from pathlib import Path

from tests.performance.sqlite_compact.bench_ingest import START_US
from tests.performance.sqlite_compact.sqlc_measure import summarize_ms, time_runs
from tests.performance.sqlite_compact.sqlc_rollup import BUCKET_US, compute, rollup_interfaces
from tests.performance.sqlite_compact.sqlc_rollup_icmp import rollup_icmp
from tests.performance.sqlite_compact.sqlc_schema import Layout, Pragmas, open_db

DAY_US = 86_400_000_000
METHODS = ("builtin", "window", "python")


def same(a: list[tuple], b: list[tuple]) -> bool:
    """Compare rollup row lists, allowing float rounding differences.

    Args:
        a (list[tuple]): Rows from one method, sorted by key.
        b (list[tuple]): Rows from another method, sorted by key.

    Returns:
        bool: True when keys, counts and values agree within 1e-9 relative.

    Raises:
        None
    """
    if len(a) != len(b):
        return False
    for ra, rb in zip(a, b, strict=True):
        for x, y in zip(ra, rb, strict=True):
            if isinstance(x, float) or isinstance(y, float):
                if x is None or y is None or not math.isclose(x, y, rel_tol=1e-9):
                    return False
            elif x != y:
                return False
    return True


def coverage(conn: sqlite3.Connection, layout: Layout, day0: int) -> dict[str, float]:
    """Summarize the stored interface rollups of one day.

    Args:
        conn (sqlite3.Connection): Connection to the benchmark database.
        layout (Layout): Candidate layout.
        day0 (int): Day start, Unix microseconds.

    Returns:
        dict[str, float]: Rows, buckets with incomplete or no valid
        coverage, flagged buckets, and the mean valid count.

    Raises:
        sqlite3.Error: If the query fails.
    """
    table = layout.rollup_table("iface_15m", day0)
    row = conn.execute(
        "SELECT COUNT(*), SUM(valid_count < expected_count), SUM(valid_count = 0),"
        f" SUM(quality_any != 0), AVG(valid_count) FROM {table}"
        " WHERE bucket_start_us >= ? AND bucket_start_us < ?",
        (day0, day0 + DAY_US),
    ).fetchone()
    keys = ("rows", "incomplete_buckets", "no_valid_buckets", "flagged_buckets", "mean_valid")
    return dict(zip(keys, row, strict=True))


def main(argv: list[str] | None = None) -> int:
    """Run the rollup measurements and write JSON results.

    Args:
        argv (list[str] | None): Arguments, or None for ``sys.argv``.

    Returns:
        int: 0 on success, 1 when the methods disagree.

    Raises:
        sqlite3.Error: If a rollup fails.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True)
    ap.add_argument("--candidate", choices="ABC", required=True)
    ap.add_argument("--hot", action="store_true")
    ap.add_argument("--day", type=int, default=0)
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--data-dir")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    layout, conn = Layout(a.candidate, hot=a.hot), open_db(a.db, Pragmas())
    day0 = START_US + a.day * DAY_US
    bucket = (day0 + 12 * 3_600_000_000, day0 + 12 * 3_600_000_000 + BUCKET_US)
    rows = {m: compute(conn, layout, bucket, m) for m in METHODS}
    res: dict[str, object] = {
        "bucket_rows": len(rows["builtin"]),
        "methods_agree": all(same(rows["builtin"], rows[m]) for m in METHODS),
        "sample_row": rows["builtin"][0],
    }
    per_bucket, per_day = {}, {}
    for m in METHODS:
        lat, _ = time_runs(lambda m=m: rollup_interfaces(conn, layout, bucket, m), a.runs)
        per_bucket[m] = summarize_ms(lat)
    for m in METHODS:
        t = time.perf_counter()
        n = rollup_interfaces(conn, layout, (day0, day0 + DAY_US), m)
        per_day[m] = {"rows": n, "seconds": round(time.perf_counter() - t, 2)}
    res["bucket"], res["day"] = per_bucket, per_day
    lat, _ = time_runs(lambda: rollup_icmp(conn, layout, bucket), a.runs)
    res["icmp_bucket"] = summarize_ms(lat)
    t = time.perf_counter()
    res["icmp_day"] = {
        "rows": rollup_icmp(conn, layout, (day0, day0 + DAY_US)),
        "seconds": round(time.perf_counter() - t, 2),
    }
    res["coverage"] = coverage(conn, layout, day0)
    conn.close()
    Path(a.out).write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps(res, indent=1))
    return 0 if res["methods_agree"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
