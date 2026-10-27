"""A demotion never half-happens: no resync marker, no role change (MPOS-003, E.39).

M6 review finding 4: ``vakr node demote`` changed the configuration to
``role = "peer"`` first and only warned when ``resync.json`` could not be
written, so the node pulled in ordinary mode, kept its divergent rows with
no report or backup, and the command exited 0.

Dependencies:
    pytest; vakr.cli.main.

Example:
    $ uv run pytest tests/unit/replication/test_demote_marker.py
"""

import tomllib
from pathlib import Path
from typing import Any

import pytest

from tests.unit.replication.repl_testkit import open_store
from vakr.cli import cmd_node, node_apply
from vakr.cli.main import main
from vakr.lifecycle.egress.errors import EgressError
from vakr.paths import InstallPaths
from vakr.replication import role_record

WEST = "https://vakr-west.example.net"


class _Down:
    """A partner that cannot be reached."""

    def get_json(self, path: str, query: Any = None) -> Any:  # noqa: ARG002
        """Fail at TCP.

        Args:
            path: The endpoint.
            query: Unused.

        Returns:
            Any: Never.

        Raises:
            EgressError: Always.
        """
        raise EgressError("tcp", "cannot connect")


@pytest.fixture
def east(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> InstallPaths:
    """An installed primary, east, with a peer URL and an unreachable partner.

    Args:
        tmp_path: The install root.
        monkeypatch: ``VAKR_ROOT`` and the partner.

    Returns:
        InstallPaths: The layout.

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    monkeypatch.setattr(cmd_node, "CLIENTS", lambda _p, _c: lambda _u: _Down())
    paths = InstallPaths.under(tmp_path)
    for directory in (paths.config_dir, paths.state_dir, paths.run_dir):
        directory.mkdir(parents=True, exist_ok=True)
    store, _ = open_store(paths.state_dir / "metadata.db")
    store.close()
    (paths.config_dir / "config.toml").write_text(
        f'config_version = 1\n[node]\nrole = "primary"\nposition = "east"\npeer_url = "{WEST}"\n'
    )
    return paths


def _role(paths: InstallPaths) -> str:
    """Read the configured role.

    Args:
        paths: The layout.

    Returns:
        str: ``node.role``.

    Raises:
        None
    """
    return tomllib.loads((paths.config_dir / "config.toml").read_text())["node"]["role"]


def test_mpos003_a_demotion_without_its_resync_marker_changes_nothing(
    east: InstallPaths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """MPOS-003, E.39: when resync.json cannot be written, the role stays and the exit is 1.

    Args:
        east: The node.
        monkeypatch: Makes the marker write fail.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    real_write = role_record.write_json

    def no_marker(path: Path, doc: Any) -> None:
        """Fail the resync marker's write only.

        Args:
            path: The record.
            doc: Its document.

        Returns:
            None

        Raises:
            OSError: For the marker.
        """
        if path.name == Path(role_record.RESYNC_FILE).name:
            raise OSError(13, "Permission denied")
        real_write(path, doc)

    monkeypatch.setattr(role_record, "write_json", no_marker)
    before = (east.config_dir / "config.toml").read_bytes()
    code = main(["node", "demote", "--yes"])
    _, err = capsys.readouterr()
    assert code == 1
    assert "resync marker" in err
    assert "configuration not changed" in err
    assert (east.config_dir / "config.toml").read_bytes() == before
    assert _role(east) == "primary"
    assert not (east.state_dir / role_record.ROLE_FILE).exists()


def test_mpos003_a_demotion_whose_config_cannot_be_written_leaves_no_marker(
    east: InstallPaths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """MPOS-003, E.39: the marker is removed again when the configuration write fails.

    Args:
        east: The node.
        monkeypatch: Makes the configuration write fail.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """

    def no_config(*_args: Any) -> None:
        """Fail like a read-only /etc.

        Args:
            *_args: Ignored.

        Returns:
            None

        Raises:
            OSError: Always.
        """
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(node_apply, "write_node_edit", no_config)
    code = main(["node", "demote", "--yes"])
    _, err = capsys.readouterr()
    assert code == 1
    assert "Read-only file system" in err
    assert _role(east) == "primary"
    assert not (east.state_dir / role_record.RESYNC_FILE).exists()
