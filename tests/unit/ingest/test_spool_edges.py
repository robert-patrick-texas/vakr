"""Edge cases: cancelled waits, resuming mid-segment, damage in the open segment, full disks.

Dependencies:
    pytest, asyncio; vakr.ingest; tests.unit.ingest.spool_testkit.

Example:
    $ uv run pytest tests/unit/ingest/test_spool_edges.py
"""

import asyncio
from pathlib import Path

import pytest

from tests.unit.ingest.spool_testkit import (
    COLLECTOR,
    CountingOps,
    FlakyWriter,
    interface_samples,
    run,
    small_settings,
    spool_settings,
)
from vakr.domain.errors import ValidationError
from vakr.ingest import Ingester, LossKind, Spool, read_incidents
from vakr.ingest import incident_log as incident_log_module
from vakr.ingest import inspect as inspect_module
from vakr.ingest.fileops import FileOps
from vakr.ingest.framing import SEGMENT_HEADER_BYTES
from vakr.ingest.gate import AdmissionGate
from vakr.ingest.incident_log import IncidentLog
from vakr.ingest.incidents import LOG_FILE, OLD_LOG_FILE, LossIncident
from vakr.ingest.reader import SpoolReader
from vakr.ingest.segments import QUARANTINE_DIR, list_segments
from vakr.ingest.state import ConsumerOffset, save_offset
from vakr.ports.telemetry import TelemetryCapabilities

CAPS = TelemetryCapabilities(rollups=False, max_batch_rows=50_000)


def test_queue_t01_gate_is_fifo_and_survives_cancelled_waiters() -> None:
    """QUEUE-T01: a cancelled waiter gives up its place; the next one is admitted in order.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Queue three waiters behind a full gate and cancel the middle one.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        with pytest.raises(ValidationError):
            AdmissionGate(0)
        gate = AdmissionGate(10)
        await gate.acquire(10)
        order: list[str] = []

        async def wait(name: str, count: int) -> None:
            """Acquire and note the order.

            Args:
                name: Label.
                count: Observations.

            Returns:
                None

            Raises:
                None
            """
            await gate.acquire(count)
            order.append(name)

        tasks = [asyncio.create_task(wait(n, c)) for n, c in (("a", 6), ("b", 4), ("c", 4))]
        await asyncio.sleep(0)
        assert gate.waiting == 3
        tasks[1].cancel()
        await asyncio.sleep(0)
        gate.release(10)
        await asyncio.gather(tasks[0], tasks[2])
        with pytest.raises(asyncio.CancelledError):
            await tasks[1]
        assert order == ["a", "c"]
        assert gate.depth == 10

    run(scenario())


def test_cancel_after_admission_returns_the_room() -> None:
    """A waiter cancelled just as it was admitted releases what it was given.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Release and cancel in the same loop iteration.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        gate = AdmissionGate(5)
        await gate.acquire(5)
        task = asyncio.create_task(gate.acquire(3))
        await asyncio.sleep(0)
        gate.release(5)  # admits the waiter: its future completes
        task.cancel()  # ...but the task is cancelled before it resumes
        with pytest.raises(asyncio.CancelledError):
            await task
        assert gate.depth == 0

    run(scenario())


def test_store006_reader_resumes_mid_segment_and_reports_a_gap_before_the_oldest(
    tmp_path: Path,
) -> None:
    """STORE-006: replay starts right after the offset; a hole before the oldest segment is loss.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Resume at offset 7 of one segment, then fake an offset far behind the spool.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        spool = Spool(spool_settings(tmp_path), COLLECTOR)
        await spool.open()
        await spool.append(interface_samples(10))
        assert spool.incidents is not None
        reader = SpoolReader(spool, spool.incidents, lambda: 0)
        reader.start(7)
        assert [f.header.seq for f in reader.read(100).frames] == [8, 9, 10]
        reader.close()
        await spool.close()
        directory = spool.directory
        list_segments(directory)[0].path.unlink()  # records 1-10 vanish from the disk
        save_offset(directory, ConsumerOffset(5, None, 0), FileOps())  # only 1-5 were stored
        again = Spool(spool_settings(tmp_path), COLLECTOR)
        await again.open()
        assert again.incidents is not None
        reader = SpoolReader(again, again.incidents, lambda: 0)
        reader.start(5)
        assert reader.read(100).frames == ()
        (incident,) = read_incidents(directory)
        assert incident.kind is LossKind.MISSING_RANGE
        assert (incident.first_seq, incident.last_seq, incident.estimated) == (6, 10, 5)
        await again.close()

    run(scenario())


def test_damage_in_the_open_segment_rotates_and_is_reported_once(tmp_path: Path) -> None:
    """STORE-007: damage found in the segment still being written makes the writer rotate.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Corrupt a durable record of the open segment, ingest, append, ingest.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        spool = Spool(small_settings(tmp_path, max_bytes=256 * 1024), COLLECTOR)
        await spool.open()
        await spool.append(interface_samples(4))
        (segment,) = list_segments(spool.directory)
        with segment.path.open("r+b") as f:
            f.seek(SEGMENT_HEADER_BYTES + 2_000)
            f.write(b"\xff\xff")
        writer = FlakyWriter()
        ingester = Ingester(spool, writer, CAPS)
        await ingester.start(run=False)
        await ingester.cycle()
        assert spool.thread is not None
        assert spool.thread.writer.rotate_requested
        receipt = await spool.append(interface_samples(2))
        assert receipt.first_seq == 5
        assert len(list_segments(spool.directory)) == 2
        await ingester.cycle()
        await ingester.cycle()
        stored = len(writer.store.observations)
        assert stored == 2 + 2  # two before the damage, then the new segment
        (incident,) = read_incidents(spool.directory)
        assert incident.kind is LossKind.DAMAGED_SEGMENT
        assert [p.name for p in (spool.directory / QUARANTINE_DIR).iterdir()] == [segment.name]
        await ingester.stop()
        await spool.close()

    run(scenario())


