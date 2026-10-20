"""Appending: durable hand-off, group commit (E.8), rotation, and bounded queueing (QUEUE-T01).

Dependencies:
    pytest, asyncio; vakr.ingest; tests.unit.ingest.spool_testkit.

Example:
    $ uv run pytest tests/unit/ingest/test_spool_append.py
"""

import asyncio
import time
from pathlib import Path

import pytest

from tests.unit.ingest.spool_testkit import (
    COLLECTOR,
    CountingOps,
    interface_samples,
    run,
    small_settings,
    spool_files,
    spool_settings,
)
from vakr.domain.errors import ValidationError
from vakr.ingest import LossKind, Spool, SpoolClosedError, SpoolLockedError, SpoolWriteError
from vakr.ingest.scan import scan_segment
from vakr.ingest.segments import list_segments


def test_store004_append_returns_only_after_the_covering_fsync(tmp_path: Path) -> None:
    """STORE-004, E.8: a receipt means the records are on disk and an fsync covered them.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Append one batch and inspect the segment.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        ops = CountingOps()
        spool = Spool(spool_settings(tmp_path), COLLECTOR, ops=ops)
        await spool.open()
        before = len(ops.fsyncs)
        receipt = await spool.append(interface_samples(5))
        assert (receipt.first_seq, receipt.last_seq, receipt.count) == (1, 5, 5)
        assert spool.durable_seq >= receipt.last_seq
        assert len(ops.fsyncs) - before >= 2  # segment header, then the data
        (segment,) = list_segments(spool.directory)
        assert scan_segment(segment.path).last_seq == 5
        assert spool.appended == 5
        assert spool.gate.depth == 0
        await spool.close()

    run(scenario())


def test_e8_fsync_every_1000_records(tmp_path: Path) -> None:
    """E.8: with a long interval, fsyncs happen exactly at each 1,000th record.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Append 2,000 records as 20 concurrent batches.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        ops = CountingOps()
        spool = Spool(spool_settings(tmp_path, fsync_interval_s=10.0), COLLECTOR, ops=ops)
        await spool.open()
        before = len(ops.fsyncs)
        batches = [interface_samples(100) for _ in range(20)]
        receipts = await asyncio.gather(*(spool.append(b) for b in batches))
        assert len(ops.fsyncs) - before == 1 + 2  # header, then at 1,000 and 2,000 records
        assert spool.thread is not None
        assert spool.thread.commit.fsyncs == 2
        assert sorted(r.last_seq for r in receipts)[-1] == 2000
        await spool.close()

    run(scenario())


def test_e8_fsync_after_the_interval(tmp_path: Path) -> None:
    """E.8: a few records are fsynced once the interval passes, not before, and not later.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Append ten records with a 200 ms interval.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        spool = Spool(spool_settings(tmp_path, fsync_interval_s=0.2), COLLECTOR)
        await spool.open()
        await spool.append(interface_samples(1))  # create the segment first
        started = time.monotonic()
        receipt = await spool.append(interface_samples(10))
        waited = time.monotonic() - started
        assert 0.1 <= waited <= 0.2 + 0.15
        assert receipt.latency_s <= 0.2 + 0.15
        assert spool.thread is not None
        assert spool.thread.commit.fsyncs == 2
        await spool.close()

    run(scenario())


def test_e8_segments_rotate_and_directory_is_fsynced(tmp_path: Path) -> None:
    """E.8: segments rotate at the size limit; each create fsyncs the directory.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Append about 40 KiB into 16 KiB segments.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        ops = CountingOps()
        spool = Spool(small_settings(tmp_path, max_bytes=256 * 1024), COLLECTOR, ops=ops)
        await spool.open()
        for _ in range(8):
            await spool.append(interface_samples(5))
        segments = list_segments(spool.directory)
        assert len(segments) >= 3
        expected = 1
        for ref in segments:
            result = scan_segment(ref.path)
            assert (result.first_seq, result.problem) == (expected, None)
            assert result.size <= 16 * 1024 + 5 * 1100  # one batch may finish a segment
            expected = (result.last_seq or 0) + 1
        assert expected == 41
        assert ops.dir_fsyncs.count(spool.directory) >= len(segments)
        await spool.close()

    run(scenario())


