"""Interface status records behave the same on every backend (E.6, E.35, schema 4, DB-004).

Subclass ``InterfaceStatusContract`` in an adapter's test module; the
adapter's ``conftest.py`` supplies ``telemetry_factory``. A status record
reads back exactly as written, its lean marks come back by time in either
order and bounded, it never shows in an interface's traffic series (it is
not a sample, E.27 #7), and it expires with native retention.

Dependencies:
    pytest; tests.contract.contract_samples; vakr.ports.telemetry.

Example:
    $ uv run pytest tests/contract -k InterfaceStatus
"""

from datetime import timedelta
from typing import cast

from tests.contract import contract_samples as s
from vakr.domain.enums import IfOperStatus, ObservationType
from vakr.domain.ids import BatchId, CollectorId, InterfaceId, new_id
from vakr.domain.paging import PageRequest
from vakr.domain.rollups import ROLLUP_BUCKET_SECONDS, RollupBucket
from vakr.domain.series import Metric, TimeWindow
from vakr.domain.timestamps import to_epoch_us
from vakr.ports.telemetry import TelemetryBackendFactory
from vakr.ports.telemetry.acceptance import AcceptanceBackend

DAY = TimeWindow(s.T0, s.T0 + timedelta(days=1))
STATUS = ObservationType.INTERFACE_STATUS


class InterfaceStatusContract:
    """Interface status storage for any ``TelemetryBackend``."""

    def test_e35_status_records_read_back_exactly_and_as_marks(
        self, telemetry_factory: TelemetryBackendFactory
    ) -> None:
        """E.35, DB-004: raw reads equal the records; marks are ordered, bounded, per collector.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        dev, iface, col = s.device().id, new_id(InterfaceId), new_id(CollectorId)
        down = s.status(dev, iface, col, s.T0)
        up = s.status(dev, iface, col, s.T0 + 5 * s.MINUTE, oper_status=IfOperStatus.UP,
                      baseline=False)  # fmt: skip
        other = s.status(dev, iface, new_id(CollectorId), s.T0)
        telemetry_factory().writer.write_batch(new_id(BatchId), [up, down, other])
        backend = cast(AcceptanceBackend, telemetry_factory())
        page = backend.query.read_raw(STATUS, iface, DAY, PageRequest(10), collector_id=col)
        assert list(page.items) == [down, up]
        marks = backend.acceptance.status_marks(iface, col, DAY)
        t0 = to_epoch_us(s.T0)
        assert [(m.ts_us - t0, m.oper_status, m.baseline, m.up_up) for m in marks.items] == [
            (0, 2, True, False), (300_000_000, 1, False, True)]  # fmt: skip
        newest = backend.acceptance.status_marks(iface, col, DAY, limit=1, newest_first=True)
        assert newest.truncated
        assert newest.items[0].up_up

    def test_e27_a_status_record_is_not_a_traffic_sample(
        self, telemetry_factory: TelemetryBackendFactory
    ) -> None:
        """E.27 #7, invariant 1: the interface's series and current state ignore status records.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        dev, iface, col = s.device().id, new_id(InterfaceId), new_id(CollectorId)
        telemetry_factory().writer.write_batch(new_id(BatchId), [s.status(dev, iface, col, s.T0)])
        backend = telemetry_factory()
        series = backend.query.interface_series(iface, col, Metric.IN_BPS, DAY)
        assert all(p.value is None for p in series.points)
        assert backend.current.interface_state(iface, col, now=s.T0 + s.MINUTE) is None

    def test_ret007_status_records_expire_with_native_data(
        self, telemetry_factory: TelemetryBackendFactory
    ) -> None:
        """RET-007, E.10: behind the native horizon a status record is gone; later ones stay.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        backend = telemetry_factory()
        if not backend.capabilities.rollups:
            return  # the horizon then follows the policy alone; covered by the series suites
        dev, iface, col = s.device().id, new_id(InterfaceId), new_id(CollectorId)
        late = s.T0 + timedelta(days=39)
        backend.writer.write_batch(
            new_id(BatchId), [s.status(dev, iface, col, s.T0), s.status(dev, iface, col, late)]
        )
        frontier = s.T0 + timedelta(days=40)
        backend.rollups.write(
            [RollupBucket(iface, col, Metric.IN_BPS, frontier, ROLLUP_BUCKET_SECONDS, 15, 0, 0)]
        )
        backend.retention.apply(frontier + timedelta(hours=1))
        everything = TimeWindow(s.T0 - timedelta(days=1), frontier)
        page = telemetry_factory().query.read_raw(STATUS, iface, everything, PageRequest(10))
        assert [o.timestamp for o in page.items] == [late]
