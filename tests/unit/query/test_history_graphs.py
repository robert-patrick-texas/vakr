"""Interface graphs: gaps, source choice, bounds, positions, utilization, markers (10.6, 12.6).

Requirement and test IDs: STORE-012, UI-010, E.19 gap points; ROLL-T05 (query
side); STATE-005 and the 10.6 table; API-006, API-T04, SEC-019 bounds;
MPOS-008 one series per position; E.17 #15 utilization only on links;
invariant 2 (storage trouble is never target state).

Dependencies:
    pytest; tests.unit.query.history_testkit.

Example:
    $ uv run pytest tests/unit/query/test_history_graphs.py
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tests.unit.query.history_testkit import TUNNEL, history, minutes, physical_fleet
from tests.unit.query.query_testkit import NOW
from vakr.domain.enums import BaselineState
from vakr.domain.errors import ValidationError
from vakr.domain.ids import InterfaceId
from vakr.domain.interfaces import InterfaceAnnotation
from vakr.domain.quality import Quality
from vakr.domain.rollups import ROLLUP_BUCKET_SECONDS
from vakr.domain.series import Metric
from vakr.domain.state import RollupBucket
from vakr.query.history_limits import RangePreset
from vakr.query.history_requests import GraphKind, GraphRequest, SourceChoice, TimeRange
from vakr.query.history_views import MarkerKind
from vakr.query.interface_capacity import NOT_A_LINK

H1 = GraphRequest(TimeRange(RangePreset.H1))


def test_store012_e19_native_outage_is_an_explicit_gap() -> None:
    """STORE-012, UI-010, E.19: a step over 1.5 intervals gets a None point; 60 s steps do not.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet, _, iface = physical_fleet()
    minutes(fleet, iface, range(59, 0, -1), skip=frozenset(range(20, 31)), in_bps=1000.0)
    graph = history(fleet).interface_graph(iface.id, GraphKind.TRAFFIC, H1)
    assert graph is not None
    assert graph.available
    inbound = graph.series[0]
    assert inbound.metric == "in_bps"
    assert inbound.resolutions[0].bucket_seconds is None
    values = [p.value for p in inbound.points]
    assert 0.0 not in values
    assert values.count(None) == inbound.gaps == 1
    gap = next(p for p in inbound.points if p.value is None)
    assert gap.t == NOW - timedelta(minutes=31) + timedelta(seconds=60)
    marker = next(m for m in inbound.markers if m.kind is MarkerKind.GAP)
    assert (marker.start, marker.end) == (gap.t, NOW - timedelta(minutes=19))
    assert len(inbound.points) == 59 - 11 + 1


