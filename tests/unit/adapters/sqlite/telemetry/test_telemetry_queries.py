"""Queries: point caps, page sizes, downsampling, and current state across partitions.

Covers API-005/API-006 and E.19 (every read is bounded), 10.6
(downsampling on the fixed width ladder with min/max envelopes, disclosed
widths), STATE-003 (a stale subject keeps its true age, read from older day
partitions), DB-005 (skipped polls are not evidence), and E.21's bounded
lookup of an interface sample's poll.

Dependencies:
    pytest; vakr.adapters.sqlite.telemetry; telemetry_testkit.

Example:
    $ uv run pytest tests/unit/adapters/sqlite/telemetry/test_telemetry_queries.py
"""

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest

from tests.contract import contract_samples as s
from tests.unit.adapters.sqlite.telemetry.telemetry_testkit import (
    BUCKET,
    MINUTE,
    T0,
    at,
    bucket,
    open_store,
    write,
)
from vakr.adapters.sqlite.telemetry import SqliteTelemetry
from vakr.adapters.sqlite.telemetry.downsample import ladder_width
from vakr.domain.enums import ObservationType, SeriesSource
from vakr.domain.errors import ValidationError
from vakr.domain.ids import CollectorId, DeviceId, InterfaceId, new_id
from vakr.domain.paging import MAX_PAGE_LIMIT, PageRequest
from vakr.domain.quality import Quality
from vakr.domain.series import MAX_SERIES_POINTS, Metric, TimeWindow


@pytest.fixture
def backend(tmp_path: Path) -> Iterator[SqliteTelemetry]:
    """Open a fresh telemetry backend and close its store afterwards.

    Args:
        tmp_path: Directory for the file.

    Returns:
        Iterator[SqliteTelemetry]: Yields the backend.

    Raises:
        None
    """
    store = open_store(tmp_path)
    yield SqliteTelemetry(store)
    store.close()


def test_api006_point_caps_and_page_sizes_are_bounded(backend: SqliteTelemetry) -> None:
    """API-005, API-006, E.19: series stop at 1,200 points and pages at their limit.

    Args:
        backend: The backend.

    Returns:
        None

    Raises:
        None
    """
    dev, iface, col = s.device().id, new_id(InterfaceId), new_id(CollectorId)
    rows = [s.sample(dev, iface, col, T0 + m * MINUTE, in_bps=float(m)) for m in range(1300)]
    for i in range(0, 1300, 650):
        write(backend, rows[i : i + 650])
    backend.maintain(T0 + 700 * MINUTE)  # half staged, half partitioned
    day = TimeWindow(T0, at(1))
    series = backend.query.interface_series(iface, col, Metric.IN_BPS, day)
    assert (len(series.points), series.truncated) == (MAX_SERIES_POINTS, True)
    assert series.points[-1].value == float(MAX_SERIES_POINTS - 1)
    page = backend.query.read_raw(ObservationType.INTERFACE, iface, day,
                                  PageRequest(limit=MAX_PAGE_LIMIT))  # fmt: skip
    assert len(page.items) == MAX_PAGE_LIMIT
    assert page.next_cursor is not None
    with pytest.raises(ValidationError):
        PageRequest(limit=MAX_PAGE_LIMIT + 1)
    with pytest.raises(ValidationError):
        backend.query.downsampled_series(iface, col, Metric.IN_BPS, day, max_points=0)
    for cap in (10, 3, 2, 1):
        small = backend.query.downsampled_series(iface, col, Metric.IN_BPS, day, max_points=cap)
        assert len(small.points) <= cap
    long = TimeWindow(T0, at(2000))  # wider than 1,200 points at the widest rung
    capped = backend.query.downsampled_series(iface, col, Metric.IN_BPS, long)
    assert (len(capped.points), capped.truncated) == (MAX_SERIES_POINTS, True)


def test_10_6_downsampling_keeps_envelopes_gaps_and_quality(backend: SqliteTelemetry) -> None:
    """10.6, STORE-012: 2-minute buckets for a day, with min/max, gaps, and OR-ed flags.

    Args:
        backend: The backend.

    Returns:
        None

    Raises:
        None
    """
    dev, iface, col = s.device().id, new_id(InterfaceId), new_id(CollectorId)
    rows = [s.sample(dev, iface, col, T0 + m * MINUTE, in_bps=float(m),
                     quality=Quality.RETRIED if m == 1 else Quality.NONE)
            for m in range(10) if m not in (4, 5)]  # fmt: skip
    write(backend, rows)
    backend.maintain(T0 + 5 * MINUTE)
    day = TimeWindow(T0, at(1))
    series = backend.query.downsampled_series(iface, col, Metric.IN_BPS, day)
    assert ladder_width(86_400, MAX_SERIES_POINTS) == 120
    assert series.resolutions[0].source is SeriesSource.NATIVE
    assert series.resolutions[0].bucket_seconds == 120
    assert len(series.points) == 720
    first, third = series.points[0], series.points[2]
    assert (first.value, first.minimum, first.maximum, first.quality) == (
        0.5, 0.0, 1.0, Quality.RETRIED,
    )  # fmt: skip
    assert third.value is None  # minutes 4 and 5 are missing: a gap, never zero
    assert series.points[4].value == 8.5
    assert all(p.value is None for p in series.points[5:])
    month = backend.query.downsampled_series(iface, col, Metric.IN_BPS, TimeWindow(T0, at(30)))
    assert (month.resolutions[0].bucket_seconds, len(month.points)) == (3600, 720)


