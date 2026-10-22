"""Scheduler counters and latency percentiles (9.9, OBS-001, OBS-004).

Every record the scheduler emits is counted twice: in lifetime totals and in
the current *period*, which a health observation takes and resets
(``CollectorHealthObservation`` counts cover the time since the previous
one). Lateness (start minus slot), duration (completion minus start), and
completion (completion minus slot) keep the most recent ``WINDOW`` samples
for nearest-rank p50/p95/p99, the same percentile rule as the rollups
(E.17 #13). A missed poll adds no latency sample: it has no start, and its
absence is never a zero.

Dependencies:
    collections.deque: Bounded sample windows.
    vakr.logic.stats: Nearest-rank percentiles.

Safety:
    Memory is fixed: three windows of ``WINDOW`` integers.

Example:
    >>> w = Window(8)
    >>> for v in (1, 2, 3, 4): w.add(v)
    >>> w.percentiles()
    Percentiles(p50=2, p95=4, p99=4, samples=4)
"""

from collections import deque
from dataclasses import dataclass, replace

from vakr.domain.enums import PollExecution
from vakr.logic.stats import nearest_rank
from vakr.scheduler.admission import CAPACITY_REASONS, MissReason
from vakr.scheduler.records import PollResult
from vakr.scheduler.step_stats import StepStats
from vakr.scheduler.steps import StepStatus

WINDOW = 4096  # E.25: about 40 minutes of polls at the MVP workload


@dataclass(slots=True)
class Counters:
    """Occurrence counts (9.9: scheduled, started, completed, late, missed, cancelled).

    Attributes:
        scheduled: Occurrences that came due and were recorded.
        started: Polls that started.
        completed: Polls that finished, however they ended.
        partial: Finished polls recorded ``PARTIAL``.
        failed: Finished polls recorded ``FAILED``.
        late: Started polls whose lateness exceeded ``late_after``.
        overrun: ``MISSED_OVERRUN`` records.
        not_attempted: ``NOT_ATTEMPTED`` records.
        capacity: Not-attempted records caused by collector capacity.
        cancelled: Polls cut short by shutdown.
        deadline: Polls cut short by ``poll_deadline``.
        step_errors: Collector-side step errors.
    """

    scheduled: int = 0
    started: int = 0
    completed: int = 0
    partial: int = 0
    failed: int = 0
    late: int = 0
    overrun: int = 0
    not_attempted: int = 0
    capacity: int = 0
    cancelled: int = 0
    deadline: int = 0
    step_errors: int = 0

    @property
    def missed(self) -> int:
        """Return occurrences that never started.

        Args:
            None

        Returns:
            int: ``overrun + not_attempted``.

        Raises:
            None
        """
        return self.overrun + self.not_attempted


@dataclass(frozen=True, slots=True)
class Percentiles:
    """Nearest-rank percentiles of a sample window, in microseconds.

    Attributes:
        p50: Median.
        p95: 95th percentile.
        p99: 99th percentile.
        samples: Number of samples.
    """

    p50: int
    p95: int
    p99: int
    samples: int


class Window:
    """The most recent samples of one latency, and the lifetime count and sum (E.33).

    Attributes:
        count: Samples since start.
        total_us: Sum of every sample since start, in microseconds.
    """

    def __init__(self, size: int = WINDOW) -> None:
        """Create an empty window.

        Args:
            size: Samples kept.

        Returns:
            None

        Raises:
            None
        """
        self._samples: deque[int] = deque(maxlen=size)
        self.count = 0
        self.total_us = 0

    def add(self, value: int) -> None:
        """Add a sample, dropping the oldest when full.

        Args:
            value: Microseconds.

        Returns:
            None

        Raises:
            None
        """
        self._samples.append(value)
        self.count += 1
        self.total_us += value

    def percentiles(self) -> Percentiles | None:
        """Return p50/p95/p99 of the window.

        Args:
            None

        Returns:
            Percentiles | None: ``None`` when there are no samples; an empty
            window is unknown, not zero.

        Raises:
            None
        """
        if not self._samples:
            return None
        values = sorted(self._samples)
        p50, p95, p99 = (nearest_rank(values, p) for p in (50, 95, 99))
        return Percentiles(p50, p95, p99, len(values))


def _count(counters: Counters, result: PollResult, late_after_us: int) -> None:
    """Add one record to a set of counters.

    Args:
        counters: Counters to update.
        result: The record.
        late_after_us: Lateness above which a start counts as late.

    Returns:
        None

    Raises:
        None
    """
    counters.scheduled += 1
    if result.miss is not None:
        counters.overrun += result.miss is MissReason.OVERRUN
        counters.not_attempted += result.miss is not MissReason.OVERRUN
        counters.capacity += result.miss in CAPACITY_REASONS
        return
    statuses = [step.status for step in result.steps]
    counters.started += 1
    counters.completed += 1
    counters.partial += result.poll.execution is PollExecution.PARTIAL
    counters.failed += result.poll.execution is PollExecution.FAILED
    counters.late += (result.lateness_us or 0) > late_after_us
    counters.cancelled += StepStatus.CANCELLED in statuses
    counters.deadline += StepStatus.DEADLINE in statuses
    counters.step_errors += statuses.count(StepStatus.ERROR)


class SchedulerStats:
    """Lifetime and per-period counters plus latency windows.

    Attributes:
        lifetime: Totals since start.
        period: Totals since the last ``take_period``.
        lateness: Start minus slot.
        duration: Completion minus start.
        completion: Completion minus slot (TEST-004).
        steps: Step outcomes, step durations, and ICMP probes (E.33).
    """

    def __init__(self, late_after_us: int, window: int = WINDOW) -> None:
        """Create empty statistics.

        Args:
            late_after_us: ``scheduler.late_after`` in microseconds.
            window: Samples kept per latency.

        Returns:
            None

        Raises:
            None
        """
        self._late_after_us = late_after_us
        self.lifetime = Counters()
        self.period = Counters()
        self.lateness = Window(window)
        self.duration = Window(window)
        self.completion = Window(window)
        self.steps = StepStats()

    def record(self, result: PollResult) -> None:
        """Count one emitted record.

        Args:
            result: The record.

        Returns:
            None

        Raises:
            None
        """
        _count(self.lifetime, result, self._late_after_us)
        _count(self.period, result, self._late_after_us)
        self.steps.record(result)
        if result.lateness_us is not None and result.duration_us is not None:
            self.lateness.add(result.lateness_us)
            self.duration.add(result.duration_us)
            self.completion.add(result.lateness_us + result.duration_us)

    def take_period(self) -> Counters:
        """Return the period's counters and start a new period.

        Args:
            None

        Returns:
            Counters: A copy of the finished period.

        Raises:
            None
        """
        finished = replace(self.period)
        self.period = Counters()
        return finished
