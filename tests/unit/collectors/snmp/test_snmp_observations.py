"""10.2, 10.3, UPTIME-002, RATE-004: SNMP observation builders.

The builders turn poll results and ``vakr.logic`` rate results into
canonical observations. A collector failure becomes ``UNKNOWN`` with
``COLLECTOR_DEGRADED`` (invariant 2), a skipped poll records no effort, and
an interface observation carries rates only when the baseline is valid,
with per-metric gaps kept off the sample-wide flags (E.17 #11).

Dependencies:
    vakr.collectors.snmp, vakr.logic: Builders and rate derivation.

Example:
    $ uv run pytest tests/unit/collectors/snmp/test_snmp_observations.py
"""

from datetime import UTC, datetime, timedelta
from typing import TypedDict

import pytest

from vakr.collectors.snmp import (
    InterfaceSample,
    UptimeReading,
    build_interface_observation,
    build_snmp_device_observation,
    skipped_snmp_observation,
    uptime_continuity,
)
from vakr.collectors.snmp.poll import SnmpPollResult
from vakr.collectors.snmp.stage1 import Stage1Result
from vakr.collectors.snmp.stage2 import Stage2Result
from vakr.domain.enums import (
    BaselineState,
    CounterFamily,
    SnmpState,
    SnmpVersion,
    UptimeContinuity,
)
from vakr.domain.ids import CollectorId, DeviceId, InterfaceId, PollId, new_id
from vakr.domain.quality import Quality
from vakr.logic.baseline import RateContext
from vakr.logic.rates import compute_rates
from vakr.ports.snmp import SnmpError, SnmpErrorKind, SnmpStats

AT = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


class Ids(TypedDict):
    """The IDs every builder takes.

    Attributes:
        poll_id: The poll.
        collector_id: The collector.
        device_id: The device.
    """

    poll_id: PollId
    collector_id: CollectorId
    device_id: DeviceId


IDS = Ids(poll_id=new_id(PollId), collector_id=new_id(CollectorId), device_id=new_id(DeviceId))


def _poll(
    responses: int, error: SnmpError | None = None, uptime: int | None = 5_000
) -> SnmpPollResult:
    """Build a poll result.

    Args:
        responses: Responses received.
        error: Stage 1's error.
        uptime: ``sysUpTime`` read.

    Returns:
        SnmpPollResult: The result.

    Raises:
        None
    """
    stats = SnmpStats(requests=2, responses=responses, timeouts=2 - responses, retries=1)
    stage1 = Stage1Result(AT, 0.0, uptime if responses else None, (), stats, error)
    stage2 = Stage2Result(()) if responses else None
    quality = stats.quality
    return SnmpPollResult("sim", SnmpVersion.V2C, AT, 0.0, 0.25, stage1, stage2, stats, quality)


def test_device_observation_from_an_answered_poll() -> None:
    """10.2: effort, uptime, version, and continuity reach the observation.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    obs = build_snmp_device_observation(_poll(1), continuity=UptimeContinuity.CONTINUOUS, **IDS)
    assert obs.state is SnmpState.RESPONDING
    assert (obs.request_count, obs.response_count, obs.retry_count) == (2, 1, 1)
    assert (obs.elapsed_ms, obs.sys_uptime_ticks, obs.error_code) == (250, 5_000, None)
    assert obs.quality is Quality.RETRIED


def test_collector_failure_is_unknown_never_down() -> None:
    """Invariant 2: a transport failure is UNKNOWN with COLLECTOR_DEGRADED, not a timeout.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    failed = _poll(0, SnmpError(SnmpErrorKind.TRANSPORT, "socket"))
    obs = build_snmp_device_observation(failed, continuity=UptimeContinuity.UNKNOWN, **IDS)
    assert obs.state is SnmpState.UNKNOWN
    assert Quality.COLLECTOR_DEGRADED in obs.quality
    assert obs.error_code == "transport"
    silent = _poll(0, SnmpError(SnmpErrorKind.TIMEOUT, "no answer"))
    timed_out = build_snmp_device_observation(silent, continuity=UptimeContinuity.UNKNOWN, **IDS)
    assert timed_out.state is SnmpState.TIMEOUT
    assert timed_out.error_code == "timeout"


