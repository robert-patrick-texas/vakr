"""Pre-update backups of operator configuration and SQLite databases.

Before a new package runs its migrations, self-update archives the
configuration directory and the state directory's databases (every
``*.db``, or only the names the project lists), so a failed update or a
manual rollback can put both back exactly (UPD-002, UPD-005). A project
leaves out a database that is too large to copy before every update.
Databases are copied with SQLite's online backup API, which gives a
consistent snapshot while services keep writing.

Dependencies:
    tarfile, sqlite3, shutil, tempfile: Standard library only.

Safety:
    Archives are written to a ``.part`` file and renamed when complete, at
    mode 0600 because configuration includes the secrets directory.
    Ownership and modes are recorded so a restore run as root puts back the
    original owners. Restores extract with the ``tar`` filter (no absolute
    paths, no escaping links), swap the configuration directory in with
    renames, replace databases atomically, and drop stale WAL files.

Example:
    >>> archive = create_backup(config_dir, state_dir, backups, now)  # doctest: +SKIP
    >>> archive.name  # doctest: +SKIP
    'pre-update-20261020T031500Z.tar.gz'
"""

import contextlib
import os
import shutil
import sqlite3
import stat
import tarfile
import tempfile
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

ARCHIVE_MODE = 0o600
BACKUP_PREFIX = "pre-update"
SIDECARS = ("-wal", "-shm", "-journal")


def create_backup(
    config_dir: Path,
    db_dir: Path,
    backups_dir: Path,
    now: datetime,
    prefix: str = BACKUP_PREFIX,
    names: Sequence[str] = (),
) -> Path:
    """Archive ``config_dir`` and the databases in ``db_dir`` into ``backups_dir``.

    Args:
        config_dir: Operator configuration directory.
        db_dir: Directory whose databases are snapshotted.
        backups_dir: Destination directory; created at mode 0700.
        now: Timestamp for the archive name (UTC).
        prefix: Archive name prefix.
        names: Database file names to snapshot, skipping absent ones;
            empty means every ``*.db`` in ``db_dir``.

    Returns:
        Path: ``<prefix>-<YYYYmmddTHHMMSSZ>.tar.gz``.

    Raises:
        OSError: When reading sources or writing the archive fails.
    """
    backups_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    archive = _unique(backups_dir / f"{prefix}-{now:%Y%m%dT%H%M%SZ}.tar.gz")
    part = archive.with_name(f"{archive.name}.part")
    with tempfile.TemporaryDirectory(dir=backups_dir) as scratch, tarfile.open(part, "w:gz") as tar:
        if config_dir.is_dir():
            tar.add(config_dir, arcname="config")
        for db in _databases(db_dir, names):
            snapshot = Path(scratch) / db.name
            _snapshot(db, snapshot)
            tar.add(snapshot, arcname=f"state/{db.name}", filter=_owned_like(db))
    part.chmod(ARCHIVE_MODE)
    part.replace(archive)
    return archive


def _databases(db_dir: Path, names: Sequence[str]) -> list[Path]:
    """Return the databases a backup includes.

    Args:
        db_dir: State directory.
        names: Listed file names, or empty for every ``*.db``.

    Returns:
        list[Path]: Existing database files, sorted.

    Raises:
        None
    """
    if not db_dir.is_dir():
        return []
    if not names:
        return sorted(db_dir.glob("*.db"))
    return sorted(db_dir / name for name in names if (db_dir / name).is_file())


def list_backups(backups_dir: Path, prefix: str = BACKUP_PREFIX) -> list[Path]:
    """List complete archives with ``prefix``, oldest first.

    Args:
        backups_dir: Backup directory.
        prefix: Archive name prefix.

    Returns:
        list[Path]: Archives sorted by modification time, then name.

    Raises:
        None
    """
    if not backups_dir.is_dir():
        return []
    # Modification time orders same-second archives that ``_unique`` numbered.
    archives = backups_dir.glob(f"{prefix}-*.tar.gz")
    return sorted(archives, key=lambda p: (p.stat().st_mtime_ns, p.name))


