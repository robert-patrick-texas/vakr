"""Which of a device's slots left an interface or its ICMP target without a sample (MVP-ACC-10).

Reading every native sample of 1,600 interfaces for 14 days would take
minutes (ADR-021: 4 µs per cold sample), so a subject is checked in two
steps. First its samples are counted per 15-minute bucket: from rollups up
to the rollup frontier (a bucket every subject has finished, E.10) and from
native samples after it. A bucket holding fewer samples than the device
had polls that ran is suspect, unless the one missing sample is simply
the last slot's, landed just past the bucket boundary. Then only the
suspect stretches are read natively, and each poll that ran without
leaving a sample of the subject is a missing slot.

Dependencies:
    datetime: Port windows.
    vakr.ports.telemetry.acceptance: Bucket and sample marks.
    vakr.query.acceptance_limits, acceptance_slots.

Safety:
    Read-only; each read is one subject and one bounded window.

Example:
    >>> suspect_spans({0: 15, 900_000_000: 15}, {0: 14, 900_000_000: 16})
    []
    >>> suspect_spans({0: 15}, {0: 13})
    [(0, 900000000)]
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from vakr.domain.enums import ObservationType
from vakr.domain.ids import CollectorId, DeviceId
from vakr.domain.observations import SnmpDeviceObservation
from vakr.domain.series import TimeWindow
from vakr.domain.timestamps import to_epoch_us
from vakr.ports.telemetry import ObservationQuery
from vakr.ports.telemetry.acceptance import AcceptanceQuery
from vakr.query.acceptance_limits import BUCKET_US, US
from vakr.query.acceptance_slots import Slot, SlotKind, slot_index
from vakr.query.snapshot import pages

RAN_KINDS = frozenset({SlotKind.RAN, SlotKind.CANCELLED})
"""Slots whose poll ran, so its subjects should have a sample."""

LOOKBACK_US = 30 * 86_400 * US
"""How far before the window a status record still says what state an interface was in."""

MAX_SNMP_READ = 100_000
"""Most SNMP device observations read to classify one device's interface gaps."""


