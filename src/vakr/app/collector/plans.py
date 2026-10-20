"""The collector's working copy of every device's poll plan (SNMP-004, SNMP-010, DISC-009).

Inventory owns the plans (6.2); the collector keeps a working copy per
device so a poll never waits for the metadata database. The copy also
carries what polling learns between discoveries: a capability the agent
turned out not to have (``CapabilityChange``) is folded in, so an
unsupported OID is not asked for every minute, and is forgotten again after
``snmp.capability_reprobe_interval`` (E.9: 7 days).

A device without a plan is polled with an empty plan, which still reads
``sysUpTime.0`` (SNMP reachability and uptime continuity), until discovery
gives it one (DISC-001).

Dependencies:
    vakr.collectors.snmp: Plans and capability changes.
    vakr.logic.capacity: Capacity inputs per interface (RATE-008).

Safety:
    Bounded by inventory: one entry per scheduled device.

Example:
    >>> from uuid import UUID
    >>> book = PlanBook()
    >>> book.plan(DeviceId(UUID(int=1))).interfaces
    ()
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from vakr.collectors.snmp import CapabilityChange, PollPlan, reprobe_due
from vakr.domain.ids import DeviceId, InterfaceId
from vakr.logic.capacity import NO_CAPACITY, CapacityInputs

EMPTY_PLAN = PollPlan(())
"""The plan of an undiscovered device: ``sysUpTime.0`` only."""


@dataclass(frozen=True, slots=True)
class DevicePlan:
    """One device's plan as inventory gave it, and as polling refined it.

    Attributes:
        base: The plan from inventory or the last discovery.
        current: ``base`` plus capabilities learned since ``learned_at``.
        learned_at: When the learned capabilities started accumulating (UTC).
        capacity: Capacity inputs by interface, for RATE-008/009.
    """

    base: PollPlan
    current: PollPlan
    learned_at: datetime
    capacity: Mapping[InterfaceId, CapacityInputs] = field(default_factory=dict)


class PlanBook:
    """Working plans by device; mutated only from the event loop."""

    def __init__(self) -> None:
        """Create an empty book.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._plans: dict[DeviceId, DevicePlan] = {}

    def __len__(self) -> int:
        """Return how many devices have a plan.

        Args:
            None

        Returns:
            int: The count.

        Raises:
            None
        """
        return len(self._plans)

    def has(self, device_id: DeviceId) -> bool:
        """Report whether a device has a plan.

        Args:
            device_id: The device.

        Returns:
            bool: True once inventory or discovery gave one.

        Raises:
            None
        """
        return device_id in self._plans

    def plan(self, device_id: DeviceId) -> PollPlan:
        """Return the plan to poll a device with now.

        Args:
            device_id: The device.

        Returns:
            PollPlan: The refined plan, or ``EMPTY_PLAN`` before discovery.

        Raises:
            None
        """
        entry = self._plans.get(device_id)
        return EMPTY_PLAN if entry is None else entry.current

    def capacity(self, device_id: DeviceId, interface_id: InterfaceId) -> CapacityInputs:
        """Return what is known about an interface's capacity.

        Args:
            device_id: The device.
            interface_id: The interface.

        Returns:
            CapacityInputs: The inputs, or ``NO_CAPACITY``.

        Raises:
            None
        """
        entry = self._plans.get(device_id)
        return NO_CAPACITY if entry is None else entry.capacity.get(interface_id, NO_CAPACITY)

    def adopt(self, device_id: DeviceId, plan: PollPlan, now: datetime) -> None:
        """Adopt a plan from inventory or discovery, keeping what polling learned if unchanged.

        Args:
            device_id: The device.
            plan: The plan inventory or discovery returned.
            now: Current time (UTC).

        Returns:
            None

        Raises:
            None
        """
        old = self._plans.get(device_id)
        if old is not None and old.base == plan:
            return
        capacity = {} if old is None else old.capacity
        self._plans[device_id] = DevicePlan(plan, plan, now, capacity)

    def set_capacity(
        self, device_id: DeviceId, capacity: Mapping[InterfaceId, CapacityInputs]
    ) -> None:
        """Replace a device's capacity inputs.

        Args:
            device_id: The device.
            capacity: Inputs by interface.

        Returns:
            None

        Raises:
            None
        """
        entry = self._plans.get(device_id)
        if entry is not None:
            self._plans[device_id] = replace(entry, capacity=dict(capacity))

    def learn(self, device_id: DeviceId, changes: Iterable[CapabilityChange]) -> None:
        """Fold capabilities learned by a poll into the working plan (SNMP-010).

        Args:
            device_id: The device.
            changes: What the last Stage 2 learned.

        Returns:
            None

        Raises:
            None
        """
        entry, found = self._plans.get(device_id), tuple(changes)
        if entry is not None and found:
            self._plans[device_id] = replace(entry, current=entry.current.with_changes(found))

    def reprobe_expired(self, now: datetime, interval: timedelta) -> int:
        """Forget learned limitations older than the re-probe interval (DISC-009).

        Args:
            now: Current time (UTC).
            interval: ``snmp.capability_reprobe_interval``.

        Returns:
            int: Devices whose plans were reset to try HC counters again.

        Raises:
            None
        """
        due = [d for d, e in self._plans.items() if reprobe_due(e.learned_at, now, interval)]
        for device_id in due:
            entry = self._plans[device_id]
            self._plans[device_id] = replace(entry, current=entry.base.reprobe(), learned_at=now)
        return len(due)

    def retain(self, devices: Iterable[DeviceId]) -> None:
        """Drop plans of devices no longer scheduled.

        Args:
            devices: Devices still scheduled.

        Returns:
            None

        Raises:
            None
        """
        keep = set(devices)
        for device_id in [d for d in self._plans if d not in keep]:
            del self._plans[device_id]

    def interface_ids(self) -> set[InterfaceId]:
        """Return every persistent interface UUID in any plan.

        Args:
            None

        Returns:
            set[InterfaceId]: The UUIDs; unresolved interfaces have none.

        Raises:
            None
        """
        return {
            entry.interface_id
            for plan in self._plans.values()
            for entry in plan.current.interfaces
            if entry.interface_id is not None
        }
