"""The unclean-stop incident under bursty appends (SPOOL-T08, STORE-007, E.8, E.36).

The resilience harness found that poll results reach the spool in bursts:
one poll's records within milliseconds, then nothing for seconds. An
estimate from only the final fsync interval before the last durable record
then undercounts the burst a power cut takes (13 against 43 records in the
harness), and a range of one interval after that record misses the lost
burst entirely. These tests pin the corrected description.

Dependencies:
    pytest: The test runner.
    vakr.ingest.losses: The incident builder under test.

Example:
    $ uv run pytest tests/unit/ingest/test_spool_losses.py
"""

import json
import uuid
from pathlib import Path

from tests.unit.ingest.spool_testkit import spool_settings
from vakr.ingest.framing import FrameHeader
from vakr.ingest.incidents import LossKind
from vakr.ingest.losses import busiest_interval, unclean_incident
from vakr.ingest.scan import Frame, SegmentScan
from vakr.ingest.state import RunState

S = 1_000_000
T0 = 1_800_000_000 * S


def frame(seq: int, append_us: int, kind: str = "interface") -> Frame:
    """Build one verified frame.

    Args:
        seq: Sequence number.
        append_us: Append time.
        kind: The envelope's observation type.

    Returns:
        Frame: The frame.

    Raises:
        None
    """
    payload = json.dumps({"observation_type": kind}).encode()
    return Frame(FrameHeader(len(payload), 0, seq, append_us, uuid.uuid4()), payload, seq * 100)


def scan_of(frames: list[Frame]) -> SegmentScan:
    """Wrap frames as the scan of a clean newest segment.

    Args:
        frames: Its frames.

    Returns:
        SegmentScan: The scan.

    Raises:
        None
    """
    last = frames[-1].header.seq
    return SegmentScan(None, len(frames), frames[0].header.seq, last, 0, 0, None, tuple(frames))


def test_busiest_interval_finds_the_fullest_burst() -> None:
    """E.8: the busiest one-interval run of frames, not the last one, is what one interval held.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the window is wrong.
    """
    burst = [frame(n, T0 + n * 1000) for n in range(1, 41)]  # 40 records within 40 ms
    frames = [*burst, frame(41, T0 + 3 * S), frame(42, T0 + 3 * S + 10)]
    assert len(busiest_interval(frames, S)) == 40
    assert busiest_interval([], S) == []
    assert len(busiest_interval(frames[-2:], S)) == 2


def test_spool_t08_unclean_stop_after_a_quiet_spell_counts_the_burst(tmp_path: Path) -> None:
    """SPOOL-T08, E.36: a stop after a quiet spell is estimated by a burst, ranged to recovery.

    Args:
        tmp_path: Spool root for the settings.

    Returns:
        None

    Raises:
        AssertionError: If the incident misdescribes the loss.
    """
    settings = spool_settings(tmp_path, fsync_interval_s=1.0, fsync_records=1000)
    burst = [
        frame(n, T0 + n * 1000, "device_poll" if n == 1 else "interface") for n in range(1, 44)
    ]
    lone = frame(44, T0 + 4 * S, "collector_health")
    detected = T0 + 30 * S
    incident = unclean_incident(RunState(False, None, T0 - S), scan_of([*burst, lone]), settings,
                                detected)  # fmt: skip
    assert incident.kind is LossKind.UNCLEAN_STOP
    assert incident.estimated == 43  # one poll's burst, not the single record before the stop
    assert set(incident.classes) == {"device_poll", "interface"}
    assert incident.first_seq == 45
    # The stop came after the last durable record, at an unknown time before recovery.
    assert (incident.start_us, incident.end_us) == (lone.header.append_us, detected)
    empty = unclean_incident(RunState(False, None, T0), None, settings, detected)
    assert (empty.estimated, empty.start_us, empty.end_us, empty.first_seq) == (0, None, None, None)
