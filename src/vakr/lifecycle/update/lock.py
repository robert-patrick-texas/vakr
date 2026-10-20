"""Exclusive lock so only one update runs at a time (UPD-007).

The daily timer and an operator at the console can start self-update at
the same moment; two concurrent ``apt-get`` runs and backups would corrupt
each other. An advisory ``flock`` on a file in the runtime directory is
released automatically when the process exits, even after a crash, so a
stale lock can never block future updates.

Dependencies:
    fcntl: POSIX advisory locking.

Safety:
    Non-blocking: a second updater fails fast with ``LockBusyError`` instead
    of queueing behind the first.

Example:
    >>> with exclusive_lock(Path("/run/vakr/self-update.lock")):  # doctest: +SKIP
    ...     pass
"""

import fcntl
import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path


class LockBusyError(Exception):
    """Another process holds the update lock."""


@contextmanager
def exclusive_lock(path: Path) -> Generator[None]:
    """Hold an exclusive non-blocking ``flock`` on ``path`` for the block.

    Args:
        path: Lock file; it and its directory are created if missing.

    Returns:
        Generator[None]: Context manager body runs while the lock is held.

    Raises:
        LockBusyError: When another process already holds the lock.
        OSError: When the lock file cannot be created.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LockBusyError(f"another update is already running (lock {path})") from exc
        yield
    finally:
        os.close(fd)
