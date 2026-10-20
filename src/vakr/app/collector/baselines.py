"""Rate baselines: the latest accepted sample of each interface (9.7, RATE-004).

A rate is the difference between this poll's cumulative counters and the
previous poll's, so the collector must remember the previous sample of every
interface it polls, keyed by the persistent interface UUID, never by
``ifIndex`` (invariant 6), and owned by this collector alone (6.2, MPOL-003).
It also remembers each device's last ``sysUpTime`` reading, because a rate
is only trusted across an agent that ran continuously (E.5, E.17 #10).

Both maps are checkpointed every minute (``vakr.app.collector.checkpoint``,
encoded by ``baseline_codec``) and loaded at startup, so a restart costs at
most one rate interval. Each
baseline records the kernel boot it was taken in: within one boot the
monotonic clock is shared by every process, so elapsed time stays monotonic
across a service restart (RATE-001); across a reboot only wall time remains.

Dependencies:
    vakr.logic.counters: ``CounterSample``.
    vakr.collectors.snmp: ``UptimeReading``.

Safety:
    Bounded by ``MAX_BASELINES`` and ``MAX_UPTIME_READINGS``. Holds counters
    and timestamps only; nothing secret.

Example:
    >>> from uuid import UUID
    >>> store = BaselineStore()
    >>> len(store), store.uptime(DeviceId(UUID(int=1))) is None
    (0, True)
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from vakr.app.collector.timings import MAX_BASELINES, MAX_UPTIME_READINGS
from vakr.collectors.snmp import UptimeReading
from vakr.domain.ids import DeviceId, InterfaceId
from vakr.logic.counters import CounterSample


@dataclass(frozen=True, slots=True)
class Baseline:
    """The latest accepted sample of one interface.

    Attributes:
        device_id: The interface's device.
        if_index: The ``ifIndex`` the counters were read from.
        sample: The cumulative counters.
        sampled_at: When they were read (UTC).
        sampled_monotonic: The same moment on the monotonic clock.
        boot_id: The kernel boot the monotonic reading belongs to.
    """

    device_id: DeviceId
    if_index: int
    sample: CounterSample
    sampled_at: datetime
    sampled_monotonic: float
    boot_id: str

    def elapsed_to(self, at: datetime, monotonic: float, boot_id: str) -> float:
        """Return the actual seconds from this sample to a later one (RATE-001).

        Args:
            at: The later sample's UTC time.
            monotonic: Its monotonic time.
            boot_id: Its kernel boot.

        Returns:
            float: Monotonic difference within one boot, else wall-clock difference.

        Raises:
            None
        """
        if boot_id and boot_id == self.boot_id:
            return monotonic - self.sampled_monotonic
        return (at - self.sampled_at).total_seconds()


class BaselineStore:
    """Per-interface baselines and per-device uptime readings of one collector.

    Attributes:
        refused: Baselines not kept because the store was full.
    """

    def __init__(self) -> None:
        """Create an empty store.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._baselines: dict[InterfaceId, Baseline] = {}
        self._uptime: dict[DeviceId, UptimeReading] = {}
        self.refused = 0

    def __len__(self) -> int:
        """Return how many interfaces have a baseline.

        Args:
            None

        Returns:
            int: The count.

        Raises:
            None
        """
        return len(self._baselines)

    def get(self, interface_id: InterfaceId) -> Baseline | None:
        """Return an interface's baseline.

        Args:
            interface_id: The persistent interface UUID.

        Returns:
            Baseline | None: The latest accepted sample, if any.

        Raises:
            None
        """
        return self._baselines.get(interface_id)

    def put(self, interface_id: InterfaceId, baseline: Baseline) -> None:
        """Remember an interface's latest sample, within the bound.

        Args:
            interface_id: The persistent interface UUID.
            baseline: The sample.

        Returns:
            None

        Raises:
            None
        """
        if interface_id not in self._baselines and len(self._baselines) >= MAX_BASELINES:
            self.refused += 1
            return
        self._baselines[interface_id] = baseline

    def uptime(self, device_id: DeviceId) -> UptimeReading | None:
        """Return a device's last successful ``sysUpTime`` reading.

        Args:
            device_id: The device.

        Returns:
            UptimeReading | None: The reading, if any.

        Raises:
            None
        """
        return self._uptime.get(device_id)

    def set_uptime(self, device_id: DeviceId, reading: UptimeReading) -> None:
        """Remember a device's latest ``sysUpTime`` reading, within the bound.

        Args:
            device_id: The device.
            reading: The reading.

        Returns:
            None

        Raises:
            None
        """
        if device_id in self._uptime or len(self._uptime) < MAX_UPTIME_READINGS:
            self._uptime[device_id] = reading

    def prune(self, devices: Iterable[DeviceId], interfaces: Iterable[InterfaceId]) -> int:
        """Forget everything not in the current plans, so memory follows inventory.

        Args:
            devices: Devices still scheduled.
            interfaces: Interfaces still in a poll plan.

        Returns:
            int: Baselines forgotten.

        Raises:
            None
        """
        keep_d, keep_i = set(devices), set(interfaces)
        gone = [iid for iid in self._baselines if iid not in keep_i]
        for iid in gone:
            del self._baselines[iid]
        for did in [d for d in self._uptime if d not in keep_d]:
            del self._uptime[did]
        return len(gone)

    def baselines(self) -> Mapping[InterfaceId, Baseline]:
        """Return a read-only view of every baseline, for the checkpoint.

        Args:
            None

        Returns:
            Mapping[InterfaceId, Baseline]: Baselines by interface.

        Raises:
            None
        """
        return MappingProxyType(self._baselines)

    def uptimes(self) -> Mapping[DeviceId, UptimeReading]:
        """Return a read-only view of every uptime reading, for the checkpoint.

        Args:
            None

        Returns:
            Mapping[DeviceId, UptimeReading]: Readings by device.

        Raises:
            None
        """
        return MappingProxyType(self._uptime)
