"""Harness for the collector end-to-end tests: install root, fake inventory, waits.

``make_root`` lays out a relocated installation in ``tmp_path`` with a fast
test configuration and migrated SQLite metadata and telemetry databases.
``LabInventory`` implements the collector's ``InventorySource`` contract in
memory, as ``vakr.inventory.service.InventoryService`` will: it gives every
discovered interface a stable UUID (never its ``ifIndex``) and returns the
plan, and its change counter moves on every change. ``wait_until`` polls a
condition on the event loop with a deadline.

Dependencies:
    vakr.app.collector, vakr.adapters.sqlite: The code under test.

Example:
    >>> LabInventory([]).change_counter()
    0
"""

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from vakr.adapters.icmp import wire
from vakr.adapters.icmp.base import SocketOpenError
from vakr.adapters.icmp.sockets import open_socket
from vakr.adapters.sqlite.metadata import METADATA_FILE, metadata_migrations
from vakr.adapters.sqlite.telemetry import TELEMETRY_FILE, telemetry_migrations
from vakr.collectors.snmp import PollPlan, plan_from_discovery
from vakr.discovery import DiscoveryResult
from vakr.domain.ids import DeviceId, InterfaceId
from vakr.domain.inventory import API_MANAGED, Device
from vakr.paths import InstallPaths

CONFIG = """config_version = 1

[scheduler]
collector_name = "e2e-east"
shutdown_grace = "2s"

[icmp]
probes = 2
spacing = "20ms"
timeout = "300ms"

[snmp]
port = {port}
timeout = "1s"
retries = 0

[spool]
ingest_interval = "1s"
fsync_interval = "100ms"
max_size_mib = 256
segment_size_mib = 16
"""
"""A configuration that keeps cycles short; the interval itself is a test-only override."""

NOW = datetime(2026, 10, 20, tzinfo=UTC)
_NS = uuid.UUID("0190a3c4-0000-7000-8000-00000000e2e0")


def icmp_unavailable() -> str | None:
    """Explain why unprivileged ICMP to loopback cannot run here, if it cannot.

    Args:
        None

    Returns:
        str | None: The reason, or ``None`` when ping sockets work.

    Raises:
        None
    """
    try:
        open_socket(4, wire.SocketKind.DATAGRAM, None).close()
    except SocketOpenError as exc:
        groups = sorted({os.getegid(), *os.getgroups()})
        return (
            f"unprivileged ICMP unavailable ({exc.reason}); ping_group_range must include {groups}"
        )
    return None


def make_root(tmp_path: Path, port: int, extra: str = "") -> InstallPaths:
    """Create a relocated install with the test configuration and migrated databases.

    Args:
        tmp_path: The test's directory.
        port: The simulator's UDP port (``snmp.port``).
        extra: More TOML appended to the configuration.

    Returns:
        InstallPaths: The layout under ``tmp_path / "root"``.

    Raises:
        OSError: If the directories cannot be created.
    """
    paths = InstallPaths.under(tmp_path / "root")
    for directory in (paths.config_dir, paths.state_dir, paths.run_dir):
        directory.mkdir(parents=True, exist_ok=True)
    paths.secrets_dir.mkdir(mode=0o750, exist_ok=True)
    (paths.config_dir / "config.toml").write_text(CONFIG.format(port=port) + extra)
    metadata_migrations(paths.state_dir / METADATA_FILE).migrate()
    telemetry_migrations(paths.state_dir / TELEMETRY_FILE).migrate()
    return paths


def lab_device(name: str, number: int, **changes: object) -> Device:
    """Build a device on 127.0.0.1 whose credential is named after it.

    Args:
        name: Device and credential name (the simulator's community key).
        number: Distinguishes the deterministic UUID.
        changes: Field overrides, such as ``reachability``.

    Returns:
        Device: The inventory record.

    Raises:
        None
    """
    device = Device(
        DeviceId(uuid.UUID(f"0190a3c4-0000-7000-8000-{number:012x}")),
        name, "127.0.0.1", API_MANAGED, NOW, NOW, credential_ref=name,
    )  # fmt: skip
    return replace(device, **changes)


def interface_uuid(device_id: DeviceId, if_index: int) -> InterfaceId:
    """Return the stable UUID the lab inventory gives one interface.

    Args:
        device_id: The device.
        if_index: The interface's ifIndex at discovery.

    Returns:
        InterfaceId: A UUIDv5 of the device and index.

    Raises:
        None
    """
    return InterfaceId(uuid.uuid5(_NS, f"{device_id}/{if_index}"))


class LabInventory:
    """An in-memory ``InventorySource``.

    Attributes:
        items: The devices.
        plans: Plans by device, after discovery.
        recorded: Discovery results handed in, in order.
        counter: The change counter.
        unresolved: ifIndexes left without a persistent UUID (identity review).
    """

    def __init__(self, devices: Sequence[Device], unresolved: Sequence[int] = ()) -> None:
        """Hold the devices.

        Args:
            devices: The inventory.
            unresolved: ifIndexes whose identity stays unresolved.

        Returns:
            None

        Raises:
            None
        """
        self.items = tuple(devices)
        self.plans: dict[DeviceId, PollPlan] = {}
        self.recorded: list[DiscoveryResult] = []
        self.counter = 0
        self.unresolved = set(unresolved)

    def devices(self) -> tuple[Device, ...]:
        """Return the devices.

        Args:
            None

        Returns:
            tuple[Device, ...]: The inventory.

        Raises:
            None
        """
        return self.items

    def change_counter(self) -> int:
        """Return the change counter.

        Args:
            None

        Returns:
            int: Moves on every change.

        Raises:
            None
        """
        return self.counter

    def poll_plan(self, device_id: DeviceId) -> PollPlan | None:
        """Return a device's plan.

        Args:
            device_id: The device.

        Returns:
            PollPlan | None: ``None`` before discovery.

        Raises:
            None
        """
        return self.plans.get(device_id)

    def record_discovery(
        self, device_id: DeviceId, result: DiscoveryResult, now: datetime
    ) -> PollPlan:
        """Assign UUIDs to discovered interfaces and keep the plan.

        Args:
            device_id: The device.
            result: The discovery result.
            now: When it finished.

        Returns:
            PollPlan: The plan.

        Raises:
            None
        """
        del now
        base = plan_from_discovery(result)
        entries = tuple(
            e if e.if_index in self.unresolved
            else replace(e, interface_id=interface_uuid(device_id, e.if_index))
            for e in base.interfaces
        )  # fmt: skip
        self.plans[device_id] = PollPlan(entries)
        self.recorded.append(result)
        self.counter += 1
        return self.plans[device_id]


async def wait_until(
    check: Callable[[], Awaitable[bool]], timeout_s: float, step_s: float = 0.25
) -> bool:
    """Poll an async condition until it holds or the deadline passes.

    Args:
        check: The condition.
        timeout_s: Deadline.
        step_s: Pause between checks.

    Returns:
        bool: True once the condition held; False at the deadline.

    Raises:
        None
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        if await check():
            return True
        await asyncio.sleep(step_s)
    return False
