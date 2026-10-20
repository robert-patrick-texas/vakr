"""Test doubles for the collector service parts: devices, targets, samples, a scripted prober.

The module name is unique on purpose: helpers are never imported from a
``conftest``. Devices use documentation addresses and credential names
only; no community appears anywhere.

Dependencies:
    vakr.app.collector, vakr.scheduler: The code under test.
    tests.unit.collectors.icmp.icmp_results: Compact ICMP results.

Example:
    >>> make_device(1).name
    'dev001'
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from tests.unit.collectors.icmp.icmp_results import make_result
from vakr.collectors.snmp import InterfaceSample
from vakr.domain.enums import CounterFamily, IfAdminStatus, IfOperStatus, ReachabilityStrategy
from vakr.domain.ids import CollectorId, DeviceId, InterfaceId, PollId, new_id
from vakr.domain.inventory import API_MANAGED, Device
from vakr.ports.icmp import IcmpCounters, ProbeRequest, ProbeResult
from vakr.scheduler.steps import PollContext
from vakr.scheduler.targets import PollTarget

T0 = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
CID = CollectorId(UUID("0190a3c4-0000-7000-8000-0000000c0c01"))


def make_device(i: int, **changes: object) -> Device:
    """Build a device ``devNNN`` at a documentation address.

    Args:
        i: Device number.
        changes: Field overrides.

    Returns:
        Device: The record, with credential reference ``lab-v2c``.

    Raises:
        None
    """
    device = Device(
        DeviceId(UUID(f"0190a3c4-0000-7000-8000-{i:012x}")), f"dev{i:03d}", f"192.0.2.{i}",
        API_MANAGED, T0, T0, credential_ref="lab-v2c",
    )  # fmt: skip
    return replace(device, **changes)


def make_target(
    device: Device, strategy: ReachabilityStrategy = ReachabilityStrategy.ICMP_GATED,
    interval_s: int = 60,
) -> PollTarget:  # fmt: skip
    """Wrap a device as a poll target.

    Args:
        device: The device.
        strategy: Reachability strategy.
        interval_s: Effective interval.

    Returns:
        PollTarget: The target.

    Raises:
        None
    """
    return PollTarget(device, interval_s, interval_s, strategy)


def poll_ctx(target: PollTarget) -> PollContext:
    """Return a fresh poll context for a target.

    Args:
        target: The device.

    Returns:
        PollContext: New poll ID, the lab collector, ``T0``.

    Raises:
        None
    """
    return PollContext(new_id(PollId), CID, target, T0)


def iface(n: int) -> InterfaceId:
    """Return a deterministic interface UUID.

    Args:
        n: Interface number.

    Returns:
        InterfaceId: The UUID.

    Raises:
        None
    """
    return InterfaceId(UUID(f"0190a3c4-0000-7000-8000-00000001{n:04x}"))


def counter_sample(
    if_index: int, at_s: float, octets: int | None, *, interface: int = 1,
    family: CounterFamily = CounterFamily.HC64,
) -> InterfaceSample:  # fmt: skip
    """Build a Stage 2 sample whose counters grow with ``octets``.

    Args:
        if_index: The ifIndex.
        at_s: Seconds after ``T0``, on both clocks.
        octets: Inbound octets (outbound is half); ``None`` for an unanswered sample.
        interface: Interface number for the UUID.
        family: Counter family.

    Returns:
        InterfaceSample: The sample.

    Raises:
        None
    """
    up, oper = IfAdminStatus.UP, IfOperStatus.UP
    if octets is None:
        return InterfaceSample(if_index, iface(interface), T0 + timedelta(seconds=at_s), at_s,
                               family, up, oper)  # fmt: skip
    return InterfaceSample(
        if_index, iface(interface), T0 + timedelta(seconds=at_s), at_s, family, up, oper,
        in_octets=octets, out_octets=octets // 2, in_packets=octets // 100,
        out_packets=octets // 200, in_errors=0, out_errors=0,
    )  # fmt: skip


class ScriptedProber:
    """An ``IcmpProber`` that answers each cycle from a script.

    Attributes:
        script: Compact probe specs per cycle (see ``icmp_results``); the
            last entry repeats.
        requests: Requests received.
    """

    def __init__(self, *script: list[int | str]) -> None:
        """Hold the script.

        Args:
            script: One spec list per cycle.

        Returns:
            None

        Raises:
            None
        """
        self.script = list(script)
        self.requests: list[ProbeRequest] = []

    async def probe(self, request: ProbeRequest) -> ProbeResult:
        """Answer one cycle.

        Args:
            request: The request.

        Returns:
            ProbeResult: The scripted result for the request's target.

        Raises:
            None
        """
        self.requests.append(request)
        specs = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        return make_result(specs, target=request.target)

    def counters(self) -> IcmpCounters:
        """Return empty counters.

        Args:
            None

        Returns:
            IcmpCounters: All zero.

        Raises:
            None
        """
        return IcmpCounters()
