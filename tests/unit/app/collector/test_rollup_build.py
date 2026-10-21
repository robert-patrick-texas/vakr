"""Building rollup buckets from one bucket's evidence (ROLL-T01, ROLL-T02, ROLL-T05, RET-004..008).

Dependencies:
    tests.contract.contract_samples; vakr.app.collector.rollup_build.

Example:
    $ uv run pytest tests/unit/app/collector/test_rollup_build.py
"""

from datetime import timedelta
from uuid import UUID

from tests.contract import contract_samples as s
from vakr.app.collector.rollup_build import (
    expected_count,
    icmp_buckets,
    interface_buckets,
    is_thin,
    one_per_instant,
)
from vakr.domain.ids import CollectorId, DeviceId, InterfaceId, ObservationId, new_id
from vakr.domain.observations import IcmpObservation
from vakr.domain.quality import Quality
from vakr.domain.rollups import RollupBucket
from vakr.domain.series import Metric
from vakr.ports.telemetry.rollup_source import (
    INTERFACE_METRICS,
    DeviceEvidence,
    InterfaceSample,
    InterfaceSamples,
    PollSlot,
)

SPAN = (s.T0, 900)
SECOND = timedelta(seconds=1)


def sample(at_s: int, in_bps: float | None, quality: Quality = Quality.NONE) -> InterfaceSample:
    """Build a rollup sample with ``in_bps`` and fixed other metrics.

    Args:
        at_s: Seconds after ``T0``.
        in_bps: Inbound rate, or ``None``.
        quality: Flags.

    Returns:
        InterfaceSample: The sample.

    Raises:
        None
    """
    values = (in_bps, 10.0, None, 2.0, 0.0, 0.0)
    return InterfaceSample(s.T0 + at_s * SECOND, new_id(ObservationId), values, quality)


def series(samples: list[InterfaceSample]) -> InterfaceSamples:
    """Wrap samples as one interface's evidence.

    Args:
        samples: Ordered samples.

    Returns:
        InterfaceSamples: The evidence.

    Raises:
        None
    """
    return InterfaceSamples(new_id(InterfaceId), new_id(CollectorId), new_id(DeviceId),
                            tuple(samples))  # fmt: skip


def by_metric(buckets: list[RollupBucket]) -> dict[Metric, RollupBucket]:
    """Index buckets by metric.

    Args:
        buckets: Buckets of one subject.

    Returns:
        dict[Metric, RollupBucket]: Metric to bucket.

    Raises:
        None
    """
    return {b.metric: b for b in buckets}


def test_roll_t01_interface_max_p95_and_coverage_counts_are_exact() -> None:
    """ROLL-T01, RET-004: 30-second polling, one spike, one missing rate, two lost polls.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    rates = [1_000.0 * (k + 1) for k in range(28)]
    rates[5] = 9_000_000.0  # the spike a 400-day graph must still show
    samples = [sample(30 * k, r) for k, r in enumerate(rates)]
    samples[7] = sample(30 * 7, None, Quality.BASELINE_GAP)  # sampled, but no rate
    buckets = by_metric(interface_buckets(series(samples), SPAN, expected=30))
    assert list(buckets) == list(INTERFACE_METRICS)
    inbound = buckets[Metric.IN_BPS]
    valid = [r for k, r in enumerate(rates) if k != 7]
    assert (inbound.expected_count, inbound.sample_count, inbound.valid_count) == (30, 28, 27)
    assert (inbound.minimum, inbound.maximum) == (1_000.0, 9_000_000.0)
    assert inbound.p95 == sorted(valid)[25]  # nearest rank: ceil(0.95 * 27) = 26th value
    assert inbound.mean == sum(valid) / 27
    assert inbound.quality is Quality.BASELINE_GAP
    assert buckets[Metric.IN_PPS].valid_count == 0
    assert buckets[Metric.IN_PPS].maximum is None  # a missing metric is a gap, never zero
    assert buckets[Metric.IN_ERRORS].maximum == 0.0  # a measured zero stays zero


def test_roll_t05_thin_and_empty_coverage_stay_visible() -> None:
    """ROLL-T05, STORE-012: 10 of 15 samples is thin; no sample means no bucket at all.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    made = interface_buckets(series([sample(60 * k, 5.0) for k in range(10)]), SPAN, 15)
    assert {(b.expected_count, b.sample_count, b.valid_count) for b in made} >= {(15, 10, 10)}
    assert is_thin(made)
    assert not is_thin(interface_buckets(series([sample(0, 1.0)]), SPAN, 1))
    assert interface_buckets(series([]), SPAN, 15) == []
    assert not is_thin([])


