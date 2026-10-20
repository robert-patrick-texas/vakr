"""Table families, column lists, and partition names of the Compact telemetry file (ADR-021).

Layout C+H keeps each observation type in its own *family* of tables:

- one native table per UTC day, ``obs_<type>_dYYYYMMDD``, clustered on
  ``(series_key, collector_key, ts_us, observation_uuid)``, so a graph of
  one interface reads one contiguous key range per day;
- one staging table, ``obs_<type>_hot``, clustered on time first, which
  absorbs the 10-second commits as appends at the right edge of its B-tree;
- one rollup table per UTC month, ``roll_<family>_mYYYYMM``.

``series_key`` is the dictionary key of the observation's subject: the
interface for interface samples, the collector for health snapshots, and
the device for everything else. Column lists here must match the DDL in
``sql/0001_initial.sql``; a unit test compares them.

Dependencies:
    datetime, re: Partition dates and identifier checks.
    vakr.domain: Observation types and errors.

Safety:
    Every table name that reaches SQL text is built here from a fixed
    family and a UTC date and then checked against ``_TABLE``, so no request
    value can ever become part of a statement.

Example:
    >>> day_table("obs_iface", 1_790_812_800_000_000)
    'obs_iface_d20261001'
    >>> month_table("roll_iface", 1_790_812_800_000_000), hot_table("obs_icmp")
    ('roll_iface_m202610', 'obs_icmp_hot')
"""

import re
from datetime import UTC, datetime

from vakr.domain.enums import ObservationType
from vakr.domain.errors import StorageError
from vakr.domain.series import Metric

DAY_US = 86_400_000_000
"""One UTC day in microseconds; native partitions are this wide."""

KEY_COLUMNS = ("series_key", "collector_key", "ts_us", "observation_uuid")
"""Leading columns of every native table, in clustering order."""

COLUMNS: dict[str, tuple[str, ...]] = {
    "obs_iface": (
        "poll_uuid", "device_key", "ifindex_observed", "admin_status", "oper_status",
        "in_octets", "out_octets", "in_packets", "out_packets", "in_errors", "out_errors",
        "in_bps", "out_bps", "in_pps", "out_pps", "in_error_delta", "out_error_delta",
        "in_error_rate", "out_error_rate", "sample_elapsed_us", "counter_family",
        "counter_discontinuity_ticks", "baseline_state", "quality_flags",
    ),
    "obs_icmp": (
        "poll_uuid", "address_family", "source_address", "target_address",
        "rtt_1_us", "rtt_2_us", "rtt_3_us", "rtt_4_us", "rtt_5_us", "rtt_more",
        "sent_count", "received_count", "lost_count", "loss_percent",
        "min_rtt_us", "avg_rtt_us", "median_rtt_us", "max_rtt_us", "jitter_us",
        "state", "quality_flags",
    ),
    "obs_snmp": (
        "poll_uuid", "snmp_version", "request_count", "response_count", "timeout_count",
        "retry_count", "elapsed_ms", "sys_uptime_ticks", "uptime_continuity",
        "availability_state", "error_code", "quality_flags",
    ),
    "obs_poll": (
        "poll_uuid", "configured_interval_s", "effective_interval_s", "reachability",
        "execution", "worker_uuid", "started_offset_us", "completed_offset_us",
        "quality_flags",
    ),
    "obs_health": (
        "state", "software_version", "worker_uuid", "clock_offset_us", "event_loop_lag_us",
        "queue_depth", "queue_capacity", "polls_completed", "polls_late", "polls_missed",
        "spool_bytes", "spool_used_pct", "active_devices", "active_interfaces",
        "quality_flags",
    ),
}  # fmt: skip
"""Columns after ``KEY_COLUMNS`` for each native family, in table order."""

NATIVE_FAMILIES = tuple(COLUMNS)
ROLLUP_FAMILIES = ("roll_iface", "roll_icmp")
ROLLUP_COLUMNS = ("series_key", "collector_key", "bucket_offset_s", "width_adj_s", "payload")
"""Columns of every rollup table; the first four are the primary key."""

FAMILY_OF: dict[ObservationType, str] = {
    ObservationType.INTERFACE: "obs_iface",
    ObservationType.ICMP: "obs_icmp",
    ObservationType.SNMP_DEVICE: "obs_snmp",
    ObservationType.DEVICE_POLL: "obs_poll",
    ObservationType.COLLECTOR_HEALTH: "obs_health",
}
"""Native family storing each observation type."""

