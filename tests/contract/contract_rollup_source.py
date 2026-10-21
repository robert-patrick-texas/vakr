"""The rollup job's read port behaves the same on every backend (RET-004, RET-009, DB-004).

Subclass ``RollupSourceContract`` in an adapter's test module; the
adapter's ``conftest.py`` supplies ``telemetry_factory``. Every test reads
pages until ``next_cursor`` is ``None`` and compares the items, because a
backend may return fewer items than the limit before the last page.

Dependencies:
    pytest; tests.contract.contract_samples; vakr.ports.telemetry.

Example:
    $ uv run pytest tests/contract -k RollupSource
"""

from collections.abc import Callable
from datetime import timedelta

import pytest

from tests.contract import contract_samples as s
from vakr.domain.enums import UptimeContinuity
from vakr.domain.errors import ValidationError
from vakr.domain.ids import BatchId, CollectorId, InterfaceId, new_id
from vakr.domain.paging import Page, PageRequest
from vakr.domain.quality import Quality
from vakr.domain.rollups import RollupBucket
from vakr.domain.series import Metric, TimeWindow
from vakr.ports.telemetry import TelemetryBackend, TelemetryBackendFactory
from vakr.ports.telemetry.rollup_source import PollSlot

BUCKET = TimeWindow(s.T0 + timedelta(hours=1), s.T0 + timedelta(hours=1, minutes=15))
SECOND = timedelta(seconds=1)


def every[T](read: Callable[[TimeWindow, PageRequest], Page[T]], limit: int) -> list[T]:
    """Read every page of a rollup-source listing.

    Args:
        read: A page method.
        limit: Subjects per page.

    Returns:
        list[T]: Every item, in page order.

    Raises:
        None
    """
    items: list[T] = []
    cursor = None
    for _ in range(100):
        page = read(BUCKET, PageRequest(limit, cursor))
        assert page.total is None
        items.extend(page.items)
        cursor = page.next_cursor
        if cursor is None:
            return items
    raise AssertionError("paging did not end")


def write(backend: TelemetryBackend, rows: list) -> None:
    """Store observations in one batch.

    Args:
        backend: The backend.
        rows: Observations.

    Returns:
        None

    Raises:
        None
    """
    backend.writer.write_batch(new_id(BatchId), rows)


