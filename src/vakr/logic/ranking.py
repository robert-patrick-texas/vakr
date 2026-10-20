"""Top-utilization and top-error rankings (DASH-003, DASH-004, DASH-005).

Top utilization ranks interfaces by the higher of their inbound and outbound
p95 utilization over the window, 15 minutes by default (DASH-003). The p95
is the nearest-rank percentile from ``vakr.logic.stats``, the same one the
rollups use (RET-004), so a dashboard and a rollup never disagree.

Top errors ranks by the sum of valid inbound and outbound error deltas over
the window, with errors per million packets when every counted sample also
has packet deltas (DASH-004). The input is deltas from validated baselines,
never cumulative counters, so a counter that has climbed for a year cannot
outrank an interface that is failing now.

Samples whose baseline was unavailable are ``None`` and are left out, never
counted as zero. Each entry reports how many samples it rests on, so the UI
can mark incomplete coverage; interfaces with no usable sample are listed as
excluded with the reason (DASH-005). Ties break in natural name order.

Dependencies:
    dataclasses, enum: Immutable inputs and results.
    vakr.logic.stats, vakr.logic.natural: p95 and name order.
    vakr.logic.capacity, vakr.logic.rates: Denominator labels and rate results.

Safety:
    Pure. Missing samples reduce coverage; they never lower a score.

Example:
    >>> s = UtilizationSeries("ge-0/0/1", (10.0, 20.0, None), (30.0, 40.0, None), 3)
    >>> e = rank_utilization([s]).ranked[0]
    >>> e.score_pct, e.direction.value, e.coverage.label
    (40.0, 'out', '2 of 3 samples')
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from vakr.logic.capacity import CapacitySource
from vakr.logic.natural import natural_key
from vakr.logic.rates import RateResult
from vakr.logic.stats import p95

DEFAULT_WINDOW_S = 900  # DASH-003: 15 minutes
NO_BASELINE = "no valid baseline in the window"
NO_CAPACITY = "capacity unknown; utilization unavailable"


class Direction(StrEnum):
    """Which direction set an interface's utilization score."""

    IN = "in"
    OUT = "out"


@dataclass(frozen=True, slots=True)
class Coverage:
    """How many samples a ranking entry rests on (DASH-005).

    Attributes:
        valid: Samples with a valid baseline.
        expected: Samples the window should hold.
    """

    valid: int
    expected: int

    @property
    def complete(self) -> bool:
        """Report whether every expected sample was usable.

        Args:
            None

        Returns:
            bool: True when ``valid >= expected``.

        Raises:
            None
        """
        return self.valid >= self.expected

    @property
    def label(self) -> str:
        """Return coverage as operator text.

        Args:
            None

        Returns:
            str: For example ``"13 of 15 samples"``.

        Raises:
            None
        """
        return f"{self.valid} of {self.expected} samples"


@dataclass(frozen=True, slots=True)
class Excluded:
    """An interface left out of a ranking, with the reason (DASH-005).

    Attributes:
        key: Interface name or identifier.
        reason: Operator text.
    """

    key: str
    reason: str