def as_time(us: int) -> datetime:
    """Return epoch microseconds as a UTC datetime.

    Args:
        us: Microseconds.

    Returns:
        datetime: The instant.

    Raises:
        None
    """
    return datetime.fromtimestamp(us // US, UTC).replace(microsecond=us % US)


def expected_per_bucket(slots: Sequence[Slot]) -> dict[int, int]:
    """Count the slots that ran in each 15-minute bucket.

    Args:
        slots: The device's timeline.

    Returns:
        dict[int, int]: Bucket start to slots that ran.

    Raises:
        None
    """
    out: dict[int, int] = {}
    for s in slots:
        if s.kind in RAN_KINDS:
            b = s.at_us - s.at_us % BUCKET_US
            out[b] = out.get(b, 0) + 1
    return out


def bucket_samples(
    query: AcceptanceQuery, kind: ObservationType, subject: UUID, collector: CollectorId,
    window: tuple[int, int], frontier_us: int | None,
) -> tuple[dict[int, int], bool]:  # fmt: skip
    """Count a subject's samples per bucket: rollups before the frontier, native after.

    Args:
        query: The acceptance port.
        kind: ``INTERFACE`` or ``ICMP``.
        subject: Interface or device.
        collector: The collector.
        window: ``(start_us, end_us)``, bucket-aligned start.
        frontier_us: The rollup frontier, or ``None`` without rollups.

    Returns:
        tuple[dict[int, int], bool]: Bucket start to samples, and whether a read was cut short.

    Raises:
        StorageError: If the store is unavailable.
    """
    split = window[0] if frontier_us is None else min(max(frontier_us, window[0]), window[1])
    counts: dict[int, int] = {}
    truncated = False
    if split > window[0]:
        rolled = query.bucket_marks(kind, subject, collector,
                                    TimeWindow(as_time(window[0]), as_time(split)))  # fmt: skip
        counts.update((m.start_us, m.samples) for m in rolled.items)
        truncated = rolled.truncated
    if split < window[1]:
        tail = query.sample_marks(kind, subject, collector,
                                  TimeWindow(as_time(split), as_time(window[1])))  # fmt: skip
        for m in tail.items:
            b = m.ts_us - m.ts_us % BUCKET_US
            counts[b] = counts.get(b, 0) + 1
        truncated = truncated or tail.truncated
    return counts, truncated


def suspect_spans(expected: dict[int, int], counts: dict[int, int]) -> list[tuple[int, int]]:
    """Return the stretches of buckets that hold fewer samples than polls that ran.

    Args:
        expected: Bucket start to slots that ran.
        counts: Bucket start to samples.

    Returns:
        list[tuple[int, int]]: ``(start_us, end_us)`` stretches of suspect buckets, merged.

    Raises:
        None
    """
    spans: list[tuple[int, int]] = []
    for b in sorted(expected):
        want, got = expected[b], counts.get(b, 0)
        if got >= want:
            continue
        spill = counts.get(b + BUCKET_US, 0) - expected.get(b + BUCKET_US, 0)
        if got + 1 == want and spill > 0:
            continue  # the last slot's sample was taken just after the boundary
        if spans and spans[-1][1] == b:
            spans[-1] = (spans[-1][0], b + BUCKET_US)
        else:
            spans.append((b, b + BUCKET_US))
    return spans


def missing_slots(
    query: AcceptanceQuery, kind: ObservationType, subject: UUID, collector: CollectorId,
    slots: Sequence[Slot], spans: Sequence[tuple[int, int]],
) -> tuple[list[int], bool]:  # fmt: skip
    """Read the suspect stretches natively and return the ran slots without a sample.

    Args:
        query: The acceptance port.
        kind: ``INTERFACE`` or ``ICMP``.
        subject: Interface or device.
        collector: The collector.
        slots: The device's timeline.
        spans: Suspect stretches.

    Returns:
        tuple[list[int], bool]: Slot indices, ascending, and whether a read was cut short.

    Raises:
        StorageError: If the store is unavailable.
    """
    if not spans:
        return [], False
    # A sample belongs to the latest poll that ran at or before it, so a poll
    # that overran its slot still owns the sample it took after that slot.
    ran = [i for i, s in enumerate(slots) if s.kind in RAN_KINDS]
    ran_times = [slots[i].at_us for i in ran]
    missing: list[int] = []
    truncated = False
    for start, end in spans:
        lo, hi = slot_index(ran_times, start - 1) + 1, slot_index(ran_times, end - 1) + 1
        if lo >= hi:
            continue
        period = max(slots[i].interval_s for i in ran[lo:hi]) * US
        read = TimeWindow(as_time(ran_times[lo]), as_time(ran_times[hi - 1] + 2 * period))
        marks = query.sample_marks(kind, subject, collector, read)
        truncated = truncated or marks.truncated
        seen = {slot_index(ran_times, m.ts_us) for m in marks.items}
        missing.extend(ran[j] for j in range(lo, hi) if j not in seen)
    return missing, truncated


def snmp_by_slot(
    query: ObservationQuery, device: DeviceId, collector: CollectorId, slots: Sequence[Slot],
    indices: Sequence[int],
) -> tuple[dict[int, SnmpDeviceObservation], bool]:  # fmt: skip
    """Read the SNMP device observations of the polls at the given slots.

    Args:
        query: Raw observation reads.
        device: The device.
        collector: The collector.
        slots: The device's timeline.
        indices: Slot indices whose poll's SNMP result is needed, ascending.

    Returns:
        tuple[dict[int, SnmpDeviceObservation], bool]: Slot index to observation, and
        whether the read was cut short.

    Raises:
        StorageError: If the store is unavailable.
    """
    if not indices:
        return {}, False
    ran = [i for i, s in enumerate(slots) if s.kind in RAN_KINDS]
    ran_times = [slots[i].at_us for i in ran]
    last = slots[indices[-1]]
    window = TimeWindow(as_time(slots[indices[0]].at_us),
                        as_time(last.at_us + 2 * last.interval_s * US))  # fmt: skip
    found, cut = pages(lambda p: query.read_raw(ObservationType.SNMP_DEVICE, device, window, p,
                                               collector_id=collector), MAX_SNMP_READ)  # fmt: skip
    out: dict[int, SnmpDeviceObservation] = {}
    for o in found:
        if isinstance(o, SnmpDeviceObservation):
            j = slot_index(ran_times, to_epoch_us(o.timestamp))
            if j >= 0:
                out.setdefault(ran[j], o)
    return out, cut


def down_slots(
    query: AcceptanceQuery, iface: UUID, collector: CollectorId, slots: Sequence[Slot],
    indices: Sequence[int], window: tuple[int, int],
) -> set[int]:  # fmt: skip
    """Return the missing slots at which a status record says the interface was not up/up.

    A status holds from its record until the interface's next record from the
    same collector (E.35), so the state at the window start is the newest
    record before it, read back as far as native data goes (30 days).

    Args:
        query: The acceptance port.
        iface: The interface.
        collector: The collector.
        slots: The device's timeline.
        indices: Missing slot indices, ascending.
        window: ``(start_us, end_us)`` of the report.

    Returns:
        set[int]: The slot indices explained as down.

    Raises:
        StorageError: If the store is unavailable.
    """
    if not indices:
        return set()
    last = slots[indices[-1]]
    end = last.at_us + last.interval_s * US
    before = query.status_marks
    lead = before(
        iface,
        collector,
        TimeWindow(as_time(window[0] - LOOKBACK_US), as_time(window[0])),
        limit=1,
        newest_first=True,
    )
    marks = [
        *lead.items,
        *before(iface, collector, TimeWindow(as_time(window[0]), as_time(end))).items,
    ]
    times = [m.ts_us for m in marks]
    down: set[int] = set()
    for i in indices:
        slot = slots[i]
        j = slot_index(times, slot.at_us + slot.interval_s * US - 1)
        if j >= 0 and not marks[j].up_up:
            down.add(i)
    return down
