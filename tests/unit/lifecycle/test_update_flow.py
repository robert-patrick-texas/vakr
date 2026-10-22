"""Self-update flows with injected HTTP, installer, and hooks (UPD-T01, UPD-T03, UPD-T05).

Releases are signed for real with a throwaway ed25519 key and verified with
``ssh-keygen``; only the network, ``apt-get``, ``systemctl``, and the new
binary are faked.

Dependencies:
    tests.unit.lifecycle.helpers: ``Harness`` and fakes.

Example:
    $ uv run pytest tests/unit/lifecycle/test_update_flow.py
"""

import sqlite3
import tarfile
from pathlib import Path

import pytest

from tests.unit.lifecycle.helpers import Harness
from vakr.lifecycle.update.lock import LockBusyError, exclusive_lock
from vakr.lifecycle.update.policy import Request, crossed_marker
from vakr.lifecycle.update.state import load_state
from vakr.lifecycle.update.versions import Version

V = Version.parse


def yes(question: str) -> bool:
    """Confirm every question.

    Args:
        question: Ignored prompt.

    Returns:
        bool: Always ``True``.

    Raises:
        None
    """
    del question
    return True


def _seed(h: Harness) -> None:
    """Create operator config and a metadata database to back up.

    Args:
        h: Harness.

    Returns:
        None

    Raises:
        None
    """
    h.kit.layout.config_dir.mkdir(parents=True)
    (h.kit.layout.config_dir / "config.toml").write_text("[update]\nchannel = 'prerelease'\n")
    h.kit.layout.db_dir.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(h.kit.layout.db_dir / "meta.db") as db:
        db.execute("create table devices (name text)")
        db.execute("insert into devices values ('rtr1')")
    db.close()


def _rows(h: Harness) -> list[tuple[str]]:
    """Read the metadata table.

    Args:
        h: Harness.

    Returns:
        list[tuple[str]]: Rows.

    Raises:
        None
    """
    with sqlite3.connect(h.kit.layout.db_dir / "meta.db") as db:
        rows = db.execute("select name from devices order by name").fetchall()
    db.close()
    return rows


def test_upd_t01_update_to_next_release(harness: Harness) -> None:
    """UPD-T01: N -> N+1 installs, runs hooks via the new binary, and summarizes.

    Args:
        harness: Updater with fakes.

    Returns:
        None

    Raises:
        None
    """
    h = harness
    _seed(h)
    h.publish("7.2.0")
    h.publish("7.3.0", body="## Changes\n- faster polling\n- new option x")
    outcome = h.updater.update(V("7.2.0"), Request(), yes)
    assert (outcome.status, outcome.exit_code) == ("updated", 0)
    assert h.installer.installed == ["vakr_7.3.0_amd64.deb"]
    assert h.runner.calls == ["config upgrade", "try-restart vakr.target", "check health"]
    state = load_state(h.kit.layout.state_file)
    assert (state.result, state.from_version, state.to_version) == ("success", "7.2.0", "7.3.0")
    with tarfile.open(state.backup) as archive:
        assert {"config/config.toml", "state/meta.db"} <= set(archive.getnames())
    cached = sorted(p.name for p in h.kit.layout.cache_root.iterdir())
    assert cached == ["7.2.0", "7.3.0"]  # the running version was fetched for rollback
    summary = "\n".join(outcome.summary("vakr"))
    assert "updated 7.2.0 -> 7.3.0" in summary
    assert "  - faster polling" in summary
    assert "update.verify.ok" in h.ids()
    assert h.updater.check(V("7.3.0"), Request()).status == "up-to-date"


