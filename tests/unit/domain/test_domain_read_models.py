"""Read-model, paging, freshness, assembly, and clock tests (API-005/006, STORE-012, TIME-005, E.3).

Dependencies:
    pytest; vakr.domain; vakr.ports.clock and vakr.ports.telemetry.results.

Example:
    $ uv run pytest tests/unit/domain/test_domain_read_models.py
"""

import base64
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from tests.contract import contract_samples as s
from vakr.domain.enums import Freshness, ObservationType, SeriesSource
from vakr.domain.errors import ValidationError
from vakr.domain.ids import BatchId, CollectorId, DeviceId, InterfaceId, new_id
from vakr.domain.paging import MAX_PAGE_LIMIT, Page, PageRequest, decode_cursor, encode_cursor
from vakr.domain.projections import build_device_state, build_interface_state, latest
from vakr.domain.quality import Quality
from vakr.domain.rollups import ROLLUP_BUCKET_SECONDS, RollupBucket, bucket_start
from vakr.domain.series import (
    Metric,
    Series,
    SeriesPoint,
    SeriesResolution,
    TimeWindow,
    metric_value,
)
from vakr.domain.series_assembly import NativeSample, assemble_series, resolution_spans
from vakr.domain.state import CurrentDeviceState, freshness_at, stale_after
from vakr.ports.clock import ManualClock, SystemClock
from vakr.ports.telemetry.results import (
    RetentionPolicy,
    RetentionReport,
    TelemetryCapabilities,
    WriteResult,
)

COL, DEV, IFACE = new_id(CollectorId), new_id(DeviceId), new_id(InterfaceId)
HOUR = timedelta(hours=1)
BUCKET = timedelta(seconds=ROLLUP_BUCKET_SECONDS)


def test_api005_page_requests_and_cursors() -> None:
    """API-005: limits are bounded; cursors round-trip and reject tampering.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert PageRequest().limit == 100
    assert PageRequest(limit=MAX_PAGE_LIMIT).limit == 500
    for bad in (
        {"limit": 0},
        {"limit": 501},
        {"limit": True},
        {"cursor": ""},
        {"cursor": "x" * 2000},
    ):
        with pytest.raises(ValidationError):
            PageRequest(**cast(dict[str, Any], bad))
    key = (3, "core1", "0190a3c4-0000-7000-8000-000000000001")
    assert decode_cursor(encode_cursor(key), 3) == key
    not_list = base64.urlsafe_b64encode(b'{"a":1}').decode()
    with_bool = base64.urlsafe_b64encode(b"[true]").decode()
    for bad_cursor, arity in (("%%%", 1), (encode_cursor(key), 2), (not_list, 1), (with_bool, 1)):
        with pytest.raises(ValidationError):
            decode_cursor(bad_cursor, arity)
    with pytest.raises(ValidationError):
        encode_cursor((True,))
    assert Page((), None, 0).total == 0


def test_api006_windows_are_bounded_and_half_open() -> None:
    """API-006: windows need both ends, in UTC, in order.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    window = TimeWindow(s.T0, s.T0 + HOUR)
    assert window.duration == HOUR
    assert window.contains(s.T0)
    assert not window.contains(s.T0 + HOUR)
    for start, end in ((s.T0, s.T0), (s.T0 + HOUR, s.T0), (datetime(2026, 1, 1), s.T0)):
        with pytest.raises(ValidationError):
            TimeWindow(start, end)


