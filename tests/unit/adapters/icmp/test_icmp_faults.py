"""Tests that collector trouble is collector evidence, never target loss (OBS-002, OBS-003).

Covers ``ping_group_range`` refusal and the raw-socket fallback (ICMP-012),
local send errors, a stale queued ICMP error failing an unrelated send,
socket read failures, the in-flight cap and per-cycle deadline, kernel
receive-buffer drops, cancellation, the socket limit, and shutdown. In
every case the affected probes are ``NOT_SENT`` or ``ABORTED`` with a
``CollectorFault``, and no probe is reported lost because of the collector.

Dependencies:
    asyncio, errno, pytest: Event loop, error codes, parametrisation.
    tests.unit.adapters.icmp.icmp_fakes: The fake socket layer.
    vakr.adapters.icmp: The prober and its channel.

Example:
    $ uv run pytest tests/unit/adapters/icmp/test_icmp_faults.py
"""

import asyncio
import errno
import socket

import pytest

from tests.unit.adapters.icmp.icmp_fakes import (
    FakeNet,
    FakeSocket,
    drain,
    echo_reply,
    ext_err,
    manual_clock,
    wait_sent,
)
from vakr.adapters.icmp import wire
from vakr.adapters.icmp.base import Tally
from vakr.adapters.icmp.channel import Channel
from vakr.adapters.icmp.pool import Opener
from vakr.adapters.icmp.transport import SocketIcmpProber
from vakr.adapters.icmp.wire import SocketKind
from vakr.ports.icmp import CollectorFault, IcmpCounters, ProbeOutcome, ProbeRequest, ProbeResult

PERM = CollectorFault.PERMISSION_DENIED
REQUEST = ProbeRequest("192.0.2.1", 5, 0.0, 0.05)


async def probe_once(
    net: Opener,
    request: ProbeRequest = REQUEST,
    *,
    max_in_flight: int = 1024,
    raw_fallback: bool = True,
) -> tuple[ProbeResult, IcmpCounters]:
    """Run one cycle on a fresh prober.

    Args:
        net: Fake socket factory.
        request: The cycle.
        max_in_flight: In-flight cap.
        raw_fallback: Whether the raw fallback is allowed.

    Returns:
        tuple[ProbeResult, IcmpCounters]: Result and counters.

    Raises:
        None
    """
    prober = SocketIcmpProber(opener=net, max_in_flight=max_in_flight, raw_fallback=raw_fallback)
    async with prober:
        result = await prober.probe(request)
        await asyncio.sleep(0)  # slots are released by done-callbacks, one loop turn later
        return result, prober.counters()


def test_ping_group_range_refusal_is_not_sent_permission_denied() -> None:
    """ICMP-012: without permission nothing is sent and the reason is the collector's.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    net = FakeNet({SocketKind.DATAGRAM: PERM, SocketKind.RAW: PERM})
    result, counters = asyncio.run(probe_once(net))
    assert result.fault is PERM
    assert {s.outcome for s in result.samples} == {ProbeOutcome.NOT_SENT}
    assert result.raw_rtts_us == ()
    assert (counters.not_sent, counters.sent, counters.sockets_open) == (5, 0, 0)


@pytest.mark.parametrize("raw_fallback", [True, False])
def test_other_open_failures_never_try_raw(raw_fallback: bool) -> None:
    """Only a permission refusal can be cured by CAP_NET_RAW, so only it falls back.

    Args:
        raw_fallback: Whether the fallback is enabled.

    Returns:
        None

    Raises:
        None
    """
    net = FakeNet({SocketKind.DATAGRAM: CollectorFault.FAMILY_UNSUPPORTED})
    result, _ = asyncio.run(probe_once(net, raw_fallback=raw_fallback))
    assert result.fault is CollectorFault.FAMILY_UNSUPPORTED
    assert net.sockets == []


async def raw_cycle(net: FakeNet) -> tuple[ProbeResult, IcmpCounters]:
    """Probe through the raw fallback and answer with and without our identifier.

    Args:
        net: Fake network refusing datagram sockets.

    Returns:
        tuple[ProbeResult, IcmpCounters]: Result and counters.

    Raises:
        None
    """
    async with SocketIcmpProber(clock=manual_clock(), opener=net) as prober:
        task = asyncio.create_task(prober.probe(ProbeRequest("192.0.2.1", 2, 0.0, 0.2)))
        sock = await wait_sent(net.sockets, 2)
        ident = wire.HEADER.unpack_from(sock.sent[0][0])[3]
        header = bytes([0x45]) + bytes(19)
        sock.deliver(header + echo_reply(sock.sent[0][0], ident), ("192.0.2.1", 0))
        sock.deliver(header + echo_reply(sock.sent[1][0], ident ^ 1), ("192.0.2.1", 0))
        await drain(sock)
        return await task, prober.counters()


def test_raw_fallback_works_and_ignores_other_programs_icmp() -> None:
    """ICMP-012 fallback: with CAP_NET_RAW, a raw socket is used; others' pings are ignored.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    net = FakeNet({SocketKind.DATAGRAM: PERM})
    result, counters = asyncio.run(raw_cycle(net))
    assert net.sockets[0].kind is SocketKind.RAW
    assert [s.outcome for s in result.samples] == [ProbeOutcome.REPLY, ProbeOutcome.TIMEOUT]
    assert wire.checksum(net.sockets[0].sent[0][0]) == 0
    assert counters.foreign == 0


