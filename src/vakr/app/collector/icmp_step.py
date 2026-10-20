"""The scheduler's ICMP step over the real ICMP collector (9.2, ICMP-001..014, E.25 #5).

``CollectorIcmpStep.icmp`` resolves the device's poll address (a DNS name
gets a bounded lookup, 9.11), runs one probe cycle through
``IcmpCollector``, and hands the scheduler the reply count, the
``IcmpObservation``, and, when ``icmp_gated`` skips SNMP, the
``SnmpDeviceObservation`` that records the skip (9.2 "persist ICMP -> SNMP
SKIPPED").

Collector trouble is never a reply count. When no probe could be sent at
all (``ping_group_range`` excludes the collector, no route, an unresolvable
name), the step raises, the scheduler records a collector-side ``ERROR``,
and the gate sees no reply count: under ``icmp_gated`` SNMP then runs, so a
host where vakr cannot ping still collects (E.25 #5).

Dependencies:
    asyncio, ipaddress, socket: Bounded name resolution.
    vakr.collectors.icmp: The cycle and its summary.
    vakr.scheduler.steps: The step protocol.

Safety:
    Sends ICMP Echo only, through the unprivileged adapter. Exception
    messages name the fault, never the address.

Example:
    >>> IcmpNotSentError("permission").args
    ('permission',)
"""

import asyncio
import ipaddress
import socket
from collections.abc import Callable

from vakr.app.collector.tally import FaultTally
from vakr.collectors.icmp.cycle import IcmpCollector
from vakr.collectors.icmp.cycle import PollContext as IcmpContext
from vakr.collectors.snmp import skipped_snmp_observation
from vakr.domain.enums import AddressFamily, ReachabilityStrategy
from vakr.domain.inventory import Device
from vakr.domain.quality import Quality
from vakr.logic.icmp import SnmpPlan
from vakr.ports.icmp_types import CollectorFault
from vakr.scheduler.steps import IcmpResult, PollContext

RESOLVE_TIMEOUT_S = 2.0
"""Most time one DNS lookup of an ICMP target may take (E.22, as ``vakr icmp probe``)."""

_FAMILIES = {AddressFamily.IPV4: socket.AF_INET, AddressFamily.IPV6: socket.AF_INET6}


class IcmpNotSentError(Exception):
    """No probe left the collector, so the cycle says nothing about the device."""


class IcmpResolutionError(Exception):
    """The poll address is a name that did not resolve in time (9.11)."""


async def resolve_address(device: Device, timeout_s: float = RESOLVE_TIMEOUT_S) -> str:
    """Return the IP literal to probe, resolving a DNS name with a bound.

    Args:
        device: The device; its ``address_family`` picks the family.
        timeout_s: Bound on the lookup.

    Returns:
        str: A canonical IP literal.

    Raises:
        IcmpResolutionError: If the name does not resolve in time.
    """
    try:
        return str(ipaddress.ip_address(device.poll_address))
    except ValueError:
        pass  # a DNS name
    chosen = device.address_family
    family = socket.AF_UNSPEC if chosen is None else _FAMILIES[chosen]
    loop = asyncio.get_running_loop()
    try:
        async with asyncio.timeout(timeout_s):
            infos = await loop.getaddrinfo(
                device.poll_address, None, family=family, type=socket.SOCK_DGRAM
            )
    except (OSError, TimeoutError) as exc:
        raise IcmpResolutionError(type(exc).__name__) from None
    if not infos:
        raise IcmpResolutionError("no address")
    return str(ipaddress.ip_address(str(infos[0][4][0]).split("%", 1)[0]))


class CollectorIcmpStep:
    """``IcmpStep`` for the collector service."""

    def __init__(self, collector: IcmpCollector, tally: FaultTally) -> None:
        """Bind the collector.

        Args:
            collector: The ICMP collector; its ``settings`` follow reloads.
            tally: Where collector-side failures are counted.

        Returns:
            None

        Raises:
            None
        """
        self.collector = collector
        self._tally = tally
        # Called when the host stops letting vakr ping: a hard stop (ICMP-012).
        self.on_permission_lost: Callable[[], None] | None = None

    async def icmp(self, ctx: PollContext) -> IcmpResult:
        """Probe the device once and report the cycle.

        Args:
            ctx: The poll.

        Returns:
            IcmpResult: Replies, sent probes, observations, and poll flags.

        Raises:
            IcmpResolutionError: If the poll address does not resolve.
            IcmpNotSentError: If no probe could be sent (collector trouble).
        """
        device, strategy = ctx.target.device, ctx.target.strategy
        try:
            address = await resolve_address(device)
        except IcmpResolutionError:
            self._tally.fault("icmp_resolution")
            raise
        context = IcmpContext(ctx.poll_id, ctx.collector_id, device.id, strategy, self._tally.state)
        cycle = await self.collector.collect(address, context)
        if cycle.observation is None:
            self._tally.fault("icmp_not_sent")
            fault = cycle.result.fault
            if fault is CollectorFault.PERMISSION_DENIED and self.on_permission_lost is not None:
                self.on_permission_lost()
            raise IcmpNotSentError("unknown" if fault is None else fault.value)
        observations: list[object] = [cycle.observation]
        if strategy is ReachabilityStrategy.ICMP_GATED and cycle.snmp is SnmpPlan.SKIP:
            observations.append(
                skipped_snmp_observation(
                    poll_id=ctx.poll_id,
                    collector_id=ctx.collector_id,
                    device_id=device.id,
                    timestamp=cycle.observation.timestamp,
                )
            )
        flags = cycle.summary.quality & Quality.COLLECTOR_DEGRADED
        return IcmpResult(cycle.summary.received, cycle.summary.sent, tuple(observations), flags)
