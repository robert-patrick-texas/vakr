"""Tests for the ICMP prober over a fake socket layer: matching and timing (ICMP-T01, T06).

Replies are delivered by the test, in any order, after advancing a manual
clock, so every RTT is exact. Covers out-of-order replies, duplicates,
foreign tokens and sources, late replies after the timeout, ICMP
unreachable errors from the error queue, the kernel arrival stamp, IPv6,
and source binding.

Dependencies:
    asyncio, pytest: Event loop and assertions.
    tests.unit.adapters.icmp.icmp_fakes: The fake socket layer.
    vakr.adapters.icmp.transport: The prober under test.

Example:
    $ uv run pytest tests/unit/adapters/icmp/test_icmp_transport.py
"""

import asyncio
import secrets
import socket
import struct
import time

import pytest

from tests.unit.adapters.icmp.icmp_fakes import (
    START,
    FakeNet,
    FakeSocket,
    advance_us,
    drain,
    echo_reply,
    ext_err,
    manual_clock,
    wait_sent,
)
from vakr.adapters.icmp import wire
from vakr.adapters.icmp.transport import SocketIcmpProber
from vakr.ports.clock import ManualClock
from vakr.ports.icmp import IcmpCounters, ProbeOutcome, ProbeRequest, ProbeResult

V4 = ("192.0.2.1", 0)
RECVERR_V4 = (socket.IPPROTO_IP, socket.IP_RECVERR)


async def deliver_after(clock: ManualClock, sock: FakeSocket, index: int, rtt_us: int) -> None:
    """Advance the clock to ``rtt_us`` after the send and deliver probe ``index``'s reply.

    Args:
        clock: Manual clock; every probe in these tests is sent at the same instant.
        sock: The fake socket.
        index: Which sent probe to answer.
        rtt_us: Total microseconds since the send.

    Returns:
        None

    Raises:
        None
    """
    advance_us(clock, rtt_us - round((clock.monotonic() - 1000.0) * 1e6))
    request, address = sock.sent[index]
    sock.deliver(echo_reply(request), address)
    await drain(sock)


async def five_out_of_order(clock: ManualClock, net: FakeNet) -> ProbeResult:
    """Send five probes at once and answer them out of order.

    Args:
        clock: Manual clock.
        net: Fake network.

    Returns:
        ProbeResult: The prober's result.

    Raises:
        None
    """
    async with SocketIcmpProber(clock=clock, opener=net) as prober:
        task = asyncio.create_task(prober.probe(ProbeRequest("192.0.2.1", 5, 0.0, 5.0)))
        sock = await wait_sent(net.sockets, 5)
        for index, rtt in ((0, 1000), (4, 1050), (2, 1100), (1, 1200), (3, 1300)):
            await deliver_after(clock, sock, index, rtt)
        result = await task
        assert prober.counters().replies == 5
        return result


def test_icmp_t01_out_of_order_replies_keep_send_order_and_exact_rtts() -> None:
    """ICMP-T01, ICMP-003: per-probe RTTs are exact and stay in send order.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    clock, net = manual_clock(), FakeNet()
    result = asyncio.run(five_out_of_order(clock, net))
    assert result.raw_rtts_us == (1000, 1200, 1100, 1300, 1050)
    assert [s.sent_at for s in result.samples] == [START] * 5
    assert result.fault is None
    assert result.started_at == START
    assert round(result.duration_s, 9) == 0.0013
    assert net.sockets[0].closed


async def noisy_replies(clock: ManualClock, net: FakeNet) -> tuple[ProbeResult, IcmpCounters]:
    """Answer one probe, then send a duplicate and three foreign replies.

    Args:
        clock: Manual clock.
        net: Fake network.

    Returns:
        tuple[ProbeResult, IcmpCounters]: Result and counters.

    Raises:
        None
    """
    async with SocketIcmpProber(clock=clock, opener=net) as prober:
        task = asyncio.create_task(prober.probe(ProbeRequest("192.0.2.1", 2, 0.0, 0.2)))
        sock = await wait_sent(net.sockets, 2)
        await deliver_after(clock, sock, 0, 800)
        request = sock.sent[0][0]
        sock.deliver(echo_reply(request), V4)  # duplicate
        # Flip the token's last byte: writing a fixed 0xff left it unchanged whenever the
        # random token already ended in 0xff (1 run in 256), making this a duplicate.
        sock.deliver(echo_reply(request[:-1] + bytes([request[-1] ^ 0xFF])), V4)  # wrong token
        sock.deliver(echo_reply(sock.sent[1][0]), ("192.0.2.66", 0))  # wrong source
        sock.deliver(b"\x00\x00", V4)  # not even a header
        await drain(sock)
        result = await task
        return result, prober.counters()


def test_duplicates_and_foreign_replies_are_dropped_and_counted() -> None:
    """OBS-001: extra and forged replies are counted, never used as a measurement.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    result, counters = asyncio.run(noisy_replies(manual_clock(), FakeNet()))
    assert [s.outcome for s in result.samples] == [ProbeOutcome.REPLY, ProbeOutcome.TIMEOUT]
    assert result.raw_rtts_us == (800, -1)
    assert (counters.duplicate, counters.foreign, counters.timeouts) == (1, 3, 1)


