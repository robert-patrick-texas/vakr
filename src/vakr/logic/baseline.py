"""Decide whether two counter samples can be subtracted (9.7, E.4, RATE-003, RATE-005).

A rate is the difference between two cumulative counters divided by the time
between them. That only holds when nothing reset the counters in between: a
router reload, an interface reset, a switch between 32-bit and 64-bit
counters, or an identity mix-up all make the subtraction meaningless, and
subtracting anyway is how monitoring tools draw bandwidth spikes after
maintenance windows. These checks refuse such a baseline instead.

Every pair-wide reason in 9.7 is checked and all that apply are flagged. A
decrease is judged per counter, and only after the pair checks pass, so a
reboot is reported as an uptime reset rather than an unexplained decrease.
A 32-bit counter that went down is accepted as a wrap (``COUNTER_WRAP_32``)
only when the pair is otherwise valid, the line rate is known, and the
wrapped delta is physically possible at that rate (RATE-005).

Dependencies:
    dataclasses, math: Immutable context; finiteness checks.
    vakr.logic.capacity, vakr.logic.counters: Line rate and samples.
    vakr.domain.enums, vakr.domain.quality: Families, continuity, and flags.

Safety:
    Pure. Missing evidence, such as an unknown line rate, makes the checks
    stricter, never looser.

Example:
    >>> max_gap_s(CounterFamily.HC64, 60.0, line_rate=None)
    300.0
    >>> round(max_gap_s(CounterFamily.LEGACY32, 60.0, line_rate=10**9), 2)
    17.18
"""

from dataclasses import dataclass
from math import isfinite

from vakr.domain.enums import CounterFamily, UptimeContinuity
from vakr.domain.quality import Quality
from vakr.logic.capacity import NO_CAPACITY, CapacityInputs, line_rate_bps
from vakr.logic.counters import CounterSample, counter_width

MAX_GAP_INTERVALS = 5.0  # E.4: maximum baseline gap in effective intervals
PLAUSIBILITY_FACTOR = 1.10  # E.4: flag rates above 1.10 x effective capacity
_WRAP_32 = 2**32
_HALF_WRAP_BITS = 2**32 * 8 / 2  # half a 32-bit octet counter's range, in bits


@dataclass(frozen=True, slots=True)
class RateContext:
    """Evidence about a pair of samples beyond the counters themselves.

    Attributes:
        elapsed_s: Actual seconds between the two samples; None only when
            there is no prior sample.
        interval_s: Effective polling interval in seconds.
        continuity: ``sysUpTime`` continuity across the pair (E.5).
        capacity: What is known about the interface's capacity.
        prior_missing: True when an earlier baseline existed but the prior
            poll produced no sample, so ``BASELINE_MISSING_PRIOR`` applies
            instead of ``BASELINE_FIRST_SAMPLE``.
        max_gap_intervals: E.4 maximum gap, in intervals.
        plausibility_factor: E.4 over-capacity threshold, as a multiple.
    """

    elapsed_s: float | None
    interval_s: float
    continuity: UptimeContinuity
    capacity: CapacityInputs = NO_CAPACITY
    prior_missing: bool = False
    max_gap_intervals: float = MAX_GAP_INTERVALS
    plausibility_factor: float = PLAUSIBILITY_FACTOR

    def __post_init__(self) -> None:
        """Reject settings that would make every rate meaningless.

        Args:
            None

        Returns:
            None

        Raises:
            ValueError: If the interval or a factor is not positive, or the
                elapsed time is not finite.
        """
        if min(self.interval_s, self.max_gap_intervals, self.plausibility_factor) <= 0:
            raise ValueError("interval and factors must be positive")
        if self.elapsed_s is not None and not isfinite(self.elapsed_s):
            raise ValueError("elapsed_s must be finite")


def max_gap_s(
    family: CounterFamily,
    interval_s: float,
    line_rate: int | None,
    gap_intervals: float = MAX_GAP_INTERVALS,
) -> float:
    """Return the longest usable gap between two samples (E.4).

    Args:
        family: Counter family of the pair.
        interval_s: Effective polling interval in seconds.
        line_rate: Line rate in b/s, or None when unknown.
        gap_intervals: Maximum gap in intervals.

    Returns:
        float: ``gap_intervals x interval`` for HC counters. For 32-bit ones,
        the smaller of that and half the wrap time at line rate, so a second
        wrap cannot hide inside the gap. Unknown line rate leaves only the
        interval rule.

    Raises:
        None
    """
    gap = gap_intervals * interval_s
    if family is CounterFamily.LEGACY32 and line_rate:
        gap = min(gap, _HALF_WRAP_BITS / line_rate)
    return gap


def pair_flags(
    prev: CounterSample, curr: CounterSample, ctx: RateContext, elapsed: float
) -> Quality:
    """Collect every 9.7 reason that rejects the pair as a whole.

    Args:
        prev: Prior sample.
        curr: Current sample.
        ctx: Pair evidence.
        elapsed: Seconds between the samples.

    Returns:
        Quality: Invalidating flags; ``NONE`` when the pair is usable.

    Raises:
        None
    """
    flags = Quality.NONE
    if not (prev.identity_resolved and curr.identity_resolved):
        flags |= Quality.BASELINE_IDENTITY_UNRESOLVED
    if prev.family is not curr.family:
        flags |= Quality.BASELINE_WIDTH_CHANGE
    # Support appearing or disappearing is treated as a change: no evidence, no trust.
    if prev.discontinuity_ticks != curr.discontinuity_ticks:
        flags |= Quality.BASELINE_DISCONTINUITY
    if ctx.continuity is not UptimeContinuity.CONTINUOUS:
        flags |= Quality.BASELINE_UPTIME_RESET
    line_rate = line_rate_bps(ctx.capacity)
    if elapsed <= 0:
        flags |= Quality.BASELINE_ELAPSED_INVALID
    elif elapsed > max_gap_s(curr.family, ctx.interval_s, line_rate, ctx.max_gap_intervals):
        flags |= Quality.BASELINE_GAP
    return flags


def counter_delta(
    name: str, prev: CounterSample, curr: CounterSample, ctx: RateContext, elapsed: float
) -> tuple[int | None, Quality]:
    """Subtract one counter, accepting a 32-bit wrap only when it is possible (RATE-005).

    Args:
        name: Counter name.
        prev: Prior sample; the pair already passed ``pair_flags``.
        curr: Current sample.
        ctx: Pair evidence.
        elapsed: Positive seconds between the samples.

    Returns:
        tuple[int | None, Quality]: The delta, or None with the reason.

    Raises:
        None
    """
    before, after = prev.value(name), curr.value(name)
    if after is None:
        return None, Quality.MISSING_FIELDS
    if before is None:
        return None, Quality.BASELINE_MISSING_PRIOR
    if after >= before:
        return after - before, Quality.NONE
    line_rate = line_rate_bps(ctx.capacity)
    if counter_width(name, curr.family) == 32 and line_rate:
        wrapped = (after - before) % _WRAP_32
        # Each packet or error carries at least one octet, so one bound fits all counters.
        if wrapped * 8 / elapsed <= ctx.plausibility_factor * line_rate:
            return wrapped, Quality.COUNTER_WRAP_32
    return None, Quality.BASELINE_DECREASE
