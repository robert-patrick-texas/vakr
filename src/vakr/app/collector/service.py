"""``CollectorService``: the Compact headless collector's run loop (6.2, SVC-003, 16.4).

One process does everything ``vakr-collector.service`` owns in the Compact
profile: the scheduler runs ICMP and SNMP polls through the collector steps,
the spool pump makes every result durable, the ingester writes the spool
into the SQLite telemetry store (the only telemetry writer), discovery runs
beside polling (with manual rediscoveries an administrator queued, picked up
by the operation watcher), and timers checkpoint rate baselines every minute, write a
collector-health observation every minute, and keep the telemetry store in
shape. A watcher follows the inventory change counter, and SIGHUP reloads
the configuration.

Shutdown follows 16.4 in order: stop discovery and the timers; stop the
scheduler, which gives running polls ``scheduler.shutdown_grace`` and
records every slot it will not run; drain the outbox into the spool; write
a last health observation; close the spool (fsync and the clean-stop
marker); stop the ingester after its last write; write the final baseline
checkpoint; close the adapters and databases.

Dependencies:
    vakr.scheduler, vakr.ingest: Scheduling, spool, ingestion.
    vakr.app.collector: Steps, plans, baselines, discovery, timers, health.
    vakr.service.sdnotify: Readiness for ``Type=notify``.

Safety:
    Every blocking call runs in a worker thread; every queue is bounded.

Example:
    >>> CollectorService.__name__
    'CollectorService'
"""

import asyncio
import logging
from collections.abc import Coroutine
from functools import partial
from typing import Any

from vakr.app.collector.build import CollectorParts
from vakr.app.collector.derive import RateSettings
from vakr.app.collector.discovery_manual import ManualDiscoveryRunner
from vakr.app.collector.icmp_step import CollectorIcmpStep
from vakr.app.collector.maintenance import RollupHook, TelemetryUpkeep
from vakr.app.collector.metrics_loop import metrics_loop
from vakr.app.collector.operations import OperationWatcher
from vakr.app.collector.pipeline import SpoolPump
from vakr.app.collector.plans import PlanBook
from vakr.app.collector.ranking_projector import collector_projector
from vakr.app.collector.reload import reload_service
from vakr.app.collector.replan import Replanner
from vakr.app.collector.rollup_job import collector_job
from vakr.app.collector.shutdown import shut_down
from vakr.app.collector.snmp_step import CollectorSnmpStep
from vakr.app.collector.tally import FaultTally
from vakr.app.collector.timers import health_loop, repeat, write_checkpoint
from vakr.app.collector.timings import EX_PREFLIGHT, ServiceTimings
from vakr.collectors.icmp.cycle import IcmpCollector
from vakr.domain.ids import WorkerId, new_id
from vakr.ingest import Ingester
from vakr.ports.clock import Clock, SystemClock
from vakr.scheduler.driver import Scheduler, SchedulerIds
from vakr.scheduler.outbox import Outbox
from vakr.scheduler.steps import PollSteps
from vakr.service.sdnotify import notify

_LOG = logging.getLogger(__name__)