@pytest.mark.parametrize("last", [b"\xff", b"\x00"])
def test_a_forged_token_is_foreign_whatever_the_token_ends_with(
    monkeypatch: pytest.MonkeyPatch, last: bytes
) -> None:
    """OBS-001: the forged reply differs from the real token even when it ends in 0xff.

    A fixed replacement byte left the token unchanged once in 256 runs, so the
    forged reply counted as a duplicate (a CI failure on 2026-10-27).

    Args:
        monkeypatch: Fixes the last byte of every probe token.
        last: The byte the tokens end with.

    Returns:
        None

    Raises:
        None
    """
    real = secrets.token_bytes
    monkeypatch.setattr(secrets, "token_bytes", lambda n: real(n - 1) + last)
    _result, counters = asyncio.run(noisy_replies(manual_clock(), FakeNet()))
    assert (counters.duplicate, counters.foreign, counters.timeouts) == (1, 3, 1)


async def late_after_deadline(clock: ManualClock, net: FakeNet) -> tuple[ProbeResult, IcmpCounters]:
    """Let every probe time out, then deliver all the replies.

    Args:
        clock: Manual clock.
        net: Fake network.

    Returns:
        tuple[ProbeResult, IcmpCounters]: Result and counters after the late replies.

    Raises:
        None
    """
    async with SocketIcmpProber(clock=clock, opener=net) as prober:
        result = await prober.probe(ProbeRequest("192.0.2.1", 5, 0.0, 0.02))
        sock = net.sockets[0]
        for request, address in sock.sent:
            sock.deliver(echo_reply(request), address)
        await drain(sock)
        return result, prober.counters()


def test_replies_after_the_timeout_are_late_not_success() -> None:
    """ICMP-004: a reply after its timeout leaves the probe lost (-1) and counts as late.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    result, counters = asyncio.run(late_after_deadline(manual_clock(), FakeNet()))
    assert result.raw_rtts_us == (-1,) * 5
    assert result.received == 0
    assert result.fault is None  # silence from the target is target evidence
    assert (counters.timeouts, counters.late, counters.in_flight) == (5, 5, 0)


async def slow_read(clock: ManualClock, net: FakeNet) -> ProbeResult:
    """Deliver a reply whose RTT on the clock exceeds the timeout.

    Args:
        clock: Manual clock.
        net: Fake network.

    Returns:
        ProbeResult: The result.

    Raises:
        None
    """
    async with SocketIcmpProber(clock=clock, opener=net) as prober:
        task = asyncio.create_task(prober.probe(ProbeRequest("192.0.2.1", 1, 0.0, 1.0)))
        sock = await wait_sent(net.sockets, 1)
        await deliver_after(clock, sock, 0, 1_500_000)
        return await task


def test_a_reply_older_than_the_timeout_never_becomes_an_rtt() -> None:
    """No stored RTT exceeds the per-probe timeout, even if the timer fired late.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    result = asyncio.run(slow_read(manual_clock(), FakeNet()))
    assert result.samples[0].outcome is ProbeOutcome.TIMEOUT


