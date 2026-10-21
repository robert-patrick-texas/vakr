"""Top utilization and top errors: the E.17 #13 and #15 rules (DASH-003..005, MPOS-008).

Requirement and test IDs: DASH-T01 (directional p95, denominator), DASH-T02
(valid deltas, per million), DASH-T03 (coverage and exclusions), E.17 #13
(tie reports inbound; zero errors rank last), E.17 #15 (logical interfaces
leave utilization rankings), MPOS-008 (one collector), E.28 (aligned,
reused windows), invariant 2 (telemetry trouble is not zero).

Dependencies:
    pytest; tests.unit.query.history_testkit.

Example:
    $ uv run pytest tests/unit/query/test_rankings.py
"""

from datetime import timedelta

import pytest

from tests.unit.query.history_testkit import TUNNEL, minutes, physical_fleet, rankings
from tests.unit.query.query_testkit import NOW, Fleet
from vakr.domain.enums import BaselineState
from vakr.domain.errors import ValidationError
from vakr.domain.interfaces import InterfaceAnnotation
from vakr.domain.quality import Quality
from vakr.logic.ranking import NO_BASELINE
from vakr.query.catalog_scope import InterfaceScope
from vakr.query.history_limits import RankingWindow
from vakr.query.interface_capacity import NOT_A_LINK
from vakr.query.ranking_views import RankingRequest
from vakr.query.rankings import RankingQueries

LAST_15 = range(15, 0, -1)


def _fleet() -> tuple[Fleet, RankingQueries]:
    """Build three ports, a tunnel, and a silent port with known traffic and errors.

    Args:
        None

    Returns:
        tuple: The fleet and the ranking service.

    Raises:
        None
    """
    fleet, device, tie = physical_fleet()
    out = fleet.interface(device, "Gi0/0/2", if_type=6, if_high_speed=1000)
    tunnel = fleet.interface(device, "Tunnel1", if_type=TUNNEL, if_speed=100_000)
    fleet.interface(device, "Gi0/0/9", if_type=6, if_high_speed=1000)
    minutes(
        fleet,
        tie,
        LAST_15,
        in_bps=5e8,
        out_bps=5e8,
        in_error_delta=2,
        out_error_delta=0,
        in_pps=100.0,
        out_pps=100.0,
    )
    minutes(
        fleet, out, range(10, 0, -1), in_bps=1e8, out_bps=8e8, in_error_delta=0, out_error_delta=4
    )
    minutes(fleet, tunnel, LAST_15, in_bps=9e4, out_bps=9e4, in_error_delta=0, out_error_delta=0)
    return fleet, rankings(fleet)


def test_dash_t01_directional_p95_tie_reports_inbound() -> None:
    """DASH-T01, E.17 #13: in and out p95 separately, the higher ranks, a tie reports inbound.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    _, service = _fleet()
    view = service.top_utilization()
    assert view.available
    assert view.window_name == "15m"
    assert view.window.end == NOW
    shown = [(r.rank, r.interface.name, r.score_pct, r.direction) for r in view.rows]
    assert shown == [(1, "Gi0/0/2", 80.0, "out"), (2, "Gi0/0/1", 50.0, "in")]
    first = view.rows[0]
    assert (first.capacity_bps, first.capacity_source) == (1_000_000_000, "ifHighSpeed")
    assert (first.coverage.label, first.coverage.complete) == ("10 of 15 samples", False)
    assert view.rows[1].coverage.complete


def test_dash_t03_e17_15_exclusions_carry_the_reason() -> None:
    """DASH-T03, E.17 #15: a tunnel leaves the utilization ranking; silence is not zero.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    _, service = _fleet()
    view = service.top_utilization()
    reasons = {e.interface.name: e.reason for e in view.excluded}
    assert reasons == {"Tunnel1": NOT_A_LINK, "Gi0/0/9": "no samples from east in the window"}
    assert (view.candidates, view.excluded_total) == (4, 2)


def test_e17_15_capacity_override_lets_a_tunnel_rank() -> None:
    """E.17 #15: ``capacity_override_bps`` makes a logical interface a utilization denominator.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet, device, _ = physical_fleet()
    tunnel = fleet.interface(
        device,
        "Tunnel9",
        if_type=TUNNEL,
        if_speed=100_000,
        annotation=InterfaceAnnotation(capacity_override_bps=10_000_000),
    )
    minutes(fleet, tunnel, LAST_15, in_bps=5e6, out_bps=1e6)
    view = rankings(fleet).top_utilization()
    assert [(r.interface.name, r.score_pct, r.capacity_source) for r in view.rows] == [
        ("Tunnel9", 50.0, "capacity override")
    ]


def test_dash_t02_errors_per_million_and_zero_errors_last() -> None:
    """DASH-T02, E.17 #13: most errors first; per million only with packets; zeros at the bottom.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    _, service = _fleet()
    view = service.top_errors()
    shown = [(r.interface.name, r.errors, r.per_million) for r in view.rows]
    assert shown == [
        ("Gi0/0/2", 40, None),
        ("Gi0/0/1", 30, 30 * 1e6 / (15 * 200 * 60)),
        ("Tunnel1", 0, None),
    ]
    assert [e.interface.name for e in view.excluded] == ["Gi0/0/9"]


