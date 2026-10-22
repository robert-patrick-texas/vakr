"""The dashboard endpoints: define, change, delete, and view (UI-004, DB-003, SEC-018, DASH-T06).

An admin creates a dashboard with every widget kind; operators and kiosk
displays read and view it but never change it (AUTH-001 covers the whole
matrix). Writes check references, bounds, and the version, and are
audited with the admin. The definition answer matches its declared
OpenAPI model, and one ``/view`` answer carries every widget.

Dependencies:
    pytest; fastapi.testclient; tests.unit.api.api_testkit,
    tests.unit.query.dashboard_testkit.

Example:
    $ uv run pytest tests/unit/api/test_api_dashboards.py
"""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.unit.api.api_testkit import ApiKit
from tests.unit.query.dashboard_testkit import DashboardFleet, dashboard_fleet
from vakr.api.schemas_dashboards import DashboardOut
from vakr.domain.paging import PageRequest
from vakr.domain.users import Role

BOARDS = "/api/v1/dashboards"


def setup() -> tuple[ApiKit, DashboardFleet, TestClient, dict[str, str]]:
    """Build the app over the dashboard fleet and sign in an admin.

    Args:
        None

    Returns:
        tuple: The kit, the fleet, the admin's client, and its CSRF header.

    Raises:
        None
    """
    fleet = dashboard_fleet()
    kit = ApiKit.create(fleet=fleet.fleet)
    kit.add_user("adam", Role.ADMIN)
    kit.add_user("olivia", Role.OPERATOR)
    client = kit.client()
    assert kit.login(client, "adam").status_code == 200
    csrf = {"X-CSRF-Token": client.get("/api/v1/auth/session").json()["csrf_token"]}
    return kit, fleet, client, csrf


def body(fleet: DashboardFleet, name: str = "NOC core") -> dict[str, Any]:
    """Return a create body with one widget of every kind.

    Args:
        fleet: The fleet.
        name: Dashboard name.

    Returns:
        dict[str, Any]: The JSON body.

    Raises:
        None
    """
    return {"name": name, "description": "core and WAN", "refresh_s": 30, "widgets": [
        {"kind": "pinned_interfaces", "title": "Uplinks",
         "interfaces": [str(fleet.iid("core1 Gi0/1/0")), str(fleet.iid("edge1 Gi0/0/3"))]},
        {"kind": "interface_selection", "title": "Austin",
         "criteria": {"site_id": str(fleet.austin.id)}, "limit": 10},
        {"kind": "device_selection", "title": "Backbone",
         "criteria": {"group_id": str(fleet.backbone.id)}},
        {"kind": "ranking", "title": "Top WAN", "ranking": "utilization",
         "criteria": {"wan_only": True}, "window": "1h", "limit": 5},
    ]}  # fmt: skip


