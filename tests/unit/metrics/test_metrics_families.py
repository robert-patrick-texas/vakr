"""Every catalogued family is produced, and overload is never target-down (OBS-T01, OBS-T03).

OBS-T01: the collector's families are built from the scheduler, spool,
ingester, fault tally, and upkeep objects it really runs (fed one poll of
every step and one miss), written to a snapshot with the real writer, and
read back by the API's metrics service with its state, storage, lag, and
listener readers. Every family of the catalog must appear in the scrape.

OBS-T03: an overloaded collector shows as ``vakr_collector_state{state=
"overloaded"} 1`` with capacity misses; the device it could not poll is
judged Unknown, not Down, by the same status service the metrics count.

Dependencies:
    pytest; tests.contract.contract_samples, tests.integration.collector.collector_lab,
    tests.unit.query.query_testkit, tests.unit.scheduler.sched_fakes.

Example:
    $ uv run pytest tests/unit/metrics/test_metrics_families.py
"""

import dataclasses
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.contract.contract_samples import icmp
from tests.integration.collector.collector_lab import make_root
from tests.unit.metrics.metrics_testkit import parse
from tests.unit.query.query_testkit import Fleet
from tests.unit.scheduler.sched_fakes import IDS, make_target
from vakr.app.api.metrics_storage import replication_families, storage_families
from vakr.app.collector.metrics_families import (
    fault_family,
    latency_families,
    scheduler_families,
    spool_families,
)
from vakr.app.collector.metrics_loop import upkeep_families
from vakr.domain.enums import CollectorState, IcmpState
from vakr.domain.ids import PollId, new_id
from vakr.domain.quality import Quality
from vakr.ingest import SpoolHealth
from vakr.ingest.ingest_steps import IngestStatus
from vakr.metrics.catalog import CATALOG, build
from vakr.metrics.exposition import render
from vakr.metrics.service import MetricsService, merge, process_families
from vakr.metrics.snapshot import encode_snapshot, write_snapshot
from vakr.metrics.state import state_families
from vakr.query import OverviewQueries
from vakr.query.settings import QuerySettings
from vakr.replication import observation_status as obs_status
from vakr.replication import status as pull_status
from vakr.replication.status import record as pull_record
from vakr.scheduler.admission import Miss, MissReason
from vakr.scheduler.health import SchedulerSnapshot
from vakr.scheduler.lag import LoopLagMonitor
from vakr.scheduler.machine import Phase
from vakr.scheduler.plan import Slot
from vakr.scheduler.records import StartedPoll, finished_result, missed_result
from vakr.scheduler.runner import PollProgress, StepRecord
from vakr.scheduler.stats import SchedulerStats
from vakr.scheduler.steps import PollContext, StepStatus

WHEN = datetime(2026, 10, 21, 12, tzinfo=UTC)
S = 1_000_000


def _stats() -> SchedulerStats:
    """Record one poll that ran every step, and one overrun.

    Args:
        None

    Returns:
        SchedulerStats: The statistics.

    Raises:
        None
    """
    stats = SchedulerStats(late_after_us=5 * S)
    target = make_target(1)
    ctx = PollContext(new_id(PollId), IDS.collector_id, target, WHEN)
    started = StartedPoll(ctx, 100 * S, 101 * S, WHEN, IDS.worker_id)
    steps = [StepRecord(Phase.ICMP, StepStatus.OK, 101 * S, 102 * S),
             StepRecord(Phase.STAGE1, StepStatus.OK, 102 * S, 103 * S),
             StepRecord(Phase.FALLBACK, StepStatus.SKIPPED, 103 * S, 103 * S),
             StepRecord(Phase.STAGE2, StepStatus.PARTIAL, 103 * S, 104 * S)]  # fmt: skip
    probe = icmp(target.device.id, IDS.collector_id, WHEN, rtts_us=(900, None, 1100, 950, 1000))
    stats.record(finished_result(started, PollProgress(steps, [probe]), 104 * S))
    miss = Miss(Slot(target, 160 * S), MissReason.OVERRUN)
    stats.record(missed_result(miss, IDS.collector_id, WHEN))
    return stats


def collector_families(snap: SchedulerSnapshot, stats: SchedulerStats) -> list:
    """Build the collector's families the way its metrics loop does.

    Args:
        snap: The scheduler peek.
        stats: Its statistics.

    Returns:
        list: The families, with the process readings.

    Raises:
        None
    """
    spool = dataclasses.replace(SpoolHealth.empty(str(IDS.collector_id), 0, 1 << 30),
                                oldest_unacked_age_s=2.5, storage_available=True)  # fmt: skip
    ingest = IngestStatus(batches=3, inserted=40, duplicates=2)
    ingest.lag_window.observe(0.4)
    upkeep = SimpleNamespace(passes={"move": 4, "projection": 4, "retention": 1},
                             failures={"move": 0, "projection": 1, "retention": 0},
                             last_success={"move": WHEN},
                             last_duration_s={"move": 0.2})  # fmt: skip
    return [*process_families("collector"), *scheduler_families(snap, 60.0),
            *latency_families(stats), *spool_families(snap, spool, ingest),
            fault_family({"snmp_session": 2, "a_new_reason": 1}),
            build("vakr_collector_interfaces_sampled", [((), 12)]),
            *upkeep_families(upkeep)]  # ty: ignore[invalid-argument-type]  # fmt: skip


