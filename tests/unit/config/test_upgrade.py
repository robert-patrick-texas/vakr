"""Config upgrade tests: CFGM-T01, CFGM-T02, CFGM-T03, CFGM-T05 (CFGM-001 to CFGM-003).

Dependencies:
    pytest: Temporary directories and monkeypatching.

Example:
    $ uv run pytest tests/unit/config/test_upgrade.py
"""

import tomllib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from vakr.lifecycle.configmerge import (
    Catalog,
    ConfigLayout,
    Migration,
    UpgradeError,
    fileio,
    load_config,
    plan_upgrade,
    read_layers,
    upgrade,
)

NOW = datetime(2026, 10, 20, 1, 2, 3, tzinfo=UTC)


def _snapshot(root: Path) -> dict[str, bytes]:
    """Capture every file under ``root`` byte for byte.

    Args:
        root: Directory to snapshot.

    Returns:
        dict[str, bytes]: Relative path to content.

    Raises:
        OSError: If a file cannot be read.
    """
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def test_new_release_options_apply_defaults_without_edits(
    catalog4: Catalog,
    migrations4: tuple[Migration, ...],
    layout: ConfigLayout,
    write: Callable[[Path, str], Path],
) -> None:
    """CFGM-T01: options added by a release take their defaults; no operator edit is needed.

    Args:
        catalog4: Fixture catalog whose newest release adds ``extra.flag``.
        migrations4: Fixture migration chain.
        layout: Operator-file layout.
        write: File-writing helper.

    Returns:
        None

    Raises:
        None
    """
    main = write(layout.main_path, 'config_version = 4\n\n[update]\nchannel = "prerelease"\n')
    before = main.read_bytes()
    result = load_config(catalog4, migrations4, read_layers(layout))
    assert result.issues == []
    assert result.values["extra.flag"] is True
    assert [str(o) for o in result.sources["extra.flag"]] == ["default"]
    assert result.values["update.channel"] == "prerelease"
    assert plan_upgrade(catalog4, migrations4, read_layers(layout)) == ()
    assert upgrade(catalog4, migrations4, layout, layout.config_dir / "bk").changes == ()
    assert main.read_bytes() == before


def test_rename_and_move_keep_values_comments_backup_and_diff(
    catalog4: Catalog,
    migrations4: tuple[Migration, ...],
    layout: ConfigLayout,
    write: Callable[[Path, str], Path],
    v1_config: str,
    tmp_path: Path,
) -> None:
    """CFGM-T02: rename and move keep values and comments, and produce a backup and diff.

    Args:
        catalog4: Fixture catalog.
        migrations4: Fixture migration chain.
        layout: Operator-file layout.
        write: File-writing helper.
        v1_config: Operator file at config_version 1.
        tmp_path: Temporary directory for backups.

    Returns:
        None

    Raises:
        None
    """
    main = write(layout.main_path, v1_config)
    result = upgrade(catalog4, migrations4, layout, tmp_path / "backups", now=NOW)
    text = main.read_text()
    for kept in (
        "# Operator notes: production site",
        "config_version = 4  # maintained by upgrade",
        "# Follow pre-releases during the pilot",
        'channel = "prerelease"  # pilot channel',
        'hold_version = "1.0.3"  # pinned for change freeze',
        "timeout_ms = 2000  # seconds; CPU-limited routers",
    ):
        assert kept in text
    assert "[upgrade]" not in text
    assert "[legacy]" not in text
    assert text.index("channel =") < text.index("hold_version =") < text.index("[poll]")
    assert result.backup == tmp_path / "backups/20261020T010203Z"
    assert (result.backup / "config.toml").read_text() == v1_config
    assert '-track = "beta"  # pilot channel' in result.diff
    assert '+channel = "prerelease"  # pilot channel' in result.diff
    assert load_config(catalog4, migrations4, read_layers(layout)).issues == []


def test_skipping_releases_applies_every_migration_in_order(
    catalog4: Catalog,
    migrations4: tuple[Migration, ...],
    layout: ConfigLayout,
    write: Callable[[Path, str], Path],
    v1_config: str,
    tmp_path: Path,
) -> None:
    """CFGM-T05: a v1 file runs 1->2->3->4 in order; a v2 drop-in runs only 2->3->4.

    ``poll.wait`` only reaches ``poll.timeout_ms`` (scaled to milliseconds)
    if 1->2 runs before 2->3, so the result proves the order.

    Args:
        catalog4: Fixture catalog.
        migrations4: Fixture migration chain.
        layout: Operator-file layout.
        write: File-writing helper.
        v1_config: Operator file at config_version 1.
        tmp_path: Temporary directory for backups.

    Returns:
        None

    Raises:
        None
    """
    write(layout.main_path, v1_config)
    dropin = write(
        layout.config_dir / "conf.d/20-site.toml", "config_version = 2\n[poll]\ntimeout = 3\n"
    )
    upgrade(catalog4, migrations4, layout, tmp_path / "backups")
    main = tomllib.loads(layout.main_path.read_text())
    assert main == {
        "config_version": 4,
        "update": {"channel": "prerelease", "hold_version": "1.0.3"},
        "poll": {"timeout_ms": 2000, "interval": "60s"},
        "node": {"host": "collector.example.net", "port": 8443},
        "net": {"no_proxy": ["10.0.0.0/8", ".corp.example"]},
    }
    assert tomllib.loads(dropin.read_text()) == {"config_version": 4, "poll": {"timeout_ms": 3000}}
    effective = load_config(catalog4, migrations4, read_layers(layout))
    assert effective.values["poll.timeout_ms"] == 3000
    assert set(effective.versions.values()) == {4}


