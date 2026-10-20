"""Turn one collector's current state into Appendix E.2 evidence and a status (E.2, MPOS-007).

``CurrentDeviceState`` carries the newest ICMP and the newest SNMP evidence
separately, and they need not come from the same poll: under ``icmp_gated``
an unreachable device gets no SNMP request, so its newest SNMP observation
may be an hour-old "responding". Combining the two would turn a dead device
into "ICMP lost, SNMP OK". So only evidence from the *same poll as the
newest evidence* counts; the other protocol is ``UNKNOWN`` for that poll
(E.29 decision 2).

Collector trouble comes from three places, and any of them makes the state
"Unknown — collector …" rather than a verdict about the target (OBS-003):
the collector's latest health report, a health report older than the E.3
threshold (the collector stopped reporting: ``unavailable``), and the
``COLLECTOR_DEGRADED`` flag on the latest poll or its evidence.

Dependencies:
    vakr.logic.status, vakr.logic.icmp, vakr.logic.freshness: The rules.
    vakr.domain: States, observations, and flags.

Safety:
    Pure: callers pass ``now``.

Example:
    >>> from datetime import UTC, datetime
    >>> now = datetime(2026, 10, 20, tzinfo=UTC)
    >>> collector_state(None, now, 60) is None
    True
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from vakr.domain.enums import (
    CollectorState,
    IcmpState,
    PollExecution,
    ReachabilityStrategy,
    SnmpState,
)
from vakr.domain.inventory import Device
from vakr.domain.observations import (
    CollectorHealthObservation,
    IcmpObservation,
    SnmpDeviceObservation,
)
from vakr.domain.quality import Quality
from vakr.domain.state import CurrentDeviceState
from vakr.logic.freshness import stale_threshold_s
from vakr.logic.icmp import ProbeGroup, summarise_probes
from vakr.logic.status import (
    CompositeStatus,
    DeviceStatus,
    PositionStatus,
    composite_status,
    device_status,
)
from vakr.logic.status_table import ROW_SEVERITY, StatusEvidence
from vakr.query.settings import QuerySettings

_CUT_SHORT = frozenset({PollExecution.NOT_ATTEMPTED, PollExecution.CANCELLED})
"""Last polls that read as unknown: never started, or cut short by shutdown."""

TELEMETRY_UNAVAILABLE = "Unknown — telemetry unavailable"


def collector_state(
    health: CollectorHealthObservation | None, now: datetime, interval_s: float
) -> CollectorState | None:
    """Judge a collector from its latest health report.

    Args:
        health: The newest report, or ``None`` if it never reported.
        now: Reference time (UTC).
        interval_s: Interval for the E.3 threshold (``polling.default_interval``).

    Returns:
        CollectorState | None: The reported state; ``UNAVAILABLE`` when the
        report is older than the E.3 threshold; ``None`` when it never reported.

    Raises:
        None
    """
    if health is None:
        return None
    if (now - health.timestamp).total_seconds() > stale_threshold_s(interval_s):
        return CollectorState.UNAVAILABLE
    return health.state


@dataclass(frozen=True, slots=True)
class Judgement:
    """One collector's evidence about a device and the E.2 result.

    Attributes:
        state: The collector's current state for the device, if any.
        icmp: ICMP evidence from the newest poll, if any.
        snmp: SNMP evidence from the newest poll, if any.
        strategy: Reachability strategy in force.
        interval_s: Effective interval used for freshness.
        collector: Collector condition used for row 3.
        status: The E.2 result.
    """

    state: CurrentDeviceState | None
    icmp: IcmpObservation | None
    snmp: SnmpDeviceObservation | None
    strategy: ReachabilityStrategy
    interval_s: int
    collector: CollectorState
    status: DeviceStatus


def _same_poll(
    state: CurrentDeviceState,
) -> tuple[IcmpObservation | None, SnmpDeviceObservation | None]:
    """Keep only the evidence that belongs to the newest poll.

    Args:
        state: The collector's current state.

    Returns:
        tuple: ICMP and SNMP evidence of the newest poll; ``None`` for a
        protocol that poll did not produce.

    Raises:
        None
    """
    candidates = [o for o in (state.icmp, state.snmp) if o is not None]
    anchor = max(candidates, key=lambda o: (o.timestamp, o.observation_id)).poll_id
    icmp = state.icmp if state.icmp is not None and state.icmp.poll_id == anchor else None
    snmp = state.snmp if state.snmp is not None and state.snmp.poll_id == anchor else None
    return icmp, snmp


def _collector_condition(
    state: CurrentDeviceState | None,
    icmp: IcmpObservation | None,
    snmp: SnmpDeviceObservation | None,
    reported: CollectorState | None,
) -> CollectorState:
    """Combine the health report with collector flags on the newest poll.

    Args:
        state: Current state, for the latest poll record.
        icmp: ICMP evidence of the newest poll.
        snmp: SNMP evidence of the newest poll.
        reported: Judged health report, if any.

    Returns:
        CollectorState: ``DEGRADED`` when the newest poll says the collector
        was degraded and the report says nothing worse.

    Raises:
        None
    """
    condition = reported or CollectorState.HEALTHY
    flags = Quality.NONE
    for o in (icmp, snmp, state.poll if state is not None else None):
        if o is not None:
            flags |= o.quality
    if condition is CollectorState.HEALTHY and Quality.COLLECTOR_DEGRADED in flags:
        return CollectorState.DEGRADED
    return condition


def judge(
    device: Device,
    state: CurrentDeviceState | None,
    reported: CollectorState | None,
    last_reset: datetime | None,
    settings: QuerySettings,
    now: datetime,
) -> Judgement:
    """Evaluate the E.2 table for one device as one collector sees it.

    Args:
        device: The device record (admin state, overrides).
        state: That collector's current state for it, if any.
        reported: The collector's judged health, if it ever reported.
        last_reset: When a recent agent restart was detected, if any.
        settings: Defaults for strategy, interval, and ICMP thresholds.
        now: Reference time (UTC).

    Returns:
        Judgement: The evidence used and the status.

    Raises:
        None
    """
    poll = state.poll if state is not None else None
    icmp, snmp = _same_poll(state) if state is not None else (None, None)
    strategy = poll.reachability if poll else device.reachability or settings.strategy
    interval = poll.effective_interval_s if poll else device.poll_interval_s
    interval = interval or settings.default_interval_s
    collector = _collector_condition(state, icmp, snmp, reported)
    impairments = ()
    if icmp is not None and icmp.state is IcmpState.IMPAIRED:
        group = ProbeGroup(icmp.rtts_us, sent=len(icmp.rtts_us))
        impairments = summarise_probes(group, settings.icmp_policy).impairments
    evidence = StatusEvidence(
        icmp=icmp.state if icmp is not None else IcmpState.UNKNOWN,
        snmp=snmp.state if snmp is not None else SnmpState.UNKNOWN,
        observed_at=state.observed_at if state is not None else None,
        admin=device.admin_state,
        strategy=strategy,
        collector=collector,
        not_attempted=poll is not None and poll.execution in _CUT_SHORT,
        last_reset_at=last_reset,
        icmp_impairments=impairments,
        interval_s=float(interval),
    )
    status = device_status(evidence, now, settings.restart_window_s)
    return Judgement(state, icmp, snmp, strategy, interval, collector, status)


def telemetry_unavailable(status: DeviceStatus) -> DeviceStatus:
    """Name storage trouble as the reason for an Unknown status (13.4, invariant 3).

    Args:
        status: The status computed without telemetry.

    Returns:
        DeviceStatus: Rows 1-2 unchanged; anything else "Unknown — telemetry unavailable".

    Raises:
        None
    """
    if status.row in (1, 2):
        return status
    return DeviceStatus(status.bucket, status.color, TELEMETRY_UNAVAILABLE, 3, ROW_SEVERITY[3])


def combine(positions: Sequence[PositionStatus], fallback: DeviceStatus) -> CompositeStatus:
    """Merge per-position results (MPOS-007); with no positions, use ``fallback``.

    Args:
        positions: Each collector's result.
        fallback: The no-evidence result, which still honours admin rows 1-2.

    Returns:
        CompositeStatus: The merged status.

    Raises:
        None
    """
    if positions:
        return composite_status(positions)
    return composite_status([PositionStatus("local", fallback)])