@pytest.mark.parametrize(
    ("code", "fault"),
    [
        (errno.ENETUNREACH, CollectorFault.LOCAL_NO_ROUTE),
        (errno.EPERM, CollectorFault.LOCAL_POLICY),
        (errno.ENOBUFS, CollectorFault.OVERLOADED),
        (errno.EADDRNOTAVAIL, CollectorFault.SOURCE_UNAVAILABLE),
        (errno.EINVAL, CollectorFault.SEND_ERROR),
    ],
)
def test_local_send_errors_are_collector_faults(code: int, fault: CollectorFault) -> None:
    """A send this host refused says nothing about the target (invariant 2).

    Args:
        code: errno ``sendto`` raises.
        fault: Expected classification.

    Returns:
        None

    Raises:
        None
    """

    def opener(version: int, kind: SocketKind, source: str | None) -> FakeSocket:
        """Open a socket whose first send fails.

        Args:
            version: IP version.
            kind: Socket kind.
            source: Source address.

        Returns:
            FakeSocket: The socket.

        Raises:
            None
        """
        sock = FakeSocket(version, kind, source)
        sock.send_errors.extend([code] * 3)
        return sock

    result, counters = asyncio.run(probe_once(opener, ProbeRequest("192.0.2.1", 1, 0.0, 0.05)))
    assert result.samples[0].outcome is ProbeOutcome.NOT_SENT
    assert result.fault is fault
    assert counters.not_sent == 1


async def stale_error(net: FakeNet) -> tuple[ProbeResult, ProbeResult]:
    """Fail target B's send with an error queued for target A, then answer B.

    Args:
        net: Fake network.

    Returns:
        tuple[ProbeResult, ProbeResult]: Results for A and B.

    Raises:
        None
    """
    async with SocketIcmpProber(clock=manual_clock(), opener=net) as prober:
        first = asyncio.create_task(prober.probe(ProbeRequest("192.0.2.1", 1, 0.0, 1.0)))
        sock = await wait_sent(net.sockets, 1)
        anc = [(socket.IPPROTO_IP, socket.IP_RECVERR, ext_err(2, 3, 1, "198.51.100.1"))]
        sock.errqueue.append((sock.sent[0][0], anc, ("192.0.2.1", 0)))  # queued, not yet read
        sock.send_errors.append(errno.EHOSTUNREACH)
        second = asyncio.create_task(prober.probe(ProbeRequest("192.0.2.2", 1, 0.0, 1.0)))
        sock = await wait_sent(net.sockets, 2)
        sock.deliver(echo_reply(sock.sent[1][0]), ("192.0.2.2", 0))
        await drain(sock)
        return await first, await second