async def stamped(clock: ManualClock, net: FakeNet, unread_ns: list[int]) -> tuple[int, ...]:
    """Deliver replies carrying kernel arrival stamps ``unread_ns`` before now.

    Args:
        clock: Manual clock.
        net: Fake network.
        unread_ns: For each probe, how long ago the kernel "received" it.

    Returns:
        tuple[int, ...]: The RTTs.

    Raises:
        None
    """
    async with SocketIcmpProber(clock=clock, opener=net) as prober:
        request = ProbeRequest("192.0.2.1", len(unread_ns), 0.0, 5.0)
        task = asyncio.create_task(prober.probe(request))
        sock = await wait_sent(net.sockets, len(unread_ns))
        advance_us(clock, 100_000)
        for (packet, address), ago in zip(sock.sent, unread_ns, strict=True):
            stamp = struct.pack("=qq", *divmod(time.time_ns() - ago, 1_000_000_000))
            sock.deliver(echo_reply(packet), address, [(socket.SOL_SOCKET, 35, stamp)])
        await drain(sock)
        return (await task).raw_rtts_us


def test_kernel_arrival_stamp_removes_time_spent_unread() -> None:
    """A reply read late because the loop was busy keeps its true RTT (E.22).

    Implausible stamps (in the future, or older than the send) are ignored.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    ago = [40_000_000, -5_000_000_000, 3_600_000_000_000]
    corrected, future, ancient = asyncio.run(stamped(manual_clock(), FakeNet(), ago))
    assert 50_000 <= corrected <= 60_000
    assert future == ancient == 100_000


async def unreachable(clock: ManualClock, net: FakeNet) -> tuple[ProbeResult, IcmpCounters]:
    """Answer probe 0 with a router's "host unreachable"; send a stray error too.

    Args:
        clock: Manual clock.
        net: Fake network.

    Returns:
        tuple[ProbeResult, IcmpCounters]: Result and counters.

    Raises:
        None
    """
    async with SocketIcmpProber(clock=clock, opener=net) as prober:
        task = asyncio.create_task(prober.probe(ProbeRequest("192.0.2.1", 2, 0.0, 0.2)))
        sock = await wait_sent(net.sockets, 2)
        anc = [(*RECVERR_V4, ext_err(2, 3, 1, "198.51.100.1"))]
        sock.deliver_error(sock.sent[0][0], anc, V4)
        sock.deliver_error(
            wire.echo_request(wire.SocketKind.DATAGRAM, 4, 0, 999, b"x" * 8), anc, V4
        )
        await drain(sock)
        await deliver_after(clock, sock, 1, 700)
        return await task, prober.counters()


def test_icmp_unreachable_resolves_the_probe_as_lost_with_the_reason() -> None:
    """IP_RECVERR: a router's unreachable is target-path evidence, with who said it.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    result, counters = asyncio.run(unreachable(manual_clock(), FakeNet()))
    first = result.samples[0]
    assert first.outcome is ProbeOutcome.UNREACHABLE
    assert first.rtt_us == -1
    assert first.detail == "host unreachable (reported by 198.51.100.1)"
    assert result.raw_rtts_us == (-1, 700)
    assert result.fault is None
    assert (counters.unreachable, counters.late) == (1, 1)


async def both_families(net: FakeNet) -> list[ProbeResult]:
    """Probe an IPv4 and an IPv6 target, one with a pinned source.

    Args:
        net: Fake network that answers every probe at once.

    Returns:
        list[ProbeResult]: Results for both.

    Raises:
        None
    """
    async with SocketIcmpProber(opener=net) as prober:
        return list(
            await asyncio.gather(
                prober.probe(ProbeRequest("192.0.2.1", 3, 0.0, 5.0)),
                prober.probe(ProbeRequest("2001:db8::1", 3, 0.001, 5.0, "2001:db8::10")),
            )
        )


def test_icmp_t06_ipv4_and_ipv6_share_nothing_but_the_rules() -> None:
    """ICMP-T06, ICMP-011: each family gets its socket; the source is bound and recorded.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    net = FakeNet()
    net.auto_reply = True
    v4, v6 = asyncio.run(both_families(net))
    assert [s.outcome for s in v4.samples + v6.samples] == [ProbeOutcome.REPLY] * 6
    assert (v4.request.family.value, v6.request.family.value) == ("ipv4", "ipv6")
    assert [(s.version, s.source) for s in net.sockets] == [(4, None), (6, "2001:db8::10")]
    assert net.sockets[1].sent[0][1] == ("2001:db8::1", 0, 0, 0)
    assert net.sockets[1].sent[0][0][0] == 128
