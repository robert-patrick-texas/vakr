"""Reading metadata records from SQLite through their ``Spec`` (DB-001, DB-007).

``SqliteReader`` implements ``get``, ``get_by_name``, and ``list_page`` for
any record type. A page's set-valued fields are loaded with one query per
membership table for the whole page, not one per record. A row that no
longer decodes into a valid domain record (a hand-edited or damaged file)
is reported as ``StorageError``, not as the caller's ``ValidationError``.

Dependencies:
    vakr.adapters.sqlite.metadata.paging, specs, transaction.
    vakr.domain: Errors and paging types.

Safety:
    SQL is built from ``Spec`` constants; IDs and names are bound values.

Example:
    >>> from vakr.adapters.sqlite.metadata.specs import TAG
    >>> SqliteReader(tx, TAG).get_by_name("wan")  # doctest: +SKIP
"""

import sqlite3
from collections import defaultdict
from typing import Any
from uuid import UUID

from vakr.adapters.sqlite.encoding import blob_to_uuid
from vakr.adapters.sqlite.errors import ErrorContext
from vakr.adapters.sqlite.metadata.paging import fetch_page
from vakr.adapters.sqlite.metadata.specs import Link, Spec
from vakr.adapters.sqlite.metadata.transaction import SqliteTransaction
from vakr.domain.errors import NotFoundError, StorageError
from vakr.domain.paging import Page, PageRequest


class SqliteReader[E, I: UUID, F]:
    """The read half of a repository for one record type."""

    def __init__(self, tx: SqliteTransaction, spec: Spec) -> None:
        """Bind to a unit of work's transaction.

        Args:
            tx: The transaction.
            spec: How the records are stored.

        Returns:
            None

        Raises:
            None
        """
        self._tx = tx
        self._spec = spec
        # Identifiers come from the Spec constants; only values are bound.
        self._select = f"SELECT {', '.join(spec.columns)} FROM {spec.table}"  # noqa: S608

    def get(self, entity_id: I) -> E | None:
        """Return a record by ID.

        Args:
            entity_id: Record ID.

        Returns:
            E | None: The record, or ``None``.

        Raises:
            StorageError: If the database cannot be read or the row is damaged.
        """
        return self._one("id", entity_id.bytes)

    def get_by_name(self, name: str) -> E | None:
        """Return a record by exact name.

        Args:
            name: Natural key, compared by code point.

        Returns:
            E | None: The record, or ``None``.

        Raises:
            StorageError: If the database cannot be read or the row is damaged.
        """
        return self._one("name", name)

    def list_page(self, page: PageRequest, where: F | None = None) -> Page[E]:
        """Return one page of matching records in the spec's order.

        Args:
            page: Limit and cursor.
            where: Filter, or ``None``.

        Returns:
            Page[E]: The page, with ``total``.

        Raises:
            ValidationError: If the cursor is invalid.
            StorageError: If the database cannot be read.
        """
        rows, cursor, total = fetch_page(
            self._tx, self._spec.listing, self._spec.where(where), page
        )
        return Page(tuple(self._decode(rows)), cursor, total)

    def _one(self, column: str, value: object) -> Any:
        """Return the record whose unique ``column`` equals ``value``.

        Args:
            column: ``id`` or ``name``.
            value: Bound value.

        Returns:
            Any: The record, or ``None``.

        Raises:
            StorageError: If the database cannot be read or the row is damaged.
        """
        rows = self._tx.query(f"{self._select} WHERE {column} = ?", (value,))
        found = self._decode(rows)
        return found[0] if found else None

    def _stored(self, key: UUID) -> Any:
        """Return the stored record or raise.

        Args:
            key: Record ID.

        Returns:
            Any: The record.

        Raises:
            NotFoundError: If it does not exist.
        """
        stored = self._one("id", key.bytes)
        if stored is None:
            raise NotFoundError(self._spec.entity, key)
        return stored

    def _exists(self, table: str, key: UUID) -> bool:
        """Tell whether ``table`` has a row with this ID.

        Args:
            table: Table name from a constant.
            key: Record ID.

        Returns:
            bool: Whether it exists.

        Raises:
            StorageError: If the database cannot be read.
        """
        sql = f"SELECT 1 FROM {table} WHERE id = ?"  # noqa: S608  # table is a constant
        return bool(self._tx.query(sql, (key.bytes,)))

    def _decode(self, rows: list[sqlite3.Row]) -> list[Any]:
        """Decode rows and their set-valued fields into records.

        Args:
            rows: Rows in ``spec.columns`` order.

        Returns:
            list[Any]: The records, in row order.

        Raises:
            StorageError: If a row no longer forms a valid record.
        """
        if not rows:
            return []
        owners = [row["id"] for row in rows]
        sets = {link.attr: self._members(link, owners) for link in self._spec.links}
        try:
            return [
                self._spec.decode(row, {a: s.get(row["id"], frozenset()) for a, s in sets.items()})
                for row in rows
            ]
        except ValueError:  # ValidationError included: the stored row breaks a domain rule
            key = blob_to_uuid(rows[0]["id"]) if len(rows) == 1 else "in this list"
            raise StorageError(
                f"a stored {self._spec.entity} ({key}) is not valid; run `vakr db status`",
                retryable=False,
            ) from None

    def _members(self, link: Link, owners: list[bytes]) -> dict[bytes, frozenset[UUID]]:
        """Load one membership table for several owners at once.

        Args:
            link: The membership table.
            owners: Owner ID blobs (at most a page, so well under SQLite's
                bound-parameter limit).

        Returns:
            dict[bytes, frozenset[UUID]]: Owner blob to member IDs.

        Raises:
            StorageError: If the database cannot be read.
        """
        marks = ", ".join("?" for _ in owners)
        kind = "" if link.kind is None else " AND kind = ?"
        sql = (
            f"SELECT {link.owner}, {link.member} FROM {link.table} "  # noqa: S608  # constants
            f"WHERE {link.owner} IN ({marks}){kind}"
        )
        params = (*owners, link.kind) if link.kind is not None else tuple(owners)
        grouped: defaultdict[bytes, set[UUID]] = defaultdict(set)
        for row in self._tx.query(sql, params, ErrorContext(self._spec.entity)):
            grouped[row[0]].add(blob_to_uuid(row[1]))
        return {owner: frozenset(members) for owner, members in grouped.items()}
