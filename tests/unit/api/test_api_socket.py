"""E.12 over a real Unix socket: the connecting account decides the role; TCP never can.

A uvicorn server with ``PeerProtocol`` runs on a socket in ``tmp_path``.
The test process connects as itself; mapping its own GID to the operator
group makes it an operator, mapping nothing refuses it (unless the tests
run as root, which is admin by rule). The same app served over loopback TCP
treats the same request as anonymous: peer credentials exist only on the
socket.

Dependencies:
    uvicorn, httpx2; vakr.api.peer; vakr.app.api.server; tests.unit.api.api_testkit.

Example:
    $ uv run pytest tests/unit/api/test_api_socket.py
"""

import os
import socket
import uuid
from pathlib import Path

import httpx2

from tests.unit.api.api_testkit import ApiKit, serving
from vakr.api.context import account_name
from vakr.api.peer import peer_principal, peer_protocol
from vakr.app.api.listeners import open_tcp, open_unix
from vakr.auth import LocalGroups
from vakr.domain.users import Role

ROOT = os.geteuid() == 0


def test_e12_peer_principal_from_a_socketpair() -> None:
    """E.12: the kernel's UID and groups map to a role; no group grants nothing.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    left, right = socket.socketpair(socket.AF_UNIX)
    with left, right:
        mine = LocalGroups(admin_gid=None, operator_gid=os.getgid())
        principal, uid = peer_principal(right, mine, account_name)
        assert uid == os.geteuid()
        assert principal is not None
        assert principal.role is (Role.ADMIN if ROOT else Role.OPERATOR)
        assert principal.name == f"local:{account_name(os.geteuid())}"
        nobody, _ = peer_principal(right, LocalGroups(None, None), account_name)
        assert (nobody is None) != ROOT
        admin, _ = peer_principal(right, LocalGroups(os.getgid(), None), account_name)
        assert admin is not None
        assert admin.role is Role.ADMIN
        viewer, _ = peer_principal(right, LocalGroups(None, None, os.getgid()), account_name)
        assert viewer is not None
        assert viewer.role is (Role.ADMIN if ROOT else Role.OPERATOR)
    with socket.socket(socket.AF_INET) as tcp:
        assert peer_principal(tcp, mine, account_name) == (None, None)


def test_e12_real_socket_roles_and_refusal(tmp_path: Path) -> None:
    """E.12, 13.1: over the socket the account is the credential; others get 403 everywhere.

    Args:
        tmp_path: Directory for the socket.

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    path = tmp_path / "api.sock"
    groups = LocalGroups(admin_gid=None, operator_gid=os.getgid())
    with serving(kit.app, open_unix(path), peer_protocol(groups, account_name)):
        assert path.stat().st_mode & 0o777 == 0o666
        with httpx2.Client(transport=httpx2.HTTPTransport(uds=str(path)),
                           base_url="http://localhost") as client:  # fmt: skip
            who = client.get("/api/v1/auth/session").json()
            assert who["kind"] == "local"
            assert who["role"] == ("admin" if ROOT else "operator")
            assert client.get("/api/v1/version").status_code == 200
            assert "server" not in client.get("/api/v1/health/live").headers
    refused = LocalGroups(None, None)
    transport = httpx2.HTTPTransport(uds=str(path))
    with (
        serving(kit.app, open_unix(path), peer_protocol(refused, account_name)),
        httpx2.Client(transport=transport, base_url="http://localhost") as client,
    ):
        answer = client.get("/api/v1/health/live")
        if not ROOT:
            assert (answer.status_code, answer.json()["code"]) == (403, "peer_refused")


def test_e12_vakr_view_reads_but_changes_nothing(tmp_path: Path) -> None:
    """E.12, SEC-006: a vakr-view member is an operator on the socket: reads, never admin writes.

    Args:
        tmp_path: Directory for the socket.

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    path = tmp_path / "api.sock"
    groups = LocalGroups(admin_gid=None, operator_gid=None, viewer_gid=os.getgid())
    resolve = f"/api/v1/interfaces/{uuid.uuid4()}/identity-resolution"
    with (
        serving(kit.app, open_unix(path), peer_protocol(groups, account_name)),
        httpx2.Client(transport=httpx2.HTTPTransport(uds=str(path)),
                      base_url="http://localhost") as client,
    ):  # fmt: skip
        assert client.get("/api/v1/devices").status_code == 200
        if not ROOT:
            assert client.get("/api/v1/auth/session").json()["role"] == "operator"
            answer = client.post(resolve, json={"review_id": str(uuid.uuid4()), "decision": "new"})
            assert (answer.status_code, answer.json()["code"]) == (403, "forbidden")


def test_e12_tcp_never_carries_peer_credentials() -> None:
    """E.12, AUTH-001: the same request over loopback TCP is anonymous.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    sock = open_tcp("127.0.0.1", 0)
    port = sock.getsockname()[1]
    with serving(kit.app, sock), httpx2.Client(base_url=f"http://127.0.0.1:{port}") as client:
        assert client.get("/api/v1/version").status_code == 401
        live = client.get("/api/v1/health/live")
        assert live.status_code == 200
        assert "server" not in live.headers
        assert live.headers["x-frame-options"] == "DENY"
