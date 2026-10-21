"""OPS-001, SVC-004: ``vakr check health`` asks the running API over its socket (the ``api`` check).

Canned answers on a socket in a relocated install cover every verdict, and
one test asks a real API. The API never runs this check itself.

Dependencies:
    vakr.health.api_check; the registry; tests.unit.api.api_testkit.

Example:
    $ uv run pytest tests/unit/api/test_api_health_check.py
"""

import json
import os
import socket
import threading
from pathlib import Path

import pytest

from tests.unit.api.api_testkit import ApiKit, serving
from vakr.api.context import account_name
from vakr.api.peer import peer_protocol
from vakr.app.api.listeners import open_unix
from vakr.auth import LocalGroups
from vakr.health import registry
from vakr.health.api_check import check_api, judge
from vakr.health.model import Status
from vakr.paths import InstallPaths


def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``VAKR_ROOT`` at an install with a state and a run directory.

    Args:
        tmp_path: Test directory.
        monkeypatch: Sets ``VAKR_ROOT``.

    Returns:
        Path: Where the API socket belongs.

    Raises:
        None
    """
    paths = InstallPaths.under(tmp_path / "r")
    for directory in (paths.config_dir, paths.state_dir, paths.run_dir):
        directory.mkdir(parents=True)
    monkeypatch.setenv("VAKR_ROOT", str(paths.root))
    return paths.run_dir / "api.sock"


def answer_once(path: Path, status: int, body: dict[str, object]) -> threading.Thread:
    """Serve one canned HTTP answer on a Unix socket.

    Args:
        path: Socket path.
        status: HTTP status to answer.
        body: JSON body.

    Returns:
        threading.Thread: The serving thread.

    Raises:
        None
    """
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)
    payload = json.dumps(body).encode()

    def run() -> None:
        """Accept one connection, read the request, answer.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        with server:
            conn, _ = server.accept()
            with conn:
                conn.recv(4096)
                head = (f"HTTP/1.1 {status} X\r\nContent-Type: application/json\r\n"
                        f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n")  # fmt: skip
                conn.sendall(head.encode() + payload)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


@pytest.mark.parametrize(
    ("status", "body", "verdict"),
    [
        (200, {"status": "ready"}, Status.OK),
        (200, {"status": "degraded"}, Status.OK),
        (503, {"status": "not_ready"}, Status.CRITICAL),
        (403, {"code": "peer_refused"}, Status.UNKNOWN),
        (500, {}, Status.UNKNOWN),
    ],
)
def test_ops001_api_check_verdicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int, body: dict[str, object],
    verdict: Status,
) -> None:  # fmt: skip
    """OPS-001: each readiness answer maps to its Nagios state.

    Args:
        tmp_path: Test directory.
        monkeypatch: Relocates the install.
        status: Canned HTTP status.
        body: Canned body.
        verdict: Expected check state.

    Returns:
        None

    Raises:
        None
    """
    path = layout(tmp_path, monkeypatch)
    thread = answer_once(path, status, body)
    result = check_api()
    thread.join(5)
    assert result.status is verdict, result.summary
    if verdict is Status.OK:
        assert result.perfdata[0].label == "api_ready_s"


def test_ops001_api_check_without_a_node_a_socket_or_an_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OPS-001: no node and no socket are OK; a dead socket is CRITICAL; it is registered.

    Args:
        tmp_path: Test directory.
        monkeypatch: Relocates the install.

    Returns:
        None

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path / "absent"))
    assert check_api().status is Status.OK
    path = layout(tmp_path, monkeypatch)
    assert "not running" in check_api().summary
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(path))
    stale.close()  # a socket file nobody listens on
    dead = check_api()
    assert dead.status is Status.CRITICAL
    assert "not answering" in dead.summary
    assert check_api in registry.CHECKS
    assert judge(200, {"status": "odd"}, 0.1).status is Status.UNKNOWN


def test_ops001_api_check_asks_a_real_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """OPS-001, SVC-004: the check reads the real readiness answer over the socket.

    Args:
        tmp_path: Test directory.
        monkeypatch: Relocates the install.

    Returns:
        None

    Raises:
        None
    """
    path = layout(tmp_path, monkeypatch)
    kit = ApiKit.create()
    groups = LocalGroups(admin_gid=None, operator_gid=os.getgid())
    with serving(kit.app, open_unix(path), peer_protocol(groups, account_name)):
        result = check_api()
    assert result.status is Status.OK
    assert result.summary.startswith("ready")