def test_roll_t05_rollup_gaps_stay_visible() -> None:
    """ROLL-T05, STORE-012: a 90-day graph reads rollups; empty buckets are None, not zero.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet, _, iface = physical_fleet()
    col = fleet.collectors["east"].id
    start = NOW - timedelta(days=90)
    width = timedelta(seconds=ROLLUP_BUCKET_SECONDS)
    buckets = [
        RollupBucket(
            iface.id,
            col,
            Metric.IN_BPS,
            start + k * width,
            ROLLUP_BUCKET_SECONDS,
            15,
            15,
            15,
            minimum=1.0,
            mean=2.0,
            p95=3.0,
            maximum=4.0,
        )
        for k in (*range(8), *range(16, 24))
    ]
    fleet.telemetry().rollups.write(buckets)
    graph = history(fleet).interface_graph(
        iface.id, GraphKind.TRAFFIC, GraphRequest(TimeRange(RangePreset.D90))
    )
    assert graph is not None
    inbound = graph.series[0]
    assert [(r.source, r.bucket_seconds) for r in inbound.resolutions] == [("rollup", 7200)]
    first, second, third = inbound.points[:3]
    assert (first.value, first.low, first.high) == (2.0, 1.0, 4.0)
    assert second.value is None
    assert third.value == 2.0
    assert any(m.kind is MarkerKind.GAP and m.start == second.t for m in inbound.markers)


@pytest.mark.parametrize(
    ("preset", "source", "expected"),
    [
        (RangePreset.H1, SourceChoice.AUTO, ("native", None)),
        (RangePreset.H6, SourceChoice.AUTO, ("native", None)),
        (RangePreset.H24, SourceChoice.AUTO, ("native", 120)),
        (RangePreset.D7, SourceChoice.AUTO, ("native", 600)),
        (RangePreset.D30, SourceChoice.AUTO, ("native", 3600)),
        (RangePreset.D90, SourceChoice.AUTO, ("rollup", 7200)),
        (RangePreset.D400, SourceChoice.AUTO, ("rollup", 43200)),
        (RangePreset.H24, SourceChoice.ROLLUP, ("rollup", 900)),
        (RangePreset.D90, SourceChoice.NATIVE, ("native", 7200)),
        (RangePreset.H1, SourceChoice.NATIVE, ("native", None)),
    ],
)
def test_state005_source_follows_the_10_6_table(
    preset: RangePreset, source: SourceChoice, expected: tuple[str, int | None]
) -> None:
    """STATE-005, 10.6: auto source per preset, overrides honoured, and both disclosed.

    Args:
        preset: The range preset.
        source: Requested source.
        expected: ``(source, bucket width)`` of the single span.

    Returns:
        None

    Raises:
        None
    """
    fleet, _, iface = physical_fleet()
    graph = history(fleet).interface_graph(
        iface.id, GraphKind.TRAFFIC, GraphRequest(TimeRange(preset), source)
    )
    assert graph is not None
    assert graph.source == source.value
    spans = graph.series[0].resolutions
    assert [(s.source, s.bucket_seconds) for s in spans] == [expected]
    assert graph.window.preset == preset.value
    assert graph.window.timezone == "UTC"
    assert graph.window.end == NOW


def test_api006_ranges_and_caps_are_bounded() -> None:
    """API-006, API-T04, SEC-019: bad ranges and caps are refused, never widened.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet, _, iface = physical_fleet()
    service = history(fleet)
    day = datetime(2026, 10, 19, tzinfo=UTC)
    bad = [
        TimeRange(start=day - timedelta(days=401), end=day),
        TimeRange(start=day, end=day + timedelta(seconds=30)),
        TimeRange(start=day.replace(tzinfo=None), end=day + timedelta(hours=1)),
        TimeRange(start=NOW, end=NOW + timedelta(hours=1)),
        TimeRange(start=day),
    ]
    for rng in bad:
        with pytest.raises(ValidationError):
            service.interface_graph(iface.id, GraphKind.TRAFFIC, GraphRequest(rng))
    for cap in (0, 1201):
        with pytest.raises(ValidationError):
            GraphRequest(max_points=cap)
    custom = GraphRequest(TimeRange(start=day, end=day + timedelta(hours=2)), max_points=10)
    graph = service.interface_graph(iface.id, GraphKind.TRAFFIC, custom)
    assert graph is not None
    assert (graph.window.start, graph.window.preset) == (day, None)
    assert [s.resolutions[0].bucket_seconds for s in graph.series] == [900, 900]
    assert not graph.truncated


def test_mpos008_one_series_per_position_never_merged() -> None:
    """MPOS-008, 27.5 #6: every position is its own series; the local one is the default.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet, _, iface = physical_fleet(("east", "west"))
    minutes(fleet, iface, range(5), position="east", in_bps=100.0)
    minutes(fleet, iface, range(5), position="west", in_bps=900.0)
    both = history(fleet).interface_graph(iface.id, GraphKind.TRAFFIC, H1)
    assert both is not None
    inbound = [s for s in both.series if s.metric == "in_bps"]
    assert {(s.position, s.points[-1].value) for s in inbound} == {("east", 100.0), ("west", 900.0)}
    assert len({s.collector_id for s in inbound}) == 2
    local = history(fleet, local_collector="east").interface_graph(iface.id, request=H1)
    assert local is not None
    assert {s.position for s in local.series} == {"east"}
    west = fleet.collectors["west"].id
    picked = history(fleet).interface_graph(
        iface.id, request=GraphRequest(TimeRange(RangePreset.H1), collector_id=west)
    )
    assert picked is not None
    assert {s.position for s in picked.series} == {"west"}


def test_e17_15_utilization_needs_a_link_and_lines_show_capacity() -> None:
    """E.17 #15, RATE-008, 12.6: tunnels get traffic but no percent; ports get lines and percent.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet, device, iface = physical_fleet()
    tunnel = fleet.interface(device, "Tunnel1", if_type=TUNNEL, if_speed=100_000)
    contracted = fleet.interface(
        device,
        "Gi0/0/2",
        if_type=6,
        if_high_speed=1000,
        annotation=InterfaceAnnotation(contracted_bps=200_000_000),
    )
    minutes(fleet, iface, range(3), in_bps=250_000_000.0)
    service = history(fleet)
    util = service.interface_graph(iface.id, GraphKind.UTILIZATION, H1)
    assert util is not None
    assert util.available
    assert util.unit.base == "%"
    assert util.series[0].points[-1].value == 25.0
    blocked = service.interface_graph(tunnel.id, GraphKind.UTILIZATION, H1)
    assert blocked is not None
    assert (blocked.available, blocked.reason) == (False, NOT_A_LINK)
    traffic = service.interface_graph(tunnel.id, GraphKind.TRAFFIC, H1)
    assert traffic is not None
    assert traffic.available
    assert traffic.references == ()
    lines = service.interface_graph(contracted.id, GraphKind.TRAFFIC, H1)
    assert lines is not None
    assert [(r.kind, r.label) for r in lines.references] == [
        ("capacity", "Capacity 1 Gbit/s (ifHighSpeed)"),
        ("contracted", "Contracted 200 Mbit/s"),
    ]
    percent = service.interface_graph(contracted.id, GraphKind.UTILIZATION, H1)
    assert percent is not None
    assert [r.value for r in percent.references] == [20.0]


