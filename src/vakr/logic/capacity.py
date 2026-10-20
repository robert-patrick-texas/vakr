"""Choose an interface's utilization denominator and its line rate (RATE-008..010).

Utilization is only as honest as its denominator. RATE-008 fixes the order:
an operator's explicit capacity override, then a contracted or committed
rate when it is configured as the denominator, then ``ifHighSpeed``
(reported in Mb/s and normalised to b/s), then ``ifSpeed``. The result names
the source so the UI can label it. With none of them, utilization is
unavailable rather than guessed (RATE-010).

``ifSpeed`` is a Gauge32: on links faster than about 4.29 Gb/s it is pinned
at 4,294,967,295 and only ``ifHighSpeed`` is meaningful (RFC 2863), so a
saturated ``ifSpeed`` is never used as a denominator. Zero means the agent
does not know the speed, so it counts as missing.

Reported speeds are a denominator only where they are a link's capacity:
physical ports and port-channels (whose speed is the sum of their member
links). A tunnel's, loopback's, VLAN interface's, or subinterface's
``ifSpeed`` is a configured or nominal number (IOS XE reports 100 kb/s for
many tunnels), so there only an operator's override or contracted rate is
used, and traffic is shown without a percentage (owner decision 2026-10-20,
Appendix E.17 #15).

Line rate is a separate question: the fastest the hardware can count, used
to judge 32-bit counter wraps (E.4). A contracted rate below the port speed
does not stop the counter from moving at port speed, so line rate ignores
contracts and takes the largest of the override and the reported speeds.

Dependencies:
    dataclasses, enum: Immutable inputs and results.

Safety:
    Pure. Missing or zero speeds yield ``None``, never a default capacity.

Example:
    >>> c = effective_capacity(CapacityInputs(if_high_speed_mbps=1000, if_speed_bps=IF_SPEED_MAX))
    >>> c.bps, c.source.value
    (1000000000, 'if_high_speed')
    >>> line_rate_bps(CapacityInputs(contracted_bps=50_000_000, contracted_is_denominator=True))
"""

from dataclasses import dataclass
from enum import StrEnum

IF_SPEED_MAX = 2**32 - 1  # Gauge32 ceiling; ifSpeed reads this on faster links
PORT_TYPES = frozenset({6, 7, 18, 19, 22, 30, 39, 62, 69, 71, 117})
"""IANAifType values of physical ports: Ethernet, DS1/E1/DS3, serial, SONET, Wi-Fi."""
LAG_TYPE = 161  # ieee8023adLag: a port-channel, whose speed is its members' sum
PRECISE_BELOW_MBPS = 20  # below this, ifSpeed (bit/s) is more precise than ifHighSpeed
BPS_PER_MBPS = 1_000_000


class CapacitySource(StrEnum):
    """Which configured or reported value is the utilization denominator (RATE-008)."""

    OVERRIDE = "override"
    CONTRACTED = "contracted"
    IF_HIGH_SPEED = "if_high_speed"
    IF_SPEED = "if_speed"


DENOMINATOR_LABELS = {
    CapacitySource.OVERRIDE: "capacity override",
    CapacitySource.CONTRACTED: "contracted rate",
    CapacitySource.IF_HIGH_SPEED: "ifHighSpeed",
    CapacitySource.IF_SPEED: "ifSpeed",
}
"""Operator-facing names for each denominator, for UI labels (RATE-008)."""


@dataclass(frozen=True, slots=True)
class CapacityInputs:
    """Everything known about an interface's capacity; None means not known.

    Attributes:
        override_bps: Operator-set capacity in bits per second.
        contracted_bps: Contracted or committed rate in bits per second.
        contracted_is_denominator: True when policy uses the contracted rate
            as the utilization denominator.
        if_high_speed_mbps: ``ifHighSpeed`` as reported, in Mb/s.
        if_speed_bps: ``ifSpeed`` as reported, in b/s.
        link_speed: True when the reported speeds are the link's capacity
            (``reports_link_capacity``); False makes them no denominator.
    """

    override_bps: int | None = None
    contracted_bps: int | None = None
    contracted_is_denominator: bool = False
    if_high_speed_mbps: int | None = None
    if_speed_bps: int | None = None
    link_speed: bool = True

    def __post_init__(self) -> None:
        """Reject negative rates, which no agent or config can mean.

        Args:
            None

        Returns:
            None

        Raises:
            ValueError: If any rate is negative.
        """
        values = (self.override_bps, self.contracted_bps, self.if_high_speed_mbps)
        if any(v is not None and v < 0 for v in (*values, self.if_speed_bps)):
            raise ValueError("capacity values must not be negative")


