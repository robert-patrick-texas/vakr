"""Definitions, thresholds, and bounds of the MVP acceptance reports (27.6, Appendix E.35).

``vakr acceptance schedule`` (MVP-ACC-01), ``gaps`` (MVP-ACC-10), and
``coverage`` (MVP-ACC-02) judge a production node from its own telemetry.
Every number they judge by is here, with the Appendix E.35 row it comes
from; none is an operator option, because an acceptance criterion that the
judged system can tune is not a criterion.

Dependencies:
    datetime: Windows and tolerances.
    vakr.domain.quality: The flags that mark discontinuities and spikes.

Safety:
    Constants only.

Example:
    >>> ON_TIME_TOLERANCE_US, ON_TIME_MIN_SHARE, REFERENCE_INTERVAL_S
    (5000000, 0.99, 60)
"""

from datetime import timedelta

from vakr.domain.quality import Quality

US = 1_000_000
"""Microseconds per second."""

DAY_US = 86_400 * US
"""One UTC day in microseconds."""

BUCKET_US = 900 * US
"""The 15-minute rollup bucket (E.10)."""

# Windows (E.35 "Windows").
SCHEDULE_DEFAULT = timedelta(days=7)
"""``schedule`` window: MVP-ACC-01 judges drift over 7 days."""

GAPS_DEFAULT = timedelta(days=14)
"""``gaps`` window: MVP-ACC-10's 14-day parallel run."""

EVENTS_DEFAULT = timedelta(days=14)
"""``coverage`` discontinuity window, the same 14 days."""

MIN_WINDOW = timedelta(hours=1)
"""Shortest window any report accepts."""

MAX_WINDOW = timedelta(days=30)
"""Longest window: native data, which every report reads, is kept 30 days (E.10)."""

SETTLE = timedelta(minutes=5)
"""Windows end this long before now, so records still in the spool are not called gaps (E.24)."""

# MVP-ACC-01 (E.35 "Schedule").
ON_TIME_TOLERANCE_US = 5 * US
"""A poll is on time when it starts within ±5 s of its scheduled time (27.6)."""

ON_TIME_MIN_SHARE = 0.99
"""At least 99% of healthy-device polls start on time (27.6)."""

REFERENCE_INTERVAL_S = 60
"""The reference workload's poll interval (27.2)."""

REFERENCE_DEVICES = 100
"""The reference workload's devices (27.2), reported for comparison only."""

REFERENCE_INTERFACES = 1_600
"""The reference workload's active interfaces (27.2), reported for comparison only."""

DRIFT_MAX_US_PER_DAY = 100_000
"""Largest allowed slope of the daily median start offset or slot phase: 100 ms a day."""

DRIFT_MIN_DAYS = 7
"""Drift is judged only over at least 7 UTC days that each hold enough polls."""

DRIFT_MIN_POLLS_PER_DAY = 60
"""A UTC day counts toward drift once it holds this many on-time-eligible polls."""

# MVP-ACC-10 (E.35 "Gaps").
GAP_FACTOR = 1.5
"""Consecutive records further apart than 1.5 intervals leave slots without a record (E.19)."""

HEALTH_SILENCE_US = 150 * US
"""Collector silent: health reports more than max(2.5 * 60 s, 60 s + 30 s) apart (E.3, E.27)."""

SPOOL_FULL_PCT = 95.0
"""A not-attempted poll while the spool was this full was refused for storage (E.24)."""

DEFAULT_INTERVAL_S = 60
"""Slot width assumed for a device with no poll record in the window (E.25)."""

# MVP-ACC-02 (E.35 "Coverage").
COVERAGE_RANGES = (
    ("1h", timedelta(hours=1)), ("24h", timedelta(hours=24)), ("7d", timedelta(days=7)),
    ("30d", timedelta(days=30)), ("400d", timedelta(days=400)),
)  # fmt: skip
"""The ranges MVP-ACC-02 names; the last is served from rollups (E.28 ``auto`` source)."""

NATIVE_RANGE_MAX = timedelta(days=30)
"""Ranges up to 30 days are drawn from native data, longer ones from rollups (E.28)."""

FAMILIES = (("traffic", 0b000011), ("packets", 0b001100), ("errors", 0b110000))
"""Graph families and their presence bits in ``INTERFACE_METRICS`` order (in, out)."""

EVENT_FLAGS = (
    Quality.BASELINE_UPTIME_RESET | Quality.BASELINE_DISCONTINUITY
    | Quality.BASELINE_DECREASE | Quality.BASELINE_WIDTH_CHANGE
)  # fmt: skip
"""Device reloads and counter discontinuities whose samples must reset, not spike (RATE-004)."""

SPIKE_FLAG = Quality.RATE_EXCEEDS_CAPACITY
"""A rate above the plausibility bound (E.4, RATE-009): a spike when it follows an event."""

SPIKE_LOOKAHEAD = 2
"""Samples after an event that must not carry ``SPIKE_FLAG``."""

RECENT_BUCKETS = 8
"""Newest rollup buckets read first when looking for a graph family (2 hours)."""

# Bounds (E.35 "Bounds").
DEFAULT_ROWS = 1_000
"""Rows a report lists by default; summaries always count everything."""

MAX_ROWS = 10_000
"""Most rows ``--limit`` may ask for."""

MAX_DEVICES = 5_000
"""Most devices one report reads (E.26 file bound)."""

MAX_INTERFACES_PER_DEVICE = 5_000
"""Most interfaces of one device one report reads."""

MAX_AUDIT_EVENTS = 10_000
"""Most audit events a gap report reads for inventory changes."""

MAX_HEALTH_REPORTS = 50_000
"""Most collector-health reports read per collector: 30 days at one a minute is 43,200."""

MAX_LOSS_INCIDENTS = 3_000
"""Most telemetry-loss incidents considered per collector (the E.24 log holds about 3,000)."""
