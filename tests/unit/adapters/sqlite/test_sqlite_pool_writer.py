"""Bounded single-writer path and connection pool (5.3, DB-008, 17.2 bounded limits).

Dependencies:
    pytest, threading; vakr.adapters.sqlite.pool, writer.

Example:
    $ uv run pytest tests/unit/adapters/sqlite/test_sqlite_pool_writer.py
"""

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from vakr.adapters.sqlite.connection import OpenMode, SqliteSettings
from vakr.adapters.sqlite.pool import ConnectionPool
from vakr.adapters.sqlite.writer import WriterGate
from vakr.domain.errors import StorageError


def _hold(gate: WriterGate, release: threading.Event, held: threading.Event) -> None:
    """Hold the gate from another thread until told to let go.

    Args:
        gate: The gate.
        release: Set to let go.
        held: Set once the gate is held.

    Returns:
        None

    Raises:
        None
    """
    gate.acquire()
    held.set()
    release.wait(5)
    gate.release()


def test_gate_admits_one_writer_and_refuses_self_deadlock() -> None:
    """5.3: one writer at a time; a thread asking twice fails at once instead of hanging.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    gate = WriterGate()
    gate.acquire()
    assert gate.held
    with pytest.raises(StorageError, match="already holds") as caught:
        gate.acquire()
    assert not caught.value.retryable
    gate.release()
    assert not gate.held


def test_gate_bounds_the_queue_and_the_wait() -> None:
    """DB-008: a full queue fails at once and a long wait times out, both retryable.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    gate = WriterGate(SqliteSettings(lock_wait_ms=150, max_waiting_writers=1))
    release, held = threading.Event(), threading.Event()
    holder = threading.Thread(target=_hold, args=(gate, release, held))
    holder.start()
    assert held.wait(5)
    outcome: list[StorageError] = []

    def wait_in_queue() -> None:
        """Queue behind the holder and record the timeout.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        try:
            gate.acquire()
        except StorageError as exc:
            outcome.append(exc)

    waiter = threading.Thread(target=wait_in_queue)
    waiter.start()
    deadline = time.monotonic() + 5
    while gate.waiting == 0 and time.monotonic() < deadline:
        time.sleep(0.005)
    started = time.monotonic()
    with pytest.raises(StorageError, match="already waiting") as full:
        gate.acquire()  # the queue holds one waiter; the next is refused immediately
    assert full.value.retryable
    assert time.monotonic() - started < 0.1
    waiter.join(5)
    assert len(outcome) == 1
    assert "waited 150 ms" in str(outcome[0])
    assert outcome[0].retryable
    release.set()
    holder.join(5)


def test_waiting_writer_gets_in_when_the_holder_leaves() -> None:
    """DB-008: a queued writer proceeds as soon as the writer before it releases.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    gate = WriterGate()
    release, held = threading.Event(), threading.Event()
    holder = threading.Thread(target=_hold, args=(gate, release, held))
    holder.start()
    assert held.wait(5)
    threading.Timer(0.05, release.set).start()
    gate.acquire()  # blocks until the holder lets go, well inside lock_wait_ms
    gate.release()
    holder.join(5)


def test_pool_reuses_connections_and_bounds_them(tmp_path: Path) -> None:
    """5.3: returned connections are reused; past the bound, borrowers fail in time.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    settings = SqliteSettings(max_connections=2, lock_wait_ms=100)
    pool = ConnectionPool(tmp_path / "x.db", settings, mode=OpenMode.CREATE)
    first = pool.acquire()
    first.execute("BEGIN")
    first.execute("CREATE TABLE t (x INTEGER)")
    pool.release(first)  # rolled back on return
    again = pool.acquire()
    assert again is first
    assert not again.in_transaction
    second = pool.acquire()
    with pytest.raises(StorageError, match="all 2 connections") as caught:
        pool.acquire()
    assert caught.value.retryable
    pool.release(second)
    pool.close()
    pool.release(again)  # returned after close: closed, not kept
    with pytest.raises(sqlite3.ProgrammingError):
        again.execute("SELECT 1")
    with pytest.raises(StorageError, match="closed"):
        pool.acquire()


def test_pool_frees_slots_of_failed_and_broken_connections(tmp_path: Path) -> None:
    """A connection that cannot open, prepare, or roll back gives its slot back.

    Args:
        tmp_path: Directory for the database.

    Returns:
        None

    Raises:
        None
    """
    settings = SqliteSettings(max_connections=1, lock_wait_ms=100)
    missing = ConnectionPool(tmp_path / "no" / "x.db", settings)
    for _ in range(2):  # the second attempt proves the first freed its slot
        with pytest.raises(StorageError):
            missing.acquire()

    def refuse(_conn: sqlite3.Connection) -> None:
        """Fail adapter setup on a new connection.

        Args:
            _conn: The connection.

        Returns:
            None

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("setup failed")

    picky = ConnectionPool(tmp_path / "x.db", settings, mode=OpenMode.CREATE, prepare=refuse)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            picky.acquire()
    pool = ConnectionPool(tmp_path / "x.db", settings)
    broken = pool.acquire()
    broken.close()
    pool.release(broken)  # its rollback fails, so it is discarded
    fresh = pool.acquire()
    assert fresh is not broken
    pool.release(fresh)
    pool.close()
