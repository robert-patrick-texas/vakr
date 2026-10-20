"""Summarise one ICMP probe group and decide whether SNMP runs (9.3, Appendix E.1, E.7).

A reachability cycle sends five Echo probes (ICMP-001). Each probe either
returns an RTT in integer microseconds or is lost. This module turns that
group into the stored summary (ICMP-008), the reachability state an operator
sees, and the SNMP gate for the device's strategy (ICMP-006, -007, -013, -014).

Cisco IOS XE Control Plane Policing rate-limits ICMP to the route processor,
so losing one probe in five is routine: 4/5 stays REACHABLE with
``PARTIAL_LOSS``. One reply proves the device answers, so UNREACHABLE means
zero replies.

Dependencies:
    dataclasses, enum, fractions: Immutable inputs and results; exact thresholds.
    vakr.logic.stats: Exact half-to-even summary statistics (E.7).
    vakr.domain.enums, vakr.domain.quality: Shared states and flags.

Safety:
    Pure. A lost probe is ``None`` and never enters latency or jitter math
    (ICMP-005). Collector trouble yields ``UNKNOWN``, never ``UNREACHABLE``
    (invariant 2).

Example:
    >>> s = summarise_probes(ProbeGroup((1200, 1300, None, 1250, 1400), sent=5))
    >>> s.state.name, s.loss_percent, s.median_rtt_us, s.quality.name
    ('REACHABLE', 20.0, 1275, 'PARTIAL_LOSS')
    >>> snmp_plan(1, ReachabilityStrategy.ICMP_GATED).name
    'SKIP'
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction

from vakr.domain.enums import CollectorState, IcmpState, ReachabilityStrategy
from vakr.domain.quality import Quality
from vakr.logic import stats

NO_REPLY_SENTINEL = -1  # raw storage value for a lost probe (ICMP-004)


class Impairment(StrEnum):
    """Why a group is ``IMPAIRED``; drives the E.2 row 6 label."""

    LOSS = "loss"
    LATENCY = "latency"


class SnmpPlan(StrEnum):
    """What the SNMP part of this cycle should do after the ICMP group."""

    COLLECT = "collect"  # normal Stage 1 and Stage 2
    STAGE1_FALLBACK = "stage1_fallback"  # one low-cost Stage 1 request (ICMP-013)
    SKIP = "skip"


@dataclass(frozen=True, slots=True)
class ProbeGroup:
    """The probes of one reachability cycle, in send order (ICMP-003).

    Attributes:
        rtts_us: One entry per probe sent: RTT in integer microseconds, or
            ``None`` when no reply arrived. Empty when nothing was sent.
        sent: Number of probes sent; must equal ``len(rtts_us)``. Zero means
            the poll was not attempted.
    """

    rtts_us: tuple[int | None, ...]
    sent: int

    def __post_init__(self) -> None:
        """Reject inputs that would corrupt the summary.

        Args:
            None

        Returns:
            None

        Raises:
            ValueError: If ``sent`` disagrees with the RTT count, or an RTT is
                negative (a raw ``-1`` must be converted with ``from_raw``).
        """
        if self.sent != len(self.rtts_us):
            raise ValueError(f"sent={self.sent} but {len(self.rtts_us)} RTT entries given")
        if any(r is not None and r < 0 for r in self.rtts_us):
            raise ValueError("RTTs must be non-negative microseconds; use None for no reply")

    @classmethod
    def from_raw(cls, raw: Sequence[int]) -> ProbeGroup:
        """Build a group from stored values, where ``-1`` means no reply (ICMP-004).

        Args:
            raw: Stored per-probe values in send order.

        Returns:
            ProbeGroup: The same probes with ``-1`` replaced by ``None``.

        Raises:
            ValueError: If a value is negative but not the sentinel.
        """
        rtts = tuple(None if v == NO_REPLY_SENTINEL else v for v in raw)
        return cls(rtts, sent=len(rtts))


@dataclass(frozen=True, slots=True)
class IcmpPolicy:
    """Reachability thresholds; defaults are Appendix E.1.

    Attributes:
        impaired_loss_percent: Loss at or above this makes the state IMPAIRED
            (``icmp.impaired_loss_percent``).
        impaired_rtt_ms: When set, a median RTT above it makes the state
            IMPAIRED (``icmp.impaired_rtt_ms``, unset by default).
    """

    impaired_loss_percent: float = 40.0
    impaired_rtt_ms: float | None = None

    def __post_init__(self) -> None:
        """Validate the thresholds.

        Args:
            None

        Returns:
            None

        Raises:
            ValueError: If the loss threshold is outside (0, 100] or the RTT
                threshold is not positive.
        """
        if not 0 < self.impaired_loss_percent <= 100:
            raise ValueError("impaired_loss_percent must be in (0, 100]")
        if self.impaired_rtt_ms is not None and self.impaired_rtt_ms <= 0:
            raise ValueError("impaired_rtt_ms must be positive when set")


DEFAULT_POLICY = IcmpPolicy()


@dataclass(frozen=True, slots=True)
class IcmpSummary:
    """Stored summary of one probe group (ICMP-008); RTTs in integer microseconds.

    Attributes:
        sent: Probes sent.
        received: Replies received.
        lost: ``sent - received``.
        loss_percent: One decimal place, half to even; None if nothing was sent.
        min_rtt_us: Smallest RTT; None without replies.
        avg_rtt_us: Mean, rounded half to even; None without replies.
        median_rtt_us: Median, rounded half to even; None without replies.
        max_rtt_us: Largest RTT; None without replies.
        jitter_us: Population standard deviation, rounded half to even; None
            with fewer than two replies (ICMP-010).
        state: Reachability state (E.1).
        quality: Evidence flags.
        impairments: Why the state is IMPAIRED; empty otherwise.
    """

    sent: int
    received: int
    lost: int
    loss_percent: float | None
    min_rtt_us: int | None
    avg_rtt_us: int | None
    median_rtt_us: int | None
    max_rtt_us: int | None
    jitter_us: int | None
    state: IcmpState
    quality: Quality
    impairments: tuple[Impairment, ...]


def _impairments(replies: list[int], sent: int, policy: IcmpPolicy) -> tuple[Impairment, ...]:
    """List the E.1 impairment reasons for a group with at least one reply.

    Args:
        replies: Successful RTTs in microseconds, at least one.
        sent: Probes sent.
        policy: Thresholds.

    Returns:
        tuple[Impairment, ...]: LOSS and/or LATENCY, in that order.

    Raises:
        None
    """
    found: list[Impairment] = []
    lost = sent - len(replies)
    lossy = stats.percent_one_decimal(lost, sent) >= policy.impaired_loss_percent
    if lost and (lossy or len(replies) == 1):
        found.append(Impairment.LOSS)
    limit = policy.impaired_rtt_ms
    # Fraction(str(...)) keeps a threshold such as 1.005 ms exact in microseconds.
    if limit is not None and stats.median_half_even(replies) > Fraction(str(limit)) * 1000:
        found.append(Impairment.LATENCY)
    return tuple(found)


def _classify(
    replies: list[int], sent: int, policy: IcmpPolicy, collector: CollectorState
) -> tuple[IcmpState, Quality, tuple[Impairment, ...]]:
    """Apply the E.1 table.

    Args:
        replies: Successful RTTs in microseconds.
        sent: Probes sent.
        policy: Thresholds.
        collector: Health of the collector that sent the probes.

    Returns:
        tuple[IcmpState, Quality, tuple[Impairment, ...]]: State, flags, and
        impairment reasons.

    Raises:
        None
    """
    flags = Quality.NONE if collector is CollectorState.HEALTHY else Quality.COLLECTOR_DEGRADED
    if sent == 0:
        return IcmpState.UNKNOWN, flags | Quality.NOT_ATTEMPTED, ()
    if not replies:
        # Collector trouble can drop replies, so it cannot prove the target is down.
        return (IcmpState.UNKNOWN if flags else IcmpState.UNREACHABLE), flags, ()
    found = _impairments(replies, sent, policy)
    if len(replies) == 1:
        # One reply proves reachability but is too little to call the path healthy.
        return IcmpState.IMPAIRED, flags | Quality.SINGLE_REPLY, found
    if len(replies) < sent and Impairment.LOSS not in found:
        flags |= Quality.PARTIAL_LOSS
    return (IcmpState.IMPAIRED if found else IcmpState.REACHABLE), flags, found


def summarise_probes(
    group: ProbeGroup,
    policy: IcmpPolicy = DEFAULT_POLICY,
    collector: CollectorState = CollectorState.HEALTHY,
) -> IcmpSummary:
    """Summarise one probe group and classify reachability (9.3, E.1, E.7).

    Args:
        group: Probes of one cycle; empty when the poll was not attempted.
        policy: Thresholds, defaulting to Appendix E.1.
        collector: Health of the collector that sent the probes. Anything
            but HEALTHY adds ``COLLECTOR_DEGRADED`` and turns 0/5 into UNKNOWN.

    Returns:
        IcmpSummary: Counts, rounded latency summary, state, and flags.

    Raises:
        None
    """
    replies = sorted(r for r in group.rtts_us if r is not None)
    n, lost = len(replies), group.sent - len(replies)
    state, quality, impairments = _classify(replies, group.sent, policy, collector)
    return IcmpSummary(
        sent=group.sent,
        received=n,
        lost=lost,
        loss_percent=stats.percent_one_decimal(lost, group.sent) if group.sent else None,
        min_rtt_us=replies[0] if n else None,
        avg_rtt_us=stats.mean_half_even(replies) if n else None,
        median_rtt_us=stats.median_half_even(replies) if n else None,
        max_rtt_us=replies[-1] if n else None,
        jitter_us=stats.pstdev_half_even(replies) if n >= 2 else None,
        state=state,
        quality=quality,
        impairments=impairments,
    )


def snmp_plan(received: int, strategy: ReachabilityStrategy) -> SnmpPlan:
    """Decide the SNMP part of a cycle from the ICMP reply count (ICMP-006..014).

    SNMP success never changes the ICMP summary: under ``snmp_fallback`` a
    lost group stays lost and the device shows "ICMP lost, SNMP OK" (E.2 row 5).

    Args:
        received: Replies in this cycle's probe group (0 if not probed).
        strategy: The device's reachability strategy.

    Returns:
        SnmpPlan: COLLECT for 2+ replies under ``icmp_gated``/``snmp_fallback``
        and always under ``snmp_only``; STAGE1_FALLBACK for 0-1 reply under
        ``snmp_fallback``; otherwise SKIP, including always under ``icmp_only``.

    Raises:
        None
    """
    if strategy is ReachabilityStrategy.SNMP_ONLY:
        return SnmpPlan.COLLECT
    if strategy is ReachabilityStrategy.ICMP_ONLY:
        return SnmpPlan.SKIP
    if received >= 2:
        return SnmpPlan.COLLECT
    if strategy is ReachabilityStrategy.SNMP_FALLBACK:
        return SnmpPlan.STAGE1_FALLBACK
    return SnmpPlan.SKIP
