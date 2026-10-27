"""Sample records and observations for the contract suites.

Builders return valid domain objects with fresh UUIDs and fixed, bucket-
aligned times, so every adapter sees identical inputs. Keyword overrides
replace any field.

Dependencies:
    vakr.domain: The records being built.

Example:
    >>> device().name
    'core1'
"""

from dataclasses import fields, is_dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from vakr.domain.catalog import Collector, Group, Tag
from vakr.domain.control import AuditEvent, CollectionPolicy, IdentityHistoryEntry
from vakr.domain.enums import (
    AddressFamily,
    AuditOutcome,
    BaselineState,
    CollectorState,
    CounterFamily,
    IcmpState,
    IdentityDecisionSource,
    IfAdminStatus,
    IfOperStatus,
    InterfaceLifecycle,
    PollExecution,
    ReachabilityStrategy,
    ReconcileOutcome,
    SnmpState,
    SnmpVersion,
    UptimeContinuity,
)
from vakr.domain.ids import (
    AuditEventId,
    CollectorId,
    DeviceId,
    GroupId,
    IdentityHistoryId,
    InterfaceId,
    ObservationId,
    PolicyId,
    PollId,
    SiteId,
    TagId,
    new_id,
)
from vakr.domain.interfaces import INACTIVE_STATES, Interface
from vakr.domain.inventory import API_MANAGED, Device, ManagedBy, Site
from vakr.domain.observations import (
    CollectorHealthObservation,
    DevicePoll,
    IcmpObservation,
    InterfaceObservation,
    SnmpDeviceObservation,
)
from vakr.domain.quality import Quality
from vakr.domain.status_observations import InterfaceStatusObservation

T0 = datetime(2026, 10, 1, tzinfo=UTC)
"""Reference time, aligned to the 15-minute rollup grid."""

MINUTE = timedelta(minutes=1)
FILE_OWNER = ManagedBy.file("/etc/vakr/inventory/devices.toml")


def site(name: str = "Dallas DC1", **overrides: Any) -> Site:
    """Build a site.

    Args:
        name: Site name.
        **overrides: Field values to replace.

    Returns:
        Site: The site.

    Raises:
        None
    """
    base: dict[str, Any] = {"id": new_id(SiteId), "name": name, "code": "DAL1"}
    base["managed_by"] = API_MANAGED
    return Site(**base | overrides)


def tag(name: str = "wan", **overrides: Any) -> Tag:
    """Build a tag.

    Args:
        name: Tag name.
        **overrides: Field values to replace.

    Returns:
        Tag: The tag.

    Raises:
        None
    """
    base: dict[str, Any] = {"id": new_id(TagId), "name": name, "managed_by": API_MANAGED}
    return Tag(**base | overrides)


def group(name: str = "core", **overrides: Any) -> Group:
    """Build a group.

    Args:
        name: Group name.
        **overrides: Field values to replace.

    Returns:
        Group: The group.

    Raises:
        None
    """
    base: dict[str, Any] = {"id": new_id(GroupId), "name": name, "managed_by": API_MANAGED}
    return Group(**base | overrides)


def policy(name: str = "skip-loopbacks", order: int = 10, **overrides: Any) -> CollectionPolicy:
    """Build a collection policy with a nested rule document.

    Args:
        name: Policy name.
        order: Evaluation order.
        **overrides: Field values to replace.

    Returns:
        CollectionPolicy: The policy.

    Raises:
        None
    """
    rules = {"match": {"if_type": [24, 131]}, "action": "exclude", "weight": 0.5, "note": None}
    base: dict[str, Any] = {
        "id": new_id(PolicyId),
        "name": name,
        "order": order,
        "scope": "interface",
        "rules": rules,
        "managed_by": API_MANAGED,
    }
    return CollectionPolicy(**base | overrides)


def collector(name: str = "east", **overrides: Any) -> Collector:
    """Build a collector.

    Args:
        name: Collector name.
        **overrides: Field values to replace.

    Returns:
        Collector: The collector.

    Raises:
        None
    """
    base: dict[str, Any] = {
        "id": new_id(CollectorId),
        "name": name,
        "position": name,
        "managed_by": API_MANAGED,
    }
    return Collector(**base | overrides)


def device(name: str = "core1", **overrides: Any) -> Device:
    """Build a device.

    Args:
        name: Device name.
        **overrides: Field values to replace.

    Returns:
        Device: The device.

    Raises:
        None
    """
    base: dict[str, Any] = {
        "id": new_id(DeviceId),
        "name": name,
        "poll_address": "192.0.2.1",
        "managed_by": API_MANAGED,
        "created_at": T0,
        "updated_at": T0,
    }
    return Device(**base | overrides)


