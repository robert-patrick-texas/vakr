"""Exclusive lock so only one update runs at a time (UPD-007).

The daily timer and an operator at the console can start self-update at
the same moment; two concurrent ``apt-get`` runs and backups would corrupt
each other. An advisory ``flock`` is released automatically when the
process exits, even after a crash, so a stale lock can never block future
updates.

The lock lives in self-update's root-only directory, opened without
following links (UPD-005): in a directory another account may write, that
account could swap in a link, so root would create a file wherever it
points, or hold the lock itself and stop every update.

Dependencies:
    fcntl: POSIX advisory locking.
    vakr.lifecycle.update.private: The directory check.

Safety:
    Non-blocking: a second updater fails fast with ``LockBusyError`` instead
    of queueing behind the first.

Example:
    >>> with exclusive_lock(Path("/var/lib/vakr-update/self-update.lock")):  # doctest: +SKIP
    ...     pass
"""

import fcntl
import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from vakr.lifecycle.update.private import ensure_private_dir


class LockBusyError(Exception):
    """Another process holds the update lock."""


@contextmanager
def exclusive_lock(path: Path) -> Generator[None]:
    """Hold an exclusive non-blocking ``flock`` on ``path`` for the block.

    Args:
        path: Lock file; it and its directory are created if missing, and
            the directory must be the caller's alone.

    Returns:
        Generator[None]: Context manager body runs while the lock is held.

    Raises:
        LockBusyError: When another process already holds the lock.
        OSError: When the lock file cannot be created, is a link, or its
            directory is not the caller's alone (``PrivateStorageError``).
    """
    ensure_private_dir(path.parent, mode=0o755)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LockBusyError(f"another update is already running (lock {path})") from exc
        yield
    finally:
        os.close(fd)