def expected_samples(window_s: float, interval_s: float) -> int:
    """Return how many samples a window should hold at an interval.

    Args:
        window_s: Ranking window in seconds.
        interval_s: Effective polling interval in seconds.

    Returns:
        int: Whole intervals in the window, at least one.

    Raises:
        ValueError: If either value is not positive.
    """
    if window_s <= 0 or interval_s <= 0:
        raise ValueError("window and interval must be positive")
    return max(1, int(window_s // interval_s))


@dataclass(frozen=True, slots=True)
class UtilizationSeries:
    """One interface's utilization samples over the window.

    Attributes:
        key: Interface name or identifier; ties break in natural order.
        in_pct: Inbound utilization per sample; None where unavailable.
        out_pct: Outbound utilization per sample; None where unavailable.
        expected_samples: Samples the window should hold.
        denominator: Denominator of the newest sample (RATE-008), if known.
    """

    key: str
    in_pct: tuple[float | None, ...]
    out_pct: tuple[float | None, ...]
    expected_samples: int
    denominator: CapacitySource | None = None

    @classmethod
    def from_rates(
        cls, key: str, results: Sequence[RateResult], expected: int
    ) -> UtilizationSeries:
        """Collect utilization from rate results; invalid baselines give None.

        Args:
            key: Interface name or identifier.
            results: Rate results in the window, oldest first.
            expected: Samples the window should hold.

        Returns:
            UtilizationSeries: Per-sample utilization and the newest denominator.

        Raises:
            None
        """
        sources = [r.capacity.source for r in results if r.capacity is not None]
        in_pct = tuple(r.in_utilization_pct for r in results)
        out_pct = tuple(r.out_utilization_pct for r in results)
        return cls(key, in_pct, out_pct, expected, sources[-1] if sources else None)


@dataclass(frozen=True, slots=True)
class UtilizationEntry:
    """One ranked interface (DASH-003).

    Attributes:
        key: Interface name or identifier.
        p95_in_pct: Inbound p95; None without inbound samples.
        p95_out_pct: Outbound p95; None without outbound samples.
        score_pct: The higher of the two, which sets the rank.
        direction: The direction that set the score.
        coverage: Samples behind the score's direction.
        denominator: Utilization denominator, for the label (RATE-008).
    """

    key: str
    p95_in_pct: float | None
    p95_out_pct: float | None
    score_pct: float
    direction: Direction
    coverage: Coverage
    denominator: CapacitySource | None


@dataclass(frozen=True, slots=True)
class UtilizationRanking:
    """Top-utilization result.

    Attributes:
        ranked: Highest score first, at most ``limit`` entries.
        excluded: Interfaces with no usable sample, in natural order.
    """

    ranked: tuple[UtilizationEntry, ...]
    excluded: tuple[Excluded, ...]


def _first(side: tuple[float, Direction, list[float]]) -> float:
    """Return a side's p95, the key for picking the higher direction.

    Args:
        side: ``(p95, direction, samples)``.

    Returns:
        float: The p95.

    Raises:
        None
    """
    return side[0]


def _utilization_entry(s: UtilizationSeries) -> UtilizationEntry | Excluded:
    """Score one interface by its higher directional p95.

    Args:
        s: The interface's samples.

    Returns:
        UtilizationEntry | Excluded: The entry, or why it cannot be ranked.

    Raises:
        None
    """
    valid_in = [v for v in s.in_pct if v is not None]
    valid_out = [v for v in s.out_pct if v is not None]
    if not (valid_in or valid_out):
        return Excluded(s.key, NO_CAPACITY if s.denominator is None else NO_BASELINE)
    p_in = p95(valid_in) if valid_in else None
    p_out = p95(valid_out) if valid_out else None
    sides = ((p_in, Direction.IN, valid_in), (p_out, Direction.OUT, valid_out))
    # max() keeps the first of equal scores, so a tie is reported as inbound.
    score, direction, used = max(((p, d, u) for p, d, u in sides if p is not None), key=_first)
    coverage = Coverage(len(used), s.expected_samples)
    return UtilizationEntry(s.key, p_in, p_out, score, direction, coverage, s.denominator)


def rank_utilization(
    series: Sequence[UtilizationSeries], limit: int | None = None
) -> UtilizationRanking:
    """Rank interfaces by the higher of in/out p95 utilization (DASH-003, DASH-005).

    Args:
        series: One entry per interface over the same window.
        limit: Maximum ranked entries; None for all.

    Returns:
        UtilizationRanking: Ranked entries and excluded interfaces.

    Raises:
        None
    """
    results = [_utilization_entry(s) for s in series]
    ranked = [r for r in results if isinstance(r, UtilizationEntry)]
    ranked.sort(key=lambda e: (-e.score_pct, natural_key(e.key)))
    excluded = sorted(
        (r for r in results if isinstance(r, Excluded)), key=lambda x: natural_key(x.key)
    )
    return UtilizationRanking(tuple(ranked[:limit]), tuple(excluded))
