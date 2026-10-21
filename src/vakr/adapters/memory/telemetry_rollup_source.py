"""In-memory ``RollupSource``: the reference version of the rollup job's reads (RET-004, RET-009).

Selection is a scan over a snapshot of the store; the paging, ordering, and
schedule rules are the ones ``vakr.ports.telemetry.rollup_source`` defines.
Pages are keyset pages over subject UUID text, in the ``(uuid text,)``
cursor form the SQLite adapter uses too.

Dependencies:
    vakr.adapters.memory.paging, telemetry_store: Keyset paging and storage.
    vakr.domain, vakr.ports.telemetry.rollup_source: Observations and read models.

Safety:
    Read-only; every call is bounded by one window and one page of subjects.

Example:
    >>> from vakr.adapters.memory.telemetry_store import MemoryTelemetryStore
    >>> MemoryRollupSource(MemoryTelemetryStore()).__class__.__name__
    'MemoryRollupSource'
"""

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from vakr.adapters.memory.paging import paginate
from vakr.adapters.memory.telemetry_store import MemoryTelemetryStore
from vakr.domain.enums import UptimeContinuity
from vakr.domain.errors import ValidationError
from vakr.domain.ids import CollectorId
from vakr.domain.observations import (
    DevicePoll,
    IcmpObservation,
    InterfaceObservation,
    Observation,
    SnmpDeviceObservation,
)
from vakr.domain.paging import Page, PageRequest, decode_cursor
from vakr.domain.series import TimeWindow, metric_value
from vakr.ports.telemetry.rollup_source import (
    INTERFACE_METRICS,
    SCHEDULE_LOOKBACK,
    DeviceEvidence,
    InterfaceSample,
    InterfaceSamples,
    PollSlot,
)

type Key = tuple[UUID, UUID]
"""``(subject, collector)``."""


@dataclass(slots=True)
class _Devices:
    """Device evidence gathered from one snapshot, keyed by device and collector.

    Attributes:
        cycles: ICMP cycles in the window.
        polls: Poll records in the schedule lookback.
        restarts: Keys with an SNMP restart in the window.
    """

    cycles: dict[Key, list[IcmpObservation]] = field(default_factory=lambda: defaultdict(list))
    polls: dict[Key, list[DevicePoll]] = field(default_factory=lambda: defaultdict(list))
    restarts: set[Key] = field(default_factory=set)

    def item(self, key: Key) -> DeviceEvidence:
        """Assemble one device's evidence.

        Args:
            key: Device and collector.

        Returns:
            DeviceEvidence: Cycles in order, the latest poll slot, and the restart flag.

        Raises:
            None
        """
        cycles = sorted(self.cycles.get(key, ()), key=lambda c: (c.timestamp, c.observation_id))
        last = max(self.polls.get(key, ()), key=lambda p: (p.scheduled_at, p.observation_id),
                   default=None)  # fmt: skip
        slot = None if last is None else PollSlot(last.scheduled_at, last.effective_interval_s)
        return DeviceEvidence(key[0], CollectorId(key[1]), tuple(cycles), slot,
                              key in self.restarts)  # fmt: skip


def _gather(snapshot: Iterable[Observation], window: TimeWindow) -> _Devices:
    """Collect the device evidence a bucket needs.

    Args:
        snapshot: Every stored observation.
        window: One bucket.

    Returns:
        _Devices: Cycles, polls (from ``SCHEDULE_LOOKBACK`` before the
        window), and restarts.

    Raises:
        None
    """
    found, earliest = _Devices(), window.start - SCHEDULE_LOOKBACK
    reset = UptimeContinuity.RESET_DETECTED
    for o in snapshot:
        if isinstance(o, IcmpObservation) and window.contains(o.timestamp):
            found.cycles[o.device_id, o.collector_id].append(o)
        elif isinstance(o, DevicePoll) and earliest <= o.scheduled_at < window.end:
            found.polls[o.device_id, o.collector_id].append(o)
        elif (isinstance(o, SnmpDeviceObservation) and window.contains(o.timestamp)
              and o.uptime_continuity is reset):  # fmt: skip
            found.restarts.add((o.device_id, o.collector_id))
    return found


