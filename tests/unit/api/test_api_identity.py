"""IFID-007 and SEC-018 over HTTP: identity reviews, the admin's decision, and its audit trail.

Dependencies:
    tests.unit.api.api_testkit; tests.contract.contract_metadata_identity (review builder).

Example:
    $ uv run pytest tests/unit/api/test_api_identity.py
"""

import uuid

from tests.contract.contract_metadata_identity import review
from tests.unit.api.api_testkit import ApiKit
from vakr.domain.paging import PageRequest
from vakr.domain.users import Role


def seeded() -> tuple[ApiKit, str, str, str]:
    """Build one device with an interface and an open review naming it.

    Args:
        None

    Returns:
        tuple: The kit, the device UUID, the interface UUID, and the review UUID.

    Raises:
        None
    """
    kit = ApiKit.create()
    device = kit.fleet.device("edge1")
    iface = kit.fleet.interface(device, "Gi0/0/5", if_index=5)
    case = kit.fleet.add("reviews", review(device.id, (iface.id,)))
    return kit, str(device.id), str(iface.id), str(case.id)


def test_ifid007_admin_maps_a_review_with_audit_and_history() -> None:
    """IFID-007, SEC-018: the decision continues history, closes the review, and is audited.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit, device_id, iface_id, review_id = seeded()
    kit.add_user("adam", Role.ADMIN)
    client = kit.client()
    kit.login(client, "adam")
    csrf = client.get("/api/v1/auth/session").json()["csrf_token"]
    listed = client.get("/api/v1/identity-reviews", params={"device": device_id}).json()
    assert [r["id"] for r in listed["items"]] == [review_id]
    assert listed["items"][0]["candidates"][0]["interface_id"] == iface_id
    url = f"/api/v1/interfaces/{iface_id}/identity-resolution"
    body = {"review_id": review_id, "decision": "map"}
    done = client.post(url, json=body, headers={"X-CSRF-Token": csrf})
    assert done.status_code == 200
    assert done.json() == {"review_id": review_id, "decision": "map", "interface_id": iface_id,
                           "if_index": 5}  # fmt: skip
    again = client.post(url, json=body, headers={"X-CSRF-Token": csrf})
    assert (again.status_code, again.json()["code"]) == (409, "conflict")
    assert client.get("/api/v1/identity-reviews").json()["page"]["total"] == 0
    history = client.get(f"/api/v1/interfaces/{iface_id}/identity-history").json()
    assert history["items"][-1]["source"] == "admin"
    assert history["items"][-1]["actor"] == "adam"
    with kit.fleet.uow() as uow:
        actions = [(e.action, e.actor) for e in uow.audit.list_page(PageRequest(limit=50)).items]
    assert ("interface.review.mapped", "adam") in actions


def test_ifid007_new_identity_from_the_socket_and_refusals() -> None:
    """IFID-007: ``new`` creates an identity; another device's interface is refused; 404s.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit, _, iface_id, review_id = seeded()
    other = kit.fleet.interface(kit.fleet.device("edge2"), "Gi0/0/1")
    admin = kit.socket_client(Role.ADMIN)
    wrong = admin.post(f"/api/v1/interfaces/{other.id}/identity-resolution",
                       json={"review_id": review_id, "decision": "map"})  # fmt: skip
    assert (wrong.status_code, wrong.json()["code"]) == (409, "conflict")
    unknown = admin.post(f"/api/v1/interfaces/{uuid.uuid4()}/identity-resolution",
                         json={"review_id": review_id, "decision": "new"})  # fmt: skip
    assert unknown.status_code == 404
    made = admin.post(f"/api/v1/interfaces/{iface_id}/identity-resolution",
                      json={"review_id": review_id, "decision": "new"})  # fmt: skip
    assert made.status_code == 200
    assert made.json()["interface_id"] != iface_id
    with kit.fleet.uow() as uow:
        actors = {e.actor for e in uow.audit.list_page(PageRequest(limit=50)).items}
    assert "local:tester" in actors
    bad = admin.post(f"/api/v1/interfaces/{iface_id}/identity-resolution",
                     json={"review_id": review_id, "decision": "maybe"})  # fmt: skip
    assert bad.status_code == 422
    operator = kit.socket_client(Role.OPERATOR)
    refused = operator.post(f"/api/v1/interfaces/{iface_id}/identity-resolution",
                            json={"review_id": review_id, "decision": "new"})  # fmt: skip
    assert (refused.status_code, refused.json()["code"]) == (403, "forbidden")
