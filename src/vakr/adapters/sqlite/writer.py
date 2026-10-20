"""The bounded single-writer path for one SQLite database (5.3, DB-008).

SQLite lets one connection write at a time. If every thread simply tried,
the losers would spin in SQLite's busy handler, burning CPU and timing out
at random. ``WriterGate`` instead queues this process's writers for the
single write lock, with two bounds (blueprint 4.3: every queue is bounded):

- at most ``max_waiting_writers`` may wait; one more fails at once;
- none waits longer than ``lock_wait_ms``.

Both failures are retryable ``StorageError``s, never a hang. Readers never
touch the gate: in WAL mode they read a snapshot while the writer works.
Another *process* (``vakr db migrate``, a second service) is handled by
SQLite's own ``busy_timeout``, which is bounded too.

A unit of work takes the gate at its first write and releases it at
``commit`` or ``rollback``. The M3 telemetry writer thread takes it per batch.

Dependencies:
    threading: The condition variable behind the queue.
    vakr.adapters.sqlite.connection: The bounds.
    vakr.domain.errors: ``StorageError``.

Safety:
    A thread that already holds the gate and asks again would wait for
    itself; that is refused immediately instead.

Example:
    >>> gate = WriterGate(DEFAULT_SETTINGS)
    >>> gate.acquire(); gate.held
    True
    >>> gate.release(); gate.held
    False
"""

import threading

from vakr.adapters.sqlite.connection import DEFAULT_SETTINGS, SqliteSettings
from vakr.domain.errors import StorageError


class WriterGate:
    """Admits one writer at a time, with a bounded queue and a bounded wait.

    Attributes:
        None beyond the private queue state behind ``held`` and ``waiting``.
    """

    def __init__(self, settings: SqliteSettings = DEFAULT_SETTINGS) -> None:
        """Create an open gate.

        Args:
            settings: ``max_waiting_writers`` and ``lock_wait_ms``.

        Returns:
            None

        Raises:
            None
        """
        self._cond = threading.Condition()
        self._holder: int | None = None
        self._waiting = 0
        self._max_waiting = settings.max_waiting_writers
        self._wait_ms = settings.lock_wait_ms

    @property
    def held(self) -> bool:
        """Whether a writer holds the gate now.

        Args:
            None

        Returns:
            bool: ``True`` while a writer is admitted.

        Raises:
            None
        """
        with self._cond:
            return self._holder is not None

    @property
    def waiting(self) -> int:
        """How many writers are queued now.

        Args:
            None

        Returns:
            int: Queue length.

        Raises:
            None
        """
        with self._cond:
            return self._waiting

    def acquire(self) -> None:
        """Wait, within the bounds, until this thread may write.

        Args:
            None

        Returns:
            None

        Raises:
            StorageError: If this thread already holds the gate, the queue is
                full, or the wait times out.
        """
        me = threading.get_ident()
        with self._cond:
            if self._holder == me:
                raise StorageError(
                    "this thread already holds the database write path in another unit of "
                    "work; commit or close that one first",
                    retryable=False,
                )
            if self._holder is not None and self._waiting >= self._max_waiting:
                raise StorageError(
                    f"{self._waiting} writers are already waiting for the database; retry shortly",
                    retryable=True,
                )
            self._waiting += 1
            try:
                free = self._cond.wait_for(lambda: self._holder is None, self._wait_ms / 1000)
            finally:
                self._waiting -= 1
            if not free:
                raise StorageError(
                    f"waited {self._wait_ms} ms for the database write path; retry shortly",
                    retryable=True,
                )
            self._holder = me

    def release(self) -> None:
        """Let the next queued writer in.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        with self._cond:
            self._holder = None
            self._cond.notify()
