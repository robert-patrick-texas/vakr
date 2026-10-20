"""Segment files on disk: names, listing, creation, and the moves that keep data visible.

A collector's spool directory holds ``seg-<first seq>.spool`` files, at most
one of them being appended to. A segment whose middle fails its checksum is
renamed ``seg-<first seq>.damaged``: its records before the damage are still
ingested, and once they are stored the file moves to ``quarantine/`` instead
of being deleted, so nothing that might matter to a support case disappears
silently (STORE-006). Every create, rename, and removal fsyncs the directory
too; without that a power cut can forget a segment the data fsync already
covered.

Dependencies:
    os, pathlib: File descriptors and directory entries.
    vakr.ingest.fileops: The fsync and write calls tests substitute.
    vakr.ingest.framing: The segment header.

Safety:
    Segment files are created with ``O_EXCL`` and mode 0640, so a stale file
    is never reused and observation data is not world-readable.

Example:
    >>> segment_name(42)
    'seg-00000000000000000042.spool'
    >>> parse_segment_name("seg-00000000000000000042.damaged")
    (42, True)
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path

from vakr.ingest.fileops import FileOps, write_all
from vakr.ingest.framing import encode_segment_header

SEGMENT_SUFFIX = ".spool"
DAMAGED_SUFFIX = ".damaged"
QUARANTINE_DIR = "quarantine"
_NAME = re.compile(r"seg-(\d{20})(\.spool|\.damaged)")
_FILE_MODE = 0o640
_DIR_MODE = 0o750


@dataclass(frozen=True, slots=True)
class SegmentRef:
    """One segment file in a spool directory. Immutable; the file may change.

    Attributes:
        path: The file.
        first_seq: Sequence number in its name, equal to its header's.
        damaged: Whether the file was found damaged mid-segment.
    """

    path: Path
    first_seq: int
    damaged: bool = False

    @property
    def name(self) -> str:
        """The file name, for operator messages and incident records.

        Args:
            None

        Returns:
            str: For example ``seg-00000000000000000001.spool``.

        Raises:
            None
        """
        return self.path.name


def segment_name(first_seq: int, *, damaged: bool = False) -> str:
    """Return the file name of the segment that starts at ``first_seq``.

    Args:
        first_seq: Sequence number of its first record.
        damaged: Whether to use the damaged suffix.

    Returns:
        str: Twenty zero-padded digits, so names sort in sequence order.

    Raises:
        None
    """
    return f"seg-{first_seq:020d}{DAMAGED_SUFFIX if damaged else SEGMENT_SUFFIX}"


def parse_segment_name(name: str) -> tuple[int, bool] | None:
    """Parse a segment file name.

    Args:
        name: A file name.

    Returns:
        tuple[int, bool] | None: First sequence number and damaged flag, or
        ``None`` for any other file.

    Raises:
        None
    """
    match = _NAME.fullmatch(name)
    return None if match is None else (int(match[1]), match[2] == DAMAGED_SUFFIX)


def list_segments(directory: Path) -> list[SegmentRef]:
    """List a spool directory's segments in sequence order.

    Args:
        directory: The collector's spool directory.

    Returns:
        list[SegmentRef]: Segments, oldest first; empty if the directory is missing.

    Raises:
        OSError: If the directory exists but cannot be read.
    """
    if not directory.is_dir():
        return []
    found = []
    for entry in directory.iterdir():
        parsed = parse_segment_name(entry.name)
        if parsed is not None and entry.is_file():
            found.append(SegmentRef(entry, parsed[0], parsed[1]))
    return sorted(found, key=lambda ref: ref.first_seq)


def ensure_directory(path: Path, ops: FileOps) -> None:
    """Create a spool directory (mode 0750) and make its entry durable.

    Args:
        path: Directory to create; parents are created as needed.
        ops: File operations.

    Returns:
        None

    Raises:
        OSError: If it cannot be created.
    """
    if path.is_dir():
        return
    path.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
    ops.fsync_dir(path.parent)


def create_segment(directory: Path, first_seq: int, created_us: int, ops: FileOps) -> int:
    """Create a segment, write and fsync its header, and fsync the directory.

    Args:
        directory: The spool directory.
        first_seq: Sequence number of its first record.
        created_us: Creation time in epoch microseconds.
        ops: File operations.

    Returns:
        int: An append-only file descriptor positioned after the header.

    Raises:
        OSError: If the file exists already or cannot be written.
    """
    path = directory / segment_name(first_seq)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND, _FILE_MODE)
    try:
        write_all(fd, encode_segment_header(first_seq, created_us), ops)
        ops.fsync(fd)
        ops.fsync_dir(directory)
    except OSError:
        os.close(fd)
        path.unlink(missing_ok=True)
        raise
    return fd


def mark_damaged(ref: SegmentRef, ops: FileOps) -> SegmentRef:
    """Rename a segment to its damaged name so the damage is recorded only once.

    Args:
        ref: A segment that failed a checksum mid-file.
        ops: File operations.

    Returns:
        SegmentRef: The renamed segment.

    Raises:
        OSError: If the rename fails.
    """
    if ref.damaged:
        return ref
    target = ref.path.with_name(segment_name(ref.first_seq, damaged=True))
    ref.path.rename(target)
    ops.fsync_dir(ref.path.parent)
    return SegmentRef(target, ref.first_seq, damaged=True)


def quarantine(path: Path, ops: FileOps) -> Path:
    """Move a file into the spool's ``quarantine/`` directory, keeping it for support.

    Args:
        path: A file in a spool directory.
        ops: File operations.

    Returns:
        Path: Its new location.

    Raises:
        OSError: If the move fails.
    """
    target_dir = path.parent / QUARANTINE_DIR
    ensure_directory(target_dir, ops)
    target = target_dir / path.name
    path.rename(target)
    ops.fsync_dir(target_dir)
    ops.fsync_dir(path.parent)
    return target


def remove_segment(ref: SegmentRef, ops: FileOps) -> int:
    """Delete a fully acknowledged segment, or quarantine it if it was damaged.

    Args:
        ref: The segment.
        ops: File operations.

    Returns:
        int: Bytes removed from the spool's segment total.

    Raises:
        OSError: If the file cannot be removed or moved.
    """
    size = ref.path.stat().st_size
    if ref.damaged:
        quarantine(ref.path, ops)
    else:
        ref.path.unlink()
        ops.fsync_dir(ref.path.parent)
    return size


def quarantine_bytes(directory: Path) -> tuple[int, int]:
    """Measure the quarantine directory.

    Args:
        directory: The collector's spool directory.

    Returns:
        tuple[int, int]: File count and total bytes.

    Raises:
        None
    """
    target = directory / QUARANTINE_DIR
    try:
        sizes = [p.stat().st_size for p in target.iterdir() if p.is_file()]
    except OSError:
        return 0, 0
    return len(sizes), sum(sizes)
