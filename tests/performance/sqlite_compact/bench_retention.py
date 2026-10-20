# Table names in SQL here come from fixed prefixes, UTC dates and the writer-filled
# partition catalog, never from input, so string-built SQL cannot be injected.
# ruff: noqa: S608
r"""Measure one day of native retention: drop a day's tables (C) or DELETE it (A/B).

Layout C expires the oldest UTC day by dropping its three tables and
removing them from the catalog in one transaction (Appendix E.10).
Layouts A and B must ``DELETE`` the day's rows; the fastest way available
is one primary-key range delete per series, then ``PRAGMA
incremental_vacuum`` to give pages back (the file must have been created
with ``auto_vacuum = INCREMENTAL``). For each phase the run records time,
bytes the process wrote, WAL size after commit, and page and freelist
counts, so the I/O cost of each approach is explicit.

Dependencies:
    sqlc_schema: Layout, catalog, and connection settings.
    sqlc_measure: File sizes.
    bench_ingest: ``proc_io`` counters.

Safety:
    Deletes data from the benchmark database named by ``--db`` only.

Example:
    $ uv run python -m tests.performance.sqlite_compact.bench_retention \
        --db /data/bench/scale-CH.db --candidate C --out retention.json
"""

import argparse
import json
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from tests.performance.sqlite_compact.bench_ingest import proc_io
from tests.performance.sqlite_compact.sqlc_columns import KEYS, NATIVE_KINDS
from tests.performance.sqlite_compact.sqlc_measure import file_bytes
from tests.performance.sqlite_compact.sqlc_schema import Pragmas, open_db, tables_for

DAY_US = 86_400_000_000


def pages(conn: sqlite3.Connection) -> dict[str, int]:
    """Report page and freelist counts.

    Args:
        conn (sqlite3.Connection): Connection to the database.

    Returns:
        dict[str, int]: ``page_count`` and ``freelist_count``.

    Raises:
        sqlite3.Error: If a PRAGMA fails.
    """
    return {
        "page_count": conn.execute("PRAGMA page_count").fetchone()[0],
        "freelist_count": conn.execute("PRAGMA freelist_count").fetchone()[0],
    }


def phase(db: str, conn: sqlite3.Connection, fn: Callable[[], object]) -> dict[str, object]:
    """Run one retention phase and record its cost.

    Args:
        db (str): Database path.
        conn (sqlite3.Connection): Writer connection.
        fn (Callable[[], object]): The phase; its return value is recorded.

    Returns:
        dict[str, object]: Seconds, result, bytes written, WAL size and pages.

    Raises:
        sqlite3.Error: If the phase fails.
    """
    io0, t = proc_io(), time.perf_counter()
    result = fn()
    seconds = time.perf_counter() - t
    io1 = proc_io()
    return {
        "seconds": round(seconds, 3),
        "result": result,
        "write_bytes": io1["write_bytes"] - io0["write_bytes"],
        "wchar": io1["wchar"] - io0["wchar"],
        **file_bytes(db),
        **pages(conn),
    }


def drop_day(conn: sqlite3.Connection, day0: int) -> list[str]:
    """Drop every native table of one UTC day and uncatalog it, in one transaction.

    Args:
        conn (sqlite3.Connection): Writer connection in autocommit mode.
        day0 (int): Day start, Unix microseconds.

    Returns:
        list[str]: Dropped table names.

    Raises:
        sqlite3.Error: If the transaction fails; it is rolled back.
    """
    names = [t for k in NATIVE_KINDS for t in tables_for(conn, k, day0, day0 + 1)]
    conn.execute("BEGIN IMMEDIATE")
    try:
        for t in names:
            conn.execute(f"DROP TABLE {t}")
        conn.executemany(
            "DELETE FROM partition_catalog WHERE table_name = ?", [(t,) for t in names]
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return names


def delete_day(conn: sqlite3.Connection, cutoff: int) -> int:
    """Delete native rows older than ``cutoff`` with one key-range delete per series.

    Args:
        conn (sqlite3.Connection): Writer connection in autocommit mode.
        cutoff (int): Rows with ``ts_us < cutoff`` are removed.

    Returns:
        int: Rows deleted.

    Raises:
        sqlite3.Error: If the transaction fails; it is rolled back.
    """
    deleted = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for kind in NATIVE_KINDS:
            series, dictionary = (
                (("interface_key", "dict_interface"))
                if kind == "iface"
                else ("device_key", "dict_device")
            )
            keys = conn.execute(
                f"SELECT d.{series}, c.collector_key FROM {dictionary} d"
                " CROSS JOIN dict_collector c"
            ).fetchall()
            s, c, ts = KEYS[kind]
            sql = f"DELETE FROM obs_{kind} WHERE {s} = ? AND {c} = ? AND {ts} < ?"
            deleted += conn.executemany(sql, [(k, ck, cutoff) for k, ck in keys]).rowcount
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return deleted


def main(argv: list[str] | None = None) -> int:
    """Expire the oldest day with the candidate's method and write JSON results.

    Args:
        argv (list[str] | None): Arguments, or None for ``sys.argv``.

    Returns:
        int: Process exit status.

    Raises:
        sqlite3.Error: If retention fails.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True)
    ap.add_argument("--candidate", choices="ABC", required=True)
    ap.add_argument("--hot", action="store_true")
    ap.add_argument("--data-dir")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    conn = open_db(a.db, Pragmas())
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    first = tables_for(conn, "iface", 0, 2**62)[0]
    if a.candidate == "C":
        day0 = conn.execute(
            "SELECT start_us FROM partition_catalog WHERE table_name = ?", (first,)
        ).fetchone()[0]
    else:
        day0 = conn.execute(f"SELECT MIN(ts_us) FROM {first}").fetchone()[0] // DAY_US * DAY_US
    res: dict[str, object] = {
        "before": {**file_bytes(a.db), **pages(conn)},
        "auto_vacuum": conn.execute("PRAGMA auto_vacuum").fetchone()[0],
    }
    if a.candidate == "C":
        res["drop"] = phase(a.db, conn, lambda: drop_day(conn, day0))
    else:
        res["delete"] = phase(a.db, conn, lambda: delete_day(conn, day0 + DAY_US))
    res["checkpoint"] = phase(
        a.db, conn, lambda: conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    )
    if res["auto_vacuum"] == 2:
        res["incremental_vacuum"] = phase(
            a.db, conn, lambda: conn.execute("PRAGMA incremental_vacuum").fetchall() and None
        )
        res["checkpoint_after_vacuum"] = phase(
            a.db, conn, lambda: conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        )
    conn.close()
    Path(a.out).write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
