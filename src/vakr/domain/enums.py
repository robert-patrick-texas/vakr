"""State vocabularies shared by collection, storage, and presentation (blueprint 7.5-7.7).

Every state an operator can see is an explicit enum rather than a free-form
string, so a typo cannot invent a new state and every layer agrees on the
same meaning. String values are lowercase and stable because they are part
of the language-neutral observation contract (ARCH-002) and of stored data:
renaming a value is a schema migration, not a refactor.

Dependencies:
    enum: ``StrEnum`` for stored/serialised states, ``IntEnum`` where the
        SNMP MIB defines integer values.

Safety:
    ``UNKNOWN`` members exist on purpose: missing evidence is never coerced
    into a healthy or failed state (invariants 1-3 in blueprint 4.3).

Example:
    >>> AdminState("maintenance").name
    'MAINTENANCE'
    >>> IfOperStatus(7).name
    'LOWER_LAYER_DOWN'
"""

from enum import IntEnum, StrEnum


class AdminState(StrEnum):
    """Operator-set device state (7.5); controls scheduling, not observation."""

    ENABLED = "enabled"
    MAINTENANCE = "maintenance"
    DISABLED = "disabled"
    RETIRED = "retired"


class IcmpState(StrEnum):
    """Reachability derived from one five-probe ICMP group (Appendix E.1)."""

    REACHABLE = "reachable"
    IMPAIRED = "impaired"
    UNREACHABLE = "unreachable"
    UNKNOWN = "unknown"


class SnmpState(StrEnum):
    """Outcome of the SNMP part of one device poll (7.6)."""

    RESPONDING = "responding"
    TIMEOUT = "timeout"
    ERROR = "error"
    SKIPPED = "skipped"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class UptimeContinuity(StrEnum):
    """Whether ``sysUpTime`` shows the management agent ran continuously (E.5)."""

    CONTINUOUS = "continuous"
    RESET_DETECTED = "reset_detected"
    UNKNOWN = "unknown"


class PollExecution(StrEnum):
    """Lifecycle of one scheduled ``DevicePoll`` (SCHED-012, SCHED-017)."""

    SCHEDULED = "scheduled"
    RUNNING = "running"
    COMPLETE = "complete"
    PARTIAL = "partial"
    MISSED_OVERRUN = "missed_overrun"
    FAILED = "failed"
    NOT_ATTEMPTED = "not_attempted"
    # Appended: shutdown cut a running poll short; finished steps' evidence is kept (16.4).
    CANCELLED = "cancelled"


class PersistenceState(StrEnum):
    """Where an observation is on its way to durable storage (6.4, STORE-004)."""

    QUEUED = "queued"
    SPOOLED = "spooled"
    COMMITTED = "committed"
    REPLAYING = "replaying"
    FAILED = "failed"


class CollectorState(StrEnum):
    """Health of a collector; never translated into target state (OBS-003)."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    OVERLOADED = "overloaded"
    UNAVAILABLE = "unavailable"


class InterfaceLifecycle(StrEnum):
    """Interface lifecycle (LIFE-001, Appendix E.6); history survives every state."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    STALE = "stale"
    RETIRED = "retired"


class InterestClass(StrEnum):
    """How often operators look at an interface (META-011).

    ``PRIMARY`` is the high-priority annotation: uplinks and primary-path
    transit. ``SECONDARY`` is the default. Both are collected identically.
    """

    PRIMARY = "primary"
    SECONDARY = "secondary"


class ReachabilityStrategy(StrEnum):
    """How ICMP results gate SNMP collection for a device (ICMP-006..014)."""

    ICMP_GATED = "icmp_gated"
    SNMP_FALLBACK = "snmp_fallback"
    ICMP_ONLY = "icmp_only"
    SNMP_ONLY = "snmp_only"


class CounterFamily(StrEnum):
    """Which IF-MIB counter width produced a sample (SNMP-007, RATE-003)."""

    HC64 = "hc64"
    LEGACY32 = "legacy32"


class BaselineState(StrEnum):
    """Whether a counter sample could be paired with a valid previous one (9.7).

    ``NO_BASELINE`` covers the first sample and a missing previous sample;
    ``INVALIDATED`` means a previous sample existed but could not be used.
    The specific reason is carried in quality flags.
    """

    VALID = "valid"
    NO_BASELINE = "no_baseline"
    INVALIDATED = "invalidated"


class SnmpVersion(StrEnum):
    """SNMP protocol version of a credential (SNMP-001); v1 is not supported."""

    V2C = "2c"
    V3 = "3"


class AddressFamily(StrEnum):
    """IP address family of a poll target or interface address (ICMP-011)."""

    IPV4 = "ipv4"
    IPV6 = "ipv6"


class StatusBucket(StrEnum):
    """Device-list ordering bucket (7.7): attention first, offline last."""

    ATTENTION = "attention"
    ACTIVE = "active"
    OFFLINE = "offline"


class StatusColor(StrEnum):
    """Display colour of a status; never the only cue (UI-011, 12.8)."""

    RED = "red"
    AMBER = "amber"
    GREEN = "green"
    GRAY = "gray"


class ReconcileOutcome(StrEnum):
    """Result of matching a discovered interface to a persistent identity (IFID-003)."""

    EXACT = "exact"
    CONFIDENT_RENUMBER = "confident_renumber"
    PROBABLE = "probable"
    AMBIGUOUS = "ambiguous"
    NEW = "new"
    MISSING = "missing"


class IfAdminStatus(IntEnum):
    """IF-MIB ``ifAdminStatus`` values (RFC 2863); integers as the agent sends them."""

    UP = 1
    DOWN = 2
    TESTING = 3


class IfOperStatus(IntEnum):
    """IF-MIB ``ifOperStatus`` values (RFC 2863); integers as the agent sends them."""

    UP = 1
    DOWN = 2
    TESTING = 3
    UNKNOWN = 4
    DORMANT = 5
    NOT_PRESENT = 6
    LOWER_LAYER_DOWN = 7


# Appended by M2-core (domain model, ports, envelope). Existing members above
# are unchanged; these classes extend the shared vocabulary.


class ManagedByKind(StrEnum):
    """Which source owns a configurable record (META-008, blueprint 11.2)."""

    FILE = "file"
    API = "api"


class AuditOutcome(StrEnum):
    """Result of an audited action (SEC-018)."""

    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"


class IdentityDecisionSource(StrEnum):
    """Who decided an interface identity outcome (IFID-005, IFID-007)."""

    AUTOMATIC = "automatic"
    ADMIN = "admin"


class ObservationType(StrEnum):
    """Kind of observation carried by the language-neutral envelope (6.3, ARCH-002)."""

    DEVICE_POLL = "device_poll"
    ICMP = "icmp"
    SNMP_DEVICE = "snmp_device"
    INTERFACE = "interface"
    COLLECTOR_HEALTH = "collector_health"


class Freshness(StrEnum):
    """Whether current state is recent enough to present as current (TIME-005, E.3)."""

    FRESH = "fresh"
    STALE = "stale"


class SeriesSource(StrEnum):
    """Which storage tier produced part of a series (STATE-005, 10.6)."""

    NATIVE = "native"
    ROLLUP = "rollup"
