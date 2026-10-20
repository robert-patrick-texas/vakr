"""Building blocks every telemetry read shares: arms, horizon, keys, and row decoding.

A native read of ``[start, end)`` touches the day partitions the catalog
lists for that window plus the family's staging table, one ``SELECT``
("arm") each, joined with ``UNION ALL``. Every arm is a primary-key range
on ``(series_key, collector_key, ts_us)`` in a day partition, or a time
range in staging. Because a row is staged exactly when its timestamp is at
or after the boundary, and every arm is read inside one read transaction,
no row is read twice or missed while the writer moves rows (ADR-021).

Retention drops whole days, but the native horizon it records may fall
inside a day: rows before the horizon are expired, so every read starts
at the horizon at the earliest, exactly as if they had been deleted.

Dependencies:
    sqlite3: Statements on the caller's read transaction.
    vakr.adapters.sqlite.telemetry.*: Layout, catalog, keys, and codecs.

Safety:
    Table names come from the catalog via ``layout.checked``; every value is
    a bound parameter.

Example:
    >>> SUBJECT_DICTIONARY["obs_iface"], SUBJECT_DICTIONARY["obs_health"]
    ('interface', 'collector')
"""

# Arm SQL interpolates checked partition names only; values are bound parameters.
# ruff: noqa: S608

import sqlite3
from collections.abc import Callable
from functools import partial
from uuid import UUID

from vakr.adapters.sqlite.telemetry.codec import decode
from vakr.adapters.sqlite.telemetry.keys import KeyCache, find_key, find_uuid
from vakr.adapters.sqlite.telemetry.layout import all_columns, hot_table
from vakr.adapters.sqlite.telemetry.partitions import HORIZON, state, tables
from vakr.domain.observations import Observation

SUBJECT_DICTIONARY = {
    "obs_iface": "interface",
    "obs_icmp": "device",
    "obs_snmp": "device",
    "obs_poll": "device",
    "obs_health": "collector",
}
"""Native family to the dictionary of its ``series_key``."""

NOT_ATTEMPTED_BIT = 4
"""``Quality.NOT_ATTEMPTED``; rows carrying it are not evidence (invariant 2)."""


def horizon(conn: sqlite3.Connection) -> int | None:
    """Return the native horizon, or ``None`` before retention first ran.

    Args:
        conn: Connection inside the caller's transaction.

    Returns:
        int | None: Microseconds; native rows before it are expired.

    Raises:
        sqlite3.Error: If the state cannot be read.
    """
    return state(conn, HORIZON)


def native_start(conn: sqlite3.Connection, start_us: int) -> int:
    """Clamp a window start to the native horizon.

    Args:
        conn: Connection inside the caller's transaction.
        start_us: Requested start.

    Returns:
        int: The later of ``start_us`` and the horizon.

    Raises:
        sqlite3.Error: If the state cannot be read.
    """
    cut = horizon(conn)
    return start_us if cut is None else max(start_us, cut)


def sources(conn: sqlite3.Connection, family: str, start_us: int, end_us: int) -> list[str]:
    """List the tables a native read of ``[start_us, end_us)`` needs, oldest first.

    Args:
        conn: Connection inside the caller's read transaction.
        family: A native family.
        start_us: Window start.
        end_us: Window end.

    Returns:
        list[str]: Day partitions overlapping the window, then the staging table.

    Raises:
        StorageError: If the catalog holds a foreign name.
        sqlite3.Error: If the catalog cannot be read.
    """
    return [*tables(conn, family, start_us, end_us), hot_table(family)]


def union(columns: str, names: list[str], where: str) -> str:
    """Join one ``SELECT columns FROM name WHERE where`` per table with ``UNION ALL``.

    Args:
        columns: Column list each arm selects.
        names: Checked table names.
        where: Condition with named parameters.

    Returns:
        str: The compound ``SELECT``.

    Raises:
        None
    """
    return " UNION ALL ".join(f"SELECT {columns} FROM {name} WHERE {where}" for name in names)


def subject_key(
    conn: sqlite3.Connection, cache: KeyCache, family: str, subject: UUID
) -> int | None:
    """Return the ``series_key`` of a subject, or ``None`` if it has no telemetry.

    Args:
        conn: Connection.
        cache: The key cache.
        family: The subject's native family.
        subject: Interface, device, or collector UUID.

    Returns:
        int | None: The key.

    Raises:
        sqlite3.Error: If the dictionary cannot be read.
    """
    return find_key(conn, cache, SUBJECT_DICTIONARY[family], subject)


def decoder(
    conn: sqlite3.Connection, cache: KeyCache, family: str
) -> Callable[[tuple[object, ...]], Observation]:
    """Return a function that decodes full rows of ``family`` read on ``conn``.

    Args:
        conn: The connection the rows come from.
        cache: The key cache.
        family: A native family.

    Returns:
        Callable[[tuple[object, ...]], Observation]: Row to observation.

    Raises:
        None
    """
    uuid_of = partial(find_uuid, conn, cache)
    return lambda row: decode(family, row, uuid_of)


def full_columns(family: str) -> str:
    """Return a native family's column list for ``SELECT``.

    Args:
        family: A native family.

    Returns:
        str: Comma-separated column names, keys first.

    Raises:
        KeyError: If ``family`` is not native.
    """
    return ", ".join(all_columns(family))
