"""Rollup arithmetic: one bucket's values summarised exactly once (RET-004, RET-006, RET-008).

A 15-minute rollup keeps, per metric, the smallest and largest value, a
central value, and the nearest-rank p95, so a 400-day graph still shows
the peaks the native samples had (10.5, 10.6). The rules here are the only
place those numbers are computed:

- **Rates** (interface metrics): minimum, maximum, ``vakr.logic.stats.p95``
  (E.17 #13), and the arithmetic mean, computed with ``math.fsum`` and kept
  between the minimum and the maximum.
- **Loss**: every cycle's loss and the bucket's loss are computed from
  probe counts, never by averaging percentages (RET-008): the bucket's
  central value is ``100 * sum(lost) / sum(sent)``. Every value is rounded
  once, to one decimal place half to even, as E.7 stores loss.
- **Replies** (RTT): minimum, maximum, p95, and the median are taken over
  every individual reply in the bucket, never over per-cycle summaries
  (ADR-021), and the median is rounded half to even like E.7's summaries.
- **Whole microseconds** (jitter): the mean is rounded half to even.
- **Schedule slots**: how many polls a fixed-interval schedule intends in
  a window, the bucket's expected sample count.

Dependencies:
    math: Exact float sums.
    vakr.logic.stats: Nearest-rank p95 and half-even rounding (E.7, E.17).

Safety:
    Pure. No value, no summary: every function returns ``None`` instead of
    zero when nothing valid was observed (blueprint 4.3).

Example:
    >>> summarize([3.0, 1.0, 2.0])
    Summary(valid=3, minimum=1.0, mean=2.0, p95=3.0, maximum=3.0)
    >>> loss_summary([(5, 0), (2, 2)]).mean, schedule_slots(0, 900, 30, 60)
    (71.4, 15)
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

from vakr.logic.stats import mean_half_even, median_half_even, p95, percent_one_decimal


@dataclass(frozen=True, slots=True)
class Summary:
    """The aggregates of one metric over one bucket. Immutable.

    Attributes:
        valid: Observations that contributed a value.
        minimum: Smallest value.
        mean: Central value: the mean, the median for replies, or loss
            from summed counts.
        p95: Nearest-rank 95th percentile.
        maximum: Largest value.
    """

    valid: int
    minimum: float
    mean: float
    p95: float
    maximum: float


def summarize(values: Sequence[float]) -> Summary | None:
    """Summarise rate samples: minimum, mean, nearest-rank p95, maximum.

    Args:
        values: The available samples; callers leave missing ones out.

    Returns:
        Summary | None: The aggregates, or ``None`` without any sample.

    Raises:
        None
    """
    if not values:
        return None
    low, high = min(values), max(values)
    # fsum is exact before its single rounding; the clamp keeps an average of
    # identical values from landing one unit in the last place outside them.
    mean = min(max(math.fsum(values) / len(values), low), high)
    return Summary(len(values), float(low), float(mean), float(p95(values)), float(high))


def whole_summary(values: Sequence[int]) -> Summary | None:
    """Summarise integer-microsecond samples, such as jitter, with a half-even mean (E.7).

    Args:
        values: The available samples.

    Returns:
        Summary | None: The aggregates, or ``None`` without any sample.

    Raises:
        None
    """
    if not values:
        return None
    return Summary(len(values), float(min(values)), float(mean_half_even(values)),
                   float(p95(values)), float(max(values)))  # fmt: skip


def reply_summary(replies: Sequence[int], valid: int) -> Summary | None:
    """Summarise every individual reply of a bucket, with the median as central value.

    Args:
        replies: Round-trip times in microseconds of every answered probe.
        valid: Cycles that had at least one reply.

    Returns:
        Summary | None: The aggregates, or ``None`` when nothing answered.

    Raises:
        None
    """
    if not replies or valid <= 0:
        return None
    return Summary(valid, float(min(replies)), float(median_half_even(replies)),
                   float(p95(replies)), float(max(replies)))  # fmt: skip


def loss_summary(counts: Sequence[tuple[int, int]]) -> Summary | None:
    """Summarise packet loss from probe counts, never from percentages (RET-008).

    Args:
        counts: ``(sent, received)`` of every valid cycle; ``sent`` is positive.

    Returns:
        Summary | None: Per-cycle minimum, p95, and maximum, and the bucket
        loss from the summed counts, each to one decimal place half to even
        (E.7); ``None`` without any cycle.

    Raises:
        ValueError: If a cycle sent nothing or received more than it sent.
    """
    if not counts:
        return None
    if any(sent <= 0 or not 0 <= received <= sent for sent, received in counts):
        raise ValueError("a cycle must send probes and receive at most what it sent")
    each = [percent_one_decimal(sent - received, sent) for sent, received in counts]
    sent = sum(c[0] for c in counts)
    lost = sent - sum(c[1] for c in counts)
    return Summary(len(each), min(each), percent_one_decimal(lost, sent), p95(each), max(each))


def schedule_slots(start_us: int, end_us: int, anchor_us: int, period_us: int) -> int:
    """Count the slots ``anchor + k * period`` (any integer ``k``) inside ``[start, end)``.

    Args:
        start_us: Window start, microseconds.
        end_us: Window end, exclusive.
        anchor_us: Any one slot of the schedule, such as a poll's scheduled time.
        period_us: The schedule's interval, positive.

    Returns:
        int: The number of slots, 0 or more.

    Raises:
        ValueError: If ``period_us`` is not positive.
    """
    if period_us <= 0:
        raise ValueError("period_us must be positive")
    first = -((anchor_us - start_us) // period_us)  # ceil((start - anchor) / period)
    after = -((anchor_us - end_us) // period_us)  # ceil((end - anchor) / period)
    return max(0, after - first)