def test_dry_run_shows_diff_and_writes_nothing(
    catalog4: Catalog,
    migrations4: tuple[Migration, ...],
    layout: ConfigLayout,
    write: Callable[[Path, str], Path],
    v1_config: str,
    tmp_path: Path,
) -> None:
    """CFGM-003: ``--dry-run`` returns the diff without touching any file.

    Args:
        catalog4: Fixture catalog.
        migrations4: Fixture migration chain.
        layout: Operator-file layout.
        write: File-writing helper.
        v1_config: Operator file at config_version 1.
        tmp_path: Temporary directory for backups.

    Returns:
        None

    Raises:
        None
    """
    write(layout.main_path, v1_config)
    before = _snapshot(tmp_path)
    result = upgrade(catalog4, migrations4, layout, tmp_path / "backups", dry_run=True)
    assert result.dry_run
    assert result.backup is None
    assert "+config_version = 4" in result.diff
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ('[node]\naddress = "no-port-here"\n', "config migration 3->4 failed"),
        ('[update]\ncolour = "red"\n', "update.colour: unknown key"),
        ("config_version = 9\n", "newer than this release supports"),
        ("this is = not toml [\n", "invalid TOML"),
    ],
)
def test_failed_migration_or_validation_leaves_files_byte_identical(
    catalog4: Catalog,
    migrations4: tuple[Migration, ...],
    layout: ConfigLayout,
    write: Callable[[Path, str], Path],
    v1_config: str,
    tmp_path: Path,
    extra: str,
    expected: str,
) -> None:
    """CFGM-T03: migration or validation failure aborts with every file unchanged.

    Args:
        catalog4: Fixture catalog.
        migrations4: Fixture migration chain.
        layout: Operator-file layout.
        write: File-writing helper.
        v1_config: Operator file at config_version 1.
        tmp_path: Temporary directory for backups.
        extra: A drop-in that makes the upgrade fail.
        expected: Text the failure must report.

    Returns:
        None

    Raises:
        None
    """
    write(layout.main_path, v1_config)
    header = "" if "config_version" in extra else "config_version = 1\n"
    write(layout.config_dir / "conf.d/50-bad.toml", header + extra)
    before = _snapshot(tmp_path)
    with pytest.raises(UpgradeError) as caught:
        upgrade(catalog4, migrations4, layout, tmp_path / "backups")
    assert any(expected in str(issue) for issue in caught.value.issues)
    assert _snapshot(tmp_path) == before
    assert not (tmp_path / "backups").exists()


def test_write_failure_rolls_back_files_already_replaced(
    catalog4: Catalog,
    migrations4: tuple[Migration, ...],
    layout: ConfigLayout,
    write: Callable[[Path, str], Path],
    v1_config: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CFGM-T03: a disk error on the second file restores the first to its original bytes.

    Args:
        catalog4: Fixture catalog.
        migrations4: Fixture migration chain.
        layout: Operator-file layout.
        write: File-writing helper.
        v1_config: Operator file at config_version 1.
        tmp_path: Temporary directory for backups.
        monkeypatch: Replaces the atomic writer with a failing one.

    Returns:
        None

    Raises:
        None
    """
    main = write(layout.main_path, v1_config)
    dropin = write(layout.config_dir / "conf.d/20-site.toml", "config_version = 2\n")
    originals = (main.read_bytes(), dropin.read_bytes())
    real_write = fileio.atomic_write

    def failing_write(path: Path, data: bytes, like: Path | None = None) -> None:
        """Fail when the drop-in receives its upgraded content.

        Args:
            path: Destination.
            data: Content.
            like: Mode template.

        Returns:
            None

        Raises:
            OSError: For the drop-in's new content.
        """
        if path == dropin and data != originals[1]:
            raise OSError("disk full")
        real_write(path, data, like)

    monkeypatch.setattr(fileio, "atomic_write", failing_write)
    with pytest.raises(OSError, match="disk full"):
        upgrade(catalog4, migrations4, layout, tmp_path / "backups")
    assert (main.read_bytes(), dropin.read_bytes()) == originals


def test_file_without_config_version_is_treated_as_version_one(
    catalog4: Catalog,
    migrations4: tuple[Migration, ...],
    layout: ConfigLayout,
    write: Callable[[Path, str], Path],
    tmp_path: Path,
) -> None:
    """A drop-in that never stated a version is migrated from 1 and then stamped.

    Args:
        catalog4: Fixture catalog.
        migrations4: Fixture migration chain.
        layout: Operator-file layout.
        write: File-writing helper.
        tmp_path: Temporary directory for backups.

    Returns:
        None

    Raises:
        None
    """
    dropin = write(layout.config_dir / "conf.d/10-a.toml", '# mine\n[update]\ntrack = "stable"\n')
    result = load_config(catalog4, migrations4, read_layers(layout))
    assert result.values["update.channel"] == "stable"
    assert result.versions == {str(dropin): 1}
    upgrade(catalog4, migrations4, layout, tmp_path / "backups")
    assert dropin.read_text().startswith("# mine\nconfig_version = 4\n")
    assert fileio.stamp(NOW) == "20261020T010203Z"
