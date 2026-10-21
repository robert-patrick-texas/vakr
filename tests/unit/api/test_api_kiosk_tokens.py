"""Kiosk token administration over HTTP: create once, list without secrets, revoke (E.12, AUTH-001).

Dependencies:
    tests.unit.api.api_testkit: The app over memory stores with a manual clock.

Example:
    $ uv run pytest tests/unit/api/test_api_kiosk_tokens.py
"""

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.unit.api.api_testkit import ApiKit
from vakr.domain.paging import PageRequest
from vakr.domain.users import Role

TOKENS = "/api/v1/kiosk-tokens"


def admin(kit: ApiKit) -> tuple[TestClient, dict[str, str]]:
    """Sign in an admin and return the client with its CSRF header.

    Args:
        kit: The app.

    Returns:
        tuple: The client and ``{"X-CSRF-Token": ...}``.

    Raises:
        None
    """
    kit.add_user("adam", Role.ADMIN)
    client = kit.client()
    assert kit.login(client, "adam").status_code == 200
    return client, {"X-CSRF-Token": client.get("/api/v1/auth/session").json()["csrf_token"]}


def create(client: TestClient, csrf: dict[str, str], **body: Any) -> Any:
    """Create a kiosk token.

    Args:
        client: The admin's client.
        csrf: The CSRF header.
        **body: ``label`` and optional ``lifetime``.

    Returns:
        Any: The response.

    Raises:
        None
    """
    return client.post(TOKENS, json=body, headers=csrf)


def test_e12_create_answers_the_token_once_and_list_never_shows_it() -> None:
    """E.12, SEC-018: 201 with the token and no-store; lists, logs, and audit never hold it.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    client, csrf = admin(kit)
    made = create(client, csrf, label="noc-wall", lifetime="90d")
    assert made.status_code == 201
    assert made.headers["cache-control"] == "no-store"
    body = made.json()
    token = body["token"]
    assert token.startswith("vkk_")
    assert body["setup_path"] == "/kiosk/setup"
    kiosk = body["kiosk"]
    assert (kiosk["label"], kiosk["created_by"], kiosk["live"]) == ("noc-wall", "adam", True)
    listed = client.get(TOKENS)
    assert listed.status_code == 200
    assert token not in listed.text
    assert "token_hash" not in listed.text
    row = listed.json()["rows"][0]
    assert set(row) == {"id", "label", "created_by", "created_at", "expires_at",
                        "last_used_at", "live"}  # fmt: skip
    assert listed.json()["page"]["total"] == 1
    record = kit.auth.kiosk.find("noc-wall")
    assert record is not None
    assert record.expires_at - record.created_at == timedelta(days=90)
    with kit.fleet.uow() as uow:
        audit = "\n".join(repr(e) for e in uow.audit.list_page(PageRequest(limit=500)).items)
    assert "kiosk.create" in audit
    assert token not in audit


def test_e12_revoke_by_id_then_404_and_the_audit_names_the_admin() -> None:
    """E.12: DELETE revokes once; a second DELETE or an unknown ID is 404.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    client, csrf = admin(kit)
    kiosk_id = create(client, csrf, label="lobby").json()["kiosk"]["id"]
    assert client.delete(f"{TOKENS}/{kiosk_id}", headers=csrf).status_code == 204
    again = client.delete(f"{TOKENS}/{kiosk_id}", headers=csrf)
    assert (again.status_code, again.json()["code"]) == (404, "not_found")
    assert client.get(TOKENS).json()["rows"] == []
    with kit.fleet.uow() as uow:
        events = uow.audit.list_page(PageRequest(limit=500)).items
    assert next((e.action, e.actor) for e in events) == ("kiosk.revoke", "adam")


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"label": "bad\tlabel"}, 422),
        ({"label": " leading-space"}, 422),
        ({"label": "x" * 65}, 422),
        ({"label": "wall", "lifetime": "400d"}, 422),
        ({"label": "wall", "lifetime": "12h"}, 422),
        ({"label": "wall", "lifetime": "soon"}, 422),
        ({"label": "wall", "extra": 1}, 422),
    ],
)
def test_e12_labels_and_lifetimes_are_bounded_and_never_echoed(
    body: dict[str, object], status: int
) -> None:
    """E.12: label rule, 1 to 365 days, no unknown fields; the input is not repeated back.

    Args:
        body: The request body.
        status: The expected status.

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    client, csrf = admin(kit)
    answer = create(client, csrf, **body)
    assert answer.status_code == status
    for value in body.values():
        if isinstance(value, str) and len(value) > 4:
            assert value not in answer.text


def test_e12_duplicate_labels_conflict_and_paging_follows_the_label_order() -> None:
    """E.12, API-005: a taken label is 409; pages follow label order with next_cursor.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    client, csrf = admin(kit)
    for label in ("c-wall", "a-wall", "b-wall"):
        assert create(client, csrf, label=label).status_code == 201
    assert create(client, csrf, label="a-wall").status_code == 409
    first = client.get(TOKENS, params={"limit": 2}).json()
    assert [r["label"] for r in first["rows"]] == ["a-wall", "b-wall"]
    assert first["page"]["total"] == 3
    rest = client.get(TOKENS, params={"limit": 2, "cursor": first["page"]["next_cursor"]}).json()
    assert [r["label"] for r in rest["rows"]] == ["c-wall"]
    assert rest["page"]["next_cursor"] is None


def test_auth001_token_management_needs_an_admin_and_the_csrf_token() -> None:
    """AUTH-001, SEC-011: operators and kiosks are refused; a cookie admin needs X-CSRF-Token.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    client, _csrf = admin(kit)
    no_csrf = client.post(TOKENS, json={"label": "wall"})
    assert (no_csrf.status_code, no_csrf.json()["code"]) == (403, "csrf_failed")
    kit.add_user("olivia", Role.OPERATOR)
    operator = kit.client()
    kit.login(operator, "olivia")
    op_csrf = {"X-CSRF-Token": operator.get("/api/v1/auth/session").json()["csrf_token"]}
    assert operator.get(TOKENS).status_code == 403
    assert operator.post(TOKENS, json={"label": "wall"}, headers=op_csrf).status_code == 403
    display = kit.client()
    display.post("/api/v1/auth/kiosk", json={"token": kit.kiosk_token("wall-x")})
    assert display.get(TOKENS).status_code == 403
    assert display.post(TOKENS, json={"label": "wall"}).status_code == 403
    socket = kit.socket_client(Role.ADMIN)
    assert socket.post(TOKENS, json={"label": "from-socket"}).status_code == 201
