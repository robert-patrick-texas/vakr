"""``CurrentStateQuery`` and ``CollectorHealthRepository`` for SQLite (STATE-001..004, DB-005).

Each state is computed on demand from the newest retained evidence of one
collector (``latest``), and built with ``vakr.domain.projections`` so age,
freshness (E.3), and quality are computed exactly as in every other
backend. Collectors are never merged (STATE-004). Listings are keyset
pages ordered by subject UUID, with the total count of subjects that have
state; they read the dictionary, the evidence, and each state in one
snapshot.

Dependencies:
    vakr.adapters.sqlite.telemetry.*: Latest-row lookups, keys, and the store.
    vakr.domain: Projections, paging, and read models.

Safety:
    Read-only; every lookup is bounded by the retained partitions.

Example:
    >>> SqliteCurrentStateQuery.__name__, SqliteCollectorHealthRepository.__name__
    ('SqliteCurrentStateQuery', 'SqliteCollectorHealthRepository')
"""

import sqlite3
from datetime import datetime
from typing import cast
from uuid import UUID

from vakr.adapters.sqlite.telemetry.keys import find_key, find_uuid
from vakr.adapters.sqlite.telemetry.latest import (
    keyset_page,
    listing_after,
    newest,
    poll_interval,
    with_evidence,
)
from vakr.adapters.sqlite.telemetry.reads import decoder
from vakr.adapters.sqlite.telemetry.store import SqliteTelemetryStore
from vakr.domain.ids import CollectorId, DeviceId, InterfaceId
from vakr.domain.observations import (
    CollectorHealthObservation,
    DevicePoll,
    IcmpObservation,
    InterfaceObservation,
    SnmpDeviceObservation,
)
from vakr.domain.paging import Page, PageRequest
from vakr.domain.projections import build_device_state, build_interface_state
from vakr.domain.state import CurrentDeviceState, CurrentInterfaceState
from vakr.domain.timestamps import require_utc


