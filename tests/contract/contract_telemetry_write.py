"""Telemetry write/read parity: DB-004, STORE-009, STORE-010, STORE-013.

Subclass ``TelemetryWriteContract`` in an adapter's test module; the
adapter's ``conftest.py`` supplies ``telemetry_factory``.

Dependencies:
    pytest; tests.contract.contract_samples; vakr.ports.telemetry.

Example:
    $ uv run pytest tests/contract -k TelemetryWrite
"""

from datetime import timedelta
from typing import cast

import pytest

from tests.contract import contract_samples as s
from vakr.domain.enums import BaselineState, CounterFamily, ObservationType, PollExecution
from vakr.domain.errors import ValidationError
from vakr.domain.ids import BatchId, CollectorId, DeviceId, InterfaceId, new_id
from vakr.domain.observations import Observation, observation_subject
from vakr.domain.paging import PageRequest
from vakr.domain.quality import Quality
from vakr.domain.series import TimeWindow
from vakr.ports.telemetry import TelemetryBackendFactory

DAY = TimeWindow(s.T0 - timedelta(days=1), s.T0 + timedelta(days=1))


def _one_of_each(device: DeviceId, iface: InterfaceId, col: CollectorId) -> list[Observation]:
    """Build one observation of every type, with ``None`` values and combined flags.

    Args:
        device: Device UUID.
        iface: Interface UUID.
        col: Collector UUID.

    Returns:
        list[Observation]: ICMP, SNMP, interface, poll, and health observations.

    Raises:
        None
    """
    no_baseline = Quality.BASELINE_FIRST_SAMPLE | Quality.PARTIAL_RESPONSE | Quality.MISSING_FIELDS
    lost = (None, None, None, None, None)
    return [
        s.icmp(device, col, s.T0, rtts_us=lost, min_rtt_us=None, avg_rtt_us=None,
               median_rtt_us=None, max_rtt_us=None, jitter_us=None),
        s.snmp(device, col, s.T0, sys_uptime_ticks=(1 << 32) - 1, error_code="partial-walk"),
        s.sample(device, iface, col, s.T0, baseline_state=BaselineState.NO_BASELINE,
                 quality=no_baseline, in_bps=None, out_bps=None, out_octets=None,
                 counter_family=CounterFamily.LEGACY32, in_octets=(1 << 32) - 1),
        s.poll(device, col, s.T0, execution=PollExecution.NOT_ATTEMPTED, started_at=None,
               completed_at=None, quality=Quality.NOT_ATTEMPTED | Quality.COLLECTOR_DEGRADED),
        s.health(col, s.T0, spool_used_pct=12.5, clock_offset_us=-250),
    ]  # fmt: skip


class TelemetryWriteContract:
    """Write and raw-read parity for any ``TelemetryBackend``."""

    def test_db004_every_type_round_trips(self, telemetry_factory: TelemetryBackendFactory) -> None:
        """DB-004, STORE-013: IDs, ``None`` values, wide counters, and quality survive storage.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        written = _one_of_each(s.device().id, new_id(InterfaceId), new_id(CollectorId))
        result = telemetry_factory().writer.write_batch(new_id(BatchId), written)
        assert (result.inserted, result.duplicates, result.total) == (5, 0, 5)
        reader = telemetry_factory().query
        kinds = [ObservationType.ICMP, ObservationType.SNMP_DEVICE, ObservationType.INTERFACE,
                 ObservationType.DEVICE_POLL, ObservationType.COLLECTOR_HEALTH]  # fmt: skip
        for kind, observation in zip(kinds, written, strict=True):
            page = reader.read_raw(kind, observation_subject(observation), DAY, PageRequest())
            assert page.items == (observation,)
            assert int(page.items[0].quality) == int(observation.quality)
            assert page.total is None
            s.assert_no_row_ids(page)

    def test_db004_raw_reads_are_ordered_bounded_and_per_collector(
        self, telemetry_factory: TelemetryBackendFactory
    ) -> None:
        """DB-004, STATE-004: ``(timestamp, id)`` order, half-open windows, collectors apart.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        device, east, west = s.device().id, new_id(CollectorId), new_id(CollectorId)
        times = [s.T0 + m * s.MINUTE for m in (4, 0, 2, 1, 3, 5)]
        east_obs = [s.icmp(device, east, t) for t in times]
        twin = s.icmp(device, east, s.T0 + 2 * s.MINUTE)  # same instant, different UUID
        west_obs = [s.icmp(device, west, s.T0 + s.MINUTE)]
        backend = telemetry_factory()
        backend.writer.write_batch(new_id(BatchId), [*east_obs, twin, *west_obs])
        window = TimeWindow(s.T0, s.T0 + 5 * s.MINUTE)
        pages, cursor = [], None
        while True:
            page = backend.query.read_raw(
                ObservationType.ICMP,
                device,
                window,
                PageRequest(limit=2, cursor=cursor),
                collector_id=east,
            )
            pages.extend(page.items)
            if (cursor := page.next_cursor) is None:
                break
        expected = sorted((o for o in [*east_obs, twin] if window.contains(o.timestamp)),
                          key=lambda o: (o.timestamp, o.observation_id))  # fmt: skip
        assert pages == expected
        assert len(pages) == 6
        both = backend.query.read_raw(ObservationType.ICMP, device, window, PageRequest(limit=500))
        assert len(both.items) == 7
        with pytest.raises(ValidationError):
            backend.query.read_raw(ObservationType.ICMP, device, window, PageRequest(cursor="??"))

    def test_store009_duplicate_batches_are_idempotent(
        self, telemetry_factory: TelemetryBackendFactory
    ) -> None:
        """STORE-009: replaying a batch inserts nothing twice and reports duplicates.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        device, col = s.device().id, new_id(CollectorId)
        batch = [s.icmp(device, col, s.T0 + m * s.MINUTE) for m in range(4)]
        extra = s.icmp(device, col, s.T0 + 9 * s.MINUTE)
        writer = telemetry_factory().writer
        first = writer.write_batch(new_id(BatchId), batch)
        replay = writer.write_batch(new_id(BatchId), batch)
        overlap = telemetry_factory().writer.write_batch(new_id(BatchId), [batch[0], extra, extra])
        assert (first.inserted, first.duplicates) == (4, 0)
        assert (replay.inserted, replay.duplicates) == (0, 4)
        assert (overlap.inserted, overlap.duplicates) == (1, 2)
        assert writer.write_batch(new_id(BatchId), []).total == 0
        stored = telemetry_factory().query.read_raw(
            ObservationType.ICMP, device, DAY, PageRequest()
        )
        assert len(stored.items) == 5

    def test_store010_rejected_batch_writes_nothing(
        self, telemetry_factory: TelemetryBackendFactory
    ) -> None:
        """STORE-010, STORE-011: an oversized or invalid batch is refused as a whole.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        backend = telemetry_factory()
        device, col = s.device().id, new_id(CollectorId)
        valid = s.icmp(device, col, s.T0)
        too_many = [valid] * (backend.capabilities.max_batch_rows + 1)
        with pytest.raises(ValidationError):
            backend.writer.write_batch(new_id(BatchId), too_many)
        with pytest.raises(ValidationError):
            backend.writer.write_batch(new_id(BatchId), [valid, cast(Observation, object())])
        stored = backend.query.read_raw(ObservationType.ICMP, device, DAY, PageRequest())
        assert stored.items == ()
