"""Lean read port for the MVP acceptance reports (MVP-ACC-01, MVP-ACC-02, MVP-ACC-10, E.35).

The acceptance reports judge a 7- to 14-day run of a whole node: about a
million poll records per collector and week, and 1,600 interfaces whose
native samples are far too many to rebuild as observations (ADR-021
measured 0.84 µs warm and 4 µs cold per native sample read). This port
returns only what the reports need, as integers in immutable named tuples
(cheaper to build by the million than frozen dataclasses):

- ``poll_marks``: one device's poll records from one collector, with
  scheduled, start, and completion times in epoch microseconds.
- ``sample_marks``: the time, quality, and per-metric presence of one
  interface's (or device's ICMP) native observations, for the short
  stretches a report inspects closely.
- ``bucket_marks``: the 15-minute rollup buckets of one subject, reduced
  to sample count, per-metric presence, and quality, so a report can find
  the few quarter-hours worth a native read without scanning every sample.

Shared semantics every backend reproduces (contract ``contract_acceptance``):

- Rows before the native (or rollup) horizon are expired and never returned.
- Marks are ordered by time, then observation UUID (oldest first), or
  newest first when ``newest_first`` is set; at most ``limit`` come back
  and ``truncated`` says whether more existed.
- ``available`` bit ``i`` is set when metric ``i`` of ``INTERFACE_METRICS``
  (interfaces) or ``ICMP_METRICS`` (ICMP) has a value: a sample's value is
  not ``None``, a bucket's ``valid_count`` is above zero. Missing stays
  missing (invariant 1).
- Bucket marks cover 15-minute buckets only. A backend without rollups
  raises ``NotImplementedError`` from ``bucket_marks``.

Dependencies:
    vakr.domain: Enums, IDs, quality, windows.
    vakr.ports.telemetry: The base backend and the rollup metric order.

Safety:
    Read-only, and bounded: one subject, one window, and ``limit`` marks per call.

Example:
    >>> MAX_MARKS, PollMark(0, 60, PollExecution.COMPLETE, Quality.NONE, 1, 2).duration_us
    (100000, 1)
"""

from dataclasses import dataclass
from typing import NamedTuple, Protocol
from uuid import UUID

from vakr.domain.checks import require_int
from vakr.domain.enums import ObservationType, PollExecution
from vakr.domain.errors import ValidationError
from vakr.domain.ids import CollectorId, DeviceId
from vakr.domain.quality import Quality
from vakr.domain.series import TimeWindow
from vakr.ports.telemetry import TelemetryBackend

MAX_MARKS = 100_000
"""Largest ``limit``: 31 days of polls at the 30 s minimum interval (E.18) is 89,280."""

MARK_TYPES = (ObservationType.INTERFACE, ObservationType.ICMP)
"""Observation types ``sample_marks`` and ``bucket_marks`` accept."""


def check_request(observation_type: ObservationType | None, limit: int) -> None:
    """Refuse a request every backend must refuse, so adapters agree (DB-004).

    Args:
        observation_type: The type a sample or bucket read asks for, or ``None`` for polls.
        limit: The requested limit.

    Returns:
        None

    Raises:
        ValidationError: If the type is not in ``MARK_TYPES`` or ``limit`` is out of range.
    """
    require_int(limit, "limit", minimum=1, maximum=MAX_MARKS)
    if observation_type is not None and observation_type not in MARK_TYPES:
        raise ValidationError(
            "marks are read for interface or ICMP observations", field="observation_type"
        )


class PollMark(NamedTuple):
    """One poll record reduced to its timing and outcome. Immutable.

    Attributes:
        scheduled_us: Scheduled time, epoch microseconds (UTC).
        interval_s: Effective interval in force, in seconds.
        execution: How far the poll got.
        quality: The poll's quality flags.
        started_us: Start time in epoch microseconds, if it started.
        completed_us: Completion time in epoch microseconds, if it finished.
    """

    scheduled_us: int
    interval_s: int
    execution: PollExecution
    quality: Quality
    started_us: int | None
    completed_us: int | None

    @property
    def duration_us(self) -> int | None:
        """Return how long the poll ran, if it started and finished.

        Args:
            None

        Returns:
            int | None: Microseconds, or ``None``.

        Raises:
            None
        """
        if self.started_us is None or self.completed_us is None:
            return None
        return self.completed_us - self.started_us


class SampleMark(NamedTuple):
    """One native interface or ICMP observation reduced to time, flags, and presence. Immutable.

    Attributes:
        ts_us: Observation time, epoch microseconds (UTC).
        quality: The observation's quality flags.
        available: Bit ``i`` set when metric ``i`` has a value (module docstring).
    """

    ts_us: int
    quality: Quality
    available: int


