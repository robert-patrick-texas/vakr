"""The ``vakr-api`` process serves the web build it finds, or only the API (ADR-035 #2, #3; E.31).

``run_api`` reads the build from ``VAKR_WEB_DIR`` (development and tests)
or ``/opt/vakr/share/web`` (the package) once before serving. With a build,
the same TCP listener answers pages and the API; without one, the API
answers and the log says once why there are no pages.

Dependencies:
    tests.unit.api.test_api_server (relocated install, HTTP helper), web_testkit.

Example:
    $ uv run pytest tests/unit/api/test_api_web_process.py
"""

import asyncio
import os
import signal
from pathlib import Path

import httpx2
import pytest

from tests.unit.api.test_api_server import free_port, get, install
from tests.unit.api.web_testkit import INDEX, fake_build
from tests.unit.auth.auth_testkit import cheap_hasher
from vakr.app.api.entry import run_api
from vakr.auth import LocalGroups, Passwords


def serve_and_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, paths_to_get: list[str]
) -> list[httpx2.Response]:
    """Run the API process, GET some paths over TCP, and stop it.

    Args:
        tmp_path: Test directory.
        monkeypatch: Silences sd_notify and maps this account to operator.
        paths_to_get: URL paths to request once ready.

    Returns:
        list[httpx2.Response]: The answers, in order.

    Raises:
        None
    """
    port = free_port()
    paths = install(tmp_path, port)
    said: list[str] = []
    monkeypatch.setattr("vakr.app.api.server.notify", lambda state: said.append(state) or True)
    mine = LocalGroups(admin_gid=None, operator_gid=os.getgid())
    monkeypatch.setattr("vakr.app.api.build.local_groups", lambda: mine)

    async def scenario() -> list[httpx2.Response]:
        """Start, probe, and stop.

        Args:
            None

        Returns:
            list[httpx2.Response]: The answers.

        Raises:
            None
        """
        task = asyncio.create_task(run_api(paths, passwords=Passwords(cheap_hasher())))
        for _ in range(200):
            if said:
                break
            await asyncio.sleep(0.05)
        answers = [await asyncio.to_thread(get, p, port=port) for p in paths_to_get]
        os.kill(os.getpid(), signal.SIGTERM)
        assert await task == 0
        return answers

    return asyncio.run(scenario())


def test_adr035_api_process_serves_pages_from_vakr_web_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-035 #3, E.31: the build in ``VAKR_WEB_DIR`` is served beside the API on one origin.

    Args:
        tmp_path: Test directory.
        monkeypatch: Sets ``VAKR_WEB_DIR``.

    Returns:
        None

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_WEB_DIR", str(fake_build(tmp_path)))
    page, deep, live = serve_and_probe(
        tmp_path, monkeypatch, ["/", "/devices/x", "/api/v1/health/live"]
    )
    assert page.status_code == deep.status_code == 200
    assert page.text == deep.text == INDEX
    assert "sha256-" in page.headers["content-security-policy"]
    assert live.json()["status"] == "ok"


def test_e31_without_a_build_the_api_serves_and_logs_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """E.31: a missing build is one ``api.web_unavailable`` line; the API keeps answering.

    Args:
        tmp_path: Test directory.
        monkeypatch: Points ``VAKR_WEB_DIR`` at nothing.
        capsys: Captures the journal lines.

    Returns:
        None

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_WEB_DIR", str(tmp_path / "no-build"))
    page, live = serve_and_probe(tmp_path, monkeypatch, ["/", "/api/v1/health/live"])
    assert page.status_code == 404
    assert live.status_code == 200
    err = capsys.readouterr().err
    assert err.count("[api.web_unavailable]") == 1
