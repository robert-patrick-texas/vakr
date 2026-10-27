"""Evidence of a second primary goes stale: CRITICAL only while it is fresh (MPOS-006, E.39).

M6 review finding 5: the two-primaries CRITICAL came from the newest
``partner.json`` with no age limit. An old primary switched off instead of
demoted never answers again, so the record kept ``role = primary`` and the
CRITICAL never cleared. Now the answer counts as now for ``FRESH_FOR``
(5 minutes); after that it is a WARNING saying when the partner was last
seen as a primary, and an answer as a peer clears it.

Dependencies:
    pytest; vakr.replication.conflict, vakr.health.replication_check.

Example:
    $ uv run pytest tests/unit/replication/test_partner_freshness.py
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.unit.replication.repl_testkit import open_store
from vakr.cli.main import main
from vakr.config.node import NodeSettings
from vakr.health.model import Status
from vakr.health.replication_check import check_replication
from vakr.paths import InstallPaths
from vakr.replication import role_record
from vakr.replication.conflict import FRESH_FOR, conflict_at
from vakr.replication.overview import replication_reader

WEST = "https://vakr-west.example.net"
EAST = NodeSettings("primary", "east", "", WEST)


@pytest.fixture
def east(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> InstallPaths:
    """An installed primary, east, with a peer URL.

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
    (paths.config_dir / "config.toml").write_text(
        f'config_version = 1\n[node]\nrole = "primary"\nposition = "east"\npeer_url = "{WEST}"\n'
    )
    return paths


def _seen(paths: InstallPaths, role: str, ago: timedelta) -> None:
    """Record the partner's answer as the observation puller does.

    Args:
        paths: The layout.
        role: What it said it is.
        ago: How long ago.

    Returns:
        None

    Raises:
        None
    """
    at = (datetime.now(UTC) - ago).isoformat()
    role_record.write_json(paths.state_dir / role_record.PARTNER_FILE,
                           {"role": role, "position": "west", "seen_at": at})  # fmt: skip


def test_mpos006_stale_evidence_of_a_second_primary_is_a_warning_not_critical(
    east: InstallPaths,
) -> None:
    """MPOS-006, E.39: CRITICAL while fresh; WARNING with the time once stale; clear on a peer.

    Args:
        east: The node.

    Returns:
        None

    Raises:
        None
    """
    _seen(east, "primary", timedelta(seconds=30))
    fresh = check_replication()
    assert fresh.status is Status.CRITICAL
    assert "two primaries" in fresh.summary
    _seen(east, "primary", timedelta(minutes=20))
    stale = check_replication()
    assert stale.status is Status.WARNING
    assert "two primaries" not in stale.summary
    assert "last seen as a primary at" in stale.summary
    assert not any(p.label == "replication_primaries" for p in stale.perfdata)
    _seen(east, "peer", timedelta(minutes=20))
    summary = check_replication().summary
    assert "two primaries" not in summary
    assert "last seen as a primary" not in summary


def test_e39_the_freshness_bound_and_what_each_view_shows(east: InstallPaths) -> None:
    """E.39: the bound is 5 minutes; the health line and node status say which case it is.

    Args:
        east: The node.

    Returns:
        None

    Raises:
        None
    """
    now = datetime.now(UTC)
    assert timedelta(minutes=5) == FRESH_FOR
    _seen(east, "primary", FRESH_FOR - timedelta(seconds=5))
    found = conflict_at(east.state_dir, EAST, now)
    assert found is not None
    assert found.critical
    line = replication_reader(east.state_dir, EAST)()
    assert line is not None
    assert "TWO PRIMARIES" in line.text
    _seen(east, "primary", FRESH_FOR + timedelta(seconds=5))
    found = conflict_at(east.state_dir, EAST, now)
    assert found is not None
    assert not found.critical
    assert "remove node.peer_url" in found.text
    line = replication_reader(east.state_dir, EAST)()
    assert line is not None
    assert "TWO PRIMARIES" not in line.text
    assert "last seen as a primary" in line.text


def test_e39_node_status_reports_a_stale_primary_as_a_warning(
    east: InstallPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    """E.39: ``vakr node status`` says WARNING for stale evidence, CRITICAL for fresh.

    Args:
        east: The node.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    _seen(east, "primary", timedelta(hours=2))
    assert main(["node", "status"]) == 0
    out = capsys.readouterr().out
    assert "WARNING partner west was last seen as a primary" in out
    assert "CRITICAL" not in out
    assert main(["node", "status", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["two_primaries"] is None
    assert "last seen as a primary" in data["stale_primary"]
    _seen(east, "primary", timedelta(seconds=10))
    assert main(["node", "status", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert "two primaries" in data["two_primaries"]
    assert data["stale_primary"] is None
