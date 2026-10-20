"""Discover and poll many simulated devices at once and measure it (blueprint 4.2, 23.3).

``run_cycle`` discovers every device, then polls each once with Stage 1 and
Stage 2, with bounded concurrency across devices and one PDU in flight per
device. It reports wall time for each phase and per-device poll durations,
whose nearest-rank p95 is what blueprint 4.2 bounds (under 15 s).

Dependencies:
    asyncio: Bounded concurrency.
    vakr.adapters.snmp, vakr.discovery, vakr.collectors.snmp: The code measured.
    vakr.logic.stats: Nearest-rank percentiles.

Example:
    >>> LoadReport(1, 1, 0.5, 0.25, (0.25,), 10).poll_p95_s
    0.25
"""

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass

from tests.simulators.snmp.simkit import StaticResolver
from vakr.adapters.snmp import PysnmpSessionFactory
from vakr.collectors.snmp import PollPlan, SnmpPollResult, plan_from_discovery, poll_device
from vakr.collectors.snmp.poll import ANSWERED, snmp_state
from vakr.discovery import DiscoveryResult, discover
from vakr.logic.stats import p95
from vakr.ports.clock import SystemClock
from vakr.ports.snmp import SnmpSettings, SnmpTarget


@dataclass(frozen=True)
class LoadReport:
    """What one discover-and-poll cycle cost.

    Attributes:
        devices: Devices attempted.
        answered: Devices whose poll was answered.
        discovery_s: Wall time to discover every device.
        poll_s: Wall time to poll every device once.
        poll_durations_s: Each device's poll duration.
        interfaces: Up/up interfaces sampled in Stage 2.
    """

    devices: int
    answered: int
    discovery_s: float
    poll_s: float
    poll_durations_s: tuple[float, ...]
    interfaces: int

    @property
    def poll_p95_s(self) -> float:
        """Return the nearest-rank p95 of per-device poll durations.

        Args:
            None

        Returns:
            float: Seconds.

        Raises:
            None
        """
        return p95(list(self.poll_durations_s))


@dataclass(frozen=True)
class _Runner:
    """Opens bounded sessions for the cycle.

    Attributes:
        factory: The session factory.
        gate: Bounds how many devices are handled at once.
        settings: The request budget.
    """

    factory: PysnmpSessionFactory
    gate: asyncio.Semaphore
    settings: SnmpSettings

    async def discover(self, target: SnmpTarget) -> DiscoveryResult:
        """Discover one device.

        Args:
            target: The device.

        Returns:
            DiscoveryResult: Its discovery.

        Raises:
            None
        """
        async with self.gate, self.factory.open(target, self.settings) as session:
            return await discover(session, SystemClock())

    async def poll(self, target: SnmpTarget, plan: PollPlan) -> SnmpPollResult:
        """Poll one device once.

        Args:
            target: The device.
            plan: Its poll plan.

        Returns:
            SnmpPollResult: The poll.

        Raises:
            None
        """
        async with self.gate, self.factory.open(target, self.settings) as session:
            return await poll_device(session, plan, SystemClock())


async def run_cycle(port: int, communities: Mapping[str, str], concurrency: int = 32) -> LoadReport:
    """Discover every device, then poll each one once.

    Args:
        port: The simulator's UDP port on 127.0.0.1.
        communities: Community by device name.
        concurrency: Devices handled at once (the scheduler's global bound).

    Returns:
        LoadReport: Timings and counts.

    Raises:
        None
    """
    factory = PysnmpSessionFactory(StaticResolver(communities))
    runner = _Runner(factory, asyncio.Semaphore(concurrency), SnmpSettings())
    targets = [SnmpTarget("127.0.0.1", name, port=port) for name in communities]
    started = time.monotonic()
    found = await asyncio.gather(*(runner.discover(t) for t in targets))
    discovered = time.monotonic()
    plans = [plan_from_discovery(d) for d in found]
    polls = await asyncio.gather(*(runner.poll(t, p) for t, p in zip(targets, plans, strict=True)))
    finished = time.monotonic()
    return LoadReport(
        devices=len(targets),
        answered=sum(snmp_state(p) in ANSWERED for p in polls),
        discovery_s=discovered - started,
        poll_s=finished - discovered,
        poll_durations_s=tuple(p.elapsed_s for p in polls),
        interfaces=sum(len(p.stage2.samples) for p in polls if p.stage2),
    )
