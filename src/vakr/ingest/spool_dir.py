"""Opening and closing a spool directory: lock, recover, and the clean-stop marker (6.2, 16.4).

Opening takes an exclusive ``flock`` on ``<dir>/lock`` so a second process
(a misconfigured second collector with the same UUID, say) cannot write the
same spool: one producer and one consumer per spool (6.2). It then runs
recovery and measures the quarantine. Closing records a clean stop only when
the writer flushed and fsynced everything, so the next start knows whether
to report an unclean-stop loss incident (E.8, STORE-007).

These steps block on disk and run in worker threads, never on the event loop.

Dependencies:
    fcntl, os: The advisory lock.
    vakr.ingest: Recovery, incidents, segments, and state files.

Safety:
    The lock is released by closing its descriptor, so a crashed process
    never leaves a stale lock behind.

Example:
    >>> LOCK_FILE
    'lock'
"""

import fcntl
import os
from dataclasses import dataclass
from pathlib import Path

from vakr.ingest.errors import SpoolLockedError
from vakr.ingest.fileops import FileOps
from vakr.ingest.incident_log import IncidentLog
from vakr.ingest.recovery import Recovery, recover
from vakr.ingest.segments import ensure_directory, quarantine_bytes
from vakr.ingest.settings import SpoolSettings
from vakr.ingest.state import RunState, save_run_state

LOCK_FILE = "lock"


@dataclass(frozen=True, slots=True)
class OpenedDirectory:
    """A locked, recovered spool directory. Immutable.

    Attributes:
        lock_fd: Descriptor holding the lock; close it to release.
        incidents: The directory's incident log.
        recovery: What recovery found.
        quarantine_bytes: Bytes in ``quarantine/``.
    """

    lock_fd: int
    incidents: IncidentLog
    recovery: Recovery
    quarantine_bytes: int


def open_directory(
    directory: Path, settings: SpoolSettings, ops: FileOps, now_us: int
) -> OpenedDirectory:
    """Create, lock, and recover a collector's spool directory.

    Args:
        directory: The collector's spool directory.
        settings: Spool settings.
        ops: File operations.
        now_us: Current time in epoch microseconds.

    Returns:
        OpenedDirectory: The lock, incident log, and recovery result.

    Raises:
        SpoolLockedError: If another process holds the spool.
        OSError: If the directory cannot be prepared.
    """
    ensure_directory(directory, ops)
    lock_fd = lock_directory(directory)
    try:
        incidents = IncidentLog(directory, ops)
        recovery = recover(directory, settings, ops, incidents, now_us)
    except BaseException:
        os.close(lock_fd)
        raise
    return OpenedDirectory(lock_fd, incidents, recovery, quarantine_bytes(directory)[1])


def close_directory(
    directory: Path, lock_fd: int, last_seq: int | None, ops: FileOps, now_us: int
) -> None:
    """Record a clean stop (when there was one) and release the lock.

    Args:
        directory: The collector's spool directory.
        lock_fd: The lock descriptor from ``open_directory``.
        last_seq: Last sequence number written, or ``None`` if the writer
            did not stop cleanly; then the marker keeps saying *running*.
        ops: File operations.
        now_us: Current time in epoch microseconds.

    Returns:
        None

    Raises:
        OSError: If the marker cannot be written; the lock is released anyway.
    """
    try:
        if last_seq is not None:
            save_run_state(directory, RunState(True, last_seq, now_us), ops)
    finally:
        os.close(lock_fd)


def lock_directory(directory: Path) -> int:
    """Take the spool's exclusive lock without waiting.

    Args:
        directory: The collector's spool directory.

    Returns:
        int: The lock file's descriptor; closing it releases the lock.

    Raises:
        SpoolLockedError: If another process holds it.
    """
    fd = os.open(directory / LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o640)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise SpoolLockedError(f"spool {directory} is in use by another vakr process") from None
    return fd