NO_CAPACITY = CapacityInputs()


@dataclass(frozen=True, slots=True)
class Capacity:
    """The denominator chosen for utilization.

    Attributes:
        bps: Capacity in bits per second; always positive.
        source: Where it came from.
    """

    bps: int
    source: CapacitySource

    @property
    def label(self) -> str:
        """Return the operator-facing name of the denominator.

        Args:
            None

        Returns:
            str: For example ``"ifHighSpeed"``.

        Raises:
            None
        """
        return DENOMINATOR_LABELS[self.source]


def _known(value: int | None) -> int | None:
    """Treat zero as unknown, as agents report 0 when they do not know.

    Args:
        value: A reported or configured rate.

    Returns:
        int | None: The value if positive, else None.

    Raises:
        None
    """
    return value if value else None


def effective_capacity(inputs: CapacityInputs) -> Capacity | None:
    """Pick the utilization denominator in RATE-008 order.

    Args:
        inputs: What is known about the interface.

    Returns:
        Capacity | None: The first usable source, or None when capacity is
        unknown and utilization must be shown as unavailable (RATE-010).

    Raises:
        None
    """
    contracted = _known(inputs.contracted_bps) if inputs.contracted_is_denominator else None
    reported = inputs.link_speed
    high_speed = _known(inputs.if_high_speed_mbps) if reported else None
    if_speed = _known(inputs.if_speed_bps) if reported else None
    # ifHighSpeed counts whole Mb/s, so a 1.544 Mb/s T1 reads 1 or 2. Below
    # PRECISE_BELOW_MBPS an unsaturated ifSpeed is the exact rate and wins:
    # this is the "normalised" ifHighSpeed of RATE-008 (Appendix E.17).
    if high_speed and high_speed < PRECISE_BELOW_MBPS and if_speed and if_speed != IF_SPEED_MAX:
        high_speed = None
    candidates = (
        (_known(inputs.override_bps), CapacitySource.OVERRIDE),
        (contracted, CapacitySource.CONTRACTED),
        (high_speed * BPS_PER_MBPS if high_speed else None, CapacitySource.IF_HIGH_SPEED),
        (if_speed if if_speed != IF_SPEED_MAX else None, CapacitySource.IF_SPEED),
    )
    for bps, source in candidates:
        if bps:
            return Capacity(bps, source)
    return None


def reports_link_capacity(if_type: int | None, if_name: str | None) -> bool:
    """Whether an interface's reported speed is its link capacity (E.17 #15).

    Args:
        if_type: IANAifType number, if reported.
        if_name: ``ifName``; a dot marks a subinterface of a port.

    Returns:
        bool: True for a physical port that is not a subinterface, for a
        port-channel, and when the agent reported no type (nothing says the
        speed is nominal); False for every other type.

    Raises:
        None
    """
    if if_type is None or if_type == LAG_TYPE:
        return True
    return if_type in PORT_TYPES and not (if_name and "." in if_name)


def line_rate_bps(inputs: CapacityInputs) -> int | None:
    """Return the fastest rate the counters can move at, for 32-bit wrap checks.

    A saturated ``ifSpeed`` still counts, as a lower bound: it can only make
    the wrap window shorter, which is the safe direction.

    Args:
        inputs: What is known about the interface.

    Returns:
        int | None: The largest of the override, ``ifHighSpeed`` (normalised),
        and ``ifSpeed``, or None when none is known. Contracted rates are
        ignored because they do not limit what the hardware counts.

    Raises:
        None
    """
    high_speed = _known(inputs.if_high_speed_mbps)
    rates = (
        _known(inputs.override_bps),
        high_speed * BPS_PER_MBPS if high_speed else None,
        _known(inputs.if_speed_bps),
    )
    return max((r for r in rates if r), default=None)
