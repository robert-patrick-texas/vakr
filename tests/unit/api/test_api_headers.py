"""15.4 web controls on every answer, and the request body bound (WEB-T10, SEC-019).

Every response, success or error, JSON or file, carries the security
headers, the request ID, and ``Cache-Control: no-store`` under ``/api/``;
bodies above ``api.max_body_bytes`` are refused with 413 whether declared
by ``Content-Length`` or streamed in chunks.

Dependencies:
    tests.unit.api.api_testkit.

Example:
    $ uv run pytest tests/unit/api/test_api_headers.py
"""

from collections.abc import Iterator

from tests.unit.api.api_testkit import ApiKit
from tests.unit.auth.auth_testkit import new_password
from vakr.api.middleware import SECURITY_HEADERS
from vakr.domain.users import Role

REQUIRED = {name.decode(): value.decode() for name, value in SECURITY_HEADERS}


def test_web_t10_security_headers_on_every_kind_of_answer() -> None:
    """WEB-T10, 15.4: CSP with frame-ancestors none, nosniff, DENY, no-referrer, no-store.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    kit.add_user("adam", Role.ADMIN)
    client = kit.client()
    answers = [
        client.get("/api/v1/health/live"),
        client.get("/api/v1/devices"),  # 401
        client.get("/api/v1/nowhere"),  # 404
        client.post(
            "/api/v1/auth/login", content=b"x" * 70000, headers={"Content-Type": "application/json"}
        ),  # 413
        kit.login(client, "adam"),  # 200 with cookie
        client.get("/api/v1/exports/rankings/errors"),  # a file
        kit.socket_client(None).get("/api/v1/health/live"),  # refused peer
    ]
    for answer in answers:
        for name, value in REQUIRED.items():
            assert answer.headers.get(name) == value, (answer.status_code, name)
        assert answer.headers["cache-control"] == "no-store"
        assert len(answer.headers["x-request-id"]) >= 16
        assert "server" not in answer.headers
    assert "frame-ancestors 'none'" in REQUIRED["content-security-policy"]
    assert [a.status_code for a in answers] == [200, 401, 404, 413, 200, 200, 403]


def test_sec019_request_bodies_are_bounded() -> None:
    """SEC-019: a declared or streamed body over the limit is a 413 envelope; small ones pass.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create(max_body=2048)
    client = kit.client()
    declared = client.post("/api/v1/auth/login", content=b"{" + b" " * 4096 + b"}",
                           headers={"Content-Type": "application/json"})  # fmt: skip
    assert (declared.status_code, declared.json()["code"]) == (413, "body_too_large")

    def chunks() -> Iterator[bytes]:
        """Stream a body without a length.

        Args:
            None

        Yields:
            bytes: 1 KiB chunks, 4 KiB in all.

        Returns:
            Iterator[bytes]: The chunks.

        Raises:
            None
        """
        for _ in range(4):
            yield b" " * 1024

    streamed = client.post("/api/v1/auth/login", content=chunks(),
                           headers={"Content-Type": "application/json"})  # fmt: skip
    assert streamed.status_code == 413
    small = client.post("/api/v1/auth/login", json={"username": "a", "password": new_password()})
    assert small.status_code == 401


def test_web_mount_hook_attaches_a_front_end_outside_the_api() -> None:
    """14, 15.4: the ``web`` hook adds pages beside ``/api/v1``; they get the security headers.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse
    from fastapi.testclient import TestClient

    from vakr.api.app import create_app

    def attach(app: FastAPI) -> None:
        """Add one page, as a front end would.

        Args:
            app: The application.

        Returns:
            None

        Raises:
            None
        """
        app.add_api_route("/", lambda: HTMLResponse("<p>vakr</p>"), include_in_schema=False)

    kit = ApiKit.create()
    client = TestClient(create_app(kit.context, web=attach), client=("127.0.0.1", 1))
    page = client.get("/")
    assert page.status_code == 200
    assert page.headers["x-frame-options"] == "DENY"
    assert "cache-control" not in page.headers  # pages and assets may be cached; the API not
    assert client.get("/api/v1/health/live").headers["cache-control"] == "no-store"