def test_dash005_invalid_baselines_never_count() -> None:
    """DASH-005, RATE-004: samples without a baseline are left out, never ranked as zero.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet, _, iface = physical_fleet()
    minutes(
        fleet,
        iface,
        LAST_15,
        in_bps=None,
        out_bps=None,
        baseline_state=BaselineState.INVALIDATED,
        quality=Quality.BASELINE_UPTIME_RESET,
    )
    service = rankings(fleet)
    assert service.top_utilization().rows == ()
    assert [e.reason for e in service.top_utilization().excluded] == [NO_BASELINE]
    assert [e.reason for e in service.top_errors().excluded] == [NO_BASELINE]


def test_mpos008_rankings_use_one_collector() -> None:
    """MPOS-008: a ranking is one position's evidence; counters are never summed.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet, _, iface = physical_fleet(("east", "west"))
    minutes(fleet, iface, LAST_15, position="east", in_bps=1e8)
    minutes(fleet, iface, LAST_15, position="west", in_bps=3e8)
    service = rankings(fleet)
    east = service.top_utilization()
    assert (east.position, [r.score_pct for r in east.rows]) == ("east", [10.0])
    west_id = fleet.collectors["west"].id
    west = service.top_utilization(RankingRequest(collector_id=west_id))
    assert (west.position, [r.score_pct for r in west.rows]) == ("west", [30.0])
    local = rankings(fleet, local_collector=fleet.collectors["west"].name).top_utilization()
    assert local.position == "west"


def test_e28_aligned_windows_reuse_one_scan() -> None:
    """E.28: requests within one alignment step share a scan; the next step reads again.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet, _, iface = physical_fleet()
    minutes(fleet, iface, LAST_15, in_bps=1e8)
    service = rankings(fleet)
    first = service.top_utilization(RankingRequest(window=RankingWindow.H24))
    fleet.clock.advance(timedelta(seconds=40))
    again = service.top_errors(RankingRequest(window=RankingWindow.H24))
    assert again.computed_at == first.computed_at
    assert again.window.end == first.window.end
    fleet.clock.advance(timedelta(minutes=5))
    later = service.top_utilization(RankingRequest(window=RankingWindow.H24))
    assert later.window.end == first.window.end + timedelta(minutes=5)
    assert later.computed_at != first.computed_at


def test_ui008_scope_filters_and_request_bounds() -> None:
    """UI-008, DASH-006, SEC-019: provider/WAN/site scopes narrow candidates; limits are bounded.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet, device, iface = physical_fleet()
    wan = fleet.interface(
        device,
        "Gi0/0/5",
        if_type=6,
        if_high_speed=100,
        annotation=InterfaceAnnotation(is_wan_circuit=True, provider="ACME"),
    )
    minutes(fleet, iface, LAST_15, in_bps=1e8)
    minutes(fleet, wan, LAST_15, in_bps=5e7)
    service = rankings(fleet)
    by_provider = service.top_utilization(RankingRequest(scope=InterfaceScope(provider="acme")))
    assert [r.interface.name for r in by_provider.rows] == ["Gi0/0/5"]
    assert by_provider.candidates == 1
    wan_only = service.top_utilization(RankingRequest(scope=InterfaceScope(wan_only=True), limit=1))
    assert [r.rank for r in wan_only.rows] == [1]
    for bad in (0, 101):
        with pytest.raises(ValidationError):
            RankingRequest(limit=bad)


def test_invariant2_ranking_without_telemetry_or_collectors() -> None:
    """Invariant 2, UI-014: no telemetry store or no collector is "unavailable", never zero.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet, _, _ = physical_fleet()
    service = RankingQueries(fleet.uow, None, clock=fleet.clock)
    view = service.top_utilization()
    assert (view.available, view.rows) == (False, ())
    assert view.reason == "telemetry unavailable: no telemetry database yet"
    empty = RankingQueries(Fleet(()).uow, None).top_errors()
    assert empty.reason == "no collector has been registered yet"
