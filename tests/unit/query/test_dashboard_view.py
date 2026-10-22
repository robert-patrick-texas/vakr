"""A custom dashboard resolved into its view (UI-004, DASH-006, DASH-010, DASH-T04, UI-014).

Every widget kind is answered by the services the built-in pages use, in
the admin's order, within its bounds, and for one position (MPOS-008). A
pinned interface that is retired or gone keeps its place with a note.
Telemetry trouble keeps the rows with missing values, never zeros.

Dependencies:
    pytest; tests.unit.query.dashboard_testkit, history_testkit.

Example:
    $ uv run pytest tests/unit/query/test_dashboard_view.py
"""

from collections.abc import Iterable
from dataclasses import replace
from uuid import UUID

import pytest

from tests.contract.contract_dashboards import dashboard
from tests.unit.query.dashboard_testkit import DashboardFleet, dashboard_fleet, every_widget
from tests.unit.query.history_testkit import UTC_SETTINGS
from vakr.domain.dashboard_widgets import (
    DeviceCriteria,
    DeviceSelection,
    InterfaceCriteria,
    InterfaceSelection,
    PinnedInterfaces,
    Widget,
)
from vakr.domain.enums import InterestClass
from vakr.domain.ids import DashboardId, new_id
from vakr.query import to_jsonable
from vakr.query.dashboard_views import (
    GONE,
    RETIRED,
    DeviceWidgetView,
    PinnedWidgetView,
    RankingWidgetView,
    SelectionWidgetView,
)
from vakr.query.dashboards import DashboardQueries
from vakr.query.overview import OverviewQueries


def queries(kit: DashboardFleet, *, telemetry: bool = True) -> DashboardQueries:
    """Return the dashboard queries over the fleet.

    Args:
        kit: The fleet.
        telemetry: False for a node without a telemetry store.

    Returns:
        DashboardQueries: The service.

    Raises:
        None
    """
    f = kit.fleet
    tele = f.telemetry if telemetry else None
    return DashboardQueries(f.uow, OverviewQueries(f.uow, tele, UTC_SETTINGS, f.clock), f.clock)


def keys(kit: DashboardFleet, ids: Iterable[UUID]) -> list[str]:
    """Name interfaces by ``"<device> <ifName>"``.

    Args:
        kit: The fleet.
        ids: Interface UUIDs.

    Returns:
        list[str]: Their keys.

    Raises:
        None
    """
    by_id = {v.id: k for k, v in kit.interfaces.items()}
    return [by_id.get(i, "?") for i in ids]