class RollupSourceContract:
    """``RollupSource`` behaviour for any ``TelemetryBackend``."""

    def test_ret009_first_native_time_steps_over_gaps(
        self, telemetry_factory: TelemetryBackendFactory
    ) -> None:
        """RET-009: the job finds the next interface or ICMP data after an outage.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        backend = telemetry_factory()
        dev, iface, col = s.device().id, new_id(InterfaceId), new_id(CollectorId)
        late = s.T0 + timedelta(days=2, minutes=7)
        first_icmp = s.icmp(dev, col, s.T0 + timedelta(hours=5))
        write(backend, [s.poll(dev, col, s.T0), s.snmp(dev, col, s.T0), first_icmp,
                        s.sample(dev, iface, col, late)])  # fmt: skip
        source = telemetry_factory().rollup_source
        everything = TimeWindow(s.T0, s.T0 + timedelta(days=3))
        assert source.first_native_time(everything) == s.T0 + timedelta(hours=5)
        after = TimeWindow(s.T0 + timedelta(hours=6), s.T0 + timedelta(days=3))
        assert source.first_native_time(after) == late
        assert source.first_native_time(TimeWindow(s.T0, s.T0 + timedelta(hours=5))) is None

    def test_ret004_interface_samples_are_slim_ordered_and_keep_gaps(
        self, telemetry_factory: TelemetryBackendFactory
    ) -> None:
        """RET-004, invariant 1: six values in metric order; ``None`` stays ``None``.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        backend = telemetry_factory()
        dev, iface, col = s.device().id, new_id(InterfaceId), new_id(CollectorId)
        start = BUCKET.start
        rows = [s.sample(dev, iface, col, start + 60 * k * SECOND, in_bps=float(k), out_bps=None,
                         in_pps=2.0, out_pps=3.0, in_error_rate=0.0, out_error_rate=0.5,
                         quality=Quality.RETRIED)
                for k in (2, 0, 1)]  # fmt: skip
        outside = s.sample(dev, iface, col, BUCKET.end)
        write(backend, [*rows, outside])
        (found,) = every(telemetry_factory().rollup_source.interface_samples, 10)
        assert (found.interface_id, found.collector_id, found.device_id) == (iface, col, dev)
        assert [x.timestamp for x in found.samples] == [start + 60 * k * SECOND for k in range(3)]
        assert found.samples[1].values == (1.0, None, 2.0, 3.0, 0.0, 0.5)
        assert found.samples[0].quality is Quality.RETRIED
        assert {x.observation_id for x in found.samples} == {r.observation_id for r in rows}

    def test_db004_pages_cover_every_subject_and_collector_once(
        self, telemetry_factory: TelemetryBackendFactory
    ) -> None:
        """DB-004, STATE-004, API-005: keyset pages by subject, every collector kept apart.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        backend = telemetry_factory()
        dev, one, two = s.device().id, new_id(CollectorId), new_id(CollectorId)
        ifaces = [new_id(InterfaceId) for _ in range(5)]
        rows = [s.sample(dev, i, c, BUCKET.start + SECOND) for i in ifaces for c in (one, two)]
        quiet = new_id(InterfaceId)  # known to the store, silent in this bucket
        write(backend, [*rows, s.sample(dev, quiet, one, s.T0)])
        source = telemetry_factory().rollup_source
        for limit in (1, 2, 500):
            found = every(source.interface_samples, limit)
            keys = [(x.interface_id, x.collector_id) for x in found]
            assert keys == sorted(((i, c) for i in ifaces for c in (one, two)),
                                  key=lambda k: (str(k[0]), str(k[1])))  # fmt: skip
        page = source.interface_samples(BUCKET, PageRequest(2))
        assert len({x.interface_id for x in page.items}) <= 2
        with pytest.raises(ValidationError):
            source.interface_samples(BUCKET, PageRequest(2, "not-a-cursor"))
        with pytest.raises(ValidationError):
            source.device_evidence(BUCKET, PageRequest(2, "WyJub3QtYS11dWlkIl0"))

    def test_ret006_device_evidence_brings_cycles_schedule_and_restarts(
        self, telemetry_factory: TelemetryBackendFactory
    ) -> None:
        """RET-006, E.10: cycles in order, the latest poll in the lookback, the SNMP restart.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        backend = telemetry_factory()
        dev, quiet, col = s.device().id, s.device(name="edge").id, new_id(CollectorId)
        start = BUCKET.start
        cycles = [s.icmp(dev, col, start + 60 * k * SECOND) for k in (1, 0)]
        reset = s.snmp(dev, col, start + 5 * SECOND,
                       uptime_continuity=UptimeContinuity.RESET_DETECTED)  # fmt: skip
        polls = [s.poll(dev, col, start - 60 * SECOND, effective_interval_s=30),
                 s.poll(dev, col, start + 840 * SECOND, effective_interval_s=60),
                 s.poll(dev, col, BUCKET.end, effective_interval_s=10),  # after the bucket
                 s.poll(quiet, col, start - 3000 * SECOND, effective_interval_s=3600)]  # fmt: skip
        write(backend, [*cycles, reset, *polls, s.snmp(quiet, col, start)])
        found = {
            d.device_id: d for d in every(telemetry_factory().rollup_source.device_evidence, 1)
        }
        assert set(found) == {dev, quiet}
        busy = found[dev]
        assert [c.observation_id for c in busy.cycles] == [cycles[1].observation_id,
                                                          cycles[0].observation_id]  # fmt: skip
        assert busy.cycles[0] == cycles[1]
        assert busy.schedule == PollSlot(start + 840 * SECOND, 60)
        assert busy.restarted is True
        assert found[quiet].cycles == ()
        assert found[quiet].schedule == PollSlot(start - 3000 * SECOND, 3600)
        assert found[quiet].restarted is False

    def test_ret009_reads_ignore_expired_native_rows(
        self, telemetry_factory: TelemetryBackendFactory
    ) -> None:
        """RET-009, ROLL-T04: data behind the native horizon is never rolled up again.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        backend = telemetry_factory()
        dev, iface, col = s.device().id, new_id(InterfaceId), new_id(CollectorId)
        write(backend, [s.icmp(dev, col, s.T0), s.sample(dev, iface, col, s.T0),
                        s.icmp(dev, col, BUCKET.start)])  # fmt: skip
        if backend.capabilities.rollups:
            backend.rollups.write([RollupBucket(dev, col, Metric.LOSS_PCT, s.T0, 900, 15, 1, 0)])
        backend.retention.apply(s.T0 + timedelta(minutes=30) + backend.retention.policy.native)
        source = telemetry_factory().rollup_source
        assert source.first_native_time(TimeWindow(s.T0, BUCKET.end)) == BUCKET.start
        early = TimeWindow(s.T0, s.T0 + timedelta(minutes=15))
        assert source.interface_samples(early, PageRequest(5)).items == ()
        assert source.device_evidence(early, PageRequest(5)).items == ()
