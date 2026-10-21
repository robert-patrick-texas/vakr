"""Defaults and bounds of the M4 history, ranking, and search services (Appendix E.28).

Every number a graph, ranking, export, or search request can raise is
capped here, so a request can be slow but never unbounded (SEC-019,
API-005, API-006). The values are constants, not operator options; each
one is listed in Appendix E.28 with its rationale.

Dependencies:
    datetime, enum: Durations and preset names.

Safety:
    Plain constants and small pure helpers.

Example:
    >>> PRESET_SPANS[RangePreset.D400].days, RANKING_SPANS[RankingWindow.M15].seconds
    (400, 900)
    >>> GAP_FACTOR, MAX_CUSTOM_RANGE.days
    (1.5, 400)
"""

from datetime import timedelta
from enum import StrEnum


class RangePreset(StrEnum):
    """Graph range presets (12.6)."""

    H1 = "1h"
    H6 = "6h"
    H24 = "24h"
    D7 = "7d"
    D30 = "30d"
    D90 = "90d"
    D400 = "400d"


PRESET_SPANS: dict[RangePreset, timedelta] = {
    RangePreset.H1: timedelta(hours=1),
    RangePreset.H6: timedelta(hours=6),
    RangePreset.H24: timedelta(hours=24),
    RangePreset.D7: timedelta(days=7),
    RangePreset.D30: timedelta(days=30),
    RangePreset.D90: timedelta(days=90),
    RangePreset.D400: timedelta(days=400),
}
"""Length of each preset; the window ends at the request time."""

DEFAULT_PRESET = RangePreset.H24
"""Range of a graph request that names none."""

MIN_CUSTOM_RANGE = timedelta(minutes=1)
MAX_CUSTOM_RANGE = timedelta(days=400)
"""Bounds of a custom graph range (API-006, SEC-019); 400 d is the longest preset."""

MAX_FUTURE_SKEW = timedelta(minutes=5)
"""How far past "now" a custom range may end; later ends are refused, not clamped."""

ROLLUP_AUTO_AFTER = timedelta(days=30)
"""``source=auto`` reads 15-minute rollups for windows longer than this (10.6 table)."""

GAP_FACTOR = 1.5
"""A native gap is a step longer than this times the effective poll interval (E.19)."""


class RankingWindow(StrEnum):
    """Supported ranking windows (DASH-003: 15 minutes by default)."""

    M15 = "15m"
    H1 = "1h"
    H6 = "6h"
    H24 = "24h"


RANKING_SPANS: dict[RankingWindow, timedelta] = {
    RankingWindow.M15: timedelta(minutes=15),
    RankingWindow.H1: timedelta(hours=1),
    RankingWindow.H6: timedelta(hours=6),
    RankingWindow.H24: timedelta(hours=24),
}

RANKING_ALIGN: dict[RankingWindow, timedelta] = {
    RankingWindow.M15: timedelta(minutes=1),
    RankingWindow.H1: timedelta(minutes=1),
    RankingWindow.H6: timedelta(minutes=5),
    RankingWindow.H24: timedelta(minutes=5),
}
"""Ranking windows end on these UTC boundaries, so repeated requests share one result."""

RANKING_CACHE_ENTRIES = 8
"""Ranking scans kept per service; each is one collector and one aligned window."""

TOP_DEFAULT = 10
TOP_MAX = 100
"""Rows a ranking returns by default and at most."""

EXCLUDED_SHOWN = 100
"""Excluded interfaces listed by name per ranking; the rest are counted."""

OVERVIEW_ROWS = 25
"""High-priority and WAN rows on the overview; full lists page through the catalog."""

TIMELINE_MAX_SPAN = timedelta(days=7)
TIMELINE_MAX_OBSERVATIONS = 10_080
"""A state timeline reads at most 7 days and 10,080 samples (7 days at 60 s)."""

SMOKE_MAX_POLLS = 1_200
"""Per-probe "smoke" is returned only when the window holds at most this many polls."""

EXPORT_MAX_ROWS = 20_000
EXPORT_MAX_BYTES = 4 * 2**20
"""Bounds of one synchronous CSV/JSON export (API-012); larger ones are refused."""

SEARCH_TEXT_MAX = 256
"""Longest search or filter text (E.29)."""

SEARCH_HITS_PER_KIND = 50
"""Most search hits returned per kind (devices, interfaces, sites, ...)."""

EVENTS_SHOWN = 50
"""Most recent events listed per overview event feed."""

REVIEWS_READ = 5_000
"""Open identity reviews one overview reads (newest ``EVENTS_SHOWN`` are listed)."""


def align_down(seconds: float, step: timedelta) -> float:
    """Round an epoch time in seconds down to a multiple of ``step``.

    Args:
        seconds: Seconds since the epoch.
        step: Alignment step.

    Returns:
        float: The aligned time.

    Raises:
        None
    """
    width = step.total_seconds()
    return seconds - (seconds % width)
