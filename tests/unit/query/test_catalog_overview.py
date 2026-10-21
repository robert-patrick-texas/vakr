"""Global interface list, search, WAN/high-priority views, and the overview (12.1, 12.2, 12.5).

Requirement and test IDs: UI-017 and DASH-001 (primary interfaces first),
API-005 and API-T02 (stable paging, totals, no duplicates), SEC-019 (no
patterns, bounded text), 12.1 search kinds, DASH-002 and UI-008 (WAN with
provider, circuit, contracted rate), UI-009 (browse by site, tag, group,
provider, admin state), UI-014 (unavailable feeds say so), TIME-005
(freshness counts).

Dependencies:
    pytest; tests.unit.query.history_testkit.

Example:
    $ uv run pytest tests/unit/query/test_catalog_overview.py
"""

from typing import Any

import pytest

from tests.unit.query.history_testkit import catalog, minutes, overview
from tests.unit.query.query_testkit import NOW, Fleet
from vakr.domain.enums import AdminState, InterestClass
from vakr.domain.errors import ValidationError
from vakr.domain.interfaces import InterfaceAnnotation
from vakr.domain.paging import PageRequest
from vakr.ingest.incidents import LossIncident, LossKind
from vakr.ports.metadata import InterfaceFilter
from vakr.query.catalog import CatalogQueries
from vakr.query.catalog_order import InterfaceListRequest, InterfaceSort
from vakr.query.catalog_scope import InterfaceScope
from vakr.query.overview import DeviceScope

PRIMARY = InterfaceAnnotation(interest=InterestClass.PRIMARY)


def _fleet() -> Fleet:
    """Build two devices at two sites, with primary, WAN, and ordinary interfaces.

    Args:
        None

    Returns:
        Fleet: The fleet.

    Raises:
        None
    """
    fleet = Fleet()
    dal, aus = fleet.site("Dallas DC1", "DAL1"), fleet.site("Austin", "AUS1")
    edge = fleet.device("edge1", site_id=aus.id)
    core = fleet.device("core1", site_id=dal.id)
    for n in (10, 2, 1):
        fleet.interface(core, f"Gi0/0/{n}", if_type=6, if_high_speed=1000)
    fleet.interface(edge, "Gi0/0/3", if_type=6, if_high_speed=1000, annotation=PRIMARY)
    wan = InterfaceAnnotation(
        is_wan_circuit=True, provider="ACME", circuit_id="CKT-77", contracted_bps=50_000_000
    )
    fleet.interface(
        core, "Gi0/1/0", if_type=6, if_high_speed=100, annotation=wan, if_alias="ACME uplink"
    )
    fleet.poll(core)
    return fleet


def test_ui017_primary_and_wan_first_then_natural_names() -> None:
    """UI-017, DASH-001, E.29 #6: WAN and primary interfaces lead; then natural order.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    view = catalog(_fleet()).interface_list()
    names = [(r.ref.device, r.ref.name) for r in view.rows]
    assert names == [
        ("core1", "Gi0/1/0"),
        ("edge1", "Gi0/0/3"),
        ("core1", "Gi0/0/1"),
        ("core1", "Gi0/0/2"),
        ("core1", "Gi0/0/10"),
    ]
    assert view.rows[0].ref.markers == ("WAN",)
    assert (view.total, view.next_offset, view.position) == (5, None, "east")
    primary = catalog(_fleet()).high_priority()
    assert [r.ref.name for r in primary.rows] == ["Gi0/0/3"]


def test_api005_paging_is_stable_and_bounded() -> None:
    """API-005, API-T02: offset pages cover every row once, in order, with totals.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    service = catalog(_fleet())
    pages, offset = [], 0
    while offset is not None:
        page = service.interface_list(
            InterfaceListRequest(sort=InterfaceSort.NAME, offset=offset, limit=2)
        )
        pages.append([r.ref.interface_id for r in page.rows])
        offset = page.next_offset
    flat = [i for p in pages for i in p]
    whole = service.interface_list(InterfaceListRequest(sort=InterfaceSort.NAME))
    assert flat == [r.ref.interface_id for r in whole.rows]
    assert len(set(flat)) == 5
    bad: list[dict[str, Any]] = [
        {"limit": 0},
        {"limit": 501},
        {"offset": -1},
        {"search": "x" * 257},
    ]
    for fields in bad:
        with pytest.raises(ValidationError):
            InterfaceListRequest(**fields)


