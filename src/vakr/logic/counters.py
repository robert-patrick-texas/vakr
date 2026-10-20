"""Interface counter samples and per-counter results for rate derivation (9.7).

One ``CounterSample`` holds the cumulative IF-MIB counters read from one
interface in one poll, plus the evidence needed to decide whether two samples
can be subtracted: counter family, ``ifCounterDiscontinuityTime``, and
whether the persistent interface identity is settled.

Widths follow IF-MIB. Octet and packet counters are 64-bit for the HC family
and 32-bit for the legacy family (SNMP-007). Error counters are always
32-bit, because IF-MIB defines no high-capacity error counters, so an error
counter can wrap even on an HC sample. Packet counters are aggregates of up
to three components (SNMP-008), so only their sign is validated.

Dependencies:
    dataclasses: Immutable values.
    vakr.domain.enums, vakr.domain.quality: ``CounterFamily`` and flags.

Safety:
    A missing counter is ``None``, never zero (blueprint 4.3).

Example:
    >>> s = CounterSample(CounterFamily.LEGACY32, in_octets=5, in_errors=2)
    >>> counter_width("in_octets", s.family), counter_width("in_errors", CounterFamily.HC64)
    (32, 32)
"""

from dataclasses import dataclass

from vakr.domain.enums import CounterFamily
from vakr.domain.quality import Quality

COUNTERS = ("in_octets", "out_octets", "in_packets", "out_packets", "in_errors", "out_errors")
"""Counter names, in the order results list them."""

OCTET_COUNTERS = frozenset({"in_octets", "out_octets"})
ERROR_COUNTERS = frozenset({"in_errors", "out_errors"})
PACKET_COUNTERS = frozenset({"in_packets", "out_packets"})


def counter_width(name: str, family: CounterFamily) -> int:
    """Return the bit width of one counter for a counter family.

    Args:
        name: A member of ``COUNTERS``.
        family: The family that produced the sample.

    Returns:
        int: 32 or 64.

    Raises:
        None
    """
    if name in ERROR_COUNTERS or family is CounterFamily.LEGACY32:
        return 32
    return 64


@dataclass(frozen=True, slots=True)
class CounterSample:
    """Cumulative counters from one interface in one poll.

    Attributes:
        family: Counter family used for octets and packets.
        in_octets: ``ifHCInOctets`` or ``ifInOctets``; None if not returned.
        out_octets: ``ifHCOutOctets`` or ``ifOutOctets``.
        in_packets: Aggregate unicast, multicast, and broadcast received.
        out_packets: Aggregate unicast, multicast, and broadcast sent.
        in_errors: ``ifInErrors`` (always 32-bit).
        out_errors: ``ifOutErrors`` (always 32-bit).
        discontinuity_ticks: ``ifCounterDiscontinuityTime``; None when the
            agent does not support it.
        identity_resolved: False while the interface's persistent identity
            is ambiguous or awaiting review (IFID-004).
    """

    family: CounterFamily
    in_octets: int | None = None
    out_octets: int | None = None
    in_packets: int | None = None
    out_packets: int | None = None
    in_errors: int | None = None
    out_errors: int | None = None
    discontinuity_ticks: int | None = None
    identity_resolved: bool = True

    def __post_init__(self) -> None:
        """Reject counter values the agent could not have sent.

        Args:
            None

        Returns:
            None

        Raises:
            ValueError: If a counter is negative, or an octet or error counter
                does not fit its width.
        """
        for name in COUNTERS:
            value = self.value(name)
            if value is None:
                continue
            limit = None if name in PACKET_COUNTERS else 2 ** counter_width(name, self.family)
            if value < 0 or (limit is not None and value >= limit):
                raise ValueError(f"{name}={value} is outside the counter's range")

    def value(self, name: str) -> int | None:
        """Return one counter by name.

        Args:
            name: A member of ``COUNTERS``.

        Returns:
            int | None: The cumulative value, or None if not returned.

        Raises:
            AttributeError: If ``name`` is not a counter field.
        """
        return getattr(self, name)


@dataclass(frozen=True, slots=True)
class MetricRate:
    """Delta and rate for one counter between two samples.

    Attributes:
        delta: Counter increase; None when unavailable.
        rate: Per second over the actual elapsed time (RATE-001): bits/s for
            octet counters (``delta * 8 / elapsed``, RATE-006), packets/s or
            errors/s otherwise. None when unavailable.
        quality: Why it is unavailable, or ``COUNTER_WRAP_32`` and
            ``RATE_EXCEEDS_CAPACITY`` evidence when it is not.
    """

    delta: int | None
    rate: float | None
    quality: Quality = Quality.NONE
