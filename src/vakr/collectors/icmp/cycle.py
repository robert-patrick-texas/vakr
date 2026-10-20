"""Turn one ICMP probe cycle into an observation, a state, and an SNMP plan.

``build_cycle`` is pure: it keeps the probes that say something about the
target (reply, timeout, ICMP error), summarises them with
``vakr.logic.icmp`` (E.1 state, E.7 rounding, ICMP-010 jitter), and builds
the ``IcmpObservation`` with the address family and pinned source
(ICMP-011). Probes the collector could not send or stopped watching are
left out, and any collector fault marks the cycle ``COLLECTOR_DEGRADED``,
so 0 replies then means ``UNKNOWN``, not ``UNREACHABLE`` (invariant 2). A
cycle with no probe sent has no observation: the poll is not attempted.

Dependencies:
    vakr.logic.icmp: Summary math, E.1 classification, SNMP gating.
    vakr.ports.icmp: The prober contract and raw result.
    vakr.domain: Observation record, IDs, states.

Safety:
    Pure apart from ``IcmpCollector.collect``, which awaits the prober.
    Never invents a reply or a loss.

Example:
    >>> from vakr.collectors.icmp.settings import IcmpSettings
    >>> IcmpCollector.__name__, IcmpSettings().probes
    ('IcmpCollector', 5)
"""

from dataclasses import dataclass

from vakr.collectors.icmp.settings import IcmpSettings, require_gate_probes
from vakr.domain.enums import CollectorState, ReachabilityStrategy
from vakr.domain.ids import CollectorId, DeviceId, ObservationId, PollId, new_id
from vakr.domain.observations import IcmpObservation
from vakr.logic.icmp import (
    DEFAULT_POLICY,
    IcmpPolicy,
    IcmpSummary,
    ProbeGroup,
    SnmpPlan,
    snmp_plan,
    summarise_probes,
)
from vakr.ports.icmp import CollectorFault, IcmpProber, ProbeResult

_OVERLOAD_FAULTS = {CollectorFault.OVERLOADED, CollectorFault.RECEIVE_OVERFLOW}


@dataclass(frozen=True, slots=True)
class PollContext:
    """Who is polling whom, and how, for one cycle (9.1).

    Attributes:
        poll_id: The ``DevicePoll`` this cycle belongs to.
        collector_id: This collector.
        device_id: The probed device.
        strategy: The device's reachability strategy (``icmp_gated`` by default).
        collector: Collector health as the scheduler sees it.
    """

    poll_id: PollId
    collector_id: CollectorId
    device_id: DeviceId
    strategy: ReachabilityStrategy = ReachabilityStrategy.ICMP_GATED
    collector: CollectorState = CollectorState.HEALTHY


@dataclass(frozen=True, slots=True)
class IcmpCycle:
    """Everything the scheduler needs from one ICMP cycle.

    Attributes:
        result: The prober's raw result, including per-probe details.
        summary: Counts, latency summary, state, and flags.
        snmp: What the SNMP part of this poll should do (ICMP-006, -007, -013).
        observation: The record to persist, or ``None`` when no probe was sent.
    """

    result: ProbeResult
    summary: IcmpSummary
    snmp: SnmpPlan
    observation: IcmpObservation | None


def collector_state(result: ProbeResult, reported: CollectorState) -> CollectorState:
    """Combine the scheduler's view of the collector with this cycle's evidence.

    Args:
        result: The cycle's raw result.
        reported: Collector health from the scheduler.

    Returns:
        CollectorState: ``reported`` when it is already unhealthy or the
        cycle had no fault; otherwise OVERLOADED for capacity faults and
        DEGRADED for the rest.

    Raises:
        None
    """
    if reported is not CollectorState.HEALTHY or result.fault is None:
        return reported
    if result.fault in _OVERLOAD_FAULTS:
        return CollectorState.OVERLOADED
    return CollectorState.DEGRADED


def build_cycle(
    result: ProbeResult,
    context: PollContext,
    policy: IcmpPolicy = DEFAULT_POLICY,
    observation_id: ObservationId | None = None,
) -> IcmpCycle:
    """Summarise a probe result and build its observation.

    Args:
        result: The prober's raw result.
        context: Poll, collector, device, strategy, and collector health.
        policy: E.1 thresholds.
        observation_id: ID to use; a new UUIDv7 when ``None``.

    Returns:
        IcmpCycle: Summary, SNMP plan, and the observation (``None`` when
        no probe was sent).

    Raises:
        ValidationError: If the request's addresses break the observation
            rules (they are validated by ``ProbeRequest`` first).
    """
    group = ProbeGroup.from_raw(result.raw_rtts_us)
    summary = summarise_probes(group, policy, collector_state(result, context.collector))
    plan = snmp_plan(summary.received, context.strategy)
    if not group.sent:
        return IcmpCycle(result, summary, plan, None)
    request = result.request
    observation = IcmpObservation(
        observation_id=observation_id or new_id(ObservationId),
        poll_id=context.poll_id,
        collector_id=context.collector_id,
        device_id=context.device_id,
        timestamp=result.started_at,
        address_family=request.family,
        target_address=request.target,
        rtts_us=group.rtts_us,
        state=summary.state,
        quality=summary.quality,
        source_address=request.source,
        min_rtt_us=summary.min_rtt_us,
        avg_rtt_us=summary.avg_rtt_us,
        median_rtt_us=summary.median_rtt_us,
        max_rtt_us=summary.max_rtt_us,
        jitter_us=summary.jitter_us,
    )
    return IcmpCycle(result, summary, plan, observation)


class IcmpCollector:
    """Runs one ICMP cycle per device through an ``IcmpProber``.

    Attributes:
        settings: Current settings; replace it to apply a configuration reload.
    """

    def __init__(self, prober: IcmpProber, settings: IcmpSettings) -> None:
        """Create the collector.

        Args:
            prober: The ICMP adapter.
            settings: Effective ``[icmp]`` settings.

        Returns:
            None

        Raises:
            None
        """
        self._prober = prober
        self.settings = settings

    async def collect(
        self, target: str, context: PollContext, source: str | None = None
    ) -> IcmpCycle:
        """Probe one device and summarise the cycle.

        Not for ``snmp_only`` devices: the scheduler skips ICMP for them.

        Args:
            target: Canonical IP literal of the device.
            context: Poll, collector, device, strategy, and collector health.
            source: Pinned source address, if the device has one.

        Returns:
            IcmpCycle: Summary, SNMP plan, and observation.

        Raises:
            ValueError: If the probe count is too low for ``icmp_gated``.
            ValidationError: If the target or source is not acceptable.
        """
        require_gate_probes(context.strategy, self.settings.probes)
        result = await self._prober.probe(self.settings.request(target, source))
        return build_cycle(result, context, self.settings.policy)
