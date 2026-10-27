"""Which interfaces a Stage 1 status record shows not up/up now (E.6, E.35, invariant 1).

An interface that is not up/up gets no traffic samples (E.27 #7), so its
newest sample says "up" long after the link went down. The interface table
reads, for each interface whose lifecycle is ``inactive``, its newest status
record from the shown collector; when that record is newer than the newest
sample and not up/up, the row shows that status. Nothing else is called
"not up/up": ``stale`` and ``retired`` come from discovery and the
administrator, not from Stage 1, and a row without evidence stays missing.

Dependencies:
    vakr.ports.telemetry.acceptance: ``status_marks``.
    vakr.domain: Lifecycle, IF-MIB statuses, windows.

Safety:
    Read-only; at most ``MAX_STATUS_READS`` one-row reads per view, each bounded
    to the native retention window.

Example:
    >>> from vakr.domain.enums import IfOperStatus
    >>> int_status(IfOperStatus, 2).name, int_status(IfOperStatus, 99)
    ('DOWN', None)
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from enum import IntEnum

from vakr.domain.enums import InterfaceLifecycle
from vakr.domain.errors import StorageError
from vakr.domain.ids import CollectorId, InterfaceId
from vakr.domain.interfaces import Interface
from vakr.domain.series import TimeWindow
from vakr.domain.state import CurrentInterfaceState
from vakr.domain.timestamps import to_epoch_us
from vakr.ports.telemetry import TelemetryBackend
from vakr.ports.telemetry.acceptance import StatusMark

MAX_STATUS_READS = 500
"""Inactive interfaces one view looks up at most (the E.19 page maximum)."""

LOOKBACK = timedelta(days=30)
"""Status records are native data, kept 30 days (E.10)."""


def int_status[E: IntEnum](kind: type[E], value: int | None) -> E | None:
    """Return an IF-MIB integer as its enum member, ``None`` when unknown.

    Args:
        kind: ``IfAdminStatus`` or ``IfOperStatus``.
        value: The stored integer.

    Returns:
        E | None: The member, or ``None`` for a missing or unknown value.

    Raises:
        None
    """
    try:
        return None if value is None else kind(value)
    except ValueError:
        return None


def down_statuses(
    open_backend: Callable[[], TelemetryBackend] | None, interfaces: Sequence[Interface],
    collector: CollectorId | None, states: Mapping[InterfaceId, CurrentInterfaceState],
    now: datetime,
) -> dict[InterfaceId, StatusMark]:  # fmt: skip
    """Return each inactive interface's newest status record when it is not up/up and newer.

    Args:
        open_backend: Opens a telemetry view, or ``None`` without a store.
        interfaces: The interfaces shown.
        collector: The collector whose evidence is shown, or ``None``.
        states: Their newest samples from that collector.
        now: The view time (UTC).

    Returns:
        dict[InterfaceId, StatusMark]: Interfaces Stage 1 last read not up/up.

    Raises:
        None
    """
    if open_backend is None or collector is None:
        return {}
    try:
        return _down(open_backend(), interfaces, collector, states, now)
    except StorageError:
        return {}  # the rows keep their sampled state; the view reports telemetry trouble


def _down(
    backend: TelemetryBackend, interfaces: Sequence[Interface], collector: CollectorId,
    states: Mapping[InterfaceId, CurrentInterfaceState], now: datetime,
) -> dict[InterfaceId, StatusMark]:  # fmt: skip
    """Read the status records ``down_statuses`` needs.

    Args:
        backend: An open telemetry view.
        interfaces: The interfaces shown.
        collector: The collector shown.
        states: Their newest samples.
        now: The view time.

    Returns:
        dict[InterfaceId, StatusMark]: As ``down_statuses``.

    Raises:
        StorageError: If the store is unavailable.
    """
    acceptance = getattr(backend, "acceptance", None)
    if acceptance is None:
        return {}
    window = TimeWindow(now - LOOKBACK, now + timedelta(seconds=1))
    found: dict[InterfaceId, StatusMark] = {}
    inactive = [i for i in interfaces if i.lifecycle is InterfaceLifecycle.INACTIVE]
    for iface in inactive[:MAX_STATUS_READS]:
        marks = acceptance.status_marks(iface.id, collector, window, limit=1, newest_first=True)
        mark = marks.items[0] if marks.items else None
        sample = states.get(iface.id)
        newer = sample is None or (
            mark is not None and mark.ts_us > to_epoch_us(sample.observed_at)
        )
        if mark is not None and newer and not mark.up_up:
            found[iface.id] = mark
    return found
