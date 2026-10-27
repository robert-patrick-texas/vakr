"""Metadata replication port: what a primary exports and a peer applies (MPOS-003, ADR-024).

A peer copies the primary's replicated metadata as rows, table by table,
so every record keeps its UUID, its version, and its ownership exactly; it
never replays edits through the repositories, whose version and ownership
rules are about local writers. ``ReplicaState`` carries a digest of the
whole replicated set: a peer pulls only when it differs from the digest it
last applied, and applies a pull only if the rows it received hash to the
digest the primary advertised, so a snapshot torn by a concurrent edit is
never applied.

Dependencies:
    dataclasses, typing: The value types and the protocol.

Safety:
    Values are JSON-safe (``bytes`` travel as ``{"b": base64}``), so a
    page can cross the wire unchanged. Password hashes are among them, which
    is why only a replication token may read a page (MPOS-006).

Example:
    >>> ReplicaPage("tag", ("id", "name"), (), None).next_cursor is None
    True
"""

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

type Cell = int | float | str | dict[str, str] | None
"""One encoded column value; ``{"b": "<base64>"}`` stands for ``bytes``."""
MAX_DISCARDED = 1000
"""Discarded rows one replacing apply lists (E.39); the total is always counted."""


@dataclass(frozen=True, slots=True)
class ReplicaState:
    """What the primary's replicated metadata is now.

    Attributes:
        schema: Metadata schema version; peer and primary must match.
        digest: SHA-256 (hex) over every replicated row, in table and key order.
        rows: Row count per replicated table.
    """

    schema: int
    digest: str
    rows: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReplicaPage:
    """One page of one table, in primary-key order.

    Attributes:
        table: The table.
        columns: Column names, in schema order.
        rows: Encoded rows, one value per column.
        next_cursor: Where the next page starts, or ``None`` at the end.
    """

    table: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Cell, ...], ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class DiscardedRow:
    """A local row a replacing apply removed or overwrote (``vakr node demote``, ADR-024).

    Attributes:
        table: The replicated table.
        key: The row's key as text (UUIDs in their usual form).
        label: A name to recognise it by (name, username, label), or ``""``.
        change: ``removed`` (the snapshot has no such row) or ``replaced``.
    """

    table: str
    key: str
    label: str
    change: str


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """What applying a snapshot changed on the peer.

    Attributes:
        written: Rows inserted or updated, per table.
        removed: Rows removed, per table (membership and dashboards; any table when replacing).
        discarded: Local rows a replacing apply removed or overwrote, at most ``MAX_DISCARDED``.
        discarded_total: How many there were in all.
    """

    written: Mapping[str, int]
    removed: Mapping[str, int]
    discarded: tuple[DiscardedRow, ...] = ()
    discarded_total: int = 0


class MetadataReplica(Protocol):
    """Export and apply the replicated metadata of one store."""

    def tables(self) -> tuple[str, ...]:
        """Name the replicated tables, parents before children.

        Args:
            None

        Returns:
            tuple[str, ...]: The tables, in apply order.

        Raises:
            None
        """
        ...

    def state(self) -> ReplicaState:
        """Describe the replicated metadata now.

        Args:
            None

        Returns:
            ReplicaState: Schema, digest, and counts.

        Raises:
            ValidationError: If the set exceeds its bound (E.13).
            StorageError: If the store cannot be read.
        """
        ...

    def page(self, table: str, cursor: str | None, limit: int) -> ReplicaPage:
        """Return one page of one replicated table.

        Args:
            table: One of ``tables()``.
            cursor: ``next_cursor`` of the previous page, or ``None``.
            limit: Rows, within the page bound.

        Returns:
            ReplicaPage: The page.

        Raises:
            ValidationError: For an unknown table, a bad cursor, or a limit out of range.
            StorageError: If the store cannot be read.
        """
        ...

    def apply(self, snapshot: Mapping[str, tuple[ReplicaPage, ...]], digest: str, *,
              replace: bool = False) -> ApplyResult:  # fmt: skip
        """Make this store's replicated metadata equal to a pulled snapshot, atomically.

        Args:
            snapshot: Every page of every table, by table.
            digest: The digest the primary advertised for it.
            replace: Also remove or overwrite every local row the snapshot does
                not hold exactly, entity rows and closed reviews included: the
                resync of a demoted primary (ADR-024). Collector records stay.

        Returns:
            ApplyResult: What changed.

        Raises:
            ValidationError: If the snapshot is incomplete, has other columns,
                or does not hash to ``digest`` (nothing is written).
            StorageError: If the write fails (nothing is written).
        """
        ...


def encode_cell(value: Any) -> Cell:
    """Encode one column value for the wire.

    Args:
        value: An SQLite value.

    Returns:
        Cell: The value, with ``bytes`` as ``{"b": base64}``.

    Raises:
        None
    """
    if isinstance(value, bytes):
        return {"b": base64.b64encode(value).decode("ascii")}
    return value


def decode_cell(value: Cell) -> int | float | str | bytes | None:
    """Decode one wire value back to an SQLite value.

    Args:
        value: An encoded cell.

    Returns:
        int | float | str | bytes | None: The value.

    Raises:
        ValueError: For a malformed ``{"b": ...}`` cell or another mapping.
    """
    if isinstance(value, dict):
        if set(value) != {"b"}:
            raise ValueError("a mapping cell must be {'b': base64}")
        try:
            return base64.b64decode(value["b"], validate=True)
        except binascii.Error, TypeError:
            raise ValueError("a bytes cell is not valid base64") from None
    return value
