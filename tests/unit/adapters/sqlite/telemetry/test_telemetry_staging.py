"""The staging invariant, idempotent replay, late rows, partitions, and crash safety.

ADR-021 relies on one invariant: a native row is in its staging table
exactly when ``ts_us >= staging_boundary_us``, and in its day partition
otherwise, because the mover copies, deletes, and advances the boundary in
one transaction. These tests pin it (STATE-001), including with concurrent
readers, and check what it buys: replays are no-ops across a move
(STORE-009), late rows land directly in their day partition, partitions
appear at UTC midnight, and a crash between a flush and a move loses
nothing (STORE-010).

Dependencies:
    pytest, subprocess, threading; vakr.adapters.sqlite.telemetry; telemetry_testkit.

Example:
    $ uv run pytest tests/unit/adapters/sqlite/telemetry/test_telemetry_staging.py
"""

import subprocess
import sys
import textwrap
import threading
from pathlib import Path

from tests.contract import contract_samples as s
from tests.unit.adapters.sqlite.telemetry.telemetry_testkit import (
    MINUTE,
    T0,
    at,
    open_store,
    raw,
    tables_named,
    write,
)
from vakr.adapters.sqlite.telemetry import SqliteTelemetry, SqliteTelemetryStore
from vakr.domain.enums import ObservationType
from vakr.domain.ids import CollectorId, InterfaceId, new_id
from vakr.domain.observations import InterfaceObservation
from vakr.domain.paging import PageRequest
from vakr.domain.series import Metric, TimeWindow

DAY = TimeWindow(at(-1), at(3))


def _samples(minutes: range, iface: InterfaceId | None = None) -> list[InterfaceObservation]:
    """Build one interface's samples at ``T0 + m`` minutes.

    Args:
        minutes: Minute offsets.
        iface: The interface, or a new one.

    Returns:
        list[InterfaceObservation]: The samples, from one fixed device and collector.

    Raises:
        None
    """
    target = iface or new_id(InterfaceId)
    return [s.sample(_DEV, target, _COL, T0 + m * MINUTE, in_bps=float(m)) for m in minutes]


_DEV, _COL = s.device().id, new_id(CollectorId)


def _all_ids(backend: SqliteTelemetry, subject: InterfaceId) -> list[str]:
    """Read every stored sample ID of one interface through ``read_raw`` pages.

    Args:
        backend: The backend.
        subject: The interface.

    Returns:
        list[str]: IDs in ``(timestamp, id)`` order.

    Raises:
        None
    """
    ids, cursor = [], None
    while True:
        page = backend.query.read_raw(ObservationType.INTERFACE, subject, DAY,
                                      PageRequest(limit=7, cursor=cursor))  # fmt: skip
        ids.extend(str(o.observation_id) for o in page.items)
        if (cursor := page.next_cursor) is None:
            return ids


def _where(path: Path) -> tuple[int, int, int]:
    """Return the boundary and the staged and partitioned row counts, checking the invariant.

    Args:
        path: Database file.

    Returns:
        tuple[int, int, int]: Boundary, staged rows, partitioned rows.

    Raises:
        AssertionError: If a row sits on the wrong side of the boundary or twice.
    """
    state = "SELECT value FROM telemetry_state WHERE name = 'staging_boundary_us'"
    (boundary,) = raw(path, state)[0]
    staged = raw(path, "SELECT ts_us, observation_uuid FROM obs_iface_hot")
    parted = []
    for table in tables_named(path, "obs_iface_d"):
        # Partition names come from sqlite_schema, filtered to the obs_iface_d prefix.
        parted += raw(path, f"SELECT ts_us, observation_uuid FROM {table}")  # noqa: S608
    assert all(ts >= boundary for ts, _ in staged)
    assert all(ts < boundary for ts, _ in parted)
    assert not {u for _, u in staged} & {u for _, u in parted}
    return boundary, len(staged), len(parted)


