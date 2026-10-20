"""DB-008: concurrent readers beside the bounded single writer, on a real WAL file.

Every writing unit of work adds one device and one audit event together,
so a reader that ever saw the two counts differ would have seen a partial
commit (DB-002). Readers must never fail or block, writers must queue
within their bounds, and the file must pass ``PRAGMA integrity_check``.

Dependencies:
    pytest, threading; vakr.adapters.sqlite; tests.contract.contract_samples.

Example:
    $ uv run pytest tests/unit/adapters/sqlite/test_sqlite_concurrency.py
"""

import threading
import time
from collections.abc import Callable
from pathlib import Path

from tests.contract import contract_samples as s
from tests.unit.adapters.sqlite.sqlite_testkit import factory, migrated_store, raw_rows
from vakr.adapters.sqlite.connection import SqliteSettings
from vakr.adapters.sqlite.metadata import metadata_migrations
from vakr.domain.errors import StorageError
from vakr.domain.paging import PageRequest
from vakr.ports.metadata import MetadataUnitOfWorkFactory

SEED = 10
READERS = 4
WRITERS = 3
WRITES_EACH = 20


def _guard(work: Callable[[], None], failures: list[BaseException]) -> Callable[[], None]:
    """Wrap thread work so failures reach the test instead of vanishing.

    Args:
        work: The thread body.
        failures: Collects anything it raises.

    Returns:
        Callable[[], None]: The wrapped body.

    Raises:
        None
    """

    def run() -> None:
        """Run ``work`` and record what it raises.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        try:
            work()
        except BaseException as exc:  # noqa: BLE001  # the test asserts on every failure
            failures.append(exc)

    return run


def _write_pairs(new_uow: MetadataUnitOfWorkFactory, prefix: str, count: int) -> None:
    """Commit ``count`` units of work, each adding one device and its audit event.

    Args:
        new_uow: Opens units of work.
        prefix: Device name prefix, unique per writer.
        count: Units of work to commit.

    Returns:
        None

    Raises:
        None
    """
    for n in range(count):
        with new_uow() as uow:
            dev = s.device(f"{prefix}-{n:03d}")
            uow.devices.add(dev)
            uow.audit.append(s.audit(s.T0, object_id=dev.id))
            uow.commit()


def _read_until(
    new_uow: MetadataUnitOfWorkFactory, stop: threading.Event, seen: list[tuple[int, int]]
) -> None:
    """Read both counts in one snapshot, over and over, until told to stop.

    Args:
        new_uow: Opens units of work.
        stop: Set when the writers are done.
        seen: Collects ``(devices, audit events)`` per snapshot.

    Returns:
        None

    Raises:
        AssertionError: If a page disagrees with its own total.
    """
    while not stop.is_set():
        with new_uow() as uow:
            page = uow.devices.list_page(PageRequest(limit=500))
            events = uow.audit.list_page(PageRequest(limit=1)).total
            assert page.total == len(page.items)
            seen.append((page.total or 0, events or 0))


def test_db008_readers_and_bounded_writers_stay_consistent(tmp_path: Path) -> None:
    """DB-008: readers and queued writers finish, see whole commits, and leave a sound file.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "metadata.db"
    store = migrated_store(path, SqliteSettings(max_connections=READERS + WRITERS + 1))
    new_uow = factory(store)
    _write_pairs(new_uow, "seed", SEED)
    stop, failures = threading.Event(), []
    logs: list[list[tuple[int, int]]] = [[] for _ in range(READERS)]
    readers = [
        threading.Thread(target=_guard(lambda log=log: _read_until(new_uow, stop, log), failures))
        for log in logs
    ]
    writers = [
        threading.Thread(target=_guard(lambda n=n: _write_pairs(new_uow, f"w{n}", WRITES_EACH),
                                       failures))
        for n in range(WRITERS)
    ]  # fmt: skip
    for thread in (*readers, *writers):
        thread.start()
    for thread in writers:
        thread.join(60)
    stop.set()
    for thread in readers:
        thread.join(60)
    assert failures == []
    expected = SEED + WRITERS * WRITES_EACH
    with new_uow() as uow:
        assert uow.devices.list_page(PageRequest(limit=1)).total == expected
        assert uow.audit.list_page(PageRequest(limit=1)).total == expected
    for log in logs:
        assert log
        assert all(devices == events for devices, events in log)  # never a partial commit
        assert [d for d, _ in log] == sorted(d for d, _ in log)  # snapshots never go back
    store.close()
    assert metadata_migrations(path).check_integrity().ok
    assert raw_rows(path, "PRAGMA integrity_check") == [("ok",)]


def test_db008_writer_queue_is_bounded_not_unbounded(tmp_path: Path) -> None:
    """DB-008: beyond ``max_waiting_writers``, writers fail at once and retryably; none hang.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    settings = SqliteSettings(max_waiting_writers=1, lock_wait_ms=5_000, max_connections=8)
    store = migrated_store(tmp_path / "metadata.db", settings)
    new_uow = factory(store)
    release, failures = threading.Event(), []
    refused: list[StorageError] = []

    def hold() -> None:
        """Hold the write path until the contenders have tried.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        with new_uow() as uow:
            uow.tags.add(s.tag("holder"))
            release.wait(10)
            uow.commit()

    def contend(n: int) -> None:
        """Try to write once; record a refusal.

        Args:
            n: Contender number.

        Returns:
            None

        Raises:
            None
        """
        try:
            _write_pairs(new_uow, f"c{n}", 1)
        except StorageError as exc:
            refused.append(exc)

    holder = threading.Thread(target=_guard(hold, failures))
    holder.start()
    deadline = time.monotonic() + 10
    while not store._gate.held and time.monotonic() < deadline:
        time.sleep(0.005)
    contenders = [
        threading.Thread(target=_guard(lambda n=n: contend(n), failures)) for n in range(4)
    ]
    for thread in contenders:
        thread.start()
    while len(refused) < 3 and time.monotonic() < deadline:
        time.sleep(0.005)
    release.set()
    for thread in (holder, *contenders):
        thread.join(30)
    assert failures == []
    assert len(refused) == 3
    assert all(exc.retryable for exc in refused)
    with new_uow() as uow:
        assert uow.devices.list_page(PageRequest()).total == 1  # the one queued writer got in
    store.close()
