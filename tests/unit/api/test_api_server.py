"""The ``vakr-api`` process end to end: checks, two listeners, clean stop (SVC-005, 16.3, 16.4).

A relocated install in ``tmp_path`` (configuration, migrated databases, run
directory) is served by ``run_api`` exactly as the unit runs it, then
stopped with SIGTERM. The startup checks refuse a node without a state
directory and a port that is taken, each with exit 78 and one log line
naming the check.

Dependencies:
    httpx2; vakr.app.api.entry; the SQLite migration helpers.

Example:
    $ uv run pytest tests/unit/api/test_api_server.py
"""

import asyncio
import os
import signal
import socket
import stat
from pathlib import Path

import httpx2
import pytest

from tests.unit.auth.auth_testkit import cheap_hasher
from vakr.adapters.sqlite.metadata import METADATA_FILE, metadata_migrations
from vakr.adapters.sqlite.telemetry import TELEMETRY_FILE, telemetry_migrations
from vakr.app.api.entry import EX_PREFLIGHT, run_api
from vakr.app.api.server import ApiService
from vakr.auth import KEY_FILE, LocalGroups, Passwords
from vakr.paths import InstallPaths


def free_port() -> int:
    """Find a free loopback TCP port.

    Args:
        None

    Returns:
        int: A port nothing listens on right now.

    Raises:
        None
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def install(tmp_path: Path, port: int) -> InstallPaths:
    """Create a relocated install with migrated databases and an ``[api]`` section.

    Args:
        tmp_path: Test directory.
        port: TCP port for the listener.

    Returns:
        InstallPaths: The layout.

    Raises:
        None
    """
    paths = InstallPaths.under(tmp_path / "root")
    for directory in (paths.config_dir, paths.state_dir, paths.run_dir):
        directory.mkdir(parents=True, exist_ok=True)
    (paths.config_dir / "config.toml").write_text(
        f'config_version = 1\n\n[api]\nlisten_address = "127.0.0.1"\nport = {port}\n'
    )
    metadata_migrations(paths.state_dir / METADATA_FILE).migrate()
    telemetry_migrations(paths.state_dir / TELEMETRY_FILE).migrate()
    return paths


def get(path: str, *, port: int | None = None, uds: Path | None = None) -> httpx2.Response:
    """Make one blocking request over TCP or the socket.

    Args:
        path: URL path.
        port: TCP port, for TCP.
        uds: Socket path, for the socket.

    Returns:
        httpx2.Response: The answer.

    Raises:
        httpx2.HTTPError: If the request fails.
    """
    transport = httpx2.HTTPTransport(uds=str(uds)) if uds else None
    base = f"http://127.0.0.1:{port}" if port else "http://localhost"
    with httpx2.Client(transport=transport, base_url=base, timeout=5) as client:
        return client.get(path)


def test_svc_api_serves_both_listeners_and_stops_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SVC-001, SVC-004, 16.4: ready on TCP and socket; SIGTERM stops it with 0 and no socket.

    Args:
        tmp_path: Test directory.
        monkeypatch: Records readiness notifications; maps this account to operator.

    Returns:
        None

    Raises:
        None
    """
    port = free_port()
    paths = install(tmp_path, port)
    sock = paths.run_dir / "api.sock"
    said: list[str] = []
    monkeypatch.setattr("vakr.app.api.server.notify", lambda state: said.append(state) or True)
    mine = LocalGroups(admin_gid=None, operator_gid=os.getgid())  # this test runs as an operator
    monkeypatch.setattr("vakr.app.api.build.local_groups", lambda: mine)

    async def scenario() -> tuple[int, list[int], str]:
        """Start, probe both listeners, and stop.

        Args:
            None

        Returns:
            tuple: Exit status, probe statuses, and the readiness word.

        Raises:
            None
        """
        task = asyncio.create_task(run_api(paths, passwords=Passwords(cheap_hasher())))
        for _ in range(200):
            if said:
                break
            await asyncio.sleep(0.05)
        live = await asyncio.to_thread(get, "/api/v1/health/live", port=port)
        anonymous = await asyncio.to_thread(get, "/api/v1/devices", port=port)
        ready = await asyncio.to_thread(get, "/api/v1/health/ready", uds=sock)
        os.kill(os.getpid(), signal.SIGTERM)
        return await task, [live.status_code, anonymous.status_code], ready.json()["status"]

    status, probes, ready = asyncio.run(scenario())
    assert status == 0
    assert probes == [200, 401]
    assert ready in ("ready", "degraded")
    assert said[0].startswith("READY=1")
    assert any(s.startswith("STOPPING=1") for s in said)
    assert not sock.exists()
    key = paths.state_dir / KEY_FILE
    assert stat.S_IMODE(key.stat().st_mode) == 0o600


def test_svc_t02_failed_startup_checks_exit_78(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """SVC-T02, SVC-T05, 16.3: no state directory, or a taken port, stops with 78 and a reason.

    Args:
        tmp_path: Test directory.
        capsys: Captures the journal lines.

    Returns:
        None

    Raises:
        None
    """
    bare = InstallPaths.under(tmp_path / "nothing")
    bare.config_dir.mkdir(parents=True)
    assert asyncio.run(run_api(bare)) == EX_PREFLIGHT
    assert "<3>[api.preflight_failed]" in capsys.readouterr().err
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        paths = install(tmp_path, taken.getsockname()[1])
        assert asyncio.run(run_api(paths, passwords=Passwords(cheap_hasher()))) == EX_PREFLIGHT
    err = capsys.readouterr().err
    assert "startup check listen failed" in err
    assert not (paths.run_dir / "api.sock").exists()


def test_reload_is_ignored_and_a_failed_server_is_status_one() -> None:
    """16.4: SIGHUP changes nothing; a server that dies ends the service with 1.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """

    class Parts:
        """Stand-in parts with nothing to close."""

        def close(self) -> None:
            """Close nothing.

            Args:
                None

            Returns:
                None

            Raises:
                None
            """

    class Listening:
        """Stand-in listeners."""

        def close(self) -> None:
            """Close nothing.

            Args:
                None

            Returns:
                None

            Raises:
                None
            """

    class Dying:
        """A server that fails at start."""

        started = False
        should_exit = False

        async def serve(self, sockets: list[object]) -> None:
            """Fail at once.

            Args:
                sockets: The sockets it would serve.

            Returns:
                None

            Raises:
                OSError: Always.
            """
            raise OSError(f"cannot serve {len(sockets)} socket(s)")

    service = ApiService(Parts(), Listening(), [(Dying(), None)])  # ty: ignore[invalid-argument-type]
    service.request_reload()
    assert asyncio.run(service.run()) == 1
