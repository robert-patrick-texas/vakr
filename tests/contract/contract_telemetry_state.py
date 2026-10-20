"""Current-state semantics: DB-005, STATE-003/004, TIME-005, OBS-001, DB-007.

Subclass ``TelemetryStateContract`` in an adapter's test module; the
adapter's ``conftest.py`` supplies ``telemetry_factory``.

Dependencies:
    pytest; tests.contract.contract_samples; vakr.ports.telemetry.

Example:
    $ uv run pytest tests/contract -k TelemetryState
"""

from datetime import timedelta

import pytest

from tests.contract import contract_samples as s
from vakr.domain.enums import Freshness, IcmpState, PollExecution
from vakr.domain.errors import ValidationError
from vakr.domain.ids import BatchId, CollectorId, DeviceId, InterfaceId, new_id
from vakr.domain.observations import IcmpObservation
from vakr.domain.paging import PageRequest
from vakr.domain.quality import Quality
from vakr.domain.series import Metric, TimeWindow
from vakr.ports.telemetry import TelemetryBackendFactory


def _not_attempted(device: DeviceId, col: CollectorId, minutes: int) -> IcmpObservation:
    """Build an ICMP record of a poll the collector never made (invariant 2).

    Args:
        device: The device.
        col: The collector.
        minutes: Minutes after ``T0``.

    Returns:
        IcmpObservation: The record, flagged ``NOT_ATTEMPTED``.

    Raises:
        None
    """
    return s.icmp(device, col, s.T0 + minutes * s.MINUTE, rtts_us=(None,) * 5,
                  state=IcmpState.UNKNOWN, quality=Quality.NOT_ATTEMPTED, min_rtt_us=None,
                  avg_rtt_us=None, median_rtt_us=None, max_rtt_us=None, jitter_us=None)  # fmt: skip


