"""Self-update's root-only storage: directories, trusted archives, older files (UPD-005).

Self-update runs as root and, on rollback, restores a pre-update archive
into the configuration directory and the metadata database. So the
archives, and the state record that names versions and failures, must
live where only root can write: a service account that could plant an
archive, or a newer-named or newer-dated one, would otherwise choose what
root restores. Before this module, they lived in the service's own state
directory (``/var/lib/vakr``, owned by ``vakr``).

* ``check_private_dir`` refuses a directory that someone else owns or that
  group or others may write; ``ensure_private_dir`` also creates it.
* ``trusted_archives`` keeps only regular files owned by the caller, with
  one link, and not writable by group or others.
* ``adopt_legacy`` moves an older release's archives and state from the
  old location, once, under the update lock. Only files that pass the same
  test are moved, read through a descriptor opened without following
  links, so a file swapped in by the directory's owner is left behind.

Dependencies:
    os, shutil, stat: File checks and copies.
    vakr.lifecycle.update.model: ``Layout`` and ``Toolkit``.

Safety:
    Never follows a symbolic link and never deletes a file it did not
    first copy. Portable: imports nothing from vakr outside
    ``vakr.lifecycle``.

Example:
    >>> import tempfile, pathlib
    >>> d = pathlib.Path(tempfile.mkdtemp()) / "updates"
    >>> ensure_private_dir(d); oct(d.stat().st_mode & 0o777)
    '0o700'
"""

import os
import shutil
import stat
from collections.abc import Iterable
from pathlib import Path

from vakr.lifecycle.update.model import Layout, Toolkit

ADOPTED_PATTERNS = ("pre-update-*.tar.gz", "pre-rollback-*.tar.gz")
"""The archive names an older release wrote, and the only ones adopted."""
_FOREIGN_WRITE = stat.S_IWGRP | stat.S_IWOTH


class PrivateStorageError(OSError):
    """A self-update directory is not root's alone; the message says how to fix it."""


def ensure_private_dir(path: Path, mode: int = 0o700) -> None:
    """Create ``path`` for the caller if missing, then check that it is the caller's alone.

    Args:
        path: The directory.
        mode: Mode for a new directory; an existing one keeps its own.

    Returns:
        None

    Raises:
        PrivateStorageError: As ``check_private_dir``.
    """
    missing: list[Path] = []
    for candidate in (path, *path.parents):
        if os.path.lexists(candidate):
            break
        missing.append(candidate)
    # Each one is made and then chmodded: mkdir's mode is masked by the umask, and under
    # 002 a parent left to ``parents=True`` would be group-writable.
    for directory in reversed(missing):
        directory.mkdir(mode=0o755)
        directory.chmod(mode if directory == path else 0o755)
    check_private_dir(path)


def check_private_dir(path: Path) -> None:
    """Check that ``path`` is a real directory the caller alone may write.

    Args:
        path: The directory, which must exist.

    Returns:
        None

    Raises:
        PrivateStorageError: If it is not a real directory, belongs to someone
            else, or is writable by group or others.
    """
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise PrivateStorageError(f"{path} is not a directory; self-update keeps its backups "
                                  "and state only in a real directory owned by root")  # fmt: skip
    if info.st_uid != os.geteuid() or info.st_mode & _FOREIGN_WRITE:
        raise PrivateStorageError(
            f"{path} must belong to uid {os.geteuid()} and be writable by it alone "
            f"(it is uid {info.st_uid}, mode {stat.S_IMODE(info.st_mode):04o}); "
            f"fix it with: sudo chown root:root {path} && sudo chmod go-w {path}"
        )


def _trusted(info: os.stat_result) -> bool:
    """Tell whether a file is the caller's alone: regular, one link, no foreign write.

    Args:
        info: The file's ``lstat`` or ``fstat``.

    Returns:
        bool: True when root (the caller) made it and nobody else can change it.

    Raises:
        None
    """
    mine = info.st_uid == os.geteuid() and info.st_nlink == 1
    return stat.S_ISREG(info.st_mode) and mine and not info.st_mode & _FOREIGN_WRITE


def trusted_archives(archives: Iterable[Path]) -> list[Path]:
    """Keep only the archives rollback may restore, in the order given.

    Args:
        archives: Candidate archives, oldest first.

    Returns:
        list[Path]: Those that pass the ownership and link test.

    Raises:
        None
    """
    kept = []
    for archive in archives:
        try:
            if _trusted(archive.lstat()):
                kept.append(archive)
        except OSError:
            continue  # vanished since it was listed
    return kept


def _move_trusted(old: Path, new: Path) -> bool:
    """Copy ``old`` to ``new`` if it passes the test, keeping its times, then remove ``old``.

    Args:
        old: The file in the old location.
        new: Where it goes; nothing is overwritten.

    Returns:
        bool: True when it was moved.

    Raises:
        OSError: If the new location cannot be written.
    """
    try:
        fd = os.open(old, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return False  # gone, a symbolic link, or unreadable: not one of ours
    with os.fdopen(fd, "rb") as src:
        info = os.fstat(src.fileno())
        if not _trusted(info) or os.path.lexists(new):
            return False
        part = new.with_name(f".{new.name}.part")
        out = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(out, "wb") as dst:
            shutil.copyfileobj(src, dst)
            dst.flush()
            os.fsync(dst.fileno())
        part.chmod(stat.S_IMODE(info.st_mode))
        os.utime(part, ns=(info.st_atime_ns, info.st_mtime_ns))  # rollback orders by mtime
        part.replace(new)
    old.unlink(missing_ok=True)
    return True


def adopt_legacy(layout: Layout) -> list[str]:
    """Move an older release's archives and state into the private directories.

    Args:
        layout: The updater's layout, with its legacy locations.

    Returns:
        list[str]: Names of the files moved.

    Raises:
        OSError: If the private directories cannot be written.
    """
    moved: list[str] = []
    old_dir = layout.legacy_backups_dir
    if old_dir is not None and old_dir.is_dir() and not old_dir.is_symlink():
        for pattern in ADOPTED_PATTERNS:
            for old in sorted(old_dir.glob(pattern)):
                ensure_private_dir(layout.backups_dir)
                if _move_trusted(old, layout.backups_dir / old.name):
                    moved.append(old.name)
    old_state = layout.legacy_state_file
    if old_state is not None and old_state.is_file() and not layout.state_file.exists():
        ensure_private_dir(layout.state_file.parent, mode=0o755)  # health checks read it
        if _move_trusted(old_state, layout.state_file):
            moved.append(old_state.name)
    return moved


def prepare(kit: Toolkit) -> None:
    """Make the private directories safe and adopt older files; call under the update lock.

    Args:
        kit: The updater's collaborators.

    Returns:
        None

    Raises:
        PrivateStorageError: If a directory is not root's alone.
        OSError: If a directory cannot be created or written.
    """
    layout = kit.layout
    # The state directory is made here with an explicit mode: left to the first write, a
    # umask of 002 would make it group-writable and every later update would refuse it.
    # The backups directory is made by the first backup, at 0700; until then nothing exists
    # to trust. An existing one must already be root's alone.
    ensure_private_dir(layout.state_file.parent, mode=0o755)  # health checks read the state
    if os.path.lexists(layout.backups_dir):
        check_private_dir(layout.backups_dir)
    moved = adopt_legacy(layout)
    if moved:
        kit.emit("update.storage.adopted", f"moved {len(moved)} file(s) from "
                 f"{layout.legacy_backups_dir} into {layout.state_file.parent}")  # fmt: skip
