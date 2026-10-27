"""A device's slot timeline over a window, and the causes of a slot without data (MVP-ACC-10).

The scheduler records every slot it owns (SCHED-012, SCHED-017), so a
device's poll records, with the slots between them that have no record at
all, make its timeline. ``device_slots`` builds it; ``device_cause``
explains a slot whose poll produced no evidence, from the collector's and
the inventory's evidence (``acceptance_evidence``). Collector or storage
trouble is never a device cause (invariant 2): a missing record while the
collector was silent is ``collector_stopped``, not the device being down.

Dependencies:
    bisect: Slot lookups.
    vakr.domain, vakr.ports.telemetry.acceptance: Poll states and marks.
    vakr.query.acceptance_evidence, acceptance_limits.

Safety:
    Pure computation; one slot per interval of the window per device.

Example:
    >>> [s.at_us for s in device_slots([], (0, 180_000_000))]
    [0, 60000000, 120000000]
"""

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from vakr.domain.enums import ObservationType, PollExecution
from vakr.domain.quality import Quality
from vakr.ports.telemetry.acceptance import PollMark
from vakr.query.acceptance_evidence import CollectorEvidence, DeviceEvidence
from vakr.query.acceptance_limits import DEFAULT_INTERVAL_S, GAP_FACTOR, US


class GapCause(StrEnum):
    """Why a subject has no data in a slot (E.35 gap classes)."""

    COLLECTOR_STOPPED = "collector_stopped"
    COLLECTOR_BUSY = "collector_busy"
    COLLECTOR_FAULT = "collector_fault"
    STORAGE = "storage"
    TELEMETRY_LOSS = "telemetry_loss"
    POLL_OVERRUN = "poll_overrun"
    DEVICE_UNREACHABLE = "device_unreachable"
    SNMP_TIMEOUT = "snmp_timeout"
    SNMP_ERROR = "snmp_error"
    ADMIN_STATE = "admin_state"
    NOT_YET_DISCOVERED = "not_yet_discovered"
    INTERFACE_NOT_UP = "interface_not_up"
    INVENTORY_CHANGE = "inventory_change"
    UNEXPLAINED = "unexplained"


class SlotKind(StrEnum):
    """What a slot's record says, or that it has none."""

    RAN = "ran"
    CANCELLED = "cancelled"
    NO_RECORD = "no_record"
    NOT_ATTEMPTED = "not_attempted"
    OVERRUN = "overrun"
    FAILED = "failed"


DEVICE_GAP_KINDS = frozenset({SlotKind.NO_RECORD, SlotKind.NOT_ATTEMPTED, SlotKind.OVERRUN,
                              SlotKind.FAILED})  # fmt: skip
"""Slots whose poll produced no evidence at all: gaps of the device itself."""

_KIND = {
    PollExecution.COMPLETE: SlotKind.RAN, PollExecution.PARTIAL: SlotKind.RAN,
    PollExecution.RUNNING: SlotKind.RAN, PollExecution.CANCELLED: SlotKind.CANCELLED,
    PollExecution.FAILED: SlotKind.FAILED, PollExecution.MISSED_OVERRUN: SlotKind.OVERRUN,
    PollExecution.NOT_ATTEMPTED: SlotKind.NOT_ATTEMPTED,
    PollExecution.SCHEDULED: SlotKind.NOT_ATTEMPTED,
}  # fmt: skip
_POLLS = ObservationType.DEVICE_POLL.value


@dataclass(frozen=True, slots=True)
class Slot:
    """One slot of a device's timeline.

    Attributes:
        at_us: The slot's scheduled time.
        interval_s: The interval in force.
        kind: What its record says.
        mark: The record, or ``None`` for a slot without one.
    """

    at_us: int
    interval_s: int
    kind: SlotKind
    mark: PollMark | None


def _fill(low: int, high: int, interval_s: int, out: list[Slot]) -> None:
    """Append the record-less slots strictly between two instants, one interval apart.

    Args:
        low: The earlier instant (a record or the window start).
        high: The later instant.
        interval_s: The interval in force.
        out: The timeline.

    Returns:
        None

    Raises:
        None
    """
    period = interval_s * US
    if high - low <= GAP_FACTOR * period:
        return
    at = low + period
    while at <= high - period // 2:
        out.append(Slot(at, interval_s, SlotKind.NO_RECORD, None))
        at += period


