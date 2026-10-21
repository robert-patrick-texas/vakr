"""AUTH-003: CSRF protection of every state change from a browser, and CORS off (SEC-011, 15.4).

A cookie-authenticated POST needs the session's own ``X-CSRF-Token``; the
socket and kiosk tokens need none; a browser that says the request is
cross-site, or names another origin, is refused whatever it carries. No
answer ever has an ``Access-Control-Allow-*`` header.

Dependencies:
    tests.unit.api.api_testkit: The app over memory stores.

Example:
    $ uv run pytest tests/unit/api/test_api_csrf.py
"""

import uuid

from fastapi.testclient import TestClient

from tests.unit.api.api_testkit import ApiKit
from tests.unit.auth.auth_testkit import new_password
from vakr.domain.users import Role

RESOLVE = f"/api/v1/interfaces/{uuid.uuid4()}/identity-resolution"
BODY = {"review_id": str(uuid.uuid4()), "decision": "new"}


def signed_in(kit: ApiKit, name: str) -> tuple[TestClient, str]:
    """Sign an admin in and read the session's CSRF token.

    Args:
        kit: The app.
        name: Username to create.

    Returns:
        tuple[TestClient, str]: The client and its CSRF token.

    Raises:
        None
    """
    kit.add_user(name, Role.ADMIN)
    client = kit.client()
    assert kit.login(client, name).status_code == 200
    return client, client.get("/api/v1/auth/session").json()["csrf_token"]


def test_auth003_cookie_posts_need_the_sessions_own_token() -> None:
    """AUTH-003: missing, wrong, and another session's token are refused; the right one passes.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    client, token = signed_in(kit, "adam")
    _, other = signed_in(kit, "eve")
    for headers in ({}, {"X-CSRF-Token": "forged"}, {"X-CSRF-Token": other},
                    {"X-CSRF-Token": "x" * 500}):  # fmt: skip
        refused = client.post(RESOLVE, json=BODY, headers=headers)
        assert (refused.status_code, refused.json()["code"]) == (403, "csrf_failed")
    passed = client.post(RESOLVE, json=BODY, headers={"X-CSRF-Token": token})
    assert passed.status_code == 404  # past CSRF and authorization: the interface is unknown
    logout = client.post("/api/v1/auth/logout")
    assert logout.status_code == 403  # logout is a state change too


def test_auth003_cross_site_browsers_are_refused_even_with_a_token() -> None:
    """AUTH-003, 15.4: Sec-Fetch-Site cross-site or a foreign Origin refuses any state change.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    client, token = signed_in(kit, "adam")
    for extra in ({"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
                  {"Origin": "https://evil.example"}, {"Origin": "null"}):  # fmt: skip
        refused = client.post(RESOLVE, json=BODY, headers={"X-CSRF-Token": token, **extra})
        assert (refused.status_code, refused.json()["code"]) == (403, "cross_site")
    same = {"X-CSRF-Token": token, "Origin": "http://vakr.test", "Sec-Fetch-Site": "same-origin"}
    assert client.post(RESOLVE, json=BODY, headers=same).status_code == 404
    body = {"username": "a", "password": new_password()}
    evil = {"Origin": "https://evil.example"}
    login = kit.client().post("/api/v1/auth/login", json=body, headers=evil)
    assert login.status_code == 403  # no login CSRF either


def test_auth003_socket_and_kiosk_need_no_csrf_and_reads_never_do() -> None:
    """AUTH-003: no cookie, nothing to forge; GET is never a state change (15.4).

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    socket = kit.socket_client(Role.ADMIN).post(RESOLVE, json=BODY)
    assert socket.status_code == 404
    token = kit.kiosk_token()
    kiosk = kit.client().post(RESOLVE, json=BODY, headers={"Authorization": f"Bearer {token}"})
    assert kiosk.status_code == 403
    assert kiosk.json()["code"] == "forbidden"  # the role, not CSRF, refuses a kiosk
    client, _ = signed_in(kit, "adam")
    assert (
        client.get("/api/v1/devices", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200
    )


def test_sec011_cors_is_disabled() -> None:
    """SEC-011: no CORS headers on any answer, preflight included.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    client = kit.client()
    origin = {"Origin": "https://elsewhere.example"}
    preflight = client.options("/api/v1/devices", headers={
        **origin, "Access-Control-Request-Method": "GET"})  # fmt: skip
    plain = client.get("/api/v1/health/live", headers=origin)
    for answer in (preflight, plain):
        assert not [h for h in answer.headers if h.lower().startswith("access-control-")]
    assert preflight.status_code == 405
