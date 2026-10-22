"""The families ``vakr-api`` measures itself: process, requests, inventory, databases (E.33).

Dependencies:
    vakr.metrics.catalog_spec: ``spec`` and the label value sets.

Safety:
    Declarations only; no label names a device, user, path, or address.

Example:
    >>> any(s.name == "vakr_build_info" for s in API_FAMILIES)
    True
"""

from vakr.metrics.catalog_spec import (
    ADMIN_STATES,
    BUCKETS,
    DATABASES,
    DB_FILES,
    LIFECYCLES,
    LISTENERS,
    OPERATION_STATES,
    PROCESSES,
    ROUTE_GROUPS,
    SNAPSHOT_REASONS,
    STATE_REASONS,
    STATUS_CODES,
    spec,
)
from vakr.metrics.exposition import Kind

C, G, S = Kind.COUNTER, Kind.GAUGE, Kind.SUMMARY
PULL_RESULTS = frozenset({"applied", "unchanged", "failed"})
_PROC = ("process", PROCESSES)

API_FAMILIES = (
    spec("vakr_build_info", G, "Always 1; the version label is the running release.",
         ("version", None)),
    spec("vakr_process_start_time_seconds", G, "When the process started, Unix seconds.", _PROC),
    spec("vakr_process_cpu_seconds_total", C, "User and system CPU time since start.", _PROC),
    spec("vakr_process_resident_memory_bytes", G, "Resident memory.", _PROC),
    spec("vakr_process_open_fds", G, "Open file descriptors.", _PROC),
    spec("vakr_event_loop_lag_seconds", G,
         "Largest event-loop lag of the current period (at most a minute).", _PROC),
    spec("vakr_api_requests_total", C, "Requests answered, by route group and status code.",
         ("group", ROUTE_GROUPS), ("code", STATUS_CODES)),
    spec("vakr_api_request_duration_seconds", S,
         "Request duration by route group: recent quantiles, lifetime sum and count.",
         ("group", ROUTE_GROUPS)),
    spec("vakr_api_connections", G, "Open connections on a listener.", ("listener", LISTENERS)),
    spec("vakr_api_connection_limit", G,
         "Connections plus requests a listener allows before answering 503 (E.30).",
         ("listener", LISTENERS)),
    spec("vakr_collector_metrics_up", G,
         "1 when the collector's snapshot is fresh and valid, else 0 with the reason.",
         ("reason", SNAPSHOT_REASONS)),
    spec("vakr_collector_metrics_age_seconds", G,
         "Age of the collector's snapshot when it could be read."),
    spec("vakr_state_metrics_up", G,
         "1 when the inventory and database figures could be read, else 0 with the reason.",
         ("reason", STATE_REASONS)),
    spec("vakr_devices", G, "Devices in inventory by admin state.",
         ("admin_state", ADMIN_STATES)),
    spec("vakr_devices_by_status", G,
         "Listed devices by status bucket (7.7): attention, active, offline.",
         ("bucket", BUCKETS)),
    spec("vakr_interfaces", G, "Interfaces in inventory by lifecycle state.",
         ("lifecycle", LIFECYCLES)),
    spec("vakr_identity_reviews_open", G, "Interface identity reviews waiting for an admin."),
    spec("vakr_operations", G, "Admin operations not finished, by state (E.32).",
         ("state", OPERATION_STATES)),
    spec("vakr_sessions", G, "Stored sign-in sessions, expired ones not yet purged included."),
    spec("vakr_kiosk_tokens", G, "Kiosk tokens, expired ones not yet purged included."),
    spec("vakr_users", G, "Local web and API accounts."),
    spec("vakr_replication_metadata_age_seconds", G,
         "Age of a peer's newest successful metadata pull (E.13); absent on a primary."),
    spec("vakr_replication_metadata_pulls_total", C,
         "A peer's metadata pulls by result, since its pull record began.",
         ("result", PULL_RESULTS)),
    spec("vakr_database_size_bytes", G, "Size of a database file.",
         ("database", DATABASES), ("file", DB_FILES)),
    spec("vakr_database_schema_version", G, "Highest applied schema migration.",
         ("database", DATABASES)),
    spec("vakr_rollup_lag_seconds", G,
         "How far the 15-minute rollups are behind the newest complete native data."),
    spec("vakr_rollup_waiting_age_seconds", G,
         "Age of the oldest native observation still waiting for a rollup (0 when none)."),
    spec("vakr_rankings_unprojected_seconds", G,
         "Complete native data of the last 25 hours the ranking projection misses (E.28)."),
)  # fmt: skip
