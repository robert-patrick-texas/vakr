# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Integrate interval rates so any window's average costs two lookups.

The application measures traffic as one rate per poll interval (about a
minute); MRTG reports 5-minute averages on its own poll schedule. To
average the application's rates over MRTG's exact window, assume each
rate held constant over its interval (that is what a counter delta over
elapsed time means) and integrate: ``bits(t)`` is the traffic up to ``t``
and ``covered(t)`` the measured time up to ``t``. A window's average is
then the bits in it divided by the measured seconds in it, and its
coverage is the measured share. Intervals without a rate add nothing,
so a gap stays a gap: it lowers coverage, it never counts as zero traffic.

Dependencies:
    bisect: Standard library only.

Safety:
    Pure arithmetic over sorted, non-overlapping intervals; an interval
    that starts before the previous one ended is clipped to its end.

Example:
    >>> c = Cumulative([(0.0, 60.0, 100.0), (60.0, 120.0, 300.0)])
    >>> c.average(30.0, 90.0), c.coverage(0.0, 240.0)
    (200.0, 0.5)
"""

import bisect
from collections.abc import Iterable


class Cumulative:
    """Integrals of a piecewise-constant rate over sorted intervals.

    Attributes:
        starts: Interval starts, ascending.
        ends: Interval ends.
        rates: Rate per interval.
    """

    def __init__(self, intervals: Iterable[tuple[float, float, float]]) -> None:
        """Index intervals ``(start, end, rate)``.

        Args:
            intervals: Intervals with a rate, in any order.

        Returns:
            None

        Raises:
            None
        """
        self.starts: list[float] = []
        self.ends: list[float] = []
        self.rates: list[float] = []
        self._bits = [0.0]
        self._seconds = [0.0]
        last = float("-inf")
        for begin, end, rate in sorted(intervals):
            start = max(begin, last)
            if end <= start:
                continue
            self.starts.append(start)
            self.ends.append(end)
            self.rates.append(rate)
            self._bits.append(self._bits[-1] + rate * (end - start))
            self._seconds.append(self._seconds[-1] + (end - start))
            last = end

    def _at(self, t: float) -> tuple[float, float]:
        """Return bits and measured seconds up to ``t``.

        Args:
            t: Epoch seconds.

        Returns:
            tuple[float, float]: The two integrals.

        Raises:
            None
        """
        i = bisect.bisect_right(self.starts, t)
        if i == 0:
            return 0.0, 0.0
        bits, seconds = self._bits[i - 1], self._seconds[i - 1]
        inside = min(t, self.ends[i - 1]) - self.starts[i - 1]
        return bits + self.rates[i - 1] * inside, seconds + inside

    def average(self, start: float, end: float) -> float | None:
        """Return the time-weighted average rate over ``[start, end)``.

        Args:
            start: Window start.
            end: Window end.

        Returns:
            float | None: The average over the measured part, or ``None`` when
            nothing in the window was measured.

        Raises:
            None
        """
        b0, s0 = self._at(start)
        b1, s1 = self._at(end)
        return (b1 - b0) / (s1 - s0) if s1 - s0 > 0 else None

    def coverage(self, start: float, end: float) -> float:
        """Return the measured share of ``[start, end)``.

        Args:
            start: Window start.
            end: Window end.

        Returns:
            float: 0 to 1.

        Raises:
            None
        """
        return (self._at(end)[1] - self._at(start)[1]) / (end - start) if end > start else 0.0

    def straddling(self, t: float) -> int | None:
        """Return the index of the interval that ``t`` falls strictly inside.

        Args:
            t: Epoch seconds.

        Returns:
            int | None: The index, or ``None`` when ``t`` is on a boundary or in a gap.

        Raises:
            None
        """
        i = bisect.bisect_right(self.starts, t) - 1
        if i < 0 or not self.starts[i] < t < self.ends[i]:
            return None
        return i

    def maximum(self, start: float, end: float) -> float | None:
        """Return the highest interval rate overlapping ``[start, end)``.

        Args:
            start: Window start.
            end: Window end.

        Returns:
            float | None: The rate, or ``None`` when nothing overlaps.

        Raises:
            None
        """
        lo = max(bisect.bisect_right(self.ends, start), 0)
        hi = bisect.bisect_left(self.starts, end)
        return max(self.rates[lo:hi], default=None)
