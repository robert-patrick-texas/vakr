"""The families the collector writes into its snapshot file (OBS-001, OBS-004, OBS-007, E.33).

The process families of ``catalog_api`` appear in the snapshot too, with
``process="collector"``; the families here exist only there. Counters run
from the collector's start (``vakr_process_start_time_seconds``), so a
restart is visible as a reset.

Dependencies:
    vakr.metrics.catalog_spec: ``spec`` and the label value sets.

Safety:
    Declarations only; no label names a device, interface, or address.

Example:
    >>> any(s.name == "vakr_collector_state" for s in COLLECTOR_FAMILIES)
    True
"""

from vakr.metrics.catalog_spec import (
    COLLECTOR_STATES,
    CUT_SHORT,
    EXECUTIONS,
    FAULTS,
    INGEST_RESULTS,
    LATENCIES,
    MISS_REASONS,
    QUEUES,
    SPOOL_STATES,
    STEP_STATUSES,
    STEPS,
    UPKEEP,
    spec,
)
from vakr.metrics.exposition import Kind

C, G, S = Kind.COUNTER, Kind.GAUGE, Kind.SUMMARY
_STEP = ("step", STEPS)
_KIND = ("kind", UPKEEP)

COLLECTOR_FAMILIES = (
    spec("vakr_collector_state", G,
         "1 for the collector's current state, 0 for the others; overload is never target-down.",
         ("state", COLLECTOR_STATES)),
    spec("vakr_collector_devices_scheduled", G, "Devices the scheduler plans to poll."),
    spec("vakr_collector_interfaces_sampled", G, "Interfaces the collector samples."),
    spec("vakr_collector_workers", G, "Poll workers."),
    spec("vakr_collector_workers_busy", G, "Workers holding a poll or its unsent result."),
    spec("vakr_collector_slots_waiting", G, "Due poll slots waiting for a worker."),
    spec("vakr_collector_polls_scheduled_total", C, "Poll occurrences that came due."),
    spec("vakr_collector_polls_started_total", C, "Polls that started."),
    spec("vakr_collector_polls_completed_total", C, "Polls that finished, by how they ended.",
         ("execution", EXECUTIONS)),
    spec("vakr_collector_polls_late_total", C,
         "Polls that started later than scheduler.late_after after their slot."),
    spec("vakr_collector_polls_missed_total", C, "Occurrences that never ran, by reason.",
         ("reason", MISS_REASONS)),
    spec("vakr_collector_polls_capacity_misses_total", C,
         "Occurrences not attempted because the collector lacked capacity (OBS-003)."),
    spec("vakr_collector_polls_cut_short_total", C,
         "Polls ended early by shutdown or the poll deadline.", ("cause", CUT_SHORT)),
    spec("vakr_collector_step_errors_total", C, "Collector-side step failures."),
    spec("vakr_collector_faults_total", C,
         "Collector-side trouble by reason: evidence about vakr, never about a target.",
         ("reason", FAULTS)),
    spec("vakr_collector_poll_latency_seconds", S,
         "Poll lateness (start minus slot), duration, and completion (end minus slot).",
         ("latency", LATENCIES)),
    spec("vakr_collector_steps_total", C, "Poll steps by step and how they ended.",
         _STEP, ("status", STEP_STATUSES)),
    spec("vakr_collector_step_duration_seconds", S,
         "Duration of the steps that ran, by step (ICMP, SNMP Stage 1, fallback, Stage 2).",
         _STEP),
    spec("vakr_collector_icmp_probes_sent_total", C, "ICMP echo probes sent."),
    spec("vakr_collector_icmp_probes_answered_total", C, "ICMP echo probes answered."),
    spec("vakr_collector_queue_depth", G, "Items waiting in a collector queue.",
         ("queue", QUEUES)),
    spec("vakr_collector_queue_capacity", G, "Bound of a collector queue.", ("queue", QUEUES)),
    spec("vakr_collector_queue_full_waits_total", C,
         "Times a poll producer found the output queue full."),
    spec("vakr_collector_producers_blocked", G, "Producers waiting on a full queue now."),
    spec("vakr_collector_spool_bytes", G, "Bytes held in the durable spool."),
    spec("vakr_collector_spool_limit_bytes", G, "The spool's size limit."),
    spec("vakr_collector_spool_state", G, "1 for the spool's fill state, 0 for the others (6.4).",
         ("state", SPOOL_STATES)),
    spec("vakr_collector_spool_backlog_records", G, "Durable records not yet in telemetry."),
    spec("vakr_collector_spool_oldest_age_seconds", G,
         "Age of the oldest record not yet in telemetry: the replay lag."),
    spec("vakr_collector_spool_incidents_total", C, "Telemetry-loss incidents recorded."),
    spec("vakr_collector_ingest_batches_total", C, "Write batches stored in telemetry."),
    spec("vakr_collector_ingest_observations_total", C,
         "Observations ingested: stored, already there (replays), or set aside.",
         ("result", INGEST_RESULTS)),
    spec("vakr_collector_ingest_latency_seconds", S,
         "Append-to-stored time of each acknowledged batch's newest record."),
    spec("vakr_collector_ingest_storage_available", G,
         "1 when the last telemetry write succeeded, 0 when it failed."),
    spec("vakr_collector_ingest_consecutive_failures", G, "Failed writes since the last success."),
    spec("vakr_collector_clock_steps_total", C, "Wall-clock steps the scheduler detected."),
    spec("vakr_collector_wall_offset_seconds", G, "Wall clock minus the plan timeline."),
    spec("vakr_collector_upkeep_passes_total", C, "Telemetry upkeep passes completed.", _KIND),
    spec("vakr_collector_upkeep_failures_total", C, "Telemetry upkeep passes that failed.",
         _KIND),
    spec("vakr_collector_upkeep_last_success_timestamp_seconds", G,
         "When an upkeep pass last succeeded, Unix seconds.", _KIND),
    spec("vakr_collector_upkeep_last_duration_seconds", G,
         "How long the last successful upkeep pass took.", _KIND),
    spec("vakr_collector_interval_seconds", G,
         "Configured default poll interval, and the shortest and longest effective ones.",
         ("interval", frozenset({"configured", "effective_min", "effective_max"}))),
)  # fmt: skip
