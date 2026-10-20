"""ICMP and SNMP device observation rows for the ADR-021 workload (blueprint 10.1-10.2).

ICMP rows carry five raw RTTs, with ``-1`` for a probe that got no reply,
plus the summary values of blueprint 9.3 and the Appendix E.1 state. Cisco
IOS XE Control Plane Policing drops the odd probe, so about 0.4% of probes
store ``-1`` even on a healthy device. SNMP rows record request accounting
and ``sysUpTime`` continuity; a reload shows as a reset.

Dependencies:
    random: The caller's per-minute ``Random`` stream.
    sqlc_topology: Device records, UUIDv7 and vocabulary-code helpers.
    vakr.domain: ``IcmpState``, ``SnmpState`` and related enums, ``Quality``.

Safety:
    Rows contain no community strings or credentials (blueprint 10.2).

Example:
    >>> summarize([1000, -1, 1200, 1100, 900])[:3]
    (4, 1000, 1050)
"""

import math
import random

from tests.performance.sqlite_compact.sqlc_topology import Device, uuid7_bytes, vocab_codes
from vakr.domain.enums import AddressFamily, IcmpState, SnmpState, SnmpVersion, UptimeContinuity
from vakr.domain.quality import Quality

ICMP_STATE = vocab_codes(IcmpState)
SNMP_STATE = vocab_codes(SnmpState)
CONTINUITY = vocab_codes(UptimeContinuity)
IPV4 = vocab_codes(AddressFamily)[AddressFamily.IPV4]
V2C = vocab_codes(SnmpVersion)[SnmpVersion.V2C]
PROBES = 5


def summarize(
    rtts: list[int],
) -> tuple[int, int | None, int | None, int | None, int | None, int | None]:
    """Summarize one five-probe group with the blueprint 9.3 formulas.

    Summary values round half to even, the documented integer rule here.

    Args:
        rtts (list[int]): Raw RTTs in microseconds; ``-1`` means no reply.

    Returns:
        tuple: ``(received, min, avg, median, max, jitter)``; the RTT values
        are ``None`` with no replies and jitter is ``None`` below two.

    Raises:
        None
    """
    ok = sorted(x for x in rtts if x >= 0)
    n = len(ok)
    if n == 0:
        return 0, None, None, None, None, None
    mean = sum(ok) / n
    mid = ok[n // 2] if n % 2 else (ok[n // 2 - 1] + ok[n // 2]) / 2
    jitter = round(math.sqrt(sum((x - mean) ** 2 for x in ok) / n)) if n >= 2 else None
    return n, ok[0], round(mean), round(mid), ok[-1], jitter


def _state(received: int) -> tuple[int, int]:
    """Map a reply count to the Appendix E.1 state code and quality flags.

    Args:
        received (int): Replies out of five probes.

    Returns:
        tuple[int, int]: ``(state code, quality flags)``.

    Raises:
        None
    """
    if received == PROBES:
        return ICMP_STATE[IcmpState.REACHABLE], 0
    if received == PROBES - 1:
        return ICMP_STATE[IcmpState.REACHABLE], int(Quality.PARTIAL_LOSS)
    if received >= 2:
        return ICMP_STATE[IcmpState.IMPAIRED], 0
    if received == 1:
        return ICMP_STATE[IcmpState.IMPAIRED], int(Quality.SINGLE_REPLY)
    return ICMP_STATE[IcmpState.UNREACHABLE], 0


def icmp_row(
    rng: random.Random, dev: Device, ck: int, ts: int, poll_uuid: bytes, down: bool
) -> tuple:
    """Build one ICMP observation row (24 columns, blueprint 10.1 order).

    Args:
        rng (random.Random): The minute's generator.
        dev (Device): Probed device.
        ck (int): Collector key.
        ts (int): Probe group start, Unix microseconds.
        poll_uuid (bytes): The device poll's UUID.
        down (bool): True when no probe gets a reply.

    Returns:
        tuple: The row.

    Raises:
        None
    """
    base, rand = dev.rtt_us[ck - 1], rng.random
    rtts = [-1] * PROBES
    if not down:
        for i in range(PROBES):
            if rand() >= 0.004:
                spike = base * (0.5 + 2.5 * rand()) if rand() < 0.01 else 0.0
                rtts[i] = int(base * (1.0 + 0.04 * abs(rng.gauss(0.0, 1.0))) + spike)
    received, lo, avg, med, hi, jitter = summarize(rtts)
    state, quality = _state(received)
    lost = PROBES - received
    return (
        dev.key,
        ck,
        ts,
        uuid7_bytes(ts // 1000, rng.getrandbits(74)),
        poll_uuid,
        IPV4,
        None,
        dev.address,
        *rtts,
        PROBES,
        received,
        lost,
        100.0 * lost / PROBES,
        lo,
        avg,
        med,
        hi,
        jitter,
        state,
        quality,
    )


def snmp_row(
    rng: random.Random,
    dev: Device,
    ck: int,
    ts: int,
    poll_uuid: bytes,
    down: bool,
    boot_us: int,
    reset: bool,
) -> tuple:
    """Build one SNMP device observation row (16 columns, blueprint 10.2 order).

    Args:
        rng (random.Random): The minute's generator.
        dev (Device): Polled device.
        ck (int): Collector key.
        ts (int): Stage 1 response time, Unix microseconds.
        poll_uuid (bytes): The device poll's UUID.
        down (bool): True when ICMP gating skipped SNMP for this poll.
        boot_us (int): Device boot time, for ``sysUpTime``.
        reset (bool): True when this collector first sees a new boot.

    Returns:
        tuple: The row.

    Raises:
        None
    """
    obs = uuid7_bytes(ts // 1000, rng.getrandbits(74))
    if down:
        return (
            dev.key,
            ck,
            ts,
            obs,
            poll_uuid,
            V2C,
            0,
            0,
            0,
            0,
            None,
            None,
            CONTINUITY[UptimeContinuity.UNKNOWN],
            SNMP_STATE[SnmpState.SKIPPED],
            None,
            int(Quality.NOT_ATTEMPTED),
        )
    # Stage 1 status in one PDU, then 30-varbind Stage 2 counter PDUs (E.4).
    requests = 1 + math.ceil(len(dev.iface_keys) * 12 / 30)
    retries = 1 if rng.random() < 0.002 else 0
    elapsed = 25 + requests * 6 + int(rng.random() * 40) + retries * 2000
    ticks = ((ts - boot_us) // 10_000) & 0xFFFF_FFFF
    continuity = UptimeContinuity.RESET_DETECTED if reset else UptimeContinuity.CONTINUOUS
    quality = int(Quality.RETRIED) if retries else 0
    return (
        dev.key,
        ck,
        ts,
        obs,
        poll_uuid,
        V2C,
        requests + retries,
        requests,
        retries,
        retries,
        elapsed,
        ticks,
        CONTINUITY[continuity],
        SNMP_STATE[SnmpState.RESPONDING],
        None,
        quality,
    )