def test_ui004_every_widget_resolves_in_order_with_retired_and_gone_in_place() -> None:
    """UI-004: pinned order is kept; retired and missing interfaces keep their place.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = dashboard_fleet()
    view = queries(kit).resolve(dashboard(widgets=every_widget(kit)))
    pinned, chosen, devices, ranking = view.widgets
    assert isinstance(pinned, PinnedWidgetView)
    assert [e.note for e in pinned.entries] == [None, RETIRED, GONE, None]
    live, retired, gone, wan = pinned.entries
    assert live.row is not None
    assert live.row.ref.name == "Gi0/0/3"
    assert live.row.current.in_rate.value == pytest.approx(10_000_000.0)
    assert retired.row is None
    assert retired.ref is not None
    assert retired.ref.name == "Gi0/0/9"
    assert (gone.row, gone.ref) == (None, None)
    assert wan.row is not None
    assert wan.row.ref.markers == ("WAN",)
    assert isinstance(chosen, SelectionWidgetView)
    assert (len(chosen.rows), chosen.total, chosen.more) == (1, 2, 1)
    assert chosen.criteria.text == "site Austin"
    assert isinstance(devices, DeviceWidgetView)
    assert [r.name for r in devices.rows] == ["core1", "edge1"]
    assert (devices.total, devices.more, devices.criteria.group) == (2, 0, "backbone")
    assert isinstance(ranking, RankingWidgetView)
    assert ranking.utilization is None
    assert ranking.errors is not None
    assert ranking.errors.candidates == 1  # only the WAN circuit may rank
    assert (view.position, view.telemetry.value, view.truncated) == ("east", "ok", False)


@pytest.mark.parametrize(
    ("criteria", "expected"),
    [
        (InterfaceCriteria(tag_id=None, provider="acme"), ["core1 Gi0/1/0"]),
        (InterfaceCriteria(wan_only=True), ["core1 Gi0/1/0"]),
        (InterfaceCriteria(interest=InterestClass.PRIMARY), ["edge1 Gi0/0/3"]),
        (InterfaceCriteria(site_id=None, group_id=None, wan_only=False,
                           interest=InterestClass.SECONDARY, provider=None),
         ["core1 Gi0/1/0", "core1 Gi0/0/1", "core1 Gi0/0/2", "edge2 Gi0/0/4"]),
    ],
)  # fmt: skip
def test_dash_t04_interface_selections_filter_and_group(
    criteria: InterfaceCriteria, expected: list[str]
) -> None:
    """DASH-T04, DASH-006: provider, WAN, and interest filters pick the right interfaces.

    Args:
        criteria: The filters.
        expected: Interfaces in priority order.

    Returns:
        None

    Raises:
        None
    """
    kit = dashboard_fleet()
    view = queries(kit).resolve(dashboard(widgets=(InterfaceSelection("x", criteria, 100),)))
    (only,) = view.widgets
    assert isinstance(only, SelectionWidgetView)
    assert keys(kit, [r.ref.interface_id for r in only.rows]) == expected


def test_dash_t04_tags_groups_and_sites_select_devices_and_their_interfaces() -> None:
    """DASH-T04, UI-004: tag, group, and site views list the right devices and interfaces.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = dashboard_fleet()
    widgets: tuple[Widget, ...] = (
        InterfaceSelection("Tagged", InterfaceCriteria(tag_id=kit.core_tag.id)),
        InterfaceSelection("Backbone", InterfaceCriteria(group_id=kit.backbone.id)),
        DeviceSelection("Austin", DeviceCriteria(site_id=kit.austin.id)),
        DeviceSelection("Tagged", DeviceCriteria(tag_id=kit.core_tag.id)),
    )
    tagged, backbone, austin, tagged_devices = queries(kit).resolve(
        dashboard(widgets=widgets)).widgets  # fmt: skip
    assert isinstance(tagged, SelectionWidgetView)
    assert isinstance(backbone, SelectionWidgetView)
    assert keys(kit, [r.ref.interface_id for r in tagged.rows]) == ["edge1 Gi0/0/3"]
    assert sorted(keys(kit, [r.ref.interface_id for r in backbone.rows])) == [
        "core1 Gi0/0/1", "core1 Gi0/0/2", "core1 Gi0/1/0", "edge1 Gi0/0/3"]  # fmt: skip
    assert backbone.criteria.text == "group backbone"
    assert isinstance(austin, DeviceWidgetView)
    assert isinstance(tagged_devices, DeviceWidgetView)
    assert sorted(r.name for r in austin.rows) == ["edge1", "edge2"]
    assert [r.name for r in tagged_devices.rows] == ["edge1"]


def test_ui014_without_telemetry_rows_stay_with_missing_values_never_zero() -> None:
    """UI-014, invariant 1: no telemetry store; inventory rows stay, values are missing.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = dashboard_fleet()
    view = queries(kit, telemetry=False).resolve(dashboard(widgets=every_widget(kit)))
    assert view.telemetry.value != "ok"
    pinned = view.widgets[0]
    assert isinstance(pinned, PinnedWidgetView)
    row = pinned.entries[0].row
    assert row is not None
    assert row.current.in_rate.value is None
    assert row.current.in_rate.text != "0"


def test_dash010_answers_stay_within_the_widget_bounds_and_unknown_is_none() -> None:
    """DASH-010: every part is bounded by its widget; an unknown dashboard is ``None``.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = dashboard_fleet()
    every = tuple(kit.interfaces[k].id for k in sorted(kit.interfaces))
    dallas = InterfaceCriteria(site_id=kit.dallas.id)
    wide = (PinnedInterfaces("All", every, graphs=True), InterfaceSelection("One", dallas, limit=1))
    service = queries(kit)
    view = service.resolve(dashboard(widgets=wide))
    pinned, one = view.widgets
    assert isinstance(pinned, PinnedWidgetView)
    assert len(pinned.entries) == len(every)
    # Dallas has three live interfaces; the retired Gi0/0/9 is left out, as on every list.
    assert isinstance(one, SelectionWidgetView)
    assert (len(one.rows), one.more) == (1, 2)
    assert service.view(new_id(DashboardId)) is None
    board = dashboard(widgets=wide)
    with kit.fleet.uow() as uow:
        uow.dashboards.add(board)
        uow.commit()
    stored = service.view(board.id)
    assert stored is not None
    assert to_jsonable(stored) == to_jsonable(replace(view, dashboard_id=board.id, name=board.name))
