"""Ingestion under failure: outages, uncertain commits, bad records (SPOOL-T03/T05, INGEST-T03).

Storage trouble must never stop polling while the spool has room
(STORE-014), never lose or duplicate a batch (STORE-009, STORE-010), and
never be reported as targets being down (invariant 2, OBS-003).

Dependencies:
    pytest; vakr.ingest; tests.unit.ingest.spool_testkit.

Example:
    $ uv run pytest tests/unit/ingest/test_ingest_failures.py
"""

from collections.abc import Sequence
from pathlib import Path

import pytest

from tests.unit.ingest.spool_testkit import (
    COLLECTOR,
    FlakyWriter,
    interface_samples,
    run,
    spool_settings,
)
from vakr.domain.enums import CollectorState
from vakr.domain.errors import ValidationError
from vakr.domain.ids import BatchId, ObservationId, new_id
from vakr.domain.observations import CollectorHealthObservation, Observation
from vakr.ingest import Ingester, LossKind, Spool, SpoolClosedError, collector_state, read_incidents
from vakr.ingest.framing import SEGMENT_HEADER_BYTES, encode_record
from vakr.ingest.scan import scan_segment
from vakr.ingest.segments import QUARANTINE_DIR, list_segments
from vakr.ingest.state import load_offset
from vakr.ports.telemetry import TelemetryCapabilities, WriteResult

CAPS = TelemetryCapabilities(rollups=False, max_batch_rows=50_000)


class Sleeps:
    """A fake ``asyncio.sleep`` that records the backoff delays instead of waiting.

    Attributes:
        delays: Seconds asked for, in order.
    """

    def __init__(self) -> None:
        """Start with no delays.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        """Record a delay.

        Args:
            seconds: The delay.

        Returns:
            None

        Raises:
            None
        """
        self.delays.append(seconds)


def test_ingest_t03_retries_with_backoff_and_acknowledges_only_after_success(
    tmp_path: Path,
) -> None:
    """INGEST-T03: a failed batch stays replayable; success is acknowledged once, never re-sent.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Fail twice, then succeed; then fail a whole cycle.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        spool = Spool(spool_settings(tmp_path), COLLECTOR)
        await spool.open()
        await spool.append(interface_samples(4))
        writer, sleeps = FlakyWriter(), Sleeps()
        writer.failures = 2
        ingester = Ingester(spool, writer, CAPS, sleep=sleeps, jitter=lambda: 0.5)
        await ingester.start(run=False)
        assert await ingester.cycle() == 4
        assert sleeps.delays == [1.0, 2.0]
        assert len(writer.calls) == 3
        assert ingester.status.storage_available
        assert ingester.status.consecutive_failures == 0
        await spool.append(interface_samples(3))
        writer.failures = 99
        assert await ingester.cycle(attempts=3) == 0
        offset = load_offset(spool.directory)
        assert offset is not None
        assert offset.seq == 4  # the failed batch is not acknowledged
        health = ingester.health()
        assert (health.storage_available, health.consecutive_failures) == (False, 3)
        assert health.last_error == "StorageError: telemetry.db is locked"
        assert health.backlog_records == 3
        assert health.oldest_unacked_age_s is not None
        writer.failures = 0
        calls = len(writer.calls)
        assert await ingester.cycle() == 3
        assert [size for _, size in writer.calls[calls:]] == [3]  # only the pending batch
        await ingester.stop()
        await spool.close()

    run(scenario())


def test_spool_t03_store014_outage_does_not_stop_polling(tmp_path: Path) -> None:
    """SPOOL-T03, STORE-014: with the database down, appends succeed and health shows backlog.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Run the background loop against a failing backend.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        spool = Spool(spool_settings(tmp_path), COLLECTOR)
        await spool.open()
        writer = FlakyWriter()
        writer.failures = 10**6
        ingester = Ingester(spool, writer, CAPS, sleep=Sleeps())
        await ingester.start(run=False)
        for _ in range(5):
            receipt = await spool.append(interface_samples(10))
            await ingester.cycle(attempts=1)
        assert receipt.last_seq == 50
        assert spool.admits_new_work()
        health = ingester.health()
        assert health.backlog_records == 50
        assert health.storage_available is False
        assert health.consecutive_failures == 5
        state = collector_state(health)
        assert state is CollectorState.DEGRADED  # the collector's state, never a target's
        snapshot = CollectorHealthObservation(
            new_id(ObservationId), COLLECTOR, spool.clock.now_utc(), state, "test-build",
            **health.collector_health_fields(),
        )  # fmt: skip
        assert snapshot.spool_bytes == health.spool_bytes
        assert snapshot.queue_capacity == 10000
        writer.failures = 0
        while await ingester.cycle():
            pass  # the pending batch first, then the rest of the backlog
        assert len(writer.store.observations) == 50
        assert ingester.health().backlog_records == 0
        await ingester.stop()
        await spool.close()

    run(scenario())


def test_spool_t05_uncertain_commit_creates_no_duplicate(tmp_path: Path) -> None:
    """SPOOL-T05, STORE-009: a batch committed before the error is retried without duplicates.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Commit, lose the reply, retry.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        spool = Spool(spool_settings(tmp_path), COLLECTOR)
        await spool.open()
        await spool.append(interface_samples(6))
        writer = FlakyWriter()
        writer.commit_then_fail = 1
        ingester = Ingester(spool, writer, CAPS, sleep=Sleeps())
        await ingester.start(run=False)
        assert await ingester.cycle() == 6
        assert len(writer.store.observations) == 6
        assert len(writer.calls) == 2
        assert (ingester.status.inserted, ingester.status.duplicates) == (0, 6)
        await ingester.stop()
        await spool.close()

    run(scenario())


