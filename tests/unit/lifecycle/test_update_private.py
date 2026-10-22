"""Self-update's root-only storage: private directories, trusted archives, adoption (UPD-005).

Rollback restores an archive as root, so only archives root made, in a
directory only root can write, may be restored. These tests run as the
test user, which plays root: "the caller" is whoever runs the updater.

Dependencies:
    pytest; tests.unit.lifecycle.helpers (the updater harness).

Example:
    $ uv run pytest tests/unit/lifecycle/test_update_private.py
"""

import dataclasses
import io
import os
import sqlite3
import tarfile
import time
from pathlib import Path

import pytest

from tests.unit.lifecycle.helpers import Harness
from vakr.health.lifecycle_checks import check_update
from vakr.lifecycle.update import private
from vakr.lifecycle.update.lock import exclusive_lock
from vakr.lifecycle.update.model import Layout
from vakr.lifecycle.update.policy import Request
from vakr.lifecycle.update.state import UpdateState, load_state, save_state
from vakr.lifecycle.update.versions import Version

V = Version.parse


def yes(_question: str) -> bool:
    """Confirm every prompt.

    Args:
        _question: The prompt.

    Returns:
        bool: Always True.

    Raises:
        None
    """
    return True


def _archive(path: Path, config_text: str, mode: int = 0o600) -> None:
    """Write a pre-update archive holding one configuration file.

    Args:
        path: Archive path.
        config_text: Contents of ``config/config.toml``.
        mode: File mode to leave it at.

    Returns:
        None

    Raises:
        None
    """
    data = config_text.encode()
    with tarfile.open(path, "w:gz") as tar:
        info = tarfile.TarInfo("config/config.toml")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    path.chmod(mode)


def _layout(tmp_path: Path) -> Layout:
    """Build a layout with an old location beside the private one.

    Args:
        tmp_path: Test directory.

    Returns:
        Layout: Private directories under ``update``, the old ones under ``state``.

    Raises:
        None
    """
    return Layout(cache_root=tmp_path / "cache", backups_dir=tmp_path / "update/backups",
                  state_file=tmp_path / "update/update-state.json", lock_file=tmp_path / "lock",
                  config_dir=tmp_path / "etc", db_dir=tmp_path / "state",
                  legacy_backups_dir=tmp_path / "state/backups",
                  legacy_state_file=tmp_path / "state/update-state.json")  # fmt: skip


def test_upd005_private_dirs_are_made_for_the_caller_and_others_are_refused(tmp_path: Path) -> None:
    """UPD-005: a new directory gets the mode asked for; a shared or fake one is refused.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    made = tmp_path / "a/b"
    private.ensure_private_dir(made)
    assert made.stat().st_mode & 0o777 == 0o700
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o775)
    with pytest.raises(private.PrivateStorageError, match="writable by it alone"):
        private.check_private_dir(shared)
    fake = tmp_path / "fake"
    fake.symlink_to(made)
    with pytest.raises(private.PrivateStorageError, match="not a directory"):
        private.check_private_dir(fake)


def test_upd005_only_the_callers_own_single_link_archives_are_trusted(tmp_path: Path) -> None:
    """UPD-005: group-writable, linked, and hard-linked archives are never restored.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    good, loose = tmp_path / "pre-update-1.tar.gz", tmp_path / "pre-update-2.tar.gz"
    _archive(good, "good")
    _archive(loose, "loose", mode=0o664)
    linked = tmp_path / "pre-update-3.tar.gz"
    linked.symlink_to(good)
    twin = tmp_path / "pre-update-4.tar.gz"
    _archive(twin, "twin")
    os.link(twin, tmp_path / "elsewhere")
    assert private.trusted_archives([good, loose, linked, twin, tmp_path / "gone"]) == [good]