def device_slots(marks: Sequence[PollMark], window: tuple[int, int]) -> list[Slot]:
    """Build a device's timeline: its records, plus record-less slots before, between, after.

    Args:
        marks: Poll marks by scheduled time, inside the window.
        window: ``(start_us, end_us)``.

    Returns:
        list[Slot]: Slots by time; without records, one every ``DEFAULT_INTERVAL_S``.

    Raises:
        None
    """
    out: list[Slot] = []
    if not marks:
        period = DEFAULT_INTERVAL_S * US
        out.extend(Slot(at, DEFAULT_INTERVAL_S, SlotKind.NO_RECORD, None)
                   for at in range(window[0], window[1], period))  # fmt: skip
        return out
    first, period = marks[0], marks[0].interval_s * US
    if first.scheduled_us - window[0] > GAP_FACTOR * period:
        lead = (first.scheduled_us - window[0]) // period
        out.extend(Slot(first.scheduled_us - k * period, first.interval_s, SlotKind.NO_RECORD,
                        None) for k in range(lead, 0, -1))  # fmt: skip
    for i, mark in enumerate(marks):
        if i and mark.scheduled_us == marks[i - 1].scheduled_us:
            continue  # a slot recorded twice is one slot (the schedule report counts it)
        out.append(Slot(mark.scheduled_us, mark.interval_s, _KIND[mark.execution], mark))
        nxt = marks[i + 1].scheduled_us if i + 1 < len(marks) else None
        _fill(mark.scheduled_us, window[1] + mark.interval_s * US if nxt is None else nxt,
              mark.interval_s, out)  # fmt: skip
    return [s for s in out if window[0] <= s.at_us < window[1]]


def slot_index(times: Sequence[int], at_us: int) -> int:
    """Return the index of the slot an observation at ``at_us`` belongs to (latest at or before).

    Args:
        times: The timeline's slot times, ``[s.at_us for s in slots]``.
        at_us: The observation time.

    Returns:
        int: The index, or -1 before the first slot.

    Raises:
        None
    """
    return bisect_right(times, at_us) - 1


def _recorded_cause(slot: Slot, collector: CollectorEvidence) -> GapCause:
    """Explain a slot whose record says the poll overran, failed, or was not attempted.

    Args:
        slot: The slot (it has a record).
        collector: The collector's evidence.

    Returns:
        GapCause: The cause.

    Raises:
        None
    """
    at, period = slot.at_us, slot.interval_s * US
    flags = slot.mark.quality if slot.mark is not None else Quality.NONE
    if slot.kind is SlotKind.OVERRUN:
        return GapCause.POLL_OVERRUN
    if slot.kind is SlotKind.FAILED:
        degraded = flags & Quality.COLLECTOR_DEGRADED
        return GapCause.COLLECTOR_FAULT if degraded else GapCause.DEVICE_UNREACHABLE
    if collector.spool_full(at, period):
        return GapCause.STORAGE
    stopping = collector.silent_soon(at, 2 * period) or collector.silent(at)
    return GapCause.COLLECTOR_STOPPED if stopping else GapCause.COLLECTOR_BUSY


def device_cause(slot: Slot, collector: CollectorEvidence, device: DeviceEvidence) -> GapCause:
    """Explain a slot whose poll produced no evidence (a kind in ``DEVICE_GAP_KINDS``).

    Args:
        slot: The slot.
        collector: The collector's evidence.
        device: The device's inventory evidence.

    Returns:
        GapCause: The first cause the evidence supports, else ``unexplained``.

    Raises:
        None
    """
    at = slot.at_us
    if slot.kind is not SlotKind.NO_RECORD:
        cause = _recorded_cause(slot, collector)
    elif collector.loss(at, _POLLS) is not None:
        cause = GapCause.TELEMETRY_LOSS
    elif collector.silent(at):
        lagging = not collector.local and collector.silent_to_end(at)
        cause = GapCause.STORAGE if lagging else GapCause.COLLECTOR_STOPPED
    elif device.created_us > at:
        cause = GapCause.INVENTORY_CHANGE
    elif device.offline_since(at):
        cause = GapCause.ADMIN_STATE
    else:
        cause = GapCause.UNEXPLAINED
    return cause
