"""CLI: ``vakr replication``, setup node flags, and every peer refusal (MPOS-003, MPOS-006).

Dependencies:
    pytest; vakr.cli.main.

Example:
    $ uv run pytest tests/unit/replication/test_replication_cli.py
"""

import json
from pathlib import Path

import pytest

from tests.unit.replication.repl_testkit import open_store
from vakr.cli.main import main
from vakr.paths import InstallPaths

PRIMARY = "https://vakr-east.example.net"
PEER_TABLE = f'\n[node]\nrole = "peer"\nposition = "west"\nprimary_url = "{PRIMARY}"\n'


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> InstallPaths:
    """An installed node with a migrated metadata database and a minimal config.

    Args:
        tmp_path: The install root.
        monkeypatch: ``VAKR_ROOT``.

    Returns:
        InstallPaths: The layout.

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    paths = InstallPaths.under(tmp_path)
    for directory in (paths.config_dir, paths.state_dir, paths.run_dir):
        directory.mkdir(parents=True, exist_ok=True)
    store, _ = open_store(paths.state_dir / "metadata.db")
    store.close()
    (paths.config_dir / "config.toml").write_text("config_version = 1\n")
    return paths


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    """Run ``vakr`` and capture its output.

    Args:
        capsys: Output capture.
        *argv: Arguments.

    Returns:
        tuple: Exit status, stdout, stderr.

    Raises:
        None
    """
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


@pytest.mark.usefixtures("root")
def test_mpos006_token_create_shows_it_once_and_list_never(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """MPOS-006: the token is printed by create only; revoke refuses a pasted token unechoed.

    Args:
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    code, out, _ = _run(capsys, "replication", "token", "create", "west", "--lifetime", "30d")
    token = next(w for w in out.split() if w.startswith("vkr_"))
    assert code == 0
    assert "replication.token" in out
    code, out, _ = _run(capsys, "replication", "token", "list", "--json")
    rows = json.loads(out)["replication_tokens"]
    assert code == 0
    assert [r["label"] for r in rows] == ["west"]
    assert token not in out
    code, _, err = _run(capsys, "replication", "token", "revoke", token)
    assert code == 1
    assert token not in err
    assert _run(capsys, "replication", "token", "create", "x", "--lifetime", "731d")[0] == 1
    assert _run(capsys, "replication", "token", "revoke", "west")[0] == 0
    assert _run(capsys, "replication", "token", "revoke", "west")[0] == 1


@pytest.mark.parametrize("argv", [
    ("user", "add", "bob", "--role", "operator"),
    ("user", "disable", "bob"),
    ("inventory", "apply"),
    ("interface", "review", "new", "00000000-0000-7000-8000-000000000001"),
    ("inventory", "rediscover", "core1"),
])  # fmt: skip
def test_mpos_t03_cli_metadata_edits_are_refused_on_a_peer(
    root: InstallPaths, capsys: pytest.CaptureFixture[str], argv: tuple[str, ...]
) -> None:
    """MPOS-003, MPOS-T03: each write names the primary and exits 1; nothing is touched.

    Args:
        root: The node.
        capsys: Output capture.
        argv: The command.

    Returns:
        None

    Raises:
        None
    """
    with (root.config_dir / "config.toml").open("a") as handle:
        handle.write(PEER_TABLE)
    code, _, err = _run(capsys, *argv)
    assert code == 1, err
    assert f"its primary is {PRIMARY}" in err, err


def test_mpos001_setup_writes_the_node_table_and_a_peer_creates_no_admin(
    root: InstallPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    """MPOS-001, MPOS-003: setup records role, position, and primary; a bad URL changes nothing.

    Args:
        root: The node.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    config = root.config_dir / "config.toml"
    config.write_text("config_version = 1  # keep me\n")
    config.chmod(0o640)
    code, _, err = _run(capsys, "setup", "--role", "peer", "--position", "west",
                        "--primary-url", "http://not-https")  # fmt: skip
    assert code == 1
    assert "https://" in err
    assert "node" not in config.read_text()
    code, out, _ = _run(capsys, "setup", "--role", "peer", "--position", "west",
                        "--primary-url", PRIMARY)  # fmt: skip
    text = config.read_text()
    assert code == 0
    assert "# keep me" in text
    assert 'role = "peer"' in text
    assert PRIMARY in text
    assert "creates no admin" in out
    assert "replication token create west" in out
    assert config.stat().st_mode & 0o777 == 0o640
    code, out, _ = _run(capsys, "replication", "status", "--json")
    assert code == 0
    assert json.loads(out)["role"] == "peer"
    assert json.loads(out)["metadata_pull"] is None
