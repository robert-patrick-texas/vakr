"""View models for top-utilization and top-error rankings (12.2, DASH-003..005, E.17 #13).

A ranking row says how much evidence it rests on (``CoverageView``), so a
score from 3 of 15 samples is visibly thin (DASH-005), and which capacity
the utilization was measured against (RATE-008). Interfaces that cannot be
ranked are listed as excluded with the reason, never ranked as zero. A
ranking is always one collector's evidence (MPOS-008).

Dependencies:
    vakr.query.catalog_scope, catalog_views, history_views: Scope, references, windows.
    vakr.query.history_limits: Ranking windows and row bounds (E.28).

Safety:
    Measurements and identifiers only.

Example:
    >>> CoverageView(13, 15).complete, CoverageView(13, 15).label
    (False, '13 of 15 samples')
"""

from dataclasses import dataclass, field
from datetime import datetime

from vakr.domain.catalog import Collector
from vakr.domain.checks import require_int
from vakr.domain.errors import ValidationError
from vakr.domain.ids import CollectorId
from vakr.domain.series import TimeWindow
from vakr.query.catalog_scope import InterfaceScope
from vakr.query.catalog_views import InterfaceRef
from vakr.query.history_limits import TOP_DEFAULT, TOP_MAX, RankingWindow
from vakr.query.history_views import WindowView
from vakr.query.settings import QuerySettings
from vakr.query.zone import zone_name


@dataclass(frozen=True, slots=True)
class CoverageView:
    """How many samples a ranking entry rests on (DASH-005).

    Attributes:
        valid: Samples that counted.
        expected: Samples the window should hold at the effective interval.
        complete: True when ``valid >= expected``.
        label: Such as ``13 of 15 samples``.
    """

    valid: int
    expected: int
    complete: bool = field(init=False)
    label: str = field(init=False)

    def __post_init__(self) -> None:
        """Derive ``complete`` and ``label``.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        object.__setattr__(self, "complete", self.valid >= self.expected)
        object.__setattr__(self, "label", f"{self.valid} of {self.expected} samples")


@dataclass(frozen=True, slots=True)
class UtilizationRow:
    """One ranked interface by directional p95 utilization (DASH-003).

    Attributes:
        rank: 1 for the highest score.
        interface: The interface.
        score_pct: The higher of the two directional p95 values.
        direction: ``in`` or ``out``: the direction that set the score (ties: ``in``).
        in_p95_pct: Inbound p95 utilization, if any inbound sample.
        out_p95_pct: Outbound p95 utilization, if any outbound sample.
        in_p95_bps: Inbound p95 rate.
        out_p95_bps: Outbound p95 rate.
        capacity_bps: The denominator.
        capacity_source: Where it came from, such as ``ifHighSpeed``.
        coverage: Samples behind the scoring direction.
    """

    rank: int
    interface: InterfaceRef
    score_pct: float
    direction: str
    in_p95_pct: float | None
    out_p95_pct: float | None
    in_p95_bps: float | None
    out_p95_bps: float | None
    capacity_bps: int
    capacity_source: str
    coverage: CoverageView


@dataclass(frozen=True, slots=True)
class ErrorRow:
    """One ranked interface by valid error deltas (DASH-004).

    Attributes:
        rank: 1 for the most errors.
        interface: The interface.
        errors: Input plus output errors over the window.
        in_errors: Input part.
        out_errors: Output part.
        per_million: Errors per million packets, when every counted sample had
            packet deltas and packets moved; else ``None``.
        coverage: Samples counted.
    """

    rank: int
    interface: InterfaceRef
    errors: int
    in_errors: int
    out_errors: int
    per_million: float | None
    coverage: CoverageView


@dataclass(frozen=True, slots=True)
class ExcludedRow:
    """An interface left out of a ranking, with the reason (DASH-005).

    Attributes:
        interface: The interface.
        reason: Operator words.
    """

    interface: InterfaceRef
    reason: str


@dataclass(frozen=True, slots=True)
class RankingView[Row: (UtilizationRow, ErrorRow)]:
    """A ranking over one window from one collector.

    Attributes:
        kind: ``utilization`` or ``errors``.
        window: Window (aligned to the E.28 step) and zone.
        window_name: The ranking window, such as ``15m``.
        collector_id: The collector whose samples were ranked.
        position: Its label.
        rows: Ranked rows, at most the requested limit.
        candidates: Interfaces that matched the filters.
        excluded: Some excluded interfaces, in natural order (E.28 cap).
        excluded_total: How many were excluded in all.
        truncated: True when the inventory or the scan hit a bound.
        available: False when the ranking could not be computed at all.
        reason: Why not, in operator words.
        generated_at: When the view was computed (UTC).
        computed_at: When the underlying scan ran (it may be reused, E.28).
        notes: Standing explanations.
    """

    kind: str
    window: WindowView
    window_name: str
    collector_id: CollectorId | None
    position: str
    rows: tuple[Row, ...]
    candidates: int
    excluded: tuple[ExcludedRow, ...]
    excluded_total: int
    truncated: bool
    available: bool
    reason: str | None
    generated_at: datetime
    computed_at: datetime | None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RankingRequest:
    """What a ranking request asks for.

    Attributes:
        window: The ranking window (15 minutes by default, DASH-003).
        limit: Rows to return, 1 to 100.
        collector_id: Rank this collector's samples; the local (else first) one by default.
        scope: Which interfaces may rank (site, provider, WAN, tag, group, ...).
    """

    window: RankingWindow = RankingWindow.M15
    limit: int = TOP_DEFAULT
    collector_id: CollectorId | None = None
    scope: InterfaceScope = field(default_factory=InterfaceScope)

    def __post_init__(self) -> None:
        """Validate the request.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If ``limit`` or ``window`` is out of range.
        """
        require_int(self.limit, "limit", minimum=1, maximum=TOP_MAX)
        if not isinstance(self.window, RankingWindow):
            raise ValidationError("window must be 15m, 1h, 6h, or 24h", field="window")


def empty_view(
    kind: str,
    request: RankingRequest,
    window: TimeWindow,
    collector: Collector | None,
    truncated: bool,
    now: datetime,
    settings: QuerySettings,
) -> RankingView:
    """Return a ranking view without rows, to be filled in or marked unavailable.

    Args:
        kind: ``utilization`` or ``errors``.
        request: The request.
        window: The aligned window.
        collector: The ranked collector, if any.
        truncated: Whether the inventory read was cut short.
        now: When the view is computed (UTC).
        settings: For the display timezone.

    Returns:
        RankingView: Available, empty, not yet computed.

    Raises:
        None
    """
    shown = WindowView(window.start, window.end, request.window.value, zone_name(settings.timezone))
    return RankingView(
        kind,
        shown,
        request.window.value,
        collector.id if collector else None,
        collector.position if collector else "",
        (),
        0,
        (),
        0,
        truncated,
        True,
        None,
        now,
        None,
    )
