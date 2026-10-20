"""The scheduler's SNMP steps over the real SNMP collector (9.2, 9.5-9.7, SNMP-004..019).

``stage1`` reads ``sysUpTime.0`` and the exact status OIDs of the device's
working plan, judges uptime continuity against the device's previous
reading (E.5), and returns the ``SnmpDeviceObservation`` at once, so a
Stage 2 cut short by the deadline or shutdown never loses Stage 1's
evidence (9.2). Its ``up_up`` count is the number of interfaces Stage 2
will sample, so the scheduler runs Stage 2 only when there is something to
sample (ADR-005).

``stage2`` reads counters for exactly those interfaces, folds learned
capabilities into the working plan (SNMP-010), derives rates against each
interface's baseline (``vakr.app.collector.derive``), and returns the
``InterfaceObservation`` values.

Each stage opens its own short session; Stage 2 starts at the request size
Stage 1 ended at (SNMP-017). A session that cannot open is collector
trouble: Stage 1 records it as ``UNKNOWN`` with ``COLLECTOR_DEGRADED``
(E.23), never as the device being down.

Dependencies:
    vakr.collectors.snmp: Stage 1, Stage 2, observation builders.
    vakr.ports.snmp: The session factory port.
    vakr.app.collector: Plans, baselines, rate derivation, tally.

Safety:
    Credentials are resolved inside the adapter at session open (15.2).

Example:
    >>> CollectorSnmpStep.__name__
    'CollectorSnmpStep'
"""

from dataclasses import replace

from vakr.app.collector.baselines import BaselineStore
from vakr.app.collector.derive import DeriveContext, RateSettings, derive_observation
from vakr.app.collector.plans import PlanBook
from vakr.app.collector.snmp_parts import (
    Stage1Detail,
    answer_status,
    collector_failure,
    snmp_target,
    split_eligible,
    stage1_plan,
    stage2_status,
)
from vakr.app.collector.tally import FaultTally
from vakr.collectors.snmp import (
    SnmpPollResult,
    Stage2Result,
    UptimeReading,
    build_snmp_device_observation,
    run_stage1,
    run_stage2,
    uptime_continuity,
)
from vakr.collectors.snmp.config import SnmpConfig
from vakr.domain.enums import SnmpState, UptimeContinuity
from vakr.domain.quality import Quality
from vakr.ports.clock import Clock
from vakr.ports.snmp import SnmpSessionError, SnmpSessionFactory
from vakr.scheduler.steps import PollContext, SnmpResult, StepStatus

UNKNOWN = UptimeContinuity.UNKNOWN


