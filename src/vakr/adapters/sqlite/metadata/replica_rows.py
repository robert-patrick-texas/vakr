"""Row-level helpers of the SQLite metadata replica: writes, removals, digest, cursors (MPOS-003).

``vakr.adapters.sqlite.metadata.replica`` decides what to apply; these
functions do one row or one table at a time, with names that come only
from its ``TABLES`` and ``PRAGMA table_info`` and values that are always bound.

Dependencies:
    hashlib, json: The canonical row encoding for the digest.
    vakr.adapters.sqlite.metadata.transaction: The writing transaction.

Safety:
    SQL names are allowlisted by the caller; values are bound.

Example:
    >>> cursor_part(bytes([1])), key_of(["x01", 7, "sabc"]) == (bytes([1]), 7, "abc")
    ('x01', True)
"""

import json
from collections.abc import Iterable, Sequence
from typing import Any

from vakr.adapters.sqlite.metadata.transaction import SqliteTransaction
from vakr.domain.errors import ValidationError
from vakr.ports.metadata.replica import Cell

UPSERT, MEMBERS, MIRROR, APPEND = "upsert", "members", "mirror", "append"


def write_row(tx: SqliteTransaction, table: str, mode: str, cols: tuple[str, ...],
              keys: tuple[str, ...], row: tuple[object, ...]) -> int:  # fmt: skip
    """Insert or update one row; ``0`` when nothing changed.

    Args:
        tx: The writing transaction.
        table: The table.
        mode: Its ``TABLES`` mode.
        cols: Its columns.
        keys: Its key columns.
        row: Decoded values.

    Returns:
        int: Rows changed.

    Raises:
        StorageError: If the write fails.
    """
    marks = ", ".join("?" * len(cols))
    if mode in (MEMBERS, APPEND):
        sql = f"INSERT OR IGNORE INTO {table} ({', '.join(cols)}) VALUES ({marks})"  # noqa: S608
        return tx.execute(sql, row)
    rest = [c for c in cols if c not in keys]
    sets = ", ".join(f"{c} = excluded.{c}" for c in rest)
    differs = " OR ".join(f"{table}.{c} IS NOT excluded.{c}" for c in rest)
    return tx.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({marks}) "  # noqa: S608
        f"ON CONFLICT ({', '.join(keys)}) DO UPDATE SET {sets} WHERE {differs}",
        row,
    )


def remove_absent(tx: SqliteTransaction, table: str, cols: tuple[str, ...], keys: tuple[str, ...],
                   rows: Iterable[tuple[object, ...]]) -> int:  # fmt: skip
    """Delete local rows whose key the snapshot no longer holds.

    Args:
        tx: The writing transaction.
        table: A membership table or ``dashboard``.
        cols: Its columns.
        keys: Its key columns.
        rows: The snapshot's decoded rows.

    Returns:
        int: Rows deleted.

    Raises:
        StorageError: If the write fails.
    """
    at = [cols.index(k) for k in keys]
    wanted = {tuple(row[i] for i in at) for row in rows}
    local = tx.query(f"SELECT {', '.join(keys)} FROM {table}")  # noqa: S608
    gone = [tuple(r[k] for k in keys) for r in local if tuple(r[k] for k in keys) not in wanted]
    match = " AND ".join(f"{k} = ?" for k in keys)
    sql = f"DELETE FROM {table} WHERE {match}"  # noqa: S608  # allowlisted names
    return sum(tx.execute(sql, key) for key in gone)


def hash_rows(digest: Any, table: str, rows: Iterable[Sequence[Cell]]) -> None:
    """Feed one table's encoded rows to the digest, canonically.

    Args:
        digest: The running SHA-256.
        table: The table name, which separates tables.
        rows: Encoded rows in key order.

    Returns:
        None

    Raises:
        None
    """
    digest.update(f"#{table}\n".encode())
    for row in rows:
        digest.update(json.dumps(list(row), separators=(",", ":"), ensure_ascii=True).encode())
        digest.update(b"\n")


def cursor_part(value: object) -> str | int:
    """Encode one key value for a cursor (``bytes`` as ``x`` + hex, text as ``s`` + text).

    Args:
        value: A key value.

    Returns:
        str | int: The cursor part.

    Raises:
        None
    """
    if isinstance(value, bytes):
        return "x" + value.hex()
    return value if isinstance(value, int) else "s" + str(value)


def key_of(parts: Sequence[str | int]) -> tuple[object, ...]:
    """Decode cursor parts back to key values.

    Args:
        parts: From ``decode_cursor``.

    Returns:
        tuple: The key values.

    Raises:
        ValidationError: For a part this module did not write.
    """
    try:
        return tuple(p if isinstance(p, int) else bytes.fromhex(p[1:]) if p[:1] == "x"
                     else p[1:] for p in parts)  # fmt: skip
    except ValueError:
        raise ValidationError("cursor is not a valid page cursor", field="cursor") from None
