"""Derive rates and utilization from a validated counter baseline (9.7, RATE-001..010).

``vakr.logic.baseline`` decides whether two samples can be subtracted; this
module does the arithmetic and assembles the result. When the baseline is
refused, the raw sample is kept, every rate is ``None`` with the reasons in
quality flags, and the sample becomes the next baseline (RATE-004): a reset
produces a gap, never a spike. Missing counters affect only their own metric.

Rates use the actual elapsed time, never the configured interval (RATE-001):
``delta_octets * 8 / elapsed`` for bits per second and ``delta / elapsed``
for packets and errors (RATE-006, RATE-007). Utilization divides by the
RATE-008 denominator and names it. A rate above 1.10 x that capacity is kept
and flagged ``RATE_EXCEEDS_CAPACITY`` (RATE-009); with no capacity, rates
are absolute and utilization is unavailable (RATE-010).

Dependencies:
    dataclasses: Immutable results.
    vakr.logic.baseline, vakr.logic.capacity, vakr.logic.counters: Checks,
        denominators, and samples.
    vakr.domain.enums, vakr.domain.quality: Baseline states and flags.

Safety:
    Pure. Over-capacity rates are flagged, never clipped.

Example:
    >>> from vakr.domain.enums import CounterFamily, UptimeContinuity
    >>> prev = CounterSample(CounterFamily.HC64, in_octets=1_000)
    >>> curr = CounterSample(CounterFamily.HC64, in_octets=751_000)
    >>> ctx = RateContext(elapsed_s=60.0, interval_s=60.0, continuity=UptimeContinuity.CONTINUOUS)
    >>> r = compute_rates(prev, curr, ctx)
    >>> r.baseline.name, r.in_octets.rate, r.quality.name
    ('VALID', 100000.0, 'MISSING_FIELDS|CAPACITY_UNKNOWN')
"""

from dataclasses import dataclass

from vakr.domain.enums import BaselineState
from vakr.domain.quality import BASELINE_INVALIDATING, Quality
from vakr.logic.baseline import RateContext, counter_delta, pair_flags
from vakr.logic.capacity import Capacity, effective_capacity
from vakr.logic.counters import COUNTERS, OCTET_COUNTERS, CounterSample, MetricRate


@dataclass(frozen=True, slots=True)
class RateResult:
    """Baseline verdict, per-counter results, and utilization for one sample.

    Attributes:
        baseline: VALID, NO_BASELINE, or INVALIDATED.
        quality: Sample-wide flags. An invalidating flag here means every
            metric is unavailable; per-metric reasons are on each metric.
        in_octets: Inbound octet delta and bits per second.
        out_octets: Outbound octet delta and bits per second.
        in_packets: Inbound packet delta and packets per second.
        out_packets: Outbound packet delta and packets per second.
        in_errors: Inbound error delta and errors per second (RATE-007).
        out_errors: Outbound error delta and errors per second.
        capacity: Utilization denominator used, or None (RATE-008, RATE-010).
        in_utilization_pct: Inbound percent of capacity; None if unavailable.
        out_utilization_pct: Outbound percent of capacity; None if unavailable.
    """

    baseline: BaselineState
    quality: Quality
    in_octets: MetricRate
    out_octets: MetricRate
    in_packets: MetricRate
    out_packets: MetricRate
    in_errors: MetricRate
    out_errors: MetricRate
    capacity: Capacity | None
    in_utilization_pct: float | None
    out_utilization_pct: float | None

    def metric(self, name: str) -> MetricRate:
        """Return one counter's result by name.

        Args:
            name: A member of ``vakr.logic.counters.COUNTERS``.

        Returns:
            MetricRate: The result for that counter.

        Raises:
            AttributeError: If ``name`` is not a counter.
        """
        return getattr(self, name)


def _metric(
    name: str,
    delta: int | None,
    flags: Quality,
    elapsed: float,
    capacity: Capacity | None,
    ctx: RateContext,
) -> MetricRate:
    """Turn one delta into a rate and flag implausible octet rates (RATE-006, RATE-009).

    Args:
        name: Counter name.
        delta: Counter increase, or None.
        flags: Flags from ``counter_delta``.
        elapsed: Positive seconds between the samples.
        capacity: Utilization denominator, or None.
        ctx: Pair evidence.

    Returns:
        MetricRate: Delta, rate, and flags.

    Raises:
        None
    """
    if delta is None:
        return MetricRate(None, None, flags)
    octets = name in OCTET_COUNTERS
    rate = delta * 8 / elapsed if octets else delta / elapsed
    if octets and capacity and rate > ctx.plausibility_factor * capacity.bps:
        flags |= Quality.RATE_EXCEEDS_CAPACITY
    return MetricRate(delta, rate, flags)