def test_12_6_quality_markers_and_flags() -> None:
    """12.6, RATE-004: restart, discontinuity, partial, and collector trouble become markers.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet, _, iface = physical_fleet()
    invalid: dict[str, Any] = {
        "in_bps": None,
        "out_bps": None,
        "baseline_state": BaselineState.INVALIDATED,
    }
    minutes(fleet, iface, range(9, 7, -1), in_bps=5.0)
    minutes(fleet, iface, range(7, 6, -1), quality=Quality.BASELINE_UPTIME_RESET, **invalid)
    minutes(fleet, iface, range(6, 5, -1), quality=Quality.BASELINE_DISCONTINUITY, **invalid)
    minutes(fleet, iface, range(5, 3, -1), in_bps=5.0, quality=Quality.PARTIAL_RESPONSE)
    minutes(fleet, iface, range(3, 2, -1), in_bps=5.0, quality=Quality.COLLECTOR_DEGRADED)
    graph = history(fleet).interface_graph(iface.id, GraphKind.TRAFFIC, H1)
    assert graph is not None
    inbound = graph.series[0]
    kinds = [m.kind for m in inbound.markers if m.kind is not MarkerKind.GAP]
    assert kinds == [
        MarkerKind.RESTART,
        MarkerKind.DISCONTINUITY,
        MarkerKind.PARTIAL,
        MarkerKind.COLLECTOR,
    ]
    partial = next(m for m in inbound.markers if m.kind is MarkerKind.PARTIAL)
    assert partial.end - partial.start == timedelta(minutes=2)
    restart = next(p for p in inbound.points if "baseline_uptime_reset" in p.flags)
    assert restart.value is None


def test_invariant2_storage_trouble_and_unknown_subjects() -> None:
    """Invariant 2, UI-014: no telemetry store reads as unavailable, never as an idle link.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet, device, iface = physical_fleet()
    graph = history(fleet, telemetry=False).interface_graph(iface.id, GraphKind.PACKETS)
    assert graph is not None
    assert (graph.available, graph.series) == (False, ())
    assert graph.reason == "telemetry unavailable: no telemetry database yet"
    assert history(fleet).interface_graph(InterfaceId(device.id)) is None


def test_12_6_si_units_follow_the_largest_value() -> None:
    """12.6: SI network units, one per graph (``Gbit/s``, ``kpkt/s``); values stay in base units.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet, _, iface = physical_fleet()
    minutes(fleet, iface, range(3), in_bps=1_500_000_000.0, out_bps=5.0, in_pps=1500.0, out_pps=2.0)
    service = history(fleet)
    traffic = service.interface_graph(iface.id, GraphKind.TRAFFIC, H1)
    packets = service.interface_graph(iface.id, GraphKind.PACKETS, H1)
    assert traffic is not None
    assert packets is not None
    assert (traffic.unit.display, traffic.unit.divisor) == ("Gbit/s", 1e9)
    assert traffic.series[0].points[-1].value == 1_500_000_000.0
    assert packets.unit.display == "kpkt/s"
