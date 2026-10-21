"""Observations of one poll of an end-to-end device, as the collector would write them (ICMP-009).

``poll_rows`` builds the poll record, its ICMP probe group, its SNMP reply,
and the interface samples of one poll, sharing one poll ID, for a device's
planned behaviour: OK (five replies), packet loss (three of five), or down
(no reply, so SNMP is skipped under ``icmp_gated``). ``sample_row`` builds
one interface sample with deterministic, diurnal rates, or with an invalid
baseline and no rates (UI-010: such a stretch is a gap, never zero).

Dependencies:
    tests.contract.contract_samples: Valid observation builders.
    vakr.domain: Enums and quality flags.

Safety:
    Pure builders; no I/O.

Example:
    >>> rtt_summary((900, None, 1100))["median_rtt_us"]
    1100
"""

import math
from datetime import datetime, timedelta
from typing import Any

from tests.contract import contract_samples as samples
from vakr.domain.catalog import Collector
from vakr.domain.enums import (
    BaselineState,
    IcmpState,
    IfAdminStatus,
    IfOperStatus,
    SnmpState,
    UptimeContinuity,
)
from vakr.domain.ids import PollId, new_id
from vakr.domain.interfaces import Interface
from vakr.domain.inventory import Device
from vakr.domain.observations import Observation
from vakr.domain.quality import Quality

DAY_S = 86_400
BOOT_UPTIME_S = 41 * DAY_S
"""``sysUpTime`` of every device at the seed's start: 41 days, no restart in view."""


