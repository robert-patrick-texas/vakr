"""The rollup job on a real SQLite telemetry file (ROLL-T03, ROLL-T04, ROLL-T05, RET-001..009).

Each test opens a fresh, migrated ``telemetry.db`` in ``tmp_path``, writes a
small simulated fleet with ``rollup_lab.Fleet``, and drives
``RollupJob`` with an explicit ``now``, as ``TelemetryUpkeep`` does hourly.

Dependencies:
    pytest; tests.integration.rollups.rollup_lab; the SQLite telemetry testkit.

Example:
    $ uv run pytest tests/integration/rollups/test_rollup_job.py
"""

import asyncio
from collections.abc import Iterator, Sequence
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tests.integration.rollups.rollup_lab import Fleet
from tests.unit.adapters.sqlite.telemetry.telemetry_testkit import T0, open_store, raw
from vakr.adapters.sqlite.telemetry import TELEMETRY_FILE, SqliteTelemetry, SqliteTelemetryStore
from vakr.app.collector.maintenance import TelemetryUpkeep
from vakr.app.collector.rollup_job import RollupJob
from vakr.app.collector.rollup_settings import RollupSettings
from vakr.app.collector.timings import ServiceTimings
from vakr.domain.enums import ObservationType
from vakr.domain.errors import StorageError
from vakr.domain.observations import DevicePoll, Observation
from vakr.domain.paging import PageRequest
from vakr.domain.rollups import RollupBucket, bucket_start
from vakr.domain.series import Metric, TimeWindow
from vakr.ports.clock import SystemClock
from vakr.ports.telemetry import RetentionPolicy, RollupRepository

HOUR, DAY, BUCKET = timedelta(hours=1), timedelta(days=1), timedelta(minutes=15)
SETTLED = timedelta(minutes=11)  # just past E.10's 10-minute completeness delay


class FailingRollups:
    """A rollup repository that fails its n-th write, like a crash mid-pass.

    Attributes:
        writes: Successful writes so far.
    """

    def __init__(self, inner: RollupRepository, fail_at: int) -> None:
        """Wrap a repository.

        Args:
            inner: The real repository.
            fail_at: The 1-based write that raises.

        Returns:
            None

        Raises:
            None
        """
        self._inner, self._fail_at, self.writes = inner, fail_at, 0

    def write(self, buckets: Sequence[RollupBucket]) -> int:
        """Write, or fail on the chosen call.

        Args:
            buckets: Buckets.

        Returns:
            int: Buckets written.

        Raises:
            StorageError: On the ``fail_at``-th call; nothing of it is stored.
        """
        if self.writes + 1 == self._fail_at:
            raise StorageError("disk I/O error", retryable=True)
        self.writes += 1
        return self._inner.write(buckets)

    def read(self, *args: object, **kwargs: object) -> tuple[RollupBucket, ...]:
        """Delegate reads.

        Args:
            *args: As ``RollupRepository.read``.
            **kwargs: As ``RollupRepository.read``.

        Returns:
            tuple[RollupBucket, ...]: The buckets.

        Raises:
            None
        """
        return self._inner.read(*args, **kwargs)  # ty: ignore[invalid-argument-type]

    def coverage_end(self) -> datetime | None:
        """Delegate the frontier.

        Args:
            None

        Returns:
            datetime | None: The frontier.

        Raises:
            None
        """
        return self._inner.coverage_end()


def when(o: Observation) -> datetime:
    """Return an observation's time: its timestamp, or a poll's scheduled slot.

    Args:
        o: An observation.

    Returns:
        datetime: The time.

    Raises:
        None
    """
    return o.scheduled_at if isinstance(o, DevicePoll) else o.timestamp


_OPEN: list[SqliteTelemetryStore] = []


@pytest.fixture(autouse=True)
def _close_stores() -> Iterator[None]:
    """Close every store a test opened.

    Args:
        None

    Returns:
        Iterator[None]: Yields once, then closes.

    Raises:
        None
    """
    yield
    while _OPEN:
        _OPEN.pop().close()


