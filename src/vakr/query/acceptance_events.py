"""MVP-ACC-02 "no spikes across device reloads or counter discontinuities", from vakr's own flags.

When a device reloads, or an interface's counters jump (a changed
``ifCounterDiscontinuityTime``, a decrease, a change of counter width),
vakr must store no rate for that sample (RATE-004), so the graph shows a
gap instead of a spike, and the next samples must be plausible again
(below 1.10 times capacity, RATE-009). Rollup buckets carry the union of their
samples' flags, so ``interface_events`` reads only the flagged quarter-hours
natively (plus the native tail past the rollup frontier) and judges each
event sample: a ``reset`` when it has no rate, a ``spike`` when it has one
or a sample right after it is over capacity. ``reload_check`` confirms
that every monitored interface of a reloaded device reset at the reload.

Dependencies:
    vakr.domain, vakr.ports.telemetry.acceptance: Flags and marks.
    vakr.query.acceptance_limits, acceptance_subjects.

Safety:
    Read-only; native reads only around flagged buckets and reloads.

Example:
    >>> events, stray = judge_samples([SampleMark(0, Quality.BASELINE_UPTIME_RESET, 0)])
    >>> events[0][2:], stray
    ((True, False), 0)
"""

from collections.abc import Sequence
from dataclasses import dataclass

from vakr.domain.enums import ObservationType
from vakr.domain.ids import CollectorId, InterfaceId
from vakr.domain.quality import BASELINE_INVALIDATING, Quality
from vakr.domain.series import TimeWindow
from vakr.ports.telemetry.acceptance import AcceptanceQuery, SampleMark
from vakr.query.acceptance_limits import (
    BUCKET_US,
    DEFAULT_INTERVAL_S,
    EVENT_FLAGS,
    SPIKE_FLAG,
    SPIKE_LOOKAHEAD,
    US,
)
from vakr.query.acceptance_subjects import as_time

_IFACE = ObservationType.INTERFACE
RATE_BITS = 0b111111
"""Every interface rate in ``INTERFACE_METRICS`` order."""

_READ_PAD_US = 3 * DEFAULT_INTERVAL_S * US
_RELOAD_LOOK_US = 10 * 60 * US


@dataclass(frozen=True, slots=True)
class InterfaceEvents:
    """The event samples of one interface in the event window.

    Attributes:
        events: ``(time, flags, reset, spike)`` per event sample, oldest first.
        over_capacity: Samples over the plausibility bound that follow no event.
        truncated: Whether a read was cut short.
    """

    events: tuple[tuple[int, Quality, bool, bool], ...]
    over_capacity: int
    truncated: bool


def judge_samples(
    marks: Sequence[SampleMark],
) -> tuple[list[tuple[int, Quality, bool, bool]], int]:
    """Judge the event samples of one ordered run of samples.

    Args:
        marks: Samples by time.

    Returns:
        tuple: ``(time, flags, reset, spike)`` per event, and the over-capacity
        samples that follow no event.

    Raises:
        None
    """
    events: list[tuple[int, Quality, bool, bool]] = []
    after_event: set[int] = set()
    for i, m in enumerate(marks):
        if not m.quality & EVENT_FLAGS:
            continue
        reset = not m.available & RATE_BITS
        follow = marks[i + 1 : i + 1 + SPIKE_LOOKAHEAD]
        spike = not reset or any(f.quality & SPIKE_FLAG for f in follow)
        after_event.update(range(i, i + 1 + SPIKE_LOOKAHEAD))
        events.append((m.ts_us, m.quality & EVENT_FLAGS, reset, spike))
    stray = sum(1 for i, m in enumerate(marks) if m.quality & SPIKE_FLAG and i not in after_event)
    return events, stray


def interface_events(
    query: AcceptanceQuery, iface: InterfaceId, collector: CollectorId,
    window: tuple[int, int], frontier_us: int | None,
) -> InterfaceEvents:  # fmt: skip
    """Find and judge one interface's reloads, discontinuities, and over-capacity samples.

    Args:
        query: The acceptance port.
        iface: The interface.
        collector: The collector.
        window: ``(start_us, end_us)`` of the event window.
        frontier_us: The rollup frontier, or ``None`` without rollups (then every sample is read).

    Returns:
        InterfaceEvents: The judged events.

    Raises:
        StorageError: If the store is unavailable.
    """
    split = window[0] if frontier_us is None else min(max(frontier_us, window[0]), window[1])
    stretches: list[tuple[int, int]] = []
    truncated = False
    if split > window[0]:
        rolled = query.bucket_marks(_IFACE, iface, collector,
                                    TimeWindow(as_time(window[0]), as_time(split)))  # fmt: skip
        truncated = rolled.truncated
        for b in rolled.items:
            if b.quality & (EVENT_FLAGS | SPIKE_FLAG):
                end = min(b.start_us + BUCKET_US + _READ_PAD_US, window[1])
                if stretches and stretches[-1][1] >= b.start_us:
                    stretches[-1] = (stretches[-1][0], end)
                else:
                    stretches.append((b.start_us, end))
    if split < window[1]:
        stretches.append((split, window[1]))
    events: list[tuple[int, Quality, bool, bool]] = []
    stray = 0
    for start, end in stretches:
        marks = query.sample_marks(_IFACE, iface, collector,
                                   TimeWindow(as_time(start), as_time(end)))  # fmt: skip
        truncated = truncated or marks.truncated
        found, extra = judge_samples(marks.items)
        events.extend(found)
        stray += extra
    return InterfaceEvents(tuple(events), stray, truncated)


def reload_check(
    query: AcceptanceQuery, iface: InterfaceId, collector: CollectorId, reload_us: int,
    interval_s: int,
) -> str:  # fmt: skip
    """Judge an interface's first sample after its device reloaded.

    Args:
        query: The acceptance port.
        iface: The interface.
        collector: The collector.
        reload_us: When the SNMP observation detected the restart.
        interval_s: The device's poll interval.

    Returns:
        str: ``reset`` (no rate, an invalidating flag), ``spike`` (a rate across the
        reload), or ``unsampled`` (no sample within 10 minutes).

    Raises:
        StorageError: If the store is unavailable.
    """
    start = reload_us - interval_s * US // 2
    window = TimeWindow(as_time(start), as_time(reload_us + _RELOAD_LOOK_US))
    marks = query.sample_marks(_IFACE, iface, collector, window, limit=1)
    if not marks.items:
        return "unsampled"
    first = marks.items[0]
    reset = bool(first.quality & BASELINE_INVALIDATING) and not first.available & RATE_BITS
    return "reset" if reset else "spike"
