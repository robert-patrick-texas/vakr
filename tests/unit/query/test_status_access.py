"""The local path for an account that cannot read the databases (E.30 #16, 13.4).

A member of ``vakr-view`` uses the socket only: the group grants no access
to the state directory. When ``vakr-api`` is not running, or with
``--local``, ``vakr status`` must say that this account cannot read the
databases and how to get an answer, never "no data yet", which is how a
``metadata.db`` it cannot see would otherwise read. The state directory is
made untraversable for the test; as root the check cannot apply, so the
test is skipped there.

Dependencies:
    pytest; tests.unit.query.test_status_cli (seed, run), tests.unit.client.client_testkit.

Example:
    $ uv run pytest tests/unit/query/test_status_access.py
"""

from collections.abc import Generator
from pathlib import Path

import pytest

from tests.unit.client.client_testkit import ROOT, short_dir
from tests.unit.query.test_status_cli import run, seed
from vakr.paths import InstallPaths


@pytest.fixture
def locked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    """A seeded node with no API whose state directory this account cannot enter.

    Args:
        tmp_path: Install root.
        monkeypatch: ``VAKR_ROOT``.

    Returns:
        Generator[Path]: The state directory; its mode is restored afterwards.

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    paths = InstallPaths.under(tmp_path)
    for directory in (paths.config_dir, paths.state_dir, paths.run_dir):
        directory.mkdir(parents=True, exist_ok=True)
    seed(paths.state_dir)
    with short_dir() as d:
        (paths.config_dir / "config.toml").write_text(
            f'config_version = 1\n\n[api]\nlisten_address = ""\nsocket_path = "{d / "api.sock"}"\n'
        )
        # A listing but no traversal: how 0750 vakr:vakr looks to an account outside vakr.
        paths.state_dir.chmod(0o600)
        try:
            yield paths.state_dir
        finally:
            paths.state_dir.chmod(0o750)


@pytest.mark.skipif(ROOT, reason="root may enter every directory")
@pytest.mark.parametrize(
    ("args", "says"),
    [
        ((), "vakr-api is not running (no "),
        (("--json",), "vakr-api is not running (no "),
        (("--local",), "--local reads the databases in "),
    ],
)
def test_e30_16_an_account_without_file_access_is_told_why(
    locked: Path, capsys: pytest.CaptureFixture[str], args: tuple[str, ...], says: str
) -> None:
    """E.30 #16, 13.4: UNKNOWN with the real reason and the way round it, not "no data yet".

    Args:
        locked: The unreadable state directory.
        capsys: Output capture.
        args: Arguments after ``status``.
        says: Start of the expected reason.

    Returns:
        None

    Raises:
        None
    """
    code, out, err = run(capsys, *args)
    text = out + err
    assert code == 3
    assert says in text
    assert f"this account cannot read the databases in {locked}" in text or (
        f"{locked}, which this account cannot read" in text
    )
    assert "run the command with sudo" in text
    assert "no data yet" not in text