def test_obs_t01_every_catalogued_family_is_in_the_scrape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OBS-T01, OBS-001, OBS-004, OBS-007: the full catalog, through the real file and reader.

    Args:
        tmp_path: The relocated install.
        monkeypatch: ``VAKR_ROOT`` for the storage checks.

    Returns:
        None

    Raises:
        None
    """
    paths = make_root(tmp_path, 161)
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path / "root"))  # the checks read the environment
    stats = _stats()
    snap = SchedulerSnapshot(devices=1, workers=4, busy_workers=1, waiting=0, queue_depth=0,
                             queue_capacity=1000, loop_lag_us=1500, intervals_s=(60, 300),
                             lifetime=stats.lifetime)  # fmt: skip
    path = paths.run_dir / "collector-metrics.json"
    pull_record(paths.state_dir / pull_status.FILE, "applied", datetime.now(UTC), digest="0" * 64)
    stamp = obs_status.stamp(datetime.now(UTC))
    obs_status.write_status(
        paths.state_dir / obs_status.FILE,
        obs_status.ObservationPullStatus(
            sources={"x": obs_status.SourceStatus(cursor=5, newest_seq=9, newest_ts=stamp)}
        ),
    )
    now = WHEN.timestamp()
    write_snapshot(path, encode_snapshot(collector_families(snap, stats), now - 3))
    fleet = Fleet()
    fleet.poll(fleet.device("sw1"))
    overview = OverviewQueries(fleet.uow, fleet.telemetry, QuerySettings(timezone="UTC"),
                               fleet.clock)  # fmt: skip
    service = MetricsService(
        snapshot_path=path,
        readers=(
            lambda: state_families(fleet.uow, overview),
            lambda: storage_families(paths),
            lambda: replication_families(paths),
        ),
    )
    service.lag = LoopLagMonitor(fleet.clock)
    service.lag.observe(2000)
    service.listeners = lambda: [("tcp", 3, 100), ("socket", 1, 32)]
    service.requests.observe("/api/v1/devices", 200, 0.02)
    families = parse(service.render(service.live(), now_s=now))
    assert set(CATALOG) - set(families) == set()
    assert families["vakr_collector_metrics_up"].values() == {(("reason", "ok"),): 1.0}
    steps = families["vakr_collector_steps_total"].values()
    assert steps[(("step", "snmp_fallback_stage1"), ("status", "skipped"))] == 1
    assert families["vakr_collector_icmp_probes_sent_total"].values() == {(): 5.0}
    assert families["vakr_collector_icmp_probes_answered_total"].values() == {(): 4.0}
    assert families["vakr_collector_faults_total"].values() == {
        (("reason", "other"),): 1.0, (("reason", "snmp_session"),): 2.0}  # fmt: skip
    processes = {dict(labels)["process"] for _, labels, _ in
                 families["vakr_process_cpu_seconds_total"].samples}  # fmt: skip
    assert processes == {"api", "collector"}
    durations = families["vakr_collector_step_duration_seconds"].values()
    assert (("step", "snmp_fallback_stage1"), ("quantile", "0.5")) not in durations  # skipped


def test_obs_t03_overload_is_capacity_degradation_never_target_down() -> None:
    """OBS-003, OBS-T03: overload shows on the collector; the unpolled device is not Down.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    stats = _stats()
    stats.lifetime.capacity, stats.lifetime.not_attempted = 7, 7
    snap = SchedulerSnapshot(devices=1, workers=1, busy_workers=1, waiting=9,
                             producers_blocked=2, period=dataclasses.replace(stats.lifetime),
                             lifetime=stats.lifetime)  # fmt: skip
    families = parse(render(merge(collector_families(snap, stats))))
    state = families["vakr_collector_state"].values()
    assert state[(("state", "overloaded"),)] == 1
    assert state[(("state", "healthy"),)] == 0
    assert families["vakr_collector_polls_capacity_misses_total"].values() == {(): 7.0}
    assert not any("down" in name for name in families)
    labels = set()
    for fleet_state in (CollectorState.HEALTHY, CollectorState.OVERLOADED):
        fleet = Fleet()
        fleet.health(state=fleet_state)
        device = fleet.device("core")
        if fleet_state is CollectorState.OVERLOADED:  # the polls it ran carry the collector's flag
            fleet.poll(device, icmp=IcmpState.UNREACHABLE, snmp=None,
                       poll_quality=Quality.COLLECTOR_DEGRADED,
                       icmp_quality=Quality.COLLECTOR_DEGRADED)  # fmt: skip
        else:
            fleet.poll(device, icmp=IcmpState.UNREACHABLE, snmp=None)
        labels.add((fleet_state, fleet.queries().device_list().rows[0].status.label))
    by_state = dict(labels)
    assert by_state[CollectorState.HEALTHY] == "Down"
    assert by_state[CollectorState.OVERLOADED] != "Down"
