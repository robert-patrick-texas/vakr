"""Value types of the ICMP prober port: requests, samples, results (ICMP-001..005, -011).

A ``ProbeRequest`` asks for ``count`` Echo requests to one IP literal,
``spacing_s`` apart, waiting ``timeout_s`` for each reply. The result holds
one ``ProbeSample`` per planned probe and never guesses: a probe the
collector could not send, or could no longer watch, is collector evidence
(``NOT_SENT``, ``ABORTED``) with a ``CollectorFault``, not packet loss
(invariant 2, OBS-003). ``vakr.ports.icmp`` re-exports everything here.

Raw RTTs use the storage convention of ICMP-004: integer microseconds, or
``-1`` for no reply. ``vakr.logic.icmp.ProbeGroup.from_raw`` converts them.

Dependencies:
    dataclasses, enum, ipaddress: Value types and target checks.
    vakr.domain: Address family and ``ValidationError``.

Safety:
    Targets must be canonical unicast IP literals; multicast, unspecified,
    limited-broadcast, and IPv4-mapped targets are rejected (15.5). Every
    count and duration is bounded, so one request cannot run unbounded.

Example:
    >>> request = ProbeRequest("192.0.2.1", count=5, spacing_s=0.2, timeout_s=1.0)
    >>> request.family.value, round(request.deadline_s, 3)
    ('ipv4', 2.0)
"""

import ipaddress
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from vakr.domain.enums import AddressFamily
from vakr.domain.errors import ValidationError

NO_REPLY_US = -1
"""Raw RTT of a probe that got no reply (ICMP-004)."""

MAX_PROBES = 100
MAX_SPACING_S = 60.0
MIN_TIMEOUT_S = 0.001
MAX_TIMEOUT_S = 60.0
DEADLINE_SLACK_S = 0.2
"""Allowance for late sends after the last planned probe (Appendix E.22)."""


class ProbeOutcome(StrEnum):
    """What happened to one planned probe."""

    REPLY = "reply"  # an Echo reply carrying this probe's token arrived in time
    TIMEOUT = "timeout"  # sent; nothing arrived within the per-probe timeout
    UNREACHABLE = "unreachable"  # sent; the network returned an ICMP error for it
    NOT_SENT = "not_sent"  # the collector could not send it (collector evidence)
    ABORTED = "aborted"  # sent, but the collector stopped watching (collector evidence)


OBSERVED = frozenset({ProbeOutcome.REPLY, ProbeOutcome.TIMEOUT, ProbeOutcome.UNREACHABLE})
"""Outcomes that say something about the target; the others describe the collector."""


class CollectorFault(StrEnum):
    """Collector-side reasons a probe has no target evidence (OBS-002)."""

    PERMISSION_DENIED = "permission_denied"  # ping_group_range excludes us (ICMP-012)
    FAMILY_UNSUPPORTED = "family_unsupported"  # e.g. IPv6 disabled on the host
    SOCKET_ERROR = "socket_error"  # socket creation, reading, or resources failed
    SOURCE_UNAVAILABLE = "source_unavailable"  # the pinned source is not on this host
    SOCKET_LIMIT = "socket_limit"  # too many distinct sources in use
    OVERLOADED = "overloaded"  # in-flight cap, send buffer, or event-loop lag
    LOCAL_NO_ROUTE = "local_no_route"  # this host has no route to the target
    LOCAL_POLICY = "local_policy"  # a local firewall or broadcast rule refused it
    SEND_ERROR = "send_error"  # any other local send failure
    RECEIVE_OVERFLOW = "receive_overflow"  # the kernel dropped replies on our socket
    CANCELLED = "cancelled"  # the caller cancelled the cycle


