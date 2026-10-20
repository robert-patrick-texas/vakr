"""Crash-safe writes of operator configuration, with backups and rollback.

A configuration upgrade runs unattended from self-update on production
hosts. A power cut or full disk halfway through must never leave a truncated
``config.toml`` that stops collection. Every write therefore goes to a
temporary file in the same directory, is fsynced, and is renamed over the
original; a timestamped backup is written first; and if any file of a
multi-file change fails, files already replaced are restored to their
original bytes (CFGM-002, CFGM-003).

Dependencies:
    os, tempfile: fsync, atomic rename, and same-directory temporary files.

Safety:
    Replaced files keep their permission bits and, when the process may set
    it, their owner, so ``root:vakr 0640`` files stay readable by services.

Example:
    >>> stamp(datetime(2026, 10, 20, 1, 2, 3, tzinfo=UTC))
    '20261020T010203Z'
"""

import contextlib
import os
import stat
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

NEW_FILE_MODE = 0o644
"""Mode for newly created configuration files, which never hold secrets (SEC-001)."""


def stamp(now: datetime | None = None) -> str:
    """Format a UTC timestamp for backup directory names.

    Args:
        now: Moment to format; ``None`` means the current time.

    Returns:
        str: ``YYYYMMDDTHHMMSSZ``.

    Raises:
        None
    """
    return (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def atomic_write(path: Path, data: bytes, like: Path | None = None) -> None:
    """Replace ``path`` with ``data`` so readers see the old or new file, never a mix.

    Args:
        path: Destination file.
        data: Complete new content.
        like: File whose mode and owner to copy; defaults to ``path`` itself
            when it exists.

    Returns:
        None

    Raises:
        OSError: If the temporary file cannot be written or renamed.
    """
    template = like if like is not None and like.exists() else path
    info = template.stat() if template.exists() else None
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temp_path = Path(temporary)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temp_path.chmod(stat.S_IMODE(info.st_mode) if info else NEW_FILE_MODE)
        if info and (info.st_uid, info.st_gid) != (os.geteuid(), os.getegid()):
            # Only root can give a file away; elsewhere keeping the mode is the best we can do.
            with contextlib.suppress(PermissionError):
                os.chown(temp_path, info.st_uid, info.st_gid)
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)
    _fsync_directory(path.parent)


def _fsync_directory(directory: Path) -> None:
    """Flush a directory entry so a completed rename survives a crash.

    Args:
        directory: Directory containing the renamed file.

    Returns:
        None

    Raises:
        OSError: If the directory cannot be opened.
    """
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_backup(
    base: Path, originals: Sequence[tuple[Path, bytes]], root: Path, now: datetime | None = None
) -> Path:
    """Copy original file contents into a new timestamped backup directory.

    Args:
        base: Directory the backed-up paths are relative to (the config dir).
        originals: ``(path, original bytes)`` pairs.
        root: Directory under which ``<timestamp>/`` is created.
        now: Backup time; ``None`` means now.

    Returns:
        Path: The new backup directory.

    Raises:
        OSError: If the backup cannot be written; operator files are untouched.
    """
    name = stamp(now)
    target, suffix = root / name, 1
    while target.exists():
        target, suffix = root / f"{name}-{suffix}", suffix + 1
    target.mkdir(parents=True, mode=0o750)
    for path, raw in originals:
        relative = path.relative_to(base) if path.is_relative_to(base) else Path(path.name)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(destination, raw, like=path)
    return target


def commit(
    base: Path,
    changes: Sequence[tuple[Path, bytes | None, bytes]],
    backup_root: Path,
    now: datetime | None = None,
) -> Path:
    """Back up, then atomically write, several files as one change.

    Args:
        base: Configuration directory, for backup-relative paths.
        changes: ``(path, original bytes or None for a new file, new bytes)``.
        backup_root: Directory receiving the timestamped backup.
        now: Backup time; ``None`` means now.

    Returns:
        Path: The backup directory.

    Raises:
        OSError: If a write fails; files already replaced are restored to
            their original bytes (new files are removed) before re-raising.
    """
    originals = [(path, before) for path, before, _ in changes if before is not None]
    backup = write_backup(base, originals, backup_root, now)
    done: list[tuple[Path, bytes | None]] = []
    try:
        for path, before, after in changes:
            atomic_write(path, after)
            done.append((path, before))
    except OSError:
        for path, before in reversed(done):
            if before is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, before)
        raise
    return backup
