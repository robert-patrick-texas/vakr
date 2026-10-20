"""What one SNMP poll of one device asks for, and what it remembers between polls.

A ``PollPlan`` lists the known interfaces by their current ``ifIndex``
(SNMP-004: no minute-level walks), whether each needs a current status
check (SNMP-005), whether policy wants its counters, and the capabilities
learned so far: which counter family to read and whether
``ifCounterDiscontinuityTime`` exists (SNMP-010). Stage 2 reports newly
learned capabilities as ``CapabilityChange`` values; ``with_changes`` folds
them in, so an unsupported OID is not requested again every cycle, and
``reprobe`` forgets them on the configured interval (DISC-009, E.9).

Dependencies:
    dataclasses: Immutable plans.
    vakr.discovery: Discovery results to plan from.
    vakr.domain: Counter families and interface IDs.

Safety:
    ``ifIndex`` here is only the address of a row in this cycle; the
    persistent identity is ``interface_id`` (invariant 6).

Example:
    >>> plan = PollPlan((InterfacePlan(3),))
    >>> plan.with_changes([CapabilityChange(3, "hc_octets", False)]).interfaces[0].family.value
    'legacy32'
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from vakr.discovery.model import DiscoveredInterface, DiscoveryResult
from vakr.domain.enums import CounterFamily
from vakr.domain.errors import ValidationError
from vakr.domain.ids import InterfaceId

CAPABILITIES = ("hc_octets", "hc_packets", "discontinuity")
"""Capability names a ``CapabilityChange`` may carry."""


@dataclass(frozen=True, slots=True)
class CapabilityChange:
    """A capability learned during a poll (SNMP-010).

    Attributes:
        if_index: The interface.
        capability: ``hc_octets``, ``hc_packets``, or ``discontinuity``.
        supported: What the agent showed.
    """

    if_index: int
    capability: str
    supported: bool


@dataclass(frozen=True, slots=True)
class InterfacePlan:
    """What to collect from one interface.

    Attributes:
        if_index: The interface's current ifIndex.
        interface_id: Its persistent UUID, once reconciled; ``None`` before.
        check_status: Read ``ifAdminStatus``/``ifOperStatus`` this cycle.
        collect: Policy wants counters; Stage 2 still requires up/up.
        family: Counter family to request (HC preferred, SNMP-007).
        hc_packets: Request HC packet components with HC octets.
        discontinuity: Request ``ifCounterDiscontinuityTime`` (SNMP-009).
    """

    if_index: int
    interface_id: InterfaceId | None = None
    check_status: bool = True
    collect: bool = True
    family: CounterFamily = CounterFamily.HC64
    hc_packets: bool = True
    discontinuity: bool = True

    def __post_init__(self) -> None:
        """Validate the plan entry.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If the ifIndex is invalid, or counters are wanted
                without a status check, which SNMP-006 requires.
        """
        if type(self.if_index) is not int or not 1 <= self.if_index <= 2_147_483_647:
            raise ValidationError("if_index must be 1..2147483647", field="if_index")
        if self.collect and not self.check_status:
            raise ValidationError(
                "counters need a current status check (SNMP-006)", field="collect"
            )

    def learned(self, change: CapabilityChange) -> InterfacePlan:
        """Apply one learned capability.

        Args:
            change: The capability and whether it is supported.

        Returns:
            InterfacePlan: The updated entry.

        Raises:
            None
        """
        if change.capability == "hc_octets":
            family = CounterFamily.HC64 if change.supported else CounterFamily.LEGACY32
            return replace(self, family=family)
        if change.capability == "hc_packets":
            return replace(self, hc_packets=change.supported)
        return replace(self, discontinuity=change.supported)


@dataclass(frozen=True, slots=True)
class PollPlan:
    """The interfaces one poll covers, in ifIndex order.

    Attributes:
        interfaces: One entry per known interface.
    """

    interfaces: tuple[InterfacePlan, ...]

    def __post_init__(self) -> None:
        """Require each ifIndex at most once.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: On a repeated ifIndex.
        """
        indexes = [entry.if_index for entry in self.interfaces]
        if len(set(indexes)) != len(indexes):
            raise ValidationError("a poll plan lists each ifIndex once", field="interfaces")

    def with_changes(self, changes: Iterable[CapabilityChange]) -> PollPlan:
        """Fold learned capabilities into the plan for the next cycle.

        Args:
            changes: Capabilities learned by the last poll.

        Returns:
            PollPlan: The updated plan.

        Raises:
            None
        """
        by_index = {entry.if_index: entry for entry in self.interfaces}
        for change in changes:
            if change.if_index in by_index:
                by_index[change.if_index] = by_index[change.if_index].learned(change)
        return PollPlan(tuple(by_index[entry.if_index] for entry in self.interfaces))

    def reprobe(self) -> PollPlan:
        """Forget learned limitations so the next poll tries HC again (DISC-009).

        Args:
            None

        Returns:
            PollPlan: Every interface back to HC, HC packets, and discontinuity.

        Raises:
            None
        """
        return PollPlan(
            tuple(
                replace(e, family=CounterFamily.HC64, hc_packets=True, discontinuity=True)
                for e in self.interfaces
            )
        )


def reprobe_due(learned_at: datetime, now: datetime, interval: timedelta) -> bool:
    """Report whether learned capabilities are old enough to re-probe (E.9: 7 d).

    Args:
        learned_at: When the capabilities were last learned (UTC).
        now: The current time (UTC).
        interval: ``snmp.capability_reprobe_interval``.

    Returns:
        bool: True when ``interval`` has passed.

    Raises:
        None
    """
    return now - learned_at >= interval


def plan_from_discovery(
    result: DiscoveryResult, collect: Callable[[DiscoveredInterface], bool] | None = None
) -> PollPlan:
    """Plan polls of every discovered interface, using discovery's probes.

    An unknown capability (the probe failed) is tried, not assumed missing,
    so a failed probe never downgrades an interface to 32-bit counters.

    Args:
        result: The discovery result.
        collect: Whether policy wants an interface's counters; all when ``None``.

    Returns:
        PollPlan: One entry per discovered interface.

    Raises:
        None
    """
    entries = []
    for interface in result.interfaces:
        hc = interface.hc_octets is not False
        entries.append(
            InterfacePlan(
                interface.if_index,
                collect=True if collect is None else collect(interface),
                family=CounterFamily.HC64 if hc else CounterFamily.LEGACY32,
                hc_packets=interface.hc_packets is not False,
                discontinuity=interface.discontinuity is not False,
            )
        )
    return PollPlan(tuple(entries))
