"""Rolling latency summaries: recent p50/p95/p99 plus lifetime count and sum (OBS-004, E.33).

Shared by the scheduler, the ingester, and ``vakr.metrics``; pure logic, so
every package may use it.

A Prometheus summary needs quantiles, ``_sum``, and ``_count``. vakr keeps
the most recent ``WINDOW`` samples for the quantiles, by the same
nearest-rank rule as the scheduler and the rollups (E.17 #13), and a
lifetime count and sum since the process started. A summary with no sample
yet has no quantiles: an empty window is unknown, never zero (invariant 1).

Dependencies:
    collections.deque: The bounded window.
    vakr.logic.stats: Nearest-rank percentiles.

Safety:
    Memory is fixed at ``WINDOW`` floats per summary.

Example:
    >>> s = Summary(8)
    >>> for v in (0.1, 0.2, 0.3, 0.4): s.observe(v)
    >>> q = s.quantiles()
    >>> (q.p50, q.p99, q.count, round(q.total, 3))
    (0.2, 0.4, 4, 1.0)
"""

import math
from collections import deque
from dataclasses import dataclass

from vakr.logic.stats import nearest_rank

WINDOW = 4096
"""Samples kept per summary: about 40 minutes of polls at the MVP workload (E.25, E.33)."""


@dataclass(frozen=True, slots=True)
class Quantiles:
    """A summary's current values, in seconds.

    Attributes:
        p50: Median of the window.
        p95: 95th percentile of the window.
        p99: 99th percentile of the window.
        count: Samples since start.
        total: Sum of every sample since start.
    """

    p50: float
    p95: float
    p99: float
    count: int
    total: float


class Summary:
    """Recent samples and lifetime totals of one latency."""

    def __init__(self, size: int = WINDOW) -> None:
        """Create an empty summary.

        Args:
            size: Samples kept for the quantiles.

        Returns:
            None

        Raises:
            None
        """
        self._window: deque[float] = deque(maxlen=size)
        self.count = 0
        self.total = 0.0

    def observe(self, seconds: float | None) -> None:
        """Add one sample; ``None``, negative, or non-finite values are ignored.

        Args:
            seconds: The latency.

        Returns:
            None

        Raises:
            None
        """
        if seconds is None or not math.isfinite(seconds) or seconds < 0:
            return
        self._window.append(seconds)
        self.count += 1
        self.total += seconds

    def quantiles(self) -> Quantiles | None:
        """Return the current quantiles and totals.

        Args:
            None

        Returns:
            Quantiles | None: ``None`` before the first sample.

        Raises:
            None
        """
        if not self._window:
            return None
        values = sorted(self._window)
        p50, p95, p99 = (nearest_rank(values, p) for p in (50, 95, 99))
        return Quantiles(p50, p95, p99, self.count, self.total)