def _subject_page[R](keys: Iterable[Key], page: PageRequest, build: Callable[[Key], R]) -> Page[R]:
    """Page the subjects of ``keys`` and build one item per subject and collector.

    Args:
        keys: Every ``(subject, collector)`` with evidence.
        page: Limit (subjects) and cursor.
        build: Turns one key into its item.

    Returns:
        Page[R]: Items in ``(subject, collector)`` UUID-text order; ``total`` is ``None``.

    Raises:
        ValidationError: If the cursor is malformed.
    """
    if page.cursor is not None:
        _cursor_uuid(page.cursor)
    keys = set(keys)
    subjects = paginate({s for s, _c in keys}, lambda s: (str(s),), page, count=False)
    chosen = set(subjects.items)
    ordered = sorted((k for k in keys if k[0] in chosen), key=lambda k: (str(k[0]), str(k[1])))
    return Page(tuple(build(k) for k in ordered), subjects.next_cursor, None)


def _cursor_uuid(cursor: str) -> UUID:
    """Decode a ``(uuid text,)`` cursor, refusing anything else as the SQLite adapter does.

    Args:
        cursor: The page cursor.

    Returns:
        UUID: The last subject of the previous page.

    Raises:
        ValidationError: If the cursor is malformed.
    """
    (text,) = decode_cursor(cursor, 1)
    try:
        return UUID(str(text))
    except ValueError:
        raise ValidationError("cursor is not a valid page cursor", field="cursor") from None


class MemoryRollupSource:
    """``RollupSource`` over a ``MemoryTelemetryStore``."""

    def __init__(self, store: MemoryTelemetryStore) -> None:
        """Bind the source.

        Args:
            store: The store.

        Returns:
            None

        Raises:
            None
        """
        self._store = store

    def _snapshot(self) -> list[Observation]:
        """Copy the stored observations under the store lock.

        Args:
            None

        Returns:
            list[Observation]: Every observation.

        Raises:
            None
        """
        with self._store.lock:
            return list(self._store.observations.values())

    def first_native_time(self, window: TimeWindow) -> datetime | None:
        """Return the earliest interface or ICMP observation time in the window.

        Args:
            window: Bounded window.

        Returns:
            datetime | None: The time, or ``None``.

        Raises:
            None
        """
        times = [o.timestamp for o in self._snapshot()
                 if isinstance(o, InterfaceObservation | IcmpObservation)
                 and window.contains(o.timestamp)]  # fmt: skip
        return min(times, default=None)

    def device_evidence(self, window: TimeWindow, page: PageRequest) -> Page[DeviceEvidence]:
        """Return a page of devices' ICMP cycles, schedules, and restart evidence.

        Args:
            window: One bucket.
            page: Limit (devices) and cursor.

        Returns:
            Page[DeviceEvidence]: The page.

        Raises:
            ValidationError: If the cursor is malformed.
        """
        found = _gather(self._snapshot(), window)
        return _subject_page([*found.cycles, *found.polls, *found.restarts], page, found.item)

    def interface_samples(self, window: TimeWindow, page: PageRequest) -> Page[InterfaceSamples]:
        """Return a page of interfaces' samples in the window.

        Args:
            window: One bucket.
            page: Limit (interfaces) and cursor.

        Returns:
            Page[InterfaceSamples]: The page.

        Raises:
            ValidationError: If the cursor is malformed.
        """
        groups: dict[Key, list[InterfaceObservation]] = defaultdict(list)
        for o in self._snapshot():
            if isinstance(o, InterfaceObservation) and window.contains(o.timestamp):
                groups[o.interface_id, o.collector_id].append(o)
        return _subject_page(groups, page, lambda k: _interface_item(k, groups[k]))


def _interface_item(key: Key, found: Iterable[InterfaceObservation]) -> InterfaceSamples:
    """Reduce one interface's observations to rollup samples.

    Args:
        key: Interface and collector.
        found: Its observations in the window.

    Returns:
        InterfaceSamples: Samples in ``(timestamp, observation_id)`` order.

    Raises:
        None
    """
    ordered = sorted(found, key=lambda o: (o.timestamp, o.observation_id))
    samples = tuple(
        InterfaceSample(o.timestamp, o.observation_id,
                        tuple(metric_value(o, m) for m in INTERFACE_METRICS), o.quality)
        for o in ordered
    )  # fmt: skip
    return InterfaceSamples(key[0], CollectorId(key[1]), ordered[-1].device_id, samples)
