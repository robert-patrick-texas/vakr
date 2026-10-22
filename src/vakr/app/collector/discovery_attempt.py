"""One discovery attempt: walk the agent, record the result, say what happened (DISC-001, DISC-009).

``attempt`` opens an SNMP session, runs ``vakr.discovery.discover``, and
hands an answered result to inventory in a worker thread. It answers with
an ``Attempt``: ``ok`` with the new plan and, when inventory reports it,
what the write changed; ``failed`` with the reason in operator words; or
``gone`` when inventory no longer knows the device. A manual rediscovery
turns an ``Attempt`` into its operation's result (DISC-T03).

``ReportingInventory`` is the optional part of the inventory contract that
says what a discovery changed (``vakr.inventory.service.InventoryService``
has it); a source without it still works, with empty change counts.

Dependencies:
    asyncio: The worker thread.
    vakr.discovery, vakr.inventory.discovery_report: The walk and the report.
    vakr.app.collector: The inventory contract and the SNMP target.

Safety:
    Session errors are the adapter's operator messages, never a community.

Example:
    >>> Attempt(FAILED, "the agent did not answer").ok
    False
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from vakr.app.collector.inventory import InventorySource
from vakr.app.collector.snmp_parts import snmp_target
from vakr.collectors.snmp.config import SnmpConfig
from vakr.discovery import discover
from vakr.discovery.model import DiscoveryResult
from vakr.domain.errors import NotFoundError
from vakr.domain.ids import DeviceId
from vakr.domain.inventory import Device
from vakr.inventory.discovery_report import DiscoveryReport
from vakr.ports.clock import Clock
from vakr.ports.snmp import SnmpSessionError, SnmpSessionFactory

OK, FAILED, GONE = "ok", "failed", "gone"


@runtime_checkable
class ReportingInventory(Protocol):
    """An inventory source that also says what a recorded discovery changed."""

    def record_discovery_report(
        self, device_id: DeviceId, result: DiscoveryResult, now: datetime
    ) -> DiscoveryReport:
        """Record a discovery and report the plan and the changes.

        Args:
            device_id: The device.
            result: What discovery found.
            now: When it finished (UTC).

        Returns:
            DiscoveryReport: Plan, identity changes, capability changes.

        Raises:
            NotFoundError: If the device is gone.
            StorageError: If the metadata database is unavailable.
        """
        ...


@dataclass(frozen=True, slots=True)
class Attempt:
    """How one discovery attempt ended.

    Attributes:
        status: ``ok``, ``failed``, or ``gone``.
        why: Operator words for a failure; empty otherwise.
        found: The discovery result, when the agent answered.
        report: The recorded plan and changes, when ``ok``.
        at: When the result was recorded (UTC), when ``ok``.
    """

    status: str
    why: str = ""
    found: DiscoveryResult | None = None
    report: DiscoveryReport | None = None
    at: datetime | None = None

    @property
    def ok(self) -> bool:
        """Whether the discovery was recorded.

        Args:
            None

        Returns:
            bool: True for ``ok``.

        Raises:
            None
        """
        return self.status == OK


async def attempt(
    inventory: InventorySource, factory: SnmpSessionFactory, clock: Clock,
    config: SnmpConfig | None, device: Device,
) -> Attempt:  # fmt: skip
    """Walk one device and record an answered result.

    Args:
        inventory: Where results are recorded.
        factory: Opens SNMP sessions.
        clock: Monotonic and UTC time.
        config: Current ``[snmp]`` settings, or ``None`` before they load.
        device: The device.

    Returns:
        Attempt: The outcome.

    Raises:
        StorageError: If inventory cannot store the result.
    """
    if config is None:
        return Attempt(FAILED, "no SNMP settings loaded")
    try:
        async with factory.open(snmp_target(device, config.port), config.settings) as session:
            found = await discover(session, clock, config.settings.abandon_after)
    except SnmpSessionError as exc:
        return Attempt(FAILED, exc.error.message)
    if not found.answered:
        return Attempt(FAILED, "the agent did not answer", found)
    now = clock.now_utc()
    try:
        if isinstance(inventory, ReportingInventory):
            report = await asyncio.to_thread(
                inventory.record_discovery_report, device.id, found, now
            )
        else:
            plan = await asyncio.to_thread(inventory.record_discovery, device.id, found, now)
            report = DiscoveryReport(plan)
    except NotFoundError:
        return Attempt(GONE, "removed from inventory", found)
    return Attempt(OK, "", found, report, now)