def test_ui004_an_admin_creates_reads_and_views_a_dashboard() -> None:
    """UI-004, DASH-T06: 201 with the definition; operators and kiosks read and view it.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit, fleet, admin, csrf = setup()
    made = admin.post(BOARDS, json=body(fleet), headers=csrf)
    assert made.status_code == 201, made.text
    out = DashboardOut.model_validate(made.json())  # the answer matches its OpenAPI model
    assert (out.version, out.managed_by, out.visibility, out.widget_schema) == (
        1,
        "api",
        "private",
        1,
    )
    assert [w.kind for w in out.widgets] == ["pinned_interfaces", "interface_selection",
                                             "device_selection", "ranking"]  # fmt: skip
    operator = kit.client()
    assert kit.login(operator, "olivia").status_code == 200
    listed = operator.get(BOARDS).json()
    assert [(r["name"], r["widgets"]) for r in listed["rows"]] == [("NOC core", 4)]
    assert listed["page"]["total"] == 1
    assert operator.get(f"{BOARDS}/{out.id}").json() == made.json()
    kiosk = {"Authorization": f"Bearer {kit.kiosk_token()}"}
    view = kit.client().get(f"{BOARDS}/{out.id}/view", headers=kiosk)
    assert view.status_code == 200
    answer = view.json()
    assert [w["kind"] for w in answer["widgets"]] == ["pinned_interfaces",
        "interface_selection", "device_selection", "ranking"]  # fmt: skip
    assert answer["refresh_s"] == 30
    assert answer["position"] == "east"
    assert answer["widgets"][3]["utilization"]["window_name"] == "1h"
    assert operator.get(f"{BOARDS}/{uuid.uuid4()}/view").status_code == 404


@pytest.mark.parametrize(
    ("change", "field"),
    [
        (lambda b: b["widgets"][0]["interfaces"].append(str(uuid.uuid4())),
         "widgets[0].interfaces"),
        (lambda b: b["widgets"][1]["criteria"].update(site_id=str(uuid.uuid4())),
         "widgets[1].criteria.site_id"),
        (lambda b: b["widgets"][2].update(criteria={}), "widgets[2].criteria"),
        (lambda b: b["widgets"][1]["criteria"].update(colour="red"), "widgets.1"),
        (lambda b: b.update(refresh_s=5), "refresh_s"),
        (lambda b: b["widgets"].extend([b["widgets"][3]] * 2), "widgets"),
        (lambda b: b.update(widgets=b["widgets"] * 4), "widgets"),
    ],
)  # fmt: skip
def test_writes_refuse_missing_inventory_and_broken_bounds_naming_the_field(
    change: Any, field: str
) -> None:
    """CFG-004, DASH-010: 422 names the field; nothing is stored.

    Args:
        change: Breaks the body.
        field: Start of the field the answer names.

    Returns:
        None

    Raises:
        None
    """
    _kit, fleet, admin, csrf = setup()
    broken = body(fleet)
    change(broken)
    answer = admin.post(BOARDS, json=broken, headers=csrf)
    assert answer.status_code == 422, answer.text
    # Body-model problems are named body.<field>; codec and reference problems by their path.
    fields = [str(f["field"]).removeprefix("body.") for f in answer.json()["fields"]]
    assert any(f.startswith(field) for f in fields), answer.json()
    assert admin.get(BOARDS).json()["page"]["total"] == 0


def test_db003_patch_needs_the_current_version_and_names_are_unique() -> None:
    """DB-003: a stale version is 409; absent fields stay; a taken name is 409.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    _kit, fleet, admin, csrf = setup()
    first = admin.post(BOARDS, json=body(fleet), headers=csrf).json()
    admin.post(BOARDS, json=body(fleet, "WAN"), headers=csrf)
    url = f"{BOARDS}/{first['id']}"
    renamed = admin.patch(url, json={"version": 1, "name": "NOC"}, headers=csrf)
    assert renamed.status_code == 200, renamed.text
    assert (renamed.json()["version"], renamed.json()["widgets"]) == (2, first["widgets"])
    stale = admin.patch(url, json={"version": 1, "description": "lost"}, headers=csrf)
    assert (stale.status_code, stale.json()["code"]) == (409, "conflict")
    taken = admin.patch(url, json={"version": 2, "name": "WAN"}, headers=csrf)
    assert taken.status_code == 409
    assert admin.post(BOARDS, json=body(fleet, "WAN"), headers=csrf).status_code == 409
    assert admin.patch(f"{BOARDS}/{uuid.uuid4()}", json={"version": 1},
                       headers=csrf).status_code == 404  # fmt: skip
    no_csrf = admin.patch(url, json={"version": 2, "name": "x"})
    assert (no_csrf.status_code, no_csrf.json()["code"]) == (403, "csrf_failed")


def test_sec018_create_change_and_delete_are_audited_with_the_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SEC-018, E.31: audit records name the admin; delete is 204 then 404; the count is bounded.

    Args:
        monkeypatch: Lowers the dashboard limit.

    Returns:
        None

    Raises:
        None
    """
    _kit, fleet, admin, csrf = setup()
    made = admin.post(BOARDS, json=body(fleet), headers=csrf).json()
    admin.patch(f"{BOARDS}/{made['id']}", json={"version": 1, "refresh_s": 120}, headers=csrf)
    assert admin.delete(f"{BOARDS}/{made['id']}", headers=csrf).status_code == 204
    assert admin.delete(f"{BOARDS}/{made['id']}", headers=csrf).status_code == 404
    assert admin.get(f"{BOARDS}/{made['id']}").status_code == 404
    with fleet.fleet.uow() as uow:
        events = [e for e in uow.audit.list_page(PageRequest(limit=500)).items
                  if e.object_type == "dashboard"]  # fmt: skip
    assert sorted((e.action, e.actor) for e in events) == [
        ("dashboard.create", "adam"),
        ("dashboard.delete", "adam"),
        ("dashboard.update", "adam"),
    ]
    assert all("NOC core" in e.summary for e in events)
    monkeypatch.setattr("vakr.dashboards.service.MAX_DASHBOARDS", 1)
    assert admin.post(BOARDS, json=body(fleet, "one"), headers=csrf).status_code == 201
    over = admin.post(BOARDS, json=body(fleet, "two"), headers=csrf)
    assert over.status_code == 422
    assert "at most 1 dashboards" in over.json()["message"]
