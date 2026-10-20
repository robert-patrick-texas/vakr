"""Invariant 2, 9.11, SNMP-016: collector-side failures stay collector-side.

DNS names resolve with a bounded lookup and the same guardrails as IP
literals; a lookup that fails, hangs, or returns an unusable address is a
``RESOLUTION`` problem. A library failure while sending, a socket that never
opened, and a request after the session closed are ``TRANSPORT`` problems.
An unusable answer from the agent is ``MALFORMED``, which does count as an
answer (SNMP-013).

Dependencies:
    asyncio, socket, pytest: Scenarios and monkeypatching.
    tests.simulators.snmp.simlab: The in-process agent and factory.

Example:
    $ uv run pytest tests/unit/adapters/snmp/test_snmp_failures.py
"""

import asyncio
import socket

import pytest
from pysnmp.error import PySnmpError
from pysnmp.proto import errind

from tests.simulators.snmp.iosxe_dataset import router
from tests.simulators.snmp.simlab import FAST, lab
from tests.unit.adapters.snmp.snmp_fakes import FakeTransport, answer, oids
from vakr.adapters.snmp import getbatch, transport, walking
from vakr.adapters.snmp.engine import RequestEngine
from vakr.adapters.snmp.guards import GuardedDispatcher, RedactedCommunity, close_dispatcher
from vakr.adapters.snmp.pdu import Pdu, PduError, PduKind, Reply
from vakr.adapters.snmp.transport import PysnmpTransport
from vakr.domain.enums import AddressFamily
from vakr.ports.clock import SystemClock
from vakr.ports.snmp import SnmpError, SnmpErrorKind, SnmpSessionError, SnmpSettings, SnmpTarget

UPTIME = (1, 3, 6, 1, 2, 1, 1, 3, 0)


def test_dns_names_resolve_with_guardrails(monkeypatch: pytest.MonkeyPatch) -> None:
    """9.11, 15.5: a name resolves; a hanging or unusable answer is a resolution problem.

    Args:
        monkeypatch: Replaces the loop's ``getaddrinfo``.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> list[SnmpErrorKind | int]:
        """Run the scenario against the simulator.

        Args:
            None

        Returns:
            list[SnmpErrorKind | int]: What the assertions check.

        Raises:
            None
        """
        found: list[SnmpErrorKind | int] = []
        async with lab([router(1)]) as sim:
            name = SnmpTarget("localhost", "rtr-sim-001", port=sim.port, family=AddressFamily.IPV4)
            async with sim.factory.open(name, FAST) as session:
                found.append(len((await session.get([UPTIME])).varbinds))
            loop = asyncio.get_running_loop()
            for fake in (_multicast, _hang):
                monkeypatch.setattr(loop, "getaddrinfo", fake)
                sim.factory._resolve_timeout_s = 0.2
                with pytest.raises(SnmpSessionError) as caught:
                    async with sim.factory.open(name, FAST):
                        pass
                found.append(caught.value.error.kind)
        return found

    assert asyncio.run(scenario()) == [1, SnmpErrorKind.RESOLUTION, SnmpErrorKind.RESOLUTION]


async def _multicast(*_: object, **__: object) -> list:
    """Resolve to a multicast address, which can never be an agent.

    Args:
        *_: Ignored.
        **__: Ignored.

    Returns:
        list: One ``getaddrinfo`` entry.

    Raises:
        None
    """
    return [(socket.AF_INET, socket.SOCK_DGRAM, 17, "", ("224.0.0.9", 161))]


async def _hang(*_: object, **__: object) -> list:
    """Never resolve within the bound.

    Args:
        *_: Ignored.
        **__: Ignored.

    Returns:
        list: Never returns in time.

    Raises:
        None
    """
    await asyncio.sleep(5)
    return []


def test_transport_failures_are_collector_side(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invariant 2: send failures, a socket that never opened, and a closed session.

    Args:
        monkeypatch: Breaks PySNMP's command functions.

    Returns:
        None

    Raises:
        None
    """

    async def boom(*_: object) -> object:
        """Fail the way PySNMP does when ``sendto`` fails.

        Args:
            *_: Ignored.

        Returns:
            object: Never returns.

        Raises:
            PySnmpError: Always.
        """
        raise PySnmpError("sendto failed") from OSError(101, "Network is unreachable")

    async def stopped(*_: object) -> tuple:
        """Answer with a non-timeout error indication.

        Args:
            *_: Ignored.

        Returns:
            tuple: PySNMP's result for a terminated request.

        Raises:
            None
        """
        return ("Request #1 terminated", 0, 0, ())

    async def silent(*_: object) -> tuple:
        """Time out the way PySNMP reports it.

        Args:
            *_: Ignored.

        Returns:
            tuple: PySNMP's result for a timed-out request.

        Raises:
            None
        """
        return (errind.requestTimedOut, 0, 0, ())

    async def scenario() -> list[str]:
        """Run the scenario against a dispatcher that never opened its socket.

        Args:
            None

        Returns:
            list[str]: The failure messages.

        Raises:
            None
        """
        messages = []
        endpoint = await transport.UdpTransportTarget.create(("127.0.0.1", 9), timeout=1, retries=0)
        dispatcher = GuardedDispatcher(("127.0.0.1", 9), "127.0.0.1:9")
        sender = PysnmpTransport(dispatcher, RedactedCommunity("x"), endpoint, "127.0.0.1:9")
        assert repr(sender) == "PysnmpTransport(127.0.0.1:9)"
        for fake in (boom, stopped, silent):
            monkeypatch.setattr(transport, "get_cmd", fake)
            with pytest.raises(PduError) as caught:
                await sender.send(Pdu(PduKind.GET, (UPTIME,)), 0.2)
            assert caught.value.error.collector_side
            messages.append(caught.value.error.message)
        sender.forget()
        with pytest.raises(PduError) as caught:
            await sender.send(Pdu(PduKind.GETNEXT, (UPTIME,)), 0.2)
        messages.append(caught.value.error.message)
        close_dispatcher(dispatcher)
        return messages

    found = asyncio.run(scenario())
    assert "Network is unreachable" in found[0]
    assert "stopped the request" in found[1]
    assert "could not open its UDP socket" in found[2]
    assert "already closed" in found[3]


def test_malformed_answers_count_as_answers_but_fail_their_request() -> None:
    """SNMP-013: an unusable answer proves the agent answered; its data is still rejected.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    broken = SnmpError(SnmpErrorKind.MALFORMED, "garbled")

    def script(pdu: Pdu) -> Reply:
        """Answer GETs normally and refuse everything else as garbled.

        Args:
            pdu: The request.

        Returns:
            Reply: The reply.

        Raises:
            PduError: For anything but GET.
        """
        if pdu.kind is PduKind.GET and len(pdu.oids) == 1:
            return answer(pdu)
        raise PduError(broken)

    engine = RequestEngine(
        FakeTransport(script), SnmpSettings(), asyncio.Lock(), SystemClock(), "x"
    )
    walked = asyncio.run(walking.walk(engine, (1, 3, 6, 1)))
    got = asyncio.run(getbatch.get(engine, oids(3)))
    assert walked.error == broken
    assert got.error == broken
    assert len(got.failed) == 3
    assert engine.answered
    assert engine.stats.responses == 2
