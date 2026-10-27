"""SPOOL-T02, SPOOL-T08: recovery keeps every readable record and reports every loss with its range.

Each test builds a spool, damages it the way a power cut, a bad disk, or an
operator would, reopens it as the next service start would, and reads it
back with the consumer.

Dependencies:
    pytest; vakr.ingest; tests.unit.ingest.spool_testkit.

Example:
    $ uv run pytest tests/unit/ingest/test_spool_recovery.py
"""

import asyncio
import os
from pathlib import Path

from tests.unit.ingest.spool_testkit import (
    COLLECTOR,
    interface_samples,
    mixed_samples,
    run,
    small_settings,
    spool_settings,
)
from vakr.ingest import LossKind, Spool, read_incidents
from vakr.ingest.framing import SEGMENT_HEADER_BYTES, encode_record
from vakr.ingest.reader import SpoolReader
from vakr.ingest.recovery import next_seq_after
from vakr.ingest.segments import QUARANTINE_DIR, list_segments
from vakr.ingest.settings import SpoolSettings
from vakr.ingest.state import ConsumerOffset, RunState, load_run_state, save_offset


async def _fill(
    settings: SpoolSettings, batches: int, size: int = 5, *, clean: bool = True
) -> Spool:
    """Append some batches, then close cleanly or abandon the spool as a crash would.

    Args:
        settings: Spool settings.
        batches: Appends to make.
        size: Observations per append.
        clean: Close properly; otherwise only stop the writer thread.

    Returns:
        Spool: The spool (closed or abandoned).

    Raises:
        None
    """
    spool = Spool(settings, COLLECTOR)
    await spool.open()
    for _ in range(batches):
        await spool.append(interface_samples(size))
    if clean:
        await spool.close()
    else:
        _abandon(spool)
    return spool


def _abandon(spool: Spool) -> None:
    """Stop a spool's thread and drop its lock without the clean-stop marker.

    Args:
        spool: An open spool.

    Returns:
        None

    Raises:
        None
    """
    assert spool.thread is not None
    assert spool._opened is not None
    spool.thread.stop()
    spool.thread.join()
    os.close(spool._opened.lock_fd)


async def _reopen_and_read(settings: SpoolSettings) -> tuple[Spool, list[int]]:
    """Reopen a spool and read every committed record's sequence number.

    Args:
        settings: Spool settings.

    Returns:
        tuple[Spool, list[int]]: The open spool and the sequence numbers read.

    Raises:
        None
    """
    spool = Spool(settings, COLLECTOR)
    await spool.open()
    assert spool.incidents is not None
    reader = SpoolReader(spool, spool.incidents, lambda: 0)
    reader.start(None)
    seqs = [f.header.seq for f in reader.read(10_000).frames]
    reader.close()
    return spool, seqs


def test_clean_restart_records_no_incident_and_continues_numbering(tmp_path: Path) -> None:
    """A clean stop leaves nothing to report; numbering continues after the last record.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Fill, close, reopen, append.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        settings = spool_settings(tmp_path)
        await _fill(settings, 3)
        spool, seqs = await _reopen_and_read(settings)
        assert seqs == list(range(1, 16))
        assert spool.recovery is not None
        assert spool.recovery.incidents == ()
        assert (await spool.append(interface_samples(1))).first_seq == 16
        await spool.close()
        state = load_run_state(spool.directory)
        assert state is not None
        assert (state.clean, state.last_seq) == (True, 16)

    run(scenario())


def test_spool_t08_unclean_stop_records_an_estimated_loss(tmp_path: Path) -> None:
    """SPOOL-T08, E.8: after a crash, durable records survive and the final interval is reported.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Abandon a spool after mixed appends, then recover.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        settings = spool_settings(tmp_path, fsync_interval_s=5.0, fsync_records=8)
        spool = Spool(settings, COLLECTOR)
        await spool.open()
        receipts = await asyncio.gather(*(spool.append(mixed_samples()) for _ in range(2)))
        assert max(r.last_seq for r in receipts) == 8
        _abandon(spool)
        again, seqs = await _reopen_and_read(settings)
        assert seqs == list(range(1, 9))
        assert again.recovery is not None
        assert again.recovery.unclean
        (incident,) = again.recovery.incidents
        assert incident.kind is LossKind.UNCLEAN_STOP
        assert incident.first_seq == 9
        # The busiest fsync interval estimates what one interval held, capped at fsync_records.
        assert incident.estimated == 8
        assert not incident.exact
        assert set(incident.classes) == {"device_poll", "icmp", "interface", "snmp_device"}
        assert incident.start_us is not None
        # The stop came after the last durable record and before this recovery (E.36).
        assert incident.end_us == incident.detected_us
        await again.close()
        assert read_incidents(again.directory) == [incident]

    run(scenario())


