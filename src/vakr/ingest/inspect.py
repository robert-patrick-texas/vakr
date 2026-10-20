"""Read-only inspection of a spool directory from another process, for ``vakr check health``.

The health check runs every minute from CheckMK or Nagios (OPS-001) and must
never disturb the collector: it takes no lock and writes nothing. It
measures the directory itself (segments, quarantine, free space), reads the
consumer offset and the incident log, and finds the oldest unacknowledged
record by walking frame headers, so its numbers are right even when the
collector is down and ``status.json`` is stale.

Dependencies:
    os: Filesystem free space (``statvfs``).
    vakr.ingest: Framing, segments, state files, incidents, and status.

Safety:
    Read-only. A file that disappears mid-inspection (the ingester deleted
    it) is skipped, not reported as an error.

Example:
    >>> from pathlib import Path
    >>> oldest_unacked_us(Path("/nonexistent"), 0) is None
    True
"""

import os
from dataclasses import dataclass
from pathlib import Path

from vakr.ingest.capacity import CapacityState, capacity_state
from vakr.ingest.framing import (
    FRAME_HEADER_BYTES,
    SEGMENT_HEADER_BYTES,
    decode_frame_header,
    plausible_length,
)
from vakr.ingest.incidents import LossIncident, read_incidents
from vakr.ingest.segments import list_segments, quarantine_bytes
from vakr.ingest.settings import SpoolSettings
from vakr.ingest.state import load_offset, load_run_state
from vakr.ingest.status import SpoolHealth, read_status

INCIDENT_WINDOW_US = 24 * 3600 * 1_000_000
"""Incidents this recent keep the health check at WARNING (E.24)."""


@dataclass(frozen=True, slots=True)
class SpoolInspection:
    """What one collector's spool directory holds. Immutable.

    Attributes:
        directory: The directory.
        segments: Segment files.
        total_bytes: Bytes in segments and quarantine.
        used_pct: ``total_bytes`` as a percentage of the size limit, capped at 100.
        state: Capacity state.
        quarantined_files: Files in ``quarantine/``.
        acked_seq: Consumer offset, or ``None`` if none recorded.
        oldest_unacked_us: Append time of the oldest unacknowledged record, or ``None``.
        recent_incidents: Loss incidents within ``INCIDENT_WINDOW_US``.
        status: The ingester's last ``status.json``, or ``None``.
        clean_stop: Whether the writer last stopped cleanly; ``None`` without a marker.
        fs_free_bytes: Free bytes for unprivileged users on its filesystem, or ``None``.
    """

    directory: Path
    segments: int
    total_bytes: int
    used_pct: float
    state: CapacityState
    quarantined_files: int
    acked_seq: int | None
    oldest_unacked_us: int | None
    recent_incidents: tuple[LossIncident, ...]
    status: SpoolHealth | None
    clean_stop: bool | None
    fs_free_bytes: int | None


def inspect_spool(directory: Path, settings: SpoolSettings, now_us: int) -> SpoolInspection:
    """Inspect one collector's spool directory without changing it.

    Args:
        directory: The collector's spool directory.
        settings: Size limit and thresholds.
        now_us: Current time, for the incident window.

    Returns:
        SpoolInspection: The findings.

    Raises:
        OSError: If the directory cannot be listed.
    """
    sizes = []
    for ref in list_segments(directory):
        try:
            sizes.append(ref.path.stat().st_size)
        except FileNotFoundError:
            continue  # deleted by the ingester while we looked
    files, quarantined = quarantine_bytes(directory)
    total = sum(sizes) + quarantined
    pct = min(100.0, 100.0 * total / settings.max_bytes)
    offset, run = load_offset(directory), load_run_state(directory)
    acked = None if offset is None else offset.seq
    return SpoolInspection(
        directory, len(sizes), total, pct, capacity_state(pct, settings), files, acked,
        oldest_unacked_us(directory, acked or 0),
        tuple(read_incidents(directory, since_us=now_us - INCIDENT_WINDOW_US)),
        read_status(directory), None if run is None else run.clean, _free_bytes(directory),
    )  # fmt: skip


def oldest_unacked_us(directory: Path, acked: int) -> int | None:
    """Find the append time of the first record after ``acked`` by walking frame headers.

    Args:
        directory: The collector's spool directory.
        acked: The consumer offset (0 if none).

    Returns:
        int | None: Epoch microseconds, or ``None`` if every record is acknowledged.

    Raises:
        None
    """
    segments = list_segments(directory)
    start = [s for s in segments if s.first_seq <= acked + 1]
    for ref in segments[len(start) - 1 :] if start else segments:
        try:
            found = _first_after(ref.path, acked)
        except OSError:
            continue
        if found is not None:
            return found
    return None


def _first_after(path: Path, acked: int) -> int | None:
    """Walk one segment's frame headers to the first record after ``acked``.

    Args:
        path: The segment.
        acked: The consumer offset.

    Returns:
        int | None: That record's append time, or ``None`` if the segment has none.

    Raises:
        OSError: If the file cannot be read.
    """
    with path.open("rb") as f:
        f.seek(SEGMENT_HEADER_BYTES)
        while len(head := f.read(FRAME_HEADER_BYTES)) == FRAME_HEADER_BYTES:
            header = decode_frame_header(head)
            if not plausible_length(header.length):
                return None
            if header.seq > acked:
                return header.append_us
            f.seek(header.length, os.SEEK_CUR)
    return None


def _free_bytes(directory: Path) -> int | None:
    """Return the free space available to unprivileged writers.

    Args:
        directory: Any path on the filesystem.

    Returns:
        int | None: Bytes, or ``None`` if unknown.

    Raises:
        None
    """
    try:
        stats = os.statvfs(directory)
    except OSError:
        return None
    return stats.f_bavail * stats.f_frsize
