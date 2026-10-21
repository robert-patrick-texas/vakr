"""AUTH-001: the role authorization matrix of every API v1 route (SEC-005, SEC-006, E.12, 3.1).

Every route is called by every kind of caller: anonymous, an operator and
an admin signed in with a cookie, a kiosk display with a bearer token, and
Unix-socket peers mapped to admin, operator, or refused. The expected
decision comes from ``LEVELS``, which lists every route explicitly, so a
route added without a declared access level fails here. "Allowed" means
the request got past authorization: any answer but 401 and 403 (an unknown
UUID answers 404, which is fine).

Dependencies:
    tests.unit.api.api_testkit: The app over memory stores.

Example:
    $ uv run pytest tests/unit/api/test_api_authz.py
"""

import uuid

import pytest

from tests.unit.api.api_testkit import ApiKit
from tests.unit.auth.auth_testkit import new_password
from vakr.domain.users import Role

ANON, VIEW, ADMIN = "anonymous", "operator", "admin"
LEVELS = {
    ("GET", "/api/v1/version"): VIEW,
    ("GET", "/api/v1/health/live"): ANON,
    ("GET", "/api/v1/health/ready"): ANON,
    ("GET", "/api/v1/health/detail"): VIEW,
    ("POST", "/api/v1/auth/login"): ANON,
    ("POST", "/api/v1/auth/logout"): ANON,
    ("GET", "/api/v1/auth/session"): VIEW,
    ("GET", "/api/v1/devices"): VIEW,
    ("GET", "/api/v1/devices/{device_uuid}"): VIEW,
    ("GET", "/api/v1/device-lookup"): VIEW,
    ("GET", "/api/v1/devices/{device_uuid}/interfaces"): VIEW,
    ("GET", "/api/v1/devices/{device_uuid}/history/reachability"): VIEW,
    ("GET", "/api/v1/interfaces"): VIEW,
    ("GET", "/api/v1/interfaces/{interface_uuid}"): VIEW,
    ("GET", "/api/v1/interfaces/{interface_uuid}/history/status"): VIEW,
    ("GET", "/api/v1/interfaces/{interface_uuid}/history/traffic"): VIEW,
    ("GET", "/api/v1/interfaces/{interface_uuid}/history/packets"): VIEW,
    ("GET", "/api/v1/interfaces/{interface_uuid}/history/errors"): VIEW,
    ("GET", "/api/v1/interfaces/{interface_uuid}/history/utilization"): VIEW,
    ("GET", "/api/v1/interfaces/{interface_uuid}/identity-history"): VIEW,
    ("POST", "/api/v1/interfaces/{interface_uuid}/identity-resolution"): ADMIN,
    ("GET", "/api/v1/identity-reviews"): VIEW,
    ("GET", "/api/v1/sites"): VIEW,
    ("GET", "/api/v1/tags"): VIEW,
    ("GET", "/api/v1/groups"): VIEW,
    ("GET", "/api/v1/collectors"): VIEW,
    ("GET", "/api/v1/collectors/{collector_uuid}/health"): VIEW,
    ("GET", "/api/v1/overview"): VIEW,
    ("GET", "/api/v1/rankings/utilization"): VIEW,
    ("GET", "/api/v1/rankings/errors"): VIEW,
    ("GET", "/api/v1/high-priority"): VIEW,
    ("GET", "/api/v1/wan-circuits"): VIEW,
    ("GET", "/api/v1/search"): VIEW,
    ("GET", "/api/v1/exports/interfaces/{interface_uuid}/history/{kind}"): VIEW,
    ("GET", "/api/v1/exports/devices/{device_uuid}/history/reachability"): VIEW,
    ("GET", "/api/v1/exports/rankings/{kind}"): VIEW,
}
"""Every route and the least role it needs; the session route needs any caller."""
FILL = {"{kind}": "errors", "{device_uuid}": str(uuid.uuid4()),
        "{interface_uuid}": str(uuid.uuid4()), "{collector_uuid}": str(uuid.uuid4())}  # fmt: skip
QUERY = {"/api/v1/search": {"q": "core"}, "/api/v1/device-lookup": {"key": "core"}}
BODY = {"/api/v1/interfaces/{interface_uuid}/identity-resolution":
        {"review_id": str(uuid.uuid4()), "decision": "map"},
        "/api/v1/auth/login": {"username": "nobody", "password": new_password()}}  # fmt: skip