def test_upd_t03_health_failure_rolls_back_and_is_not_retried(harness: Harness) -> None:
    """UPD-T03: forced health failure restores package, config, and data; no timer retry.

    Args:
        harness: Updater with fakes.

    Returns:
        None

    Raises:
        None
    """
    h = harness
    _seed(h)
    h.publish("7.2.0")
    h.publish("7.3.0")
    config = h.kit.layout.config_dir / "config.toml"
    original = config.read_text()

    def migrate(package: Path) -> None:
        """Simulate the new version rewriting config and data.

        Args:
            package: Package being installed.

        Returns:
            None

        Raises:
            None
        """
        if "7.3.0" in package.name:
            config.write_text("config_version = 99\n")
            with sqlite3.connect(h.kit.layout.db_dir / "meta.db") as db:
                db.execute("insert into devices values ('migrated')")
            db.close()
            (h.kit.layout.db_dir / "meta.db-wal").write_bytes(b"stale")

    h.installer.on_install = migrate
    h.runner.codes["check health"] = 2
    outcome = h.updater.update(V("7.2.0"), Request(), yes)
    assert (outcome.status, outcome.exit_code) == ("failed", 1)
    assert "health check failed (exit 2)" in outcome.message
    assert h.installer.installed == ["vakr_7.3.0_amd64.deb", "vakr_7.2.0_amd64.deb"]
    assert config.read_text() == original
    assert _rows(h) == [("rtr1",)]
    assert not (h.kit.layout.db_dir / "meta.db-wal").exists()
    assert h.runner.calls.count("try-restart vakr.target") == 2
    assert "update.rollback.done" in h.ids()
    state = load_state(h.kit.layout.state_file)
    assert (state.result, state.failed_versions) == ("failed", ("7.3.0",))

    timer = Request(if_newer=True, unattended=True)
    again = h.updater.update(V("7.2.0"), timer, yes)
    assert again.status == "skipped"
    assert "failed to install before" in again.message
    assert len(h.installer.installed) == 2

    h.runner.codes.clear()
    h.publish("7.4.0")
    newer = h.updater.update(V("7.2.0"), timer, yes)
    assert newer.status == "updated"
    assert load_state(h.kit.layout.state_file).failed_versions == ("7.3.0",)
    retry = h.updater.update(V("7.4.0"), Request(version=V("7.3.0")), yes)
    assert retry.status == "updated"  # an operator may retry explicitly (downgrade here)
    assert load_state(h.kit.layout.state_file).failed_versions == ()


def test_install_failure_without_rollback_package(harness: Harness) -> None:
    """A failed ``apt-get`` with no previous package still restores config and records it.

    Args:
        harness: Updater with fakes.

    Returns:
        None

    Raises:
        None
    """
    h = harness
    _seed(h)
    h.publish("7.3.0")
    h.installer.fail_for.add("vakr_7.3.0_amd64.deb")
    outcome = h.updater.update(V("7.2.0"), Request(), yes)
    assert outcome.status == "failed"
    assert "package install failed (exit 100)" in outcome.message
    assert "no verified previous package available" in outcome.message
    assert "update.rollback.unavailable" in h.ids()
    assert "update.rollback.incomplete" in h.ids()


def test_upd_t05_timer_updates_once_then_noops_and_respects_hold(harness: Harness) -> None:
    """UPD-T05: newer present -> one update; absent -> no-op; beyond hold -> no-op.

    Args:
        harness: Updater with fakes.

    Returns:
        None

    Raises:
        None
    """
    h = harness
    h.publish("7.2.0")
    h.publish("7.3.0")
    timer = Request(if_newer=True, unattended=True)
    assert h.updater.update(V("7.2.0"), timer, yes).status == "updated"
    assert h.updater.update(V("7.3.0"), timer, yes).status == "up-to-date"
    h.publish("7.4.0-rc.1", prerelease=True)
    held = Request(hold=V("7.3.0"), if_newer=True, unattended=True)
    assert h.updater.update(V("7.3.0"), held, yes).status == "up-to-date"
    explicit = Request(hold=V("7.3.0"), version=V("7.4.0-rc.1"), unattended=True)
    beyond = h.updater.update(V("7.3.0"), explicit, yes)
    assert (beyond.status, beyond.exit_code) == ("skipped", 0)
    assert "beyond hold_version 7.3.0" in beyond.message
    stable = Request(channel="stable", if_newer=True, unattended=True)
    assert h.updater.update(V("7.3.0"), stable, yes).status == "up-to-date"
    assert h.installer.installed == ["vakr_7.3.0_amd64.deb"]


def test_manual_marker_blocks_only_unattended(harness: Harness) -> None:
    """UPD-005: notes carrying the manual marker stop the timer, not an operator.

    Args:
        harness: Updater with fakes.

    Returns:
        None

    Raises:
        None
    """
    h = harness
    h.publish("7.3.0", body="Telemetry restore needed: manual-update-required")
    timer = Request(if_newer=True, unattended=True)
    blocked = h.updater.update(V("7.2.0"), timer, yes)
    assert blocked.status == "skipped"
    assert "manual update" in blocked.message
    assert h.updater.update(V("7.2.0"), Request(), lambda _question: False).status == "cancelled"
    assert h.updater.update(V("7.2.0"), Request(), yes).status == "updated"


