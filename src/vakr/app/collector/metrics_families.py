"""The collector's self-metric families, from its in-memory state (OBS-001, OBS-003, OBS-004).

Everything here is already counted by the scheduler, the spool, the
ingester, the fault tally, and the upkeep timer; this module only reads it
into the catalogued families of ``vakr.metrics.catalog_collector``. Counters
are lifetime totals since the collector started; a value not measured yet
is left out, never written as 0 (invariant 1).

Overload is the collector's own state (``vakr_collector_state``, the
capacity-miss and late counters), never a device count: the metrics have no
per-device series at all (OBS-003, E.33).

Dependencies:
    vakr.metrics: Catalog builders and summaries.
    vakr.scheduler, vakr.ingest: The snapshots read.

Safety:
    Counts, sizes, and timings with fixed labels only.

Example:
    >>> from vakr.scheduler.health import SchedulerSnapshot
    >>> fams = scheduler_families(SchedulerSnapshot(devices=2, workers=4, busy_workers=0,
    ...                                             waiting=0), None)
    >>> fams[0].name, fams[0].samples[0].value
    ('vakr_collector_devices_scheduled', 2.0)
"""

from vakr.app.collector.health import worst
from vakr.ingest import SpoolHealth, collector_state
from vakr.ingest.ingest_steps import IngestStatus
from vakr.logic.window import Quantiles
from vakr.metrics.catalog import build, summary
from vakr.metrics.catalog_spec import COLLECTOR_STATES, FAULTS, SPOOL_STATES
from vakr.metrics.exposition import Family
from vakr.scheduler.health import SchedulerSnapshot
from vakr.scheduler.stats import SchedulerStats, Window
from vakr.scheduler.step_stats import StepStats

_US = 1e6


def _quantiles(window: Window) -> Quantiles | None:
    """Convert a scheduler window (microseconds) to seconds.

    Args:
        window: The window.

    Returns:
        Quantiles | None: ``None`` before the first sample.

    Raises:
        None
    """
    p = window.percentiles()
    if p is None:
        return None
    return Quantiles(p.p50 / _US, p.p95 / _US, p.p99 / _US, window.count, window.total_us / _US)


def scheduler_families(snap: SchedulerSnapshot, configured_s: float | None) -> list[Family]:
    """Read the scheduler snapshot: workload, poll counters, queue, clock, intervals.

    Args:
        snap: A peek at the scheduler (the period is not closed).
        configured_s: The default poll interval, ``polling.default_interval``.

    Returns:
        list[Family]: The families.

    Raises:
        None
    """
    life = snap.lifetime
    ok = life.completed - life.partial - life.failed
    low, high = snap.intervals_s or (None, None)
    return [
        build("vakr_collector_devices_scheduled", [((), snap.devices)]),
        build("vakr_collector_workers", [((), snap.workers)]),
        build("vakr_collector_workers_busy", [((), snap.busy_workers)]),
        build("vakr_collector_slots_waiting", [((), snap.waiting)]),
        build("vakr_collector_polls_scheduled_total", [((), life.scheduled)]),
        build("vakr_collector_polls_started_total", [((), life.started)]),
        build("vakr_collector_polls_completed_total",
              [(("ok",), ok), (("partial",), life.partial), (("failed",), life.failed)]),
        build("vakr_collector_polls_late_total", [((), life.late)]),
        build("vakr_collector_polls_missed_total",
              [(("overrun",), life.overrun), (("not_attempted",), life.not_attempted)]),
        build("vakr_collector_polls_capacity_misses_total", [((), life.capacity)]),
        build("vakr_collector_polls_cut_short_total",
              [(("cancelled",), life.cancelled), (("deadline",), life.deadline)]),
        build("vakr_collector_step_errors_total", [((), life.step_errors)]),
        build("vakr_collector_queue_full_waits_total", [((), snap.queue_full_waits)]),
        build("vakr_collector_producers_blocked", [((), snap.producers_blocked)]),
        build("vakr_collector_clock_steps_total", [((), snap.clock_steps)]),
        build("vakr_collector_wall_offset_seconds", [((), snap.wall_offset_us / _US)]),
        build("vakr_event_loop_lag_seconds", [(("collector",), None if snap.loop_lag_us is None
                                               else snap.loop_lag_us / _US)]),
        build("vakr_collector_interval_seconds",
              [(("configured",), configured_s), (("effective_min",), low),
               (("effective_max",), high)]),
    ]  # fmt: skip


