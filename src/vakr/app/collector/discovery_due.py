"""When each device is discovered: soon, then once per interval, failures backing off (DISC-002).

The rules, in the order ``DiscoveryRunner`` applies them:

1. **A failed discovery** (no answer, no session, an inventory error) is
   retried with bounded exponential backoff: ``discovery_retry_min_s`` (60 s)
   after the first failure, doubling after each consecutive one, at most
   ``discovery_retry_max_s`` (1 h). A device that stays unreachable costs
   one short attempt an hour, not one a minute.
2. **A successful discovery** is repeated only after the device's
   rediscovery interval, ``snmp.rediscovery_interval`` (24 h, E.9) or its
   own ``discovery_interval_s``, clamped to E.18's 30 s to 30 d (DISC-002).
   That holds even when inventory still has no poll plan for the device,
   for example an agent that reports no interfaces, so such a device is
   never rediscovered every cycle.
3. **A device never discovered by this process and without a plan** is
   discovered within ``discovery_spread_s`` (60 s), placed by a hash of the
   collector and device, so a restart or a bulk import does not start every
   walk in the same second (DISC-001).
4. **A device with a plan from an earlier run** waits for its next slot on
   an anchored grid: slot ``k`` is ``k * interval + offset`` seconds after the
   Unix epoch, with ``offset`` from the E.11 hash. Rediscoveries are spread
   evenly over the interval and land at the same time of day across
   restarts, the way polling slots do (E.25 #1).

Outcomes are kept in memory only. After a restart the grid and the
first-discovery spread take over again.

Dependencies:
    math: Ceiling division for the next slot.
    vakr.scheduler.hashing: The E.11 hash.
    vakr.policy.settings: The E.18 ``discovery_interval`` limits.

Safety:
    Pure.

Example:
    >>> next_slot_s(1000.0, 60, 15)
    1035
    >>> retry_delay_s(1, 60, 3600), retry_delay_s(4, 60, 3600), retry_delay_s(9, 60, 3600)
    (60.0, 480.0, 3600.0)
"""

import math
from dataclasses import dataclass
from uuid import UUID

from vakr.domain.enums import ReachabilityStrategy
from vakr.domain.inventory import Device
from vakr.policy.settings import SETTING_LIMITS
from vakr.scheduler.hashing import hash64
from vakr.scheduler.targets import PollTarget

_LIMIT = SETTING_LIMITS["discovery_interval"]
MIN_DISCOVERY_S, MAX_DISCOVERY_S = int(_LIMIT.low), int(_LIMIT.high)
DEFAULT_REDISCOVERY_S = 86_400.0
"""``snmp.rediscovery_interval`` when no configuration is loaded yet (E.9: 24 h)."""


@dataclass(slots=True)
class Outcome:
    """What this process knows about a device's discoveries.

    Attributes:
        failures: Consecutive failed discoveries; 0 after a success.
        succeeded: True once a discovery of the device succeeded.
    """

    failures: int = 0
    succeeded: bool = False


def discoverable(target: PollTarget) -> bool:
    """Report whether a device is polled over SNMP at all (ICMP-014).

    Args:
        target: The device.

    Returns:
        bool: False for ``icmp_only`` and for devices without a credential.

    Raises:
        None
    """
    no_snmp = target.strategy is ReachabilityStrategy.ICMP_ONLY
    return not no_snmp and target.device.credential_ref is not None


def clamp_discovery_interval(seconds: int) -> int:
    """Clamp a rediscovery interval to E.18's 30 s to 30 d.

    Args:
        seconds: Requested interval.

    Returns:
        int: The interval in force.

    Raises:
        None
    """
    return min(max(seconds, MIN_DISCOVERY_S), MAX_DISCOVERY_S)


def discovery_interval_s(device: Device, default_s: float) -> int:
    """Return a device's rediscovery interval (DISC-002).

    Args:
        device: The device; ``discovery_interval_s`` overrides the default.
        default_s: ``snmp.rediscovery_interval`` in seconds.

    Returns:
        int: Whole seconds, clamped to E.18.

    Raises:
        None
    """
    return clamp_discovery_interval(device.discovery_interval_s or round(default_s))


def discovery_hash(collector_name: str, device_id: UUID) -> int:
    """Return the E.11 hash that places a device's discoveries.

    Args:
        collector_name: This collector's name.
        device_id: The device.

    Returns:
        int: ``hash64(name + "/" + uuid + "/discovery")``; the suffix keeps
        discovery off the polling offsets.

    Raises:
        None
    """
    return hash64(f"{collector_name}/{device_id}/discovery")


def first_delay_s(collector_name: str, device_id: UUID, spread_s: float) -> float:
    """Return how long after scheduling a device without a plan is discovered.

    Args:
        collector_name: This collector's name.
        device_id: The device.
        spread_s: Window first discoveries are spread over.

    Returns:
        float: Seconds in ``[0, spread_s)``; 0 when ``spread_s`` is 0.

    Raises:
        None
    """
    if spread_s <= 0:
        return 0.0
    millis = round(spread_s * 1000)
    return (discovery_hash(collector_name, device_id) % millis) / 1000


def retry_delay_s(failures: int, low_s: float, high_s: float) -> float:
    """Return the bounded exponential backoff after consecutive failures.

    Args:
        failures: Consecutive failed discoveries, at least 1.
        low_s: Wait after the first failure.
        high_s: Longest wait.

    Returns:
        float: ``min(high_s, low_s * 2 ** (failures - 1))``.

    Raises:
        None
    """
    exponent = min(max(failures, 1) - 1, 32)  # bounded, so a long outage cannot overflow
    return min(high_s, low_s * 2.0**exponent)


def next_slot_s(after_s: float, interval_s: int, offset_s: int) -> int:
    """Return the first grid slot strictly after a time.

    Args:
        after_s: Unix seconds.
        interval_s: Grid spacing.
        offset_s: Offset of slot 0 from the epoch, ``0 <= offset < interval``.

    Returns:
        int: Unix seconds of the slot.

    Raises:
        None
    """
    k = math.floor((after_s - offset_s) / interval_s) + 1
    return k * interval_s + offset_s


def rediscovery_at_s(now_s: float, interval_s: int, collector_name: str, device_id: UUID) -> int:
    """Return a planned device's next grid slot, when this process has not discovered it.

    Args:
        now_s: Unix seconds now.
        interval_s: The device's rediscovery interval.
        collector_name: This collector's name.
        device_id: The device.

    Returns:
        int: Unix seconds of the next rediscovery.

    Raises:
        None
    """
    offset = discovery_hash(collector_name, device_id) % interval_s
    return next_slot_s(now_s, interval_s, offset)