def interface(device_id: DeviceId, if_name: str = "Gi1/0/1", **overrides: Any) -> Interface:
    """Build an active interface; an inactive or stale one's stale clock starts at ``updated_at``.

    Args:
        device_id: Owning device.
        if_name: ``ifName``.
        **overrides: Field values to replace; ``inactive_since`` too.

    Returns:
        Interface: The interface.

    Raises:
        None
    """
    base: dict[str, Any] = {
        "id": new_id(InterfaceId),
        "device_id": device_id,
        "if_index": 1,
        "if_name": if_name,
        "if_descr": if_name,
        "if_alias": "",
        "lifecycle": InterfaceLifecycle.ACTIVE,
        "managed_by": API_MANAGED,
        "created_at": T0,
        "updated_at": T0,
    }
    merged = base | overrides
    if merged["lifecycle"] in INACTIVE_STATES:
        merged.setdefault("inactive_since", merged["updated_at"])
    return Interface(**merged)


def history(iface: Interface, at: datetime, **overrides: Any) -> IdentityHistoryEntry:
    """Build an identity-history entry for an interface.

    Args:
        iface: The interface.
        at: Decision time.
        **overrides: Field values to replace.

    Returns:
        IdentityHistoryEntry: The entry.

    Raises:
        None
    """
    base: dict[str, Any] = {
        "id": new_id(IdentityHistoryId),
        "interface_id": iface.id,
        "device_id": iface.device_id,
        "decided_at": at,
        "outcome": ReconcileOutcome.CONFIDENT_RENUMBER,
        "source": IdentityDecisionSource.AUTOMATIC,
        "previous_if_index": 3,
        "observed_if_index": 7,
        "score": 0.95,
        "runner_up_score": 0.4,
        "evidence": ("ifName equal", "ifAlias equal"),
    }
    return IdentityHistoryEntry(**base | overrides)


def audit(at: datetime, **overrides: Any) -> AuditEvent:
    """Build an audit event.

    Args:
        at: Event time.
        **overrides: Field values to replace.

    Returns:
        AuditEvent: The event.

    Raises:
        None
    """
    base: dict[str, Any] = {
        "id": new_id(AuditEventId),
        "occurred_at": at,
        "actor": "alice",
        "action": "device.update",
        "object_type": "device",
        "object_id": None,
        "outcome": AuditOutcome.SUCCESS,
        "summary": "comment changed",
    }
    return AuditEvent(**base | overrides)


def icmp(device_id: UUID, collector_id: UUID, at: datetime, **overrides: Any) -> IcmpObservation:
    """Build an ICMP observation with one lost probe (``None`` RTT).

    Args:
        device_id: The device.
        collector_id: The collector.
        at: Timestamp.
        **overrides: Field values to replace.

    Returns:
        IcmpObservation: The observation.

    Raises:
        None
    """
    base: dict[str, Any] = {
        "observation_id": new_id(ObservationId),
        "poll_id": new_id(PollId),
        "collector_id": collector_id,
        "device_id": device_id,
        "timestamp": at,
        "address_family": AddressFamily.IPV4,
        "target_address": "192.0.2.1",
        "rtts_us": (900, None, 1100, 1000, 1000),
        "state": IcmpState.IMPAIRED,
        "quality": Quality.PARTIAL_LOSS | Quality.RETRIED,
        "min_rtt_us": 900,
        "avg_rtt_us": 1000,
        "median_rtt_us": 1000,
        "max_rtt_us": 1100,
        "jitter_us": 100,
    }
    return IcmpObservation(**base | overrides)


def snmp(
    device_id: UUID, collector_id: UUID, at: datetime, **overrides: Any
) -> SnmpDeviceObservation:
    """Build an SNMP device observation without an uptime reading.

    Args:
        device_id: The device.
        collector_id: The collector.
        at: Timestamp.
        **overrides: Field values to replace.

    Returns:
        SnmpDeviceObservation: The observation.

    Raises:
        None
    """
    base: dict[str, Any] = {
        "observation_id": new_id(ObservationId),
        "poll_id": new_id(PollId),
        "collector_id": collector_id,
        "device_id": device_id,
        "timestamp": at,
        "snmp_version": SnmpVersion.V2C,
        "state": SnmpState.RESPONDING,
        "uptime_continuity": UptimeContinuity.UNKNOWN,
        "request_count": 3,
        "response_count": 3,
        "timeout_count": 0,
        "retry_count": 0,
        "elapsed_ms": 42,
    }
    return SnmpDeviceObservation(**base | overrides)