def rtt_summary(rtts: tuple[int | None, ...]) -> dict[str, int | None]:
    """Return the RTT summary fields an ICMP observation needs.

    Args:
        rtts: Per-probe RTTs in microseconds; ``None`` is a lost probe.

    Returns:
        dict: Min, average, median, maximum, and jitter; all ``None`` without replies.

    Raises:
        None
    """
    replies = sorted(r for r in rtts if r is not None)
    keys = ("min_rtt_us", "avg_rtt_us", "median_rtt_us", "max_rtt_us", "jitter_us")
    if not replies:
        return dict.fromkeys(keys)
    median = replies[len(replies) // 2]
    jitter = max(replies) - min(replies) if len(replies) > 1 else None
    return dict(zip(keys, (replies[0], sum(replies) // len(replies), median, replies[-1], jitter),
                    strict=True))  # fmt: skip


def probe_rtts(minute: int, behavior: str) -> tuple[int | None, ...]:
    """Return five deterministic probe RTTs for one poll.

    Args:
        minute: Minutes since the epoch of the poll, for variation.
        behavior: ``ok``, ``loss`` (two lost), or ``down`` (all lost).

    Returns:
        tuple: Five RTTs in microseconds or ``None``.

    Raises:
        None
    """
    if behavior == "down":
        return (None,) * 5
    base = 1000 + int(300 * math.sin(minute / 37.0))
    rtts: list[int | None] = [base + (k * 97 + minute * 31) % 400 for k in range(5)]
    if behavior == "loss":
        rtts[1] = rtts[3] = None
    return tuple(rtts)


def icmp_state(rtts: tuple[int | None, ...]) -> tuple[IcmpState, Quality]:
    """Classify a probe group as E.1 does.

    Args:
        rtts: The five RTTs.

    Returns:
        tuple[IcmpState, Quality]: The state and its quality flag.

    Raises:
        None
    """
    replies = sum(1 for r in rtts if r is not None)
    if replies == 0:
        return IcmpState.UNREACHABLE, Quality.NONE
    if replies == 5:
        return IcmpState.REACHABLE, Quality.NONE
    if replies == 4:
        return IcmpState.REACHABLE, Quality.PARTIAL_LOSS
    return IcmpState.IMPAIRED, Quality.SINGLE_REPLY if replies == 1 else Quality.NONE


def device_rows(
    device: Device, collector: Collector, at: datetime, rtts: tuple[int | None, ...], boot: datetime
) -> tuple[PollId, list[Observation]]:
    """Build the poll record, ICMP group, and (when ICMP allows it) SNMP reply of one poll.

    Args:
        device: The device.
        collector: The polling collector.
        at: The scheduled time (UTC).
        rtts: The probe group.
        boot: When the device's agent started (for ``sysUpTime``).

    Returns:
        tuple: The poll ID and the observations.

    Raises:
        None
    """
    pid = new_id(PollId)
    state, quality = icmp_state(rtts)
    rows: list[Observation] = [
        samples.poll(device.id, collector.id, at, poll_id=pid),
        samples.icmp(device.id, collector.id, at, poll_id=pid, state=state, quality=quality,
                     rtts_us=rtts, target_address=device.poll_address, **rtt_summary(rtts)),
    ]  # fmt: skip
    if state is not IcmpState.UNREACHABLE and quality is not Quality.SINGLE_REPLY:
        ticks = int((at - boot).total_seconds() * 100)
        rows.append(samples.snmp(device.id, collector.id, at + timedelta(seconds=2), poll_id=pid,
                                 state=SnmpState.RESPONDING, sys_uptime_ticks=ticks,
                                 uptime_continuity=UptimeContinuity.CONTINUOUS))  # fmt: skip
    return pid, rows


def rates(minute: int, speed_mbps: int, salt: int) -> dict[str, float]:
    """Return deterministic diurnal rates for one sample.

    Args:
        minute: Minutes since the epoch of the sample.
        speed_mbps: Link speed, so utilization stays plausible.
        salt: Per-interface offset.

    Returns:
        dict[str, float]: ``in_bps``, ``out_bps``, ``in_pps``, ``out_pps``.

    Raises:
        None
    """
    day = math.sin(2 * math.pi * ((minute + salt * 53) % 1440) / 1440)
    in_bps = speed_mbps * 1e6 * (0.25 + 0.15 * day) + (minute * 7919 + salt) % 50_000
    out_bps = in_bps * 0.4
    return {
        "in_bps": in_bps,
        "out_bps": out_bps,
        "in_pps": in_bps / 4000,
        "out_pps": out_bps / 4000,
    }


def sample_row(
    iface: Interface, collector: Collector, at: datetime, pid: PollId, **opts: Any
) -> Observation:
    """Build one interface sample.

    Args:
        iface: The interface.
        collector: The collector.
        at: Sample time (UTC).
        pid: The poll it belongs to.
        **opts: ``speed_mbps``, ``salt``, ``invalid`` (a baseline-invalidating
            ``Quality`` flag: no rates), ``errors`` (inbound error delta),
            ``oper_down`` (oper status down, zero traffic).

    Returns:
        Observation: The sample.

    Raises:
        None
    """
    minute = int(at.timestamp() // 60)
    base: dict[str, Any] = {"poll_id": pid, "if_index": iface.if_index or 1, "in_octets": None,
                            "out_octets": None, "admin_status": IfAdminStatus.UP,
                            "oper_status": IfOperStatus.UP}  # fmt: skip
    invalid: Quality | None = opts.get("invalid")
    if invalid is not None:
        base |= {"baseline_state": BaselineState.INVALIDATED, "quality": invalid,
                 "in_bps": None, "out_bps": None, "sample_elapsed_us": None}  # fmt: skip
    else:
        base |= rates(minute, opts.get("speed_mbps", 1000), opts.get("salt", 0))
        errors = int(opts.get("errors", 0))
        base |= {"in_error_delta": errors, "out_error_delta": 0,
                 "in_error_rate": errors / 60, "out_error_rate": 0.0}  # fmt: skip
    if opts.get("oper_down"):
        base |= {"oper_status": IfOperStatus.DOWN}
        if invalid is None:
            base |= {"in_bps": 0.0, "out_bps": 0.0, "in_pps": 0.0, "out_pps": 0.0}
    return samples.sample(iface.device_id, iface.id, collector.id, at, **base)
