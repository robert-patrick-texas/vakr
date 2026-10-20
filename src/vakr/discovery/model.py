"""What SNMP discovery found: system facts, interfaces, and per-table completeness.

A discovery result is an observation, not inventory. It records what one
agent said at one time, keyed by the ``ifIndex`` the agent used then. That
index is only an observed attribute (invariant 6): identity reconciliation
(ADR-015, milestone M4) turns these rows into persistent interface UUIDs.

Completeness is explicit at three levels (DISC-008, SNMP-014):

- each subtable (``system``, ``ifTable``, ``ifXTable``, ``ipAddressTable``,
  ``ipAddrTable``) is ``complete``, ``partial``, ``failed``, ``absent`` (the
  agent does not implement it), or ``skipped``;
- each interface lists the required fields the agent did not return;
- capability probes (HC counters, ``ifCounterDiscontinuityTime``) are
  ``True``, ``False``, or ``None`` when the probe itself failed, so an
  unknown capability is never mistaken for an unsupported one.

Dependencies:
    dataclasses, datetime, enum: Immutable records.
    vakr.domain: IF-MIB status enums and quality flags.
    vakr.ports.snmp: Errors and effort counts.

Safety:
    No field holds a credential.

Example:
    >>> SubtableStatus.ABSENT.usable, SubtableStatus.PARTIAL.usable
    (True, False)
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from vakr.domain.enums import IfAdminStatus, IfOperStatus
from vakr.domain.quality import Quality
from vakr.ports.snmp import SnmpError, SnmpStats


class SubtableStatus(StrEnum):
    """How completely one MIB table was read."""

    COMPLETE = "complete"  # every column walked to its end
    PARTIAL = "partial"  # some rows or columns arrived, some did not
    FAILED = "failed"  # nothing arrived
    ABSENT = "absent"  # walked to the end and empty: the agent does not implement it
    SKIPPED = "skipped"  # not needed, e.g. ipAddrTable when ipAddressTable had IPv4

    @property
    def usable(self) -> bool:
        """Whether the table's answer is complete, even if empty.

        Args:
            None

        Returns:
            bool: True for complete, absent, and skipped tables.

        Raises:
            None
        """
        return self in (SubtableStatus.COMPLETE, SubtableStatus.ABSENT, SubtableStatus.SKIPPED)


@dataclass(frozen=True, slots=True)
class SubtableResult:
    """The completeness of one table.

    Attributes:
        name: Table name, such as ``ifXTable``.
        status: How completely it was read.
        rows: Varbinds received from it.
        failed_columns: Columns whose walk stopped early.
        error: The first error, if any.
    """

    name: str
    status: SubtableStatus
    rows: int = 0
    failed_columns: tuple[str, ...] = ()
    error: SnmpError | None = None


@dataclass(frozen=True, slots=True)
class SystemFacts:
    """The system group (DISC-004); ``None`` means not returned.

    Attributes:
        sys_descr: ``sysDescr.0``.
        sys_object_id: ``sysObjectID.0`` in dotted form.
        sys_uptime_ticks: ``sysUpTime.0``: management-agent uptime in
            hundredths of a second, not proof of chassis uptime (UPTIME-003).
        sys_contact: ``sysContact.0``.
        sys_name: ``sysName.0``.
        sys_location: ``sysLocation.0``.
        missing: Objects the agent did not return.
    """

    sys_descr: str | None = None
    sys_object_id: str | None = None
    sys_uptime_ticks: int | None = None
    sys_contact: str | None = None
    sys_name: str | None = None
    sys_location: str | None = None
    missing: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DiscoveredInterface:
    """One ``ifTable`` row as the agent described it (DISC-005).

    Attributes:
        if_index: The ifIndex the agent used; observed, never identity.
        if_descr: ``ifDescr``.
        if_name: ``ifName``.
        if_alias: ``ifAlias``, the operator description on the device.
        if_type: IANAifType number.
        if_mtu: ``ifMtu`` in octets.
        if_speed: ``ifSpeed`` in bits per second (4,294,967,295 = saturated).
        if_high_speed: ``ifHighSpeed`` in megabits per second.
        mac: ``ifPhysAddress`` as lower-case colon hex; ``None`` when empty.
        admin_status: ``ifAdminStatus``.
        oper_status: ``ifOperStatus``.
        last_change_ticks: ``ifLastChange`` in TimeTicks.
        addresses: IPv4 and IPv6 addresses as ``address/prefix`` (DISC-006).
        hc_octets: ifHCInOctets exists; ``None`` if the probe failed.
        hc_packets: ifHCInUcastPkts exists; ``None`` if the probe failed.
        discontinuity: ifCounterDiscontinuityTime exists; ``None`` if unknown.
        missing: Required fields the agent did not return.
        quality: ``MISSING_FIELDS`` when ``missing`` is not empty.
    """

    if_index: int
    if_descr: str | None = None
    if_name: str | None = None
    if_alias: str | None = None
    if_type: int | None = None
    if_mtu: int | None = None
    if_speed: int | None = None
    if_high_speed: int | None = None
    mac: str | None = None
    admin_status: IfAdminStatus | None = None
    oper_status: IfOperStatus | None = None
    last_change_ticks: int | None = None
    addresses: tuple[str, ...] = ()
    hc_octets: bool | None = None
    hc_packets: bool | None = None
    discontinuity: bool | None = None
    missing: tuple[str, ...] = ()
    quality: Quality = Quality.NONE

    @property
    def up_up(self) -> bool:
        """Whether discovery saw the interface admin up and oper up.

        Args:
            None

        Returns:
            bool: True only when both statuses are ``UP``.

        Raises:
            None
        """
        return self.admin_status is IfAdminStatus.UP and self.oper_status is IfOperStatus.UP


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """Everything one discovery run learned from one agent.

    Attributes:
        target: The agent as ``address:port``.
        started_at: When discovery started (UTC).
        completed_at: When it ended (UTC).
        elapsed_s: Duration on the monotonic clock.
        system: The system group.
        interfaces: Interfaces in ifIndex order.
        subtables: Completeness of each table.
        stats: SNMP effort spent.
        quality: ``PARTIAL_RESPONSE`` and/or ``MISSING_FIELDS``, plus
            ``RETRIED`` and ``REQUEST_SIZE_REDUCED``.
    """

    target: str
    started_at: datetime
    completed_at: datetime
    elapsed_s: float
    system: SystemFacts
    interfaces: tuple[DiscoveredInterface, ...]
    subtables: tuple[SubtableResult, ...]
    stats: SnmpStats = field(default_factory=SnmpStats)
    quality: Quality = Quality.NONE

    @property
    def answered(self) -> bool:
        """Whether the agent answered at all (SNMP-013).

        Args:
            None

        Returns:
            bool: True when at least one response arrived.

        Raises:
            None
        """
        return self.stats.responses > 0

    @property
    def complete(self) -> bool:
        """Whether every table was read completely.

        Args:
            None

        Returns:
            bool: True when every subtable is usable and nothing is missing.

        Raises:
            None
        """
        return all(s.status.usable for s in self.subtables) and not self.quality & (
            Quality.PARTIAL_RESPONSE | Quality.MISSING_FIELDS
        )

    @property
    def addresses_complete(self) -> bool:
        """Whether the interface addresses are known completely (DISC-T04).

        Args:
            None

        Returns:
            bool: True when an IP-MIB table answered completely.

        Raises:
            None
        """
        ip = [s for s in self.subtables if s.name in ("ipAddressTable", "ipAddrTable")]
        return bool(ip) and all(s.status.usable for s in ip)

    def subtable(self, name: str) -> SubtableResult | None:
        """Return one table's completeness by name.

        Args:
            name: Such as ``ifXTable``.

        Returns:
            SubtableResult | None: The entry, or ``None`` if not attempted.

        Raises:
            None
        """
        return next((s for s in self.subtables if s.name == name), None)