METRIC_COLUMN: dict[Metric, str] = {
    Metric.IN_BPS: "in_bps",
    Metric.OUT_BPS: "out_bps",
    Metric.IN_PPS: "in_pps",
    Metric.OUT_PPS: "out_pps",
    Metric.IN_ERRORS: "in_error_rate",
    Metric.OUT_ERRORS: "out_error_rate",
    Metric.RTT_MEDIAN_US: "median_rtt_us",
    Metric.LOSS_PCT: "loss_percent",
    Metric.JITTER_US: "jitter_us",
}
"""Native column holding each metric (``Metric.field`` in storage terms)."""

ROLLUP_FAMILY_OF: dict[ObservationType, str] = {
    ObservationType.INTERFACE: "roll_iface",
    ObservationType.ICMP: "roll_icmp",
}
"""Rollup family storing each observation type's metrics."""

_TABLE = re.compile(r"(?:obs|roll)_[a-z]+_(?:hot|d[0-9]{8}|m[0-9]{6})")


def checked(name: str) -> str:
    """Return a table name after confirming it has a partition's shape.

    Args:
        name: Candidate table name, usually read from the catalog.

    Returns:
        str: ``name`` unchanged.

    Raises:
        StorageError: If the name is not a telemetry partition name, which
            means the catalog was edited behind vakr's back.
    """
    if _TABLE.fullmatch(name) is None:
        raise StorageError("the telemetry partition catalog holds a foreign name", retryable=False)
    return name


def all_columns(family: str) -> tuple[str, ...]:
    """Return every column of a native family's tables, keys first.

    Args:
        family: A native family such as ``obs_iface``.

    Returns:
        tuple[str, ...]: Column names in table order.

    Raises:
        KeyError: If ``family`` is not a native family.
    """
    return KEY_COLUMNS + COLUMNS[family]


def hot_table(family: str) -> str:
    """Name the staging table of a native family.

    Args:
        family: A native family.

    Returns:
        str: ``<family>_hot``.

    Raises:
        StorageError: If ``family`` would not make a valid name.
    """
    return checked(f"{family}_hot")


def day_start(ts_us: int) -> int:
    """Return the start of the UTC day containing a timestamp.

    Args:
        ts_us: Microseconds since the Unix epoch.

    Returns:
        int: Midnight UTC of that day, in microseconds.

    Raises:
        None
    """
    return ts_us - ts_us % DAY_US


def day_table(family: str, ts_us: int) -> str:
    """Name the native partition holding ``ts_us``.

    Args:
        family: A native family.
        ts_us: A timestamp in that day.

    Returns:
        str: ``<family>_dYYYYMMDD``.

    Raises:
        StorageError: If ``family`` would not make a valid name.
    """
    return checked(f"{family}_d{_utc(ts_us):%Y%m%d}")


def month_bounds(ts_us: int) -> tuple[int, int]:
    """Return the UTC month containing a timestamp as ``[start, end)``.

    Args:
        ts_us: Microseconds since the Unix epoch.

    Returns:
        tuple[int, int]: Month start and the next month's start, in microseconds.

    Raises:
        None
    """
    start = _utc(ts_us).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    nxt = start.replace(year=start.year + start.month // 12, month=start.month % 12 + 1)
    return _us(start), _us(nxt)


def month_table(family: str, ts_us: int) -> str:
    """Name the rollup partition whose month contains ``ts_us``.

    Args:
        family: A rollup family.
        ts_us: A bucket start in that month.

    Returns:
        str: ``<family>_mYYYYMM``.

    Raises:
        StorageError: If ``family`` would not make a valid name.
    """
    return checked(f"{family}_m{_utc(ts_us):%Y%m}")


def _utc(ts_us: int) -> datetime:
    """Convert microseconds to an aware UTC datetime for naming.

    Args:
        ts_us: Microseconds since the Unix epoch.

    Returns:
        datetime: The instant.

    Raises:
        None
    """
    return datetime.fromtimestamp(ts_us // 1_000_000, UTC)


def _us(moment: datetime) -> int:
    """Convert a whole-second aware datetime to microseconds.

    Args:
        moment: Aware UTC datetime with no sub-second part.

    Returns:
        int: Microseconds since the Unix epoch.

    Raises:
        None
    """
    return int(moment.timestamp()) * 1_000_000
