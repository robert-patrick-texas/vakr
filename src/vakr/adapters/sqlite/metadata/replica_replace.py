"""Replace mode of the SQLite metadata replica: a demoted primary's resync (MPOS-003, ADR-024).

An ordinary pull never deletes an entity row, never rewrites a closed
identity review, and never changes identity history, because the
primary never does. A demoted primary is different: during a partition
it may have accepted edits its successor never saw, so its replicated
tables must become exactly the new primary's. In replace mode the apply,
in its one transaction:

- drops the guard triggers on the replicated tables (``RAISE(ABORT)``
  only), and creates them again from their stored SQL before it returns,
  so a rollback restores them too;
- removes every local row whose key the snapshot lacks, and deletes the
  rows that differ in membership and append-only tables so the snapshot's
  copy lands (entity rows are overwritten by the upsert);
- removes the node-local rows that point at removed rows: sessions of a
  removed account and operations on a removed device or collector.

Collector records are never removed: each node's own record must stay,
and an extra one is harmless. Every removed or overwritten row is listed
for the demote report by table, key, and a name, never by its values
(account rows hold password hashes).

Dependencies:
    uuid: Key rendering.
    vakr.adapters.sqlite.metadata.transaction: The writing transaction.

Safety:
    Table and column names come from the replica's ``TABLES`` and
    ``PRAGMA table_info``; trigger names and SQL from ``sqlite_master``;
    values are always bound.

Example:
    >>> key_text((bytes(16),))
    '00000000-0000-0000-0000-000000000000'
"""

from collections.abc import Sequence
from uuid import UUID

from vakr.adapters.sqlite.metadata.replica_rows import APPEND, MEMBERS
from vakr.adapters.sqlite.metadata.transaction import SqliteTransaction
from vakr.ports.metadata.replica import DiscardedRow

KEPT_TABLES = frozenset({"collector"})
"""Tables whose local rows a replacing apply never removes."""
_LABELS = ("name", "username", "label", "title", "if_name", "ifname", "description")
_DEPENDENTS = {"local_user": (("auth_session", "user_id"),),
               "device": (("operation", "device_id"),)}  # fmt: skip
"""Node-local rows that point at a replicated row, removed with it."""


def drop_guards(tx: SqliteTransaction, tables: Sequence[str]) -> list[tuple[str, str]]:
    """Drop the guard triggers on the replicated tables, returning them for ``restore_guards``.

    Args:
        tx: The writing transaction.
        tables: The replicated tables.

    Returns:
        list[tuple[str, str]]: ``(name, sql)`` of each trigger dropped.

    Raises:
        StorageError: If the schema cannot be read or changed.
    """
    marks = ", ".join("?" * len(tables))
    found = tx.query(f"SELECT name, sql FROM sqlite_master WHERE type = 'trigger' "  # noqa: S608
                     f"AND tbl_name IN ({marks}) ORDER BY name", tuple(tables))  # fmt: skip
    guards = [(r["name"], r["sql"]) for r in found if "RAISE(ABORT" in (r["sql"] or "")]
    for name, _ in guards:
        tx.execute(f'DROP TRIGGER "{name}"')  # a name sqlite_master holds
    return guards


def restore_guards(tx: SqliteTransaction, guards: Sequence[tuple[str, str]]) -> None:
    """Create the dropped guard triggers again, exactly as they were.

    Args:
        tx: The writing transaction.
        guards: What ``drop_guards`` returned.

    Returns:
        None

    Raises:
        StorageError: If a trigger cannot be created.
    """
    for _, sql in guards:
        tx.execute(sql)


def key_text(key: Sequence[object]) -> str:
    """Render a row key for the report: 16-byte blobs as UUIDs.

    Args:
        key: The key values.

    Returns:
        str: The parts joined by ``/``.

    Raises:
        None
    """
    parts = [str(UUID(bytes=k)) if isinstance(k, bytes) and len(k) == 16
             else k.hex() if isinstance(k, bytes) else str(k) for k in key]  # fmt: skip
    return "/".join(parts)


def _label(cols: Sequence[str], row: Sequence[object]) -> str:
    """Pick a column to recognise a row by.

    Args:
        cols: The table's columns.
        row: The row's values.

    Returns:
        str: The first naming column's value, at most 128 characters, or ``""``.

    Raises:
        None
    """
    for name in _LABELS:
        if name in cols and isinstance(row[cols.index(name)], str):
            return str(row[cols.index(name)])[:128]
    return ""


def discard_divergent(tx: SqliteTransaction, table: str, mode: str, cols: tuple[str, ...],
                      keys: tuple[str, ...],
                      rows: Sequence[tuple[object, ...]]) -> list[DiscardedRow]:  # fmt: skip
    """Remove or mark the local rows of one table that the snapshot does not hold exactly.

    Args:
        tx: The writing transaction, guards dropped.
        table: A replicated table.
        mode: Its ``TABLES`` mode.
        cols: Its columns.
        keys: Its key columns.
        rows: The snapshot's decoded rows.

    Returns:
        list[DiscardedRow]: Every local row removed, or about to be overwritten.

    Raises:
        StorageError: If a read or delete fails.
    """
    at = [cols.index(k) for k in keys]
    wanted = {tuple(row[i] for i in at): tuple(row) for row in rows}
    found: list[DiscardedRow] = []
    gone: list[tuple[object, ...]] = []
    for local in tx.query(f"SELECT {', '.join(cols)} FROM {table}"):  # noqa: S608  # allowlisted
        values = tuple(local[c] for c in cols)
        key = tuple(values[i] for i in at)
        theirs = wanted.get(key)
        if theirs == values or (theirs is None and table in KEPT_TABLES):
            continue
        change = "removed" if theirs is None else "replaced"
        found.append(DiscardedRow(table, key_text(key), _label(cols, values), change))
        if theirs is None or mode in (MEMBERS, APPEND):
            gone.append(key)
    _delete(tx, table, keys, gone)
    return found


def _delete(tx: SqliteTransaction, table: str, keys: tuple[str, ...],
            gone: Sequence[tuple[object, ...]]) -> None:  # fmt: skip
    """Delete rows by key, and the node-local rows that point at them.

    Args:
        tx: The writing transaction.
        table: The table.
        keys: Its key columns.
        gone: Keys to delete.

    Returns:
        None

    Raises:
        StorageError: If a delete fails.
    """
    match = " AND ".join(f"{k} = ?" for k in keys)
    for key in gone:
        for child, column in _DEPENDENTS.get(table, ()):
            tx.execute(f"DELETE FROM {child} WHERE {column} = ?", key[:1])  # noqa: S608
        tx.execute(f"DELETE FROM {table} WHERE {match}", key)  # noqa: S608  # allowlisted