def test_downsampling_rebuckets_rollups_before_the_horizon(backend: SqliteTelemetry) -> None:
    """STATE-005, 10.6: rollup spans re-bucket 15-minute buckets, weighting means by samples.

    Args:
        backend: The backend.

    Returns:
        None

    Raises:
        None
    """
    iface, col = new_id(InterfaceId), new_id(CollectorId)
    buckets = [bucket(iface, col, Metric.IN_BPS, T0 + k * BUCKET, 10.0 * k) for k in range(8)]
    buckets[1] = bucket(iface, col, Metric.IN_BPS, T0 + BUCKET, 10.0, valid_count=5)
    backend.rollups.write(buckets)
    backend.retention.apply(at(31))  # horizon = coverage end, T0 + 2 h
    window = TimeWindow(T0, T0 + timedelta(hours=3))
    series = backend.query.downsampled_series(iface, col, Metric.IN_BPS, window, max_points=5)
    rollup, native = series.resolutions
    assert (rollup.source, rollup.bucket_seconds, native.source) == (
        SeriesSource.ROLLUP, 3600, SeriesSource.NATIVE,
    )  # fmt: skip
    head = series.points[0]
    # Hour 0: buckets 0-3, means 7, 17, 27, 37, weighted 15, 5, 15, 15 by valid samples.
    assert head.value == pytest.approx((7 * 15 + 17 * 5 + 27 * 15 + 37 * 15) / 50)
    assert (head.minimum, head.maximum) == (0.0, 44.0)
    assert [p.timestamp for p in series.points] == [T0, T0 + timedelta(hours=1),
                                                    T0 + timedelta(hours=2)]  # fmt: skip
    assert series.points[2].value is None  # native span with no samples


def test_state003_stale_subjects_keep_their_true_age(backend: SqliteTelemetry) -> None:
    """STATE-003, DB-005: old evidence is found in older partitions; skipped polls are not evidence.

    Args:
        backend: The backend.

    Returns:
        None

    Raises:
        None
    """
    dev, iface, col = s.device().id, new_id(InterfaceId), new_id(CollectorId)
    poll = s.poll(dev, col, at(0, 12), effective_interval_s=300)
    old = s.sample(dev, iface, col, at(0, 12), poll_id=poll.poll_id)
    write(backend, [poll, old, s.icmp(dev, col, at(0, 12))])
    backend.maintain(at(3))
    skipped = s.sample(dev, iface, col, at(3, 0, 5), quality=Quality.NOT_ATTEMPTED)
    write(backend, [skipped, s.icmp(dev, col, at(3, 0, 5), quality=Quality.NOT_ATTEMPTED)])
    now = at(3, 1)
    state = backend.current.interface_state(iface, col, now=now)
    assert state is not None
    assert (state.observation, state.age, state.effective_interval_s) == (old, now - at(0, 12), 300)
    device = backend.current.device_state(DeviceId(dev), col, now=now)
    assert device is not None
    assert device.observed_at == at(0, 12)
    listing = backend.current.device_states(col, PageRequest(), now=now)
    assert [d.device_id for d in listing.items] == [dev]
    late_poll = s.poll(dev, col, at(1, 0), effective_interval_s=900)
    far = s.sample(dev, iface, col, at(2, 6), poll_id=late_poll.poll_id)
    write(backend, [s.poll(dev, col, at(0, 6), poll_id=late_poll.poll_id), far])
    found = backend.current.interface_state(iface, col, now=now)
    assert found is not None
    assert found.effective_interval_s == 60  # the poll is outside the 24 h lookback (E.21)


def test_raw_reads_merge_collectors_in_timestamp_and_id_order(backend: SqliteTelemetry) -> None:
    """DB-004, STATE-004: without a collector filter, collectors interleave by (time, id).

    Args:
        backend: The backend.

    Returns:
        None

    Raises:
        None
    """
    dev, east, west = s.device().id, new_id(CollectorId), new_id(CollectorId)
    rows = [s.icmp(dev, c, T0 + m * MINUTE) for m in range(6) for c in (east, west)]
    write(backend, rows)
    backend.maintain(T0 + 5 * MINUTE)
    window = TimeWindow(T0, at(1))
    seen, cursor = [], None
    while True:
        page = backend.query.read_raw(ObservationType.ICMP, dev, window,
                                      PageRequest(limit=5, cursor=cursor))  # fmt: skip
        seen.extend(page.items)
        if (cursor := page.next_cursor) is None:
            break
    assert seen == sorted(rows, key=lambda o: (o.timestamp, o.observation_id))
    unknown = backend.query.read_raw(ObservationType.ICMP, new_id(DeviceId), window, PageRequest())
    assert unknown.items == ()
