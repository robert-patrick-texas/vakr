"""Turn one bucket's native evidence into rollup buckets (RET-004, RET-006, RET-008, E.10).

For each interface and collector, ``interface_buckets`` writes all six
interface metrics; for each device and collector with ICMP cycles,
``icmp_buckets`` writes RTT, loss, and jitter. The arithmetic lives in
``vakr.logic.rollup``; this module decides which observations feed which
metric (Appendix E.10):

- **Coverage.** ``sample_count`` counts observations (one per instant: two
  at the same instant keep the larger UUID, as series do), ``valid_count``
  those with a value, and ``expected_count`` the poll slots the device's
  schedule intended in the bucket (``expected_count``). A thin bucket stays
  visibly thin; a missing value is never zero (ROLL-T05).
- **Quality** is the union of the members' flags. ICMP buckets also carry
  ``BASELINE_UPTIME_RESET`` when SNMP saw the agent restart in the bucket,
  so a restart survives native retention (RET-006).
- **Loss** comes from summed probe counts (RET-008). Cycles flagged
  ``NOT_ATTEMPTED`` or ``COLLECTOR_DEGRADED`` are counted as samples but
  give no loss value, because collector trouble is never target loss
  (invariant 2).
- **RTT** is summarised over every individual reply; its central value
  (the bucket's ``mean`` field) is the median reply.

Dependencies:
    vakr.logic.rollup: Summaries and schedule slots.
    vakr.domain, vakr.ports.telemetry.rollup_source: Buckets and read models.

Safety:
    Pure: no I/O, no clock.

Example:
    >>> from datetime import UTC, datetime
    >>> expected_count(None, datetime(2026, 10, 1, tzinfo=UTC), 900, 60)
    15
"""

from collections.abc import Callable, Sequence
from datetime import datetime
from functools import reduce
from operator import or_
from uuid import UUID

from vakr.domain.ids import CollectorId
from vakr.domain.observations import IcmpObservation
from vakr.domain.quality import Quality
from vakr.domain.rollups import RollupBucket
from vakr.domain.series import Metric
from vakr.domain.timestamps import to_epoch_us
from vakr.logic.rollup import (
    Summary,
    loss_summary,
    reply_summary,
    schedule_slots,
    summarize,
    whole_summary,
)
from vakr.ports.telemetry.rollup_source import (
    INTERFACE_METRICS,
    DeviceEvidence,
    InterfaceSamples,
    PollSlot,
)

UNTRUSTED_LOSS = Quality.NOT_ATTEMPTED | Quality.COLLECTOR_DEGRADED
"""Cycles with these flags give no loss value (invariant 2)."""

_US = 1_000_000


