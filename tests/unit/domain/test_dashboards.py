"""Dashboard records, widget bounds, and the widget codec with its upgrades (DASH-007, DASH-010).

Every bound of Appendix E.31 is refused one past its limit; the codec
round-trips every widget kind, refuses unknown kinds and fields with the
field path, and upgrades older widget schemas step by step while refusing
newer ones.

Dependencies:
    pytest; tests.contract.contract_dashboards; vakr.domain.

Example:
    $ uv run pytest tests/unit/domain/test_dashboards.py
"""

from typing import Any

import pytest

from tests.contract.contract_dashboards import dashboard
from vakr.domain import dashboard_upgrades, dashboards
from vakr.domain.dashboard_codec import widgets_from_json, widgets_to_json
from vakr.domain.dashboard_upgrades import upgrade
from vakr.domain.dashboard_widgets import (
    MAX_PINNED,
    MAX_SELECTION_ROWS,
    DeviceCriteria,
    DeviceSelection,
    InterfaceCriteria,
    InterfaceSelection,
    PinnedInterfaces,
    RankingKind,
    RankingWidget,
)
from vakr.domain.dashboards import MAX_WIDGETS
from vakr.domain.errors import ValidationError
from vakr.domain.ids import InterfaceId, SiteId, new_id

SITE = new_id(SiteId)


def pins(n: int) -> tuple[InterfaceId, ...]:
    """Return ``n`` fresh interface IDs.

    Args:
        n: How many.

    Returns:
        tuple[InterfaceId, ...]: The IDs.

    Raises:
        None
    """
    return tuple(new_id(InterfaceId) for _ in range(n))


@pytest.mark.parametrize(
    "build",
    [
        lambda: PinnedInterfaces("x", ()),
        lambda: PinnedInterfaces("x", pins(MAX_PINNED + 1)),
        lambda: PinnedInterfaces("x", (pins(1)[0],) * 2),
        lambda: PinnedInterfaces(" x", pins(1)),
        lambda: PinnedInterfaces("x" * 81, pins(1)),
        lambda: InterfaceSelection("x", InterfaceCriteria()),
        lambda: InterfaceSelection("x", InterfaceCriteria(site_id=SITE), MAX_SELECTION_ROWS + 1),
        lambda: InterfaceSelection("x", InterfaceCriteria(provider="p" * 129)),
        lambda: DeviceSelection("x", DeviceCriteria()),
        lambda: RankingWidget("x", RankingKind.ERRORS, window="2h"),
        lambda: RankingWidget("x", RankingKind.ERRORS, limit=26),
        lambda: dashboard(name="n" * 81),
        lambda: dashboard(description="d" * 501),
        lambda: dashboard(refresh_s=14),
        lambda: dashboard(refresh_s=3601),
        lambda: dashboard(widgets=(PinnedInterfaces("x", pins(1)),) * (MAX_WIDGETS + 1)),
        lambda: dashboard(widgets=(RankingWidget("x", RankingKind.ERRORS),) * 3),
        lambda: dashboard(version=0),
    ],
)  # fmt: skip
def test_dash010_every_bound_is_refused_one_past_its_limit(build: Any) -> None:
    """DASH-010, E.31: widgets and dashboards refuse anything past their bounds.

    Args:
        build: Builds a record one past a bound.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValidationError):
        build()


def test_dash007_the_codec_round_trips_every_widget_kind() -> None:
    """DASH-007: encoding then decoding gives the same widgets; absent fields take defaults.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    widgets = dashboard().widgets
    assert widgets_from_json(1, widgets_to_json(widgets)) == widgets
    (short,) = widgets_from_json(1, [{"kind": "ranking", "title": "t", "ranking": "errors"}])
    assert short == RankingWidget("t", RankingKind.ERRORS)


@pytest.mark.parametrize(
    ("value", "field"),
    [
        ({}, "widgets"),
        ([{"kind": "chart", "title": "t"}], "widgets[0].kind"),
        ([{"kind": "pinned_interfaces", "title": "t", "interfaces": ["x"]}],
         "widgets[0].interfaces"),
        ([{"kind": "pinned_interfaces", "title": "t", "interfaces": [], "extra": 1}],
         "widgets[0]"),
        ([{"kind": "interface_selection", "title": "t", "criteria": {"colour": "red"}}],
         "widgets[0].criteria"),
        ([{"kind": "interface_selection", "title": "t", "criteria": {"interest": "low"}}],
         "widgets[0].criteria"),
        ([{"kind": "ranking", "title": "t", "ranking": "latency"}], "widgets[0].ranking"),
        ([{"kind": "device_selection", "title": "t", "criteria": {"site_id": str(SITE)}},
          {"kind": "ranking", "title": "", "ranking": "errors"}], "widgets[1]"),
    ],
)  # fmt: skip
def test_dash007_the_codec_refuses_with_the_field_path(value: Any, field: str) -> None:
    """DASH-007: unknown kinds, fields, and values are refused, naming where.

    Args:
        value: The widget JSON.
        field: Start of the field the error names.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValidationError) as exc:
        widgets_from_json(1, value)
    assert (exc.value.field or "").startswith(field), exc.value.field


def test_dash007_older_schemas_upgrade_step_by_step_and_newer_ones_are_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DASH-007: steps run in order up to the current schema; a gap or a newer schema fails.

    Args:
        monkeypatch: Pretends this release writes widget schema 3.

    Returns:
        None

    Raises:
        None
    """
    steps = dashboard_upgrades.UPGRADES
    monkeypatch.setattr(dashboards, "WIDGET_SCHEMA", 3)
    monkeypatch.setitem(steps, 1, lambda ws: [{**w, "title": "a"} for w in ws])
    monkeypatch.setitem(steps, 2, lambda ws: [{**w, "title": w["title"] + "b"}
                                                                 for w in ws])  # fmt: skip
    old = [{"kind": "ranking", "title": "t", "ranking": "errors"}]
    assert upgrade(1, old) == [{"kind": "ranking", "title": "ab", "ranking": "errors"}]
    assert upgrade(3, old) == old
    for bad in (4, 0, True, "1"):
        with pytest.raises(ValidationError):
            upgrade(bad, old)  # ty: ignore[invalid-argument-type]
    monkeypatch.delitem(steps, 2)
    with pytest.raises(ValidationError, match="no upgrade from widget schema 2"):
        upgrade(1, old)
