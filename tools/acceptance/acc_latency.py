# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""MVP-ACC-04: the application's median RTT and loss against SmokePing's.

SmokePing sends ``pings`` (20 by default) in a burst once per step and
stores the round's median and lost count; the application sends five
spaced probes per cycle, every minute. For each SmokePing row ``T`` the
application's probes from the cycles that started in ``(T-step, T]`` are
pooled. Per row, the medians agree within ``max(tolerance_ms,
tolerance_rel x SmokePing's median)``; per target, the criterion is the
median of the row medians on both sides over every compared row. Loss is
consistent when the two loss rates differ by at most ``loss_abs`` or by at
most ``loss_z`` standard errors of their difference (the two tools sample
independently, with different counts), and no row has one tool seeing
total loss while the other lost less than ``high_loss``. Row
disagreements are categorised but do not decide the verdict, because the
criterion is the median over the run.

Dependencies:
    acc_sources, acc_vakr, acc_config: Rounds, cycles, rules.
    statistics, math, bisect: Standard library.

Safety:
    Pure computation.

Example:
    >>> loss_consistent(10, 2000, 3, 1500, LatencyRules())
    True
"""

import bisect
import math
import statistics
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from acc_config import LatencyRules
from acc_sources import Round
from acc_vakr import Device

LATENCY_CATEGORIES = {
    "vakr_gap": "the application has no ICMP cycle in the row",
    "smokeping_unknown": "SmokePing has no median and no loss for the row",
    "loss_outage": "one tool lost every probe while the other lost less than the high-loss share",
    "loss_burst": "one tool lost at least the high-loss share of its probes, so its median "
                  "comes from few replies",
    "high_variance": "the RTT spread (interquartile range) in the row is wider than the "
                     "tolerance, so two samples of it differ by chance",
    "round_blend": "agrees over the neighbouring rows: SmokePing's RRD row blends two rounds",
    "unexplained": "none of the above; needs a look",
}  # fmt: skip


@dataclass
class LatencyResult:
    """One SmokePing series compared with one device.

    Attributes:
        path: SmokePing target path.
        vantage: Where SmokePing measured from.
        device: The application's device.
        rows: SmokePing rows in the overlap period.
        compared: Rows with both medians.
        agreed: Compared rows within the tolerance.
        excluded: Rows not compared, by category.
        explained: Rows outside the tolerance, by category.
        medians: Median of row medians, SmokePing and the application, ms.
        loss: ``(lost, sent)`` for SmokePing and for the application.
        outages: Rows in ``loss_outage``.
        listed: Excluded and disagreeing rows, for the report.
        verdict: ``pass``, ``fail``, ``review``, or ``insufficient``.
    """

    path: str
    vantage: str
    device: Device
    rows: int = 0
    compared: int = 0
    agreed: int = 0
    excluded: Counter[str] = field(default_factory=Counter)
    explained: Counter[str] = field(default_factory=Counter)
    medians: tuple[float | None, float | None] = (None, None)
    loss: tuple[tuple[float, int], tuple[float, int]] = ((0.0, 0), (0.0, 0))
    outages: int = 0
    listed: list[dict[str, object]] = field(default_factory=list)
    verdict: str = "insufficient"


@dataclass
class LatencyReport:
    """MVP-ACC-04 over all series.

    Attributes:
        results: Compared series.
        unmatched: ``(path, vantage, problem)`` without a matching device.
        skipped: ``(path, vantage, note)`` that SmokePing measures another way.
    """

    results: list[LatencyResult] = field(default_factory=list)
    unmatched: list[tuple[str, str, str]] = field(default_factory=list)
    skipped: list[tuple[str, str, str]] = field(default_factory=list)


def tolerance_ms(smokeping: float, rules: LatencyRules) -> float:
    """Return the RTT tolerance for a SmokePing median.

    Args:
        smokeping: SmokePing's median, ms.
        rules: The rules.

    Returns:
        float: ``max(tolerance_ms, tolerance_rel x median)``.

    Raises:
        None
    """
    return max(rules.tolerance_ms, rules.tolerance_rel * smokeping)


def loss_consistent(s_lost: float, s_sent: int, v_lost: float, v_sent: int,
                    rules: LatencyRules) -> bool:  # fmt: skip
    """Test whether two loss rates are consistent.

    Args:
        s_lost: SmokePing's lost pings.
        s_sent: SmokePing's pings.
        v_lost: The application's lost probes.
        v_sent: The application's probes.
        rules: ``loss_abs`` and ``loss_z``.

    Returns:
        bool: True when the rates differ by at most ``loss_abs`` or ``loss_z``
        standard errors of the difference of two proportions.

    Raises:
        None
    """
    if not s_sent or not v_sent:
        return False
    p_s, p_v = s_lost / s_sent, v_lost / v_sent
    pooled = (s_lost + v_lost) / (s_sent + v_sent)
    error = math.sqrt(pooled * (1 - pooled) * (1 / s_sent + 1 / v_sent))
    return abs(p_s - p_v) <= max(rules.loss_abs, rules.loss_z * error)


def _spread(values: Sequence[float]) -> float:
    """Return the interquartile range, or 0 for fewer than two values.

    Args:
        values: RTTs.

    Returns:
        float: Q3 minus Q1.

    Raises:
        None
    """
    if len(values) < 2:
        return 0.0
    q = statistics.quantiles(values, n=4)
    return q[2] - q[0]


def pool(device: Device, starts: list[float], low: float, high: float) -> tuple[list[float], int]:
    """Pool the probes of cycles that started in ``(low, high]``.

    Args:
        device: The device.
        starts: Its cycle starts, sorted.
        low: Exclusive start.
        high: Inclusive end.

    Returns:
        tuple: Successful RTTs (ms) and probes sent.

    Raises:
        None
    """
    rtts: list[float] = []
    sent = 0
    for start in starts[bisect.bisect_right(starts, low) : bisect.bisect_right(starts, high)]:
        cycle = device.cycles[start]
        sent += len(cycle.rtts_ms)
        rtts += [r for r in cycle.rtts_ms if r is not None]
    return rtts, sent


def category_of(r: Round, rtts: list[float], sent: int, wide: list[float],
              rules: LatencyRules) -> str:  # fmt: skip
    """Name why a compared row's medians disagree.

    Args:
        r: The SmokePing row.
        rtts: The application's RTTs in the row.
        sent: The application's probes in the row.
        wide: The application's RTTs over the neighbouring rows too.
        rules: The rules.

    Returns:
        str: A ``LATENCY_CATEGORIES`` key.

    Raises:
        None
    """
    median = r.median_ms or 0.0
    lost_s = (r.loss or 0.0) / r.pings if r.pings else 0.0
    if lost_s >= rules.high_loss or (sent - len(rtts)) / sent >= rules.high_loss:
        return "loss_burst"
    if tolerance_ms(median, rules) < max(_spread(r.rtts_ms), _spread(rtts)):
        return "high_variance"
    if wide and abs(statistics.median(wide) - median) <= tolerance_ms(median, rules):
        return "round_blend"
    return "unexplained"