def backend(
    tmp_path: Path, name: str = "a", policy: RetentionPolicy | None = None
) -> SqliteTelemetry:
    """Open a fresh telemetry store under ``tmp_path / name``, closed after the test.

    Args:
        tmp_path: Test directory.
        name: Subdirectory, so one test can hold two stores.
        policy: Retention policy, or the Compact default.

    Returns:
        SqliteTelemetry: The backend.

    Raises:
        None
    """
    (tmp_path / name).mkdir()
    store = open_store(tmp_path / name, policy)
    _OPEN.append(store)
    return SqliteTelemetry(store)


def stored_buckets(tmp_path: Path, name: str = "a") -> int:
    """Count metric buckets the catalog says are stored.

    Args:
        tmp_path: Test directory.
        name: The store's subdirectory.

    Returns:
        int: The catalog's bucket count over all rollup partitions.

    Raises:
        None
    """
    sql = "SELECT coalesce(sum(bucket_count), 0) FROM partition_catalog WHERE family LIKE 'roll_%'"
    return int(raw(tmp_path / name / TELEMETRY_FILE, sql)[0][0])


def test_roll_t03_an_interrupted_job_resumes_without_missing_or_duplicate_buckets(
    tmp_path: Path,
) -> None:
    """ROLL-T03, RET-009: a failure mid-pass, then a restart, gives the uninterrupted result.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    fleet = Fleet()
    rows = list(fleet.observations(T0, 180))
    clean, crashed = backend(tmp_path, "clean"), backend(tmp_path, "crashed")
    fleet.write(clean, rows)
    fleet.write(crashed, rows)
    now = T0 + 3 * HOUR + SETTLED
    assert RollupJob(clean.rollup_source, clean.rollups).run_pass(now).buckets == 12
    failing = FailingRollups(crashed.rollups, fail_at=5)
    with pytest.raises(StorageError):
        RollupJob(crashed.rollup_source, failing).run(now)
    assert crashed.rollups.coverage_end() == T0 + 4 * BUCKET  # four whole buckets committed
    resumed = RollupJob(crashed.rollup_source, crashed.rollups).run_pass(now)
    assert (resumed.buckets, resumed.frontier, resumed.stopped) == (8, T0 + 3 * HOUR, "caught_up")
    window = TimeWindow(T0, T0 + 3 * HOUR)
    expected = fleet.buckets(clean, window)
    assert fleet.buckets(crashed, window) == expected
    assert all(len(found) == 12 for found in expected.values())
    per_bucket = 2 * 3 + 4 * 6  # two devices' ICMP metrics, four interfaces' metrics
    assert (
        stored_buckets(tmp_path, "crashed") == stored_buckets(tmp_path, "clean") == 12 * per_bucket
    )
    again = RollupJob(crashed.rollup_source, crashed.rollups).run_pass(now)
    assert (again.buckets, again.written) == (0, 0)
    assert stored_buckets(tmp_path, "crashed") == 12 * per_bucket


def test_roll_t04_native_rows_expire_only_behind_rollup_coverage(tmp_path: Path) -> None:
    """ROLL-T04, RET-001..003: retention waits for rollups, then expires exactly to them.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    fleet, store = Fleet(devices=1), backend(tmp_path)
    fleet.write(store, fleet.observations(T0, 120))
    now = T0 + 2 * HOUR + SETTLED + store.retention.policy.native
    waiting = store.retention.apply(now)
    assert (waiting.native_cutoff, waiting.native_removed) == (None, 0)
    settings = RollupSettings(max_buckets=4)
    first = RollupJob(store.rollup_source, store.rollups, settings).run_pass(now)
    assert (first.buckets, first.frontier, first.stopped) == (4, T0 + HOUR, "bucket_limit")
    partial = store.retention.apply(now)
    assert partial.native_cutoff == T0 + HOUR  # never past the rollup frontier
    assert partial.native_removed == 60 * (1 + 1 + 2)  # polls, cycles, samples of hour one
    RollupJob(store.rollup_source, store.rollups, settings).run_pass(now)
    done = store.retention.apply(now)
    assert (done.native_cutoff, done.native_removed) == (T0 + 2 * HOUR, 240)
    series = store.query.interface_series(fleet.subjects()[1][0], fleet.collector, Metric.IN_BPS,
                                          TimeWindow(T0, T0 + 2 * HOUR))  # fmt: skip
    assert len(series.points) == 8  # every quarter-hour still graphed, from rollups
    assert series.points[0].maximum == 14_000.0
    assert series.points[-1].maximum == 119_000.0


