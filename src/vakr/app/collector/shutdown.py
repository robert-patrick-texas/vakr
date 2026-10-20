"""Graceful shutdown of the collector service, in the order of 16.4 (SVC-003, SVC-T03).

1. Mark the service draining (``STOPPING=1``) and stop the timers and
   discovery: nothing new starts.
2. Stop the scheduler: running polls get ``scheduler.shutdown_grace``, the
   rest are cancelled and recorded with what their finished steps produced,
   and every slot that will not run is recorded as not attempted.
3. Drain the outbox into the spool, then spool a last collector-health
   observation, which shows the collector draining.
4. Close the spool: queued records are written and fsynced, and the
   clean-stop marker is set.
5. Stop the ingester after one last write of everything spooled.
6. Write the final rate-baseline checkpoint, so the next start resumes rates.
7. Close the adapters and both databases.

The steps are bounded by ``scheduler.shutdown_grace`` plus the adapters'
own bounds; the total is logged against the budget in Appendix E.27, which
the unit's ``TimeoutStopSec`` comfortably exceeds.

Dependencies:
    asyncio: Waiting for tasks.
    vakr.service.sdnotify: The ``STOPPING=1`` notification.

Safety:
    Never cancels a spool append or a database write in progress.

Example:
    >>> shut_down.__name__
    'shut_down'
"""

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from vakr.app.collector.timers import health_tick, write_checkpoint
from vakr.service.sdnotify import notify

if TYPE_CHECKING:
    from vakr.app.collector.service import CollectorService

_LOG = logging.getLogger(__name__)


async def shut_down(service: CollectorService, tasks: dict[str, asyncio.Task[None]]) -> None:
    """Stop in 16.4 order within the stop budget.

    Args:
        service: The service.
        tasks: The long-running tasks by name.

    Returns:
        None

    Raises:
        None
    """
    began = service.clock.monotonic()
    notify("STOPPING=1\nSTATUS=draining")
    service.loops_stop.set()
    await service.discovery.stop()
    service.scheduler.stop()
    await asyncio.gather(tasks["scheduler"], return_exceptions=True)
    service.pump.stop()
    await asyncio.gather(tasks["spool pump"], return_exceptions=True)
    await service.pump.drain()
    with contextlib.suppress(Exception):
        await health_tick(service)
    service.upkeep.stop()
    await asyncio.gather(*tasks.values(), *service.reloads, return_exceptions=True)
    await service.parts.spool.close()
    await service.ingester.stop()
    await write_checkpoint(service)
    await service.parts.close()
    took = service.clock.monotonic() - began
    budget = service.parts.scheduler.shutdown_grace_s + service.timings.stop_budget_s
    level = logging.WARNING if took > budget else logging.INFO
    _LOG.log(
        level,
        "collector stopped in %.1f s (budget %.0f s)",
        took,
        budget,
        extra={"event_id": "collector.stopped"},
    )
