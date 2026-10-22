"""The overview's recent-event feeds, from the sources the stores expose today (12.2 widget 7).

Each feed is an ``EventFeed``; a kind no store records yet is
``available=False`` with the reason (UI-014), never an empty list that
would read as "nothing happened":

- ``restart``: management-agent restarts (``sysUpTime`` reset) found by the
  status snapshot within ``status.recent_restart_window``, per position.
  ``sysUpTime`` is management-subsystem uptime (UPTIME-003), so the text
  says "agent restarted", never "device rebooted";
- ``rediscovery``: manual rediscoveries (admin operations, E.32) requested
  in the last 24 hours, newest first, with their outcome; scheduled
  rediscoveries are not recorded as events (each device shows its
  discovery age);
- ``identity_review``: open identity reviews (IFID-004), newest first;
- ``telemetry_loss``: spool incidents (STORE-007) of the last 24 hours,
  newest first, from the injected reader; not available without one.

Dependencies:
    vakr.ports.metadata: Identity reviews and admin operations.
    vakr.query.operation_text: Operation outcomes in operator words.
    vakr.ingest.incidents: The incident record (type only).
    vakr.query.snapshot, overview_views, history_limits: Inputs, views, caps.

Safety:
    Event texts hold names, counts, and times; incident details are the
    spool's own operator sentences, which carry no secrets.

Example:
    >>> LOSS_WINDOW == REDISCOVERY_WINDOW
    True
"""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

from vakr.domain.paging import PageRequest
from vakr.domain.reviews import ReviewState
from vakr.ingest.incidents import LossIncident
from vakr.ports.metadata import MetadataUnitOfWorkFactory
from vakr.ports.metadata.reviews import ReviewFilter
from vakr.query.history_limits import EVENTS_SHOWN, REVIEWS_READ
from vakr.query.operation_text import outcome_words
from vakr.query.overview_views import EventFeed, EventRow
from vakr.query.snapshot import Inventory, TelemetrySnapshot, pages
from vakr.query.text import age_text

LOSS_WINDOW = timedelta(hours=24)
"""Telemetry-loss incidents shown on the overview (E.28)."""

REDISCOVERY_WINDOW = timedelta(hours=24)
"""Manual rediscoveries shown on the overview (E.32)."""


def _feed(kind: str, rows: list[EventRow], reason: str | None) -> EventFeed:
    """Return an available feed, newest first, capped at ``EVENTS_SHOWN``.

    Args:
        kind: The event kind.
        rows: Events in any order.
        reason: What the feed covers, if worth saying.

    Returns:
        EventFeed: The feed.

    Raises:
        None
    """
    rows.sort(key=lambda r: (r.at, r.subject), reverse=True)
    return EventFeed(kind, True, reason, tuple(rows[:EVENTS_SHOWN]), len(rows))


def _restarts(inv: Inventory, snap: TelemetrySnapshot, window_s: float) -> EventFeed:
    """List recent agent restarts per position.

    Args:
        inv: The inventory (device names, positions).
        snap: The status snapshot with restart times.
        window_s: The restart window, for the feed's description.

    Returns:
        EventFeed: The restart feed; unavailable when telemetry is unreadable.

    Raises:
        None
    """
    if not snap.available:
        return EventFeed("restart", False, f"telemetry unavailable: {snap.detail}", (), 0)
    names = {d.id: d.name for d in inv.devices}
    where = {c.id: c.position for c in inv.collectors}
    rows = [
        EventRow(
            "restart",
            at,
            names.get(device, str(device)),
            device,
            where.get(col, ""),
            "management agent restarted (sysUpTime reset)",
        )
        for (col, device), at in snap.resets.items()
    ]
    return _feed("restart", rows, f"restarts detected in the last {age_text(window_s)}")


