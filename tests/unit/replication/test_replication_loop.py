"""The collector's pull loop and the registered ``replication`` check (MPOS-002, MPOS-006).

Dependencies:
    pytest; vakr.app.collector.replication_loop, vakr.health.replication_check.

Example:
    $ uv run pytest tests/unit/replication/test_replication_loop.py
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from vakr.app.collector import replication_loop
from vakr.config.loader import EffectiveConfig
from vakr.health.model import Status
from vakr.health.replication_check import check_replication
from vakr.paths import InstallPaths
from vakr.ports.clock import ManualClock
from vakr.replication import status
from vakr.replication.metadata_pull import PullError

PEER = {"node": {"role": "peer", "position": "west", "primary_url": "https://east.example"}}


def _service(paths: InstallPaths, config: dict) -> SimpleNamespace:
    """A stand-in for the running collector service.

    Args:
        paths: The layout.
        config: Configuration tables.

    Returns:
        SimpleNamespace: ``parts``, ``clock``, and ``loops_stop``.

    Raises:
        None
    """
    parts = SimpleNamespace(paths=paths, config=EffectiveConfig(config), uow_factory=None)
    clock = ManualClock(datetime(2026, 10, 22, tzinfo=UTC))
    return SimpleNamespace(parts=parts, clock=clock, loops_stop=asyncio.Event())


def test_mpos002_a_primary_idles_and_a_peer_without_a_token_records_why(tmp_path: Path) -> None:
    """MPOS-002, MPOS-006: the loop never blocks a primary; a missing token is node health.

    Args:
        tmp_path: The install root.

    Returns:
        None

    Raises:
        None
    """
    paths = InstallPaths.under(tmp_path)
    primary = _service(paths, {})

    async def idle() -> None:
        """Run the primary's loop until it is told to stop.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        task = asyncio.create_task(replication_loop.metadata_pull_loop(primary))
        await asyncio.sleep(0)
        primary.loops_stop.set()
        await asyncio.wait_for(task, 1)

    asyncio.run(idle())
    with pytest.raises(PullError, match="replication token"):
        replication_loop._pull(_service(paths, PEER))
    found = status.read_status(paths.state_dir / status.FILE)
    assert found is not None
    assert found.pulls["failed"] == 1
    assert found.last_success_at is None


def test_mpos006_the_check_is_ok_on_a_primary_and_ages_a_peer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MPOS-006: OK on a primary; on a peer the newest success decides.

    Args:
        tmp_path: The install root.
        monkeypatch: ``VAKR_ROOT``.

    Returns:
        None

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    paths = InstallPaths.under(tmp_path)
    for directory in (paths.config_dir, paths.state_dir):
        directory.mkdir(parents=True, exist_ok=True)
    config = paths.config_dir / "config.toml"
    config.write_text("config_version = 1\n")
    assert check_replication().status is Status.OK
    config.write_text('config_version = 1\n[node]\nrole = "peer"\nposition = "west"\n'
                      'primary_url = "https://east.example"\n')  # fmt: skip
    assert check_replication().status is Status.WARNING  # no pull recorded yet
    status.record(paths.state_dir / status.FILE, "applied", datetime.now(UTC), digest="a" * 64)
    result = check_replication()
    assert result.status is Status.OK
    assert result.perfdata[0].label == "replication_metadata_age_s"