def prune_backups(backups_dir: Path, keep: int = 5, prefix: str = BACKUP_PREFIX) -> list[Path]:
    """Delete all but the newest ``keep`` archives with ``prefix``.

    Args:
        backups_dir: Backup directory.
        keep: Archives to retain.
        prefix: Archive name prefix.

    Returns:
        list[Path]: Removed archives.

    Raises:
        OSError: When an archive cannot be removed.
    """
    archives = list_backups(backups_dir, prefix)
    doomed = archives[: max(len(archives) - keep, 0)]
    for path in doomed:
        path.unlink()
    return doomed


def restore_backup(archive: Path, config_dir: Path, db_dir: Path) -> None:
    """Put configuration and databases back exactly as archived.

    Args:
        archive: Archive from ``create_backup``.
        config_dir: Configuration directory to replace.
        db_dir: Directory receiving the archived ``*.db`` files.

    Returns:
        None

    Raises:
        OSError: When extraction or replacement fails.
        tarfile.TarError: When the archive is corrupt or unsafe.
    """
    config_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=config_dir.parent, prefix=".restore-"))
    try:
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(staging, filter="tar")
        if (staging / "config").is_dir():
            _swap_in(staging / "config", config_dir)
        for db in sorted((staging / "state").glob("*.db")):
            _replace_db(db, db_dir / db.name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _snapshot(db: Path, dest: Path) -> None:
    """Copy a SQLite database consistently, or byte-for-byte if not SQLite.

    Args:
        db: Live database file.
        dest: Snapshot path.

    Returns:
        None

    Raises:
        OSError: When the fallback copy fails.
    """
    try:
        source = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
        target = sqlite3.connect(dest)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
    except sqlite3.DatabaseError:
        # A stray non-SQLite *.db file must not block updates; keep its bytes.
        dest.unlink(missing_ok=True)
        shutil.copyfile(db, dest)


def _owned_like(original: Path) -> Callable[[tarfile.TarInfo], tarfile.TarInfo]:
    """Build a tar filter that records ``original``'s owner and mode.

    Args:
        original: File whose ownership the archived snapshot should carry.

    Returns:
        Callable: Filter taking and returning a ``TarInfo``.

    Raises:
        OSError: When ``original`` cannot be stat'ed.
    """
    info = original.stat()

    def apply(member: tarfile.TarInfo) -> tarfile.TarInfo:
        """Copy owner, group, and permission bits onto ``member``.

        Args:
            member: Archive entry being added.

        Returns:
            tarfile.TarInfo: The adjusted entry.

        Raises:
            None
        """
        member.uid, member.gid = info.st_uid, info.st_gid
        member.uname = member.gname = ""
        member.mode = stat.S_IMODE(info.st_mode)
        return member

    return apply


def _swap_in(restored: Path, config_dir: Path) -> None:
    """Replace ``config_dir`` with ``restored`` using renames.

    Args:
        restored: Extracted configuration directory (same filesystem).
        config_dir: Live configuration directory.

    Returns:
        None

    Raises:
        OSError: When a rename fails.
    """
    aside = config_dir.with_name(f".{config_dir.name}.pre-restore")
    shutil.rmtree(aside, ignore_errors=True)
    if config_dir.exists():
        config_dir.rename(aside)
    restored.rename(config_dir)
    shutil.rmtree(aside, ignore_errors=True)


def _replace_db(source: Path, target: Path) -> None:
    """Atomically replace ``target`` with ``source`` and drop stale sidecars.

    Args:
        source: Restored database in the staging area.
        target: Live database path.

    Returns:
        None

    Raises:
        OSError: When copying or renaming fails.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.restore")
    shutil.copy2(source, temp)
    info = source.stat()
    # Unprivileged test runs cannot chown and keep the caller's ownership.
    with contextlib.suppress(PermissionError):
        os.chown(temp, info.st_uid, info.st_gid)
    temp.replace(target)
    # A WAL left by the newer version would be replayed onto the restored file.
    for suffix in SIDECARS:
        target.with_name(f"{target.name}{suffix}").unlink(missing_ok=True)


def _unique(path: Path) -> Path:
    """Return ``path`` or a numbered variant that does not exist yet.

    Args:
        path: Preferred archive path.

    Returns:
        Path: A free path in the same directory.

    Raises:
        None
    """
    candidate, counter = path, 1
    while candidate.exists():
        candidate = path.with_name(path.name.replace(".tar.gz", f"-{counter}.tar.gz"))
        counter += 1
    return candidate
