"""A ready-made test lab: in-process agent, fake communities, and the real adapter.

``lab`` starts a ``SimAgent`` serving the given devices under freshly
generated communities, and builds a ``PysnmpSessionFactory`` whose resolver
knows those communities by device name. Tests then talk to the agent through
vakr's real PySNMP adapter, exactly as the collector will.

Dependencies:
    tests.simulators.snmp: The agent, dataset, and credential helpers.
    vakr.adapters.snmp: The adapter under test.

Example:
    >>> FAST.timeout_s
    0.2
"""

import asyncio
import threading
from collections.abc import AsyncGenerator, Generator, Mapping, Sequence
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass

from tests.simulators.snmp.agent import SimAgent
from tests.simulators.snmp.agent_device import AgentDevice
from tests.simulators.snmp.iosxe_dataset import device_records
from tests.simulators.snmp.simkit import StaticResolver, fake_community
from tests.simulators.snmp.simtypes import DeviceSpec
from tests.simulators.snmp.snmprec import Record
from vakr.adapters.snmp import PysnmpSessionFactory
from vakr.collectors.snmp import CapabilityChange, InterfaceSample, SnmpPollResult
from vakr.discovery import DiscoveryResult, SubtableStatus
from vakr.ports.clock import Clock, SystemClock
from vakr.ports.snmp import SnmpSettings, SnmpTarget

FAST = SnmpSettings(timeout_s=0.2, retries=1)
"""Settings that keep timeout tests quick: 0.2 s per attempt, one retry."""


@dataclass
class Lab:
    """A running agent and a factory that can reach it.

    Attributes:
        agent: The in-process agent.
        port: Its UDP port on 127.0.0.1.
        factory: The PySNMP session factory.
        resolver: The resolver behind the factory.
        communities: Generated community by device name.
        devices: Simulated device by name.
    """

    agent: SimAgent
    port: int
    factory: PysnmpSessionFactory
    resolver: StaticResolver
    communities: dict[str, str]
    devices: dict[str, AgentDevice]

    def target(self, name: str) -> SnmpTarget:
        """Return the target for one simulated device.

        Args:
            name: The device name, which is also its credential name.

        Returns:
            SnmpTarget: 127.0.0.1 at the agent's port.

        Raises:
            None
        """
        return SnmpTarget("127.0.0.1", name, port=self.port)


@asynccontextmanager
async def lab(specs: Sequence[DeviceSpec], clock: Clock | None = None) -> AsyncGenerator[Lab]:
    """Serve ``specs`` from an in-process agent until the context exits.

    Args:
        specs: The devices.
        clock: Drives counter progression and the adapter's accounting;
            the system clock when ``None``.

    Returns:
        AsyncGenerator[Lab]: Yields the running lab.

    Raises:
        OSError: If the agent cannot bind a port.
    """
    clock = SystemClock() if clock is None else clock
    agent = SimAgent(clock=clock.monotonic)
    communities = {spec.name: fake_community() for spec in specs}
    devices = {s.name: agent.add(communities[s.name], device_records(s)) for s in specs}
    port = await agent.start()
    resolver = StaticResolver(communities)
    try:
        yield Lab(
            agent, port, PysnmpSessionFactory(resolver, clock=clock), resolver, communities, devices
        )
    finally:
        agent.close()


def samples(result: SnmpPollResult) -> tuple[InterfaceSample, ...]:
    """Return a poll's Stage 2 samples, failing the test if Stage 2 did not run.

    Args:
        result: The poll.

    Returns:
        tuple[InterfaceSample, ...]: The samples.

    Raises:
        AssertionError: If Stage 2 did not run.
    """
    assert result.stage2 is not None
    return result.stage2.samples


def changes(result: SnmpPollResult) -> tuple[CapabilityChange, ...]:
    """Return the capabilities a poll learned, failing the test if Stage 2 did not run.

    Args:
        result: The poll.

    Returns:
        tuple[CapabilityChange, ...]: The changes.

    Raises:
        AssertionError: If Stage 2 did not run.
    """
    assert result.stage2 is not None
    return result.stage2.changes


def status(found: DiscoveryResult, table: str) -> SubtableStatus:
    """Return one table's completeness, failing the test if it was not attempted.

    Args:
        found: The discovery result.
        table: The table name.

    Returns:
        SubtableStatus: The status.

    Raises:
        AssertionError: If the table was not attempted.
    """
    entry = found.subtable(table)
    assert entry is not None
    return entry.status


@contextmanager
def threaded_agent(devices: Mapping[str, Sequence[Record]]) -> Generator[int]:
    """Run an in-process agent on its own thread, for code that calls ``asyncio.run``.

    Args:
        devices: Records by community.

    Returns:
        Generator[int]: Yields the agent's UDP port on 127.0.0.1.

    Raises:
        OSError: If the agent cannot bind a port.
    """
    loop = asyncio.new_event_loop()
    agent = SimAgent()
    for community, records in devices.items():
        agent.add(community, records)
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        yield asyncio.run_coroutine_threadsafe(agent.start(), loop).result(timeout=10)
    finally:
        loop.call_soon_threadsafe(agent.close)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=10)
        loop.close()
