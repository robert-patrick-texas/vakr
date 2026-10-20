"""ICMP prober port: probe one target with N Echo requests (ICMP-001..005, ICMP-011).

The scheduler asks an ``IcmpProber`` to run one reachability cycle per
device: ``count`` Echo requests ``spacing_s`` apart, each waiting
``timeout_s`` for its reply. The prober returns raw per-probe outcomes;
``vakr.collectors.icmp`` turns them into an ``IcmpObservation`` with the
E.1 state. The port knows nothing about SNMP or storage, and the value
types live beside it in ``vakr.ports.icmp_types``.

Dependencies:
    dataclasses, typing: Counter snapshot and the protocol.
    vakr.ports.icmp_types: Requests, samples, results, faults.

Safety:
    A prober reports collector trouble (no permission, socket failure,
    overload) as ``CollectorFault`` evidence, never as target loss
    (invariant 2, OBS-003).

Example:
    >>> request = ProbeRequest("2001:db8::1", count=5, spacing_s=0.2, timeout_s=1.0)
    >>> request.family.value, IcmpCounters().sent
    ('ipv6', 0)
"""

from dataclasses import dataclass
from typing import Protocol

from vakr.ports.icmp_types import (
    DEADLINE_SLACK_S,
    MAX_PROBES,
    NO_REPLY_US,
    OBSERVED,
    CollectorFault,
    ProbeOutcome,
    ProbeRequest,
    ProbeResult,
    ProbeSample,
    probe_address,
)

__all__ = [
    "DEADLINE_SLACK_S",
    "MAX_PROBES",
    "NO_REPLY_US",
    "OBSERVED",
    "CollectorFault",
    "IcmpCounters",
    "IcmpProber",
    "ProbeOutcome",
    "ProbeRequest",
    "ProbeResult",
    "ProbeSample",
    "probe_address",
]


@dataclass(frozen=True, slots=True)
class IcmpCounters:
    """Running totals for self-observability (OBS-001); all start at zero.

    Attributes:
        sent: Echo requests handed to the kernel.
        replies: Replies matched to a waiting probe.
        timeouts: Probes that timed out.
        unreachable: Probes answered by an ICMP error.
        late: Replies for a probe that had already timed out.
        duplicate: Extra replies for a probe already answered.
        foreign: Replies matching no probe this prober sent.
        not_sent: Probes the collector could not send.
        receive_errors: Socket read errors.
        receive_overflow: Replies the kernel dropped on a full socket buffer.
        sockets_open: Sockets currently open.
        in_flight: Probes currently waiting for a reply.
        in_flight_peak: Highest ``in_flight`` seen.
    """

    sent: int = 0
    replies: int = 0
    timeouts: int = 0
    unreachable: int = 0
    late: int = 0
    duplicate: int = 0
    foreign: int = 0
    not_sent: int = 0
    receive_errors: int = 0
    receive_overflow: int = 0
    sockets_open: int = 0
    in_flight: int = 0
    in_flight_peak: int = 0


class IcmpProber(Protocol):
    """Sends ICMP Echo probe groups; implemented by ``vakr.adapters.icmp``."""

    async def probe(self, request: ProbeRequest) -> ProbeResult:
        """Run one cycle and return every probe's outcome.

        Args:
            request: Target, count, spacing, timeout, and optional source.

        Returns:
            ProbeResult: One sample per planned probe. Collector trouble is
            reported through ``fault`` and ``NOT_SENT``/``ABORTED`` samples.

        Raises:
            None
        """
        ...

    def counters(self) -> IcmpCounters:
        """Return a snapshot of the running totals.

        Args:
            None

        Returns:
            IcmpCounters: Current values.

        Raises:
            None
        """
        ...