def test_stale_queued_error_for_one_target_does_not_fail_another() -> None:
    """IP_RECVERR reports a pending error on the next call; the send is retried.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    a, b = asyncio.run(stale_error(FakeNet()))
    assert a.samples[0].outcome is ProbeOutcome.UNREACHABLE
    assert b.samples[0].outcome is ProbeOutcome.REPLY


async def broken_reads(net: FakeNet) -> tuple[ProbeResult, ProbeResult, IcmpCounters]:
    """Break the socket during a cycle, then probe again.

    Args:
        net: Fake network.

    Returns:
        tuple[ProbeResult, ProbeResult, IcmpCounters]: Both results and counters.

    Raises:
        None
    """
    async with SocketIcmpProber(clock=manual_clock(), opener=net) as prober:
        task = asyncio.create_task(prober.probe(ProbeRequest("192.0.2.1", 2, 0.0, 5.0)))
        sock = await wait_sent(net.sockets, 2)
        sock.fail_reads(errno.EHOSTUNREACH)  # a pending ICMP error: skipped
        sock.fail_reads(errno.EBADF)  # a broken socket: the channel closes
        broken = await task
        net.auto_reply = True
        again = await prober.probe(ProbeRequest("192.0.2.1", 1, 0.0, 5.0))
        return broken, again, prober.counters()


def test_a_broken_socket_aborts_waiting_probes_and_is_replaced() -> None:
    """Probes the collector can no longer watch are ABORTED, not lost; a new socket opens.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    net = FakeNet()
    broken, again, counters = asyncio.run(broken_reads(net))
    assert {s.outcome for s in broken.samples} == {ProbeOutcome.ABORTED}
    assert broken.fault is CollectorFault.SOCKET_ERROR
    assert broken.raw_rtts_us == ()
    assert again.samples[0].outcome is ProbeOutcome.REPLY
    assert (len(net.sockets), counters.receive_errors, counters.in_flight) == (2, 1, 0)


def test_in_flight_cap_and_deadline_turn_late_probes_into_overload() -> None:
    """OBS-003: probes that cannot get a slot before the deadline are overload, not loss.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    request = ProbeRequest("192.0.2.1", 5, 0.0, 0.3)
    result, counters = asyncio.run(probe_once(FakeNet(), request, max_in_flight=2))
    outcomes = [s.outcome for s in result.samples]
    assert outcomes == [ProbeOutcome.TIMEOUT] * 2 + [ProbeOutcome.NOT_SENT] * 3
    assert result.fault is CollectorFault.OVERLOADED
    assert result.raw_rtts_us == (-1, -1)
    assert (counters.in_flight_peak, counters.in_flight, counters.not_sent) == (2, 0, 3)


async def overflowing(net: FakeNet) -> tuple[ProbeResult, IcmpCounters]:
    """Let the kernel "drop" three replies on our socket during a cycle.

    Args:
        net: Fake network.

    Returns:
        tuple[ProbeResult, IcmpCounters]: Result and counters.

    Raises:
        None
    """
    async with SocketIcmpProber(opener=net) as prober:
        task = asyncio.create_task(prober.probe(ProbeRequest("192.0.2.1", 2, 0.0, 0.05)))
        (await wait_sent(net.sockets, 2)).drops += 3
        return await task, prober.counters()


def test_receive_buffer_drops_mark_the_cycle_as_collector_trouble() -> None:
    """Losses during a kernel receive-buffer overflow may be ours, so the cycle is flagged.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    result, counters = asyncio.run(overflowing(FakeNet()))
    assert result.fault is CollectorFault.RECEIVE_OVERFLOW
    assert counters.receive_overflow == 3


