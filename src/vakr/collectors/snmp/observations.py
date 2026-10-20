"""Build canonical observations from SNMP poll results (10.2, 10.3).

These pure functions are what the collection pipeline calls after a poll:

- ``uptime_continuity`` compares this poll's ``sysUpTime.0`` with the last
  successful one (E.5, UPTIME-002) through ``vakr.logic.uptime``;
- ``build_snmp_device_observation`` makes the ``SnmpDeviceObservation``;
- ``skipped_snmp_observation`` records SNMP skipped by ICMP gating;
- ``build_interface_observation`` combines a raw ``InterfaceSample`` with the
  ``RateResult`` that ``vakr.logic.rates.compute_rates`` derived from it.

Rates are never computed here: the pipeline owns the previous sample per
interface and calls ``vakr.logic`` (RATE-001..010).

Dependencies:
    vakr.collectors.snmp: Poll results and samples.
    vakr.logic.rates, vakr.logic.uptime: Rate and continuity results.
    vakr.domain: The observation types.

Safety:
    Pure. No credential reaches an observation (10.2).

Example:
    >>> from datetime import UTC, datetime
    >>> at = datetime(2026, 10, 20, tzinfo=UTC)
    >>> uptime_continuity(None, UptimeReading(1, at))[0].name
    'UNKNOWN'
"""

from dataclasses import dataclass
from datetime import datetime

from vakr.collectors.snmp.counters import InterfaceSample
from vakr.collectors.snmp.poll import SnmpPollResult, snmp_state
from vakr.domain.enums import BaselineState, SnmpState, SnmpVersion, UptimeContinuity
from vakr.domain.ids import CollectorId, DeviceId, InterfaceId, ObservationId, PollId, new_id
from vakr.domain.observations import InterfaceObservation, SnmpDeviceObservation
from vakr.domain.quality import BASELINE_INVALIDATING, Quality
from vakr.logic.counters import COUNTERS
from vakr.logic.rates import RateResult
from vakr.logic.uptime import check_uptime


@dataclass(frozen=True, slots=True)
class UptimeReading:
    """One ``sysUpTime.0`` value and when it was read.

    Attributes:
        ticks: Hundredths of a second since the agent started.
        at: When it was read (UTC).
    """

    ticks: int
    at: datetime


def uptime_continuity(
    previous: UptimeReading | None, current: UptimeReading | None
) -> tuple[UptimeContinuity, Quality, str]:
    """Judge whether the management agent ran continuously between two polls (E.5).

    Args:
        previous: The last successful reading, if any.
        current: This poll's reading, if ``sysUpTime`` arrived.

    Returns:
        tuple[UptimeContinuity, Quality, str]: The verdict, its quality flag
        (``UPTIME_AHEAD``, ``CLOCK_UNSYNCED``, or none), and evidence text.
        Without both readings the verdict is ``UNKNOWN``.

    Raises:
        None
    """
    if previous is None or current is None:
        return UptimeContinuity.UNKNOWN, Quality.NONE, "no earlier sysUpTime to compare with"
    elapsed = (current.at - previous.at).total_seconds()
    check = check_uptime(previous.ticks, current.ticks, elapsed)
    return check.continuity, check.quality, check.evidence


def build_snmp_device_observation(
    result: SnmpPollResult,
    *,
    poll_id: PollId,
    collector_id: CollectorId,
    device_id: DeviceId,
    continuity: UptimeContinuity,
    extra_quality: Quality = Quality.NONE,
    observation_id: ObservationId | None = None,
) -> SnmpDeviceObservation:
    """Build the SNMP device observation of one poll (10.2).

    Args:
        result: The poll.
        poll_id: The ``DevicePoll``.
        collector_id: This collector.
        device_id: The device.
        continuity: From ``uptime_continuity``.
        extra_quality: More flags, such as the continuity flag.
        observation_id: A fixed UUID, for tests; new when ``None``.

    Returns:
        SnmpDeviceObservation: A collector failure is ``UNKNOWN`` (invariant 2).

    Raises:
        ValidationError: If a value breaks the observation's rules.
    """
    state = snmp_state(result)
    quality = result.quality | extra_quality
    if state is SnmpState.UNKNOWN:
        quality |= Quality.COLLECTOR_DEGRADED
    error = result.error
    return SnmpDeviceObservation(
        observation_id=observation_id or new_id(ObservationId),
        poll_id=poll_id,
        collector_id=collector_id,
        device_id=device_id,
        timestamp=result.started_at,
        snmp_version=result.version,
        state=state,
        uptime_continuity=continuity,
        request_count=result.stats.requests,
        response_count=result.stats.responses,
        timeout_count=result.stats.timeouts,
        retry_count=result.stats.retries,
        elapsed_ms=max(0, round(result.elapsed_s * 1000)),
        quality=quality,
        sys_uptime_ticks=result.stage1.sys_uptime_ticks,
        error_code=None if error is None else error.code,
    )