def _rediscoveries(metadata: MetadataUnitOfWorkFactory, inv: Inventory, now: datetime) -> EventFeed:
    """List manual rediscoveries requested in the last 24 hours, newest first.

    Args:
        metadata: Opens a metadata unit of work.
        inv: The inventory, for device names and positions.
        now: Reference time (UTC).

    Returns:
        EventFeed: The rediscovery feed.

    Raises:
        StorageError: If the metadata store cannot be read.
    """
    names = {d.id: d.name for d in inv.devices}
    where = {c.id: c.position for c in inv.collectors}
    with metadata() as uow:
        found = uow.operations.list_page(PageRequest(limit=EVENTS_SHOWN)).items
    since = now - REDISCOVERY_WINDOW
    rows = [
        EventRow(
            "rediscovery",
            op.finished_at or op.started_at or op.requested_at,
            names.get(op.device_id, str(op.device_id)),
            op.device_id,
            where.get(op.collector_id, "") if op.collector_id else "",
            f"manual rediscovery {outcome_words(op)} (requested by {op.requested_by})",
        )
        for op in found
        if op.requested_at >= since
    ]
    reason = "manual rediscoveries in the last 24 h; scheduled ones show as each device's age"
    return _feed("rediscovery", rows, reason)


def _reviews(metadata: MetadataUnitOfWorkFactory, inv: Inventory) -> EventFeed:
    """List open identity reviews, newest first.

    Args:
        metadata: Opens a metadata unit of work.
        inv: The inventory, for device names.

    Returns:
        EventFeed: The review feed.

    Raises:
        StorageError: If the metadata store cannot be read.
    """
    names = {d.id: d.name for d in inv.devices}
    with metadata() as uow:
        found, _ = pages(
            lambda p: uow.reviews.list_page(p, ReviewFilter(state=ReviewState.OPEN)),
            REVIEWS_READ,
        )
    rows = [
        EventRow(
            "identity_review",
            r.opened_at,
            names.get(r.device_id, str(r.device_id)),
            r.device_id,
            "",
            f"{r.if_name or r.if_descr}: identity awaits review ({len(r.candidates)} candidate(s))",
        )
        for r in found
    ]
    return _feed("identity_review", rows, None)


def _losses(read: Callable[[], Sequence[LossIncident]] | None, now: datetime) -> EventFeed:
    """List telemetry-loss incidents of the last 24 hours.

    Args:
        read: Reads recorded incidents, or ``None`` when no reader is wired.
        now: Reference time (UTC).

    Returns:
        EventFeed: The loss feed; unavailable without a reader.

    Raises:
        None
    """
    if read is None:
        return EventFeed("telemetry_loss", False, "not available: no spool incident reader", (), 0)
    since = now - LOSS_WINDOW
    rows = []
    for inc in read():
        at = datetime.fromtimestamp(inc.detected_us / 1_000_000, UTC)
        if at >= since:
            rows.append(
                EventRow(
                    "telemetry_loss",
                    at,
                    inc.segment or "spool",
                    None,
                    "",
                    f"{inc.kind.value}: {inc.detail}",
                )
            )
    return _feed("telemetry_loss", rows, "incidents in the last 24 h")


def events(
    metadata: MetadataUnitOfWorkFactory,
    inv: Inventory,
    snap: TelemetrySnapshot,
    incidents: Callable[[], Sequence[LossIncident]] | None,
    now: datetime,
    restart_window_s: float = 3600.0,
) -> tuple[EventFeed, ...]:
    """Return the four feeds: restart, rediscovery, identity review, telemetry loss.

    Args:
        metadata: Opens a metadata unit of work.
        inv: The inventory.
        snap: The status snapshot.
        incidents: Reads telemetry-loss incidents, or ``None``.
        now: Reference time (UTC).
        restart_window_s: ``status.recent_restart_window`` in seconds.

    Returns:
        tuple[EventFeed, ...]: The feeds in that order.

    Raises:
        StorageError: If the metadata store cannot be read.
    """
    return (
        _restarts(inv, snap, restart_window_s),
        _rediscoveries(metadata, inv, now),
        _reviews(metadata, inv),
        _losses(incidents, now),
    )
