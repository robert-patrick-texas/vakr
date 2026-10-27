"""Serve this node's own observations to the other position (MPOS-004, MPOS-005, ADR-024).

``ObservationSource`` answers the two replication endpoints a partner pulls:

- ``state()``: this node's role and position, and each of its own
  collectors with the oldest and newest seq its replication log holds;
- ``page(collector, after, limit)``: the observations logged after
  ``after``, oldest first, as checksummed envelopes (6.3), within
  ``MAX_FEED_LIMIT`` observations and ``BYTE_BUDGET`` bytes, so an answer
  stays well under the partner client's 8 MiB cap. ``next_after``,
  ``has_more``, ``oldest_seq``, ``newest_seq``, and ``missing`` let the
  partner report a gap honestly instead of guessing.

Only collectors this node ingested are served: a request for any other
collector, the partner's own included, is a ``NotFoundError`` (404).

Dependencies:
    json: Envelope sizes for the byte budget.
    vakr.domain.envelope: Envelopes, exactly as the spool carries them.
    vakr.ports: The feed and the metadata unit of work.

Safety:
    Observations carry no secrets; the answers hold collector UUIDs, names,
    and positions, which the partner already has from metadata.

Example:
    >>> BYTE_BUDGET < 8 * 1024 * 1024
    True
"""

import json
from collections.abc import Callable
from typing import Any

from vakr.domain.envelope import envelope_to_dict, seal
from vakr.domain.errors import NotFoundError, StorageError, ValidationError
from vakr.domain.ids import BatchId, CollectorId, new_id
from vakr.ports.metadata.unit_of_work import MetadataUnitOfWorkFactory
from vakr.ports.telemetry.replication import FeedPage, ObservationFeed

MAX_FEED_LIMIT = 10_000
"""Most observations one page holds (E.13)."""
BYTE_BUDGET = 6 * 1024 * 1024
"""Envelope bytes one page holds at most: room under the client's 8 MiB cap for the rest."""


class ObservationSource:
    """The serving half of observation replication, behind the API's replication routes."""

    def __init__(
        self,
        local_ids: Callable[[], tuple[CollectorId, ...]],
        feed: Callable[[], ObservationFeed | None],
        uow_factory: MetadataUnitOfWorkFactory,
    ) -> None:
        """Bind the source.

        Args:
            local_ids: This node's own collector UUIDs (its identity file).
            feed: Opens the telemetry store's feed; ``None`` without telemetry.
            uow_factory: Metadata units of work, for collector names and positions.

        Returns:
            None

        Raises:
            None
        """
        self._local_ids, self._feed, self._uow = local_ids, feed, uow_factory

    def state(self) -> list[dict[str, Any]]:
        """Describe each of this node's own collectors and its log bounds.

        Args:
            None

        Returns:
            list[dict]: ``id``, ``name``, ``position``, ``oldest_seq``, ``newest_seq``.

        Raises:
            StorageError: If a database cannot be read.
        """
        feed, found = self._feed(), []
        with self._uow() as uow:
            for cid in self._local_ids():
                record = uow.collectors.get(cid)
                oldest, newest = feed.bounds(cid) if feed is not None else (None, None)
                found.append({"id": str(cid), "name": record.name if record else None,
                              "position": record.position if record else None,
                              "oldest_seq": oldest, "newest_seq": newest})  # fmt: skip
        return found

    def page(self, collector: CollectorId, after: int, limit: int) -> dict[str, Any]:
        """Return one page of a local collector's logged observations.

        Args:
            collector: One of this node's collectors.
            after: Last seq the partner holds (0 for none).
            limit: 1 to ``MAX_FEED_LIMIT`` observations.

        Returns:
            dict: The page, envelopes oldest first.

        Raises:
            NotFoundError: If ``collector`` is not this node's own.
            ValidationError: If ``after`` or ``limit`` is out of range.
            StorageError: If telemetry is unavailable or cannot be read.
        """
        if collector not in self._local_ids():
            raise NotFoundError("collector", collector)
        if after < 0 or not 1 <= limit <= MAX_FEED_LIMIT:
            raise ValidationError(f"after must be 0 or more and limit 1 to {MAX_FEED_LIMIT}")
        feed = self._feed()
        if feed is None:
            raise StorageError("no telemetry database yet; start the collector", retryable=True)
        return _answer(collector, after, feed.feed(collector, after, limit))


def _answer(collector: CollectorId, after: int, page: FeedPage) -> dict[str, Any]:
    """Encode a page, stopping early at ``BYTE_BUDGET`` (the rest follows next time).

    Args:
        collector: The collector served.
        after: The request's ``after``.
        page: The feed's page.

    Returns:
        dict: The wire form of the page.

    Raises:
        None
    """
    batch, size, served, last = new_id(BatchId), 0, [], after
    more = page.has_more
    for entry in page.entries:
        wire = envelope_to_dict(seal(entry.observation, batch))
        size += len(json.dumps(wire, separators=(",", ":"), default=str))
        if served and size > BYTE_BUDGET:
            more = True
            break
        served.append({"seq": entry.seq, "envelope": wire})
        last = entry.seq
    else:
        last = page.next_after  # entries skipped as missing still advance the cursor
    # Status records ride with their poll's SNMP entry (schema 4, E.35); a partner that
    # predates them ignores the member.
    attached = [{"seq": a.seq, "envelope": envelope_to_dict(seal(a.observation, batch))}
                for a in page.attached if a.seq <= last]  # fmt: skip
    return {"collector": str(collector), "after": after, "next_after": last, "has_more": more,
            "oldest_seq": page.oldest_seq, "newest_seq": page.newest_seq,
            "missing": sum(1 for seq in page.missing if seq <= last),
            "observations": served, "attachments": attached}  # fmt: skip