def test_spool_t02_torn_tail_is_truncated_and_reported(tmp_path: Path) -> None:
    """SPOOL-T02: a torn final write is cut off; earlier records survive; the loss is recorded.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Append a partial frame to the last segment, then recover.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        settings = spool_settings(tmp_path)
        spool = await _fill(settings, 2)
        (segment,) = list_segments(spool.directory)
        good = segment.path.stat().st_size
        torn = encode_record(11, 0, COLLECTOR, b'{"observation_type":"icmp"}')[:30]
        with segment.path.open("ab") as f:
            f.write(torn)
        again, seqs = await _reopen_and_read(settings)
        assert seqs == list(range(1, 11))
        assert segment.path.stat().st_size == good
        assert again.recovery is not None
        assert again.recovery.truncated_bytes == len(torn)
        (incident,) = again.recovery.incidents
        assert (incident.kind, incident.first_seq, incident.estimated) == (
            LossKind.TORN_TAIL,
            11,
            1,
        )
        assert (await again.append(interface_samples(1))).first_seq == 11
        await again.close()

    run(scenario())


def test_spool_t02_mid_segment_damage_is_quarantined_not_skipped(tmp_path: Path) -> None:
    """SPOOL-T02: a checksum failure inside a sealed segment keeps its bytes and reports the range.

    Records before the damage are ingested; the missing range is exact
    because the next segment says where numbering resumed; once acknowledged,
    the damaged file moves to quarantine/ instead of being deleted.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Flip a byte in the middle of the first segment, then consume everything.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        settings = small_settings(tmp_path, max_bytes=256 * 1024)
        spool = await _fill(settings, 8)
        first, second = list_segments(spool.directory)[:2]
        data = bytearray(first.path.read_bytes())
        data[SEGMENT_HEADER_BYTES + 3 * 950] ^= 0xFF  # inside the fourth record or so
        first.path.write_bytes(bytes(data))
        again, seqs = await _reopen_and_read(settings)
        bad = seqs.index(second.first_seq) if second.first_seq in seqs else len(seqs)
        assert seqs[:bad] == list(range(1, bad + 1))
        assert 1 < bad < second.first_seq - 1
        assert seqs[bad:] == list(range(second.first_seq, 41))
        (incident,) = read_incidents(again.directory)
        assert incident.kind is LossKind.DAMAGED_SEGMENT
        assert incident.first_seq == bad + 1
        assert list_segments(again.directory)[0].damaged
        assert again.incidents is not None
        reader = SpoolReader(again, again.incidents, lambda: 0)
        reader.start(None)
        reader.read(10_000)
        reader.release(40)
        assert [p.name for p in (again.directory / QUARANTINE_DIR).iterdir()] == [
            first.path.with_suffix(".damaged").name
        ]
        await again.close()
        _, seqs = await _reopen_and_read(settings)
        assert len(read_incidents(again.directory)) == 1  # not reported twice

    run(scenario())


def test_spool_t08_deleted_segment_is_an_exact_missing_range(tmp_path: Path) -> None:
    """SPOOL-T08, STORE-007: a segment removed by hand becomes an exact missing-range incident.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Delete the second of several segments.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        settings = small_settings(tmp_path, max_bytes=256 * 1024)
        spool = await _fill(settings, 8)
        segments = list_segments(spool.directory)
        segments[1].path.unlink()
        save_offset(spool.directory, ConsumerOffset(0, None, 0), spool.ops)
        again = Spool(settings, COLLECTOR)
        await again.open()
        assert again.incidents is not None
        reader = SpoolReader(again, again.incidents, lambda: 0)
        reader.start(0)
        seqs = [f.header.seq for f in reader.read(10_000).frames]
        gap = range(segments[1].first_seq, segments[2].first_seq)
        assert not set(gap) & set(seqs)
        assert len(seqs) == 40 - len(gap)
        (incident,) = read_incidents(again.directory)
        assert incident.kind is LossKind.MISSING_RANGE
        assert incident.exact
        assert (incident.first_seq, incident.last_seq) == (gap[0], gap[-1])
        assert incident.estimated == len(gap)
        await again.close()

    run(scenario())


def test_spool_t02_unreadable_header_is_quarantined(tmp_path: Path) -> None:
    """SPOOL-T02: a segment whose header is garbage is moved aside with an incident.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Overwrite a sealed segment's header.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        settings = small_settings(tmp_path, max_bytes=256 * 1024)
        spool = await _fill(settings, 8)
        first, second = list_segments(spool.directory)[:2]
        with first.path.open("r+b") as f:
            f.write(b"\x00" * 8)
        again, seqs = await _reopen_and_read(settings)
        assert seqs[0] == second.first_seq
        (incident,) = again.recovery.incidents if again.recovery else ()
        assert incident.kind is LossKind.BAD_HEADER
        assert (incident.first_seq, incident.last_seq) == (1, second.first_seq - 1)
        assert (again.directory / QUARANTINE_DIR / first.name).exists()
        await again.close()

    run(scenario())


def test_half_created_last_segment_is_removed_silently(tmp_path: Path) -> None:
    """A last segment cut short before its header fsync never held a durable record.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Add a 10-byte segment file after a clean stop.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        settings = spool_settings(tmp_path)
        spool = await _fill(settings, 1)
        stub = spool.directory / "seg-00000000000000000006.spool"
        stub.write_bytes(b"VAKRSPOL\x00\x01")
        again, seqs = await _reopen_and_read(settings)
        assert seqs == [1, 2, 3, 4, 5]
        assert stub.stat().st_size == SEGMENT_HEADER_BYTES  # the new writer's own empty segment
        assert again.recovery is not None
        assert again.recovery.incidents == ()
        await again.close()

    run(scenario())


def test_next_sequence_never_reuses_a_known_number() -> None:
    """Numbering continues after the largest of disk, offset, and clean-stop marker.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert next_seq_after(None, None, None) == 1
    assert next_seq_after(10, ConsumerOffset(12, None, 0), None) == 13
    assert next_seq_after(None, ConsumerOffset(5, None, 0), RunState(True, 9, 0)) == 10