@dataclass(frozen=True, slots=True)
class ProbeRequest:
    """One reachability cycle to run against one target.

    Attributes:
        target: Canonical IP literal to probe (DNS is resolved beforehand, 9.11).
        count: Probes to send (ICMP-001).
        spacing_s: Seconds between planned send times (ICMP-002).
        timeout_s: Seconds to wait for each probe's reply.
        source: Canonical source address to send from, or ``None`` to let
            the kernel choose (9.11, ICMP-011).
    """

    target: str
    count: int
    spacing_s: float
    timeout_s: float
    source: str | None = None

    def __post_init__(self) -> None:
        """Validate the target, source, and bounds.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If an address is not an acceptable canonical
                unicast literal, or a count or duration is out of range.
        """
        ip = probe_address(self.target, "target")
        if self.source is not None and probe_address(self.source, "source").version != ip.version:
            message = "source must have the same address family as target"
            raise ValidationError(message, field="source")
        if isinstance(self.count, bool) or not isinstance(self.count, int):
            raise ValidationError("count must be an integer", field="count")
        if not 1 <= self.count <= MAX_PROBES:
            raise ValidationError(f"count must be between 1 and {MAX_PROBES}", field="count")
        if not 0 <= self.spacing_s <= MAX_SPACING_S:
            raise ValidationError(f"spacing must be 0-{MAX_SPACING_S:g} s", field="spacing_s")
        if not MIN_TIMEOUT_S <= self.timeout_s <= MAX_TIMEOUT_S:
            raise ValidationError(f"timeout must be 1 ms-{MAX_TIMEOUT_S:g} s", field="timeout_s")

    @property
    def family(self) -> AddressFamily:
        """Return the address family of the target.

        Args:
            None

        Returns:
            AddressFamily: IPV4 or IPV6.

        Raises:
            None
        """
        version = ipaddress.ip_address(self.target).version
        return AddressFamily.IPV4 if version == 4 else AddressFamily.IPV6

    @property
    def deadline_s(self) -> float:
        """Return the hard upper bound on the cycle, from its start.

        A probe is sent only if its whole timeout fits before the deadline,
        so no reply window is ever cut short.

        Args:
            None

        Returns:
            float: ``(count - 1) * spacing + timeout + DEADLINE_SLACK_S``.

        Raises:
            None
        """
        return (self.count - 1) * self.spacing_s + self.timeout_s + DEADLINE_SLACK_S


def probe_address(text: object, field: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Parse an address that may be probed or used as a source.

    Args:
        text: Candidate canonical IP literal.
        field: Field name for error messages.

    Returns:
        ipaddress.IPv4Address | ipaddress.IPv6Address: The parsed address.

    Raises:
        ValidationError: If ``text`` is not a canonical IP literal, or is
            multicast, unspecified, limited broadcast, or IPv4-mapped.
    """
    try:
        ip = ipaddress.ip_address(text if isinstance(text, str) else "")
    except ValueError:
        raise ValidationError(f"{field} must be an IP address", field=field) from None
    if str(ip) != text:
        raise ValidationError(f"{field} must be written canonically as {ip}", field=field)
    if ip.is_multicast or ip.is_unspecified or str(ip) == "255.255.255.255":
        raise ValidationError(f"{field} must be a unicast address", field=field)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        raise ValidationError(f"{field}: use the IPv4 address {ip.ipv4_mapped}", field=field)
    return ip


@dataclass(frozen=True, slots=True)
class ProbeSample:
    """The outcome of one planned probe, in send order (ICMP-003).

    Attributes:
        index: Position in the cycle, from 0.
        outcome: What happened.
        rtt_us: Round-trip time in integer microseconds for ``REPLY``;
            ``NO_REPLY_US`` otherwise.
        sent_at: Wall-clock UTC send time, or ``None`` if never sent.
        fault: Collector reason for ``NOT_SENT`` and ``ABORTED``.
        detail: Operator-readable note, such as who reported an unreachable.
    """

    index: int
    outcome: ProbeOutcome
    rtt_us: int = NO_REPLY_US
    sent_at: datetime | None = None
    fault: CollectorFault | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Everything one cycle produced (ICMP-003, ICMP-011).

    Attributes:
        request: The request that was run.
        started_at: Wall-clock UTC start of the cycle, from the ``Clock``.
        duration_s: Cycle duration on the monotonic clock.
        samples: One sample per planned probe.
        fault: First collector fault seen, or ``None`` when the collector
            was able to observe every probe.
    """

    request: ProbeRequest
    started_at: datetime
    duration_s: float
    samples: tuple[ProbeSample, ...]
    fault: CollectorFault | None = None

    @property
    def raw_rtts_us(self) -> tuple[int, ...]:
        """Return raw RTTs of the probes that tell us about the target.

        Args:
            None

        Returns:
            tuple[int, ...]: Microseconds or ``-1``, in send order, for
            ``REPLY``, ``TIMEOUT``, and ``UNREACHABLE`` samples only.

        Raises:
            None
        """
        return tuple(s.rtt_us for s in self.samples if s.outcome in OBSERVED)

    @property
    def received(self) -> int:
        """Return the number of probes answered.

        Args:
            None

        Returns:
            int: ``REPLY`` samples.

        Raises:
            None
        """
        return sum(1 for s in self.samples if s.outcome is ProbeOutcome.REPLY)
