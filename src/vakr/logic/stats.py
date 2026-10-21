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

``exact_parts`` keeps a float sum exact while it is split: the ranking
projection stores each bucket's packet total as the few non-overlapping
floats whose real sum is exactly the bucket's sum, so ``math.fsum`` of any
mix of stored parts and fresh samples is the correctly rounded total of
every sample, whatever the split (DASH-004, E.17 #13, E.28).

Dependencies:
    fractions, math: Exact rationals, integer square roots, and finiteness.

Safety:
    Pure. Empty inputs raise ``ValueError`` instead of returning zero, because
    a missing statistic is never zero (blueprint 4.3).

Example:
    >>> mean_half_even([1, 2]), median_half_even([1, 2, 3, 4]), pstdev_half_even([1, 3])
    (2, 2, 1)
    >>> percent_one_decimal(3, 2000), nearest_rank([40, 10, 30, 20], 95)
    (0.2, 40)
    >>> from math import fsum
    >>> (1e16 + 1.0) - 1e16, fsum(exact_parts([1e16, 1.0]) + exact_parts([-1e16]))
    (0.0, 1.0)
"""

from collections.abc import Iterable, Sequence
from fractions import Fraction
from math import ceil, isfinite, isqrt


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


def exact_parts(values: Iterable[float]) -> list[float]:
    """Return non-overlapping floats whose real sum is exactly the real sum of ``values``.

    Shewchuk's expansion (the algorithm inside ``math.fsum``): each value is
    added with error-free two-sums, so nothing is rounded away. Splitting a
    sum into parts and joining them later with ``math.fsum`` therefore gives
    the same correctly rounded total as summing every value at once. A
    non-finite value is kept as it is, so ``math.fsum`` treats it as it
    would the original.

    Args:
        values: Finite floats whose partial sums stay finite, plus any
            non-finite ones.

    Returns:
        list[float]: Usually one to three parts; empty for no values.

    Raises:
        None
    """
    parts: list[float] = []
    special: list[float] = []
    for value in values:
        if not isfinite(value):
            special.append(value)
            continue
        x, kept = value, 0
        for y in parts:
            big, small = (y, x) if abs(x) < abs(y) else (x, y)
            high = big + small
            low = small - (high - big)  # exactly what rounding dropped from high
            if low:
                parts[kept] = low
                kept += 1
            x = high
        parts[kept:] = [x]
    return parts + special