CALLERS = ("anonymous", "operator", "admin", "kiosk", "socket-admin", "socket-operator",
           "socket-refused")  # fmt: skip


def concrete(path: str) -> str:
    """Fill a route template with sample values.

    Args:
        path: The template.

    Returns:
        str: A callable URL path.

    Raises:
        None
    """
    for key, value in FILL.items():
        path = path.replace(key, value)
    return path


def expected(level: str, caller: str) -> bool:
    """Decide whether a caller may pass a route of ``level``.

    Args:
        level: ``anonymous``, ``operator``, or ``admin``.
        caller: One of ``CALLERS``.

    Returns:
        bool: True when it may.

    Raises:
        None
    """
    if caller == "socket-refused":
        return False  # refused on every endpoint, even anonymous ones (E.12)
    if level == ANON:
        return True
    if level == VIEW:
        return caller != "anonymous"
    return caller in ("admin", "socket-admin")


@pytest.fixture(scope="module")
def kit() -> ApiKit:
    """Build one app with an operator, an admin, and a kiosk token.

    Args:
        None

    Returns:
        ApiKit: The kit.

    Raises:
        None
    """
    built = ApiKit.create()
    built.add_user("olivia", Role.OPERATOR)
    built.add_user("adam", Role.ADMIN)
    return built


def test_auth001_every_route_has_a_declared_level(kit: ApiKit) -> None:
    """AUTH-001: the matrix covers exactly the app's routes, so none is unguarded by accident.

    Args:
        kit: The app.

    Returns:
        None

    Raises:
        None
    """
    paths = kit.app.openapi()["paths"]
    routes = {(method.upper(), path) for path, ops in paths.items() for method in ops}
    assert routes == set(LEVELS)


@pytest.mark.parametrize("caller", CALLERS)
def test_auth001_authorization_matrix(kit: ApiKit, caller: str) -> None:
    """AUTH-001, SEC-006: each caller passes exactly the routes its role allows.

    Args:
        kit: The app.
        caller: The kind of caller.

    Returns:
        None

    Raises:
        None
    """
    headers: dict[str, str] = {}
    if caller.startswith("socket"):
        role = {"socket-admin": Role.ADMIN, "socket-operator": Role.OPERATOR}.get(caller)
        client = kit.socket_client(role)
    else:
        client = kit.client()
        if caller in ("operator", "admin"):
            name = "olivia" if caller == "operator" else "adam"
            assert kit.login(client, name).status_code == 200
            headers["X-CSRF-Token"] = client.get("/api/v1/auth/session").json()["csrf_token"]
        elif caller == "kiosk":
            headers["Authorization"] = f"Bearer {kit.kiosk_token(f'wall-{uuid.uuid4().hex[:8]}')}"
    for (method, template), level in LEVELS.items():
        if template in ("/api/v1/auth/logout", "/api/v1/auth/login") and caller != "socket-refused":
            continue  # these change the caller's own session; test_api_auth covers them
        url = concrete(template)
        response = client.request(method, url, params=QUERY.get(template),
                                  json=BODY.get(template), headers=headers)  # fmt: skip
        allowed = response.status_code not in (401, 403)
        assert allowed == expected(level, caller), (caller, method, template, response.text)
        if not allowed:
            assert set(response.json()) == {"code", "message", "request_id", "fields"}


def test_auth001_kiosk_is_operator_and_session_route_names_each_caller(kit: ApiKit) -> None:
    """AUTH-001, E.12: who-am-I answers each kind of caller with its kind and role.

    Args:
        kit: The app.

    Returns:
        None

    Raises:
        None
    """
    token = kit.kiosk_token("lobby")
    kiosk = kit.client().get("/api/v1/auth/session", headers={"Authorization": f"Bearer {token}"})
    assert (kiosk.json()["kind"], kiosk.json()["role"], kiosk.json()["csrf_token"]) == (
        "kiosk", "operator", None)  # fmt: skip
    local = kit.socket_client(Role.ADMIN).get("/api/v1/auth/session").json()
    assert (local["kind"], local["role"], local["name"]) == ("local", "admin", "local:tester")
    assert kit.client().get("/api/v1/auth/session").status_code == 401
    bogus = kit.client().get("/api/v1/devices", headers={"Authorization": "Bearer vks_x"})
    assert bogus.status_code == 401
    assert bogus.json()["code"] == "unauthenticated"
