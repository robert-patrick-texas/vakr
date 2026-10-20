"""The collector-health observation, written every minute (10.4, OBS-001..003, OBS-007).

Once a minute the collector describes itself: scheduler counts and
lateness, event-loop lag, queue depth, spool fill, and how many devices and
interfaces it polls. The observation goes through the spool like any other,
so its history is queryable, and ``vakr check health`` and the views can
tell a degraded collector from a degraded network (OBS-002, OBS-003).

The state is the worst of three opinions (E.25 #7, E.24): the scheduler's
(``OVERLOADED`` on capacity misses or a blocked queue; ``DEGRADED`` on loop
lag, step errors, or draining), the spool's (``DEGRADED`` at CRITICAL fill
or while the telemetry database refuses writes), and the steps' own count
of collector-side failures (``DEGRADED``).

Dependencies:
    vakr.scheduler.health: The scheduler snapshot.
    vakr.ingest: Spool and ingestion health.
    vakr.domain.polls: ``CollectorHealthObservation``.

Safety:
    Counts and sizes only.

Example:
    >>> worst([CollectorState.HEALTHY, CollectorState.DEGRADED]).value
    'degraded'
"""

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from vakr.domain.enums import CollectorState
from vakr.domain.ids import CollectorId, ObservationId, WorkerId, new_id
from vakr.domain.polls import CollectorHealthObservation
from vakr.ingest import SpoolHealth, collector_state
from vakr.scheduler.health import LAG_DEGRADED_US, SchedulerSnapshot

_RANK = {
    CollectorState.HEALTHY: 0,
    CollectorState.DEGRADED: 1,
    CollectorState.OVERLOADED: 2,
    CollectorState.UNAVAILABLE: 3,
}


def worst(states: Iterable[CollectorState]) -> CollectorState:
    """Return the most severe collector state.

    Args:
        states: Opinions about the collector.

    Returns:
        CollectorState: The worst; ``HEALTHY`` when there are none.

    Raises:
        None
    """
    return max(states, key=lambda s: _RANK.get(s, 1), default=CollectorState.HEALTHY)


def probe_state(snapshot: SchedulerSnapshot) -> CollectorState:
    """Return the collector state the ICMP math should assume (E.1).

    Only conditions that distort every measurement count: overload, which
    delays probes, and event-loop lag. One device's DNS failure or missing
    credential is not a reason to read every other device's lost probes as
    unknown.

    Args:
        snapshot: The latest scheduler snapshot.

    Returns:
        CollectorState: ``OVERLOADED``, ``DEGRADED`` on loop lag, else ``HEALTHY``.

    Raises:
        None
    """
    if snapshot.collector_state() is CollectorState.OVERLOADED:
        return CollectorState.OVERLOADED
    if (snapshot.loop_lag_us or 0) > LAG_DEGRADED_US:
        return CollectorState.DEGRADED
    return CollectorState.HEALTHY


def health_observation(
    collector_id: CollectorId,
    worker_id: WorkerId,
    at: datetime,
    version: str,
    snapshot: SchedulerSnapshot,
    spool: SpoolHealth,
    step_faults: int,
    interfaces: int,
) -> CollectorHealthObservation:
    """Build one collector-health observation.

    Args:
        collector_id: This collector.
        worker_id: This worker process.
        at: Snapshot time (UTC).
        version: The running vakr version.
        snapshot: Scheduler snapshot that closed the period.
        spool: Spool and ingestion health.
        step_faults: Collector-side step failures in the period.
        interfaces: Interfaces the collector samples.

    Returns:
        CollectorHealthObservation: The observation; unmeasured values are ``None``.

    Raises:
        ValidationError: If a value breaks the observation's rules.
    """
    state = worst(
        (
            snapshot.collector_state(),
            collector_state(spool),
            CollectorState.DEGRADED if step_faults else CollectorState.HEALTHY,
        )
    )
    values: dict[str, Any] = spool.collector_health_fields()
    values |= snapshot.health_values()  # the poll queue's depth, not the spool's
    return CollectorHealthObservation(
        observation_id=new_id(ObservationId),
        collector_id=collector_id,
        timestamp=at,
        state=state,
        software_version=version,
        worker_id=worker_id,
        active_interfaces=interfaces,
        **values,
    )
