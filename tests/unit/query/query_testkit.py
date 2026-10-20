"""A small fleet builder for query, CLI, and TUI tests (no secrets, no real devices).

``Fleet`` owns a memory metadata store, a memory telemetry store, and a
manual clock. Its helpers add devices, interfaces, polls, samples, and
collector health through the ports, so the services under test read data
written exactly as the collector writes it. Addresses come from the
documentation ranges (RFC 5737) and names are invented.

Dependencies:
    vakr.adapters.memory: Reference stores.
    tests.contract.contract_samples: Valid domain records.

Example:
    >>> fleet = Fleet()
    >>> fleet.queries().device_list().total
    0
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from tests.contract import contract_samples as samples
from vakr.adapters.memory import (
    MemoryMetadataStore,
    MemoryTelemetryStore,
    memory_telemetry_factory,
    memory_uow_factory,
)
from vakr.domain.catalog import Collector
from vakr.domain.enums import AdminState, IcmpState, PollExecution, SnmpState, UptimeContinuity
from vakr.domain.ids import BatchId, DeviceId, PollId, new_id
from vakr.domain.interfaces import Interface
from vakr.domain.inventory import Device, Site
from vakr.domain.observations import Observation
from vakr.domain.quality import Quality
from vakr.ports.clock import ManualClock
from vakr.query import QuerySettings, StatusQueries

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
"""The fixed "now" every fleet's clock starts at."""

RTTS = {
    IcmpState.REACHABLE: (900, 1000, 1100, 1000, 1000),
    IcmpState.IMPAIRED: (900, None, None, 1000, 1000),
    IcmpState.UNREACHABLE: (None, None, None, None, None),
    IcmpState.UNKNOWN: (None, None, None, None, None),
}


