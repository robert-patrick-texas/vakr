"""Graph requests: bounded time ranges and the source choice of 10.6 (API-006, SEC-019).

A request names a preset (1h, 6h, 24h, 7d, 30d, 90d, 400d) or a custom
range of 1 minute to 400 days, and a source: ``auto`` follows the 10.6
table, ``native`` or ``rollup`` overrides it. ``plan`` turns that into a
read:

- ``rollup``: the whole window from 15-minute rollups, re-bucketed on the
  ladder (at least 15 minutes wide);
- ``auto`` beyond 30 days (the 90d and 400d presets): the same, as 10.6 asks;
- otherwise, when the window holds at most ``max_points`` polls at the
  effective interval: one point per observation (``ObservationQuery``),
  with E.19 gap points, the 1h and 6h presets at the 60 s default;
- otherwise: native data downsampled on the ladder with a min/max envelope
  (the 24h preset at 120 s, 7d at 10 min, 30d at 1 h); with ``auto``, the
  part before the native horizon comes from rollups, with ``native`` it is
  a disclosed gap.

Dependencies:
    vakr.domain: Windows, sources, and validation.
    vakr.query.history_limits: Presets and bounds (E.28).

Safety:
    Every resolved window is bounded; out-of-range requests raise
    ``ValidationError`` rather than being silently widened.

Example:
    >>> from datetime import UTC, datetime
    >>> now = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    >>> window, preset = TimeRange(RangePreset.H6).resolve(now)
    >>> window.duration.total_seconds(), preset.value
    (21600.0, '6h')
    >>> plan(window, SourceChoice.AUTO, 60, 1200, horizon=None, rollups=True).raw
    True
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import NamedTuple

from vakr.domain.checks import require_int
from vakr.domain.enums import SeriesSource
from vakr.domain.errors import ValidationError
from vakr.domain.ids import CollectorId
from vakr.domain.series import MAX_SERIES_POINTS, TimeWindow
from vakr.query.history_limits import (
    DEFAULT_PRESET,
    MAX_CUSTOM_RANGE,
    MAX_FUTURE_SKEW,
    MIN_CUSTOM_RANGE,
    PRESET_SPANS,
    ROLLUP_AUTO_AFTER,
    RangePreset,
)


class SourceChoice(StrEnum):
    """Which tier a graph reads (10.6, STATE-005)."""

    AUTO = "auto"
    NATIVE = "native"
    ROLLUP = "rollup"


class GraphKind(StrEnum):
    """Interface graph kinds (12.5)."""

    TRAFFIC = "traffic"
    PACKETS = "packets"
    ERRORS = "errors"
    UTILIZATION = "utilization"


def _utc(moment: datetime, name: str) -> datetime:
    """Convert an aware time to UTC; refuse a naive one.

    Args:
        moment: The time.
        name: Field name for the error.

    Returns:
        datetime: The same instant in UTC.

    Raises:
        ValidationError: If ``moment`` has no timezone.
    """
    if not isinstance(moment, datetime) or moment.tzinfo is None:
        raise ValidationError(f"{name} must be a time with a timezone", field=name)
    return moment.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class TimeRange:
    """A preset or a custom range; custom wins when both ends are given.

    Attributes:
        preset: The preset; ``None`` with no custom range means the default (24h).
        start: Custom range start (any zone; converted to UTC).
        end: Custom range end.
    """

    preset: RangePreset | None = None
    start: datetime | None = None
    end: datetime | None = None

    def resolve(self, now: datetime) -> tuple[TimeWindow, RangePreset | None]:
        """Return the bounded window and the preset it came from.

        Args:
            now: Request time (UTC); presets end here, to the second.

        Returns:
            tuple: The window, and the preset (``None`` for a custom range).

        Raises:
            ValidationError: If only one custom end is given, an end is
                naive, the range is shorter than 1 minute or longer than
                400 days, or it ends more than 5 minutes in the future.
        """
        now = now.replace(microsecond=0)
        if self.start is None and self.end is None:
            preset = RangePreset(self.preset or DEFAULT_PRESET)
            return TimeWindow(now - PRESET_SPANS[preset], now), preset
        if self.start is None or self.end is None:
            raise ValidationError("a custom range needs both start and end", field="start")
        start, end = _utc(self.start, "start"), _utc(self.end, "end")
        if end - start < MIN_CUSTOM_RANGE or end - start > MAX_CUSTOM_RANGE:
            raise ValidationError("a custom range must be 1 minute to 400 days", field="end")
        if end > now + MAX_FUTURE_SKEW:
            raise ValidationError("a custom range may not end in the future", field="end")
        return TimeWindow(start, end), None


@dataclass(frozen=True, slots=True)
class GraphRequest:
    """What a graph request asks for.

    Attributes:
        range: Preset or custom range.
        source: ``auto`` (10.6) or an explicit override.
        collector_id: Only this collector's series; ``None`` picks the default
            (the local collector for traffic graphs, every position for latency).
        max_points: Point cap per series, 1 to 1,200 (E.19).
    """

    range: TimeRange = field(default_factory=TimeRange)
    source: SourceChoice = SourceChoice.AUTO
    collector_id: CollectorId | None = None
    max_points: int = MAX_SERIES_POINTS

    def __post_init__(self) -> None:
        """Validate the request.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If ``max_points`` is out of range or ``source`` unknown.
        """
        require_int(self.max_points, "max_points", minimum=1, maximum=MAX_SERIES_POINTS)
        if not isinstance(self.source, SourceChoice):
            raise ValidationError("source must be auto, native, or rollup", field="source")


class Plan(NamedTuple):
    """How to read one series.

    Attributes:
        raw: One point per observation (``ObservationQuery``) rather than buckets.
        source: For bucketed reads, the forced source, or ``None`` to split at the horizon.
    """

    raw: bool
    source: SeriesSource | None


def plan(
    window: TimeWindow,
    choice: SourceChoice,
    interval_s: float,
    max_points: int,
    *,
    horizon: datetime | None,
    rollups: bool,
) -> Plan:
    """Choose the read for a window by the 10.6 table and the explicit override.

    Args:
        window: The window.
        choice: Requested source.
        interval_s: Effective poll interval in seconds.
        max_points: Point cap.
        horizon: Native horizon, if retention has run.
        rollups: Whether the store keeps rollups.

    Returns:
        Plan: Raw or bucketed, and the forced source.

    Raises:
        ValidationError: If ``rollup`` is asked of a store without rollups.
    """
    fits = window.duration.total_seconds() / interval_s <= max_points
    if choice is SourceChoice.ROLLUP:
        if not rollups:
            raise ValidationError("this telemetry store keeps no rollups", field="source")
        return Plan(False, SeriesSource.ROLLUP)
    if choice is SourceChoice.NATIVE:
        crosses = horizon is not None and horizon > window.start
        return Plan(True, None) if fits and not crosses else Plan(False, SeriesSource.NATIVE)
    if window.duration > ROLLUP_AUTO_AFTER and rollups:
        return Plan(False, SeriesSource.ROLLUP)
    return Plan(fits, None)