class TelemetryStateContract:
    """Current state and collector health for any ``TelemetryBackend``."""

    def test_db005_device_state_is_latest_valid_evidence(
        self, telemetry_factory: TelemetryBackendFactory
    ) -> None:
        """DB-005, STATE-003, E.3: latest non-skipped evidence, with age, freshness, and quality.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        dev, col, other = s.device().id, new_id(CollectorId), new_id(CollectorId)
        newest = s.icmp(dev, col, s.T0 + s.MINUTE)
        snmp = s.snmp(dev, col, s.T0 + timedelta(seconds=30), quality=Quality.RETRIED)
        missed = s.poll(dev, col, s.T0 + 2 * s.MINUTE, execution=PollExecution.NOT_ATTEMPTED,
                        started_at=None, completed_at=None, quality=Quality.NOT_ATTEMPTED,
                        effective_interval_s=120)  # fmt: skip
        rows = [s.icmp(dev, col, s.T0), newest, _not_attempted(dev, col, 2), snmp, missed,
                s.icmp(dev, other, s.T0 + 5 * s.MINUTE)]  # fmt: skip
        telemetry_factory().writer.write_batch(new_id(BatchId), rows)
        current = telemetry_factory().current
        state = current.device_state(dev, col, now=s.T0 + 3 * s.MINUTE)
        assert state is not None
        assert (state.icmp, state.snmp, state.poll) == (newest, snmp, missed)
        assert (state.observed_at, state.age) == (s.T0 + s.MINUTE, 2 * s.MINUTE)
        assert (state.collector_id, state.effective_interval_s) == (col, 120)
        assert state.quality == newest.quality | Quality.RETRIED
        assert state.freshness is Freshness.FRESH
        edge = current.device_state(dev, col, now=s.T0 + s.MINUTE + timedelta(seconds=300))
        late = current.device_state(dev, col, now=s.T0 + s.MINUTE + timedelta(seconds=301))
        assert edge is not None
        assert edge.freshness is Freshness.FRESH
        assert late is not None
        assert late.freshness is Freshness.STALE
        assert current.device_state(s.device().id, col, now=s.T0) is None
        with pytest.raises(ValidationError):
            current.device_state(dev, col, now=s.T0.replace(tzinfo=None))

    def test_db005_ties_and_missing_evidence(
        self, telemetry_factory: TelemetryBackendFactory
    ) -> None:
        """DB-005, OBS-002: equal timestamps pick the larger UUID; skipped polls are not evidence.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        dev, idle, col = s.device().id, s.device().id, new_id(CollectorId)
        twins = [s.icmp(dev, col, s.T0) for _ in range(3)]
        telemetry_factory().writer.write_batch(
            new_id(BatchId), [*twins, _not_attempted(idle, col, 0)]
        )
        current = telemetry_factory().current
        state = current.device_state(dev, col, now=s.T0)
        assert state is not None
        assert state.icmp == max(twins, key=lambda o: o.observation_id)
        assert state.poll is None
        assert state.snmp is None
        assert state.effective_interval_s == 60
        assert current.device_state(idle, col, now=s.T0) is None
        assert current.interface_state(new_id(InterfaceId), col, now=s.T0) is None

    def test_db005_interface_states_and_listings(
        self, telemetry_factory: TelemetryBackendFactory
    ) -> None:
        """DB-005, API-005: interface state uses its poll's interval; listings page by ID.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        dev, col = s.device().id, new_id(CollectorId)
        i1, i2 = sorted([new_id(InterfaceId), new_id(InterfaceId)], key=str)
        slow = s.poll(dev, col, s.T0, effective_interval_s=300)
        fresh = s.sample(dev, i1, col, s.T0, poll_id=slow.poll_id)
        skipped = s.sample(dev, i1, col, s.T0 + s.MINUTE, quality=Quality.NOT_ATTEMPTED)
        others = [s.icmp(d, col, s.T0) for d in sorted((s.device().id for _ in range(2)), key=str)]
        rows = [slow, fresh, skipped, s.sample(dev, i2, col, s.T0), s.icmp(dev, col, s.T0), *others]
        backend = telemetry_factory()
        backend.writer.write_batch(new_id(BatchId), rows)
        state = backend.current.interface_state(i1, col, now=s.T0 + timedelta(seconds=700))
        assert state is not None
        assert state.observation == fresh
        assert (state.device_id, state.effective_interval_s, state.freshness) == (
            dev,
            300,
            Freshness.FRESH,
        )
        first = backend.current.interface_states(dev, col, PageRequest(limit=1), now=s.T0)
        rest = backend.current.interface_states(
            dev, col, PageRequest(cursor=first.next_cursor), now=s.T0
        )
        assert [x.interface_id for x in (*first.items, *rest.items)] == [i1, i2]
        assert first.total == 2
        assert rest.items[0].effective_interval_s == 60
        devices = backend.current.device_states(col, PageRequest(limit=2), now=s.T0)
        more = backend.current.device_states(col, PageRequest(cursor=devices.next_cursor), now=s.T0)
        ids = [x.device_id for x in (*devices.items, *more.items)]
        assert ids == sorted([dev, *(o.device_id for o in others)], key=str)
        assert devices.total == 3

    def test_obs001_collector_health_latest(
        self, telemetry_factory: TelemetryBackendFactory
    ) -> None:
        """OBS-001, OBS-003: the newest health snapshot per collector.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        col = new_id(CollectorId)
        snaps = [s.health(col, s.T0 + m * s.MINUTE) for m in (1, 3, 2)]
        telemetry_factory().writer.write_batch(new_id(BatchId), snaps)
        health = telemetry_factory().collector_health
        assert health.latest(col) == snaps[1]
        assert health.latest(new_id(CollectorId)) is None

    def test_db007_no_row_ids_in_state_or_series(
        self, telemetry_factory: TelemetryBackendFactory
    ) -> None:
        """DB-007, API-002: state and series read models expose UUIDs only.

        Args:
            telemetry_factory: Opens views of one telemetry store.

        Returns:
            None

        Raises:
            None
        """
        dev, iface, col = s.device().id, new_id(InterfaceId), new_id(CollectorId)
        rows = [s.icmp(dev, col, s.T0), s.poll(dev, col, s.T0), s.sample(dev, iface, col, s.T0)]
        backend = telemetry_factory()
        backend.writer.write_batch(new_id(BatchId), rows)
        window = TimeWindow(s.T0, s.T0 + s.MINUTE)
        s.assert_no_row_ids(backend.current.device_states(col, PageRequest(), now=s.T0))
        s.assert_no_row_ids(backend.current.interface_states(dev, col, PageRequest(), now=s.T0))
        s.assert_no_row_ids(backend.query.interface_series(iface, col, Metric.IN_BPS, window))