def _corrupt_envelope(segment: Path, index: int) -> None:
    """Change one record's envelope bytes but give the frame a valid CRC again.

    The frame then passes the spool's check, and only the envelope's own
    SHA-256 catches the change, as with a record from a buggy or newer producer.

    Args:
        segment: The segment file.
        index: Zero-based record to change.

    Returns:
        None

    Raises:
        None
    """
    frames = scan_segment(segment, keep_tail=1000).tail
    data = segment.read_bytes()[:SEGMENT_HEADER_BYTES]
    for i, frame in enumerate(frames):
        payload = frame.payload
        if i == index:
            payload = payload.replace(b'"in_bps":', b'"in_bps":9', 1)
        h = frame.header
        data += encode_record(h.seq, h.append_us, h.batch_id, payload)
    segment.write_bytes(data)


def test_store007_undecodable_record_is_quarantined_not_dropped(tmp_path: Path) -> None:
    """STORE-007: an envelope that fails its own checksum is set aside with an exact incident.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Tamper with the second of four records, then ingest.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        settings = spool_settings(tmp_path)
        spool = Spool(settings, COLLECTOR)
        await spool.open()
        await spool.append(interface_samples(4))
        await spool.close()
        _corrupt_envelope(list_segments(spool.directory)[0].path, 1)
        again = Spool(settings, COLLECTOR)
        await again.open()
        writer = FlakyWriter()
        ingester = Ingester(again, writer, CAPS)
        await ingester.start(run=False)
        assert await ingester.cycle() == 4
        assert len(writer.store.observations) == 3
        assert ingester.status.set_aside == 1
        (kept,) = (again.directory / QUARANTINE_DIR).iterdir()
        assert kept.name.startswith("undecodable-")
        assert scan_segment(kept).records == 1
        (incident,) = read_incidents(again.directory)
        assert (incident.kind, incident.first_seq, incident.estimated, incident.exact) == (
            LossKind.UNDECODABLE, 2, 1, True,
        )  # fmt: skip
        offset = load_offset(again.directory)
        assert offset is not None
        assert offset.seq == 4
        await ingester.stop()
        await again.close()

    run(scenario())


class RejectingWriter:
    """A backend that calls every batch invalid, which no retry can fix."""

    def write_batch(
        self,
        batch_id: BatchId,  # noqa: ARG002 - the port's signature
        observations: Sequence[Observation],
    ) -> WriteResult:
        """Refuse the batch.

        Args:
            batch_id: The batch.
            observations: Its observations.

        Returns:
            WriteResult: Never.

        Raises:
            ValidationError: Always.
        """
        raise ValidationError(f"batch of {len(observations)} breaks a backend rule")


def test_store007_batch_rejected_by_storage_is_set_aside(tmp_path: Path) -> None:
    """STORE-007: a batch the backend rejects as invalid is quarantined and reported, not retried.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Ingest into a rejecting backend.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        spool = Spool(spool_settings(tmp_path), COLLECTOR)
        await spool.open()
        await spool.append(interface_samples(3))
        ingester = Ingester(spool, RejectingWriter(), CAPS)
        await ingester.start(run=False)
        assert await ingester.cycle() == 3
        assert ingester.status.set_aside == 3
        (incident,) = read_incidents(spool.directory)
        assert incident.kind is LossKind.REJECTED_BY_STORAGE
        assert incident.estimated == 3
        assert "breaks a backend rule" in incident.detail
        assert any(
            p.name.startswith("rejected-") for p in (spool.directory / QUARANTINE_DIR).iterdir()
        )
        await ingester.stop()
        await spool.close()

    run(scenario())


def test_ingester_needs_an_open_spool(tmp_path: Path) -> None:
    """Ingestion refuses to start on a closed spool, and to cycle before starting.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Misuse the API.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        ingester = Ingester(Spool(spool_settings(tmp_path), COLLECTOR), FlakyWriter(), CAPS)
        with pytest.raises(SpoolClosedError, match="open the spool"):
            await ingester.start()
        with pytest.raises(SpoolClosedError, match="start ingestion"):
            await ingester.cycle()
        await ingester.stop()

    run(scenario())
