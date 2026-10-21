"""API-T03, API-007, SEC-003: one redacted error envelope, and no secret in any answer or log.

Dependencies:
    tests.unit.api.api_testkit: The app over memory stores.

Example:
    $ uv run pytest tests/unit/api/test_api_errors.py
"""

import logging
import uuid

import pytest

from tests.unit.api.api_testkit import ApiKit
from vakr.api import routes_health
from vakr.domain.errors import StorageError
from vakr.domain.users import Role

ENVELOPE = {"code", "message", "request_id", "fields"}


@pytest.mark.parametrize(
    ("method", "path", "params", "status", "code"),
    [
        ("GET", "/api/v1/devices", None, 401, "unauthenticated"),
        ("GET", "/api/v1/nowhere", None, 404, "not_found"),
        ("DELETE", "/api/v1/devices", None, 405, "method_not_allowed"),
    ],
)
def test_api_t03_routing_and_auth_errors_use_the_envelope(
    method: str, path: str, params: dict[str, str] | None, status: int, code: str
) -> None:
    """API-T03: auth and routing errors carry code, message, request ID, and fields.

    Args:
        method: HTTP method.
        path: URL path.
        params: Query parameters.
        status: Expected status.
        code: Expected code.

    Returns:
        None

    Raises:
        None
    """
    answer = ApiKit.create().client().request(method, path, params=params)
    body = answer.json()
    assert (answer.status_code, body["code"]) == (status, code)
    assert set(body) == ENVELOPE
    assert body["request_id"] == answer.headers["x-request-id"]


@pytest.mark.parametrize(
    ("path", "params", "field"),
    [
        ("/api/v1/devices", {"limit": "0"}, "limit"),
        ("/api/v1/devices", {"limit": "501"}, "limit"),
        ("/api/v1/devices", {"cursor": "!!not-a-cursor!!"}, "cursor"),
        ("/api/v1/devices", {"cursor": "a" * 1025}, "cursor"),
        ("/api/v1/devices", {"search": "x" * 257}, "search"),
        ("/api/v1/devices", {"bucket": "purple"}, "bucket"),
        ("/api/v1/devices/not-a-uuid", None, "device_uuid"),
        ("/api/v1/search", {"q": ""}, "q"),
    ],
)
def test_api_t03_validation_errors_name_the_field(
    path: str, params: dict[str, str] | None, field: str
) -> None:
    """API-T03, SEC-019: out-of-bounds input is a 422 naming the field.

    Args:
        path: URL path.
        params: Query parameters.
        field: The field the envelope must name.

    Returns:
        None

    Raises:
        None
    """
    answer = ApiKit.create().socket_client(Role.OPERATOR).get(path, params=params)
    body = answer.json()
    assert (answer.status_code, body["code"]) == (422, "invalid_request")
    assert any(f["field"].split(".")[0] == field for f in body["fields"]), body


def test_api_t03_not_found_storage_and_failure_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    """API-T03: 404 for unknown objects, 503 for storage, 500 without a traceback.

    Args:
        monkeypatch: Breaks a service.

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    client = kit.socket_client(Role.OPERATOR)
    missing = client.get(f"/api/v1/devices/{uuid.uuid4()}")
    assert (missing.status_code, missing.json()["code"]) == (404, "not_found")

    def unavailable(*_args: object, **_kwargs: object) -> None:
        """Fail like a locked database.

        Args:
            *_args: Ignored.
            **_kwargs: Ignored.

        Returns:
            None

        Raises:
            StorageError: Always, with driver-like text.
        """
        raise StorageError("database is locked: SELECT secret FROM x", retryable=True)

    monkeypatch.setattr(kit.context.overview, "device_list", unavailable)
    storage = client.get("/api/v1/devices")
    assert (storage.status_code, storage.json()["code"]) == (503, "storage_unavailable")
    assert storage.headers["retry-after"] == "5"
    assert "SELECT" not in storage.text  # the driver's words never reach the client

    def crash(*_args: object) -> None:
        """Fail unexpectedly.

        Args:
            *_args: Ignored.

        Returns:
            None

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("internal detail: /var/lib/vakr/auth.key")

    monkeypatch.setattr(routes_health, "_components", crash)
    failed = client.get("/api/v1/health/ready")
    assert (failed.status_code, failed.json()["code"]) == (500, "internal_error")
    assert "Traceback" not in failed.text
    assert "auth.key" not in failed.text
    assert failed.headers["x-content-type-options"] == "nosniff"


def test_sec003_no_password_or_token_in_any_answer_or_log(caplog: pytest.LogCaptureFixture) -> None:
    """SEC-003, CRED-002: passwords, session, kiosk, and CSRF tokens never leak.

    Args:
        caplog: Log capture.

    Returns:
        None

    Raises:
        None
    """
    caplog.set_level(logging.DEBUG)
    kit = ApiKit.create()
    password = kit.add_user("alice", Role.ADMIN)
    kiosk = kit.kiosk_token()
    client = kit.client()
    answers = [kit.login(client, "alice")]
    session = client.cookies["vakr_session"]
    csrf = answers[0].json()["csrf_token"]
    long_password = password + "x" * 2000
    answers += [
        client.post("/api/v1/auth/login", json={"username": "alice", "password": long_password}),
        client.post("/api/v1/auth/login", json={"username": password, "password": password}),
        client.get("/api/v1/devices", params={"search": "q"}),
        client.post("/api/v1/auth/logout"),  # CSRF refused: the token is not echoed
        kit.client().get("/api/v1/devices", headers={"Authorization": f"Bearer {kiosk}x"}),
        kit.client().get("/api/v1/overview", headers={"Authorization": f"Bearer {kiosk}"}),
    ]
    texts = [a.text + repr(dict(a.headers)) for a in answers[1:]]
    texts.append(answers[0].text)
    logs = caplog.text
    for secret in (password, session, kiosk):
        assert all(secret not in t for t in texts), secret[:4]
        assert secret not in logs
    assert csrf not in logs
