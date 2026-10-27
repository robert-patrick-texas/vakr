"""The manual inventory endpoints: devices, annotations, and bulk tags (27.4, META-008, MPOS-003).

An admin adds, changes, and retires a device and annotates and tags
interfaces; operators read the records but never change them (AUTH-001
covers the whole matrix). Refusals use the envelope with one entry per
field: 422 for the file's rules, 409 ``managed_by_file`` naming the file,
409 ``conflict`` for a stale version, and 409 ``peer_read_only`` on a peer.
Answers match their declared OpenAPI models, and the interface lists offer
the "not yet classified" filter (META-013).

Dependencies:
    pytest; fastapi.testclient; tests.unit.api.api_testkit.

Example:
    $ uv run pytest tests/unit/api/test_api_inventory_edits.py
"""

from typing import Any

from fastapi.testclient import TestClient

from tests.unit.api.api_testkit import ApiKit
from vakr.api.schemas_inventory import AnnotationOut, DeviceRecordOut, TagChangeOut
from vakr.config.node import NodeSettings
from vakr.domain.paging import PageRequest
from vakr.domain.users import Role
from vakr.inventory.model import FILE_OWNER
from vakr.inventory.validate import Checks

DEVICES = "/api/v1/devices"


def setup() -> tuple[ApiKit, TestClient, dict[str, str]]:
    """Build the app with one credential name and sign in an admin.

    Args:
        None

    Returns:
        tuple: The kit, the admin's client, and its CSRF header.

    Raises:
        None
    """
    kit = ApiKit.create()
    kit.context.inventory_checks = lambda: Checks(frozenset({"network-v2c"}))
    kit.add_user("adam", Role.ADMIN)
    kit.add_user("olivia", Role.OPERATOR)
    client = kit.client()
    assert kit.login(client, "adam").status_code == 200
    csrf = {"X-CSRF-Token": client.get("/api/v1/auth/session").json()["csrf_token"]}
    return kit, client, csrf


def fields_of(answer: Any) -> list[str]:
    """Return the field paths of an error envelope.

    Args:
        answer: The response.

    Returns:
        list[str]: Field paths, sorted.

    Raises:
        None
    """
    return sorted(f["field"] for f in answer.json()["fields"])


def test_27_4_an_admin_adds_changes_and_retires_a_device() -> None:
    """27.4, META-009, DB-003, SEC-018: 201, PATCH with version, stale 409, DELETE retires.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit, admin, csrf = setup()
    body = {"name": "edge9.example.net", "address": "192.0.2.9", "credential": "network-v2c",
            "tags": ["wan"], "interval": "2m", "reachability": "snmp_fallback",
            "address_family": "ipv4"}  # fmt: skip
    made = admin.post(DEVICES, json=body, headers=csrf)
    assert made.status_code == 201, made.text
    out = DeviceRecordOut.model_validate(made.json())
    assert (out.managed_by, out.editable, out.version, out.interval, out.warnings) == (
        "api", True, 1, "120s", [])  # fmt: skip
    assert (out.reachability, out.address_family) == ("snmp_fallback", "ipv4")
    url = f"{DEVICES}/{out.id}"
    operator = kit.client()
    assert kit.login(operator, "olivia").status_code == 200
    assert operator.get(f"{url}/record").json() == {**made.json(), "changes": []}
    assert operator.get(f"{DEVICES}/{out.id}").json()["row"]["name"] == "edge9.example.net"
    patch = admin.patch(url, json={"version": 1, "interval": None, "comment": "x"}, headers=csrf)
    assert patch.status_code == 200, patch.text
    assert (patch.json()["version"], patch.json()["interval"]) == (2, None)
    assert patch.json()["changes"] == ["interval_s: 120 -> none", "comment changed"]
    stale = admin.patch(url, json={"version": 1, "comment": "y"}, headers=csrf)
    assert (stale.status_code, stale.json()["code"]) == (409, "conflict")
    gone = admin.delete(url, params={"version": 2}, headers=csrf)
    assert (gone.status_code, gone.json()["admin_state"]) == (200, "retired")
    back = admin.patch(url, json={"version": 3, "admin_state": "enabled"}, headers=csrf)
    assert back.json()["admin_state"] == "enabled"
    with kit.fleet.uow() as uow:
        events = uow.audit.list_page(PageRequest(50)).items
    actions = [e.action for e in events if e.action.startswith("device.")]
    assert sorted(actions) == ["device.create", "device.retire", "device.update", "device.update"]
    assert {e.actor for e in events if e.action.startswith("device.")} == {"adam"}
    assert all(e.request_id for e in events if e.action.startswith("device."))


def test_meta008_refusals_name_each_field() -> None:
    """META-008, CONF-008, API-007: 422 by field, 409 managed_by_file naming the file.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit, admin, csrf = setup()
    filed = kit.fleet.device("edge1.example.net", poll_address="192.0.2.10", managed_by=FILE_OWNER)
    bad = admin.post(DEVICES, json={"name": "edge1.example.net", "address": "192.0.2.10",
                                    "credential": "missing"}, headers=csrf)  # fmt: skip
    assert (bad.status_code, bad.json()["code"]) == (422, "invalid_request")
    assert fields_of(bad) == ["body.address", "body.credential", "body.name"]
    typo = admin.post(DEVICES, json={"name": "x", "address": "192.0.2.1", "adress": "y"},
                      headers=csrf)  # fmt: skip
    assert typo.status_code == 422
    owned = admin.patch(
        f"{DEVICES}/{filed.id}",
        json={"version": 1, "address": "192.0.2.11", "tags": ["x"]},
        headers=csrf,
    )
    assert (owned.status_code, owned.json()["code"]) == (409, "managed_by_file")
    assert "inventory.toml" in owned.json()["message"]
    assert fields_of(owned) == ["body.address", "body.tags"]
    assert {f["message"] for f in owned.json()["fields"]} == {"managed by inventory.toml"}
    record = admin.get(f"{DEVICES}/{filed.id}/record").json()
    assert (record["editable"], record["managed_by_source"]) == (False, "inventory.toml")
    assert admin.delete(f"{DEVICES}/{filed.id}", headers=csrf).json()["code"] == "managed_by_file"
    assert admin.get(f"{DEVICES}/00000000-0000-7000-8000-000000000000/record").status_code == 404


