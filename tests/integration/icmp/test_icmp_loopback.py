"""Real unprivileged ICMP against 127.0.0.1 and ::1 (ICMP-T06, ICMP-012, Appendix E.9).

These tests open Linux ping sockets, so they need ``net.ipv4.ping_group_range``
to include one of the test process's groups. When it does not, or when IPv6
is disabled, they skip and say why. They never send to anything but
loopback. The timing test checks the E.9 budget: 100 targets with five
probes 200 ms apart finish within the 2 s cycle.

Dependencies:
    asyncio, select, socket, time: Real sockets and wall-clock timing.
    vakr.adapters.icmp, vakr.collectors.icmp, vakr.cli: Code under test.

Example:
    $ uv run pytest tests/integration/icmp
"""

import asyncio
import os
import select
import socket
import time

import pytest

from vakr.adapters.icmp import wire
from vakr.adapters.icmp.base import SocketOpenError
from vakr.adapters.icmp.sockets import open_socket, receive_drops
from vakr.adapters.icmp.transport import SocketIcmpProber
from vakr.cli.main import main
from vakr.collectors.icmp.cycle import PollContext, build_cycle
from vakr.domain.enums import AddressFamily, IcmpState
from vakr.domain.ids import CollectorId, DeviceId, PollId, new_id
from vakr.domain.quality import Quality
from vakr.ports.icmp import (
    CollectorFault,
    IcmpCounters,
    ProbeOutcome,
    ProbeRequest,
    ProbeResult,
)


def _unavailable(version: int) -> str | None:
    """Explain why unprivileged ICMP cannot run here, if it cannot.

    Args:
        version: IP version to test.

    Returns:
        str | None: The skip reason, or ``None`` when it works.

    Raises:
        None
    """
    try:
        open_socket(version, wire.SocketKind.DATAGRAM, None).close()
    except SocketOpenError as exc:
        groups = sorted({os.getegid(), *os.getgroups()})
        return (
            f"unprivileged IPv{version} ICMP unavailable to this test process ({exc.fault.value}: "
            f"{exc.reason}); net.ipv4.ping_group_range must include one of GIDs {groups}"
        )
    return None


V4_SKIP = _unavailable(4)
V6_SKIP = V4_SKIP or _unavailable(6)
needs_v4 = pytest.mark.skipif(V4_SKIP is not None, reason=V4_SKIP or "")
needs_v6 = pytest.mark.skipif(V6_SKIP is not None, reason=V6_SKIP or "")


async def probe_all(requests: list[ProbeRequest]) -> tuple[list[ProbeResult], IcmpCounters, float]:
    """Run cycles concurrently on one prober and time them.

    Args:
        requests: Cycles to run.

    Returns:
        tuple[list[ProbeResult], IcmpCounters, float]: Results, counters, seconds.

    Raises:
        None
    """
    async with SocketIcmpProber() as prober:
        start = time.monotonic()
        results = await asyncio.gather(*(prober.probe(r) for r in requests))
        return list(results), prober.counters(), time.monotonic() - start


def context() -> PollContext:
    """Return a poll context with fresh IDs.

    Args:
        None

    Returns:
        PollContext: The context.

    Raises:
        None
    """
    return PollContext(new_id(PollId), new_id(CollectorId), new_id(DeviceId))


@needs_v6
@pytest.mark.parametrize(
    ("target", "family"), [("127.0.0.1", AddressFamily.IPV4), ("::1", AddressFamily.IPV6)]
)
def test_icmp_t06_loopback_collects_with_source_and_target_context(
    target: str, family: AddressFamily
) -> None:
    """ICMP-T06: both families collect five replies, with source and target recorded.

    Args:
        target: Loopback address.
        family: Its family.

    Returns:
        None

    Raises:
        None
    """
    request = ProbeRequest(target, 5, 0.01, 1.0, target)
    (result,), _, _ = asyncio.run(probe_all([request]))
    cycle = build_cycle(result, context())
    obs = cycle.observation
    assert obs is not None
    assert (obs.state, obs.quality) == (IcmpState.REACHABLE, Quality.NONE)
    assert (obs.address_family, obs.target_address, obs.source_address) == (family, target, target)
    assert all(0 <= rtt < 1_000_000 for rtt in result.raw_rtts_us)
    stats = [v for v in (obs.min_rtt_us, obs.median_rtt_us, obs.max_rtt_us) if v is not None]
    assert len(stats) == 3
    assert sorted(stats) == stats


@needs_v4
def test_e9_hundred_targets_five_probes_fit_the_two_second_cycle() -> None:
    """E.9: 100 targets x 5 probes at 200 ms spacing complete within about 2 s.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    requests = [ProbeRequest(f"127.0.0.{i}", 5, 0.2, 1.0) for i in range(1, 101)]
    results, counters, elapsed = asyncio.run(probe_all(requests))
    assert elapsed < 2.0, f"cycle took {elapsed:.3f} s"
    assert sum(r.received for r in results) == 500
    assert all(r.fault is None for r in results)
    assert (counters.sent, counters.replies, counters.sockets_open) == (500, 500, 1)
    assert counters.in_flight_peak <= 1024


@needs_v4
def test_kernel_arrival_stamps_and_drop_counter_work_on_this_kernel() -> None:
    """The ``SO_TIMESTAMPNS`` and ``SO_MEMINFO`` numbers vakr hard-codes are right here.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    sock = open_socket(4, wire.SocketKind.DATAGRAM, None)
    try:
        assert receive_drops(sock) == 0
        sock.sendto(
            wire.echo_request(wire.SocketKind.DATAGRAM, 4, 0, 1, b"12345678"), ("127.0.0.1", 0)
        )
        readable, _, _ = select.select([sock], [], [], 2.0)
        assert readable
        data, anc, _, _ = sock.recvmsg(2048, wire.TIMESTAMP_ANC_BYTES)
        stamp = wire.arrival_ns(anc)
        assert wire.parse_echo(data, 4) is not None
        assert stamp is not None
        assert abs(time.time_ns() - stamp) < 2_000_000_000
    finally:
        sock.close()


@needs_v4
def test_a_source_not_on_this_host_is_a_collector_fault() -> None:
    """9.11: a pinned source that is not configured here is not the device's fault.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    (result,), _, _ = asyncio.run(probe_all([ProbeRequest("127.0.0.1", 2, 0.0, 0.5, "192.0.2.77")]))
    assert result.fault is CollectorFault.SOURCE_UNAVAILABLE
    assert {s.outcome for s in result.samples} == {ProbeOutcome.NOT_SENT}
    assert build_cycle(result, context()).summary.state is IcmpState.UNKNOWN


@needs_v4
def test_probe_command_end_to_end(capsys: pytest.CaptureFixture[str]) -> None:
    """``vakr icmp probe 127.0.0.1`` reports reachable and exits 0.

    Args:
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    assert main(["icmp", "probe", "127.0.0.1", "--count", "2", "--interval-ms", "10"]) == 0
    out = capsys.readouterr().out
    assert "state: reachable: 2 of 2 replies, 0.0% loss" in out
    assert socket.gethostname()  # the host stays usable after the run
