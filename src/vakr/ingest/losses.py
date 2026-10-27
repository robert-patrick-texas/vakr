"""How each kind of telemetry loss is described: range, classes, and count (STORE-007, SPOOL-T08).

An incident is only useful if it says *which* observations are gone. Where
the spool knows exactly (a refused append, a missing segment between two
that exist), the count is exact. Where it cannot know (records the process
never wrote before it died, bytes after a checksum failure), it estimates
from the best evidence on disk and says so.

Dependencies:
    json: Reads ``observation_type`` from envelopes without full decoding.
    vakr.domain: Observation types and timestamps.
    vakr.ingest: Frames, segments, incidents, and settings.

Safety:
    Builders read counts, times, and types; payloads are not copied.

Example:
    >>> estimate_records(0, 0), estimate_records(10, 1000.0)
    (0, 1)
"""

import json
from collections.abc import Sequence

from vakr.domain.observations import Observation, observation_type
from vakr.domain.timestamps import to_epoch_us
from vakr.ingest.errors import SpoolFullError
from vakr.ingest.framing import FRAME_HEADER_BYTES, MAX_RECORD_BYTES
from vakr.ingest.incidents import LossIncident, LossKind
from vakr.ingest.scan import Frame, SegmentScan
from vakr.ingest.segments import SegmentRef, segment_name
from vakr.ingest.settings import SpoolSettings
from vakr.ingest.state import RunState
from vakr.ports.clock import Clock

TYPICAL_RECORD_BYTES = FRAME_HEADER_BYTES + 1024
"""Frame size assumed when a spool has no records to average (E.24): about 1 KB envelopes."""


def estimate_records(byte_count: int, average: float) -> int:
    """Estimate how many records a run of lost bytes held, for a loss incident.

    Args:
        byte_count: Bytes that could not be read.
        average: Average frame size seen in the spool, or 0 if unknown.

    Returns:
        int: At least 1 when any bytes were lost, else 0.

    Raises:
        None
    """
    if byte_count <= 0:
        return 0
    per_record = average if average > 0 else TYPICAL_RECORD_BYTES
    return max(1, round(byte_count / min(per_record, MAX_RECORD_BYTES)))


def bad_header_incident(
    ref: SegmentRef, size: int, following: int | None, now_us: int
) -> LossIncident:
    """Describe a quarantined segment whose header could not be read.

    Args:
        ref: The segment (by its name's sequence number).
        size: Its size in bytes.
        following: First sequence number of the next segment, if any.
        now_us: Detection time.

    Returns:
        LossIncident: A ``bad_header`` incident.

    Raises:
        None
    """
    last = None if following is None else following - 1
    estimated = following - ref.first_seq if following else estimate_records(size, 0)
    return LossIncident.new(
        LossKind.BAD_HEADER, now_us, first_seq=ref.first_seq, last_seq=last,
        estimated=estimated, segment=segment_name(ref.first_seq),
        detail=f"segment header unreadable; {size} bytes moved to quarantine/",
    )  # fmt: skip


def unclean_incident(
    previous: RunState, last: SegmentScan | None, settings: SpoolSettings, now_us: int
) -> LossIncident:
    """Describe what an unclean stop may have cost: at most one fsync interval's records.

    Observations completed after the last durable record were lost with the
    process, or with the page cache in a power cut. When the stop came is
    not known, only that it was after that record and before this recovery,
    so that is the range. Poll results arrive in bursts, so the final
    interval before the last durable record can be nearly empty while the
    lost one was full; the count is therefore the most records any one
    ``fsync_interval`` held among the newest ``fsync_records`` (E.8, E.24),
    the best evidence of what one interval can hold.

    Args:
        previous: The previous run marker (not clean).
        last: Scan of the newest segment, or ``None`` if the spool was empty.
        settings: The fsync interval.
        now_us: Detection time.

    Returns:
        LossIncident: An ``unclean_stop`` incident.

    Raises:
        None
    """
    tail = list(last.tail) if last is not None else []
    window = busiest_interval(tail, _us(settings))
    start = tail[-1].header.append_us if tail else None
    return LossIncident.new(
        LossKind.UNCLEAN_STOP, now_us, start_us=start, end_us=None if start is None else now_us,
        first_seq=None if last is None or last.last_seq is None else last.last_seq + 1,
        classes=_classes(window), estimated=len(window), segment=f"run-{previous.updated_us}",
        detail="the previous collector run stopped without closing the spool; observations "
        "completed after the last durable record and before the stop were lost (at most one "
        "fsync interval; estimate from the busiest interval among the newest records)",
    )  # fmt: skip


def busiest_interval(frames: Sequence[Frame], width_us: int) -> list[Frame]:
    """Return the most frames appended within any one interval of ``width_us``.

    Args:
        frames: Verified frames in append order.
        width_us: The interval, microseconds.

    Returns:
        list[Frame]: The fullest run of frames spanning at most ``width_us``;
        the latest such run on a tie, empty for no frames.

    Raises:
        None
    """
    best: list[Frame] = []
    low = 0
    for high, frame in enumerate(frames):
        while frame.header.append_us - frames[low].header.append_us > width_us:
            low += 1
        if high - low + 1 >= len(best):
            best = list(frames[low : high + 1])
    return best


def _us(settings: SpoolSettings) -> int:
    """Return the fsync interval in microseconds.

    Args:
        settings: Spool settings.

    Returns:
        int: Microseconds.

    Raises:
        None
    """
    return round(settings.fsync_interval_s * 1_000_000)


def _classes(frames: list[Frame]) -> list[str]:
    """Collect the observation types of some records without full decoding.

    Args:
        frames: Verified frames.

    Returns:
        list[str]: Distinct ``observation_type`` values found.

    Raises:
        None
    """
    kinds = set()
    for frame in frames:
        try:
            kind = json.loads(frame.payload).get("observation_type")
        except ValueError, AttributeError:
            continue
        if isinstance(kind, str):
            kinds.add(kind)
    return sorted(kinds)


def refused_incident(
    observations: Sequence[Observation], exc: BaseException, clock: Clock
) -> LossIncident:
    """Describe observations the spool refused, with their exact count and range.

    Args:
        observations: The refused observations.
        exc: Why they were refused.
        clock: For the detection time.

    Returns:
        LossIncident: ``rejected_full`` for space, else ``write_failed``.

    Raises:
        None
    """
    stamps = [to_epoch_us(o.timestamp) for o in observations]
    kind = LossKind.REJECTED_FULL if isinstance(exc, SpoolFullError) else LossKind.WRITE_FAILED
    return LossIncident.new(
        kind, to_epoch_us(clock.now_utc()), start_us=min(stamps), end_us=max(stamps),
        classes=[observation_type(o).value for o in observations], estimated=len(observations),
        exact=True, detail=f"the spool refused the observations: {exc}",
    )  # fmt: skip
