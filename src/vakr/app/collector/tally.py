"""Collector-side trouble the steps saw, for the health observation (OBS-002, invariant 2).

When the collector itself cannot do its job (no ICMP permission, a
credential that does not resolve, a name that does not resolve, a socket
that cannot open), the poll is recorded as unknown rather than as a device
fault. The scheduler counts exceptions; this tally also counts the failures
the steps report as data, so the collector-health observation can say
"degraded" for them, and remembers the collector state the ICMP math should
assume (E.1: 0 replies from an unhealthy collector is ``UNKNOWN``).

Dependencies:
    collections.Counter: Counts by stable reason code.
    vakr.domain.enums: ``CollectorState``.

Safety:
    Reason codes are fixed strings; no addresses or secrets are kept.

Example:
    >>> tally = FaultTally()
    >>> tally.fault("snmp_session"); tally.take_period()
    1
"""

from collections import Counter
from dataclasses import dataclass, field

from vakr.domain.enums import CollectorState


@dataclass(slots=True)
class FaultTally:
    """Collector-side failures by reason, for the period and since start.

    Attributes:
        lifetime: Failures since start, by reason code.
        period: Failures since the last health observation.
        unresolved_interfaces: Up/up interfaces skipped in the last poll of
            each device because their persistent identity is unresolved.
        state: Collector state from the scheduler at the last health tick.
    """

    lifetime: Counter[str] = field(default_factory=Counter)
    period: int = 0
    unresolved_interfaces: dict[object, int] = field(default_factory=dict)
    state: CollectorState = CollectorState.HEALTHY

    def fault(self, reason: str) -> None:
        """Count one collector-side failure.

        Args:
            reason: Stable code, such as ``icmp_not_sent`` or ``snmp_session``.

        Returns:
            None

        Raises:
            None
        """
        self.lifetime[reason] += 1
        self.period += 1

    def take_period(self) -> int:
        """Return and reset the failures counted since the last call.

        Args:
            None

        Returns:
            int: The count.

        Raises:
            None
        """
        found, self.period = self.period, 0
        return found

    def unresolved_total(self) -> int:
        """Return how many polled interfaces lack a persistent identity.

        Args:
            None

        Returns:
            int: The sum over devices of their latest count.

        Raises:
            None
        """
        return sum(self.unresolved_interfaces.values())