class CollectorSnmpStep:
    """``SnmpStep`` for the collector service.

    Attributes:
        config: Current ``[snmp]`` settings; replaced on reload.
        rates: Current ``[rates]`` limits; replaced on reload.
    """

    def __init__(
        self,
        factory: SnmpSessionFactory,
        config: SnmpConfig,
        plans: PlanBook,
        baselines: BaselineStore,
        clock: Clock,
        tally: FaultTally,
        boot_id: str,
    ) -> None:
        """Bind the step to its collaborators.

        Args:
            factory: Opens SNMP sessions.
            config: ``[snmp]`` settings.
            plans: Working poll plans.
            baselines: Rate baselines and uptime readings.
            clock: Timestamps.
            tally: Where collector-side failures are counted.
            boot_id: This kernel boot, for elapsed time across restarts.

        Returns:
            None

        Raises:
            None
        """
        self.config = config
        self.rates = RateSettings()
        self._factory, self._plans, self._baselines = factory, plans, baselines
        self._clock, self._tally, self._boot_id = clock, tally, boot_id

    async def stage1(self, ctx: PollContext, fallback: bool) -> SnmpResult:
        """Read uptime and exact interface status (9.5).

        Args:
            ctx: The poll.
            fallback: True for the single low-cost ``snmp_fallback`` request.

        Returns:
            SnmpResult: Status, Stage 2 count, detail for Stage 2, and the
            device observation.

        Raises:
            None
        """
        device, clock = ctx.target.device, self._clock
        plan = stage1_plan(self._plans.plan(device.id), fallback)
        settings = replace(self.config.settings, retries=0) if fallback else self.config.settings
        started_at, started = clock.now_utc(), clock.monotonic()
        try:
            target = snmp_target(device, self.config.port)
            async with self._factory.open(target, settings) as session:
                stage1 = await run_stage1(session, plan, clock)
                size = session.max_varbinds
        except SnmpSessionError as exc:
            self._tally.fault("snmp_session")
            obs = collector_failure(
                ctx, exc.error, started_at, started, clock.monotonic() - started
            )
            return SnmpResult(
                StepStatus.NO_RESPONSE, observations=(obs,), quality=Quality.COLLECTOR_DEGRADED
            )
        result = SnmpPollResult(
            target.label,
            target.version,
            started_at,
            started,
            clock.monotonic() - started,
            stage1,
            Stage2Result(()) if stage1.answered else None,
            stage1.stats,
            stage1.quality,
        )
        eligible, unresolved = ((), 0) if fallback else split_eligible(plan, stage1)
        self._tally.unresolved_interfaces[device.id] = unresolved
        return self._judge(
            ctx, result, Stage1Detail(target, stage1, eligible, UNKNOWN, size), unresolved
        )

    def _judge(
        self, ctx: PollContext, result: SnmpPollResult, detail: Stage1Detail, unresolved: int
    ) -> SnmpResult:
        """Judge uptime continuity, build the device observation, and plan Stage 2.

        Args:
            ctx: The poll.
            result: Stage 1 as an SNMP poll result.
            detail: Stage 2's inputs; its continuity is filled in here.
            unresolved: Up/up interfaces skipped for lack of an identity.

        Returns:
            SnmpResult: The Stage 1 outcome for the scheduler.

        Raises:
            None
        """
        device_id, stage1 = ctx.target.device.id, result.stage1
        ticks = stage1.sys_uptime_ticks
        reading = None if ticks is None else UptimeReading(ticks, stage1.sampled_at)
        continuity, flag, _ = uptime_continuity(self._baselines.uptime(device_id), reading)
        if reading is not None:
            self._baselines.set_uptime(device_id, reading)
        obs = build_snmp_device_observation(
            result,
            poll_id=ctx.poll_id,
            collector_id=ctx.collector_id,
            device_id=device_id,
            continuity=continuity,
            extra_quality=flag,
        )
        quality = stage1.quality | flag
        if unresolved:
            quality |= Quality.BASELINE_IDENTITY_UNRESOLVED
        if obs.state is SnmpState.UNKNOWN:
            self._tally.fault("snmp_transport")
            quality |= Quality.COLLECTOR_DEGRADED
        detail = replace(detail, continuity=continuity)
        return SnmpResult(answer_status(obs.state), len(detail.eligible), detail, (obs,), quality)

    async def stage2(self, ctx: PollContext, stage1: SnmpResult) -> SnmpResult:
        """Read counters for the interfaces this poll's Stage 1 chose, and derive rates.

        Args:
            ctx: The poll.
            stage1: This poll's Stage 1 result.

        Returns:
            SnmpResult: Status and interface observations.

        Raises:
            TypeError: If ``stage1`` did not come from this step.
            SnmpSessionError: If the session cannot open (collector trouble).
        """
        detail = stage1.detail
        if not isinstance(detail, Stage1Detail):
            raise TypeError("stage2 needs this step's own Stage 1 result")
        device = ctx.target.device
        settings = replace(self.config.settings, max_varbinds=detail.max_varbinds)
        try:
            async with self._factory.open(detail.target, settings) as session:
                found = await run_stage2(
                    session,
                    detail.eligible,
                    detail.stage1.by_index(),
                    self._clock,
                    settings.abandon_after,
                )
        except SnmpSessionError:
            self._tally.fault("snmp_session")
            raise
        self._plans.learn(device.id, found.changes)
        context = DeriveContext(
            ctx.poll_id,
            ctx.collector_id,
            device.id,
            float(ctx.target.effective_interval_s),
            detail.continuity,
            self._boot_id,
            self.rates,
        )
        observations = tuple(
            derive_observation(s, self._baselines, self._plans.capacity(device.id, iid), context)
            for s in found.samples
            if (iid := s.interface_id) is not None
        )
        return SnmpResult(stage2_status(found), observations=observations, quality=found.quality)
