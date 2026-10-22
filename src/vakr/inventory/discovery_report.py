"""What one recorded discovery changed, and the counter capabilities it probed (DISC-009).

``InventoryService.record_discovery_report`` answers with a
``DiscoveryReport``: the new poll plan, the identity changes the write made
(``new``, ``missing``, ``review_opened``, and so on), and how many
interfaces' counter capabilities differ from the previous discovery of the
same device in this process. A manual rediscovery shows these counts to the
administrator who asked for it (DISC-T03).

``CapabilityMemory`` keeps the capabilities each device's last discovery
probed, so plans keep using 64-bit counters and the discontinuity time
where the agent has them without re-probing every cycle (SNMP-010).

Dependencies:
    threading: The memory's lock.
    vakr.collectors.snmp.plan, vakr.discovery.model: Plans and results.
    vakr.inventory.planning: ``Capabilities``.

Safety:
    Thread-safe; memory is bounded by the number of devices and interfaces.

Example:
    >>> CapabilityMemory().of(UUID(int=1))
    {}
"""

import threading
from dataclasses import dataclass
from uuid import UUID

from vakr.collectors.snmp.plan import PollPlan
from vakr.discovery.model import DiscoveryResult
from vakr.domain.ids import DeviceId
from vakr.inventory.planning import Capabilities


@dataclass(frozen=True, slots=True)
class DiscoveryReport:
    """The outcome of recording one discovery.

    Attributes:
        plan: The device's new poll plan.
        changes: Count per identity change, such as ``("new", 2)``; empty
            when the rediscovery changed no identity.
        capability_changes: Interfaces whose probed capabilities differ from
            the previous discovery in this process; 0 after a restart.
    """

    plan: PollPlan
    changes: tuple[tuple[str, int], ...] = ()
    capability_changes: int = 0


class CapabilityMemory:
    """Probed counter capabilities per device, from each device's latest discovery."""

    def __init__(self) -> None:
        """Start empty.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._lock = threading.Lock()
        self._caps: dict[UUID, dict[int, Capabilities]] = {}

    def of(self, device_id: UUID) -> dict[int, Capabilities]:
        """Return a copy of one device's capabilities by ``ifIndex``.

        Args:
            device_id: The device.

        Returns:
            dict[int, Capabilities]: Empty when the device was not discovered yet.

        Raises:
            None
        """
        with self._lock:
            return dict(self._caps.get(device_id, {}))

    def remember(self, device_id: DeviceId, result: DiscoveryResult) -> int:
        """Keep a discovery's capabilities and count the interfaces whose capabilities changed.

        Args:
            device_id: The device.
            result: What discovery found.

        Returns:
            int: Interfaces known before whose capabilities differ now (DISC-009).

        Raises:
            None
        """
        caps = {
            r.if_index: Capabilities(r.hc_octets, r.hc_packets, r.discontinuity)
            for r in result.interfaces
        }
        with self._lock:
            before = self._caps.get(device_id, {})
            self._caps[device_id] = caps
        return sum(1 for i, c in caps.items() if i in before and before[i] != c)
