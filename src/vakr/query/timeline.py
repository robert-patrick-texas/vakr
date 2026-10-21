"""Interface state timelines from native samples (12.5 "state timeline", E.19 gaps).

Every interface sample carries ``ifAdminStatus`` and ``ifOperStatus`` as
read in that poll. ``segments`` merges consecutive samples with the same
pair into one segment that lasts until the next different sample. A step
longer than ``GAP_FACTOR`` (1.5) times the effective poll interval ends
the segment one interval after its last sample and opens a segment without
samples (``admin``/``oper`` ``None``, ``samples`` 0), so an outage is
never drawn as "up". Rollups keep no status, so the timeline covers only
native data, and at most the last 7 days of a request (E.28).

Dependencies:
    vakr.domain: Interface observations and windows.
    vakr.ports.telemetry: Raw reads.
    vakr.query.history_limits, latency_views, snapshot: Bounds, views, paging.

Safety:
    ``segments`` is pure; unknown status stays ``None``. Reads are bounded.

Example:
    >>> from datetime import UTC, datetime
    >>> w = TimeWindow(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, 1, tzinfo=UTC))
    >>> [(s.oper, s.samples) for s in segments([], w, 60)]
    [(None, 0)]
"""

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta

from vakr.domain.catalog import Collector
from vakr.domain.enums import IfAdminStatus, IfOperStatus, ObservationType
from vakr.domain.observations import InterfaceObservation
from vakr.domain.series import TimeWindow
from vakr.ports.telemetry import TelemetryBackend
from vakr.query.history_limits import GAP_FACTOR, TIMELINE_MAX_OBSERVATIONS
from vakr.query.latency_views import StateSegment, TimelineView
from vakr.query.snapshot import pages


def status_word(value: IfAdminStatus | IfOperStatus | None) -> str | None:
    """Return the IF-MIB status as a word, such as ``lower-layer-down``.

    Args:
        value: The status, if read.

    Returns:
        str | None: The word, or ``None`` when not read.

    Raises:
        None
    """
    return None if value is None else value.name.lower().replace("_", "-")


def _blank(start: datetime, end: datetime) -> list[StateSegment]:
    """Return a segment without samples, or nothing when it would be empty.

    Args:
        start: Start.
        end: End.

    Returns:
        list[StateSegment]: Zero or one segment.

    Raises:
        None
    """
    return [StateSegment(start, end, None, None, 0)] if end > start else []


def segments(
    samples: Sequence[InterfaceObservation], window: TimeWindow, interval_s: float
) -> tuple[StateSegment, ...]:
    """Merge samples into status segments covering the window.

    Args:
        samples: Interface samples in time order.
        window: The window.
        interval_s: Effective poll interval in seconds.

    Returns:
        tuple[StateSegment, ...]: Contiguous segments from window start to end.

    Raises:
        None
    """
    limit, step = timedelta(seconds=GAP_FACTOR * interval_s), timedelta(seconds=interval_s)
    if not samples:
        return tuple(_blank(window.start, window.end))
    out: list[StateSegment] = []
    first = samples[0].timestamp
    out += _blank(window.start, first) if first - window.start > limit else []
    start, last, count = (window.start if not out else first), first, 0
    key = (status_word(samples[0].admin_status), status_word(samples[0].oper_status))
    for o in samples:
        now = (status_word(o.admin_status), status_word(o.oper_status))
        if o.timestamp - last > limit:
            out.append(StateSegment(start, last + step, *key, count))
            out += _blank(last + step, o.timestamp)
            start, key, count = o.timestamp, now, 0
        elif now != key:
            out.append(StateSegment(start, o.timestamp, *key, count))
            start, key, count = o.timestamp, now, 0
        last, count = o.timestamp, count + 1
    end = window.end if window.end - last <= limit else min(last + step, window.end)
    out.append(StateSegment(start, end, *key, count))
    out += _blank(end, window.end)
    return tuple(out)


def read_timeline(
    backend: TelemetryBackend,
    base: TimelineView,
    collector: Collector,
    window: TimeWindow,
    interval_s: float,
) -> TimelineView:
    """Read one position's samples (at most ``TIMELINE_MAX_OBSERVATIONS``) and fill a view.

    Args:
        backend: The telemetry view.
        base: The view to fill in (its ``interface_id`` names the interface).
        collector: The position.
        window: The window, at most 7 days.
        interval_s: The position's effective poll interval.

    Returns:
        TimelineView: The filled, available view.

    Raises:
        StorageError: If the store cannot be read.
    """
    found, more = pages(
        lambda p: backend.query.read_raw(
            ObservationType.INTERFACE, base.interface_id, window, p, collector_id=collector.id
        ),
        TIMELINE_MAX_OBSERVATIONS,
    )
    samples = [o for o in found if isinstance(o, InterfaceObservation)]
    return replace(
        base,
        collector_id=collector.id,
        position=collector.position,
        segments=segments(samples, window, interval_s),
        truncated=more,
        available=True,
        reason=None,
    )
