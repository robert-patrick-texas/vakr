"""Pure helpers of the collector's SNMP step: targets, Stage 2 eligibility, outcomes.

Kept apart from ``vakr.app.collector.snmp_step`` so each rule is small and
testable on its own:

* ``snmp_target`` builds the agent address from the device record; a device
  without a credential reference is a collector-side configuration gap
  (``CREDENTIAL``), never a device fault (invariant 2);
* ``split_eligible`` picks the interfaces Stage 2 samples: policy collects
  them, Stage 1 found them up/up *now* (SNMP-006, ADR-005), and their
  persistent identity is resolved. An up/up interface without one is counted
  and skipped, because no observation may be keyed by ``ifIndex``
  (invariant 6);
* ``answer_status`` maps the 10.2 SNMP state to the scheduler's step status;
* ``collector_failure`` records a session that could not open as
  ``UNKNOWN`` with ``COLLECTOR_DEGRADED``.

Dependencies:
    vakr.collectors.snmp: Plans, Stage 1 results, observation builders.
    vakr.ports.snmp: Targets, errors, statistics.

Safety:
    Pure. Error messages come from the adapter, which never includes secrets.

Example:
    >>> answer_status(SnmpState.PARTIAL).value
    'partial'
"""

from dataclasses import dataclass
from datetime import datetime

from vakr.app.collector.plans import EMPTY_PLAN
from vakr.collectors.snmp import (
    InterfacePlan,
    PollPlan,
    SnmpPollResult,
    Stage1Result,
    Stage2Result,
    build_snmp_device_observation,
)
from vakr.domain.enums import SnmpState, SnmpVersion, UptimeContinuity
from vakr.domain.errors import ValidationError
from vakr.domain.inventory import Device
from vakr.domain.observations import SnmpDeviceObservation
from vakr.domain.quality import Quality
from vakr.ports.snmp import SnmpError, SnmpSessionError, SnmpStats, SnmpTarget
from vakr.ports.snmp.errors import SnmpErrorKind
from vakr.scheduler.steps import PollContext, StepStatus

_STATUS = {
    SnmpState.RESPONDING: StepStatus.OK,
    SnmpState.PARTIAL: StepStatus.PARTIAL,
    SnmpState.ERROR: StepStatus.PARTIAL,  # it answered, with nothing usable
}


@dataclass(frozen=True, slots=True)
class Stage1Detail:
    """What Stage 1 hands to Stage 2 of the same poll (ADR-005).

    Attributes:
        target: The agent.
        stage1: This poll's Stage 1 result.
        eligible: Interfaces Stage 2 samples.
        continuity: ``sysUpTime`` continuity since the previous poll.
        max_varbinds: The size Stage 1 ended at, after any tooBig halving.
    """

    target: SnmpTarget
    stage1: Stage1Result
    eligible: tuple[InterfacePlan, ...]
    continuity: UptimeContinuity
    max_varbinds: int


def snmp_target(device: Device, port: int) -> SnmpTarget:
    """Build the SNMP target of a device.

    Args:
        device: The inventory record.
        port: ``snmp.port``.

    Returns:
        SnmpTarget: The agent, naming the credential by reference.

    Raises:
        SnmpSessionError: ``CREDENTIAL`` if the device names no credential,
            ``TRANSPORT`` if its address cannot be an SNMP agent.
    """
    if device.credential_ref is None:
        what = f"device {device.name} has no SNMP credential; set one or use icmp_only"
        raise SnmpSessionError(SnmpError(SnmpErrorKind.CREDENTIAL, what))
    try:
        return SnmpTarget(device.poll_address, device.credential_ref, port, device.address_family)
    except ValidationError as exc:
        raise SnmpSessionError(SnmpError(SnmpErrorKind.TRANSPORT, str(exc))) from None


def stage1_plan(plan: PollPlan, fallback: bool) -> PollPlan:
    """Return what Stage 1 asks for: everything, or only ``sysUpTime.0`` for fallback.

    Args:
        plan: The device's plan.
        fallback: True for the single low-cost ``snmp_fallback`` request (ICMP-013).

    Returns:
        PollPlan: ``EMPTY_PLAN`` for fallback, else ``plan``.

    Raises:
        None
    """
    return EMPTY_PLAN if fallback else plan


def split_eligible(plan: PollPlan, stage1: Stage1Result) -> tuple[tuple[InterfacePlan, ...], int]:
    """Choose Stage 2's interfaces and count up/up ones without an identity.

    Args:
        plan: The plan Stage 1 ran.
        stage1: Its result.

    Returns:
        tuple[tuple[InterfacePlan, ...], int]: Interfaces to sample, and how
        many up/up collected interfaces were skipped as unresolved.

    Raises:
        None
    """
    if not stage1.answered:
        return (), 0
    statuses = stage1.by_index()
    up = [
        e
        for e in plan.interfaces
        if e.collect and e.if_index in statuses and statuses[e.if_index].up_up
    ]
    eligible = tuple(e for e in up if e.interface_id is not None)
    return eligible, len(up) - len(eligible)


def answer_status(state: SnmpState) -> StepStatus:
    """Map an SNMP device state to the step status the scheduler expects.

    Args:
        state: From ``snmp_state``.

    Returns:
        StepStatus: ``OK``, ``PARTIAL``, or ``NO_RESPONSE``.

    Raises:
        None
    """
    return _STATUS.get(state, StepStatus.NO_RESPONSE)


def stage2_status(found: Stage2Result) -> StepStatus:
    """Return how Stage 2 ended, for the poll record.

    Args:
        found: The Stage 2 result.

    Returns:
        StepStatus: ``NO_RESPONSE`` without any answer, ``PARTIAL`` with
        unanswered requests, else ``OK``.

    Raises:
        None
    """
    if found.stats.responses == 0:
        return StepStatus.NO_RESPONSE
    if found.quality & Quality.PARTIAL_RESPONSE:
        return StepStatus.PARTIAL
    return StepStatus.OK


def collector_failure(
    ctx: PollContext, error: SnmpError, started_at: datetime, started: float, elapsed_s: float
) -> SnmpDeviceObservation:
    """Record that the collector could not talk to the agent at all.

    Args:
        ctx: The poll.
        error: Why the session did not open.
        started_at: When Stage 1 began (UTC).
        started: The same moment on the monotonic clock.
        elapsed_s: Seconds until the failure.

    Returns:
        SnmpDeviceObservation: ``UNKNOWN`` with ``COLLECTOR_DEGRADED`` (E.23).

    Raises:
        None
    """
    device = ctx.target.device
    stage1 = Stage1Result(started_at, started, None, (), SnmpStats(), error)
    result = SnmpPollResult(
        device.poll_address, SnmpVersion.V2C, started_at, started, max(0.0, elapsed_s), stage1, None
    )
    return build_snmp_device_observation(
        result,
        poll_id=ctx.poll_id,
        collector_id=ctx.collector_id,
        device_id=device.id,
        continuity=UptimeContinuity.UNKNOWN,
    )
