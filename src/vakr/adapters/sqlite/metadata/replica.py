"""SQLite metadata replica: export the replicated tables, and apply a pulled snapshot (MPOS-003).

The primary serves its replicated tables as rows in primary-key order; a
peer applies them in one transaction with deferred foreign keys:

- entity tables are upserted with the primary's exact columns, version
  included, and an unchanged row is not touched (so the closed-review and
  never-deleted triggers stay quiet: nothing is ever deleted from them,
  here or on the primary);
- membership tables (tags, groups) and dashboards mirror the snapshot:
  rows it no longer holds are removed;
- identity history is append-only: new rows are added.

The peer's own collector row and every node-local table (sessions, kiosk
and replication tokens, login failures, audit, operations) are untouched.
A demoted primary's first pull replaces instead (``replace=True``,
``replica_replace``): every local row the snapshot lacks goes.

Dependencies:
    hashlib, json: The snapshot digest.
    vakr.adapters.sqlite.metadata.transaction: The unit of work's transaction.
    vakr.domain.paging, vakr.ports.metadata.replica: Cursors and value types.

Safety:
    Table and column names come from ``TABLES`` and ``PRAGMA table_info``
    only; values are always bound. Pages and the whole set are bounded (E.13).

Example:
    >>> TABLES[0], len(TABLES)
    (('tag', 'upsert'), 15)
"""

import hashlib
from collections.abc import Mapping, Sequence

from vakr.adapters.sqlite.metadata.replica_replace import (
    discard_divergent,
    drop_guards,
    restore_guards,
)
from vakr.adapters.sqlite.metadata.replica_rows import (
    APPEND,
    MEMBERS,
    MIRROR,
    UPSERT,
    cursor_part,
    hash_rows,
    key_of,
    remove_absent,
    write_row,
)
from vakr.adapters.sqlite.metadata.transaction import SqliteTransaction
from vakr.domain.errors import ValidationError
from vakr.domain.paging import decode_cursor, encode_cursor
from vakr.ports.metadata.replica import (
    MAX_DISCARDED,
    ApplyResult,
    DiscardedRow,
    ReplicaPage,
    ReplicaState,
    decode_cell,
    encode_cell,
)

TABLES: tuple[tuple[str, str], ...] = (
    ("tag", UPSERT), ("device_group", UPSERT), ("site", UPSERT), ("site_tag", MEMBERS),
    ("collector", UPSERT), ("collection_policy", UPSERT), ("device", UPSERT),
    ("device_tag", MEMBERS), ("device_group_member", MEMBERS), ("interface", UPSERT),
    ("interface_tag", MEMBERS), ("identity_history", APPEND), ("local_user", UPSERT),
    ("dashboard", MIRROR), ("identity_review", UPSERT),
)  # fmt: skip
"""Replicated tables, parents first, and how each is applied."""
MAX_PAGE_ROWS = 1000
MAX_SNAPSHOT_ROWS = 500_000
"""Bound of the whole replicated set; far above the 4,000-device design point (E.13)."""


