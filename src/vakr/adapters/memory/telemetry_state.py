"""In-memory ``CurrentStateQuery`` (STATE-001..004, DB-005).

State is computed on demand from stored observations, which is exactly the
"rebuildable from observations" property STATE-002 asks of optimised
projections elsewhere.

Dependencies:
    vakr.adapters.memory.telemetry_store, paging: Storage and keyset paging.
    vakr.domain.projections: Which observation is current, and age/freshness.

Safety:
    Per-collector evidence is never merged (STATE-004).

Example:
    >>> from vakr.adapters.memory.telemetry_store import MemoryTelemetryStore
    >>> MemoryCurrentStateQuery(MemoryTelemetryStore()).__class__.__name__
    'MemoryCurrentStateQuery'
"""

from datetime import datetime
from uuid import UUID

from vakr.adapters.memory.paging import paginate
from vakr.adapters.memory.telemetry_store import MemoryTelemetryStore
from vakr.domain.ids import CollectorId, DeviceId, InterfaceId
from vakr.domain.observations import (
    DevicePoll,
    IcmpObservation,
    InterfaceObservation,
    Observation,
    SnmpDeviceObservation,
)
from vakr.domain.paging import Page, PageRequest
from vakr.domain.projections import (
    build_device_state,
    build_interface_state,
    is_evidence,
    latest,
    latest_evidence,
)
from vakr.domain.state import CurrentDeviceState, CurrentInterfaceState
from vakr.domain.timestamps import require_utc


class MemoryCurrentStateQuery:
    """``CurrentStateQuery`` over a ``MemoryTelemetryStore``."""

    def __init__(self, store: MemoryTelemetryStore) -> None:
        """Bind the query.

        Args:
            store: The store.

        Returns:
            None

        Raises:
            None
        """
        self._store = store

    def _of[T: Observation](self, kind: type[T], collector_id: UUID) -> list[T]:
        """Return stored observations of one class from one collector.

        Args:
            kind: Observation class.
            collector_id: The collector.

        Returns:
            list[T]: The observations, unordered.

        Raises:
            None
        """
        return [
            o for o in list(self._store.observations.values())
            if isinstance(o, kind) and o.collector_id == collector_id
        ]  # fmt: skip

    def device_state(
        self, device_id: DeviceId, collector_id: CollectorId, *, now: datetime
    ) -> CurrentDeviceState | None:
        """Return a device's state from one collector.

        Args:
            device_id: The device.
            collector_id: The collector.
            now: Reference time (UTC).

        Returns:
            CurrentDeviceState | None: The state, or ``None`` without evidence.

        Raises:
            ValidationError: If ``now`` is not UTC.
        """
        require_utc(now, "now")
        icmp = latest_evidence(
            o for o in self._of(IcmpObservation, collector_id) if o.device_id == device_id
        )
        snmp = latest_evidence(
            o for o in self._of(SnmpDeviceObservation, collector_id) if o.device_id == device_id
        )
        poll = latest(o for o in self._of(DevicePoll, collector_id) if o.device_id == device_id)
        return build_device_state(device_id, collector_id, icmp=icmp, snmp=snmp, poll=poll, now=now)

    def interface_state(
        self, interface_id: InterfaceId, collector_id: CollectorId, *, now: datetime
    ) -> CurrentInterfaceState | None:
        """Return an interface's state from one collector.

        Args:
            interface_id: The interface.
            collector_id: The collector.
            now: Reference time (UTC).

        Returns:
            CurrentInterfaceState | None: The state, or ``None`` without a sample.

        Raises:
            ValidationError: If ``now`` is not UTC.
        """
        require_utc(now, "now")
        samples = self._of(InterfaceObservation, collector_id)
        sample = latest_evidence(o for o in samples if o.interface_id == interface_id)
        if sample is None:
            return None
        poll = next(
            (p for p in self._of(DevicePoll, collector_id) if p.poll_id == sample.poll_id), None
        )
        interval = poll.effective_interval_s if poll is not None else None
        return build_interface_state(sample, effective_interval_s=interval, now=now)

    def device_states(
        self, collector_id: CollectorId, page: PageRequest, *, now: datetime
    ) -> Page[CurrentDeviceState]:
        """List device states from one collector, by device ID.

        Args:
            collector_id: The collector.
            page: Limit and cursor.
            now: Reference time (UTC).

        Returns:
            Page[CurrentDeviceState]: The page.

        Raises:
            ValidationError: If the cursor is invalid or ``now`` is not UTC.
        """
        require_utc(now, "now")
        evidence = [
            *self._of(IcmpObservation, collector_id),
            *self._of(SnmpDeviceObservation, collector_id),
        ]
        ids = {o.device_id for o in evidence if is_evidence(o)}
        ids_page = paginate(ids, lambda i: (str(i),), page)
        states = [self.device_state(DeviceId(i), collector_id, now=now) for i in ids_page.items]
        return Page(tuple(s for s in states if s is not None), ids_page.next_cursor, ids_page.total)

    def interface_states(
        self, device_id: DeviceId, collector_id: CollectorId, page: PageRequest, *, now: datetime
    ) -> Page[CurrentInterfaceState]:
        """List a device's interface states from one collector, by interface ID.

        Args:
            device_id: The device.
            collector_id: The collector.
            page: Limit and cursor.
            now: Reference time (UTC).

        Returns:
            Page[CurrentInterfaceState]: The page.

        Raises:
            ValidationError: If the cursor is invalid or ``now`` is not UTC.
        """
        require_utc(now, "now")
        samples = [
            o for o in self._of(InterfaceObservation, collector_id) if o.device_id == device_id
        ]
        ids = {o.interface_id for o in samples if is_evidence(o)}
        ids_page = paginate(ids, lambda i: (str(i),), page)
        states = [
            self.interface_state(InterfaceId(i), collector_id, now=now) for i in ids_page.items
        ]
        return Page(tuple(s for s in states if s is not None), ids_page.next_cursor, ids_page.total)