async def cancelled(net: FakeNet) -> tuple[IcmpCounters, ProbeResult]:
    """Cancel a cycle mid-flight, then run another.

    Args:
        net: Fake network.

    Returns:
        tuple[IcmpCounters, ProbeResult]: Counters after the cancel and the next result.

    Raises:
        None
    """
    async with SocketIcmpProber(opener=net, max_in_flight=3) as prober:
        task = asyncio.create_task(prober.probe(ProbeRequest("192.0.2.1", 3, 0.0, 5.0)))
        await wait_sent(net.sockets, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        after = prober.counters()
        net.sockets[0].auto_reply = True
        return after, await prober.probe(ProbeRequest("192.0.2.1", 3, 0.0, 5.0))


def test_cancellation_releases_every_slot_and_pending_entry() -> None:
    """21.9: cancelling a cycle frees its in-flight slots and pending entries.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    after, result = asyncio.run(cancelled(FakeNet()))
    assert after.in_flight == 0
    assert result.received == 3


async def two_sources(net: FakeNet) -> tuple[ProbeResult, ProbeResult, ProbeResult]:
    """With one socket allowed, use a second source while idle and while busy.

    Args:
        net: Fake network.

    Returns:
        tuple[ProbeResult, ProbeResult, ProbeResult]: Idle swap, busy owner, refused.

    Raises:
        None
    """
    async with SocketIcmpProber(opener=net, max_sockets=1) as prober:
        net.auto_reply = True
        idle_swap = await prober.probe(ProbeRequest("192.0.2.1", 1, 0.0, 1.0, "192.0.2.10"))
        net.auto_reply = False
        busy = asyncio.create_task(prober.probe(ProbeRequest("192.0.2.1", 1, 0.0, 0.05)))
        await wait_sent(net.sockets, 1, index=1)
        refused = await prober.probe(ProbeRequest("192.0.2.1", 1, 0.0, 1.0, "192.0.2.10"))
        return idle_swap, await busy, refused


def test_socket_limit_closes_idle_sockets_and_refuses_when_all_are_busy() -> None:
    """Sockets grow with distinct sources, never targets, and are capped.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    net = FakeNet()
    idle_swap, busy, refused = asyncio.run(two_sources(net))
    assert idle_swap.received == 1
    assert busy.samples[0].outcome is ProbeOutcome.TIMEOUT
    assert refused.fault is CollectorFault.SOCKET_LIMIT
    assert net.sockets[0].closed


async def shutdown(net: FakeNet) -> ProbeResult:
    """Close the prober while a probe waits.

    Args:
        net: Fake network.

    Returns:
        ProbeResult: The interrupted cycle.

    Raises:
        None
    """
    prober = SocketIcmpProber(opener=net)
    task = asyncio.create_task(prober.probe(ProbeRequest("192.0.2.1", 1, 0.0, 5.0)))
    await wait_sent(net.sockets, 1)
    await prober.aclose()
    return await task


def test_shutdown_aborts_waiting_probes_and_closes_sockets() -> None:
    """21.9: closing releases resources and does not invent loss.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    net = FakeNet()
    result = asyncio.run(shutdown(net))
    assert result.samples[0].outcome is ProbeOutcome.ABORTED
    assert result.samples[0].detail == "collector stopped"
    assert net.sockets[0].closed


async def channel_edges() -> list[object]:
    """Fill a one-slot channel and send to an IPv6 zone that does not exist.

    Args:
        None

    Returns:
        list[object]: Outcomes and faults observed.

    Raises:
        None
    """
    sock = FakeSocket(6, SocketKind.DATAGRAM, None)
    channel = Channel(sock, 6, SocketKind.DATAGRAM, capacity=1, clock=manual_clock(), tally=Tally())
    zone = (channel.send(0, "fe80::1%vakr-no-such-if", 5.0)).result()
    first = channel.send(1, "2001:db8::1", 5.0)
    full = (channel.send(2, "2001:db8::1", 5.0)).result()
    channel.close()
    channel.close()  # closing twice is harmless
    late = channel.send(3, "2001:db8::1", 5.0).result()
    return [(await first).outcome, full.fault, zone.fault, late.fault, sock.closed]


def test_channel_refuses_past_capacity_and_unknown_zones() -> None:
    """The pending table is a hard bound, and a bad IPv6 zone is a local routing fault.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert asyncio.run(channel_edges()) == [
        ProbeOutcome.ABORTED,
        CollectorFault.OVERLOADED,
        CollectorFault.LOCAL_NO_ROUTE,
        CollectorFault.SOCKET_ERROR,
        True,
    ]


@pytest.mark.parametrize(("in_flight", "sockets"), [(0, 8), (16385, 8), (1024, 0), (1024, 65)])
def test_prober_limits_are_validated(in_flight: int, sockets: int) -> None:
    """Every limit is bounded (CLAUDE.md non-negotiables).

    Args:
        in_flight: In-flight cap.
        sockets: Socket cap.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValueError, match="must be"):
        SocketIcmpProber(max_in_flight=in_flight, max_sockets=sockets)
