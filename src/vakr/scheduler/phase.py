"""Phase and order: where each device's poll falls inside its interval (SCHED-002..008, ADR-007).

Two independent controls (SCHED-002) place a device's poll:

* **Phase** shifts this collector's whole cycle. ``hostname`` (the default,
  SCHED-003) hashes the collector name, so two collectors watching the same
  routers do not probe them at the same instant. ``fixed`` uses an
  administrator-chosen offset instead (SCHED-004).
* **Order** places each device relative to that phase. ``common`` (the
  default) hashes the device UUID alone, so every collector visits devices
  in the same relative order. ``hashed`` hashes ``collector_name + "/" +
  device_uuid`` (SCHED-005, SCHED-006), giving each collector its own order.

A device's offset is ``(phase + order_key mod I) mod I`` in integer
microseconds, where ``I`` is its interval. Because the offset depends only
on the collector, the device, and its interval, adding or removing other
devices never moves it (reload stability), and hashed offsets spread polls
across the interval instead of bursting at the boundary (SCHED-008).
Hashing reduces collisions; it does not guarantee that two devices never
share a start time (SCHED-007).

Dependencies:
    vakr.scheduler.hashing: The E.11 hash.

Safety:
    Pure and deterministic; the same inputs give the same offsets in any
    process or language.

Example:
    >>> from uuid import UUID
    >>> rule = PhaseRule("vakr-east")
    >>> device = UUID("0190a3c4-0000-7000-8000-000000000001")
    >>> 0 <= rule.offset_us(device, 60) < 60_000_000
    True
    >>> rule.offset_us(device, 60) == PhaseRule("vakr-east").offset_us(device, 60)
    True
"""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from vakr.scheduler.hashing import hash64

US_PER_S = 1_000_000


class PhaseMode(StrEnum):
    """Where this collector's cycle starts within each interval (SCHED-003, SCHED-004)."""

    HOSTNAME = "hostname"
    FIXED = "fixed"


class OrderMode(StrEnum):
    """How devices are placed relative to the phase (SCHED-003, SCHED-005)."""

    COMMON = "common"
    HASHED = "hashed"


@dataclass(frozen=True, slots=True)
class PhaseRule:
    """The phase and order controls of one collector.

    Attributes:
        collector_name: Collector name; hostname phase and hashed order hash it.
        phase: Phase mode.
        order: Order mode.
        fixed_phase_us: Offset for ``fixed`` phase, in microseconds; taken
            modulo each device's interval.
    """

    collector_name: str
    phase: PhaseMode = PhaseMode.HOSTNAME
    order: OrderMode = OrderMode.COMMON
    fixed_phase_us: int = 0

    def __post_init__(self) -> None:
        """Validate the rule.

        Args:
            None

        Returns:
            None

        Raises:
            ValueError: If the name is empty or the fixed phase is negative.
        """
        if not self.collector_name:
            raise ValueError("a phase rule needs a collector name")
        if self.fixed_phase_us < 0:
            raise ValueError("fixed_phase_us must not be negative")

    def order_input(self, device_id: UUID) -> str:
        """Return the text hashed for a device's order key.

        Args:
            device_id: The device UUID.

        Returns:
            str: ``"<uuid>"`` for common order, ``"<collector>/<uuid>"`` for hashed.

        Raises:
            None
        """
        if self.order is OrderMode.COMMON:
            return str(device_id)
        return f"{self.collector_name}/{device_id}"

    def order_key(self, device_id: UUID) -> int:
        """Return the device's 64-bit order key; it also breaks due-time ties.

        Args:
            device_id: The device UUID.

        Returns:
            int: ``hash64(order_input(device_id))``.

        Raises:
            None
        """
        return hash64(self.order_input(device_id))

    def collector_phase_us(self, interval_s: int) -> int:
        """Return where this collector's cycle starts within an interval.

        Args:
            interval_s: Interval in whole seconds, at least 1.

        Returns:
            int: Microseconds in ``[0, interval)``.

        Raises:
            None
        """
        interval_us = interval_s * US_PER_S
        if self.phase is PhaseMode.FIXED:
            return self.fixed_phase_us % interval_us
        return hash64(self.collector_name) % interval_us

    def offset_us(self, device_id: UUID, interval_s: int) -> int:
        """Return the device's poll offset within each interval.

        Args:
            device_id: The device UUID.
            interval_s: The device's effective interval in whole seconds.

        Returns:
            int: Microseconds after each interval boundary, in ``[0, interval)``.

        Raises:
            None
        """
        interval_us = interval_s * US_PER_S
        position = self.order_key(device_id) % interval_us
        return (self.collector_phase_us(interval_s) + position) % interval_us


def first_slot_us(offset_us: int, interval_us: int, not_before_us: int) -> int:
    """Return the earliest slot on a device's grid at or after a moment.

    Slots are ``k * interval + offset`` on the plan timeline, which counts
    microseconds from the Unix epoch, so a device polls at the same offset
    past every interval boundary.

    Args:
        offset_us: The device offset within its interval.
        interval_us: The interval in microseconds.
        not_before_us: Earliest acceptable slot time.

    Returns:
        int: The first slot time at or after ``not_before_us``.

    Raises:
        None
    """
    k = -((offset_us - not_before_us) // interval_us)  # ceil((not_before - offset) / interval)
    return k * interval_us + offset_us
