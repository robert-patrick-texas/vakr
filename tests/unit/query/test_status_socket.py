"""``vakr status`` and ``vakr tui`` over the ``vakr-api`` socket answer as ``--local`` (E.29 #10).

A relocated install gets real, migrated SQLite databases (``seed``). The
real API is built from it exactly as the service builds itself
(``build_parts``) and served on a Unix socket with ``PeerProtocol``; the
test account is an operator by its own GID. Both paths read through the
same manual clock, so the text and the JSON must match line for line,
apart from the data-path line and the ``source`` field (13.1, TUI-007).

The failure paths are the 13.4 ones: no socket (the API is not running, so
the databases are read and the output says so), a socket nothing answers on
(exit 3 with what to run, never a quiet fallback), and a refused account.

Dependencies:
    pytest; vakr.app.api.build; tests.unit.query.test_status_cli; tests.unit.client.client_testkit.

Example:
    $ uv run pytest tests/unit/query/test_status_socket.py
"""

import json
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from tests.unit.auth.auth_testkit import cheap_hasher
from tests.unit.client.client_testkit import ROOT, api_socket, short_dir
from tests.unit.query.test_status_cli import run, seed
from vakr.api.app import create_app
from vakr.app.api.build import build_parts
from vakr.auth import Passwords
from vakr.cli import status_stores
from vakr.cli.main import main
from vakr.client.status import SocketStatusSource
from vakr.paths import InstallPaths
from vakr.ports.clock import ManualClock
from vakr.query import StatusQueries
from vakr.tui.app import VakrTui

CHEAP = Passwords(cheap_hasher())


@dataclass(frozen=True, slots=True)
class Node:
    """A relocated monitoring node.

    Attributes:
        paths: Its layout.
        socket: ``api.socket_path``.
        clock: The clock both paths read.
    """

    paths: InstallPaths
    socket: Path
    clock: ManualClock