def test_metrics_map_to_observation_fields() -> None:
    """10.3, STORE-012: each metric reads one field; ``None`` stays ``None``.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    sample = s.sample(DEV, IFACE, COL, s.T0, in_error_rate=0.5)
    assert Metric.IN_ERRORS.field == "in_error_rate"
    assert Metric.IN_BPS.field == "in_bps"
    assert metric_value(sample, Metric.IN_ERRORS) == 0.5
    assert metric_value(sample, Metric.OUT_PPS) is None
    assert Metric.JITTER_US.observation_type is ObservationType.ICMP
    with pytest.raises(ValidationError):
        metric_value(sample, Metric.LOSS_PCT)


def _series(**overrides: Any) -> Series:
    """Build a two-point native series, with overrides.

    Args:
        **overrides: Field values to replace.

    Returns:
        Series: The series.

    Raises:
        None
    """
    window = TimeWindow(s.T0, s.T0 + HOUR)
    base: dict[str, Any] = {
        "subject_id": IFACE, "collector_id": COL, "metric": Metric.IN_BPS, "window": window,
        "points": (SeriesPoint(s.T0, 1.0), SeriesPoint(s.T0 + s.MINUTE, None)),
        "resolutions": (SeriesResolution(SeriesSource.NATIVE, s.T0, s.T0 + HOUR),),
        "truncated": False,
    }  # fmt: skip
    return Series(**base | overrides)


def test_series_invariants() -> None:
    """STORE-012, DB-006: points are ordered, unique, inside the window; spans tile it.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert _series().points[1].value is None
    late = SeriesPoint(s.T0 + 2 * HOUR, 1.0)
    first_half = SeriesResolution(SeriesSource.NATIVE, s.T0, s.T0 + s.MINUTE)
    second_half = SeriesResolution(SeriesSource.NATIVE, s.T0 + 2 * s.MINUTE, s.T0 + HOUR)
    bad: list[dict[str, Any]] = [
        {"points": (SeriesPoint(s.T0, 1.0), SeriesPoint(s.T0, 2.0))},
        {"points": (late,)},
        {"resolutions": (first_half, second_half)},
        {"resolutions": (first_half,)},
        {"truncated": 0},
    ]
    for overrides in bad:
        with pytest.raises(ValidationError):
            _series(**overrides)
    with pytest.raises(ValidationError):
        SeriesResolution(SeriesSource.ROLLUP, s.T0, s.T0 + HOUR)


def _bucket(**overrides: Any) -> RollupBucket:
    """Build a valid rollup bucket, with overrides.

    Args:
        **overrides: Field values to replace.

    Returns:
        RollupBucket: The bucket.

    Raises:
        None
    """
    base: dict[str, Any] = {
        "subject_id": IFACE, "collector_id": COL, "metric": Metric.IN_BPS, "bucket_start": s.T0,
        "bucket_seconds": ROLLUP_BUCKET_SECONDS, "expected_count": 15, "sample_count": 15,
        "valid_count": 15, "minimum": 0.0, "mean": 1.0, "p95": 2.0, "maximum": 3.0,
    }  # fmt: skip
    return RollupBucket(**base | overrides)


def test_ret004_rollup_bucket_invariants() -> None:
    """RET-004, E.10: buckets are aligned, counts consistent, aggregates ordered and complete.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert _bucket().bucket_end == s.T0 + BUCKET
    assert bucket_start(s.T0 + 14 * s.MINUTE) == s.T0
    empty = _bucket(valid_count=0, minimum=None, mean=None, p95=None, maximum=None)
    assert empty.mean is None
    bad: list[dict[str, Any]] = [
        {"bucket_start": s.T0 + s.MINUTE},
        {"valid_count": 16},
        {"valid_count": 0},
        {"p95": None},
        {"mean": 9.0},
        {"minimum": -1.0},
        {"expected_count": -1},
    ]
    for overrides in bad:
        with pytest.raises(ValidationError):
            _bucket(**overrides)


def test_e3_freshness_threshold() -> None:
    """E.3, TIME-006: stale after max(2.5 x interval, interval + 30 s), strictly beyond.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert stale_after(60) == timedelta(seconds=150)
    assert stale_after(10) == timedelta(seconds=40)
    assert freshness_at(timedelta(seconds=150), 60) is Freshness.FRESH
    assert freshness_at(timedelta(seconds=150, microseconds=1), 60) is Freshness.STALE
    assert freshness_at(timedelta(seconds=-5), 60) is Freshness.FRESH


