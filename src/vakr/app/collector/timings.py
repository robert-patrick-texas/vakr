"""Collector service timings and bounds (Appendix E.27).

These are the service's own cadences, not per-device polling settings:
how often rate baselines are checkpointed (6.2, RATE-004), how often a
collector-health observation is written (OBS-001), how often the inventory
change counter is read (6.2), the telemetry maintenance cadence (E.21), how
discovery is spread and bounded (DISC-001, DISC-002), and the shutdown
budget (16.4). They are constants, not operator options: none of them is a
site decision, and each is documented with its reason in E.27.

``ServiceTimings`` exists so tests can run the real service loop in seconds.
``poll_interval_override_s`` is the test-only escape from the E.18 30 s
minimum: only code can set it, never configuration, so a production node
always polls within E.18's limits.

Dependencies:
    dataclasses: The frozen settings value.

Safety:
    Every bound is finite; ``__post_init__`` refuses non-positive cadences.

Example:
    >>> ServiceTimings().checkpoint_interval_s, ServiceTimings().discovery_concurrency
    (60.0, 2)
"""

from dataclasses import dataclass

MAX_BASELINES = 200_000
"""Most interfaces with a remembered rate baseline: twice the enterprise design point (4.1)."""

MAX_UPTIME_READINGS = 20_000
"""Most devices with a remembered ``sysUpTime`` reading: five times the enterprise device count."""

MAX_CHECKPOINT_BYTES = 64 << 20
"""Largest baseline checkpoint read at startup; MAX_BASELINES entries need about 40 MiB."""

SPOOL_BATCH = 2_000
"""Most observations per spool append; never more than ``spool.queue_capacity``."""

EX_PREFLIGHT = 78
"""Exit status of a failed startup preflight (``EX_CONFIG``); the unit does not restart on it."""


@dataclass(frozen=True, slots=True)
class ServiceTimings:
    """The collector service's own cadences (Appendix E.27).

    Attributes:
        checkpoint_interval_s: Rate-baseline checkpoint cadence (6.2: every minute).
        health_interval_s: Collector-health observation cadence (OBS-001).
        change_poll_s: How often the inventory change counter is read (6.2).
        maintenance_interval_s: Telemetry staging move and WAL checkpoint (E.21: 15 min).
        retention_interval_s: Rollup hook and retention pass (E.21: hourly).
        discovery_concurrency: Discoveries running at once, beside polling.
        discovery_spread_s: Window over which initial discoveries are spread.
        discovery_retry_min_s: First wait before retrying a failed discovery;
            it doubles after each consecutive failure.
        discovery_retry_max_s: Longest wait between retries of a failed discovery.
        stop_budget_s: Shutdown work after ``scheduler.shutdown_grace`` (16.4).
        poll_interval_override_s: Test only: every device's effective
            interval, bypassing E.18's 30 s minimum. ``None`` in production.
    """

    checkpoint_interval_s: float = 60.0
    health_interval_s: float = 60.0
    change_poll_s: float = 5.0
    maintenance_interval_s: float = 900.0
    retention_interval_s: float = 3600.0
    discovery_concurrency: int = 2
    discovery_spread_s: float = 60.0
    discovery_retry_min_s: float = 60.0
    discovery_retry_max_s: float = 3600.0
    stop_budget_s: float = 45.0
    poll_interval_override_s: int | None = None

    def __post_init__(self) -> None:
        """Refuse cadences that would spin or never run.

        Args:
            None

        Returns:
            None

        Raises:
            ValueError: If a cadence or the concurrency is not positive, or the
                retry bounds are reversed.
        """
        cadences = (
            self.checkpoint_interval_s,
            self.health_interval_s,
            self.change_poll_s,
            self.maintenance_interval_s,
            self.retention_interval_s,
            self.discovery_retry_min_s,
            self.stop_budget_s,
        )
        if min(cadences) <= 0 or self.discovery_spread_s < 0:
            raise ValueError("collector service cadences must be positive")
        if self.discovery_retry_max_s < self.discovery_retry_min_s:
            raise ValueError("discovery_retry_max_s must not be below discovery_retry_min_s")
        if self.discovery_concurrency < 1:
            raise ValueError("discovery_concurrency must be at least 1")
        override = self.poll_interval_override_s
        if override is not None and override < 1:
            raise ValueError("poll_interval_override_s must be at least 1 second")
