# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""MVP-ACC-03: the application's traffic rates against MRTG's 5-minute averages.

MRTG polls every 5 minutes at some phase ``d`` after each 5-minute
boundary (its cron start plus the time to reach this target), computes
one rate per poll interval, and stores the time-weighted average over
each aligned window ``[T-300, T)`` (rateup and RRD both do). So MRTG's
window ``T`` holds ``d/300`` of the poll interval ending at ``T-300+d`` and
``1-d/300`` of the one ending at ``T+d``. The comparison rebuilds exactly that
from the application's per-sample rates (``acc_series.Cumulative``), with
``d`` estimated once per target from the data, near the phase of MRTG's
last run that its files record. Windows then agree within the tolerance,
or are excluded as gaps, or get an explanation category
(``acc_explain``); the volume over all compared windows is reported too.

Dependencies:
    acc_series, acc_explain, acc_bundles, acc_vakr, acc_config.

Safety:
    Pure computation; bounded by the windows loaded.

Example:
    >>> from acc_series import Cumulative
    >>> c = Cumulative([(0.0, 600.0, 100.0), (600.0, 1200.0, 400.0)])
    >>> mrtg_view(c, 900, 300, 0), mrtg_view(c, 900, 300, 150)
    ((400.0, 1.0), (325.0, 1.0))
"""

import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from acc_bundles import MrtgTarget
from acc_config import TrafficRules
from acc_explain import QUALITY_FLAGS
from acc_series import Cumulative
from acc_vakr import Interface, Sample

SAMPLE_WINDOWS = 400
"""Windows the phase estimate looks at (evenly spread)."""


def mrtg_view(c: Cumulative, end: int, step: int, phase: int) -> tuple[float | None, float]:
    """Average the application's rate the way MRTG's window ``end`` was built.

    Args:
        c: One direction's integrals.
        end: MRTG window end.
        step: Window length.
        phase: MRTG's poll phase after each boundary, seconds.

    Returns:
        tuple[float | None, float]: The value and the measured share of the
        effective window (``None`` when nothing in it was measured).

    Raises:
        None
    """
    d = phase % step
    parts = [
        (d / step, end - 2 * step + d, end - step + d),
        (1 - d / step, end - step + d, end + d),
    ]
    value, weight, covered = 0.0, 0.0, 0.0
    for share, a, b in parts:
        if share <= 0:
            continue
        average = c.average(a, b)
        covered += share * c.coverage(a, b)
        if average is not None:
            value, weight = value + share * average, weight + share
    return (value / weight if weight else None), covered


def edge_uncertainty(c: Cumulative, end: int, step: int, phase: int) -> float:
    """Estimate how much MRTG's poll times falling inside samples can move the value.

    Args:
        c: One direction's integrals.
        end: MRTG window end.
        step: Window length.
        phase: MRTG's poll phase.

    Returns:
        float: Bits per second: for each poll time inside a sample, the weight
        change there times the rate change to a neighbouring sample times the
        nearer part of the sample.

    Raises:
        None
    """
    d = phase % step
    jumps = [(end - 2 * step + d, d / step**2), (end - step + d, abs(step - 2 * d) / step**2),
             (end + d, (step - d) / step**2)]  # fmt: skip
    total = 0.0
    for x, weight in jumps:
        i = c.straddling(x)
        if i is None or weight == 0:
            continue
        near = [c.rates[j] for j in (i - 1, i + 1) if 0 <= j < len(c.rates)]
        change = max((abs(c.rates[i] - r) for r in near), default=0.0)
        total += weight * change * min(x - c.starts[i], c.ends[i] - x)
    return total


@dataclass
class TrafficResult:
    """One MRTG target compared with one interface.

    Attributes:
        target: MRTG target name.
        interface: The matched interface.
        phase: Estimated MRTG poll phase, seconds.
        windows: MRTG windows in the overlap period.
        compared: Windows compared (not excluded).
        agreed: Compared windows within the tolerance.
        excluded: Excluded windows by category.
        explained: Out-of-tolerance windows by category.
        volume: Relative volume difference in and out over compared windows.
        rows: Every excluded or out-of-tolerance window, for the report.
        verdict: ``pass``, ``review``, ``fail``, or ``insufficient``.
    """

    target: str
    interface: Interface
    phase: int
    windows: int = 0
    compared: int = 0
    agreed: int = 0
    excluded: Counter[str] = field(default_factory=Counter)
    explained: Counter[str] = field(default_factory=Counter)
    volume: tuple[float | None, float | None] = (None, None)
    rows: list[dict[str, Any]] = field(default_factory=list)
    verdict: str = "insufficient"


def series_for(iface: Interface, target: MrtgTarget) -> tuple[Cumulative, Cumulative, list[Sample]]:
    """Index the samples MRTG would have read, and those it would not.

    Args:
        iface: The interface.
        target: The MRTG target.

    Returns:
        tuple: In and out integrals, and the samples to flag (quality flags or
        another ``ifIndex``).

    Raises:
        None
    """
    index = int(target.key) if target.kind == "ifindex" and target.key.isdigit() else None
    used = [s for s in iface.samples.values() if index is None or s.if_index == index]
    marks = [s for s in iface.samples.values()
             if s.flags & QUALITY_FLAGS or "COUNTER_WRAP_32" in s.flags
             or (index is not None and s.if_index != index)]  # fmt: skip
    cin = Cumulative((s.start, s.end, s.in_bps) for s in used if s.in_bps is not None)
    cout = Cumulative((s.start, s.end, s.out_bps) for s in used if s.out_bps is not None)
    return cin, cout, marks


def estimate_phase(series: tuple[Cumulative, Cumulative], target: MrtgTarget,
                   rules: TrafficRules) -> int:  # fmt: skip
    """Choose MRTG's poll phase: the one that fits the data best near its last run.

    Args:
        series: In and out integrals.
        target: The MRTG target (windows and last run).
        rules: Step and search range.

    Returns:
        int: Phase in seconds, ``0 <= phase < step``.

    Raises:
        None
    """
    step = rules.step_s
    usable = [w for w in sorted(target.windows.values(), key=lambda w: w.end)
              if all(v for v in w.values)]  # fmt: skip
    sample = usable[:: max(1, len(usable) // SAMPLE_WINDOWS)]
    if target.last_run is None:
        candidates = list(range(0, step, rules.phase_step_s))
    else:
        reach = range(-rules.phase_search_s, rules.phase_search_s + 1, rules.phase_step_s)
        candidates = sorted({(target.last_run + k) % step for k in reach})

    def score(phase: int) -> float:
        """Median relative error over the sample for one phase.

        Args:
            phase: Candidate phase.

        Returns:
            float: The score (infinite without data).

        Raises:
            None
        """
        errors = [abs(v - m) / m for w in sample for c, m in zip(series, w.values, strict=True)
                  if m and (v := mrtg_view(c, w.end, step, phase)[0]) is not None]  # fmt: skip
        return statistics.median(errors) if errors else float("inf")

    return min(candidates, key=score) if sample else (target.last_run or 0) % step