@pytest.fixture
def node(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[Node]:
    """Relocate the install, point ``api.socket_path`` at a short path, freeze the local clock.

    Args:
        tmp_path: Install root.
        monkeypatch: ``VAKR_ROOT`` and the local path's clock.

    Returns:
        Generator[Node]: The node; its databases are not created yet.

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    paths = InstallPaths.under(tmp_path)
    for directory in (paths.config_dir, paths.state_dir, paths.run_dir):
        directory.mkdir(parents=True, exist_ok=True)
    clock = ManualClock(datetime.now(UTC).replace(microsecond=0))
    monkeypatch.setattr(status_stores, "StatusQueries", partial(StatusQueries, clock=clock))
    with short_dir() as d:
        (paths.config_dir / "config.toml").write_text(
            f'config_version = 1\n\n[api]\nlisten_address = ""\nsocket_path = "{d / "api.sock"}"\n'
        )
        yield Node(paths, d / "api.sock", clock)


@contextmanager
def served(node: Node, *, refuse: bool = False) -> Generator[None]:
    """Run the node's API on its socket, built as ``vakr-api`` builds itself.

    Args:
        node: The node.
        refuse: Refuse the test account (403 ``peer_refused``).

    Returns:
        Generator[None]: While serving; the socket file stays afterwards, as after a crash.

    Raises:
        None
    """
    parts = build_parts(node.paths, clock=node.clock, passwords=CHEAP)
    try:
        with api_socket(create_app(parts.context), node.socket, refuse=refuse):
            yield
    finally:
        parts.close()


def without_source(out: str, json_out: bool) -> tuple[Any, str]:
    """Split the data path from the output, so both paths can be compared.

    Args:
        out: Standard output.
        json_out: Whether it is a ``--json`` document.

    Returns:
        tuple: The rest of the output, and the data path (``source`` or the ``data:`` line).

    Raises:
        None
    """
    if json_out:
        doc = json.loads(out)
        return doc, doc.pop("source", "")
    lines = out.splitlines()
    data = [line for line in lines if line.startswith("data: ")]
    return [line for line in lines if not line.startswith("data: ")], "".join(data)


CASES = (
    (), ("--json",), ("core1",), ("core1", "--json"), ("--bucket", "attention"),
    ("--search", "sw", "--sort", "name"), ("--offset", "1", "--limit", "1", "--json"),
    ("nosuch",), ("nosuch", "--json"), ("--limit", "0"),
)  # fmt: skip


@pytest.mark.parametrize(("telemetry", "down"), [(True, False), (True, True), (False, False)],
                         ids=["ok", "down", "no-telemetry"])  # fmt: skip
def test_e29_10_status_over_the_socket_matches_local(
    node: Node, capsys: pytest.CaptureFixture[str], telemetry: bool, down: bool
) -> None:
    """E.29 #10, TUI-007: exit codes, text, and JSON are identical apart from the data path.

    Args:
        node: The node.
        capsys: Output capture.
        telemetry: Whether the node has a telemetry database.
        down: Whether ``sw2`` is unreachable (exit 1).

    Returns:
        None

    Raises:
        None
    """
    core = seed(node.paths.state_dir, telemetry=telemetry, down=down)
    with served(node):
        for args in (*CASES, (str(core.id),), (str(core.id), "--json")):
            api, local = run(capsys, *args), run(capsys, *args, "--local")
            assert api[0] == local[0], args
            assert api[2] == local[2], args
            json_out = "--json" in args
            api_body, api_source = without_source(api[1], json_out)
            local_body, local_source = without_source(local[1], json_out)
            assert api_body == local_body, args
            assert (api_source, local_source) in (
                ("api", "local"), ("data: vakr-api socket", "data: local databases (--local)"),
                ("", ""),  # nothing printed, or the error document, which names no source
            ), args  # fmt: skip
        expected = 3 if not telemetry else (1 if down else 0)
        assert run(capsys)[0] == run(capsys, "--local")[0] == expected


def test_13_1_without_a_socket_the_databases_are_read_and_the_output_says_so(
    node: Node, capsys: pytest.CaptureFixture[str]
) -> None:
    """13.1, PKG-003: vakr-api ships disabled; the commands still work and say where data came from.

    Args:
        node: The node (no API running).
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    seed(node.paths.state_dir)
    code, out, _ = run(capsys)
    assert code == 0
    assert "data: local databases (vakr-api is not running)" in out.splitlines()
    code, out, _ = run(capsys, "--json")
    assert (code, json.loads(out)["source"]) == (0, "local")


def test_13_4_a_socket_that_does_not_answer_is_never_quietly_replaced(
    node: Node, capsys: pytest.CaptureFixture[str]
) -> None:
    """13.4: a stale socket exits 3 with what to run; ``--local`` is the explicit way round.

    Args:
        node: The node.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    seed(node.paths.state_dir)
    with served(node):
        assert run(capsys)[0] == 0
    assert node.socket.exists()  # what a crash leaves behind
    code, out, err = run(capsys)
    assert (code, out) == (3, "")
    assert err.startswith(f"vakr status: nothing answers on {node.socket}: vakr-api stopped")
    for hint in ("systemctl status vakr-api", "vakr doctor", "add --local"):
        assert hint in err
    code, out, _ = run(capsys, "core1", "--json")
    assert (code, json.loads(out)["error"].startswith("nothing answers on")) == (3, True)
    assert run(capsys, "--local")[0] == 0


@pytest.mark.skipif(ROOT, reason="root is admin on the socket by rule")
def test_e30_2_a_refused_account_exits_unknown_with_the_way_in(
    node: Node, capsys: pytest.CaptureFixture[str]
) -> None:
    """E.30 #2: an account outside root, vakr-admin, and vakr is told how to join; exit 3.

    Args:
        node: The node.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    seed(node.paths.state_dir)
    with served(node, refuse=True):
        code, _, err = run(capsys)
    assert code == 3
    assert "add the account to the vakr group (sudo usermod -aG vakr NAME" in err


def test_tui_opens_on_the_socket_and_refuses_one_that_does_not_answer(
    node: Node, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """13.1, 13.4: ``vakr tui`` reads through vakr-api and says so; a dead socket stops it at once.

    Args:
        node: The node.
        capsys: Output capture.
        monkeypatch: Replaces the app's main loop.

    Returns:
        None

    Raises:
        None
    """
    started: dict[str, Any] = {}

    def fake_run(app: VakrTui) -> None:
        """Record the app's data source instead of taking over the terminal.

        Args:
            app: The app.

        Returns:
            None

        Raises:
            None
        """
        started.update(source=app.source, data=app.data)

    monkeypatch.setattr(VakrTui, "run", fake_run)
    seed(node.paths.state_dir)
    with served(node):
        assert main(["tui"]) == 0
    assert isinstance(started["source"], SocketStatusSource)
    assert started["data"] == "vakr-api socket"
    started.clear()
    assert main(["tui"]) == 3
    assert started == {}
    err = capsys.readouterr().err
    assert err.startswith(f"vakr tui: nothing answers on {node.socket}")
    assert "vakr doctor" in err