def test_roll_t05_missing_samples_stay_coverage_gaps_in_rollups_and_graphs(tmp_path: Path) -> None:
    """ROLL-T05, STORE-012: a thin bucket keeps its counts; a missing one is a gap, not zero.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    fleet, store = Fleet(devices=1, interfaces=1), backend(tmp_path)
    minute = timedelta(minutes=1)

    def kept(o: Observation) -> bool:
        """Drop bucket two entirely, and five minutes of bucket one except its polls.

        Args:
            o: An observation.

        Returns:
            bool: ``True`` to write it.

        Raises:
            None
        """
        if T0 + 2 * BUCKET <= when(o) < T0 + 3 * BUCKET:
            return False
        return isinstance(o, DevicePoll) or not T0 + 20 * minute <= when(o) < T0 + 25 * minute

    rows = [o for o in fleet.observations(T0, 60) if kept(o)]
    fleet.write(store, rows)
    now = T0 + HOUR + SETTLED
    report = RollupJob(store.rollup_source, store.rollups).run_pass(now)
    assert report.buckets == 4  # the empty quarter-hour is looked at once, then skipped
    assert report.thin == 2  # the device's and the interface's bucket two
    store.retention.apply(now + store.retention.policy.native)
    iface = fleet.subjects()[1][0]
    stored = store.rollups.read(iface, fleet.collector, Metric.IN_BPS, TimeWindow(T0, T0 + HOUR),
                                limit=10)  # fmt: skip
    assert [b.bucket_start for b in stored] == [T0, T0 + BUCKET, T0 + 3 * BUCKET]
    assert (stored[1].expected_count, stored[1].sample_count) == (15, 10)
    series = store.query.interface_series(iface, fleet.collector, Metric.IN_BPS,
                                          TimeWindow(T0, T0 + HOUR))  # fmt: skip
    assert [p.value is None for p in series.points] == [False, False, True, False]
    assert series.points[2].minimum is None  # a gap, never zero


def test_a_ten_day_backlog_drains_over_bounded_passes(tmp_path: Path) -> None:
    """RET-009, E.10: weeks of backlog after an upgrade drain pass by pass, with no gap.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    fleet, store = Fleet(devices=1, interfaces=2), backend(tmp_path)
    days = 10
    fleet.write(store, fleet.observations(T0, days * 288, interval_s=300))
    now = T0 + days * DAY + SETTLED
    settings = RollupSettings(max_buckets=192)  # two days per pass
    frontiers = []
    for _ in range(6):
        report = RollupJob(store.rollup_source, store.rollups, settings).run(now)
        assert report >= 0
        frontiers.append(store.rollups.coverage_end())
    assert frontiers == [T0 + 2 * k * DAY for k in range(1, 6)] + [T0 + days * DAY]
    iface = fleet.subjects()[1][1]
    found = []
    for k in range(0, days, 5):
        window = TimeWindow(T0 + k * DAY, T0 + (k + 5) * DAY)
        found.extend(store.rollups.read(iface, fleet.collector, Metric.OUT_BPS, window, limit=1200))
    assert [b.bucket_start for b in found] == [T0 + k * BUCKET for k in range(days * 96)]
    assert {(b.expected_count, b.sample_count) for b in found} == {(3, 3)}