def expected_count(
    schedule: PollSlot | None, start: datetime, seconds: int, fallback_s: int
) -> int:
    """Return how many samples the device's schedule intended in the bucket (E.10).

    Args:
        schedule: The device's latest poll in the schedule lookback, if any.
        start: Bucket start (UTC).
        seconds: Bucket width.
        fallback_s: ``polling.default_interval`` in seconds, used without a schedule.

    Returns:
        int: Slots of ``schedule`` in the bucket, or ``ceil(seconds / fallback_s)``.

    Raises:
        ValueError: If an interval is not positive.
    """
    if schedule is None:
        if fallback_s <= 0:
            raise ValueError("fallback_s must be positive")
        return -(-seconds // fallback_s)
    lo = to_epoch_us(start)
    return schedule_slots(lo, lo + seconds * _US, to_epoch_us(schedule.scheduled_at),
                          schedule.interval_s * _US)  # fmt: skip


def one_per_instant[T](items: Sequence[T], at: Callable[[T], datetime]) -> list[T]:
    """Keep the last item of each instant from items ordered by ``(time, UUID)`` (DB-004).

    Args:
        items: Items in ``(timestamp, observation_id)`` order.
        at: An item's timestamp.

    Returns:
        list[T]: One item per instant, the one with the larger UUID.

    Raises:
        None
    """
    kept: list[T] = []
    for item in items:
        if kept and at(kept[-1]) == at(item):
            kept[-1] = item
        else:
            kept.append(item)
    return kept


def _bucket(
    subject: UUID, collector: CollectorId, metric: Metric, span: tuple[datetime, int],
    counts: tuple[int, int], summary: Summary | None, quality: Quality,
) -> RollupBucket:  # fmt: skip
    """Assemble one metric's bucket.

    Args:
        subject: Interface or device.
        collector: The collector.
        metric: The metric.
        span: ``(bucket start, bucket seconds)``.
        counts: ``(expected, sample)`` counts.
        summary: The metric's aggregates, or ``None`` without a valid value.
        quality: Union of the members' flags.

    Returns:
        RollupBucket: The bucket.

    Raises:
        ValidationError: If the values break a bucket rule.
    """
    if summary is None:
        return RollupBucket(subject, collector, metric, span[0], span[1], counts[0], counts[1], 0,
                            quality=quality)  # fmt: skip
    return RollupBucket(subject, collector, metric, span[0], span[1], counts[0], counts[1],
                        summary.valid, summary.minimum, summary.mean, summary.p95,
                        summary.maximum, quality)  # fmt: skip


def interface_buckets(series: InterfaceSamples, span: tuple[datetime, int],
                      expected: int) -> list[RollupBucket]:  # fmt: skip
    """Roll up one interface's samples from one collector: every interface metric (RET-004).

    Args:
        series: The samples in the bucket.
        span: ``(bucket start, bucket seconds)``.
        expected: Samples the schedule intended.

    Returns:
        list[RollupBucket]: One bucket per ``INTERFACE_METRICS`` entry, or
        none when there is no sample.

    Raises:
        ValidationError: If the values break a bucket rule.
    """
    samples = one_per_instant(series.samples, lambda s: s.timestamp)
    if not samples:
        return []
    quality = reduce(or_, (s.quality for s in samples), Quality.NONE)
    counts = (expected, len(samples))
    return [
        _bucket(series.interface_id, series.collector_id, metric, span, counts,
                summarize([v for s in samples if (v := s.values[i]) is not None]), quality)
        for i, metric in enumerate(INTERFACE_METRICS)
    ]  # fmt: skip


def icmp_buckets(device: DeviceEvidence, span: tuple[datetime, int],
                 expected: int) -> list[RollupBucket]:  # fmt: skip
    """Roll up one device's ICMP cycles from one collector: RTT, loss, jitter (RET-006, RET-008).

    Args:
        device: The device's evidence in the bucket.
        span: ``(bucket start, bucket seconds)``.
        expected: Cycles the schedule intended.

    Returns:
        list[RollupBucket]: RTT, loss, and jitter buckets, or none without a cycle.

    Raises:
        ValidationError: If the values break a bucket rule.
    """
    cycles: list[IcmpObservation] = one_per_instant(device.cycles, lambda c: c.timestamp)
    if not cycles:
        return []
    quality = reduce(or_, (c.quality for c in cycles), Quality.NONE)
    if device.restarted:
        quality |= Quality.BASELINE_UPTIME_RESET
    replies = [r for c in cycles for r in c.rtts_us if r is not None]
    answered = sum(1 for c in cycles if c.received > 0)
    trusted = [(c.sent, c.received) for c in cycles if not c.quality & UNTRUSTED_LOSS]
    jitter = [c.jitter_us for c in cycles if c.jitter_us is not None]
    counts, who = (expected, len(cycles)), (device.device_id, device.collector_id)
    return [
        _bucket(
            *who, Metric.RTT_MEDIAN_US, span, counts, reply_summary(replies, answered), quality
        ),
        _bucket(*who, Metric.LOSS_PCT, span, counts, loss_summary(trusted), quality),
        _bucket(*who, Metric.JITTER_US, span, counts, whole_summary(jitter), quality),
    ]


def is_thin(buckets: Sequence[RollupBucket]) -> bool:
    """Tell whether a subject's buckets hold fewer samples than intended (RET-009).

    Args:
        buckets: One subject's buckets for one bucket start.

    Returns:
        bool: ``True`` when the first bucket's sample count is below its expected count.

    Raises:
        None
    """
    return bool(buckets) and buckets[0].sample_count < buckets[0].expected_count
