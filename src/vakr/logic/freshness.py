"""Decide whether an observation still counts as current (E.3, TIME-005, TIME-006).

The newest value is not the current state if it is old: a device last seen
reachable twenty minutes ago is "unknown now", not "up". Freshness derives
from the effective polling interval (TIME-006). An observation is stale when
its age exceeds ``max(2.5 x interval, interval + 30 s)``, which is 150 s at
the 60 s default: one late or missed poll is tolerated, two are not.

Dependencies:
    dataclasses, datetime: Immutable result; timezone-aware times.
    vakr.domain.quality: The ``STALE`` flag.

Safety:
    Pure: callers pass ``now``. Naive datetimes are refused, because every
    canonical timestamp is UTC (TIME-001). A negative age, an observation
    stamped after ``now``, is reported as is and not called stale; clock
    health is judged elsewhere (MPOL-005).

Example:
    >>> from datetime import UTC, datetime, timedelta
    >>> now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    >>> f = assess_freshness(now - timedelta(seconds=151), now, interval_s=60)
    >>> f.stale, f.threshold_s, f.quality.name
    (True, 150.0, 'STALE')
"""

from dataclasses import dataclass
from datetime import datetime

from vakr.domain.quality import Quality

STALE_MULTIPLIER = 2.5  # E.3
STALE_MIN_MARGIN_S = 30.0  # E.3


def require_aware(value: datetime, name: str) -> None:
    """Refuse a naive datetime, which would be read as local time.

    Args:
        value: The datetime to check.
        name: Parameter name for the error message.

    Returns:
        None

    Raises:
        ValueError: If ``value`` has no UTC offset.
    """
    if value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware (UTC)")


def stale_threshold_s(
    interval_s: float,
    multiplier: float = STALE_MULTIPLIER,
    min_margin_s: float = STALE_MIN_MARGIN_S,
) -> float:
    """Return the age beyond which an observation is stale (E.3).

    Args:
        interval_s: Effective polling interval in seconds.
        multiplier: Interval multiple (configurable, TIME-006).
        min_margin_s: Minimum margin over one interval (configurable).

    Returns:
        float: ``max(multiplier x interval, interval + min_margin_s)``.

    Raises:
        ValueError: If the interval is not positive.
    """
    if interval_s <= 0:
        raise ValueError("interval_s must be positive")
    return max(multiplier * interval_s, interval_s + min_margin_s)


@dataclass(frozen=True, slots=True)
class Freshness:
    """Age and freshness of the newest observation (TIME-005).

    Attributes:
        age_s: Seconds since the observation; None if there is none.
        threshold_s: Age beyond which it is stale.
        stale: True when there is no observation or it is too old.
        quality: ``STALE`` when stale, otherwise ``NONE``.
    """

    age_s: float | None
    threshold_s: float
    stale: bool
    quality: Quality


def assess_freshness(
    observed_at: datetime | None,
    now: datetime,
    interval_s: float,
    multiplier: float = STALE_MULTIPLIER,
    min_margin_s: float = STALE_MIN_MARGIN_S,
) -> Freshness:
    """Report the age and freshness of an observation (E.3).

    Args:
        observed_at: When the observation was made, or None if never.
        now: The current time.
        interval_s: Effective polling interval in seconds.
        multiplier: Interval multiple (configurable, TIME-006).
        min_margin_s: Minimum margin over one interval (configurable).

    Returns:
        Freshness: Stale when never observed or when the age exceeds the
        threshold; an age exactly at the threshold is still fresh.

    Raises:
        ValueError: If a datetime is naive or the interval is not positive.
    """
    require_aware(now, "now")
    threshold = stale_threshold_s(interval_s, multiplier, min_margin_s)
    if observed_at is None:
        return Freshness(None, threshold, True, Quality.STALE)
    require_aware(observed_at, "observed_at")
    age = (now - observed_at).total_seconds()
    stale = age > threshold
    return Freshness(age, threshold, stale, Quality.STALE if stale else Quality.NONE)
