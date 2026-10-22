"""The shape of a catalogued metric family and the fixed label value sets (E.33).

Each set copies the values of an enum elsewhere in vakr (scheduler steps,
collector and spool states, admin states, lifecycle states, buckets, and so
on) as plain strings, so this package imports no scheduler, ingest, or
domain module; a test checks that every copy still equals its enum.

Dependencies:
    dataclasses: ``FamilySpec``.
    vakr.metrics.exposition: ``Kind``.

Safety:
    Constants only.

Example:
    >>> spec("vakr_x", Kind.GAUGE, "An example.", ("listener", LISTENERS)).labels[0][0]
    'listener'
"""

from dataclasses import dataclass

from vakr.metrics.exposition import Kind

type Label = tuple[str, frozenset[str] | None]
"""A label name and its allowed values; ``None`` only for ``version``."""

QUANTILES = ("0.5", "0.95", "0.99")
PROCESSES = frozenset({"api", "collector"})
STEPS = frozenset({"icmp", "snmp_stage1", "snmp_fallback_stage1", "snmp_stage2"})
STEP_STATUSES = frozenset(
    {"ok", "partial", "no_response", "skipped", "error", "deadline", "cancelled"}
)
COLLECTOR_STATES = frozenset({"healthy", "degraded", "overloaded", "unavailable"})
SPOOL_STATES = frozenset({"normal", "warning", "critical", "exhausted"})
EXECUTIONS = frozenset({"ok", "partial", "failed"})
MISS_REASONS = frozenset({"overrun", "not_attempted"})
CUT_SHORT = frozenset({"cancelled", "deadline"})
FAULTS = frozenset({"icmp_not_sent", "icmp_resolution", "snmp_session", "snmp_transport", "other"})
UPKEEP = frozenset({"move", "projection", "retention"})
INGEST_RESULTS = frozenset({"inserted", "duplicate", "set_aside"})
QUEUES = frozenset({"poll_output", "spool_write"})
LATENCIES = frozenset({"lateness", "duration", "completion"})
ADMIN_STATES = frozenset({"enabled", "maintenance", "disabled", "retired"})
LIFECYCLES = frozenset({"active", "inactive", "stale", "retired"})
BUCKETS = frozenset({"attention", "active", "offline"})
OPERATION_STATES = frozenset({"queued", "running"})
LISTENERS = frozenset({"tcp", "socket"})
ROUTE_GROUPS = frozenset(
    {"metrics", "login", "auth", "kiosk", "health", "history", "rankings", "exports",
     "dashboards", "operations", "reads", "web"}
)  # fmt: skip
STATUS_CODES = frozenset(
    {"200", "201", "202", "204", "304", "400", "401", "403", "404", "405", "409", "413",
     "415", "422", "429", "500", "502", "503", "504", "other", "none"}
)  # fmt: skip
DATABASES = frozenset({"metadata", "telemetry"})
DB_FILES = frozenset({"main", "wal"})
SNAPSHOT_REASONS = frozenset({"ok", "missing", "stale", "unreadable", "too_large", "corrupt"})
STATE_REASONS = frozenset({"ok", "metadata_unavailable"})


@dataclass(frozen=True, slots=True)
class FamilySpec:
    """One catalogued family.

    Attributes:
        name: The metric name.
        kind: Counter, gauge, or summary.
        help: One line for ``# HELP``.
        labels: Label names in output order, each with its allowed values.
    """

    name: str
    kind: Kind
    help: str
    labels: tuple[Label, ...] = ()


def spec(name: str, kind: Kind, help_text: str, *labels: Label) -> FamilySpec:
    """Declare one family.

    Args:
        name: The metric name.
        kind: Its type.
        help_text: One line.
        *labels: Label names and value sets, in output order.

    Returns:
        FamilySpec: The declaration.

    Raises:
        None
    """
    return FamilySpec(name, kind, help_text, tuple(labels))