def test_skipped_snmp_records_no_effort() -> None:
    """9.2: SNMP skipped by icmp_gated is SKIPPED with zero counts.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    obs = skipped_snmp_observation(timestamp=AT, **IDS)
    assert obs.state is SnmpState.SKIPPED
    assert (obs.request_count, obs.elapsed_ms) == (0, 0)


@pytest.mark.parametrize(
    ("prev", "curr", "seconds", "expected"),
    [
        (100_000, 106_000, 60, UptimeContinuity.CONTINUOUS),
        (100_000, 300, 60, UptimeContinuity.RESET_DETECTED),
        (100_000, 900_000, 60, UptimeContinuity.UNKNOWN),
    ],
)
def test_uptime_continuity_uses_wall_clock_gap(
    prev: int, curr: int, seconds: int, expected: UptimeContinuity
) -> None:
    """UPTIME-002, E.5: continuity, agent restart, and uptime ahead of the clock.

    Args:
        prev: Earlier ticks.
        curr: Later ticks.
        seconds: Wall-clock gap.
        expected: The verdict.

    Returns:
        None

    Raises:
        None
    """
    later = AT + timedelta(seconds=seconds)
    continuity, _, evidence = uptime_continuity(UptimeReading(prev, AT), UptimeReading(curr, later))
    assert continuity is expected
    assert evidence
    assert uptime_continuity(None, UptimeReading(curr, later))[0] is UptimeContinuity.UNKNOWN


def _sample(octets: int, at: float, missing_out: bool = False) -> InterfaceSample:
    """Build an HC interface sample.

    Args:
        octets: Inbound octets; other counters derive from it.
        at: Monotonic time.
        missing_out: Leave outbound octets missing.

    Returns:
        InterfaceSample: The sample.

    Raises:
        None
    """
    return InterfaceSample(
        3, new_id(InterfaceId), AT + timedelta(seconds=at), at, CounterFamily.HC64, None, None,
        in_octets=octets, out_octets=None if missing_out else octets, in_packets=octets // 100,
        out_packets=octets // 100, in_errors=1, out_errors=0,
        missing=("out_octets",) if missing_out else (),
        quality=Quality.MISSING_FIELDS if missing_out else Quality.NONE,
    )  # fmt: skip


def test_interface_observation_carries_rates_only_with_a_valid_baseline() -> None:
    """RATE-004, E.17 #11: first sample has no rates; a missing metric blanks only itself.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    first, second = _sample(1_000, 0.0), _sample(751_000, 60.0, missing_out=True)
    ctx = RateContext(elapsed_s=None, interval_s=60, continuity=UptimeContinuity.CONTINUOUS)
    none = compute_rates(None, first.counter_sample(), ctx)
    obs = build_interface_observation(first, none, **IDS)
    assert obs.baseline_state is BaselineState.NO_BASELINE
    assert obs.in_bps is None
    assert obs.interface_id == first.interface_id
    ctx = RateContext(elapsed_s=60.0, interval_s=60, continuity=UptimeContinuity.CONTINUOUS)
    rates = compute_rates(first.counter_sample(), second.counter_sample(), ctx)
    obs = build_interface_observation(second, rates, elapsed_s=60.0, **IDS)
    assert obs.baseline_state is BaselineState.VALID
    assert (obs.in_bps, obs.out_bps, obs.sample_elapsed_us) == (100_000.0, None, 60_000_000)
    assert Quality.MISSING_FIELDS in obs.quality
    assert Quality.BASELINE_MISSING_PRIOR not in obs.quality
    with pytest.raises(ValueError, match="persistent interface UUID"):
        build_interface_observation(
            InterfaceSample(3, None, AT, 0.0, CounterFamily.HC64, None, None), none, **IDS
        )
