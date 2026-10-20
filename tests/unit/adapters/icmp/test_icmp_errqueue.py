"""Tests for the error-queue and raw-error paths of the ICMP channel (ICMP-012, OBS-002).

Covers a local-origin error (the packet never left: collector evidence),
error-queue entries that do not mean failure, error-queue read failures,
retries exhausted by repeated stale errors, ICMP errors on the raw
fallback socket, and a timer firing for a probe that already ended.

Dependencies:
    asyncio, errno, socket: Event loop and kernel constants.
    tests.unit.adapters.icmp.icmp_fakes: The fake socket layer.
    vakr.adapters.icmp: The prober and its bookkeeping.

Example:
    $ uv run pytest tests/unit/adapters/icmp/test_icmp_errqueue.py
"""

import asyncio
import errno
import socket

from tests.unit.adapters.icmp.icmp_fakes import (
    FakeNet,
    drain,
    echo_reply,
    ext_err,
    manual_clock,
    wait_sent,
)
from vakr.adapters.icmp import wire
from vakr.adapters.icmp.base import Tally
from vakr.adapters.icmp.book import ProbeBook
from vakr.adapters.icmp.transport import SocketIcmpProber
from vakr.adapters.icmp.wire import SocketKind
from vakr.ports.icmp import CollectorFault, IcmpCounters, ProbeOutcome, ProbeRequest, ProbeResult

V4 = ("192.0.2.1", 0)
RECVERR = (socket.IPPROTO_IP, socket.IP_RECVERR)


async def queue_cases(net: FakeNet) -> tuple[ProbeResult, IcmpCounters]:
    """Feed one probe a local error, the other a redirect, then a reply.

    Also break one error-queue read on the way.

    Args:
        net: Fake network.

    Returns:
        tuple[ProbeResult, IcmpCounters]: Result and counters.

    Raises:
        None
    """
    async with SocketIcmpProber(clock=manual_clock(), opener=net) as prober:
        task = asyncio.create_task(prober.probe(ProbeRequest("192.0.2.1", 2, 0.0, 5.0)))
        sock = await wait_sent(net.sockets, 2)
        sock.errqueue_errors.append(errno.EIO)
        sock.deliver(b"", V4)  # wakes the reader, whose error-queue read then fails
        await drain(sock)
        local = [(*RECVERR, ext_err(1, 0, 0, None, errno.EMSGSIZE))]
        redirect = [(*RECVERR, ext_err(2, 5, 1, "198.51.100.1"))]
        sock.deliver_error(sock.sent[0][0], local, V4)
        sock.deliver_error(sock.sent[1][0], redirect, V4)
        await drain(sock)
        sock.deliver(echo_reply(sock.sent[1][0]), V4)
        await drain(sock)
        return await task, prober.counters()


def test_local_errors_abort_and_non_failures_are_ignored() -> None:
    """A local-origin error is collector evidence; a redirect does not end the probe.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    result, counters = asyncio.run(queue_cases(FakeNet()))
    first, second = result.samples
    assert first.outcome is ProbeOutcome.ABORTED
    assert first.fault is CollectorFault.SEND_ERROR
    assert first.detail == "local error: Message too long"
    assert second.outcome is ProbeOutcome.REPLY
    assert result.fault is CollectorFault.SEND_ERROR
    assert counters.receive_errors == 1


async def stale_every_time(net: FakeNet) -> ProbeResult:
    """Fail every send attempt with a stale error, always with one queued.

    Args:
        net: Fake network.

    Returns:
        ProbeResult: The second target's result.

    Raises:
        None
    """
    async with SocketIcmpProber(clock=manual_clock(), opener=net) as prober:
        first = asyncio.create_task(prober.probe(ProbeRequest("192.0.2.1", 1, 0.0, 0.05)))
        sock = await wait_sent(net.sockets, 1)
        anc = [(*RECVERR, ext_err(2, 3, 1, "198.51.100.1"))]
        sock.requeue_on_send_error = (sock.sent[0][0], anc, V4)
        sock.send_errors.extend([errno.EHOSTUNREACH] * 3)
        result = await prober.probe(ProbeRequest("192.0.2.2", 1, 0.0, 0.05))
        await first
        return result


def test_retries_after_stale_errors_are_bounded() -> None:
    """At most two retries: a send that keeps failing is a local routing fault.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    result = asyncio.run(stale_every_time(FakeNet()))
    assert result.samples[0].outcome is ProbeOutcome.NOT_SENT
    assert result.fault is CollectorFault.LOCAL_NO_ROUTE


async def raw_unreachable(net: FakeNet) -> ProbeResult:
    """On the raw fallback, answer the probe with a router's ICMP error packet.

    Args:
        net: Fake network refusing datagram sockets.

    Returns:
        ProbeResult: The result.

    Raises:
        None
    """
    async with SocketIcmpProber(clock=manual_clock(), opener=net) as prober:
        task = asyncio.create_task(prober.probe(ProbeRequest("192.0.2.1", 1, 0.0, 5.0)))
        sock = await wait_sent(net.sockets, 1)
        request = sock.sent[0][0]
        outer = bytes([0x45]) + bytes(19)
        quoted = bytes([0x45]) + bytes(8) + bytes([1]) + bytes(6) + socket.inet_aton("192.0.2.1")
        icmp = bytes([3, 1, 0, 0, 0, 0, 0, 0]) + quoted + request[:8]
        sock.deliver(outer + icmp, ("198.51.100.1", 0))
        await drain(sock)
        return await task


def test_raw_fallback_reads_icmp_errors_as_packets() -> None:
    """ICMP-012 fallback: unreachables arrive as packets and resolve the right probe.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    result = asyncio.run(
        raw_unreachable(FakeNet({SocketKind.DATAGRAM: CollectorFault.PERMISSION_DENIED}))
    )
    sample = result.samples[0]
    assert sample.outcome is ProbeOutcome.UNREACHABLE
    assert sample.detail == "host unreachable (reported by 198.51.100.1)"


async def stray_timer() -> int:
    """Expire a sequence number that is not waiting.

    Args:
        None

    Returns:
        int: Timeouts counted.

    Raises:
        None
    """
    tally = Tally()
    book = ProbeBook(4, manual_clock(), tally, asyncio.get_running_loop())
    book.expire(7)
    book.abandon(asyncio.get_running_loop().create_future())
    return tally.timeouts


def test_a_timer_for_a_finished_probe_does_nothing() -> None:
    """A probe resolves exactly once, so a stray timer or abandon is a no-op.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert asyncio.run(stray_timer()) == 0
    assert wire.TOKEN_BYTES == 8
