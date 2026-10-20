"""Exact summary statistics with one documented rounding rule, and the shared p95.

ICMP summaries are stored as integer microseconds rounded half to even, and
loss to one decimal place, also half to even (Appendix E.7). Computing them
with exact rational arithmetic gives the infinitely precise value before the
single rounding step, so a Python collector, a future Go collector, and a
rollup job produce identical integers. Float64 arithmetic agrees except in
rare near-tie cases, which exact arithmetic removes.

The p95 used by top-utilization rankings (DASH-003) and by interface rollups
(RET-004) is defined here once: the nearest-rank method. It always returns
one of the samples, never an interpolated value, so the same samples give
the same p95 in every component and every language.

Dependencies:
    fractions, math: Exact rationals and integer square roots.

Safety:
    Pure. Empty inputs raise ``ValueError`` instead of returning zero, because
    a missing statistic is never zero (blueprint 4.3).

Example:
    >>> mean_half_even([1, 2]), median_half_even([1, 2, 3, 4]), pstdev_half_even([1, 3])
    (2, 2, 1)
    >>> percent_one_decimal(3, 2000), nearest_rank([40, 10, 30, 20], 95)
    (0.2, 40)
"""

from collections.abc import Sequence
from fractions import Fraction
from math import ceil, isqrt


def _require(values: Sequence[object], minimum: int) -> None:
    """Refuse to summarise too few values.

    Args:
        values: The samples.
        minimum: Smallest acceptable count.

    Returns:
        None

    Raises:
        ValueError: If fewer than ``minimum`` values are given.
    """
    if len(values) < minimum:
        raise ValueError(f"need at least {minimum} value(s), got {len(values)}")


def mean_half_even(values: Sequence[int]) -> int:
    """Return the arithmetic mean, exactly rounded half to even.

    Args:
        values: Integer samples, at least one.

    Returns:
        int: The rounded mean.

    Raises:
        ValueError: If ``values`` is empty.
    """
    _require(values, 1)
    return round(Fraction(sum(values), len(values)))


def median_half_even(values: Sequence[int]) -> int:
    """Return the median, exactly rounded half to even.

    Args:
        values: Integer samples in any order, at least one.

    Returns:
        int: The middle value, or the rounded mean of the two middle values.

    Raises:
        ValueError: If ``values`` is empty.
    """
    _require(values, 1)
    ordered, mid = sorted(values), len(values) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return round(Fraction(ordered[mid - 1] + ordered[mid], 2))


def pstdev_half_even(values: Sequence[int]) -> int:
    """Return the population standard deviation, exactly rounded half to even.

    With ``N = n*sum(x^2) - sum(x)^2`` the deviation is ``sqrt(N)/n``.
    Comparing ``4N`` with ``(n*(2r+1))^2`` decides whether it lies above,
    below, or exactly on ``r + 1/2``, using integers only.

    Args:
        values: Integer samples, at least one.

    Returns:
        int: The rounded deviation.

    Raises:
        ValueError: If ``values`` is empty.
    """
    _require(values, 1)
    n = len(values)
    big_n = n * sum(x * x for x in values) - sum(values) ** 2
    r = isqrt(big_n) // n
    lhs, rhs = 4 * big_n, (n * (2 * r + 1)) ** 2
    if lhs > rhs or (lhs == rhs and r % 2):
        return r + 1
    return r


def percent_one_decimal(part: int, whole: int) -> float:
    """Return ``100 * part / whole`` to one decimal place, exactly half to even.

    Args:
        part: Numerator, such as lost probes.
        whole: Denominator, such as probes sent; must be positive.

    Returns:
        float: The rounded percentage.

    Raises:
        ValueError: If ``whole`` is not positive.
    """
    if whole <= 0:
        raise ValueError("whole must be positive")
    return float(round(Fraction(100 * part, whole), 1))


def nearest_rank[T: (int, float)](values: Sequence[T], percent: float) -> T:
    """Return the nearest-rank percentile: the sample at rank ``ceil(p/100 * n)``.

    Args:
        values: Samples in any order, at least one. Callers drop unavailable
            samples first; they are never counted as zero.
        percent: Percentile in (0, 100], such as 95.

    Returns:
        int | float: One of the samples.

    Raises:
        ValueError: If ``values`` is empty or ``percent`` is out of range.
    """
    _require(values, 1)
    if not 0 < percent <= 100:
        raise ValueError("percent must be in (0, 100]")
    rank = ceil(Fraction(str(percent)) * len(values) / 100)
    return sorted(values)[rank - 1]


def p95[T: (int, float)](values: Sequence[T]) -> T:
    """Return the nearest-rank 95th percentile (DASH-003, RET-004).

    Args:
        values: Available samples, at least one.

    Returns:
        int | float: One of the samples.

    Raises:
        ValueError: If ``values`` is empty.
    """
    return nearest_rank(values, 95)
