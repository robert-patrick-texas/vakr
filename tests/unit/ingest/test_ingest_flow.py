"""Ingestion flow: time and row flushes, whole-batch acknowledgement, durable offsets, replay.

INGEST-T01/T02 (STORE-011), SPOOL-T04 and STORE-009 (idempotent replay
against the memory telemetry adapter), and the consumer-offset durability
that makes deleting segments safe (STORE-010).

Dependencies:
    pytest, asyncio; vakr.ingest; vakr.adapters.memory via spool_testkit.

Example:
    $ uv run pytest tests/unit/ingest/test_ingest_flow.py
"""

import asyncio
import time
from collections.abc import Callable
from pathlib import Path

from tests.unit.ingest.spool_testkit import (
    COLLECTOR,
    CountingOps,
    FlakyWriter,
    interface_samples,
    run,
    small_settings,
    spool_settings,
)
from vakr.ingest import Ingester, Spool
from vakr.ingest.segments import list_segments
from vakr.ingest.state import OFFSET_FILE, load_offset
from vakr.ingest.status import read_status
from vakr.ports.telemetry import TelemetryCapabilities

CAPS = TelemetryCapabilities(rollups=False, max_batch_rows=50_000)


async def _wait_for(predicate: Callable[[], bool], timeout: float) -> float:
    """Poll ``predicate()`` until true; return how long it took.

    Args:
        predicate: Zero-argument callable.
        timeout: Seconds before giving up.

    Returns:
        float: Seconds waited.

    Raises:
        AssertionError: On timeout.
    """
    started = time.monotonic()
    while not predicate():
        assert time.monotonic() - started < timeout, "condition not reached in time"
        await asyncio.sleep(0.02)
    return time.monotonic() - started


def test_ingest_t01_small_workload_flushes_on_the_interval(tmp_path: Path) -> None:
    """INGEST-T01: a few observations reach storage within the configured interval.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Run the background loop with a 1 s interval.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        spool = Spool(spool_settings(tmp_path, ingest_interval_s=1.0), COLLECTOR)
        await spool.open()
        writer = FlakyWriter()
        ingester = Ingester(spool, writer, CAPS)
        await ingester.start()
        await spool.append(interface_samples(10))
        waited = await _wait_for(lambda: len(writer.store.observations) == 10, 3.0)
        assert waited <= 1.0 + 0.5
        assert [size for _, size in writer.calls] == [10]
        await ingester.stop()
        await spool.close()

    run(scenario())


def test_ingest_t02_row_threshold_flushes_without_waiting(tmp_path: Path) -> None:
    """INGEST-T02: at max_batch_rows the ingester writes at once, in bounded batches.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Append 250 records against a 100-row backend and a 30 s interval.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        spool = Spool(spool_settings(tmp_path, ingest_interval_s=30.0), COLLECTOR)
        await spool.open()
        writer = FlakyWriter()
        ingester = Ingester(spool, writer, TelemetryCapabilities(rollups=False, max_batch_rows=100))
        assert ingester.max_rows == 100
        await ingester.start()
        for _ in range(5):
            await spool.append(interface_samples(50))
        await _wait_for(lambda: len(writer.store.observations) == 200, 3.0)
        assert [size for _, size in writer.calls] == [100, 100]
        await ingester.stop()  # the last 50 are flushed on shutdown (16.4 step 7)
        assert len(writer.store.observations) == 250
        await spool.close()

    run(scenario())


def test_e8_whole_batches_are_acknowledged(tmp_path: Path) -> None:
    """E.8: a batch split across writes is acknowledged only once all of it is stored.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Ingest one 5-record batch three rows at a time.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        spool = Spool(spool_settings(tmp_path), COLLECTOR)
        await spool.open()
        receipt = await spool.append(interface_samples(5))
        ingester = Ingester(spool, FlakyWriter(), TelemetryCapabilities(False, 3))
        await ingester.start(run=False)
        assert await ingester.cycle() == 3
        assert load_offset(spool.directory) is None
        assert ingester.status.acked_seq == 0
        assert await ingester.cycle() == 2
        offset = load_offset(spool.directory)
        assert offset is not None
        assert (offset.seq, offset.batch_id) == (5, receipt.batch_id)
        await ingester.stop()
        await spool.close()

    run(scenario())


def test_store010_offset_is_fsynced_before_segments_are_deleted(tmp_path: Path) -> None:
    """STORE-010: the offset is written durably (file and directory), then stored segments go.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Fill several small segments and ingest them.

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
        assert len(list_segments(spool.directory)) >= 3
        ingester = Ingester(spool, FlakyWriter(), CAPS)
        await ingester.start(run=False)
        dir_syncs = ops.dir_fsyncs.count(spool.directory)
        assert await ingester.cycle() == 40
        assert ops.dir_fsyncs.count(spool.directory) > dir_syncs
        offset = load_offset(spool.directory)
        assert offset is not None
        assert offset.seq == 40
        remaining = list_segments(spool.directory)
        assert [r.first_seq for r in remaining] == [spool.active_first_seq]  # only the open one
        health = read_status(spool.directory)
        assert health is not None
        assert (health.acked_seq, health.backlog_records) == (40, 0)
        assert health.oldest_unacked_age_s is None
        assert health.storage_available is True
        await ingester.stop()
        await spool.close()

    run(scenario())


def test_spool_t04_store009_replay_after_restart_is_idempotent(tmp_path: Path) -> None:
    """SPOOL-T04, STORE-009: a lost offset replays stored batches; nothing is stored twice.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Ingest, forget the offset as a crash before its write would, restart, ingest.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        settings = spool_settings(tmp_path)
        writer = FlakyWriter()
        spool = Spool(settings, COLLECTOR)
        await spool.open()
        for _ in range(3):
            await spool.append(interface_samples(4))
        ingester = Ingester(spool, writer, CAPS)
        await ingester.start(run=False)
        await ingester.cycle()
        await ingester.stop()
        await spool.close()
        (spool.directory / OFFSET_FILE).unlink()
        again = Spool(settings, COLLECTOR)
        await again.open()
        await again.append(interface_samples(2))
        replay = Ingester(again, writer, CAPS)
        await replay.start(run=False)
        assert await replay.cycle() == 14
        assert len(writer.store.observations) == 14
        assert (replay.status.inserted, replay.status.duplicates) == (2, 12)
        health = replay.health()
        assert health.backlog_records == 0
        assert health.oldest_unacked_age_s is None
        await replay.stop()
        await again.close()

    run(scenario())


def test_damaged_offset_file_replays_from_the_oldest_record(tmp_path: Path) -> None:
    """A garbled ``consumer.offset`` is treated as absent: replay everything, idempotently.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Garble the offset between two runs.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        settings = spool_settings(tmp_path)
        writer = FlakyWriter()
        spool = Spool(settings, COLLECTOR)
        await spool.open()
        await spool.append(interface_samples(3))
        first = Ingester(spool, writer, CAPS)
        await first.start(run=False)
        await first.cycle()
        await first.stop()
        await spool.close()
        (spool.directory / OFFSET_FILE).write_text("{not json")
        assert load_offset(spool.directory) is None
        again = Spool(settings, COLLECTOR)
        await again.open()
        second = Ingester(again, writer, CAPS)
        await second.start(run=False)
        assert await second.cycle() == 3
        assert second.status.duplicates == 3
        await second.stop()
        await again.close()

    run(scenario())