def test_first_segment_waits_for_space(tmp_path: Path) -> None:
    """A full disk at start fails open cleanly, or defers the first segment to the first append.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Fail the header write at start.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        ops = CountingOps()
        spool = Spool(spool_settings(tmp_path), COLLECTOR, ops=ops)
        ops.fail_writes = 1  # the run marker cannot be written: open fails and unlocks
        with pytest.raises(OSError, match="No space"):
            await spool.open()
        ops.pass_writes, ops.fail_writes = 1, 1  # the marker fits; the segment header does not
        await spool.open()
        assert spool.active_first_seq is None
        assert (await spool.append(interface_samples(1))).first_seq == 1
        await spool.close()

    run(scenario())


def test_incident_log_rotates_and_keeps_unsaved_incidents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """STORE-007: the log is bounded by rotation; a full disk keeps incidents in memory.

    Args:
        tmp_path: Spool directory.
        monkeypatch: Shrinks the rotation size.

    Returns:
        None

    Raises:
        None
    """
    monkeypatch.setattr(incident_log_module, "MAX_LOG_BYTES", 200)
    ops = CountingOps()
    log = IncidentLog(tmp_path, ops)
    for seq in range(4):
        assert log.record(LossIncident.new(LossKind.TORN_TAIL, 1, first_seq=seq, detail="x"))
    assert not log.record(LossIncident.new(LossKind.TORN_TAIL, 2, first_seq=3, detail="again"))
    assert (tmp_path / OLD_LOG_FILE).exists()
    assert (tmp_path / LOG_FILE).exists()
    assert len(read_incidents(tmp_path)) <= 4
    ops.fail_writes = 1
    assert log.record(LossIncident.new(LossKind.TORN_TAIL, 3, first_seq=99, detail="full"))
    assert [i.first_seq for i in log.unsaved] == [99]
    assert IncidentLog(tmp_path, FileOps()).recorded == 0


def test_inspection_tolerates_vanishing_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The health check's inspection skips files deleted under it and unknown free space.

    Args:
        tmp_path: Spool root.
        monkeypatch: Makes ``statvfs`` fail.

    Returns:
        None

    Raises:
        None
    """

    async def fill() -> Path:
        """Write one record.

        Args:
            None

        Returns:
            Path: The spool directory.

        Raises:
            None
        """
        spool = Spool(spool_settings(tmp_path), COLLECTOR)
        await spool.open()
        await spool.append(interface_samples(1))
        await spool.close()
        return spool.directory

    directory = run(fill())

    def no_statvfs(_path: object) -> None:
        """Fail like an unsupported filesystem.

        Args:
            _path: Ignored.

        Returns:
            None

        Raises:
            OSError: Always.
        """
        raise OSError("statvfs unsupported")

    monkeypatch.setattr(inspect_module.os, "statvfs", no_statvfs)
    found = inspect_module.inspect_spool(directory, spool_settings(tmp_path), 0)
    assert found.fs_free_bytes is None
    assert found.segments == 1
    assert inspect_module.oldest_unacked_us(directory, 10) is None
    (directory / "seg-00000000000000000001.spool").write_bytes(b"\x00" * 100)
    assert inspect_module.oldest_unacked_us(directory, 0) is None