def skipped_snmp_observation(
    *,
    poll_id: PollId,
    collector_id: CollectorId,
    device_id: DeviceId,
    timestamp: datetime,
    version: SnmpVersion = SnmpVersion.V2C,
    observation_id: ObservationId | None = None,
) -> SnmpDeviceObservation:
    """Record that SNMP was skipped, for example by ``icmp_gated`` (9.2).

    Args:
        poll_id: The ``DevicePoll``.
        collector_id: This collector.
        device_id: The device.
        timestamp: When SNMP would have started (UTC).
        version: The device's SNMP version.
        observation_id: A fixed UUID, for tests; new when ``None``.

    Returns:
        SnmpDeviceObservation: ``SKIPPED`` with no effort counted.

    Raises:
        ValidationError: If ``timestamp`` is not UTC.
    """
    return SnmpDeviceObservation(
        observation_id or new_id(ObservationId), poll_id, collector_id, device_id, timestamp,
        version, SnmpState.SKIPPED, UptimeContinuity.UNKNOWN, 0, 0, 0, 0, 0,
    )  # fmt: skip


def _metric_flags(rates: RateResult) -> Quality:
    """Collect per-metric flags that belong on the whole observation.

    Args:
        rates: The derivation result.

    Returns:
        Quality: Every metric's flags; with a valid baseline, without the
        baseline flags, because one missing metric must not mark the whole
        sample invalid (E.17 #11).

    Raises:
        None
    """
    flags = Quality.NONE
    for name in COUNTERS:
        flags |= rates.metric(name).quality
    return flags & ~BASELINE_INVALIDATING if rates.baseline is BaselineState.VALID else flags


def build_interface_observation(
    sample: InterfaceSample,
    rates: RateResult,
    *,
    poll_id: PollId,
    collector_id: CollectorId,
    device_id: DeviceId,
    interface_id: InterfaceId | None = None,
    elapsed_s: float | None = None,
    observation_id: ObservationId | None = None,
) -> InterfaceObservation:
    """Combine a raw sample and its derived rates into an interface observation (10.3).

    Args:
        sample: The raw counters.
        rates: ``compute_rates(previous, sample.counter_sample(), context)``.
        poll_id: The ``DevicePoll``.
        collector_id: This collector.
        device_id: The device.
        interface_id: Persistent UUID; defaults to ``sample.interface_id``.
        elapsed_s: Seconds since the previous sample, when there was one.
        observation_id: A fixed UUID, for tests; new when ``None``.

    Returns:
        InterfaceObservation: Counters, rates if the baseline was valid, flags.

    Raises:
        ValueError: If no persistent interface UUID is known (invariant 6).
        ValidationError: If a value breaks the observation's rules.
    """
    iid = interface_id if interface_id is not None else sample.interface_id
    if iid is None:
        raise ValueError("an interface observation needs the persistent interface UUID")
    elapsed_us = None if elapsed_s is None or elapsed_s <= 0 else max(1, round(elapsed_s * 1e6))
    return InterfaceObservation(
        observation_id=observation_id or new_id(ObservationId),
        poll_id=poll_id, collector_id=collector_id, device_id=device_id, interface_id=iid,
        timestamp=sample.sampled_at, if_index=sample.if_index, baseline_state=rates.baseline,
        quality=sample.quality | rates.quality | _metric_flags(rates),
        admin_status=sample.admin_status, oper_status=sample.oper_status,
        in_octets=sample.in_octets, out_octets=sample.out_octets,
        in_packets=sample.in_packets, out_packets=sample.out_packets,
        in_errors=sample.in_errors, out_errors=sample.out_errors,
        in_bps=rates.in_octets.rate, out_bps=rates.out_octets.rate,
        in_pps=rates.in_packets.rate, out_pps=rates.out_packets.rate,
        in_error_delta=rates.in_errors.delta, out_error_delta=rates.out_errors.delta,
        in_error_rate=rates.in_errors.rate, out_error_rate=rates.out_errors.rate,
        sample_elapsed_us=elapsed_us, counter_family=sample.family,
        counter_discontinuity_ticks=sample.discontinuity_ticks,
    )  # fmt: skip
