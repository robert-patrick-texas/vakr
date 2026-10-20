"""Current-state projection rules shared by every backend (STATE-001..003, DB-005).

A backend finds candidate observations its own way; these functions decide
which one is current and compute age, freshness, and quality, so SQLite,
TimescaleDB, and ClickHouse report the same state for the same data:

- *evidence* is any observation not flagged ``NOT_ATTEMPTED``; a poll the
  collector never made says nothing about the target (invariant 2);
- the *latest* observation has the greatest ``(timestamp, observation_id)``,
  so equal timestamps resolve the same way everywhere;
- freshness uses the effective interval of the device's latest poll, or of
  the sample's own poll for interfaces, else the 60 s default (E.3).

Dependencies:
    vakr.domain.state: The read models and freshness rule.

Safety:
    Pure functions; nothing is fetched or mutated.

Example:
    >>> latest_evidence([]) is None
    True
"""

from collections.abc import Iterable
from datetime import datetime

from vakr.domain.ids import CollectorId, DeviceId
from vakr.domain.observations import (
    DevicePoll,
    IcmpObservation,
    InterfaceObservation,
    Observation,
    SnmpDeviceObservation,
)
from vakr.domain.quality import Quality
from vakr.domain.state import (
    DEFAULT_EFFECTIVE_INTERVAL_S,
    CurrentDeviceState,
    CurrentInterfaceState,
    freshness_at,
)
from vakr.domain.timestamps import require_utc


def is_evidence(observation: Observation) -> bool:
    """Tell whether an observation is real evidence about its target.

    Args:
        observation: Any observation.

    Returns:
        bool: ``False`` when it is flagged ``NOT_ATTEMPTED``.

    Raises:
        None
    """
    return Quality.NOT_ATTEMPTED not in observation.quality


def latest[T: Observation](observations: Iterable[T]) -> T | None:
    """Return the observation with the greatest ``(timestamp, observation_id)``.

    Args:
        observations: Candidates.

    Returns:
        T | None: The latest, or ``None`` when there are none.

    Raises:
        None
    """
    best: T | None = None
    for o in observations:
        if best is None or (o.timestamp, o.observation_id) > (best.timestamp, best.observation_id):
            best = o
    return best


def latest_evidence[T: Observation](observations: Iterable[T]) -> T | None:
    """Return the latest observation that is evidence.

    Args:
        observations: Candidates.

    Returns:
        T | None: The latest evidence, or ``None``.

    Raises:
        None
    """
    return latest(o for o in observations if is_evidence(o))


def build_device_state(
    device_id: DeviceId,
    collector_id: CollectorId,
    *,
    icmp: IcmpObservation | None,
    snmp: SnmpDeviceObservation | None,
    poll: DevicePoll | None,
    now: datetime,
) -> CurrentDeviceState | None:
    """Build a device's current state from its latest evidence.

    Args:
        device_id: The device.
        collector_id: The collector.
        icmp: Latest ICMP evidence, if any.
        snmp: Latest SNMP evidence, if any.
        poll: Latest poll record of any outcome, if any.
        now: Reference time (UTC).

    Returns:
        CurrentDeviceState | None: ``None`` when there is no evidence.

    Raises:
        ValidationError: If ``now`` is not UTC.
    """
    require_utc(now, "now")
    evidence = [o for o in (icmp, snmp) if o is not None]
    if not evidence:
        return None
    observed_at = max(o.timestamp for o in evidence)
    interval = poll.effective_interval_s if poll is not None else DEFAULT_EFFECTIVE_INTERVAL_S
    quality = Quality.NONE
    for o in evidence:
        quality |= o.quality
    return CurrentDeviceState(
        device_id=device_id,
        collector_id=collector_id,
        observed_at=observed_at,
        age=now - observed_at,
        freshness=freshness_at(now - observed_at, interval),
        effective_interval_s=interval,
        quality=quality,
        icmp=icmp,
        snmp=snmp,
        poll=poll,
    )


def build_interface_state(
    observation: InterfaceObservation, *, effective_interval_s: int | None, now: datetime
) -> CurrentInterfaceState:
    """Build an interface's current state from its latest sample.

    Args:
        observation: Latest interface evidence.
        effective_interval_s: Interval of the sample's poll, if that poll is stored.
        now: Reference time (UTC).

    Returns:
        CurrentInterfaceState: The state.

    Raises:
        ValidationError: If ``now`` is not UTC.
    """
    require_utc(now, "now")
    interval = effective_interval_s or DEFAULT_EFFECTIVE_INTERVAL_S
    age = now - observation.timestamp
    return CurrentInterfaceState(
        interface_id=observation.interface_id,
        device_id=observation.device_id,
        collector_id=observation.collector_id,
        observed_at=observation.timestamp,
        age=age,
        freshness=freshness_at(age, interval),
        effective_interval_s=interval,
        quality=observation.quality,
        observation=observation,
    )