class CollectorService:
    """The running collector; one per process and collector identity (6.2).

    Attributes:
        parts: What the preflight opened.
        scheduler, discovery, ingester, pump, upkeep, replanner, operations: The parts at work.
        plans, baselines, tally: Shared working state.
        failed: Name of a component that stopped unexpectedly, if any.
    """

    failed: str | None
    exit_code: int | None  # set by a hard stop; overrides 0 or 1
    reloads: set[asyncio.Task[bool]]

    def __init__(
        self, parts: CollectorParts, timings: ServiceTimings | None = None,
        clock: Clock | None = None, rollups: RollupHook | None = None,
    ) -> None:  # fmt: skip
        """Wire the parts together; nothing runs until ``run``.

        Args:
            parts: From ``build_parts``.
            timings: Cadences (E.27); the defaults in production.
            clock: Time source; the system clocks by default.
            rollups: The rollup hook; the real job (``collector_job``) by default.

        Returns:
            None

        Raises:
            None
        """
        self.parts, self.timings = parts, timings or ServiceTimings()
        self.clock = clock = clock or SystemClock()
        self.ids = SchedulerIds(parts.collector.id, new_id(WorkerId))
        self.tally, self.plans, self.baselines = FaultTally(), PlanBook(), parts.baselines
        self.icmp_step = CollectorIcmpStep(IcmpCollector(parts.prober, parts.icmp), self.tally)
        self.icmp_step.on_permission_lost = self._ping_refused
        self.snmp_step = CollectorSnmpStep(parts.snmp_factory, parts.snmp, self.plans,
                                           self.baselines, clock, self.tally,
                                           parts.boot_id)  # fmt: skip
        self.snmp_step.rates = RateSettings.from_config(parts.config)
        self.outbox = Outbox(parts.scheduler.queue_capacity, clock)
        steps = PollSteps(self.icmp_step, self.snmp_step)
        self.scheduler = Scheduler(clock, parts.scheduler, self.ids, steps, self.outbox,
                                   admits=parts.spool.admits_new_work)  # fmt: skip
        self.pump = SpoolPump(self.outbox, parts.spool)
        self.ingester = Ingester(parts.spool, parts.telemetry.writer, parts.telemetry.capabilities)
        name = parts.scheduler.rule.collector_name
        self.discovery = ManualDiscoveryRunner(parts.inventory, parts.snmp_factory, self.plans,
                                               clock, self.timings, name)  # fmt: skip
        self.discovery.config = parts.snmp
        self.replanner = Replanner(parts.inventory, self.scheduler, self.discovery, self.plans,
                                   self.baselines, parts.uow_factory, clock,
                                   self.timings.poll_interval_override_s)  # fmt: skip
        self.replanner.defaults = parts.scheduler.defaults
        self.discovery.on_plan = self.replanner.refresh_capacity
        self.operations = OperationWatcher(self.discovery, parts.uow_factory,
                                           parts.collector.id, clock)  # fmt: skip
        job = rollups if rollups is not None else collector_job(parts, clock, self.ingester)
        projector = collector_projector(parts, clock, self.ingester)
        self.upkeep = TelemetryUpkeep(parts.telemetry, clock, self.timings, job, projector)
        self.failed = self.exit_code = None
        self._stop, self.loops_stop, self.reloads = asyncio.Event(), asyncio.Event(), set()

    def request_stop(self) -> None:
        """Begin graceful shutdown (SIGTERM, SIGINT).

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._stop.set()

    def _ping_refused(self) -> None:
        """Stop for good when the host no longer lets vakr ping (ICMP-012).

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        if self.exit_code is None:
            _LOG.error(
                "the host refused ICMP Echo (net.ipv4.ping_group_range); stopping until it "
                "is fixed: run `vakr doctor` for the fix",
                extra={"event_id": "collector.icmp_refused"},
            )
            self.exit_code = EX_PREFLIGHT
        self.request_stop()

    def request_reload(self) -> None:
        """Reload the configuration in the background (SIGHUP, CONF-003).

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        task = asyncio.get_running_loop().create_task(self.reload())
        self.reloads.add(task)
        task.add_done_callback(self.reloads.discard)

    async def run(self) -> int:
        """Collect until asked to stop, then shut down in 16.4 order.

        Args:
            None

        Returns:
            int: 0 after a requested stop, 1 when a component stopped unexpectedly,
                78 when the host stopped letting vakr ping (no automatic restart).

        Raises:
            None
        """
        await self.ingester.start()
        try:
            await self.replanner.replan()
        except Exception as exc:  # noqa: BLE001 - the watcher retries until inventory answers
            _LOG.warning(
                "first plan failed (%s); retrying every %g s",
                type(exc).__name__,
                self.timings.change_poll_s,
                extra={"event_id": "collector.plan_failed"},
            )
        tasks = self._start_tasks()
        notify(f"READY=1\nSTATUS=collecting from {len(self.plans)} planned devices")
        _LOG.info(
            "collector %s (%s) is collecting",
            self.parts.collector.name,
            self.parts.collector.id,
            extra={"event_id": "collector.started"},
        )
        stopper = asyncio.ensure_future(self._stop.wait())
        done, _ = await asyncio.wait(
            [stopper, *tasks.values()], return_when=asyncio.FIRST_COMPLETED
        )
        stopper.cancel()
        for name, task in tasks.items():
            if task in done:
                self.failed = name
                error = None if task.cancelled() else task.exception()
                _LOG.error(
                    "%s stopped unexpectedly (%s); shutting down",
                    name,
                    type(error).__name__,
                    extra={"event_id": "collector.component_failed"},
                )
        await shut_down(self, tasks)
        if self.exit_code is not None:
            return self.exit_code
        return 0 if self.failed is None else 1

    def _start_tasks(self) -> dict[str, asyncio.Task[None]]:
        """Start every long-running part as a task.

        Args:
            None

        Returns:
            dict[str, asyncio.Task[None]]: Tasks by operator-readable name.

        Raises:
            None
        """
        t = self.timings
        jobs: dict[str, Coroutine[Any, Any, None]] = {
            "scheduler": self.scheduler.run(),
            "spool pump": self.pump.run(),
            "discovery": self.discovery.run(),
            "telemetry upkeep": self.upkeep.run(),
            "inventory watch": self.replanner.watch(self.loops_stop, t.change_poll_s),
            "operations watch": self.operations.watch(self.loops_stop, t.change_poll_s),
            "baseline checkpoint": repeat(
                self.loops_stop, t.checkpoint_interval_s, partial(write_checkpoint, self)
            ),
            "health": health_loop(self),
            "metrics snapshot": metrics_loop(self),
        }
        return {name: asyncio.create_task(job, name=name) for name, job in jobs.items()}

    def health_interval_s(self) -> float:
        """Return how often collector health is written: at least once per poll interval.

        The views read a collector as unavailable when its newest health
        observation is older than the E.3 threshold of its interval, so
        health is written at least as often as the shortest interval polled.

        Args:
            None

        Returns:
            float: Seconds; at most ``ServiceTimings.health_interval_s``.

        Raises:
            None
        """
        candidates = [self.timings.health_interval_s, float(self.replanner.defaults.interval_s)]
        if (shortest := self.replanner.shortest_interval_s) is not None:
            candidates.append(float(shortest))
        return min(candidates)

    async def reload(self) -> bool:
        """Apply a valid candidate configuration, or keep the running one (CONF-003).

        Args:
            None

        Returns:
            bool: True when the candidate was applied.

        Raises:
            None
        """
        return await reload_service(self)