def test_manual_marker_on_a_skipped_release_blocks_the_timer(harness: Harness) -> None:
    """UPD-005: the timer never jumps over a release that needs a manual update.

    Installing 7.4.0 from 7.2.0 would apply 7.3.0's migrations too, so 7.3.0's
    marker holds the timer even though 7.4.0's notes have none. Once 7.3.0 is
    installed, by an operator, the timer may take 7.4.0.

    Args:
        harness: Updater with fakes.

    Returns:
        None

    Raises:
        None
    """
    h = harness
    h.publish("7.3.0", body="Telemetry schema 2: manual-update-required")
    h.publish("7.4.0", body="Fixes only.")
    timer = Request(if_newer=True, unattended=True)
    blocked = h.updater.update(V("7.2.0"), timer, yes)
    assert blocked.status == "skipped"
    assert "7.4.0 includes 7.3.0" in blocked.message
    assert h.installer.installed == []
    assert h.updater.update(V("7.3.0"), timer, yes).status == "updated"
    assert h.installer.installed == ["vakr_7.4.0_amd64.deb"]


def test_crossed_marker_looks_only_strictly_between() -> None:
    """UPD-005: the installed and target releases are not "between"; the oldest marked one wins.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    mark = "x manual-update-required"
    listed = [(V("7.2.0"), mark), (V("7.3.0"), mark), (V("7.3.1"), mark), (V("7.4.0"), mark)]
    assert crossed_marker(listed, V("7.2.0"), V("7.4.0")) == V("7.3.0")
    assert crossed_marker(listed, V("7.3.1"), V("7.4.0")) is None
    assert crossed_marker([(V("7.3.0"), "fixes")], V("7.2.0"), V("7.4.0")) is None
    assert crossed_marker(listed, V("7.2.0"), V("7.4.0"), marker="") is None


def test_manual_rollback_restores_previous_version(harness: Harness) -> None:
    """UPD-005: ``--rollback`` reinstalls the cached previous package and its backup.

    Args:
        harness: Updater with fakes.

    Returns:
        None

    Raises:
        None
    """
    h = harness
    assert h.updater.rollback(V("7.2.0"), yes).status == "failed"
    _seed(h)
    h.publish("7.2.0")
    h.publish("7.3.0")
    assert h.updater.update(V("7.2.0"), Request(), yes).status == "updated"
    config = h.kit.layout.config_dir / "config.toml"
    before_update = load_state(h.kit.layout.state_file)
    config.write_text("changed after update\n")
    assert h.updater.rollback(V("7.3.0"), lambda _question: False).status == "cancelled"
    outcome = h.updater.rollback(V("7.3.0"), yes)
    assert (outcome.status, outcome.to_version) == ("rolled-back", "7.2.0")
    assert h.installer.installed[-1] == "vakr_7.2.0_amd64.deb"
    assert "channel" in config.read_text()
    safety = list(h.kit.layout.backups_dir.glob("pre-rollback-*.tar.gz"))
    assert len(safety) == 1
    assert load_state(h.kit.layout.state_file).result == "rolled-back"
    assert before_update.backup != str(safety[0])
    # With no success record, the newest older cached version is chosen.
    h.installer.fail_for.add("vakr_7.2.0_amd64.deb")
    failed = h.updater.rollback(V("7.3.0"), yes)
    assert failed.status == "failed"
    assert "reinstalling vakr_7.2.0_amd64.deb failed" in failed.message


def test_check_reports_available_and_lock_prevents_concurrency(harness: Harness) -> None:
    """``--check`` changes nothing; a held lock stops a second update (UPD-007).

    Args:
        harness: Updater with fakes.

    Returns:
        None

    Raises:
        None
    """
    h = harness
    h.http.publish("https://api.example", "o/r", [])
    assert h.updater.check(V("7.2.0"), Request()).message.startswith("no release on")
    h.publish("7.3.0", body="notes")
    report = h.updater.check(V("7.2.0"), Request())
    assert (report.status, report.notes) == ("available", "notes")
    assert h.installer.installed == []
    with exclusive_lock(h.kit.layout.lock_file), pytest.raises(LockBusyError):
        h.updater.update(V("7.2.0"), Request(), yes)
    explicit = h.updater.check(V("7.2.0"), Request(version=V("7.3.0"), if_newer=True))
    assert explicit.status == "available"
    gone = h.updater.check(V("7.3.0"), Request(version=V("7.3.0"), if_newer=True))
    assert gone.status == "up-to-date"
