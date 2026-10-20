"""Poll targets: which devices are scheduled, how often, and with which strategy (9.2, POLICY-007).

The scheduler works on ``PollTarget`` values, not on raw inventory rows. A
target pairs a ``Device`` with the interval and reachability strategy that
apply to it: the device's own override when it has one, otherwise the
configured ``[polling]`` defaults. Disabled and retired devices get no
target, so they are not polled and their history is kept (9.2, 7.5).
Devices in maintenance are still polled; their polls carry the
``MAINTENANCE`` flag (E.2 row 2).

Intervals outside the policy limits of E.18 (30 s to 1 h) are clamped. The
configured value stays on the poll record and the clamped value is the
effective one, so the difference is visible instead of a device being
polled every second or silently dropped (11.4, ADAPT-004).

Dependencies:
    vakr.domain.inventory: The ``Device`` record.
    vakr.policy.settings: The ``polling_interval`` limits (E.18).

Safety:
    Pure. Targets carry the credential *reference*, never a secret.

Example:
    >>> PollDefaults().interval_s, PollDefaults().strategy.value
    (60, 'icmp_gated')
    >>> clamp_interval(5), clamp_interval(7200)
    (30, 3600)
"""

from collections.abc import Iterable
from dataclasses import dataclass

from vakr.domain.enums import AdminState, ReachabilityStrategy
from vakr.domain.ids import DeviceId
from vakr.domain.inventory import Device
from vakr.policy.settings import SETTING_LIMITS

_LIMIT = SETTING_LIMITS["polling_interval"]
MIN_INTERVAL_S = int(_LIMIT.low)
MAX_INTERVAL_S = int(_LIMIT.high)
_UNSCHEDULED = frozenset({AdminState.DISABLED, AdminState.RETIRED})


@dataclass(frozen=True, slots=True)
class PollDefaults:
    """Configured defaults for devices without an override (``[polling]``).

    Attributes:
        interval_s: ``polling.default_interval`` in whole seconds.
        strategy: ``polling.reachability_strategy``.
    """

    interval_s: int = 60
    strategy: ReachabilityStrategy = ReachabilityStrategy.ICMP_GATED


@dataclass(frozen=True, slots=True)
class PollTarget:
    """One device the scheduler polls, with its resolved settings.

    Attributes:
        device: The inventory record; the steps read address and credential
            reference from it.
        configured_interval_s: Interval from the device override or default.
        effective_interval_s: Interval in force after clamping (E.18).
        strategy: Reachability strategy in force (ICMP-006..014).
    """

    device: Device
    configured_interval_s: int
    effective_interval_s: int
    strategy: ReachabilityStrategy

    @property
    def device_id(self) -> DeviceId:
        """Return the device UUID.

        Args:
            None

        Returns:
            DeviceId: ``device.id``.

        Raises:
            None
        """
        return self.device.id

    @property
    def maintenance(self) -> bool:
        """Report whether the device is in maintenance (E.2 row 2).

        Args:
            None

        Returns:
            bool: True when polls should carry the ``MAINTENANCE`` flag.

        Raises:
            None
        """
        return self.device.admin_state is AdminState.MAINTENANCE


def clamp_interval(seconds: int) -> int:
    """Clamp an interval to the E.18 ``polling_interval`` range.

    Args:
        seconds: Requested interval in seconds.

    Returns:
        int: The interval limited to 30 s .. 1 h.

    Raises:
        None
    """
    return min(max(seconds, MIN_INTERVAL_S), MAX_INTERVAL_S)


def target_for(device: Device, defaults: PollDefaults) -> PollTarget | None:
    """Resolve one device into a poll target.

    Args:
        device: The inventory record.
        defaults: Configured defaults.

    Returns:
        PollTarget | None: The target, or ``None`` for a disabled or
        retired device, which is not polled (9.2).

    Raises:
        None
    """
    if device.admin_state in _UNSCHEDULED:
        return None
    configured = device.poll_interval_s or defaults.interval_s
    strategy = device.reachability or defaults.strategy
    return PollTarget(device, configured, clamp_interval(configured), strategy)


def targets_from_devices(
    devices: Iterable[Device], defaults: PollDefaults
) -> tuple[PollTarget, ...]:
    """Resolve an inventory listing into poll targets.

    Args:
        devices: Every device the collector is responsible for.
        defaults: Configured defaults.

    Returns:
        tuple[PollTarget, ...]: Targets for enabled and maintenance devices,
        in input order.

    Raises:
        None
    """
    resolved = (target_for(device, defaults) for device in devices)
    return tuple(target for target in resolved if target is not None)