def test_sec011_writes_need_admin_and_the_csrf_token() -> None:
    """SEC-006, SEC-011: an operator is refused; a missing CSRF token is refused.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit, admin, _ = setup()
    body = {"name": "edge9", "address": "192.0.2.9"}
    assert admin.post(DEVICES, json=body).status_code == 403
    operator = kit.client()
    assert kit.login(operator, "olivia").status_code == 200
    token = {"X-CSRF-Token": operator.get("/api/v1/auth/session").json()["csrf_token"]}
    assert operator.post(DEVICES, json=body, headers=token).status_code == 403
    assert kit.socket_client(Role.ADMIN).post(DEVICES, json=body).status_code == 201


def test_mpos003_a_peer_refuses_every_edit_naming_the_primary() -> None:
    """MPOS-003, MPOS-T03: 409 peer_read_only for device, annotation, and tag writes.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit, admin, csrf = setup()
    device = kit.fleet.device("edge1", poll_address="192.0.2.10")
    iface = kit.fleet.interface(device, "Gi0/0/0")
    kit.context.node = NodeSettings("peer", "west", "https://vakr-east.example.net")
    writes = [
        ("POST", DEVICES, {"name": "edge9", "address": "192.0.2.9"}),
        ("PATCH", f"{DEVICES}/{device.id}", {"version": 1, "comment": "x"}),
        ("DELETE", f"{DEVICES}/{device.id}", None),
        ("PATCH", f"/api/v1/interfaces/{iface.id}/annotation", {"version": 1}),
        ("POST", "/api/v1/interfaces/tags", {"interfaces": [str(iface.id)], "add": ["x"]}),
    ]
    for method, url, body in writes:
        answer = admin.request(method, url, json=body, headers=csrf)
        assert (answer.status_code, answer.json()["code"]) == (409, "peer_read_only"), url
        assert "vakr-east.example.net" in answer.json()["message"]
    assert admin.get(f"/api/v1/interfaces/{iface.id}/annotation").status_code == 200


def test_meta013_annotate_tag_and_find_the_unclassified() -> None:
    """META-007, META-011, META-013: PATCH with the alias, bulk tags, the unclassified filter.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit, admin, csrf = setup()
    device = kit.fleet.device("edge1", poll_address="192.0.2.10")
    ports = [kit.fleet.interface(device, f"Gi0/0/{n}", if_index=n + 1) for n in range(3)]
    claimed = kit.fleet.interface(device, "Gi0/0/9", if_index=10, managed_by=FILE_OWNER)
    url = f"/api/v1/interfaces/{ports[0].id}/annotation"
    read = admin.get(url)
    assert AnnotationOut.model_validate(read.json()).classified is False
    changed = admin.patch(url, json={"version": 1, "high_priority": True, "provider": "Example",
                                     "circuit_id": "CKT-1"}, headers=csrf)  # fmt: skip
    assert changed.status_code == 200, changed.text
    assert (changed.json()["interest"], changed.json()["classified"]) == ("primary", True)
    wrong = admin.patch(url, json={"version": 2, "interest": "secondary", "high_priority": True},
                        headers=csrf)  # fmt: skip
    assert (wrong.status_code, fields_of(wrong)) == (422, ["body.high_priority"])
    tagged = admin.post(
        "/api/v1/interfaces/tags",
        headers=csrf,
        json={"interfaces": [str(ports[1].id)], "add": ["uplink"]},
    )
    assert TagChangeOut.model_validate(tagged.json()).changed == [ports[1].id]
    refused = admin.post("/api/v1/interfaces/tags", headers=csrf, json={
        "interfaces": [str(ports[2].id), str(claimed.id)], "add": ["uplink"]})  # fmt: skip
    assert (refused.status_code, fields_of(refused)) == (409, ["body.interfaces[1]"])
    left = admin.get("/api/v1/interfaces", params={"unclassified": True}).json()["rows"]
    assert sorted(r["ref"]["name"] for r in left) == ["Gi0/0/2", "Gi0/0/9"]
    mine = admin.get(f"{DEVICES}/{device.id}/interfaces", params={"unclassified": True}).json()
    assert sorted(r["ref"]["name"] for r in mine["rows"]) == ["Gi0/0/2", "Gi0/0/9"]
