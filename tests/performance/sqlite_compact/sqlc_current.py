# Table names in SQL here come from fixed prefixes, UTC dates and the writer-filled
# partition catalog, never from input, so string-built SQL cannot be injected.
# ruff: noqa: S608
"""Current interface state: latest observation per series (STATE-001..004).

Two strategies are measured. ``latest_by_seek`` reads the retained
observations directly: staged rows first, then day tables newest first,
one primary-key seek per series, stopping once every series is found, so
a stale interface whose last sample is days old is still reported with
its true age. ``latest_from_projection`` reads the ``cur_iface`` table the
writer upserts, which is rebuildable from observations (STATE-002).

Dependencies:
    sqlite3: Standard-library driver.
    sqlc_schema: Table resolution.

Safety:
    Table names come from the writer-filled catalog (see ``sqlc_schema``).

Example:
    >>> DEVICE_FILTER
    ' WHERE d.device_key = :d'
"""

import sqlite3

from tests.performance.sqlite_compact.sqlc_schema import Layout, tables_for

DEVICE_FILTER = " WHERE d.device_key = :d"


def _wanted(conn: sqlite3.Connection, device_key: int | None) -> int:
    """Count the series a current-state view should contain.

    Args:
        conn (sqlite3.Connection): Reader connection.
        device_key (int | None): One device, or None for all.

    Returns:
        int: Interfaces times collectors.

    Raises:
        sqlite3.Error: If the dictionaries cannot be read.
    """
    where = " WHERE device_key = :d" if device_key is not None else ""
    sql = f"SELECT COUNT(*) * (SELECT COUNT(*) FROM dict_collector) FROM dict_interface{where}"
    return conn.execute(sql, {"d": device_key}).fetchone()[0]


def latest_by_seek(
    conn: sqlite3.Connection, layout: Layout, device_key: int | None = None
) -> list[tuple]:
    """Return the newest interface row per series by seeking retained data.

    Args:
        conn (sqlite3.Connection): Reader connection (one read transaction
            is opened so every table is read from the same snapshot).
        layout (Layout): Candidate layout.
        device_key (int | None): One device, or None for every device.

    Returns:
        list[tuple]: Full interface rows ordered by series key.

    Raises:
        sqlite3.Error: If a query fails.
    """
    want, found = _wanted(conn, device_key), {}
    params = {"d": device_key}
    conn.execute("BEGIN")
    try:
        if layout.hot:
            dev = (
                (
                    " WHERE interface_key IN (SELECT interface_key FROM dict_interface"
                    " WHERE device_key = :d)"
                )
                if device_key is not None
                else ""
            )
            # SQLite fills bare columns from the row holding the single MAX().
            sql = (
                f"SELECT *, MAX(ts_us) FROM {Layout.hot_table('iface')}{dev}"
                " GROUP BY interface_key, collector_key"
            )
            found = {(r[0], r[1]): r[:-1] for r in conn.execute(sql, params)}
        for table in reversed(tables_for(conn, "iface", 0, 2**62)):
            if len(found) >= want:
                break
            sql = (
                f"SELECT t.* FROM dict_interface d CROSS JOIN dict_collector c"
                f" CROSS JOIN {table} t ON t.interface_key = d.interface_key"
                " AND t.collector_key = c.collector_key AND t.ts_us = (SELECT MAX(x.ts_us)"
                f" FROM {table} x WHERE x.interface_key = d.interface_key"
                " AND x.collector_key = c.collector_key)"
                + (DEVICE_FILTER if device_key is not None else "")
            )
            for r in conn.execute(sql, params):
                found.setdefault((r[0], r[1]), r)
    finally:
        conn.execute("COMMIT")
    return [found[k] for k in sorted(found)]


def latest_from_projection(conn: sqlite3.Connection, device_key: int | None = None) -> list[tuple]:
    """Return the projected latest row per series from ``cur_iface``.

    Args:
        conn (sqlite3.Connection): Reader connection.
        device_key (int | None): One device, or None for every device.

    Returns:
        list[tuple]: Full interface rows ordered by series key.

    Raises:
        sqlite3.Error: If the projection table is missing.
    """
    if device_key is None:
        return conn.execute("SELECT * FROM cur_iface ORDER BY 1, 2").fetchall()
    return conn.execute(
        "SELECT * FROM cur_iface WHERE interface_key IN"
        " (SELECT interface_key FROM dict_interface WHERE device_key = ?) ORDER BY 1, 2",
        (device_key,),
    ).fetchall()
