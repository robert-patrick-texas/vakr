"""Spool recovery at startup: keep what survived, account for what did not (SPOOL-T02, SPOOL-T08).

Recovery runs before the writer accepts anything:

1. Segments whose header is unreadable are moved to ``quarantine/``; one
   cut short while being created (no record could have been durable yet)
   is removed.
2. The last segment is scanned. Records that survived a process kill in
   the page cache are fsynced, so they count as committed from now on. A
   torn final write is truncated; damage before the end marks the segment
   damaged, keeping its bytes.
3. If the previous writer did not close cleanly, observations completed
   in its last fsync interval may be lost, so an ``unclean_stop`` incident
   estimates them from the records written in that final interval (E.8).
4. The next sequence number continues after everything on disk and
   everything acknowledged, so a sequence number is never reused for a
   readable record.

Every finding becomes a telemetry-loss incident (STORE-007). Lost ranges in
the *middle* of the spool, such as a deleted segment, are found by the
reader when it reaches them, because only it knows where each segment ends.

Dependencies:
    vakr.ingest: Framing, scanning, segment moves, state files, incidents.

Safety:
    Nothing readable is deleted: truncation removes only a torn final
    write, and damaged or unreadable files are renamed or quarantined.

Example:
    >>> next_seq_after(None, None, None)
    1
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from vakr.ingest.fileops import FileOps
from vakr.ingest.framing import SEGMENT_HEADER_BYTES, decode_segment_header
from vakr.ingest.incident_log import IncidentLog
from vakr.ingest.incidents import LossIncident, LossKind
from vakr.ingest.losses import bad_header_incident, estimate_records, unclean_incident
from vakr.ingest.scan import SegmentScan, TailVerdict, classify_tail
from vakr.ingest.scan import scan_segment as scan
from vakr.ingest.segments import (
    SegmentRef,
    ensure_directory,
    list_segments,
    mark_damaged,
    quarantine,
    segment_name,
)
from vakr.ingest.settings import SpoolSettings
from vakr.ingest.state import (
    ConsumerOffset,
    RunState,
    load_offset,
    load_run_state,
    save_run_state,
)

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Recovery:
    """What recovery found and the state the writer starts from. Immutable.

    Attributes:
        next_seq: Sequence number of the next record to write.
        segment_bytes: Bytes in segment files after recovery.
        unclean: Whether the previous writer stopped without closing.
        truncated_bytes: Bytes of torn final write removed.
        incidents: Loss incidents recorded by this recovery.
        offset: The consumer offset found, or ``None``.
    """

    next_seq: int
    segment_bytes: int
    unclean: bool
    truncated_bytes: int
    incidents: tuple[LossIncident, ...]
    offset: ConsumerOffset | None


def recover(
    directory: Path, settings: SpoolSettings, ops: FileOps, log: IncidentLog, now_us: int
) -> Recovery:
    """Recover a collector's spool directory and mark the new writer as running.

    Args:
        directory: The collector's spool directory (created if missing).
        settings: Spool settings (fsync interval and record count).
        ops: File operations.
        log: The directory's incident log.
        now_us: Current time in epoch microseconds.

    Returns:
        Recovery: Findings and the writer's starting point.

    Raises:
        OSError: If the directory cannot be read or repaired.
    """
    ensure_directory(directory, ops)
    previous, offset = load_run_state(directory), load_offset(directory)
    found: list[LossIncident] = []
    segments = _check_headers(list_segments(directory), ops, now_us, found)
    last_scan, truncated = None, 0
    if segments:
        last_scan, truncated = _recover_last(segments[-1], settings, ops, now_us, found)
    unclean = previous is not None and not previous.clean
    if unclean and previous is not None:
        found.append(unclean_incident(previous, last_scan, settings, now_us))
    last_on_disk = None if last_scan is None else _last_seq(segments[-1], last_scan)
    next_seq = next_seq_after(last_on_disk, offset, previous)
    recorded = tuple(i for i in found if log.record(i))
    save_run_state(directory, RunState(False, None, now_us), ops)
    size = sum(ref.path.stat().st_size for ref in list_segments(directory))
    return Recovery(next_seq, size, unclean, truncated, recorded, offset)


def next_seq_after(
    last_on_disk: int | None, offset: ConsumerOffset | None, previous: RunState | None
) -> int:
    """Choose the next sequence number so no readable record's number is reused.

    Args:
        last_on_disk: Last readable sequence number in the spool, if any.
        offset: The consumer offset, if any.
        previous: The previous run marker, if any.

    Returns:
        int: One more than the largest known sequence number, at least 1.

    Raises:
        None
    """
    known = [0, last_on_disk, None if offset is None else offset.seq]
    known.append(None if previous is None else previous.last_seq)
    return max(n for n in known if n is not None) + 1


def _last_seq(ref: SegmentRef, result: SegmentScan) -> int:
    """Return the last readable sequence number of a segment, or the one before it.

    Args:
        ref: The segment.
        result: Its scan.

    Returns:
        int: ``last_seq``, or ``first_seq - 1`` for a segment with no records.

    Raises:
        None
    """
    return result.last_seq if result.last_seq is not None else ref.first_seq - 1


def _check_headers(
    segments: list[SegmentRef], ops: FileOps, now_us: int, found: list[LossIncident]
) -> list[SegmentRef]:
    """Remove half-created segments and quarantine those with unreadable headers.

    Args:
        segments: Segments in sequence order.
        ops: File operations.
        now_us: Detection time.
        found: Incidents are appended here.

    Returns:
        list[SegmentRef]: The segments that remain readable.

    Raises:
        OSError: If a file cannot be read, removed, or moved.
    """
    kept: list[SegmentRef] = []
    for index, ref in enumerate(segments):
        size = ref.path.stat().st_size
        with ref.path.open("rb") as f:
            header = decode_segment_header(f.read(SEGMENT_HEADER_BYTES))
        if header is not None and header.first_seq == ref.first_seq:
            kept.append(ref)
            continue
        if index == len(segments) - 1 and size <= SEGMENT_HEADER_BYTES:
            # Creation fsyncs the header before any record is written, so a
            # last file this short never held a durable record.
            ref.path.unlink()
            ops.fsync_dir(ref.path.parent)
            continue
        following = segments[index + 1].first_seq if index + 1 < len(segments) else None
        quarantine(ref.path, ops)
        found.append(bad_header_incident(ref, size, following, now_us))
    return kept


def _recover_last(
    ref: SegmentRef, settings: SpoolSettings, ops: FileOps, now_us: int,
    found: list[LossIncident],
) -> tuple[SegmentScan, int]:  # fmt: skip
    """Scan the newest segment, make its records durable, and repair a bad end.

    Args:
        ref: The newest segment.
        settings: For the unclean-stop window size.
        ops: File operations.
        now_us: Detection time.
        found: Incidents are appended here.

    Returns:
        tuple[SegmentScan, int]: The scan and the bytes truncated.

    Raises:
        OSError: If the segment cannot be read or repaired.
    """
    result = scan(ref.path, keep_tail=settings.fsync_records)
    truncated = 0
    if result.problem is not None and not ref.damaged:
        lost = result.size - result.valid_end
        expected = _last_seq(ref, result) + 1
        avg = (result.valid_end - SEGMENT_HEADER_BYTES) / result.records if result.records else 0
        start = result.tail[-1].header.append_us if result.tail else None
        verdict = classify_tail(ref.path, result.valid_end, result.problem)
        if verdict is TailVerdict.TORN:
            os.truncate(ref.path, result.valid_end)
            truncated = lost
            kind, detail = LossKind.TORN_TAIL, f"torn final write of {lost} bytes truncated"
        else:
            ref = mark_damaged(ref, ops)
            kind = LossKind.DAMAGED_SEGMENT
            detail = f"checksum failure; {lost} bytes after the last good record kept"
        found.append(LossIncident.new(
            kind, now_us, first_seq=expected, start_us=start, segment=segment_name(ref.first_seq),
            estimated=estimate_records(lost, avg), detail=detail,
        ))  # fmt: skip
    _sync_file(ref.path, ops)
    if result.records == 0 and result.problem is None:
        ref.path.unlink()
        ops.fsync_dir(ref.path.parent)
    return result, truncated


def _sync_file(path: Path, ops: FileOps) -> None:
    """Fsync a file by path, so records left in the page cache become durable.

    Args:
        path: The file.
        ops: File operations.

    Returns:
        None

    Raises:
        OSError: If it cannot be opened or synced.
    """
    fd = os.open(path, os.O_RDONLY)
    try:
        ops.fsync(fd)
    finally:
        os.close(fd)
