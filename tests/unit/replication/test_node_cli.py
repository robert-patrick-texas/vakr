"""CLI: ``vakr node promote``, ``demote``, and ``status`` on an installed node (MPOS-003, E.39).

The partner client is replaced by a fake, ``systemctl`` is never run
(``VAKR_ROOT`` is set), and everything else is real: the configuration
files, the backup, the role record, the resync marker, and the audit
record in ``metadata.db``.

Dependencies:
    pytest; vakr.cli.main.

Example:
    $ uv run pytest tests/unit/replication/test_node_cli.py
"""

import json
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.unit.replication.repl_testkit import open_store
from vakr.cli import cmd_node
from vakr.cli.main import main
from vakr.domain.paging import PageRequest
from vakr.lifecycle.egress.errors import EgressError
from vakr.paths import InstallPaths
from vakr.replication import role_record, status

EAST = "https://vakr-east.example.net"
WEST = "https://vakr-west.example.net"


class _Partner:
    """The other node's ``metadata/state``, or a failure."""

    def __init__(self, answer: Any) -> None:
        """Bind it.

        Args:
            answer: A state document or an exception.

        Returns:
            None

        Raises:
            None
        """
        self.answer = answer

    def get_json(self, path: str, query: Any = None) -> Any:  # noqa: ARG002
        """Answer.

        Args:
            path: The endpoint.
            query: Unused.

        Returns:
            Any: The answer.

        Raises:
            Exception: The bound failure.
        """
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


