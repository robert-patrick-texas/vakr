"""Manual rediscovery over the API: immediate answer, progress, result, audit (DISC-T03, API-T06).

The request answers before any discovery runs (DISC-010); the operation
then moves as a collector would move it (``CollectorOperations`` on the
same store): queued, running, succeeded with its counts. A second request
joins the active operation; devices no collector would discover, unknown
devices, a full queue, and non-admins are refused; a request no collector
picks up within an hour expires with the reason (Appendix E.32).

Dependencies:
    pytest; tests.unit.api.api_testkit; vakr.operations.

Example:
    $ uv run pytest tests/unit/api/test_api_operations.py
"""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.unit.api.api_testkit import ApiKit
from vakr.domain.enums import AdminState, ReachabilityStrategy
from vakr.domain.ids import OperationId
from vakr.domain.inventory import Device
from vakr.domain.operations import OperationResult
from vakr.domain.paging import PageRequest
from vakr.domain.users import Role
from vakr.operations import CollectorOperations, limits
from vakr.ports.metadata import AuditFilter

RESULT = OperationResult(24, new=1, missing=2, reviews_opened=1, duration_ms=1800)


def admin(kit: ApiKit) -> tuple[TestClient, dict[str, str]]:
    """Sign in an admin and return the client with its CSRF header.

    Args:
        kit: The kit.

    Returns:
        tuple: The client and ``{"X-CSRF-Token": ...}``.

    Raises:
        None
    """
    kit.add_user("adam", Role.ADMIN)
    client = kit.client()
    assert kit.login(client, "adam").status_code == 200
    csrf = client.get("/api/v1/auth/session").json()["csrf_token"]
    return client, {"X-CSRF-Token": csrf}


def rediscover(client: TestClient, csrf: dict[str, str], device: Device) -> Any:
    """Ask for a rediscovery.

    Args:
        client: A signed-in client.
        csrf: Its CSRF header.
        device: The device.

    Returns:
        Any: The response.

    Raises:
        None
    """
    return client.post(f"/api/v1/devices/{device.id}/rediscovery", headers=csrf)


def test_disc_t03_the_answer_is_immediate_and_follows_the_operation() -> None:
    """DISC-T03, API-T06, DISC-010: 202 with the ID at once; progress and result follow.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    core = kit.fleet.device("core1", credential_ref="lab")
    client, csrf = admin(kit)
    asked = rediscover(client, csrf, core)
    assert asked.status_code == 202
    op = asked.json()
    assert (op["state"], op["device_name"], op["requested_by"]) == ("queued", "core1", "adam")
    assert asked.headers["location"] == f"/api/v1/operations/{op['id']}"
    again = rediscover(client, csrf, core)
    assert (again.status_code, again.json()["id"]) == (200, op["id"])  # joined, not duplicated
    east = kit.fleet.collectors["east"].id
    runs = CollectorOperations(kit.fleet.uow, kit.fleet.clock, east)
    (queued,) = runs.pending()
    claimed = runs.claim(queued)
    assert claimed is not None
    assert client.get(asked.headers["location"]).json()["state"] == "running"
    kit.fleet.clock.advance(timedelta(seconds=2))
    runs.finish(claimed, RESULT, None)
    done = client.get(asked.headers["location"]).json()
    assert done["state"] == "succeeded"
    assert done["collector_id"] == str(east)
    assert done["result"]["interfaces"] == 24
    assert done["result"]["missing"] == 2
    assert done["error"] is None
    assert done["finished_at"] is not None
    feeds = {f["kind"]: f for f in client.get("/api/v1/overview").json()["events"]}
    (event,) = feeds["rediscovery"]["rows"]  # 12.2 item 7
    assert event["subject"] == "core1"
    assert event["position"] == "east"
    assert event["text"].startswith("manual rediscovery succeeded: 24 interfaces")
    with kit.fleet.uow() as uow:
        audit = uow.audit.list_page(PageRequest(), AuditFilter(object_id=core.id)).items
    events = [e for e in audit if e.action == "device.rediscover"]
    assert {e.actor for e in events} == {"adam"}
    assert len(events) == 2
    assert {"queued", "joined"} == {e.summary.split(": ")[1].split()[0] for e in events}


@pytest.mark.parametrize(
    ("overrides", "says"),
    [
        ({"admin_state": AdminState.DISABLED}, "is disabled"),
        ({"admin_state": AdminState.RETIRED}, "is retired"),
        ({}, "no SNMP credential"),
        ({"reachability": ReachabilityStrategy.ICMP_ONLY}, "icmp_only"),
    ],
)
def test_disc003_devices_no_collector_would_discover_are_refused(
    overrides: dict[str, object], says: str
) -> None:
    """DISC-003: 409 ``refused`` with the reason, and nothing queued.

    Args:
        overrides: Device fields.
        says: Part of the reason.

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    fields = {"credential_ref": "lab", **overrides} if overrides else {}
    device = kit.fleet.device("edge1", **fields)
    client, csrf = admin(kit)
    answer = rediscover(client, csrf, device)
    assert (answer.status_code, answer.json()["code"]) == (409, "refused")
    assert says in answer.json()["message"]
    assert client.get("/api/v1/operations").json()["page"]["total"] == 0