def latency_families(stats: SchedulerStats) -> list[Family]:
    """Read poll latencies and the per-step figures.

    Args:
        stats: The scheduler's statistics.

    Returns:
        list[Family]: Poll latency and step summaries, step and ICMP counters.

    Raises:
        None
    """
    steps: StepStats = stats.steps
    latencies = (
        (("lateness",), _quantiles(stats.lateness)),
        (("duration",), _quantiles(stats.duration)),
        (("completion",), _quantiles(stats.completion)),
    )
    return [
        summary("vakr_collector_poll_latency_seconds", latencies),
        build("vakr_collector_steps_total", sorted(steps.outcomes.items())),
        summary("vakr_collector_step_duration_seconds",
                (((p,), s.quantiles()) for p, s in steps.durations.items())),
        build("vakr_collector_icmp_probes_sent_total", [((), steps.icmp_sent)]),
        build("vakr_collector_icmp_probes_answered_total", [((), steps.icmp_answered)]),
    ]  # fmt: skip


def spool_families(
    snap: SchedulerSnapshot, spool: SpoolHealth, ingest: IngestStatus
) -> list[Family]:
    """Read the spool and the ingester, and the collector's state.

    Args:
        snap: The scheduler peek, for its share of the state and its queue.
        spool: Spool and ingestion health.
        ingest: The ingester's counters.

    Returns:
        list[Family]: State, queues, spool, and ingestion families.

    Raises:
        None
    """
    state = worst((snap.collector_state(), collector_state(spool))).value
    available = None if spool.storage_available is None else int(spool.storage_available)
    return [
        build("vakr_collector_state", [((s,), int(s == state)) for s in sorted(COLLECTOR_STATES)]),
        build("vakr_collector_queue_depth",
              [(("poll_output",), snap.queue_depth), (("spool_write",), spool.queue_depth)]),
        build("vakr_collector_queue_capacity",
              [(("poll_output",), snap.queue_capacity), (("spool_write",), spool.queue_capacity)]),
        build("vakr_collector_spool_bytes", [((), spool.spool_bytes)]),
        build("vakr_collector_spool_limit_bytes", [((), spool.max_bytes)]),
        build("vakr_collector_spool_state",
              [((s,), int(s == spool.state.value)) for s in sorted(SPOOL_STATES)]),
        build("vakr_collector_spool_backlog_records", [((), spool.backlog_records)]),
        build("vakr_collector_spool_oldest_age_seconds", [((), spool.oldest_unacked_age_s)]),
        build("vakr_collector_spool_incidents_total", [((), spool.incidents)]),
        build("vakr_collector_ingest_batches_total", [((), ingest.batches)]),
        build("vakr_collector_ingest_observations_total",
              [(("inserted",), ingest.inserted), (("duplicate",), ingest.duplicates),
               (("set_aside",), ingest.set_aside)]),
        summary("vakr_collector_ingest_latency_seconds", [((), ingest.lag_window.quantiles())]),
        build("vakr_collector_ingest_storage_available", [((), available)]),
        build("vakr_collector_ingest_consecutive_failures", [((), spool.consecutive_failures)]),
    ]  # fmt: skip


def fault_family(lifetime: dict[str, int]) -> Family:
    """Read the collector-side fault counts, folding unknown reasons into ``other``.

    Args:
        lifetime: Faults by reason code since start.

    Returns:
        Family: ``vakr_collector_faults_total``.

    Raises:
        None
    """
    folded: dict[str, int] = {}
    for reason, n in lifetime.items():
        key = reason if reason in FAULTS else "other"
        folded[key] = folded.get(key, 0) + n
    return build("vakr_collector_faults_total", (((k,), v) for k, v in sorted(folded.items())))
