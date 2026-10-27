"""Pull the other position's observations, oldest first, idempotently (MPOS-004, MPOS-005).

One ``pull_once`` asks the partner which collectors it serves
(``observations/state``), then, for each, pages ``observations`` after the
cursor held here. Each page is verified (envelope checksums, every
observation the source collector's) and stored with the new cursor in one
transaction (``ReplicaWriter.write_replicated``), so a crash never skips one
and a replay changes nothing. The pass stops when the partner has nothing
newer or the pacing budget is spent; the rest follows next pass. A partner
that no longer holds what this node has not pulled yet is a gap: it is
counted and reported, never hidden (MPOS-005).

Dependencies:
    vakr.domain.envelope: Envelope verification.
    vakr.replication.client, observation_status: The partner and the record.
    vakr.ports.telemetry.replication: The writer.

Safety:
    Nothing from the partner is trusted unchecked: shapes, UUIDs, seqs, and
    checksums are verified before anything is stored. Errors name the
    partner URL, never the token.

Example:
    >>> PAGE_LIMIT
    10000
"""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from vakr.domain.envelope import envelope_from_dict, open_envelope
from vakr.domain.errors import StorageError, ValidationError
from vakr.domain.ids import CollectorId
from vakr.domain.observations import Observation
from vakr.ports.clock import Clock
from vakr.ports.telemetry.replication import ReplicaWriter
from vakr.replication import observation_status as rec
from vakr.replication.attachments import verified_attachments
from vakr.replication.client import EgressError, PartnerError, PartnerReader

PAGE_LIMIT = 10_000
"""Observations asked for per page (E.13)."""


class ObservationPullError(Exception):
    """A pull failed; the record says why, and the next pass tries again."""


