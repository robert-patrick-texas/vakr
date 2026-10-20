"""The collector's minute timers: baseline checkpoint and collector health (6.2, OBS-001).

``repeat`` runs an action every interval until the timers stop; an action
that fails is logged and tried again next time, because a timer bug must
never stop collection.

* ``write_checkpoint`` encodes the baselines on the event loop, so the
  snapshot is consistent, and writes it atomically in a worker thread
  (6.2: every minute, so a restart costs at most one rate interval).
* ``health_tick`` closes the scheduler's counting period, writes one
  ``CollectorHealthObservation`` to the spool, refreshes the collector
  state the ICMP math assumes, and resets capability limitations older
  than ``snmp.capability_reprobe_interval`` (DISC-009).

Dependencies:
    asyncio: Waits and worker threads.
    vakr.app.collector: Checkpoint, health, and the service.

Safety:
    Every write goes through the spool or the atomic checkpoint.

Example:
    >>> repeat.__name__
    'repeat'
"""

import asyncio
import contextlib
import dataclasses
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from vakr.app.collector.baseline_codec import encode_store
from vakr.app.collector.checkpoint import save_checkpoint
from vakr.app.collector.health import health_observation, probe_state
from vakr.domain.enums import CollectorState
from vakr.ingest import SpoolError
from vakr.version import get_version

if TYPE_CHECKING:
    from vakr.app.collector.service import CollectorService

_LOG = logging.getLogger(__name__)


async def repeat(
    stop: asyncio.Event, interval_s: float, action: Callable[[], Awaitable[None]]
) -> None:
    """Run an action every interval until ``stop`` is set; failures are logged.

    Args:
        stop: Set when the timers end.
        interval_s: Seconds between runs.
        action: The action.

    Returns:
        None

    Raises:
        None
    """
    while not stop.is_set():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), interval_s)
        if stop.is_set():
            return
        try:
            await action()
        except Exception as exc:  # noqa: BLE001 - a timer bug must not stop collection
            _LOG.error(
                "a collector timer failed (%s); it runs again next interval",
                type(exc).__name__,
                extra={"event_id": "collector.timer_failed"},
            )


async def health_loop(service: CollectorService) -> None:
    """Write collector health at once, then at least once per poll interval (OBS-001).

    The cadence is anchored to the monotonic clock, so a slow tick does not
    push every later one back; after a long stall it restarts from now
    instead of writing a burst.

    Args:
        service: The running service; ``health_interval_s`` is read each time.

    Returns:
        None

    Raises:
        None
    """
    clock, stop = service.clock, service.loops_stop
    due = clock.monotonic()
    while not stop.is_set():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), max(0.0, due - clock.monotonic()))
        if stop.is_set():
            return
        try:
            await health_tick(service)
        except Exception as exc:  # noqa: BLE001 - a timer bug must not stop collection
            _LOG.error(
                "the collector-health timer failed (%s); it runs again next interval",
                type(exc).__name__,
                extra={"event_id": "collector.timer_failed"},
            )
        due = max(due + service.health_interval_s(), clock.monotonic())


async def write_checkpoint(service: CollectorService) -> None:
    """Write the rate-baseline checkpoint (6.2, RATE-004).

    Args:
        service: The running service.

    Returns:
        None

    Raises:
        None
    """
    data = encode_store(service.baselines, service.parts.collector.id, service.clock.now_utc())
    try:
        await asyncio.to_thread(save_checkpoint, data, service.parts.checkpoint)
    except OSError as exc:
        _LOG.warning(
            "cannot write the rate-baseline checkpoint (%s); a restart now would "
            "cost one rate interval",
            exc.strerror or type(exc).__name__,
            extra={"event_id": "collector.checkpoint_failed"},
        )


async def health_tick(service: CollectorService, *, stopping: bool = False) -> None:
    """Spool a collector-health observation and run the capability re-probe check.

    Args:
        service: The running service.
        stopping: This is the last report before a clean stop.

    Returns:
        None

    Raises:
        None
    """
    now = service.clock.now_utc()
    snapshot = service.scheduler.snapshot(close_period=True)
    service.tally.state = probe_state(snapshot)
    observation = health_observation(
        service.ids.collector_id,
        service.ids.worker_id,
        now,
        get_version(),
        snapshot,
        service.ingester.health(),
        service.tally.take_period(),
        len(service.plans.interface_ids()),
    )
    if stopping:  # devices read "collector unavailable" while it is stopped, not "degraded"
        observation = dataclasses.replace(observation, state=CollectorState.UNAVAILABLE)
    service.plans.reprobe_expired(now, service.snmp_step.config.capability_reprobe_interval)
    try:
        await service.parts.spool.append([observation])
    except SpoolError as exc:
        _LOG.warning(
            "cannot spool the collector-health observation (%s)",
            type(exc).__name__,
            extra={"event_id": "collector.health_refused"},
        )