def test_upd005_an_older_releases_files_are_adopted_once_and_only_if_trusted(
    tmp_path: Path,
) -> None:
    """UPD-005: trusted archives and the state move with their times; the rest stay behind.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    layout = _layout(tmp_path)
    old = layout.legacy_backups_dir
    assert old is not None
    assert layout.legacy_state_file is not None
    old.mkdir(parents=True)
    kept = old / "pre-update-20261020T000000Z.tar.gz"
    _archive(kept, "kept")
    os.utime(kept, (1_700_000_000, 1_700_000_000))
    _archive(old / "pre-update-20261021T000000Z.tar.gz", "planted", mode=0o664)
    (old / "metadata-20261020T000000Z.db").write_text("vakr db backups stay")
    save_state(layout.legacy_state_file, UpdateState("success", "t", "7.2.0", "7.3.1", "ok"))
    moved = private.adopt_legacy(layout)
    assert moved == ["pre-update-20261020T000000Z.tar.gz", "update-state.json"]
    new = layout.backups_dir / kept.name
    assert new.stat().st_mtime == 1_700_000_000
    assert new.stat().st_mode & 0o777 == 0o600
    assert not kept.exists()
    assert (old / "pre-update-20261021T000000Z.tar.gz").exists()
    assert (old / "metadata-20261020T000000Z.db").exists()
    assert load_state(layout.state_file).to_version == "7.3.1"
    assert layout.backups_dir.stat().st_mode & 0o777 == 0o700
    assert layout.state_file.parent.stat().st_mode & 0o777 == 0o755
    assert private.adopt_legacy(layout) == []


def _seed(h: Harness) -> None:
    """Create operator configuration and a metadata database to back up.

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
    db.close()


def test_upd005_rollback_never_restores_an_archive_someone_else_could_have_changed(
    harness: Harness,
) -> None:
    """UPD-005: a newer archive that is not root's alone is skipped; root's own is restored.

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
    assert h.updater.update(V("7.2.0"), Request(), yes).status == "updated"
    planted = h.kit.layout.backups_dir / "pre-update-29991231T000000Z.tar.gz"
    _archive(planted, "planted = true\n", mode=0o664)
    future = time.time() + 86_400
    os.utime(planted, (future, future))
    assert h.updater.rollback(V("7.3.0"), yes).status == "rolled-back"
    assert "channel" in (h.kit.layout.config_dir / "config.toml").read_text()


def test_upd005_an_update_refuses_a_backups_directory_others_may_write(harness: Harness) -> None:
    """UPD-005: nothing is written or trusted in a shared backups directory.

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
    h.kit.layout.backups_dir.mkdir(parents=True)
    h.kit.layout.backups_dir.chmod(0o777)
    with pytest.raises(private.PrivateStorageError):
        h.updater.update(V("7.2.0"), Request(), yes)
    assert h.installer.installed == []


def test_upd005_prepare_adopts_through_the_updater_and_reports_it(harness: Harness) -> None:
    """UPD-005: the first rollback after the change finds the older release's archive.

    Args:
        harness: Updater with fakes.

    Returns:
        None

    Raises:
        None
    """
    h = harness
    layout = h.kit.layout
    legacy = dataclasses.replace(layout, legacy_backups_dir=h.root / "old/backups",
                                 legacy_state_file=h.root / "old/update-state.json")  # fmt: skip
    kit = dataclasses.replace(h.kit, layout=legacy)
    assert legacy.legacy_backups_dir is not None
    legacy.legacy_backups_dir.mkdir(parents=True)
    _archive(legacy.legacy_backups_dir / "pre-update-20261020T000000Z.tar.gz", "x")
    private.prepare(kit)
    assert (layout.backups_dir / "pre-update-20261020T000000Z.tar.gz").exists()
    assert [e for e, _ in h.events if e == "update.storage.adopted"]


def test_upd005_health_reads_the_new_record_and_shows_an_older_one_until_it_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UPD-005, OPS-001: the update check reads root's record, else the older release's.

    Args:
        tmp_path: Install root.
        monkeypatch: ``VAKR_ROOT``.

    Returns:
        None

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    old = tmp_path / "var/lib/vakr/update-state.json"
    save_state(old, UpdateState("failed", "t", "7.2.0", "7.3.0", "dpkg failed"))
    assert "dpkg failed" in check_update().summary
    new = tmp_path / "var/lib/vakr-update/update-state.json"
    save_state(new, UpdateState("success", "t", "7.2.0", "7.3.1", "ok"))
    assert check_update().summary.startswith("last update success 7.2.0 -> 7.3.1")


def test_upd005_the_update_lock_never_follows_a_link_or_lives_in_a_shared_directory(
    tmp_path: Path,
) -> None:
    """UPD-005, UPD-007: a link in the lock's place, or a shared directory, stops the update.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    target = tmp_path / "elsewhere"
    lock_dir = tmp_path / "update"
    private.ensure_private_dir(lock_dir, mode=0o755)
    (lock_dir / "self-update.lock").symlink_to(target)
    with (
        pytest.raises(OSError, match="symbolic links"),
        exclusive_lock(lock_dir / "self-update.lock"),
    ):
        pass
    assert not target.exists()
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o777)
    with pytest.raises(private.PrivateStorageError), exclusive_lock(shared / "self-update.lock"):
        pass
