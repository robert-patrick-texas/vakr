"""The partition catalog and the telemetry state rows (ADR-021, blueprint 5.1).

Queries never parse ``sqlite_schema``: they ask ``partition_catalog`` which
day or month tables overlap a window. The writer creates a partition the
first time a row needs it, from the DDL template that the schema's
migrations recorded in ``partition_template``, and catalogues it with that
template's version in the same transaction as the rows, so a partition and
its catalog row always appear (or roll back) together. Retention drops
tables and their catalog rows in one transaction too.

``telemetry_state`` holds the staging boundary (a native row is staged
exactly when ``ts_us >= staging_boundary_us``) and, once retention has
run, the native and rollup horizons.

Dependencies:
    sqlite3: Statements on the caller's connection and transaction.
    vakr.adapters.sqlite.telemetry.layout: Names and bounds.

Safety:
    Table names reaching SQL text come from ``layout`` and pass
    ``layout.checked``; template DDL comes from the migrated schema.

Example:
    >>> BOUNDARY, HORIZON, ROLLUP_HORIZON
    ('staging_boundary_us', 'native_horizon_us', 'rollup_horizon_us')
"""

# Table names in SQL text come from layout (fixed families and UTC dates) or from
# the catalog, and pass layout.checked; nothing here formats a request value.

import sqlite3

from vakr.adapters.sqlite.telemetry.layout import (
    DAY_US,
    checked,
    day_start,
    day_table,
    month_bounds,
    month_table,
)
from vakr.domain.errors import StorageError

BOUNDARY = "staging_boundary_us"
HORIZON = "native_horizon_us"
ROLLUP_HORIZON = "rollup_horizon_us"


def state(conn: sqlite3.Connection, name: str) -> int | None:
    """Read one ``telemetry_state`` value.

    Args:
        conn: Any connection, inside the caller's transaction.
        name: ``BOUNDARY``, ``HORIZON``, or ``ROLLUP_HORIZON``.

    Returns:
        int | None: The value, or ``None`` if it was never set.

    Raises:
        sqlite3.Error: If the table cannot be read.
    """
    row = conn.execute("SELECT value FROM telemetry_state WHERE name = ?", (name,)).fetchone()
    return None if row is None else int(row[0])


def set_state(conn: sqlite3.Connection, name: str, value: int) -> None:
    """Write one ``telemetry_state`` value inside the caller's write transaction.

    Args:
        conn: Connection holding the write lock.
        name: ``BOUNDARY``, ``HORIZON``, or ``ROLLUP_HORIZON``.
        value: The new value.

    Returns:
        None

    Raises:
        sqlite3.Error: If the row cannot be written.
    """
    conn.execute(
        "INSERT INTO telemetry_state (name, value) VALUES (?, ?) "
        "ON CONFLICT (name) DO UPDATE SET value = excluded.value",
        (name, value),
    )


def spans(
    conn: sqlite3.Connection, family: str, start_us: int, end_us: int
) -> list[tuple[str, int]]:
    """List a family's partitions overlapping ``[start_us, end_us)`` with their starts.

    Args:
        conn: Any connection, inside the caller's read transaction.
        family: A native or rollup family.
        start_us: Window start, inclusive.
        end_us: Window end, exclusive.

    Returns:
        list[tuple[str, int]]: Checked names and partition starts, oldest first.

    Raises:
        StorageError: If the catalog holds a foreign name.
        sqlite3.Error: If the catalog cannot be read.
    """
    rows = conn.execute(
        "SELECT table_name, start_us FROM partition_catalog WHERE family = ? AND start_us < ? "
        "AND end_us > ? ORDER BY start_us",
        (family, end_us, start_us),
    )
    return [(checked(r[0]), int(r[1])) for r in rows]


def tables(conn: sqlite3.Connection, family: str, start_us: int, end_us: int) -> list[str]:
    """List a family's partitions overlapping ``[start_us, end_us)``, oldest first.

    Args:
        conn: Any connection, inside the caller's read transaction.
        family: A native or rollup family.
        start_us: Window start, inclusive.
        end_us: Window end, exclusive.

    Returns:
        list[str]: Checked table names.

    Raises:
        StorageError: If the catalog holds a foreign name.
        sqlite3.Error: If the catalog cannot be read.
    """
    return [name for name, _start in spans(conn, family, start_us, end_us)]


