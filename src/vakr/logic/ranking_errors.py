"""Top-error ranking from validated error deltas (DASH-004, DASH-005, RATE-007).

Interfaces rank by the sum of inbound and outbound error deltas over the
window. A sample counts only when both error deltas came from a valid
baseline, so a reload or counter reset can never inject a cumulative value
(RATE-007). Errors per million packets are shown when every counted sample
also has both packet deltas and at least one packet moved:
``1e6 x (in_errors + out_errors) / (in_packets + out_packets)``. Packet
counts are the aggregate counters (SNMP-008); the figure is a ratio for
comparing interfaces, not a per-frame error probability.

Dependencies:
    dataclasses: Immutable inputs and results.
    vakr.logic.ranking, vakr.logic.natural: Coverage, exclusions, name order.
    vakr.logic.rates: ``RateResult``, to build samples from rate output.

Safety:
    Pure. Unavailable deltas reduce coverage; they never count as zero.

Example:
    >>> ok = ErrorSample(in_errors=3, out_errors=1, in_packets=1_000_000, out_packets=1_000_000)
    >>> e = rank_errors([ErrorSeries("ge-0/0/1", (ok, ErrorSample(None, None)), 2)]).ranked[0]
    >>> e.errors, e.per_million, e.coverage.label
    (4, 2.0, '1 of 2 samples')
"""

from collections.abc import Sequence
from dataclasses import dataclass

from vakr.logic.natural import natural_key
from vakr.logic.ranking import NO_BASELINE, Coverage, Excluded
from vakr.logic.rates import RateResult


@dataclass(frozen=True, slots=True)
class ErrorSample:
    """Error and packet deltas of one sample; None where the baseline was unavailable.

    Attributes:
        in_errors: Inbound error delta.
        out_errors: Outbound error delta.
        in_packets: Inbound packet delta.
        out_packets: Outbound packet delta.
    """

    in_errors: int | None
    out_errors: int | None
    in_packets: int | None = None
    out_packets: int | None = None

    @classmethod
    def from_rates(cls, result: RateResult) -> ErrorSample:
        """Take the deltas from a rate result; invalid baselines give None.

        Args:
            result: Output of ``vakr.logic.rates.compute_rates``.

        Returns:
            ErrorSample: The four deltas.

        Raises:
            None
        """
        return cls(
            result.in_errors.delta,
            result.out_errors.delta,
            result.in_packets.delta,
            result.out_packets.delta,
        )


@dataclass(frozen=True, slots=True)
class ErrorSeries:
    """One interface's samples over the window.

    Attributes:
        key: Interface name or identifier; ties break in natural order.
        samples: Samples in the window.
        expected_samples: Samples the window should hold.
    """

    key: str
    samples: tuple[ErrorSample, ...]
    expected_samples: int


@dataclass(frozen=True, slots=True)
class ErrorEntry:
    """One ranked interface (DASH-004).

    Attributes:
        key: Interface name or identifier.
        errors: Raw error delta, in plus out, which sets the rank.
        in_errors: Inbound part.
        out_errors: Outbound part.
        per_million: Errors per million packets; None when packet data
            does not cover every counted sample or no packet moved.
        coverage: Samples counted.
    """

    key: str
    errors: int
    in_errors: int
    out_errors: int
    per_million: float | None
    coverage: Coverage


@dataclass(frozen=True, slots=True)
class ErrorRanking:
    """Top-error result.

    Attributes:
        ranked: Most errors first, at most ``limit`` entries.
        excluded: Interfaces with no usable sample, in natural order.
    """

    ranked: tuple[ErrorEntry, ...]
    excluded: tuple[Excluded, ...]


def _error_entry(s: ErrorSeries) -> ErrorEntry | Excluded:
    """Sum one interface's valid error deltas.

    Args:
        s: The interface's samples.

    Returns:
        ErrorEntry | Excluded: The entry, or why it cannot be ranked.

    Raises:
        None
    """
    valid = [x for x in s.samples if x.in_errors is not None and x.out_errors is not None]
    if not valid:
        return Excluded(s.key, NO_BASELINE)
    in_errors = sum(x.in_errors or 0 for x in valid)
    out_errors = sum(x.out_errors or 0 for x in valid)
    packets = [(x.in_packets, x.out_packets) for x in valid]
    per_million = None
    if all(i is not None and o is not None for i, o in packets):
        total = sum((i or 0) + (o or 0) for i, o in packets)
        per_million = (in_errors + out_errors) * 1_000_000 / total if total else None
    coverage = Coverage(len(valid), s.expected_samples)
    return ErrorEntry(s.key, in_errors + out_errors, in_errors, out_errors, per_million, coverage)


def _order(e: ErrorEntry) -> tuple[int, bool, float, tuple[tuple[str | int, ...], str]]:
    """Sort key: most errors, then highest ratio (known before unknown), then name.

    Args:
        e: A ranked entry.

    Returns:
        tuple: The sort key.

    Raises:
        None
    """
    return -e.errors, e.per_million is None, -(e.per_million or 0.0), natural_key(e.key)


def rank_errors(series: Sequence[ErrorSeries], limit: int | None = None) -> ErrorRanking:
    """Rank interfaces by valid error deltas over the window (DASH-004, DASH-005).

    Args:
        series: One entry per interface over the same window.
        limit: Maximum ranked entries; None for all.

    Returns:
        ErrorRanking: Ranked entries and excluded interfaces.

    Raises:
        None
    """
    results = [_error_entry(s) for s in series]
    ranked = sorted((r for r in results if isinstance(r, ErrorEntry)), key=_order)
    excluded = sorted(
        (r for r in results if isinstance(r, Excluded)), key=lambda x: natural_key(x.key)
    )
    return ErrorRanking(tuple(ranked[:limit]), tuple(excluded))