@pytest.fixture
def west(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> InstallPaths:
    """An installed peer, west, whose role lives in a drop-in, with one successful pull.

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
    for directory in (paths.config_dir / "conf.d", paths.state_dir, paths.run_dir):
        directory.mkdir(parents=True, exist_ok=True)
    store, _ = open_store(paths.state_dir / "metadata.db")
    store.close()
    (paths.config_dir / "config.toml").write_text("config_version = 1\n")
    (paths.config_dir / "conf.d" / "50-role.toml").write_text(
        f'[node]\nrole = "peer"\nposition = "west"\nprimary_url = "{EAST}"\n'
    )

    status.record(paths.state_dir / status.FILE, "unchanged", datetime.now(UTC), digest="0" * 64)
    return paths


def _partner(monkeypatch: pytest.MonkeyPatch, answer: Any) -> None:
    """Make the partner answer this.

    Args:
        monkeypatch: Replaces ``cmd_node.CLIENTS``.
        answer: What it answers.

    Returns:
        None

    Raises:
        None
    """
    monkeypatch.setattr(cmd_node, "CLIENTS", lambda _p, _c: lambda _url: _Partner(answer))


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


def _node(paths: InstallPaths) -> dict[str, Any]:
    """Read the drop-in's ``[node]`` table.

    Args:
        paths: The layout.

    Returns:
        dict: The table.

    Raises:
        None
    """
    return tomllib.loads((paths.config_dir / "conf.d" / "50-role.toml").read_text())["node"]


def _audit(paths: InstallPaths) -> list[str]:
    """List the audited actions, newest first.

    Args:
        paths: The layout.

    Returns:
        list[str]: The actions.

    Raises:
        None
    """
    store, factory = open_store(paths.state_dir / "metadata.db")
    try:
        with factory() as uow:
            return [e.action for e in uow.audit.list_page(PageRequest()).items]
    finally:
        store.close()


def test_mpos003_promote_refuses_while_the_primary_answers_and_changes_nothing(
    west: InstallPaths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """MPOS-003, E.39: a primary that still answers blocks promotion; the files stay as they were.

    Args:
        west: The node.
        monkeypatch: The fake partner.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    before = (west.config_dir / "conf.d" / "50-role.toml").read_bytes()
    _partner(monkeypatch, {"role": "primary", "position": "east"})
    code, out, err = _run(capsys, "node", "promote", "--yes")
    assert code == 1
    assert "newest metadata pull" in out
    assert "still answers" in err
    assert "--force" in err
    assert (west.config_dir / "conf.d" / "50-role.toml").read_bytes() == before
    assert not (west.state_dir / role_record.ROLE_FILE).exists()


def test_mpos003_promote_with_the_primary_gone_makes_this_node_primary(
    west: InstallPaths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """MPOS-003, E.39: config rewritten (backup kept), old values recorded, audited, partner kept.

    Args:
        west: The node.
        monkeypatch: The fake partner.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    _partner(monkeypatch, EgressError("tcp", "cannot connect to vakr-east.example.net:443"))
    code, out, err = _run(capsys, "node", "promote", "--yes")
    assert (code, err) == (0, "")
    assert _node(west) == {"role": "primary", "position": "west", "peer_url": EAST}
    assert "not restarted (VAKR_ROOT is set)" in out
    change = role_record.read_changes(west.state_dir / role_record.ROLE_FILE)[-1]
    assert change["action"] == "promote"
    assert change["before"]["primary_url"] == EAST
    assert change["after"]["role"] == "primary"
    assert change["forced"] is False
    assert change["partner"]["answered"] is False
    assert change["last_pull_at"]
    backup = Path(change["config_backup"])
    assert 'role = "peer"' in (backup / "conf.d" / "50-role.toml").read_text()
    assert _audit(west)[0] == "node.promote"
    served = role_record.promotion(west.state_dir / role_record.ROLE_FILE)
    assert served is not None
    assert served["fork_at"] == change["last_pull_at"]
    code, _, err = _run(capsys, "node", "promote", "--yes")
    assert code == 1
    assert "already a primary" in err


def test_mpos003_force_overrides_an_answering_primary_and_is_recorded(
    west: InstallPaths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """MPOS-003, E.39: --force goes past the refusal, says so, and records it.

    Args:
        west: The node.
        monkeypatch: The fake partner.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    _partner(monkeypatch, {"role": "primary", "position": "east"})
    code, out, _ = _run(capsys, "node", "promote", "--yes", "--force", "--no-restart")
    assert code == 0
    assert "forced past: the primary at" in out
    assert "not restarted (--no-restart)" in out
    assert role_record.read_changes(west.state_dir / role_record.ROLE_FILE)[-1]["forced"] is True


def test_mpos003_demote_makes_the_old_primary_a_peer_that_resyncs(
    west: InstallPaths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """MPOS-003, E.39: demotion points at the new primary and leaves the resync marker.

    Args:
        west: The node, made a primary first.
        monkeypatch: The fake partner.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    _partner(monkeypatch, EgressError("tcp", "down"))
    assert _run(capsys, "node", "promote", "--yes")[0] == 0
    _partner(monkeypatch, {"role": "primary", "position": "east"})
    partner = west.state_dir / role_record.PARTNER_FILE
    before = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    role_record.write_json(partner, {"role": "primary", "position": "east", "seen_at": before})
    code, out, _ = _run(capsys, "node", "status")
    assert "CRITICAL" not in out  # seen as its primary, before the promotion
    after = (datetime.now(UTC) + timedelta(seconds=5)).isoformat()
    role_record.write_json(partner, {"role": "primary", "position": "east", "seen_at": after})
    code, out, _ = _run(capsys, "node", "status")
    assert code == 0
    assert "CRITICAL two primaries" in out
    assert "east looks like the old primary" in out
    code, out, err = _run(capsys, "node", "demote", "--yes", "--primary-url", WEST)
    assert (code, err) == (0, "")
    assert _node(west) == {"role": "peer", "position": "west", "primary_url": WEST}
    marker = role_record.read_json(west.state_dir / role_record.RESYNC_FILE)
    assert marker is not None
    assert marker["primary_url"] == WEST
    assert _audit(west)[:2] == ["node.demote", "node.promote"]
    code, out, _ = _run(capsys, "node", "status", "--json")
    data = json.loads(out)
    assert data["role"] == "peer"
    assert data["resync_pending"]["primary_url"] == WEST
    assert [c["action"] for c in data["role_changes"]] == ["promote", "demote"]
    code, _, err = _run(capsys, "node", "demote", "--yes")
    assert code == 1
    assert "already a peer" in err


@pytest.mark.usefixtures("west")
def test_mpos003_node_commands_need_root(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """MPOS-003: without root on an installed node, promote and demote refuse before checking.

    Args:
        monkeypatch: Drops ``VAKR_ROOT``.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    monkeypatch.delenv("VAKR_ROOT")
    monkeypatch.setattr(cmd_node.os, "geteuid", lambda: 1000)
    for action in ("promote", "demote"):
        code, _, err = _run(capsys, "node", action, "--yes")
        assert code == 1
        assert "sudo" in err
    assert _run(capsys, "node")[0] == 2


def test_e39_only_the_two_role_readers_are_restarted(monkeypatch: pytest.MonkeyPatch) -> None:
    """E.39: try-restart of vakr-collector and vakr-api only; a failure says what to run.

    Args:
        monkeypatch: Drops ``VAKR_ROOT``.

    Returns:
        None

    Raises:
        None
    """
    import subprocess

    from vakr.cli import node_support

    monkeypatch.delenv("VAKR_ROOT", raising=False)
    calls: list[list[str]] = []

    def ok(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        """Record the call and succeed.

        Args:
            argv: The command.
            **_kw: ``subprocess.run`` options.

        Returns:
            subprocess.CompletedProcess[str]: Success.

        Raises:
            None
        """
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    def bad(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        """Fail as systemctl does.

        Args:
            argv: The command.
            **_kw: ``subprocess.run`` options.

        Returns:
            subprocess.CompletedProcess[str]: A failure with a message.

        Raises:
            None
        """
        return subprocess.CompletedProcess(argv, 1, "", "Job failed.\nunit vakr-api failed")

    assert "restarted vakr-collector.service and vakr-api.service" in \
        node_support.restart_services(ok)  # fmt: skip
    assert calls == [["/usr/bin/systemctl", "try-restart", "vakr-collector.service",
                      "vakr-api.service"]]  # fmt: skip
    failed = node_support.restart_services(bad)
    assert "unit vakr-api failed" in failed
    assert "sudo systemctl restart" in failed
    assert node_support.confirm("go?", yes=True)


def test_e39_a_failed_audit_write_is_reported_not_fatal(
    west: InstallPaths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """E.39: without the metadata database the role still changes, with a warning.

    Args:
        west: The node.
        monkeypatch: The fake partner.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    (west.state_dir / "metadata.db").unlink()
    _partner(monkeypatch, EgressError("dns", "cannot resolve vakr-east.example.net"))
    code, _, err = _run(capsys, "node", "promote", "--yes")
    assert code == 0
    assert "warning" in err
    assert "metadata.db" in err
    assert _node(west)["role"] == "primary"


def test_e39_node_status_summarises_the_resync_report(
    west: InstallPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    """E.39: the peer's primary, the newest pull, and the resync report's totals and first rows.

    Args:
        west: The node (a peer).
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    rows = [{"table": "device", "key": f"k{n}", "label": f"edge{n}", "change": "removed"}
            for n in range(25)]  # fmt: skip
    edit = {"at": "2026-10-27T11:20:05+00:00", "actor": "cli:bob", "action": "device.create",
            "summary": "created edge9"}  # fmt: skip
    report = {"at": "2026-10-27T12:03:15+00:00", "primary_url": EAST,
              "fork_at": "2026-10-27T09:58:12+00:00", "backup": "/var/lib/vakr/backups/m.db",
              "discarded_total": 25, "discarded": rows, "edits_total": 1,
              "edits": [edit]}  # fmt: skip
    role_record.write_json(west.state_dir / role_record.REPORT_FILE, report)
    code, out, _ = _run(capsys, "node", "status")
    assert code == 0
    assert f"primary {EAST}" in out
    assert "newest metadata pull: 20" in out
    assert "25 local row(s) discarded, 1 metadata edit(s) here since 2026-10-27T09:58:12" in out
    assert "  discarded removed device edge0" in out
    assert "  edit 2026-10-27T11:20:05+00:00 cli:bob device.create: created edge9" in out
    assert "vakr node status --json" in out
    assert "edge24" not in out
