"""Scheduler settings from the effective configuration (CFGM-001, Appendix E.25).

The option catalog declares and range-checks every ``[scheduler]`` and
``[polling]`` key. This module turns the effective values into one frozen
``SchedulerSettings`` and adds the cross-key rule a single option cannot
express: ``late_after`` must not exceed ``max_start_delay``, or no start
could ever count as late (11.4, "impossible threshold orderings").

Per-device intervals and strategies are not here: a device override wins,
then the ``[polling]`` defaults read below (``vakr.scheduler.targets``).

Dependencies:
    vakr.config.loader, project: ``EffectiveConfig`` and the catalog defaults.
    vakr.scheduler.hashing, phase, targets: Names, modes, defaults.

Safety:
    Settings hold names, counts, and durations; nothing secret.

Example:
    >>> from vakr.config.loader import EffectiveConfig
    >>> scheduler_problems(EffectiveConfig({"scheduler": {"late_after": "45s"}}))
    ['scheduler.late_after (45s) must not exceed scheduler.max_start_delay (30s)']
"""

import socket
from dataclasses import dataclass, field
from typing import Any

from vakr.config.loader import EffectiveConfig
from vakr.config.project import catalog
from vakr.domain.enums import ReachabilityStrategy
from vakr.lifecycle.configmerge import parse_duration
from vakr.policy.settings import format_seconds
from vakr.scheduler.hashing import resolve_collector_name
from vakr.scheduler.phase import US_PER_S, OrderMode, PhaseMode, PhaseRule
from vakr.scheduler.targets import PollDefaults


def _get(config: EffectiveConfig, key: str) -> Any:
    """Read one option, falling back to its packaged default (CFGM-001).

    Args:
        config: Effective configuration.
        key: Dotted option key.

    Returns:
        Any: The configured value, or the catalog default when absent.

    Raises:
        KeyError: If the catalog does not declare ``key``.
    """
    option = catalog().option(key)
    if option is None:
        raise KeyError(key)
    return config.get(key, option.default)


def _seconds(config: EffectiveConfig, key: str) -> float:
    """Read a duration option in seconds.

    Args:
        config: Effective configuration.
        key: Dotted option key.

    Returns:
        float: Seconds.

    Raises:
        ValueError: If the value is not a duration.
    """
    return parse_duration(_get(config, key))


@dataclass(frozen=True, slots=True)
class SchedulerSettings:
    """Everything the scheduler driver needs from configuration.

    Attributes:
        rule: Collector name and phase/order controls (SCHED-001..006).
        defaults: ``[polling]`` defaults for devices without overrides.
        workers: Worker pool size (SCHED-016).
        queue_capacity: Output queue bound (6.4).
        late_after_s: A start later than this counts as late (4.2).
        max_start_delay_s: A slot not started this long after it came due is
            recorded as not attempted (SCHED-017).
        poll_deadline_s: A poll running longer is cut short.
        shutdown_grace_s: Time active polls get to finish at shutdown (16.4).
    """

    rule: PhaseRule
    defaults: PollDefaults = field(default_factory=PollDefaults)
    workers: int = 32
    queue_capacity: int = 1000
    late_after_s: float = 5.0
    max_start_delay_s: float = 30.0
    poll_deadline_s: float = 90.0
    shutdown_grace_s: float = 15.0

    def __post_init__(self) -> None:
        """Validate the cross-field rules.

        Args:
            None

        Returns:
            None

        Raises:
            ValueError: If a bound is not positive or ``late_after`` exceeds
                ``max_start_delay``.
        """
        if self.workers < 1 or self.queue_capacity < 1:
            raise ValueError("scheduler.workers and scheduler.queue_capacity must be at least 1")
        if min(self.late_after_s, self.max_start_delay_s, self.poll_deadline_s) <= 0:
            raise ValueError("scheduler lateness and deadline settings must be positive")
        if self.shutdown_grace_s < 0:
            raise ValueError("scheduler.shutdown_grace must not be negative")
        if self.late_after_s > self.max_start_delay_s:
            raise ValueError("scheduler.late_after must not exceed scheduler.max_start_delay")

    @property
    def late_after_us(self) -> int:
        """Return ``late_after`` in microseconds.

        Args:
            None

        Returns:
            int: Microseconds.

        Raises:
            None
        """
        return round(self.late_after_s * US_PER_S)

    @property
    def max_start_delay_us(self) -> int:
        """Return ``max_start_delay`` in microseconds.

        Args:
            None

        Returns:
            int: Microseconds.

        Raises:
            None
        """
        return round(self.max_start_delay_s * US_PER_S)


def scheduler_problems(config: EffectiveConfig) -> list[str]:
    """Return cross-key problems for ``vakr config validate``.

    Args:
        config: Effective configuration; single-key ranges are already checked.

    Returns:
        list[str]: One line per problem; empty when consistent.

    Raises:
        None
    """
    late, delay = (
        _seconds(config, "scheduler.late_after"),
        _seconds(config, "scheduler.max_start_delay"),
    )
    if late <= delay:
        return []
    return [
        f"scheduler.late_after ({format_seconds(late)}) must not exceed "
        f"scheduler.max_start_delay ({format_seconds(delay)})"
    ]


def settings_from_config(config: EffectiveConfig, hostname: str | None = None) -> SchedulerSettings:
    """Build scheduler settings from the effective configuration.

    Args:
        config: Effective, already validated configuration.
        hostname: Host name for the default collector name; ``None`` reads
            ``socket.gethostname()`` (SCHED-001).

    Returns:
        SchedulerSettings: The settings.

    Raises:
        ValueError: If a value is inconsistent or the collector name is invalid.
    """
    name = resolve_collector_name(
        str(_get(config, "scheduler.collector_name")), hostname or socket.gethostname()
    )
    rule = PhaseRule(
        name,
        PhaseMode(_get(config, "scheduler.phase")),
        OrderMode(_get(config, "scheduler.device_order")),
        round(_seconds(config, "scheduler.fixed_phase") * US_PER_S),
    )
    defaults = PollDefaults(
        int(_seconds(config, "polling.default_interval")),
        ReachabilityStrategy(_get(config, "polling.reachability_strategy")),
    )
    return SchedulerSettings(
        rule, defaults,
        workers=int(_get(config, "scheduler.workers")),
        queue_capacity=int(_get(config, "scheduler.queue_capacity")),
        late_after_s=_seconds(config, "scheduler.late_after"),
        max_start_delay_s=_seconds(config, "scheduler.max_start_delay"),
        poll_deadline_s=_seconds(config, "scheduler.poll_deadline"),
        shutdown_grace_s=_seconds(config, "scheduler.shutdown_grace"),
    )  # fmt: skip
