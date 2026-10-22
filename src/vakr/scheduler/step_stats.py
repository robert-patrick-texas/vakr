"""Per-step outcomes, durations, and ICMP probe counts for the self-metrics (OBS-001, OBS-004).

Every emitted poll record lists its steps (ICMP, SNMP Stage 1, the
low-cost fallback, Stage 2), each with how it ended and when it started
and ended on the plan timeline. Counting them here, beside the scheduler's
own counters, gives the collector's metrics per protocol without touching
the steps themselves: outcomes by step and status, the duration of each
step that ran (a skipped step took no time and adds no sample), and the
ICMP probes sent and answered, read from the poll's ``IcmpObservation``.

Dependencies:
    collections.Counter: Outcome counts.
    vakr.logic.window: Rolling duration summaries.
    vakr.domain.observations: ``IcmpObservation``.

Safety:
    Counts and durations only; no address or device is kept.

Example:
    >>> stats = StepStats()
    >>> stats.icmp_sent, stats.durations["icmp"].quantiles() is None
    (0, True)
"""

from collections import Counter

from vakr.domain.observations import IcmpObservation
from vakr.logic.window import Summary
from vakr.scheduler.machine import Phase
from vakr.scheduler.records import PollResult
from vakr.scheduler.steps import StepStatus

STEP_PHASES = (Phase.ICMP, Phase.STAGE1, Phase.FALLBACK, Phase.STAGE2)
_US = 1e6
_WINDOW = 2048
"""Duration samples kept per step."""


class StepStats:
    """Lifetime step counts and recent step durations.

    Attributes:
        outcomes: Steps by (phase value, status value).
        durations: Duration summary per phase value, in seconds.
        icmp_sent: ICMP echo probes sent since start.
        icmp_answered: ICMP echo probes answered since start.
    """

    def __init__(self) -> None:
        """Start with nothing counted.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.outcomes: Counter[tuple[str, str]] = Counter()
        self.durations = {p.value: Summary(_WINDOW) for p in STEP_PHASES}
        self.icmp_sent = 0
        self.icmp_answered = 0

    def record(self, result: PollResult) -> None:
        """Count one emitted poll record.

        Args:
            result: The record.

        Returns:
            None

        Raises:
            None
        """
        for step in result.steps:
            if step.phase not in STEP_PHASES:
                continue
            self.outcomes[step.phase.value, step.status.value] += 1
            if step.status is not StepStatus.SKIPPED:
                self.durations[step.phase.value].observe((step.ended_us - step.started_us) / _US)
        for obs in result.observations:
            if isinstance(obs, IcmpObservation):
                self.icmp_sent += len(obs.rtts_us)
                self.icmp_answered += sum(r is not None for r in obs.rtts_us)