class SqliteMetadataReplica:
    """The replicated tables of one metadata unit of work."""

    def __init__(self, tx: SqliteTransaction) -> None:
        """Bind to a unit of work's transaction.

        Args:
            tx: The transaction.

        Returns:
            None

        Raises:
            None
        """
        self._tx = tx
        self._schema: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}

    def tables(self) -> tuple[str, ...]:
        """Name the replicated tables, parents before children.

        Args:
            None

        Returns:
            tuple[str, ...]: The tables.

        Raises:
            None
        """
        return tuple(t for t, _ in TABLES)

    def _columns(self, table: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Return a table's columns and primary-key columns, in schema order.

        Args:
            table: A replicated table.

        Returns:
            tuple: ``(columns, key columns)``.

        Raises:
            ValidationError: If ``table`` is not replicated.
        """
        if table not in dict(TABLES):
            raise ValidationError(f"{table!r} is not a replicated table", field="kind")
        if table not in self._schema:
            info = self._tx.query(f"PRAGMA table_info({table})")  # allowlisted name
            cols = tuple(r["name"] for r in info)
            keys = tuple(
                r["name"] for r in sorted((r for r in info if r["pk"]), key=lambda r: r["pk"])
            )
            self._schema[table] = (cols, keys)
        return self._schema[table]

    def _select(self, table: str, after: Sequence[object] | None, limit: int) -> list:
        """Read rows of ``table`` in key order, after a key.

        Args:
            table: A replicated table.
            after: The last key read, or ``None`` from the start.
            limit: Most rows.

        Returns:
            list: The rows.

        Raises:
            StorageError: If the table cannot be read.
        """
        cols, keys = self._columns(table)
        where = f" WHERE ({', '.join(keys)}) > ({', '.join('?' * len(keys))})" if after else ""
        order = ", ".join(keys)
        sql = f"SELECT {', '.join(cols)} FROM {table}{where} ORDER BY {order} LIMIT ?"  # noqa: S608
        return self._tx.query(sql, (*(after or ()), limit))  # names from PRAGMA, values bound

    def page(self, table: str, cursor: str | None, limit: int) -> ReplicaPage:
        """Return one page of one replicated table; see ``MetadataReplica.page``.

        Args:
            table: The table.
            cursor: Previous ``next_cursor``, or ``None``.
            limit: 1 to ``MAX_PAGE_ROWS``.

        Returns:
            ReplicaPage: The page.

        Raises:
            ValidationError: For an unknown table, a bad cursor, or a bad limit.
        """
        cols, keys = self._columns(table)
        if not 1 <= limit <= MAX_PAGE_ROWS:
            raise ValidationError(f"limit must be 1 to {MAX_PAGE_ROWS}", field="limit")
        after = key_of(decode_cursor(cursor, len(keys))) if cursor else None
        rows = self._select(table, after, limit + 1)
        more, rows = len(rows) > limit, rows[:limit]
        encoded = tuple(tuple(encode_cell(r[c]) for c in cols) for r in rows)
        last = rows[-1] if rows else None
        nxt = encode_cursor(tuple(cursor_part(last[k]) for k in keys)) if more and last else None
        return ReplicaPage(table, cols, encoded, nxt)

    def state(self) -> ReplicaState:
        """Describe the replicated set: schema, digest, row counts.

        Args:
            None

        Returns:
            ReplicaState: The state, with this release's metadata schema version.

        Raises:
            ValidationError: Over ``MAX_SNAPSHOT_ROWS``.
        """
        digest, counts, total = hashlib.sha256(), {}, 0
        for table in self.tables():
            cols, _ = self._columns(table)
            rows = self._select(table, None, MAX_SNAPSHOT_ROWS + 1)
            total += len(rows)
            if total > MAX_SNAPSHOT_ROWS:
                raise ValidationError(
                    f"replicated metadata exceeds {MAX_SNAPSHOT_ROWS} rows (E.13)"
                )
            counts[table] = len(rows)
            hash_rows(digest, table, (tuple(encode_cell(r[c]) for c in cols) for r in rows))
        # Imported here: the store module builds units of work, which build this class.
        from vakr.adapters.sqlite.metadata.store import metadata_family

        schema = max(m.version for m in metadata_family().migrations)
        return ReplicaState(schema, digest.hexdigest(), counts)

    def apply(self, snapshot: Mapping[str, tuple[ReplicaPage, ...]], digest: str, *,
              replace: bool = False) -> ApplyResult:  # fmt: skip
        """Apply a pulled snapshot; see ``MetadataReplica.apply``. The caller commits.

        Args:
            snapshot: Pages by table.
            digest: The primary's advertised digest.
            replace: Remove or overwrite every local row the snapshot lacks
                (``vakr.adapters.sqlite.metadata.replica_replace``).

        Returns:
            ApplyResult: Rows written and removed per table, and what was discarded.

        Raises:
            ValidationError: Incomplete, other columns, or a digest mismatch.
        """
        rows = self._verified(snapshot, digest)
        self._tx.begin_write()
        self._tx.execute("PRAGMA defer_foreign_keys = ON")  # parents and children land together
        guards = drop_guards(self._tx, self.tables()) if replace else []
        written: dict[str, int] = {}
        removed: dict[str, int] = {}
        discarded: list[DiscardedRow] = []
        for table, mode in TABLES:
            cols, keys = self._columns(table)
            decoded = [tuple(decode_cell(v) for v in row) for row in rows[table]]
            found = discard_divergent(self._tx, table, mode, cols, keys, decoded) if replace else []
            discarded += found
            removed[table] = sum(1 for d in found if d.change == "removed")
            if mode in (MEMBERS, MIRROR):
                removed[table] += remove_absent(self._tx, table, cols, keys, decoded)
            written[table] = sum(
                write_row(self._tx, table, mode, cols, keys, row) for row in decoded
            )
        restore_guards(self._tx, guards)
        return ApplyResult(written, removed, tuple(discarded[:MAX_DISCARDED]), len(discarded))

    def _verified(self, snapshot: Mapping[str, tuple[ReplicaPage, ...]], digest: str) -> dict:
        """Check completeness, columns, and the digest; return each table's rows.

        Args:
            snapshot: Pages by table.
            digest: Advertised digest.

        Returns:
            dict: Encoded rows by table.

        Raises:
            ValidationError: On any mismatch.
        """
        check, rows = hashlib.sha256(), {}
        for table in self.tables():
            pages = snapshot.get(table)
            if pages is None:
                raise ValidationError(f"the snapshot has no {table} pages")
            cols, _ = self._columns(table)
            if any(p.columns != cols for p in pages):
                raise ValidationError(f"the primary's {table} columns differ from this node's; "
                                      "run the same vakr release on both nodes")  # fmt: skip
            rows[table] = [r for p in pages for r in p.rows]
            hash_rows(check, table, rows[table])
        if check.hexdigest() != digest:
            raise ValidationError("the pulled metadata does not match the primary's digest; "
                                  "it changed during the pull and is pulled again")  # fmt: skip
        return rows