def _result(
    baseline: BaselineState,
    quality: Quality,
    metrics: dict[str, MetricRate],
    capacity: Capacity | None,
) -> RateResult:
    """Assemble a result and its utilization.

    Args:
        baseline: Baseline verdict.
        quality: Sample-wide flags.
        metrics: One result per counter name.
        capacity: Utilization denominator, or None.

    Returns:
        RateResult: The assembled result.

    Raises:
        None
    """
    util = {}
    for name in OCTET_COUNTERS:
        rate = metrics[name].rate
        util[name] = rate / capacity.bps * 100 if capacity and rate is not None else None
    return RateResult(
        baseline=baseline,
        quality=quality,
        in_octets=metrics["in_octets"],
        out_octets=metrics["out_octets"],
        in_packets=metrics["in_packets"],
        out_packets=metrics["out_packets"],
        in_errors=metrics["in_errors"],
        out_errors=metrics["out_errors"],
        capacity=capacity,
        in_utilization_pct=util["in_octets"],
        out_utilization_pct=util["out_octets"],
    )


def _unavailable(
    curr: CounterSample, baseline: BaselineState, reasons: Quality, capacity: Capacity | None
) -> RateResult:
    """Build a result with every rate unavailable (RATE-004).

    Args:
        curr: Current sample, which still becomes the next baseline.
        baseline: NO_BASELINE or INVALIDATED.
        reasons: Why the baseline was refused; set on every metric.
        capacity: Utilization denominator, or None.

    Returns:
        RateResult: No deltas, rates, or utilization; reasons on every metric.

    Raises:
        None
    """
    metrics, flags = {}, reasons | (Quality.NONE if capacity else Quality.CAPACITY_UNKNOWN)
    for name in COUNTERS:
        missing = Quality.MISSING_FIELDS if curr.value(name) is None else Quality.NONE
        metrics[name] = MetricRate(None, None, reasons | missing)
        flags |= missing
    return _result(baseline, flags, metrics, capacity)


def compute_rates(prev: CounterSample | None, curr: CounterSample, ctx: RateContext) -> RateResult:
    """Validate the baseline and derive rates for one interface sample (9.7).

    The current sample always becomes the next baseline, whatever the
    verdict; only its rates depend on the prior sample.

    Args:
        prev: The previous sample from the same collector, device, and
            persistent interface UUID, or None when there is none.
        curr: The new sample.
        ctx: Elapsed time, interval, continuity, and capacity evidence.

    Returns:
        RateResult: VALID with rates, or NO_BASELINE / INVALIDATED with every
        rate None and the reasons flagged. ``CAPACITY_UNKNOWN`` is set
        whenever no denominator exists (RATE-010).

    Raises:
        ValueError: If ``prev`` is given without ``ctx.elapsed_s``.
    """
    capacity = effective_capacity(ctx.capacity)
    if prev is None:
        reason = (
            Quality.BASELINE_MISSING_PRIOR if ctx.prior_missing else Quality.BASELINE_FIRST_SAMPLE
        )
        return _unavailable(curr, BaselineState.NO_BASELINE, reason, capacity)
    if ctx.elapsed_s is None:
        raise ValueError("elapsed_s is required when a prior sample is given")
    elapsed = ctx.elapsed_s
    if flags := pair_flags(prev, curr, ctx, elapsed):
        return _unavailable(curr, BaselineState.INVALIDATED, flags, capacity)
    deltas = {name: counter_delta(name, prev, curr, ctx, elapsed) for name in COUNTERS}
    if any(q & Quality.BASELINE_DECREASE for _, q in deltas.values()):
        return _unavailable(curr, BaselineState.INVALIDATED, Quality.BASELINE_DECREASE, capacity)
    base = Quality.NONE if capacity else Quality.CAPACITY_UNKNOWN
    metrics = {n: _metric(n, d, q, elapsed, capacity, ctx) for n, (d, q) in deltas.items()}
    found = Quality.NONE
    for m in metrics.values():
        found |= m.quality
    if all(m.rate is None for m in metrics.values()):
        return _result(BaselineState.INVALIDATED, base | found, metrics, capacity)
    # Per-metric gaps stay on their metric so sample-wide flags keep meaning "all unavailable".
    return _result(BaselineState.VALID, base | (found & ~BASELINE_INVALIDATING), metrics, capacity)