def sample(
    device_id: UUID, interface_id: UUID, collector_id: UUID, at: datetime, **overrides: Any
) -> InterfaceObservation:
    """Build a valid-baseline interface sample with a 64-bit counter above 2^53.

    Args:
        device_id: The device.
        interface_id: The interface.
        collector_id: The collector.
        at: Timestamp.
        **overrides: Field values to replace.

    Returns:
        InterfaceObservation: The sample.

    Raises:
        None
    """
    base: dict[str, Any] = {
        "observation_id": new_id(ObservationId),
        "poll_id": new_id(PollId),
        "collector_id": collector_id,
        "device_id": device_id,
        "interface_id": interface_id,
        "timestamp": at,
        "if_index": 7,
        "baseline_state": BaselineState.VALID,
        "in_octets": (1 << 60) + 12345,
        "out_octets": 99,
        "in_bps": 1_500_000.5,
        "out_bps": 250.0,
        "sample_elapsed_us": 60_000_000,
        "counter_family": CounterFamily.HC64,
    }
    return InterfaceObservation(**base | overrides)


def poll(device_id: UUID, collector_id: UUID, at: datetime, **overrides: Any) -> DevicePoll:
    """Build a completed poll record.

    Args:
        device_id: The device.
        collector_id: The collector.
        at: Scheduled time.
        **overrides: Field values to replace.

    Returns:
        DevicePoll: The poll.

    Raises:
        None
    """
    base: dict[str, Any] = {
        "observation_id": new_id(ObservationId),
        "poll_id": new_id(PollId),
        "collector_id": collector_id,
        "device_id": device_id,
        "scheduled_at": at,
        "configured_interval_s": 60,
        "effective_interval_s": 60,
        "reachability": ReachabilityStrategy.ICMP_GATED,
        "execution": PollExecution.COMPLETE,
        "started_at": at,
        "completed_at": at + timedelta(seconds=2),
    }
    return DevicePoll(**base | overrides)


def status(
    device_id: UUID, interface_id: UUID, collector_id: UUID, at: datetime, **overrides: Any
) -> InterfaceStatusObservation:
    """Build an interface status record: oper down, the first record since the collector started.

    Args:
        device_id: The device.
        interface_id: The interface.
        collector_id: The collector.
        at: Stage 1 time.
        **overrides: Field values to replace.

    Returns:
        InterfaceStatusObservation: The record.

    Raises:
        None
    """
    base: dict[str, Any] = {
        "observation_id": new_id(ObservationId),
        "poll_id": new_id(PollId),
        "collector_id": collector_id,
        "device_id": device_id,
        "interface_id": interface_id,
        "timestamp": at,
        "if_index": 7,
        "admin_status": IfAdminStatus.UP,
        "oper_status": IfOperStatus.DOWN,
        "baseline": True,
    }
    return InterfaceStatusObservation(**base | overrides)


def health(collector_id: UUID, at: datetime, **overrides: Any) -> CollectorHealthObservation:
    """Build a collector health snapshot.

    Args:
        collector_id: The collector.
        at: Timestamp.
        **overrides: Field values to replace.

    Returns:
        CollectorHealthObservation: The snapshot.

    Raises:
        None
    """
    base: dict[str, Any] = {
        "observation_id": new_id(ObservationId),
        "collector_id": collector_id,
        "timestamp": at,
        "state": CollectorState.HEALTHY,
        "software_version": "0.0.0-test",
        "queue_depth": 3,
        "queue_capacity": 100,
    }
    return CollectorHealthObservation(**base | overrides)


_ROW_ID_NAMES = {"rowid", "row_id", "pk", "oid", "_rowid_", "serial"}


def assert_no_row_ids(value: object, path: str = "value") -> None:
    """Assert a read model exposes UUIDs, never backend row IDs (DB-007, API-002).

    Walks dataclasses, tuples, and pages recursively. Every field named
    ``id`` or ending in ``_id`` must be a UUID or ``None``, and no field may
    carry a row-ID name.

    Args:
        value: A domain record, read model, page, or tuple of them.
        path: Where ``value`` sits, for the failure message.

    Returns:
        None

    Raises:
        AssertionError: If a row ID or a non-UUID identifier is found.
    """
    if isinstance(value, tuple | list):
        for i, item in enumerate(value):
            assert_no_row_ids(item, f"{path}[{i}]")
        return
    if not is_dataclass(value):
        return
    for f in fields(value):
        item = getattr(value, f.name)
        assert f.name.lower() not in _ROW_ID_NAMES, f"{path}.{f.name} looks like a row ID"
        if f.name == "id" or f.name.endswith("_id"):
            assert item is None or isinstance(item, UUID), f"{path}.{f.name} is not a UUID"
        assert_no_row_ids(item, f"{path}.{f.name}")