def test_12_5_list_search_scope_and_measured_order() -> None:
    """12.5, UI-009: search, scope filters, and traffic order with missing values last.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = _fleet()
    service = catalog(fleet)
    rows = service.interface_list(InterfaceListRequest(search="acme")).rows
    assert [r.ref.circuit_id for r in rows] == ["CKT-77"]
    with fleet.uow() as uow:
        aus = uow.sites.get_by_name("Austin")
        busy = next(
            i
            for i in uow.interfaces.list_page(PageRequest(), InterfaceFilter()).items
            if i.if_name == "Gi0/0/2"
        )
    assert aus is not None
    scoped = service.interface_list(InterfaceListRequest(scope=InterfaceScope(site_id=aus.id)))
    assert [r.ref.site for r in scoped.rows] == ["Austin"]
    minutes(fleet, busy, range(3, 0, -1), in_bps=7e8)
    ranked = service.interface_list(InterfaceListRequest(sort=InterfaceSort.TRAFFIC))
    assert ranked.rows[0].ref.name == "Gi0/0/2"
    assert ranked.rows[0].current.in_rate.value == 7e8
    assert ranked.rows[-1].current.in_rate.value is None


def test_dash002_wan_circuits_carry_circuit_metadata() -> None:
    """DASH-002, UI-008: WAN rows name provider, circuit, contracted rate, and site.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    view = catalog(_fleet()).wan_circuits()
    assert [
        (r.ref.provider, r.ref.circuit_id, r.contracted_bps, r.ref.site) for r in view.rows
    ] == [("ACME", "CKT-77", 50_000_000, "Dallas DC1")]


def test_12_1_search_kinds_and_sec019_plain_text() -> None:
    """12.1, SEC-019, UI-017: every kind matches by substring; regex characters are literal.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    service = catalog(_fleet())
    found = service.search("ACME")
    assert {(h.kind.value, h.label) for h in found.hits} == {
        ("interface", "core1 Gi0/1/0"),
        ("provider", "ACME"),
    }
    hit = next(h for h in found.hits if h.kind.value == "interface")
    assert (hit.matched, hit.interface_id is not None) == ("if_alias", True)
    assert [h.label for h in service.search("ckt-7").hits] == ["CKT-77"]
    assert [h.kind.value for h in service.search("dal").hits] == ["site"]
    assert [h.label for h in service.search("192.0.2").hits] == ["core1", "edge1"]
    assert service.search(".*").hits == ()
    assert dict(service.search("gi0/0").totals)["interface"] == 4
    with pytest.raises(ValidationError):
        service.search("x" * 257)
    assert CatalogQueries(Fleet().uow, None).search("anything").hits == ()


def test_12_2_overview_summary_feeds_and_filters() -> None:
    """12.2, TIME-005, UI-014: counts with freshness; feeds that cannot be shown say why.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = _fleet()
    incident = LossIncident.new(
        LossKind.TORN_TAIL,
        int(NOW.timestamp() * 1e6) - 60_000_000,
        detail="a torn record was dropped",
    )
    view = overview(fleet, incidents=lambda: [incident]).overview()
    assert (view.devices.total, view.devices.fresh, view.devices.never) == (2, 1, 1)
    assert sum(s.count for s in view.devices.by_status) == 2
    feeds = {f.kind: f for f in view.events}
    assert list(feeds) == ["restart", "rediscovery", "identity_review", "telemetry_loss"]
    assert not feeds["rediscovery"].available
    assert feeds["telemetry_loss"].rows[0].text == "torn_tail: a torn record was dropped"
    assert view.filters.providers == ("ACME",)
    assert [name for _, name in view.filters.sites] == ["Austin", "Dallas DC1"]
    assert view.filters.wan_circuits == 1
    assert [r.ref.name for r in view.high_priority.rows] == ["Gi0/0/3"]
    assert view.top_utilization.window_name == "15m"
    assert view.health.telemetry_ok


def test_ui009_browse_devices_by_group() -> None:
    """UI-009: the device list filtered by site, provider, or admin state.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    fleet = _fleet()
    fleet.device("lab", admin_state=AdminState.DISABLED, poll_address="192.0.2.99")
    service = overview(fleet)
    with fleet.uow() as uow:
        dal = uow.sites.get_by_name("Dallas DC1")
    assert dal is not None
    assert [r.name for r in service.browse(DeviceScope(site_id=dal.id)).rows] == ["core1"]
    assert [r.name for r in service.browse(DeviceScope(provider="acme")).rows] == ["core1"]
    disabled = service.browse(DeviceScope(admin_state=AdminState.DISABLED))
    assert ([r.name for r in disabled.rows], disabled.total) == (["lab"], 1)