def icmp_summary(rtts: tuple[int | None, ...]) -> dict[str, int | None]:
    """Return the RTT summary fields an ICMP observation needs for these probes.

    Args:
        rtts: Per-probe RTTs; ``None`` is no reply.

    Returns:
        dict: ``min``/``avg``/``median``/``max``/``jitter`` fields, ``None`` without replies.

    Raises:
        None
    """
    replies = sorted(r for r in rtts if r is not None)
    if not replies:
        return dict.fromkeys(
            ("min_rtt_us", "avg_rtt_us", "median_rtt_us", "max_rtt_us", "jitter_us")
        )
    mid = replies[len(replies) // 2]
    return {
        "min_rtt_us": replies[0],
        "avg_rtt_us": mid,
        "median_rtt_us": mid,
        "max_rtt_us": replies[-1],
        "jitter_us": 50 if len(replies) > 1 else None,
    }


class Fleet:
    """Stores, a clock, and one collector named ``east``."""

    def __init__(self, positions: tuple[str, ...] = ("east",)) -> None:
        """Create empty stores with one collector per position.

        Args:
            positions: Collector position names.

        Returns:
            None

        Raises:
            None
        """
        self.meta = MemoryMetadataStore()
        self.uow = memory_uow_factory(self.meta)
        self.tele = MemoryTelemetryStore()
        self.telemetry = memory_telemetry_factory(self.tele)
        self.clock = ManualClock(NOW)
        self.collectors: dict[str, Collector] = {}
        for name in positions:
            self.collectors[name] = self.add("collectors", samples.collector(name))

    def add[T](self, repo: str, record: T) -> T:
        """Store one metadata record and commit.

        Args:
            repo: Repository attribute on the unit of work, such as ``"devices"``.
            record: The record.

        Returns:
            T: The stored record.

        Raises:
            None
        """
        with self.uow() as uow:
            stored = getattr(uow, repo).add(record)
            uow.commit()
        return stored

    def device(self, name: str, **overrides: Any) -> Device:
        """Add a device.

        Args:
            name: Display name.
            **overrides: Device fields.

        Returns:
            Device: The stored device.

        Raises:
            None
        """
        return self.add("devices", samples.device(name, **overrides))

    def site(self, name: str, code: str) -> Site:
        """Add a site.

        Args:
            name: Site name.
            code: Site code.

        Returns:
            Site: The stored site.

        Raises:
            None
        """
        return self.add("sites", samples.site(name, code=code))

    def interface(self, device: Device, if_name: str, **overrides: Any) -> Interface:
        """Add an interface to a device.

        Args:
            device: The device.
            if_name: ``ifName``.
            **overrides: Interface fields.

        Returns:
            Interface: The stored interface.

        Raises:
            None
        """
        return self.add("interfaces", samples.interface(device.id, if_name, **overrides))

    def write(self, *observations: Observation) -> None:
        """Write observations as one batch, as the ingest path would.

        Args:
            *observations: Observations of any type.

        Returns:
            None

        Raises:
            None
        """
        self.telemetry().writer.write_batch(new_id(BatchId), observations)

    def poll(
        self,
        device: Device,
        *,
        ago: float = 30,
        icmp: IcmpState | None = IcmpState.REACHABLE,
        snmp: SnmpState | None = SnmpState.RESPONDING,
        position: str = "east",
        **extra: Any,
    ) -> PollId:
        """Record one poll of a device: the poll record plus its ICMP and SNMP evidence.

        Args:
            device: The device.
            ago: Seconds before ``NOW`` the poll was scheduled.
            icmp: ICMP state, or ``None`` for no ICMP observation.
            snmp: SNMP state, or ``None`` for no SNMP observation.
            position: Which collector polled.
            **extra: ``execution``, ``poll_quality``, ``icmp_quality``,
                ``uptime_ticks``, ``continuity``, ``reachability``, ``rtts``,
                ``configured_interval_s``.

        Returns:
            PollId: The poll's ID, for interface samples.

        Raises:
            None
        """
        c = self.collectors[position].id
        at = NOW - timedelta(seconds=ago)
        pid = new_id(PollId)
        execution = extra.get("execution", PollExecution.COMPLETE)
        poll_fields: dict[str, Any] = {"execution": execution}
        poll_fields["quality"] = extra.get("poll_quality", Quality.NONE)
        if execution is PollExecution.NOT_ATTEMPTED:
            poll_fields |= {"started_at": None, "completed_at": None}
        for name in ("reachability", "configured_interval_s"):
            if extra.get(name) is not None:
                poll_fields[name] = extra[name]
        obs: list[Observation] = [samples.poll(device.id, c, at, poll_id=pid, **poll_fields)]
        if icmp is not None:
            rtts = extra.get("rtts", RTTS[icmp])
            obs.append(
                samples.icmp(
                    device.id, c, at, poll_id=pid, state=icmp, rtts_us=rtts,
                    quality=extra.get("icmp_quality", Quality.NONE), **icmp_summary(rtts),
                )
            )  # fmt: skip
        if snmp is not None:
            obs.append(
                samples.snmp(
                    device.id, c, at + timedelta(seconds=2), poll_id=pid, state=snmp,
                    sys_uptime_ticks=extra.get("uptime_ticks", 360_000_000),
                    uptime_continuity=extra.get("continuity", UptimeContinuity.CONTINUOUS),
                )
            )  # fmt: skip
        self.write(*obs)
        return pid

    def sample(self, interface: Interface, *, ago: float = 30, **fields: Any) -> None:
        """Record one interface sample from ``east``.

        Args:
            interface: The interface.
            ago: Seconds before ``NOW``.
            **fields: Sample fields (rates, quality, baseline state).

        Returns:
            None

        Raises:
            None
        """
        c = self.collectors["east"].id
        at = NOW - timedelta(seconds=ago)
        self.write(samples.sample(interface.device_id, interface.id, c, at, **fields))

    def health(self, *, ago: float = 10, position: str = "east", **fields: Any) -> None:
        """Record a collector health report.

        Args:
            ago: Seconds before ``NOW``.
            position: Which collector reports.
            **fields: Health fields such as ``state``.

        Returns:
            None

        Raises:
            None
        """
        c = self.collectors[position].id
        self.write(samples.health(c, NOW - timedelta(seconds=ago), **fields))

    def queries(self, *, telemetry: bool = True, **settings: Any) -> StatusQueries:
        """Return the service under test over these stores.

        Args:
            telemetry: False to simulate a node without a telemetry database.
            **settings: ``QuerySettings`` fields; the display timezone is UTC.

        Returns:
            StatusQueries: The service.

        Raises:
            None
        """
        chosen = replace(QuerySettings(timezone="UTC"), **settings)
        return StatusQueries(self.uow, self.telemetry if telemetry else None, chosen, self.clock)


def mixed_fleet() -> Fleet:
    """Build devices in every bucket, inserted out of order.

    Args:
        None

    Returns:
        Fleet: ``sw10`` OK, ``sw2`` OK, ``core`` Down, ``lab`` disabled,
        ``edge`` impaired, ``old`` stale, at sites Dallas and Austin.

    Raises:
        None
    """
    fleet = Fleet()
    dal, aus = fleet.site("Dallas DC1", "DAL1"), fleet.site("Austin", "AUS1")
    wan = fleet.add("tags", samples.tag("wan"))
    fleet.poll(fleet.device("sw10", site_id=dal.id, poll_address="192.0.2.10"))
    fleet.poll(
        fleet.device("sw2", site_id=aus.id, poll_address="192.0.2.2", tags=frozenset({wan.id}))
    )
    fleet.poll(
        fleet.device("core", poll_address="198.51.100.1"), icmp=IcmpState.UNREACHABLE, snmp=None
    )
    fleet.device("lab", admin_state=AdminState.DISABLED, poll_address="192.0.2.99")
    fleet.poll(fleet.device("edge", poll_address="192.0.2.5"), icmp=IcmpState.IMPAIRED)
    fleet.poll(fleet.device("old", poll_address="192.0.2.6"), ago=900)
    return fleet


def device_id_of(fleet: Fleet, name: str) -> DeviceId:
    """Return the ID of a device by name.

    Args:
        fleet: The fleet.
        name: Device name.

    Returns:
        DeviceId: Its ID.

    Raises:
        LookupError: If no device has that name.
    """
    with fleet.uow() as uow:
        device = uow.devices.get_by_name(name)
    if device is None:
        raise LookupError(name)
    return device.id
