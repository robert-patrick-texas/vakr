"""Latency smoke, state timelines, export, and JSON views (12.4-12.6, MPOS-008, API-012).

Requirement and test IDs: MPOS-008 and MPOS-T04 (one latency series per
position, never merged); ICMP-005 (a lost probe is no value, never 0);
12.6 smoke modes; 12.5 state timeline with E.19 gaps; API-012 and API-T09
(bounded export with timezone, units, and quality); TIME-004 (ISO UTC).

Dependencies:
    pytest; tests.unit.query.history_testkit.

Example:
    $ uv run pytest tests/unit/query/test_history_latency.py
"""

import csv
import io
import json
from datetime import timedelta
from typing import Any

import pytest

from tests.contract import contract_samples as samples
from tests.unit.query.history_testkit import history, minutes, physical_fleet
from tests.unit.query.query_testkit import NOW, Fleet
from vakr.domain.enums import IfAdminStatus, IfOperStatus
from vakr.domain.errors import ValidationError
from vakr.domain.ids import DeviceId
from vakr.query import history_limits
from vakr.query.export import COLUMNS, ExportFormat, export_view
from vakr.query.history_limits import RangePreset
from vakr.query.history_requests import GraphKind, GraphRequest, TimeRange
from vakr.query.jsonable import to_jsonable
from vakr.query.latency_views import SmokeMode

H1 = GraphRequest(TimeRange(RangePreset.H1))


def _probe(fleet: Fleet, device_id: DeviceId, position: str, ago_min: int, base: int) -> None:
    """Write one ICMP cycle with one lost probe.

    Args:
        fleet: The fleet.
        device_id: The device.
        position: The collector's position.
        ago_min: Minutes before ``NOW``.
        base: RTT of the fastest reply, microseconds.

    Returns:
        None

    Raises:
        None
    """
    col = fleet.collectors[position].id
    rtts = (base, None, base + 200, base + 100, base + 100)
    fleet.write(
        samples.icmp(
            device_id,
            col,
            NOW - timedelta(minutes=ago_min),
            rtts_us=rtts,
            min_rtt_us=base,
            avg_rtt_us=base + 100,
            median_rtt_us=base + 100,
            max_rtt_us=base + 200,
        )
    )


def test_mpos008_latency_is_per_position_with_probe_smoke() -> None:
    """MPOS-008, MPOS-T04, ICMP-005: east and west stay separate; lost probes are None.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet, device, _ = physical_fleet(("east", "west"))
    for m in range(1, 4):
        _probe(fleet, device.id, "east", m, 1_000)
        _probe(fleet, device.id, "west", m, 60_000)
    view = history(fleet).device_latency(device.id, H1)
    assert view is not None
    assert view.available
    assert [p.position for p in view.positions] == ["east", "west"]
    east, west = view.positions
    assert east.smoke_mode is SmokeMode.PROBES
    assert east.probes[-1].rtts_us == (1_000, None, 1_200, 1_100, 1_100)
    assert east.probes[-1].loss_pct == 20.0
    assert [p.value for p in west.median.points] == [60_100.0] * 3
    assert {p.value for p in east.median.points} == {1_100.0}
    assert (view.rtt_unit.display, view.loss_unit.base) == ("ms", "%")
    assert view.notes[0].startswith("Smoke: every probe")


def test_12_6_long_windows_show_a_median_envelope() -> None:
    """12.6, RET-006: beyond 1,200 polls the smoke is the band of per-poll medians, noted.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet, device, _ = physical_fleet()
    _probe(fleet, device.id, "east", 5, 2_000)
    _probe(fleet, device.id, "east", 6, 4_000)
    view = history(fleet).device_latency(device.id, GraphRequest(TimeRange(RangePreset.D7)))
    assert view is not None
    position = view.positions[0]
    assert (position.smoke_mode, position.probes) == (SmokeMode.MEDIAN_ENVELOPE, ())
    filled = [p for p in position.median.points if p.value is not None]
    assert [(p.value, p.low, p.high) for p in filled] == [(3_100.0, 2_100.0, 4_100.0)]
    assert any("rollups keep" in n for n in view.notes)
    assert history(fleet).device_latency(DeviceId(fleet.collectors["east"].id)) is None


def test_12_5_state_timeline_with_gaps() -> None:
    """12.5, E.19: status segments merge equal samples; an outage is a segment without samples.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet, _, iface = physical_fleet()
    up: dict[str, Any] = {"admin_status": IfAdminStatus.UP, "oper_status": IfOperStatus.UP}
    minutes(fleet, iface, range(59, 49, -1), **up)
    minutes(
        fleet,
        iface,
        range(49, 39, -1),
        admin_status=IfAdminStatus.UP,
        oper_status=IfOperStatus.LOWER_LAYER_DOWN,
    )
    minutes(fleet, iface, range(20, 0, -1), **up)
    view = history(fleet).interface_timeline(iface.id, H1)
    assert view is not None
    assert view.available
    shown = [(s.oper, s.samples) for s in view.segments]
    assert shown == [("up", 10), ("lower-layer-down", 10), (None, 0), ("up", 20)]
    assert view.segments[0].start == NOW - timedelta(hours=1)
    assert view.segments[-1].end == NOW
    assert view.segments[2].start == NOW - timedelta(minutes=39)
    week = history(fleet).interface_timeline(iface.id, GraphRequest(TimeRange(RangePreset.D30)))
    assert week is not None
    assert week.window.end - week.window.start == timedelta(days=7)


def test_api012_csv_and_json_export() -> None:
    """API-012, API-T09, TIME-004: UTC times, zone, unit, source, flags; gaps are empty cells.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet, _, iface = physical_fleet()
    minutes(fleet, iface, range(5, 0, -1), skip=frozenset({3}), in_bps=1_000.0)
    graph = history(fleet).interface_graph(iface.id, GraphKind.TRAFFIC, H1)
    assert graph is not None
    exported = export_view(graph, ExportFormat.CSV)
    rows = list(csv.reader(io.StringIO(exported.body.decode())))
    assert tuple(rows[0]) == COLUMNS
    inbound = [r for r in rows[1:] if r[1] == "in_bps"]
    assert inbound[0][:2] == ["2026-10-20T11:55:00Z", "in_bps"]
    assert [r[4] for r in inbound] == ["1000.0", "1000.0", "", "1000.0", "1000.0"]
    assert {(r[7], r[8], r[10]) for r in inbound} == {("bit/s", "native", "UTC")}
    assert exported.filename.endswith(".csv")
    assert exported.media_type.startswith("text/csv")
    as_json = json.loads(export_view(graph, ExportFormat.JSON).body)
    assert as_json == to_jsonable(graph)
    assert as_json["window"]["end"] == "2026-10-20T12:00:00Z"
    assert as_json["series"][0]["points"][2]["value"] is None


def test_api012_oversized_export_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """API-012, SEC-019: an export over the E.28 row bound is refused, not cut silently.

    Args:
        monkeypatch: Lowers the bound for the test.

    Returns:
        None

    Raises:
        None
    """
    fleet, _, iface = physical_fleet()
    minutes(fleet, iface, range(5, 0, -1), in_bps=1.0)
    graph = history(fleet).interface_graph(iface.id, GraphKind.TRAFFIC, H1)
    assert graph is not None
    monkeypatch.setattr("vakr.query.export.EXPORT_MAX_ROWS", 3)
    with pytest.raises(ValidationError):
        export_view(graph, ExportFormat.CSV)
    assert history_limits.EXPORT_MAX_ROWS == 20_000