def test_projection_builders() -> None:
    """STATE-003: builders compute age, interval, and quality; no evidence means no state.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    now = s.T0 + s.MINUTE
    assert build_device_state(DEV, COL, icmp=None, snmp=None, poll=None, now=now) is None
    state = build_device_state(DEV, COL, icmp=None, snmp=s.snmp(DEV, COL, s.T0), poll=None, now=now)
    assert state is not None
    assert (state.age, state.effective_interval_s) == (s.MINUTE, 60)
    sample = s.sample(DEV, IFACE, COL, s.T0)
    assert (
        build_interface_state(sample, effective_interval_s=None, now=now).effective_interval_s == 60
    )
    assert latest([]) is None
    with pytest.raises(ValidationError):
        build_interface_state(sample, effective_interval_s=None, now=datetime(2026, 1, 1))
    with pytest.raises(ValidationError):
        CurrentDeviceState(DEV, COL, s.T0, timedelta(0), Freshness.FRESH, 60, Quality.NONE,
                           None, None, None)  # fmt: skip


def test_assembly_spans_gaps_and_dedup() -> None:
    """DB-006, STORE-012: spans split at the horizon; buckets fill gaps; twins collapse.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    window = TimeWindow(s.T0 + 5 * s.MINUTE, s.T0 + HOUR)
    assert [r.source for r in resolution_spans(window, s.T0, rollups=True)] == [SeriesSource.NATIVE]
    assert [r.source for r in resolution_spans(window, s.T0 + HOUR, rollups=True)] == [
        SeriesSource.ROLLUP
    ]
    assert len(resolution_spans(window, s.T0 + 30 * s.MINUTE, rollups=False)) == 1
    spans = resolution_spans(window, s.T0 + HOUR, rollups=True)
    other_width = _bucket(bucket_start=s.T0 + BUCKET, bucket_seconds=60)
    series = assemble_series(
        subject_id=IFACE,
        collector_id=COL,
        metric=Metric.IN_BPS,
        window=window,
        spans=spans,
        native=[],
        rollups=[_bucket(bucket_start=s.T0 + BUCKET), other_width],
        max_points=10,
    )
    assert [p.timestamp for p in series.points] == [s.T0 + k * BUCKET for k in (1, 2, 3)]
    assert [p.value for p in series.points] == [1.0, None, None]
    ids = sorted([new_id(CollectorId), new_id(CollectorId)])
    twins = [NativeSample(s.T0, i, float(n), Quality.NONE) for n, i in enumerate(ids)]
    native_window = TimeWindow(s.T0, s.T0 + HOUR)
    native = assemble_series(
        subject_id=IFACE,
        collector_id=COL,
        metric=Metric.IN_BPS,
        window=native_window,
        spans=resolution_spans(native_window, None, rollups=True),
        native=twins[::-1],
        rollups=[],
        max_points=1,
    )
    assert [p.value for p in native.points] == [1.0]
    assert not native.truncated
    with pytest.raises(ValidationError):
        assemble_series(subject_id=IFACE, collector_id=COL, metric=Metric.IN_BPS, window=window,
                        spans=spans, native=[], rollups=[], max_points=0)  # fmt: skip


def test_clocks() -> None:
    """TIME-001, invariant 10: clocks give aware UTC; manual time never runs backwards.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    clock = ManualClock(s.T0, monotonic_start=5.0)
    clock.advance(timedelta(seconds=1.5))
    assert (clock.now_utc(), clock.monotonic()) == (s.T0 + timedelta(seconds=1.5), 6.5)
    clock.set_utc(s.T0 - HOUR)
    assert clock.now_utc() == s.T0 - HOUR
    assert clock.monotonic() == 6.5
    with pytest.raises(ValidationError):
        clock.advance(timedelta(seconds=-1))
    with pytest.raises(ValidationError):
        ManualClock(datetime(2026, 1, 1))
    system = SystemClock()
    assert system.now_utc().tzinfo is UTC
    assert system.monotonic() <= system.monotonic()


def test_port_value_types_validate() -> None:
    """5.2 rule 5, STORE-009: capability and result objects refuse impossible values.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert WriteResult(new_id(BatchId), 2, 1).total == 3
    bad: list[Any] = [
        lambda: TelemetryCapabilities(rollups=cast(Any, 1), max_batch_rows=10),
        lambda: TelemetryCapabilities(rollups=True, max_batch_rows=0),
        lambda: WriteResult(new_id(BatchId), -1, 0),
        lambda: RetentionPolicy(timedelta(0), timedelta(days=1)),
        lambda: RetentionReport(s.T0, None, None, -1, 0),
        lambda: RetentionReport(datetime(2026, 1, 1), None, None, 0, 0),
    ]
    for build in bad:
        with pytest.raises(ValidationError):
            build()
    assert RetentionReport(s.T0, s.T0, None, 0, 0).native_cutoff == s.T0
    assert datetime.now(UTC) > s.T0