def test_db004_two_samples_at_one_instant_count_once_and_the_larger_uuid_wins() -> None:
    """DB-004: rollups summarise the points a native series shows.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    low, high = sorted([new_id(ObservationId), new_id(ObservationId)])
    values = (1.0, None, None, None, None, None)
    first = InterfaceSample(s.T0, low, values, Quality.NONE)
    second = InterfaceSample(s.T0, high, (2.0, *values[1:]), Quality.RETRIED)
    kept = one_per_instant([first, second], lambda x: x.timestamp)
    assert kept == [second]
    inbound = interface_buckets(series([first, second]), SPAN, 15)[0]
    assert (inbound.sample_count, inbound.maximum, inbound.quality) == (1, 2.0, Quality.RETRIED)


def evidence(cycles: list[IcmpObservation], *, restarted: bool = False) -> DeviceEvidence:
    """Wrap ICMP cycles as one device's evidence.

    Args:
        cycles: ICMP observations in time order.
        restarted: SNMP saw an agent restart.

    Returns:
        DeviceEvidence: The evidence.

    Raises:
        None
    """
    device = cycles[0].device_id if cycles else new_id(DeviceId)
    collector = cycles[0].collector_id if cycles else new_id(CollectorId)
    return DeviceEvidence(device, collector, tuple(cycles), None, restarted)


def cycle(
    dev: UUID, col: UUID, at_s: int, rtts: tuple[int | None, ...], quality: Quality = Quality.NONE
) -> IcmpObservation:
    """Build an ICMP cycle whose summaries match its RTTs.

    Args:
        dev: Device.
        col: Collector.
        at_s: Seconds after ``T0``.
        rtts: Per-probe RTTs, ``None`` for no reply.
        quality: Flags.

    Returns:
        IcmpObservation: The cycle.

    Raises:
        None
    """
    got = sorted(r for r in rtts if r is not None)
    summary = {"min_rtt_us": None, "avg_rtt_us": None, "median_rtt_us": None,
               "max_rtt_us": None, "jitter_us": None}  # fmt: skip
    if got:
        summary = {"min_rtt_us": got[0], "avg_rtt_us": got[0], "median_rtt_us": got[0],
                   "max_rtt_us": got[-1], "jitter_us": got[-1] - got[0]}  # fmt: skip
    return s.icmp(dev, col, s.T0 + at_s * SECOND, rtts_us=rtts, quality=quality, **summary)


def test_roll_t02_icmp_loss_from_summed_counts_and_rtt_from_every_reply() -> None:
    """ROLL-T02, RET-006, RET-008: per-cycle averages never feed the bucket.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    dev, col = new_id(DeviceId), new_id(CollectorId)
    cycles = [
        cycle(dev, col, 0, (None, None, None, None, None)),  # outage: 5 of 5 lost
        cycle(dev, col, 60, (1_000, 3_000)),  # 2 probes after a config change, none lost
        cycle(dev, col, 120, (2_000, 2_000, 9_000, 2_000, 2_000)),
    ]
    buckets = by_metric(icmp_buckets(evidence(cycles), SPAN, expected=15))
    loss = buckets[Metric.LOSS_PCT]
    assert (loss.sample_count, loss.valid_count) == (3, 3)
    assert (loss.minimum, loss.mean, loss.maximum) == (0.0, 41.7, 100.0)  # 5 of 12, not 33.3
    rtt = buckets[Metric.RTT_MEDIAN_US]
    assert (rtt.valid_count, rtt.minimum, rtt.mean, rtt.p95, rtt.maximum) == (
        2, 1_000.0, 2_000.0, 9_000.0, 9_000.0)  # fmt: skip
    jitter = buckets[Metric.JITTER_US]
    assert (jitter.valid_count, jitter.minimum, jitter.maximum) == (2, 2_000.0, 7_000.0)


def test_collector_trouble_never_becomes_target_loss() -> None:
    """Invariant 2: degraded cycles count as samples but give no loss value.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    dev, col = new_id(DeviceId), new_id(CollectorId)
    flag = Quality.COLLECTOR_DEGRADED
    cycles = [cycle(dev, col, 60 * k, (None,) * 5, flag) for k in range(3)]
    loss = by_metric(icmp_buckets(evidence(cycles), SPAN, 15))[Metric.LOSS_PCT]
    assert (loss.sample_count, loss.valid_count, loss.mean) == (3, 0, None)
    assert flag in loss.quality
    assert icmp_buckets(evidence([]), SPAN, 15) == []


def test_ret006_an_agent_restart_survives_in_the_icmp_bucket() -> None:
    """RET-006: SNMP restart evidence is kept as a quality flag on the device's buckets.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    dev, col = new_id(DeviceId), new_id(CollectorId)
    made = icmp_buckets(evidence([cycle(dev, col, 0, (900,))], restarted=True), SPAN, 15)
    assert all(Quality.BASELINE_UPTIME_RESET in b.quality for b in made)


def test_expected_count_follows_the_device_schedule_or_the_default_interval() -> None:
    """E.10: slots of the latest poll's schedule; ``polling.default_interval`` without one.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert expected_count(None, s.T0, 900, 60) == 15
    assert expected_count(None, s.T0, 900, 120) == 8
    assert expected_count(PollSlot(s.T0 - 30 * SECOND, 30), s.T0, 900, 60) == 30
    hourly = PollSlot(s.T0 - 15 * 60 * SECOND, 3600)
    assert expected_count(hourly, s.T0, 900, 60) == 0
    assert expected_count(hourly, s.T0 + 2700 * SECOND, 900, 60) == 1