def _ensure(conn: sqlite3.Connection, family: str, name: str, bounds: tuple[int, int]) -> str:
    """Create and catalogue a partition from its family's template if it is missing.

    Args:
        conn: Connection holding the write lock.
        family: The partition's family.
        name: Its checked name.
        bounds: ``(start_us, end_us)`` it covers.

    Returns:
        str: ``name``.

    Raises:
        StorageError: If the schema has no template for the family.
        sqlite3.Error: If the DDL or catalog insert fails.
    """
    known = conn.execute("SELECT 1 FROM partition_catalog WHERE table_name = ?", (name,))
    if known.fetchone() is not None:
        return name
    template = conn.execute(
        "SELECT version, ddl FROM partition_template WHERE family = ?", (family,)
    ).fetchone()
    if template is None:
        raise StorageError(f"the telemetry schema has no template for {family}", retryable=False)
    conn.execute(str(template[1]).replace("{table}", checked(name)))
    conn.execute(
        "INSERT INTO partition_catalog (table_name, family, start_us, end_us, template_version) "
        "VALUES (?, ?, ?, ?, ?)",
        (name, family, bounds[0], bounds[1], template[0]),
    )
    return name


def ensure_day(conn: sqlite3.Connection, family: str, ts_us: int) -> str:
    """Return the native partition holding ``ts_us``, creating it at its first row.

    Args:
        conn: Connection holding the write lock.
        family: A native family.
        ts_us: A timestamp in the partition's UTC day.

    Returns:
        str: The partition name.

    Raises:
        StorageError: If the schema has no template for the family.
        sqlite3.Error: If the partition cannot be created.
    """
    start = day_start(ts_us)
    return _ensure(conn, family, day_table(family, start), (start, start + DAY_US))


def ensure_month(conn: sqlite3.Connection, family: str, ts_us: int) -> str:
    """Return the rollup partition for buckets starting in ``ts_us``'s month.

    Args:
        conn: Connection holding the write lock.
        family: A rollup family.
        ts_us: A bucket start in the partition's UTC month.

    Returns:
        str: The partition name.

    Raises:
        StorageError: If the schema has no template for the family.
        sqlite3.Error: If the partition cannot be created.
    """
    return _ensure(conn, family, month_table(family, ts_us), month_bounds(ts_us))


def ending_by(conn: sqlite3.Connection, prefix: str, cutoff_us: int) -> list[tuple[str, int]]:
    """List partitions whose families start with ``prefix`` and that end by ``cutoff_us``.

    Args:
        conn: Any connection, inside the caller's transaction.
        prefix: ``obs_`` for native or ``roll_`` for rollup partitions.
        cutoff_us: Partitions with ``end_us <= cutoff_us`` qualify.

    Returns:
        list[tuple[str, int]]: Checked names with their ``bucket_count``.

    Raises:
        StorageError: If the catalog holds a foreign name.
        sqlite3.Error: If the catalog cannot be read.
    """
    rows = conn.execute(
        "SELECT table_name, bucket_count FROM partition_catalog "
        "WHERE substr(family, 1, ?) = ? AND end_us <= ? ORDER BY start_us, table_name",
        (len(prefix), prefix, cutoff_us),
    )
    return [(checked(r[0]), int(r[1])) for r in rows]


def drop(conn: sqlite3.Connection, names: list[str]) -> None:
    """Drop partitions and uncatalogue them inside the caller's write transaction.

    Args:
        conn: Connection holding the write lock.
        names: Checked partition names.

    Returns:
        None

    Raises:
        sqlite3.Error: If a drop fails; the caller rolls everything back.
    """
    for name in names:
        conn.execute(f"DROP TABLE IF EXISTS {checked(name)}")
    conn.executemany("DELETE FROM partition_catalog WHERE table_name = ?", [(n,) for n in names])
