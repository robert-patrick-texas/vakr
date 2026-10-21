"""Read port for rankings: per-interface rate percentiles and error totals over a window (M4).

Top utilization and top errors (12.2, DASH-003, DASH-004, UI-008) need one
number per interface over a window, for every interface a collector
measured. Reading every native sample into the application would cost
millions of rows for a 24-hour window at MVP scale, so the store
summarises in place and returns one row per interface (12.9). Shared
semantics every backend reproduces:

- **One collector.** Values from different collectors are never combined
  or summed (MPOS-008, MPOL-003); callers rank one position at a time.
- **Which samples count.** Native interface observations of that
  collector with ``window.start <= timestamp < window.end``, at or after
  the native horizon. A ``None`` rate or delta is unavailable (an invalid
  baseline) and is left out, never counted as zero (RATE-004).
- **Rate percentiles.** Inbound and outbound separately, the nearest-rank
  p95 of the available ``in_bps`` / ``out_bps`` values (``vakr.logic.stats
  .p95``, E.17 #13): the value at rank ``ceil(0.95 n)`` in ascending order.
- **Error totals.** A sample counts when both error deltas are present.
  ``packets`` is the summed packet delta of the counted samples, each
  ``(in_pps + out_pps) x elapsed``, rounded once at the end; it is ``None``
  unless every counted sample has both packet rates and its elapsed time
  (E.17 #13), and ``None`` when no sample counted.
- **Order and bounds.** Rows are ordered by interface UUID text; at most
  ``limit`` rows are returned and ``truncated`` says whether more existed.
  Windows longer than ``MAX_RANKING_WINDOW`` are refused (SEC-019).

Dependencies:
    vakr.domain: IDs, windows, and errors.

Safety:
    Bounded by the window length and ``limit``; read-only.

Example:
    >>> check_ranking_bounds(MAX_RANKING_WINDOW.total_seconds(), MAX_RANKED_INTERFACES)
    >>> sorted(WindowSummary.__dataclass_fields__)[:3]
    ['error_samples', 'in_errors', 'in_p95_bps']
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from vakr.domain.errors import ValidationError
from vakr.domain.ids import CollectorId, InterfaceId
from vakr.domain.series import TimeWindow

MAX_RANKING_WINDOW = timedelta(days=1)
"""Longest window a ranking summarises (E.28)."""

MAX_RANKED_INTERFACES = 20_000
"""Most interfaces one ranking read returns (E.28, the E.29 interface bound)."""


def check_ranking_bounds(window_s: float, limit: int) -> None:
    """Refuse a ranking read that is not bounded as E.28 requires.

    Args:
        window_s: Window length in seconds.
        limit: Requested row limit.

    Returns:
        None

    Raises:
        ValidationError: If the window is longer than ``MAX_RANKING_WINDOW``
            or ``limit`` is outside 1 to ``MAX_RANKED_INTERFACES``.
    """
    if window_s > MAX_RANKING_WINDOW.total_seconds():
        raise ValidationError("a ranking window may be at most one day", field="window")
    if isinstance(limit, bool) or not 1 <= limit <= MAX_RANKED_INTERFACES:
        raise ValidationError("limit is out of range", field="limit")


@dataclass(frozen=True, slots=True)
class WindowSummary:
    """One interface's rates and errors over a window, from one collector.

    Attributes:
        interface_id: The interface.
        samples: Observations in the window, valid or not.
        in_valid: Observations with an inbound rate.
        out_valid: Observations with an outbound rate.
        in_p95_bps: Nearest-rank p95 of the inbound rates, if any.
        out_p95_bps: Nearest-rank p95 of the outbound rates, if any.
        error_samples: Observations with both error deltas (the counted ones).
        in_errors: Summed inbound error deltas of the counted observations.
        out_errors: Summed outbound error deltas of the counted observations.
        packets: Summed packet deltas of the counted observations, when
            every one has them; else ``None``.
    """

    interface_id: InterfaceId
    samples: int
    in_valid: int
    out_valid: int
    in_p95_bps: float | None
    out_p95_bps: float | None
    error_samples: int
    in_errors: int
    out_errors: int
    packets: int | None


@dataclass(frozen=True, slots=True)
class RankingScan:
    """Per-interface summaries, ordered by interface UUID text.

    Attributes:
        items: The summaries.
        truncated: True when more interfaces had samples than ``limit``.
    """

    items: tuple[WindowSummary, ...]
    truncated: bool


class RankingQuery(Protocol):
    """Per-interface window summaries for rankings (DASH-003, DASH-004)."""

    def window_summaries(
        self, collector_id: CollectorId, window: TimeWindow, *, limit: int = MAX_RANKED_INTERFACES
    ) -> RankingScan:
        """Return every interface's directional p95 rates and error totals over the window.

        Both rankings come from one read, because the samples they need are
        the same rows (12.9).

        Args:
            collector_id: The one collector whose samples count.
            window: Bounded window, at most ``MAX_RANKING_WINDOW``.
            limit: Most rows, 1 to ``MAX_RANKED_INTERFACES``.

        Returns:
            RankingScan: One row per interface with samples in the window.

        Raises:
            ValidationError: If the window or limit is out of bounds.
        """
        ...