def test_state001_every_row_on_exactly_one_side_of_the_boundary(tmp_path: Path) -> None:
    """STATE-001, ADR-021: across flushes and moves each row is staged xor partitioned.

    Args:
        tmp_path: Directory for the file.

    Returns:
        None

    Raises:
        None
    """
    store = open_store(tmp_path)
    backend, iface = SqliteTelemetry(store), new_id(InterfaceId)
    written: list[str] = []
    for start in range(0, 60, 10):
        batch = _samples(range(start, start + 10), iface)
        assert write(backend, batch).inserted == 10
        written += [str(o.observation_id) for o in batch]
        report = backend.maintain(T0 + (start + 7) * MINUTE)
        boundary, staged, parted = _where(store.path)
        assert staged + parted == len(written)
        assert report.boundary == T0 + (start + 5) * MINUTE  # now minus the 2-minute lag
        assert _all_ids(backend, iface) == sorted(written, key=written.index)
    assert boundary > 0
    store.close()


def test_state001_concurrent_readers_never_see_a_row_twice_or_miss_one(tmp_path: Path) -> None:
    """STATE-001, DB-008: readers racing the writer and mover always see a committed prefix.

    Args:
        tmp_path: Directory for the file.

    Returns:
        None

    Raises:
        None
    """
    store = open_store(tmp_path)
    iface = new_id(InterfaceId)
    rows = _samples(range(240), iface)
    committed: list[int] = [0]
    failures: list[BaseException] = []
    done = threading.Event()

    def writer() -> None:
        """Flush 4-sample batches and move after every third one.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        backend = SqliteTelemetry(store)
        try:
            for i in range(0, len(rows), 4):
                write(backend, rows[i : i + 4])
                committed[0] = i + 4
                if i % 12 == 0:
                    backend.maintain(rows[i].timestamp + 3 * MINUTE)
        except BaseException as exc:  # noqa: BLE001  # reported by the test thread
            failures.append(exc)
        finally:
            done.set()

    def reader() -> None:
        """Read the whole window repeatedly and check what it sees.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        backend, expected = SqliteTelemetry(store), [str(o.observation_id) for o in rows]
        try:
            while not done.is_set():
                before = committed[0]
                page = backend.query.read_raw(ObservationType.INTERFACE, iface, DAY,
                                              PageRequest(limit=500))  # fmt: skip
                seen = [str(o.observation_id) for o in page.items]
                assert len(seen) >= before, "a committed row was missed"
                assert seen == expected[: len(seen)], "a row was read twice or skipped"
        except BaseException as exc:  # noqa: BLE001  # reported by the test thread
            failures.append(exc)

    threads = [
        threading.Thread(target=writer),
        *(threading.Thread(target=reader) for _ in range(3)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert failures == []
    assert len(_all_ids(SqliteTelemetry(store), iface)) == len(rows)
    store.close()


def test_store009_replay_across_a_move_inserts_nothing(tmp_path: Path) -> None:
    """STORE-009: a batch replayed after some of its rows moved is entirely duplicate.

    Args:
        tmp_path: Directory for the file.

    Returns:
        None

    Raises:
        None
    """
    store = open_store(tmp_path)
    backend = SqliteTelemetry(store)
    batch = _samples(range(20))
    assert write(backend, batch).inserted == 20
    backend.maintain(T0 + 12 * MINUTE)  # minutes 0-9 move, 10-19 stay staged
    _boundary, staged, parted = _where(store.path)
    assert (staged, parted) == (10, 10)
    replay = write(backend, batch)
    assert (replay.inserted, replay.duplicates) == (0, 20)
    backend.maintain(T0 + 60 * MINUTE)
    assert (write(backend, batch).inserted, _where(store.path)[1:]) == (0, (0, 20))
    store.close()


def test_late_rows_go_straight_to_their_day_partition(tmp_path: Path) -> None:
    """ADR-021: a row older than the boundary (a peer's backfill) skips staging.

    Args:
        tmp_path: Directory for the file.

    Returns:
        None

    Raises:
        None
    """
    store = open_store(tmp_path)
    backend, iface = SqliteTelemetry(store), new_id(InterfaceId)
    write(backend, _samples(range(30, 40), iface))
    backend.maintain(T0 + 60 * MINUTE)
    late = _samples(range(10), iface)
    assert write(backend, late).inserted == 10
    _boundary, staged, parted = _where(store.path)
    assert (staged, parted) == (0, 20)
    assert len(_all_ids(backend, iface)) == 20
    assert write(backend, late).duplicates == 10
    store.close()


def test_partitions_are_created_at_utc_midnight(tmp_path: Path) -> None:
    """5.1, ADR-021: rows either side of UTC midnight land in two catalogued day tables.

    Args:
        tmp_path: Directory for the file.

    Returns:
        None

    Raises:
        None
    """
    store = open_store(tmp_path)
    backend, iface = SqliteTelemetry(store), new_id(InterfaceId)
    times = [at(0, 23, 59, 30), at(1, 0, 0, 30)]
    write(backend, [s.sample(_DEV, iface, _COL, t, in_bps=1.0) for t in times])
    backend.maintain(at(1, 0, 10))
    assert tables_named(store.path, "obs_iface_d") == ["obs_iface_d20261001", "obs_iface_d20261002"]
    catalog = raw(store.path, "SELECT table_name, start_us, end_us FROM partition_catalog "
                  "WHERE family = 'obs_iface' ORDER BY start_us")  # fmt: skip
    day_us = 86_400_000_000
    t0_us = int(T0.timestamp()) * 1_000_000
    assert [(r[1], r[2]) for r in catalog] == [(t0_us, t0_us + day_us),
                                                (t0_us + day_us, t0_us + 2 * day_us)]  # fmt: skip
    series = backend.query.interface_series(iface, _COL, Metric.IN_BPS,
                                            TimeWindow(at(0, 23), at(1, 1)))  # fmt: skip
    assert [p.timestamp for p in series.points] == times
    store.close()


_CRASH = textwrap.dedent("""
    import os, sys
    from pathlib import Path
    from vakr.adapters.sqlite.telemetry import SqliteTelemetry, SqliteTelemetryStore
    from vakr.adapters.sqlite.telemetry import maintenance
    from tests.unit.adapters.sqlite.telemetry.telemetry_testkit import write, T0, MINUTE
    from tests.unit.adapters.sqlite.telemetry.test_telemetry_staging import _samples
    backend = SqliteTelemetry(SqliteTelemetryStore(Path(sys.argv[1])))
    assert write(backend, _samples(range(30))).inserted == 30  # committed: the flush
    def crash(*_args):
        os._exit(3)  # the process dies inside the move, after copying and deleting rows
    maintenance.set_state = crash
    backend.maintain(T0 + 40 * MINUTE)
""")


def test_store010_crash_between_flush_and_move_loses_nothing(tmp_path: Path) -> None:
    """STORE-010, ADR-021: a crash mid-move leaves the flushed rows staged and the boundary alone.

    Args:
        tmp_path: Directory for the file.

    Returns:
        None

    Raises:
        None
    """
    store = open_store(tmp_path)
    store.close()
    # Our own interpreter running a fixed script on a temporary file.
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _CRASH, str(store.path)],
        check=False,
        capture_output=True,
        cwd=Path(__file__).parents[5],
    )
    assert result.returncode == 3, result.stderr.decode()
    boundary, staged, parted = _where(store.path)
    assert (boundary, staged, parted) == (0, 30, 0)
    assert tables_named(store.path, "obs_iface_d") == []  # the partition rolled back too
    assert raw(store.path, "PRAGMA integrity_check") == [("ok",)]
    reopened = SqliteTelemetryStore(store.path)
    assert SqliteTelemetry(reopened).maintain(T0 + 40 * MINUTE).moved == 30
    assert _where(store.path)[1:] == (0, 30)
    reopened.close()


def test_maintain_never_moves_back_and_truncates_the_wal(tmp_path: Path) -> None:
    """ADR-021, E.21: the boundary only advances; each maintain truncates the WAL.

    Args:
        tmp_path: Directory for the file.

    Returns:
        None

    Raises:
        None
    """
    store = open_store(tmp_path)
    backend = SqliteTelemetry(store)
    write(backend, _samples(range(30)))
    first = backend.maintain(T0 + 20 * MINUTE)
    again = backend.maintain(T0 + 5 * MINUTE)  # a clock stepping back
    assert (first.moved, again.moved, again.boundary) == (18, 0, first.boundary)
    assert not again.checkpoint_busy
    wal = store.path.with_name(f"{store.path.name}-wal")
    assert not wal.exists() or wal.stat().st_size == 0
    store.close()
