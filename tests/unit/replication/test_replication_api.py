"""Replication endpoints and the peer's refusals, on the real app over SQLite (MPOS-003, MPOS-006).

The app is built as ``vakr-api`` builds itself (``build_parts``) over a
seeded metadata database, so the endpoints answer real digests and pages.

Dependencies:
    pytest, fastapi.testclient; tests.unit.replication.repl_testkit.

Example:
    $ uv run pytest tests/unit/replication/test_replication_api.py
"""

import logging
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.unit.auth.auth_testkit import cheap_hasher
from tests.unit.replication.repl_testkit import open_store, seed
from vakr.api.app import create_app
from vakr.app.api.build import ApiParts, build_parts
from vakr.auth import Passwords
from vakr.config.node import NodeSettings
from vakr.paths import InstallPaths
from vakr.ports.clock import ManualClock

NOW = datetime(2026, 10, 22, tzinfo=UTC)
ONE = "00000000-0000-7000-8000-000000000001"


@pytest.fixture
def parts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[ApiParts]:
    """Build the API over a seeded metadata database.

    Args:
        tmp_path: The install root.
        monkeypatch: ``VAKR_ROOT``.

    Returns:
        Generator[ApiParts]: The parts; closed afterwards.

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    paths = InstallPaths.under(tmp_path)
    for directory in (paths.config_dir, paths.state_dir, paths.run_dir):
        directory.mkdir(parents=True, exist_ok=True)
    store, factory = open_store(paths.state_dir / "metadata.db")
    seed(factory, ManualClock(NOW))
    store.close()
    (paths.config_dir / "config.toml").write_text(
        'config_version = 1\n\n[api]\nlisten_address = ""\n'
    )
    built = build_parts(paths, clock=ManualClock(NOW), passwords=Passwords(cheap_hasher()))
    yield built
    built.close()


def _token(parts: ApiParts) -> str:
    """Issue a replication token on the serving node.

    Args:
        parts: The API parts.

    Returns:
        str: The token.

    Raises:
        None
    """
    return parts.context.auth.replication.create("west", actor="test").token.reveal()


def test_mpos006_a_replication_token_reads_replication_and_nothing_else(
    parts: ApiParts, caplog: pytest.LogCaptureFixture
) -> None:
    """MPOS-006: vkr_ reaches /api/v1/replication only; nothing else reaches it; it is never logged.

    Args:
        parts: The API parts.
        caplog: Log capture.

    Returns:
        None

    Raises:
        None
    """
    caplog.set_level(logging.DEBUG)
    token = _token(parts)
    bearer = {"Authorization": f"Bearer {token}"}
    with TestClient(create_app(parts.context), base_url="http://127.0.0.1") as client:
        state = client.get("/api/v1/replication/metadata/state", headers=bearer)
        assert state.status_code == 200, state.text
        doc = state.json()
        assert len(doc["digest"]) == 64
        assert doc["rows"]["device"] == 1
        assert doc["role"] == "primary"
        page = client.get("/api/v1/replication/metadata", params={"kind": "local_user"},
                          headers=bearer).json()  # fmt: skip
        assert page["table"] == "local_user"
        assert len(page["rows"]) == 1
        assert state.headers["cache-control"] == "no-store"
        refused = client.get("/api/v1/devices", headers=bearer)
        assert (refused.status_code, refused.json()["code"]) == (403, "replication_only")
        assert client.get("/api/v1/replication/metadata/state").status_code == 401
        kiosk = parts.context.auth.kiosk.create("wall", actor="test").token.reveal()
        other = client.get("/api/v1/replication/metadata/state",
                           headers={"Authorization": f"Bearer {kiosk}"})  # fmt: skip
        assert other.status_code == 401
        bad = client.get("/api/v1/replication/metadata", params={"kind": "session"}, headers=bearer)
        assert bad.status_code == 422
        found = parts.context.auth.replication.tokens()[0]
        assert parts.context.auth.replication.revoke(found.id, actor="test")
        assert client.get("/api/v1/replication/metadata/state", headers=bearer).status_code == 401
    assert token not in caplog.text
    assert token[4:20] not in repr(found)


def test_mpos_t03_a_peer_refuses_metadata_writes_naming_the_primary(parts: ApiParts) -> None:
    """MPOS-003, MPOS-T03: every state change but sign-in and kiosk tokens is refused, 409.

    Args:
        parts: The API parts.

    Returns:
        None

    Raises:
        None
    """
    parts.context.node = NodeSettings("peer", "west", "https://vakr-east.example.net")
    with TestClient(create_app(parts.context), base_url="http://127.0.0.1") as client:
        for method, url in (("POST", "/api/v1/dashboards"),
                            ("POST", f"/api/v1/devices/{ONE}/rediscovery"),
                            ("POST", f"/api/v1/interfaces/{ONE}/identity-resolution")):  # fmt: skip
            answer = client.request(method, url, json={})
            assert (answer.status_code, answer.json()["code"]) == (409, "peer_read_only"), url
            assert "https://vakr-east.example.net" in answer.json()["message"]
        assert client.post("/api/v1/kiosk-tokens", json={"label": "x"}).status_code == 401
        wrong = {"username": "alice", "password": "nope"}
        assert client.post("/api/v1/auth/login", json=wrong).status_code != 409
        assert client.get("/api/v1/version").status_code == 401  # reads are as before
    info = parts.context.node
    assert (info.role, info.position) == ("peer", "west")


def test_version_reports_role_and_position(parts: ApiParts) -> None:
    """MPOS-003: an operator sees which node and role answered.

    Args:
        parts: The API parts.

    Returns:
        None

    Raises:
        None
    """
    from vakr.api.routes_health import version

    assert version.__name__ == "version"
    parts.context.node = NodeSettings("primary", "east", "")
    app = create_app(parts.context)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        right = {"username": "alice", "password": "a long enough test password"}
        login = client.post("/api/v1/auth/login", json=right)
        assert login.status_code == 200, login.text
        doc = client.get("/api/v1/version").json()
    assert (doc["role"], doc["position"]) == ("primary", "east")