class ObservationPuller:
    """Pulls a partner's collectors into this node's telemetry."""

    def __init__(
        self,
        client: PartnerReader,
        writer: ReplicaWriter,
        record: Path,
        clock: Clock,
        local: CollectorId | None,
        register: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        """Bind the puller.

        Args:
            client: The partner client (URL and token).
            writer: Stores pages with their cursor.
            record: The status record file.
            clock: Time source.
            local: This node's own collector, never pulled from a partner.
            register: Records a partner collector in metadata (the primary),
                or ``None`` where metadata arrives by replication (the peer).

        Returns:
            None

        Raises:
            None
        """
        self._client, self._writer, self._record = client, writer, record
        self._clock, self._local, self._register = clock, local, register

    def pull_once(self, budget: int) -> int:
        """Pull every partner collector until caught up or ``budget`` observations are stored.

        Args:
            budget: Most observations to store this pass (pacing, E.13).

        Returns:
            int: Observations stored for the first time.

        Raises:
            ObservationPullError: When the pull failed (recorded).
        """
        status = rec.read_status(self._record) or rec.blank()
        status.partner_url, status.last_attempt_at = self._client.base_url, self._now()
        try:
            stored = 0
            for source in _collectors(self._client.get_json("observations/state"), self._local):
                if self._register is not None:
                    self._register(source)
                stored += self._drain(source, status, budget - stored)
        except (PartnerError, EgressError, ValidationError, StorageError) as exc:
            status.last_error, status.pulls["failed"] = rec.short_error(str(exc)), \
                status.pulls["failed"] + 1  # fmt: skip
            rec.write_status(self._record, status)
            raise ObservationPullError(status.last_error) from None
        status.last_success_at, status.last_error = status.last_attempt_at, None
        status.pulls["ok"] += 1
        rec.write_status(self._record, status)
        return stored

    def _drain(self, source: dict[str, Any], status: rec.ObservationPullStatus, budget: int) -> int:
        """Page one partner collector after this node's cursor.

        Args:
            source: The collector as the partner described it.
            status: The record, updated in place.
            budget: Most observations to store.

        Returns:
            int: Observations stored for the first time.

        Raises:
            PartnerError, EgressError, ValidationError, StorageError: As ``pull_once``.
        """
        cid = CollectorId(source["id"])
        entry = status.sources.setdefault(str(cid), rec.SourceStatus())
        entry.name, entry.position, entry.newest_seq = source["name"], source["position"], \
            source["newest_seq"]  # fmt: skip
        cursor, stored = self._writer.cursor(cid) or 0, 0
        if source["newest_seq"] is not None and cursor > source["newest_seq"]:
            self._writer.rewind(cid)  # the partner's log restarted: re-read it (idempotent)
            cursor = 0
        oldest = source["oldest_seq"]
        if oldest is not None and cursor < oldest - 1:
            entry.gaps += oldest - 1 - cursor  # the partner purged these before we asked
        while budget - stored > 0:
            query = {"collector": str(cid), "after": cursor,
                     "limit": min(PAGE_LIMIT, budget - stored)}  # fmt: skip
            page = _page(self._client.get_json("observations", query), cid, cursor)
            result = self._writer.write_replicated(cid, page.observations, page.next_after)
            stored += result.inserted
            entry.inserted, entry.duplicates = entry.inserted + result.inserted, \
                entry.duplicates + result.duplicates  # fmt: skip
            entry.gaps += page.missing
            entry.cursor, cursor = page.next_after, page.next_after
            entry.newest_seq = page.newest_seq
            if page.observations:
                newest = max(o.timestamp for o in page.observations)
                if entry.newest_ts is None or newest > datetime.fromisoformat(entry.newest_ts):
                    entry.newest_ts = rec.stamp(newest)
            if not page.has_more or (not page.observations and not page.missing):
                break  # caught up, or a partner that answers "more" with nothing new
        entry.cursor = cursor
        return stored

    def _now(self) -> str:
        """Return the current time for the record.

        Args:
            None

        Returns:
            str: ISO 8601 UTC.

        Raises:
            None
        """
        return rec.stamp(self._clock.now_utc())


class _Page:
    """A verified page of partner observations."""

    __slots__ = ("has_more", "missing", "newest_seq", "next_after", "observations")

    def __init__(self, data: dict[str, Any], observations: list[Observation]) -> None:
        """Hold a page's fields.

        Args:
            data: The checked answer.
            observations: Its verified observations.

        Returns:
            None

        Raises:
            None
        """
        self.observations, self.next_after = observations, int(data["next_after"])
        self.has_more, self.missing = bool(data["has_more"]), int(data.get("missing") or 0)
        self.newest_seq = data["newest_seq"]


def _collectors(state: Any, local: CollectorId | None) -> list[dict[str, Any]]:
    """Check the partner's state answer and list the collectors to pull.

    Args:
        state: The parsed ``observations/state`` answer.
        local: This node's own collector.

    Returns:
        list[dict]: Each with a valid ``id``; never this node's own.

    Raises:
        ValidationError: If the answer is malformed, or names this node's collector
            (the partner URL points back at this node).
    """
    collectors = state.get("collectors") if isinstance(state, dict) else None
    if not isinstance(collectors, list) or len(collectors) > rec.MAX_SOURCES:
        raise ValidationError("the partner's observation state is malformed")
    found = []
    for c in collectors:
        if not isinstance(c, dict) or not _seq_or_none(c.get("oldest_seq")) \
                or not _seq_or_none(c.get("newest_seq")):  # fmt: skip
            raise ValidationError("the partner's observation state is malformed")
        try:
            cid = CollectorId(UUID(str(c.get("id"))))
        except ValueError:
            raise ValidationError("the partner's observation state is malformed") from None
        if cid == local:
            raise ValidationError("the partner serves this node's own collector; check "
                                  "node.primary_url or node.peer_url")  # fmt: skip
        found.append({"id": cid, "name": _text(c.get("name")),
                      "position": _text(c.get("position")), "oldest_seq": c.get("oldest_seq"),
                      "newest_seq": c.get("newest_seq")})  # fmt: skip
    return found


def _page(data: Any, source: CollectorId, after: int) -> _Page:
    """Check one page and verify its observations.

    Args:
        data: The parsed ``observations`` answer.
        source: The collector asked for.
        after: The seq asked after.

    Returns:
        _Page: The verified page.

    Raises:
        ValidationError: If the page is malformed, out of order, has a bad
            checksum, or holds another collector's observation.
    """
    ok = isinstance(data, dict) and isinstance(data.get("observations"), list)
    ok = ok and _seq_or_none(data.get("next_after")) and data.get("next_after") is not None
    ok = ok and _seq_or_none(data.get("missing")) and _seq_or_none(data.get("newest_seq"))
    if not ok or data["next_after"] < after or not isinstance(data.get("has_more"), bool):
        raise ValidationError("the partner's observation page is malformed")
    observations, last = [], after
    for item in data["observations"]:
        seq = item.get("seq") if isinstance(item, dict) else None
        if not isinstance(seq, int) or seq <= last or seq > data["next_after"]:
            raise ValidationError("the partner's observation page is out of order")
        observation = open_envelope(envelope_from_dict(item.get("envelope") or {}))
        if observation.collector_id != source:
            raise ValidationError("the partner's page holds another collector's observation")
        observations.append(observation)
        last = seq
    observations.extend(verified_attachments(data, source, after))
    return _Page(data, observations)


def _seq_or_none(value: object) -> bool:
    """Tell whether a value is a seq (a non-negative int) or ``None``.

    Args:
        value: The value.

    Returns:
        bool: ``True`` when acceptable.

    Raises:
        None
    """
    return value is None or (isinstance(value, int) and not isinstance(value, bool) and value >= 0)


def _text(value: object) -> str | None:
    """Return a short label from the partner, or ``None``.

    Args:
        value: The value.

    Returns:
        str | None: At most 64 characters.

    Raises:
        None
    """
    return value[:64] if isinstance(value, str) else None
