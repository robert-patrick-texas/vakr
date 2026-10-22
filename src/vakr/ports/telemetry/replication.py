"""Observation replication ports: serve this node's observations, store a partner's (MPOS-004).

A node serves the observations its own spool ingested in ingestion order:
every one has a ``seq`` in the telemetry store's replication log, and a
partner pulls ``after`` the newest seq it holds (27.5 item 3, ADR-024). The
partner stores each page and advances its cursor for that source collector
in one transaction, so a crash between the two can neither skip nor
duplicate an observation; a replayed page is a no-op because observation
UUIDs make inserts idempotent (STORE-009).

Dependencies:
    vakr.domain: Observations and IDs.
    vakr.ports.telemetry.results: ``WriteResult``.

Safety:
    Plain values only; no driver type crosses these interfaces.

Example:
    >>> FeedPage((), 0, False, None, None).newest_seq is None
    True
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from vakr.domain.ids import CollectorId
from vakr.domain.observations import Observation
from vakr.ports.telemetry.results import WriteResult


@dataclass(frozen=True, slots=True)
class FeedEntry:
    """One logged observation with its replication sequence number.

    Attributes:
        seq: Position in this node's replication log.
        observation: The stored observation, exactly as written.
    """

    seq: int
    observation: Observation


@dataclass(frozen=True, slots=True)
class FeedPage:
    """One page of a collector's logged observations, oldest seq first.

    Attributes:
        entries: Observations after the requested seq, in seq order.
        next_after: The seq to ask ``after`` next: the last entry's, or the
            request's when the page is empty.
        has_more: Whether the log holds later entries for the collector.
        oldest_seq: Oldest seq the log still holds for it, or ``None``.
        newest_seq: Newest seq the log holds for it, or ``None``.
        missing: Seqs in this page's range whose observation retention
            already removed (they are reported, not served).
    """

    entries: tuple[FeedEntry, ...]
    next_after: int
    has_more: bool
    oldest_seq: int | None
    newest_seq: int | None
    missing: tuple[int, ...] = ()


class ObservationFeed(Protocol):
    """Reads this node's replication log (the serving side)."""

    def feed(self, collector: CollectorId, after: int, limit: int) -> FeedPage:
        """Return up to ``limit`` observations of ``collector`` logged after ``after``.

        Args:
            collector: A collector whose observations this node ingested.
            after: Last seq the caller holds (0 for none).
            limit: Most entries to return.

        Returns:
            FeedPage: The page.

        Raises:
            ValidationError: If ``after`` or ``limit`` is out of range.
            StorageError: If the store cannot be read.
        """
        ...

    def bounds(self, collector: CollectorId) -> tuple[int | None, int | None]:
        """Return the oldest and newest seq logged for ``collector``.

        Args:
            collector: The collector.

        Returns:
            tuple: ``(oldest, newest)``, both ``None`` when nothing is logged.

        Raises:
            StorageError: If the store cannot be read.
        """
        ...


class ReplicaWriter(Protocol):
    """Stores a partner's observations with their cursor (the pulling side)."""

    def write_replicated(
        self, source: CollectorId, observations: Sequence[Observation], through_seq: int
    ) -> WriteResult:
        """Store a page and set ``source``'s cursor to ``through_seq`` in one transaction.

        Args:
            source: The partner collector the page came from.
            observations: The page's observations, all made by ``source``.
            through_seq: The page's ``next_after``.

        Returns:
            WriteResult: Inserted and duplicate counts.

        Raises:
            ValidationError: If an observation is not ``source``'s, or the
                cursor would move back; nothing is written.
            StorageError: If the store cannot be written; nothing is committed.
        """
        ...

    def rewind(self, source: CollectorId) -> None:
        """Forget ``source``'s cursor: its log restarted, so read it from the start again.

        Args:
            source: The partner collector.

        Returns:
            None

        Raises:
            StorageError: If the store cannot be written.
        """
        ...

    def cursor(self, source: CollectorId) -> int | None:
        """Return the newest partner seq stored for ``source``.

        Args:
            source: A partner collector.

        Returns:
            int | None: The seq, or ``None`` before the first page.

        Raises:
            StorageError: If the store cannot be read.
        """
        ...
