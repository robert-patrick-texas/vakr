"""SPOOL-T07, 6.4: capacity states, pausing new work at EXHAUSTED, and explicit refusal when full.

A full spool must never drop observations silently: the append fails with
``SpoolFullError``, the observations' exact count, classes, and time range
go into a ``rejected_full`` incident, and a partial write is truncated so
the segment stays readable.

Dependencies:
    pytest; vakr.ingest; tests.unit.ingest.spool_testkit.

Example:
    $ uv run pytest tests/unit/ingest/test_spool_capacity.py
"""

import errno
from pathlib import Path

import pytest

from tests.unit.ingest.spool_testkit import (
    COLLECTOR,
    CountingOps,
    FlakyWriter,
    interface_samples,
    run,
    small_settings,
)
from vakr.ingest import CapacityState, Ingester, LossKind, Spool, SpoolFullError, read_incidents
from vakr.ingest.capacity import SpoolUsage, capacity_state
from vakr.ingest.scan import scan_segment
from vakr.ingest.segments import list_segments
from vakr.ports.telemetry import TelemetryCapabilities

CAPS = TelemetryCapabilities(rollups=False, max_batch_rows=50_000)


def test_spool_t07_threshold_states(tmp_path: Path) -> None:
    """SPOOL-T07: 70/85/95% of the limit start WARNING, CRITICAL, EXHAUSTED (thresholds inclusive).

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """
    settings = small_settings(tmp_path)
    assert [capacity_state(p, settings) for p in (0, 69.9, 70, 84.9, 85, 94.9, 95, 100)] == [
        CapacityState.NORMAL, CapacityState.NORMAL, CapacityState.WARNING, CapacityState.WARNING,
        CapacityState.CRITICAL, CapacityState.CRITICAL, CapacityState.EXHAUSTED,
        CapacityState.EXHAUSTED,
    ]  # fmt: skip
    usage = SpoolUsage(settings, segment_bytes=10)
    usage.set_quarantine(settings.max_bytes)
    assert usage.used_pct() == 100.0
    assert not usage.fits(1)
    usage.remove(10**9)
    usage.set_quarantine(0)
    assert usage.total == 0
    assert usage.state() is CapacityState.NORMAL


def test_spool_t07_exhausted_pauses_new_work_then_full_refuses_explicitly(tmp_path: Path) -> None:
    """SPOOL-T07, 6.4: EXHAUSTED pauses new normal work; past the limit appends fail, recorded.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Fill a 64 KiB spool while the database is down, then let ingestion drain it.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        spool = Spool(small_settings(tmp_path), COLLECTOR)
        await spool.open()
        states = []
        for _ in range(100):
            states.append(spool.usage.state())
            try:
                await spool.append(interface_samples(5))
            except SpoolFullError as exc:
                assert "spool.max_size_mib" in str(exc)  # noqa: PT017
                break
        else:
            pytest.fail("the spool never filled")
        assert states[0] is CapacityState.NORMAL
        assert {CapacityState.WARNING, CapacityState.CRITICAL} <= set(states)
        assert spool.usage.state() is CapacityState.EXHAUSTED
        assert not spool.admits_new_work()
        assert spool.usage.total <= spool.settings.max_bytes
        (incident,) = read_incidents(spool.directory)
        assert (incident.kind, incident.estimated, incident.exact) == (
            LossKind.REJECTED_FULL, 5, True,
        )  # fmt: skip
        assert incident.classes == ("interface",)
        assert incident.start_us is not None
        assert incident.end_us is not None
        assert incident.start_us < incident.end_us
        ingester = Ingester(spool, FlakyWriter(), CAPS)
        await ingester.start(run=False)
        while await ingester.cycle():
            pass
        assert spool.usage.state() is CapacityState.NORMAL
        assert spool.admits_new_work()
        await spool.append(interface_samples(5))
        await spool.close()

    run(scenario())


def test_spool_t07_disk_full_truncates_partial_write_and_records_loss(tmp_path: Path) -> None:
    """SPOOL-T07, STORE-007: ENOSPC mid-write leaves a clean segment, an error, and an incident.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Make one write fail with ENOSPC after 100 bytes.

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
        await spool.append(interface_samples(2))
        ops.fail_writes, ops.partial = 1, 100
        with pytest.raises(SpoolFullError, match="filesystem full"):
            await spool.append(interface_samples(3))
        (segment,) = list_segments(spool.directory)
        result = scan_segment(segment.path)
        assert (result.last_seq, result.problem) == (2, None)  # the partial write is gone
        ops.fail_writes = 1
        with pytest.raises(SpoolFullError):
            await spool.append(interface_samples(4))  # a second refusal is a second incident
        receipt = await spool.append(interface_samples(1))  # space is back
        assert receipt.first_seq == 3
        await spool.close()
        incidents = read_incidents(spool.directory)
        assert [(i.kind, i.estimated) for i in incidents] == [
            (LossKind.REJECTED_FULL, 3), (LossKind.REJECTED_FULL, 4),
        ]  # fmt: skip

    run(scenario())


def test_other_write_errors_are_write_failures(tmp_path: Path) -> None:
    """An I/O error that is not about space is a ``write_failed`` incident, still explicit.

    Args:
        tmp_path: Spool root.

    Returns:
        None

    Raises:
        None
    """

    async def scenario() -> None:
        """Make one write fail with EIO.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        ops = CountingOps()
        spool = Spool(small_settings(tmp_path), COLLECTOR, ops=ops)
        await spool.open()
        await spool.append(interface_samples(1))
        ops.fail_writes, ops.write_errno = 1, errno.EIO
        from vakr.ingest import SpoolWriteError

        with pytest.raises(SpoolWriteError, match="EIO"):
            await spool.append(interface_samples(2))
        await spool.close()
        (incident,) = read_incidents(spool.directory)
        assert incident.kind is LossKind.WRITE_FAILED

    run(scenario())
