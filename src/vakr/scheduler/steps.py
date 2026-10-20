"""The step contract between the scheduler and the ICMP and SNMP collectors (9.2, SCHED-009).

The scheduler does not know how to ping or speak SNMP. It drives two small
async protocols, declared here so the planning code never imports a
protocol adapter:

* ``IcmpStep.icmp`` sends one probe group and reports how many probes were
  answered, plus the observations it produced.
* ``SnmpStep.stage1`` reads ``sysUpTime`` and exact interface status;
  ``SnmpStep.stage2`` reads counters for the interfaces that Stage 1 found
  up/up, building its requests from that Stage 1 result (ADR-005).

A step reports what the *target* did by returning a result. It raises only
for collector-side trouble (a socket that cannot be opened, a bug), which
the scheduler records as ``StepStatus.ERROR``: unknown, never target-down
(invariant 2). A step's own timeouts are bounded by its adapter (E.9) and
the whole poll by ``scheduler.poll_deadline``.

Dependencies:
    typing.Protocol: Structural step interfaces.
    vakr.domain: IDs and quality flags.

Safety:
    Contexts and results carry IDs, counts, and observations; credentials
    stay inside the SNMP adapter, which resolves ``credential_ref`` itself.

Example:
    >>> IcmpResult(replies=4, sent=5).replies
    4
    >>> SnmpResult(StepStatus.NO_RESPONSE).answered
    False
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from vakr.domain.ids import CollectorId, PollId
from vakr.domain.quality import Quality
from vakr.scheduler.targets import PollTarget


class StepStatus(StrEnum):
    """How one step of a poll ended."""

    OK = "ok"  # the target answered everything asked
    PARTIAL = "partial"  # the target answered part of it (SNMP-014)
    NO_RESPONSE = "no_response"  # the target did not answer: timeout or SNMP error
    SKIPPED = "skipped"  # the strategy or the ICMP gate left the step out
    ERROR = "error"  # collector-side failure; evidence about the collector only
    DEADLINE = "deadline"  # the poll deadline ended the step
    CANCELLED = "cancelled"  # shutdown cancelled the step (16.4)


ANSWERED = frozenset({StepStatus.OK, StepStatus.PARTIAL})
"""Statuses that are evidence the target answered."""


@dataclass(frozen=True, slots=True)
class PollContext:
    """What a step needs to know about the poll it serves.

    Attributes:
        poll_id: Shared by every observation of this poll.
        collector_id: The collector running it.
        target: The device and its settings.
        scheduled_at: The slot's UTC label.
    """

    poll_id: PollId
    collector_id: CollectorId
    target: PollTarget
    scheduled_at: datetime


@dataclass(frozen=True, slots=True)
class IcmpResult:
    """Outcome of one ICMP probe group.

    Attributes:
        replies: Probes answered, ``0..sent``.
        sent: Probes sent.
        observations: Observations produced, normally one ``IcmpObservation``.
        quality: Flags to add to the poll record.
    """

    replies: int
    sent: int
    observations: tuple[object, ...] = ()
    quality: Quality = Quality.NONE

    def __post_init__(self) -> None:
        """Validate the counts.

        Args:
            None

        Returns:
            None

        Raises:
            ValueError: If ``replies`` is outside ``0..sent``.
        """
        if not 0 <= self.replies <= self.sent:
            raise ValueError("an ICMP result needs 0 <= replies <= sent")


@dataclass(frozen=True, slots=True)
class SnmpResult:
    """Outcome of one SNMP stage.

    Attributes:
        status: ``OK``, ``PARTIAL``, or ``NO_RESPONSE``.
        up_up: Stage 1 only: interfaces admin-up and oper-up now (ADR-005).
        detail: Stage 1 only: adapter state handed to Stage 2, such as the
            current up/up interface set; opaque to the scheduler.
        observations: Observations produced (device and interface).
        quality: Flags to add to the poll record.
    """

    status: StepStatus
    up_up: int = 0
    detail: object = None
    observations: tuple[object, ...] = ()
    quality: Quality = Quality.NONE

    def __post_init__(self) -> None:
        """Validate the status.

        Args:
            None

        Returns:
            None

        Raises:
            ValueError: If the status is not one a target can produce, or
                ``up_up`` is negative.
        """
        if self.status not in (*ANSWERED, StepStatus.NO_RESPONSE):
            raise ValueError("an SNMP result status must be ok, partial, or no_response")
        if self.up_up < 0:
            raise ValueError("up_up must not be negative")

    @property
    def answered(self) -> bool:
        """Report whether the target answered at least part of the request.

        Args:
            None

        Returns:
            bool: True for ``OK`` and ``PARTIAL``.

        Raises:
            None
        """
        return self.status in ANSWERED


class IcmpStep(Protocol):
    """Sends one ICMP probe group for a poll (ICMP-001..005)."""

    async def icmp(self, ctx: PollContext) -> IcmpResult:
        """Probe the target.

        Args:
            ctx: The poll.

        Returns:
            IcmpResult: Replies and observations.

        Raises:
            Exception: Only for collector-side failures.
        """
        ...


class SnmpStep(Protocol):
    """Runs SNMP Stage 1 and Stage 2 for a poll (SNMP-*, ADR-005)."""

    async def stage1(self, ctx: PollContext, fallback: bool) -> SnmpResult:
        """Read uptime and exact interface status.

        Args:
            ctx: The poll.
            fallback: True for the single low-cost ``snmp_fallback``
                attempt: one request, no retries, no Stage 2 (ICMP-013).

        Returns:
            SnmpResult: Status, current up/up count, and observations.

        Raises:
            Exception: Only for collector-side failures.
        """
        ...

    async def stage2(self, ctx: PollContext, stage1: SnmpResult) -> SnmpResult:
        """Read counters for the interfaces the current Stage 1 found up/up.

        Args:
            ctx: The poll.
            stage1: This poll's Stage 1 result.

        Returns:
            SnmpResult: Status and interface observations.

        Raises:
            Exception: Only for collector-side failures.
        """
        ...


@dataclass(frozen=True, slots=True)
class PollSteps:
    """The step implementations a scheduler drives.

    Attributes:
        icmp: The ICMP step.
        snmp: The SNMP steps.
    """

    icmp: IcmpStep
    snmp: SnmpStep