def test_unknown_devices_full_queues_and_non_admins_are_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E.32, SEC-006: 404 unknown device, 409 beyond ``MAX_ACTIVE``, 403 operator and no CSRF.

    Args:
        monkeypatch: Shrinks the bound.

    Returns:
        None

    Raises:
        None
    """
    monkeypatch.setattr(limits, "MAX_ACTIVE", 1)
    kit = ApiKit.create()
    one = kit.fleet.device("core1", credential_ref="lab")
    two = kit.fleet.device("core2", credential_ref="lab", poll_address="192.0.2.2")
    client, csrf = admin(kit)
    gone = client.post(f"/api/v1/devices/{uuid.uuid4()}/rediscovery", headers=csrf)
    assert gone.status_code == 404
    assert rediscover(client, csrf, one).status_code == 202
    full = rediscover(client, csrf, two)
    assert full.status_code == 409
    assert "already queued or running" in full.json()["message"]
    assert client.post(f"/api/v1/devices/{one.id}/rediscovery").status_code == 403  # no CSRF
    kit.add_user("olivia", Role.OPERATOR)
    reader = kit.client()
    kit.login(reader, "olivia")
    token = reader.get("/api/v1/auth/session").json()["csrf_token"]
    refused = reader.post(f"/api/v1/devices/{one.id}/rediscovery",
                          headers={"X-CSRF-Token": token})  # fmt: skip
    assert refused.status_code == 403
    assert reader.get("/api/v1/operations").json()["page"]["total"] == 1
    socket = kit.socket_client(Role.ADMIN)
    joined = socket.post(f"/api/v1/devices/{one.id}/rediscovery")
    assert (joined.status_code, joined.json()["requested_by"]) == (200, "adam")


def test_e32_unclaimed_requests_expire_and_lists_filter() -> None:
    """E.32, API-005: an hour without a collector expires it; lists filter by device and state.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    one = kit.fleet.device("core1", credential_ref="lab")
    two = kit.fleet.device("core2", credential_ref="lab", poll_address="192.0.2.2")
    client, csrf = admin(kit)
    first = rediscover(client, csrf, one).json()
    kit.fleet.clock.advance(limits.PICKUP_LIMIT + timedelta(seconds=1))
    client, csrf = kit.socket_client(Role.ADMIN), {}  # the web session went idle meanwhile
    stale = client.get(f"/api/v1/operations/{first['id']}").json()
    assert (stale["state"], stale["error"]) == ("expired", limits.EXPIRED_REASON)
    second = rediscover(client, csrf, one)
    assert second.status_code == 202
    assert second.json()["id"] != first["id"]
    rediscover(client, csrf, two)
    mine = client.get("/api/v1/operations", params={"device": str(one.id)}).json()
    assert [r["id"] for r in mine["rows"]] == [second.json()["id"], first["id"]]
    expired = client.get("/api/v1/operations", params={"state": "expired"}).json()
    assert [r["id"] for r in expired["rows"]] == [first["id"]]
    paged = client.get("/api/v1/operations", params={"limit": 2}).json()
    assert paged["page"]["total"] == 3
    assert paged["page"]["next_cursor"]
    following = {"limit": 2, "cursor": paged["page"]["next_cursor"]}
    rest = client.get("/api/v1/operations", params=following).json()
    assert len(rest["rows"]) == 1
    missing = client.get(f"/api/v1/operations/{OperationId(uuid.uuid4())}")
    assert missing.status_code == 404