class SqliteCurrentStateQuery:
    """Latest valid evidence per subject and collector."""

    def __init__(self, store: SqliteTelemetryStore) -> None:
        """Bind the query.

        Args:
            store: The open store.

        Returns:
            None

        Raises:
            None
        """
        self._store = store

    def device_state(
        self, device_id: DeviceId, collector_id: CollectorId, *, now: datetime
    ) -> CurrentDeviceState | None:
        """Return a device's state from one collector.

        Args:
            device_id: The device.
            collector_id: The collector.
            now: Reference time (UTC).

        Returns:
            CurrentDeviceState | None: ``None`` without ICMP or SNMP evidence.

        Raises:
            ValidationError: If ``now`` is not UTC.
        """
        require_utc(now, "now")
        with self._store.reading() as conn:
            return self._device(conn, device_id, collector_id, now)

    def _device(
        self, conn: sqlite3.Connection, device_id: DeviceId, collector_id: CollectorId,
        now: datetime,
    ) -> CurrentDeviceState | None:  # fmt: skip
        """Build one device state inside an open read transaction.

        Args:
            conn: Connection inside the read transaction.
            device_id: The device.
            collector_id: The collector.
            now: Reference time (UTC).

        Returns:
            CurrentDeviceState | None: The state, or ``None``.

        Raises:
            StorageError: If a stored row is damaged.
        """
        keys = self._store.keys
        d, c = find_key(conn, keys, "device", device_id), find_key(conn, keys, "collector",
                                                                     collector_id)  # fmt: skip
        if d is None or c is None:
            return None
        found = {}
        for family, evidence in (("obs_icmp", True), ("obs_snmp", True), ("obs_poll", False)):
            row = newest(conn, family, d, c, evidence=evidence)
            found[family] = None if row is None else decoder(conn, keys, family)(row)
        return build_device_state(
            device_id, collector_id, now=now,
            icmp=cast(IcmpObservation | None, found["obs_icmp"]),
            snmp=cast(SnmpDeviceObservation | None, found["obs_snmp"]),
            poll=cast(DevicePoll | None, found["obs_poll"]),
        )  # fmt: skip

    def interface_state(
        self, interface_id: InterfaceId, collector_id: CollectorId, *, now: datetime
    ) -> CurrentInterfaceState | None:
        """Return an interface's state from one collector.

        Args:
            interface_id: The interface.
            collector_id: The collector.
            now: Reference time (UTC).

        Returns:
            CurrentInterfaceState | None: ``None`` without a valid sample.

        Raises:
            ValidationError: If ``now`` is not UTC.
        """
        require_utc(now, "now")
        with self._store.reading() as conn:
            return self._interface(conn, interface_id, collector_id, now)

    def _interface(
        self, conn: sqlite3.Connection, interface_id: UUID, collector_id: UUID, now: datetime
    ) -> CurrentInterfaceState | None:
        """Build one interface state inside an open read transaction.

        Args:
            conn: Connection inside the read transaction.
            interface_id: The interface.
            collector_id: The collector.
            now: Reference time (UTC).

        Returns:
            CurrentInterfaceState | None: The state, or ``None``.

        Raises:
            StorageError: If a stored row is damaged.
        """
        keys = self._store.keys
        i, c = find_key(conn, keys, "interface", interface_id), find_key(conn, keys, "collector",
                                                                          collector_id)  # fmt: skip
        row = None if i is None or c is None else newest(conn, "obs_iface", i, c, evidence=True)
        if row is None or c is None:
            return None
        sample = cast(InterfaceObservation, decoder(conn, keys, "obs_iface")(row))
        interval = poll_interval(conn, int(cast(int, row[5])), c, sample.poll_id.bytes,
                                 int(cast(int, row[2])))  # fmt: skip
        return build_interface_state(sample, effective_interval_s=interval, now=now)

    def device_states(
        self, collector_id: CollectorId, page: PageRequest, *, now: datetime
    ) -> Page[CurrentDeviceState]:
        """List device states from one collector, ordered by device ID.

        Args:
            collector_id: The collector.
            page: Limit and cursor.
            now: Reference time (UTC).

        Returns:
            Page[CurrentDeviceState]: States and the count of devices with state.

        Raises:
            ValidationError: If the cursor is invalid or ``now`` is not UTC.
        """
        require_utc(now, "now")
        after = listing_after(page.cursor)
        keys = self._store.keys
        with self._store.reading() as conn:
            c = find_key(conn, keys, "collector", collector_id)
            if c is None:
                return Page((), None, 0)
            all_devices = [int(r[0]) for r in conn.execute("SELECT device_key FROM dict_device")]
            have = with_evidence(conn, "obs_icmp", c, all_devices)
            have |= with_evidence(conn, "obs_snmp", c, [k for k in all_devices if k not in have])
            ids = sorted((find_uuid(conn, keys, "device", k) for k in have), key=str)
            chosen, cursor = keyset_page(ids, after, page.limit)
            states = [self._device(conn, DeviceId(i), collector_id, now) for i in chosen]
        return Page(tuple(s for s in states if s is not None), cursor, len(ids))

    def interface_states(
        self, device_id: DeviceId, collector_id: CollectorId, page: PageRequest, *, now: datetime
    ) -> Page[CurrentInterfaceState]:
        """List a device's interface states from one collector, ordered by interface ID.

        Args:
            device_id: The device.
            collector_id: The collector.
            page: Limit and cursor.
            now: Reference time (UTC).

        Returns:
            Page[CurrentInterfaceState]: States and the total count.

        Raises:
            ValidationError: If the cursor is invalid or ``now`` is not UTC.
        """
        require_utc(now, "now")
        after = listing_after(page.cursor)
        keys = self._store.keys
        with self._store.reading() as conn:
            d, c = find_key(conn, keys, "device", device_id), find_key(conn, keys, "collector",
                                                                         collector_id)  # fmt: skip
            if d is None or c is None:
                return Page((), None, 0)
            mine = [int(r[0]) for r in conn.execute(
                "SELECT interface_key FROM dict_interface WHERE device_key = ?", (d,))]  # fmt: skip
            have = with_evidence(conn, "obs_iface", c, mine)
            ids = sorted((find_uuid(conn, keys, "interface", k) for k in have), key=str)
            chosen, cursor = keyset_page(ids, after, page.limit)
            states = [self._interface(conn, i, collector_id, now) for i in chosen]
        return Page(tuple(s for s in states if s is not None), cursor, len(ids))


class SqliteCollectorHealthRepository:
    """Latest collector health snapshot (OBS-001)."""

    def __init__(self, store: SqliteTelemetryStore) -> None:
        """Bind the repository.

        Args:
            store: The open store.

        Returns:
            None

        Raises:
            None
        """
        self._store = store

    def latest(self, collector_id: CollectorId) -> CollectorHealthObservation | None:
        """Return the newest retained health snapshot of a collector.

        Args:
            collector_id: The collector.

        Returns:
            CollectorHealthObservation | None: The snapshot, or ``None``.

        Raises:
            None
        """
        keys = self._store.keys
        with self._store.reading() as conn:
            c = find_key(conn, keys, "collector", collector_id)
            row = None if c is None else newest(conn, "obs_health", c, c, evidence=False)
            if row is None:
                return None
            return cast(CollectorHealthObservation, decoder(conn, keys, "obs_health")(row))
