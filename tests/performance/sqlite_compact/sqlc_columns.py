"""Column definitions for Compact telemetry tables (blueprint 10.1-10.3, 10.5).

Native rows keep every field of the observation contract. Types are chosen
for SQLite's record format: integers are stored in 0-8 bytes depending on
magnitude, a ``REAL`` with no fractional part is stored as an integer, and
``NULL`` costs only its one-byte header. Shared enums are stored as 1-based
integer codes listed in ``vocab_code``, so the file explains itself.

Dependencies:
    None.

Safety:
    No column holds credentials or community strings (blueprint 10.2).

Example:
    >>> [name for name, _ in COLUMNS["iface"]][:3]
    ['interface_key', 'collector_key', 'ts_us']
    >>> KEYS["icmp"]
    ('device_key', 'collector_key', 'ts_us')
"""

_IDS = (("observation_uuid", "BLOB NOT NULL"), ("poll_uuid", "BLOB NOT NULL"))


def _key(series: str, ts: str = "ts_us") -> tuple[tuple[str, str], ...]:
    """Return the three natural-key columns of a series table.

    Args:
        series (str): ``"interface_key"`` or ``"device_key"``.
        ts (str): Timestamp column name.

    Returns:
        tuple[tuple[str, str], ...]: ``(name, declaration)`` pairs.

    Raises:
        None
    """
    return (
        (series, "INTEGER NOT NULL"),
        ("collector_key", "INTEGER NOT NULL"),
        (ts, "INTEGER NOT NULL"),
    )


def _many(decl: str, *names: str) -> tuple[tuple[str, str], ...]:
    """Declare several columns with the same type.

    Args:
        decl (str): Column declaration such as ``"REAL"``.
        *names (str): Column names.

    Returns:
        tuple[tuple[str, str], ...]: ``(name, declaration)`` pairs.

    Raises:
        None
    """
    return tuple((n, decl) for n in names)


IFACE = (
    *_key("interface_key"),
    *_IDS,
    ("device_key", "INTEGER NOT NULL"),
    *_many(
        "INTEGER",
        "ifindex_observed",
        "admin_status",
        "oper_status",
        "in_octets",
        "out_octets",
        "in_packets",
        "out_packets",
        "in_errors",
        "out_errors",
    ),
    *_many("REAL", "in_bps", "out_bps", "in_pps", "out_pps"),
    *_many("INTEGER", "in_error_delta", "out_error_delta"),
    *_many("REAL", "in_error_rate", "out_error_rate"),
    *_many("INTEGER", "sample_elapsed_us", "counter_family", "counter_discontinuity_ticks"),
    *_many("INTEGER NOT NULL", "baseline_state", "quality_flags"),
)

ICMP = (
    *_key("device_key"),
    *_IDS,
    ("address_family", "INTEGER NOT NULL"),
    ("source_address", "BLOB"),
    ("target_address", "BLOB NOT NULL"),
    *_many(
        "INTEGER NOT NULL",
        "rtt_1_us",
        "rtt_2_us",
        "rtt_3_us",
        "rtt_4_us",
        "rtt_5_us",
        "sent_count",
        "received_count",
        "lost_count",
    ),
    ("loss_percent", "REAL NOT NULL"),
    *_many("INTEGER", "min_rtt_us", "avg_rtt_us", "median_rtt_us", "max_rtt_us", "jitter_us"),
    *_many("INTEGER NOT NULL", "state", "quality_flags"),
)

SNMP = (
    *_key("device_key"),
    *_IDS,
    *_many(
        "INTEGER NOT NULL",
        "snmp_version",
        "request_count",
        "response_count",
        "timeout_count",
        "retry_count",
    ),
    *_many("INTEGER", "elapsed_ms", "sys_uptime_ticks"),
    *_many("INTEGER NOT NULL", "uptime_continuity", "availability_state"),
    ("error_code", "TEXT"),
    ("quality_flags", "INTEGER NOT NULL"),
)

# Bucket end is bucket_start_us + 15 minutes: every rollup table has one width.
IFACE_15M = (
    *_key("interface_key", "bucket_start_us"),
    ("device_key", "INTEGER NOT NULL"),
    *_many("INTEGER NOT NULL", "sample_count", "expected_count", "valid_count", "available_count"),
    *_many(
        "REAL",
        "in_bps_max",
        "in_bps_p95",
        "in_bps_avg",
        "out_bps_max",
        "out_bps_p95",
        "out_bps_avg",
    ),
    *_many("INTEGER", "in_error_sum", "out_error_sum"),
    *_many("INTEGER NOT NULL", "flagged_count", "quality_any"),
)

ICMP_15M = (
    *_key("device_key", "bucket_start_us"),
    *_many(
        "INTEGER NOT NULL", "sample_count", "expected_count", "sent_sum", "received_sum", "lost_sum"
    ),
    *_many(
        "INTEGER",
        "rtt_min_us",
        "rtt_median_us",
        "rtt_p95_us",
        "rtt_max_us",
        "jitter_max_us",
        "jitter_p95_us",
    ),
    *_many("INTEGER NOT NULL", "snmp_ok_count", "snmp_fail_count", "restart_count", "quality_any"),
)

COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "iface": IFACE,
    "icmp": ICMP,
    "snmp": SNMP,
    "iface_15m": IFACE_15M,
    "icmp_15m": ICMP_15M,
}
KEYS = {kind: tuple(name for name, _ in cols[:3]) for kind, cols in COLUMNS.items()}
NATIVE_KINDS = ("iface", "icmp", "snmp")
ROLLUP_KINDS = ("iface_15m", "icmp_15m")

DICTIONARY_DDL = (
    "CREATE TABLE IF NOT EXISTS dict_collector (collector_key INTEGER PRIMARY KEY,"
    " collector_uuid BLOB NOT NULL UNIQUE) STRICT",
    "CREATE TABLE IF NOT EXISTS dict_device (device_key INTEGER PRIMARY KEY,"
    " device_uuid BLOB NOT NULL UNIQUE) STRICT",
    "CREATE TABLE IF NOT EXISTS dict_interface (interface_key INTEGER PRIMARY KEY,"
    " interface_uuid BLOB NOT NULL UNIQUE,"
    " device_key INTEGER NOT NULL REFERENCES dict_device (device_key)) STRICT",
    "CREATE INDEX IF NOT EXISTS dict_interface_device ON dict_interface (device_key)",
    "CREATE TABLE IF NOT EXISTS vocab_code (vocab TEXT NOT NULL, value TEXT NOT NULL,"
    " code INTEGER NOT NULL, PRIMARY KEY (vocab, value), UNIQUE (vocab, code))"
    " WITHOUT ROWID, STRICT",
    # Stand-in for the metadata database's capacity, which rankings divide by.
    "CREATE TABLE IF NOT EXISTS bench_capacity (interface_key INTEGER PRIMARY KEY,"
    " capacity_bps REAL NOT NULL) STRICT",
)