def test_queue_t01_producers_wait_at_capacity(tmp_path: Path) -> None:
    """QUEUE-T01: memory holds at most queue_capacity observations; producers wait in turn.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    class SlowOps(CountingOps):
        """Fsyncs that take 50 ms, so records stay in memory a while."""

        def fsync(self, fd: int) -> None:
            """Sleep, then fsync.

            Args:
                fd: File descriptor.

            Returns:
                None

            Raises:
                None
            """
            time.sleep(0.05)
            super().fsync(fd)

    async def scenario() -> None:
        """Run eight producers of 50 against a capacity of 100.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        spool = Spool(spool_settings(tmp_path, queue_capacity=100), COLLECTOR, ops=SlowOps())
        await spool.open()
        depths: list[int] = []
        tasks = [asyncio.create_task(spool.append(interface_samples(50))) for _ in range(8)]
        while not all(t.done() for t in tasks):
            depths.append(spool.gate.depth)
            await asyncio.sleep(0.005)
        await asyncio.gather(*tasks)
        assert max(depths) <= 100
        assert spool.gate.waits > 0
        assert spool.appended == 400
        assert spool.gate.depth == 0
        with pytest.raises(ValidationError, match=r"spool\.queue_capacity"):
            await spool.append(interface_samples(101))
        await spool.close()

    run(scenario())


def test_append_refuses_non_observations_and_closed_spools(tmp_path: Path) -> None:
    """Appends check their input up front; nothing is accepted before open or after close.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Try every refusal.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        spool = Spool(spool_settings(tmp_path), COLLECTOR)
        with pytest.raises(SpoolClosedError):
            await spool.append(interface_samples(1))
        await spool.open()
        with pytest.raises(SpoolClosedError, match="already open"):
            await spool.open()
        with pytest.raises(ValidationError):
            await spool.append(["not an observation"])  # ty: ignore[invalid-argument-type]
        assert (await spool.append([])).count == 0
        await spool.close()
        await spool.close()  # idempotent
        with pytest.raises(SpoolClosedError):
            await spool.append(interface_samples(1))
        assert spool_files(tmp_path) == ["lock", "seg-00000000000000000001.spool", "writer.state"]
        assert scan_segment(list_segments(spool.directory)[0].path).records == 0  # nothing written

    run(scenario())


def test_6_2_one_producer_per_spool(tmp_path: Path) -> None:
    """6.2: a second writer for the same spool directory is refused.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Open the same spool twice.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        first = Spool(spool_settings(tmp_path), COLLECTOR)
        await first.open()
        with pytest.raises(SpoolLockedError, match="in use"):
            await Spool(spool_settings(tmp_path), COLLECTOR).open()
        await first.close()
        second = Spool(spool_settings(tmp_path), COLLECTOR)
        await second.open()  # the lock died with the first descriptor
        await second.close()

    run(scenario())


def test_cancelled_caller_still_gets_a_durable_record(tmp_path: Path) -> None:
    """A producer that stops waiting does not lose its records or leak queue room.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Cancel an append after submission.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        spool = Spool(spool_settings(tmp_path, fsync_interval_s=0.2), COLLECTOR)
        await spool.open()
        task = asyncio.create_task(spool.append(interface_samples(3)))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await spool.close()
        assert spool.appended == 3
        assert spool.gate.depth == 0

    run(scenario())


def test_store007_failed_fsync_refuses_and_records_loss(tmp_path: Path) -> None:
    """STORE-007: an fsync error fails waiting appends explicitly; writing resumes in a new segment.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Fail one data fsync.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        ops = CountingOps()
        spool = Spool(spool_settings(tmp_path), COLLECTOR, ops=ops)
        await spool.open()
        await spool.append(interface_samples(1))
        ops.fail_fsyncs = 1
        with pytest.raises(SpoolWriteError, match="fsync failed"):
            await spool.append(interface_samples(4))
        assert spool.incidents is not None
        assert spool.incidents.recorded == 1
        receipt = await spool.append(interface_samples(2))
        assert receipt.first_seq == 6
        assert len(list_segments(spool.directory)) == 2
        await spool.close()
        from vakr.ingest import read_incidents

        (incident,) = read_incidents(spool.directory)
        assert (incident.kind, incident.estimated, incident.exact) == (
            LossKind.WRITE_FAILED, 4, True,
        )  # fmt: skip
        assert incident.classes == ("interface",)

    run(scenario())