class BucketMark(NamedTuple):
    """One 15-minute rollup bucket reduced to counts, presence, and flags. Immutable.

    Attributes:
        start_us: Bucket start, epoch microseconds (UTC).
        samples: Observations the bucket summarises (the largest sample count of its metrics).
        available: Bit ``i`` set when metric ``i`` has a valid sample.
        quality: Union of the bucket's quality flags.
    """

    start_us: int
    samples: int
    available: int
    quality: Quality


class StatusMark(NamedTuple):
    """One interface status record reduced to its time and IF-MIB statuses. Immutable.

    Attributes:
        ts_us: When Stage 1 read it, epoch microseconds (UTC).
        admin_status: ``ifAdminStatus`` integer, or ``None``.
        oper_status: ``ifOperStatus`` integer, or ``None``.
        baseline: The collector's first record of the interface since it started.
    """

    ts_us: int
    admin_status: int | None
    oper_status: int | None
    baseline: bool

    @property
    def up_up(self) -> bool:
        """Whether both statuses were ``up`` (1).

        Args:
            None

        Returns:
            bool: Admin and oper up.

        Raises:
            None
        """
        return self.admin_status == 1 and self.oper_status == 1


@dataclass(frozen=True, slots=True)
class Marks[T]:
    """Marks of one call. Immutable.

    Attributes:
        items: The marks, in the requested order.
        truncated: Whether more marks matched than ``limit``.
    """

    items: tuple[T, ...]
    truncated: bool


class AcceptanceQuery(Protocol):
    """Bounded lean reads for the acceptance reports (E.35)."""

    def poll_marks(
        self, device_id: DeviceId, collector_id: CollectorId, window: TimeWindow, *,
        limit: int = MAX_MARKS,
    ) -> Marks[PollMark]:  # fmt: skip
        """Return one device's poll records from one collector, by scheduled time.

        Args:
            device_id: The device.
            collector_id: The collector.
            window: Bounded window on the scheduled time.
            limit: At most this many marks, 1 to ``MAX_MARKS``.

        Returns:
            Marks[PollMark]: Oldest first; two records of one slot are both returned.

        Raises:
            ValidationError: If ``limit`` is out of range.
            StorageError: If the store is unavailable.
        """
        ...

    def sample_marks(
        self, observation_type: ObservationType, subject_id: UUID, collector_id: CollectorId,
        window: TimeWindow, *, limit: int = MAX_MARKS, newest_first: bool = False,
    ) -> Marks[SampleMark]:  # fmt: skip
        """Return one subject's native interface or ICMP observations as marks.

        Args:
            observation_type: ``INTERFACE`` (subject: interface) or ``ICMP`` (subject: device).
            subject_id: The interface or device.
            collector_id: The collector.
            window: Bounded window on the observation time.
            limit: At most this many marks, 1 to ``MAX_MARKS``.
            newest_first: Return the newest marks first.

        Returns:
            Marks[SampleMark]: The marks.

        Raises:
            ValidationError: If the type is not in ``MARK_TYPES`` or ``limit`` is out of range.
            StorageError: If the store is unavailable.
        """
        ...

    def bucket_marks(
        self, observation_type: ObservationType, subject_id: UUID, collector_id: CollectorId,
        window: TimeWindow, *, limit: int = MAX_MARKS, newest_first: bool = False,
    ) -> Marks[BucketMark]:  # fmt: skip
        """Return one subject's 15-minute rollup buckets whose start lies in the window.

        Args:
            observation_type: ``INTERFACE`` (subject: interface) or ``ICMP`` (subject: device).
            subject_id: The interface or device.
            collector_id: The collector.
            window: Bounded window on the bucket start.
            limit: At most this many marks, 1 to ``MAX_MARKS``.
            newest_first: Return the newest buckets first.

        Returns:
            Marks[BucketMark]: The marks.

        Raises:
            NotImplementedError: If the backend has no rollups.
            ValidationError: If the type is not in ``MARK_TYPES`` or ``limit`` is out of range.
            StorageError: If the store is unavailable.
        """
        ...

    def status_marks(
        self, interface_id: UUID, collector_id: CollectorId, window: TimeWindow, *,
        limit: int = MAX_MARKS, newest_first: bool = False,
    ) -> Marks[StatusMark]:  # fmt: skip
        """Return one interface's status records from one collector (E.35).

        Args:
            interface_id: The interface.
            collector_id: The collector.
            window: Bounded window on the Stage 1 time.
            limit: At most this many marks, 1 to ``MAX_MARKS``.
            newest_first: Return the newest records first.

        Returns:
            Marks[StatusMark]: The marks.

        Raises:
            ValidationError: If ``limit`` is out of range.
            StorageError: If the store is unavailable.
        """
        ...


class AcceptanceBackend(TelemetryBackend, Protocol):
    """A telemetry backend that also serves the acceptance reports' lean reads."""

    @property
    def acceptance(self) -> AcceptanceQuery:
        """Lean reads for the acceptance reports.

        Args:
            None

        Returns:
            AcceptanceQuery: The port.

        Raises:
            None
        """
        ...