def test_time_budget_stops_a_pass_and_the_next_one_continues(tmp_path: Path) -> None:
    """E.10: a pass starts no bucket after its time budget; nothing is lost or repeated.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    fleet, store = Fleet(devices=1, interfaces=1), backend(tmp_path)
    fleet.write(store, fleet.observations(T0, 120))
    ticks = iter(range(1000))
    job = RollupJob(store.rollup_source, store.rollups, RollupSettings(budget_s=3.5),
                    monotonic=lambda: float(next(ticks)))  # fmt: skip
    now = T0 + 2 * HOUR + SETTLED
    first = job.run_pass(now)
    assert (first.buckets, first.stopped) == (3, "time_budget")
    second = job.run_pass(now)
    assert (second.buckets, second.stopped, second.frontier) == (3, "time_budget", T0 + 6 * BUCKET)
    assert job.run_pass(now).frontier == T0 + 2 * HOUR


def test_buckets_wait_for_the_spool_and_for_e10_completeness(tmp_path: Path) -> None:
    """E.10, RET-009: no bucket is rolled up while its data may still be on its way.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    fleet, store = Fleet(devices=1, interfaces=1), backend(tmp_path)
    fleet.write(store, fleet.observations(T0, 120))
    held = RollupJob(store.rollup_source, store.rollups, pending=lambda: T0 + HOUR)
    assert held.run_pass(T0 + 3 * HOUR).frontier == T0 + 3 * BUCKET  # 10 min before the spool
    fresh = RollupJob(store.rollup_source, store.rollups)
    assert fresh.run_pass(T0 + 2 * HOUR + timedelta(minutes=10)).frontier == T0 + 7 * BUCKET
    assert fresh.run_pass(T0 + 2 * HOUR + SETTLED).frontier == T0 + 2 * HOUR


def test_an_outage_is_stepped_over_without_writing_empty_buckets(tmp_path: Path) -> None:
    """RET-009, ROLL-T05: a collector outage costs one look, not one bucket per quarter-hour.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    fleet, store = Fleet(devices=1, interfaces=1), backend(tmp_path)
    fleet.write(store, fleet.observations(T0, 30))
    fleet.write(store, fleet.observations(T0 + 3 * DAY, 30))
    report = RollupJob(store.rollup_source, store.rollups).run_pass(T0 + 3 * DAY + HOUR)
    # Two buckets, one look at the outage, two buckets, one look at the empty tail.
    assert (report.buckets, report.frontier) == (6, T0 + 3 * DAY + 2 * BUCKET)
    devices, _ = fleet.subjects()
    stored = store.rollups.read(devices[0], fleet.collector, Metric.LOSS_PCT,
                                TimeWindow(T0, T0 + 4 * DAY), limit=100)  # fmt: skip
    assert [b.bucket_start for b in stored] == [T0, T0 + BUCKET, T0 + 3 * DAY,
                                                T0 + 3 * DAY + BUCKET]  # fmt: skip


def test_roll_t04_upkeep_runs_the_job_before_each_retention_pass(tmp_path: Path) -> None:
    """ROLL-T04, E.27: the collector's hourly pass rolls up, then expires exactly behind it.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    store = backend(tmp_path, policy=RetentionPolicy(DAY, 400 * DAY))
    clock = SystemClock()
    base = bucket_start(clock.now_utc()) - 3 * DAY
    fleet = Fleet(devices=1, interfaces=1)
    fleet.write(store, fleet.observations(base, 60))
    job = RollupJob(store.rollup_source, store.rollups, monotonic=clock.monotonic)
    upkeep = TelemetryUpkeep(store, clock, ServiceTimings(), job)

    async def one_pass() -> None:
        """Run upkeep until its first retention pass has finished.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        task = asyncio.create_task(upkeep.run())
        for _ in range(200):
            if upkeep.passes["retention"]:
                break
            await asyncio.sleep(0.05)
        upkeep.stop()
        await task

    asyncio.run(one_pass())
    assert upkeep.failures == {"move": 0, "projection": 0, "retention": 0}
    assert store.rollups.coverage_end() == base + HOUR
    assert store.retention.native_horizon() == base + HOUR
    iface = fleet.subjects()[1][0]
    page = store.query.read_raw(ObservationType.INTERFACE, iface, TimeWindow(base, base + HOUR),
                                PageRequest(10))  # fmt: skip
    assert page.items == ()
