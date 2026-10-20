"""New-option reporting and annotation tests: CFGM-T04 (CFGM-004).

Dependencies:
    pytest: Temporary directories.

Example:
    $ uv run pytest tests/unit/config/test_new_options.py
"""

import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest

from vakr.lifecycle.configmerge import (
    Catalog,
    ConfigLayout,
    Migration,
    UpgradeError,
    annotate,
    load_config,
    new_options,
    read_layers,
)


def test_new_options_lists_exactly_the_added_options(catalog4: Catalog) -> None:
    """CFGM-T04: only options introduced after the baseline release are listed, in order.

    Args:
        catalog4: Fixture catalog with options from 1.0.0 to 2.0.0.

    Returns:
        None

    Raises:
        None
    """
    assert [o.key for o in new_options(catalog4, "1.2.0")] == [
        "node.host",
        "node.port",
        "extra.flag",
    ]
    assert [o.key for o in new_options(catalog4, "v1.3.0")] == ["extra.flag"]
    assert [o.key for o in new_options(catalog4, "2.0.0rc1")] == ["extra.flag"]
    assert new_options(catalog4, "2.0.0") == ()
    with pytest.raises(ValueError, match="not a release version"):
        new_options(catalog4, "latest")


def test_annotate_adds_comments_where_operators_edit_and_is_idempotent(
    catalog4: Catalog,
    migrations4: tuple[Migration, ...],
    layout: ConfigLayout,
    write: Callable[[Path, str], Path],
    tmp_path: Path,
) -> None:
    """CFGM-T04: annotation lands where the table is, changes no values, and is idempotent.

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
    main = write(layout.main_path, 'config_version = 4\n\n[update]\nchannel = "stable"\n')
    site = write(
        layout.config_dir / "conf.d/50-site.toml",
        "config_version = 4\n\n[node]\nport = 9443  # firewall rule 12\n\n"
        "# polling notes\n[poll]\ntimeout_ms = 5\n",
    )
    data_before = [tomllib.loads(p.read_text()) for p in (main, site)]
    result, added = annotate(
        catalog4, migrations4, layout, new_options(catalog4, "1.2.0"), tmp_path / "bk"
    )
    assert added == ("node.host", "extra.flag")
    site_text = site.read_text()
    assert site_text.index("# New in 1.3.0: node.host") < site_text.index("# polling notes")
    assert '# host = ""' in site_text
    assert "port = 9443  # firewall rule 12" in site_text
    assert main.read_text().endswith(
        "# [extra]\n\n# New in 2.0.0: extra.flag\n"
        "# A feature added by the newest release.\n# flag = true\n"
    )
    assert [tomllib.loads(p.read_text()) for p in (main, site)] == data_before
    assert {c.path for c in result.changes} == {main, site}
    assert result.backup is not None
    assert (result.backup / "conf.d/50-site.toml").exists()
    snapshot = (main.read_bytes(), site.read_bytes())
    again, added_again = annotate(
        catalog4, migrations4, layout, new_options(catalog4, "1.2.0"), tmp_path / "bk"
    )
    assert (again.changes, added_again, again.backup) == ((), (), None)
    assert (main.read_bytes(), site.read_bytes()) == snapshot
    assert load_config(catalog4, migrations4, read_layers(layout)).issues == []


def test_annotate_creates_the_main_file_and_refuses_invalid_configuration(
    catalog4: Catalog,
    migrations4: tuple[Migration, ...],
    layout: ConfigLayout,
    write: Callable[[Path, str], Path],
    tmp_path: Path,
) -> None:
    """A missing main file is created with ``config_version``; an invalid config is left alone.

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
    result, added = annotate(
        catalog4, migrations4, layout, new_options(catalog4, "1.3.0"), tmp_path / "bk"
    )
    assert added == ("extra.flag",)
    assert layout.main_path.read_text().startswith("config_version = 4\n\n# [extra]\n")
    assert result.changes[0].before is None
    bad = write(layout.config_dir / "conf.d/10-bad.toml", "[update]\nchanel = 1\n")
    with pytest.raises(UpgradeError):
        annotate(catalog4, migrations4, layout, new_options(catalog4, "1.0.0"), tmp_path / "bk")
    assert bad.read_text() == "[update]\nchanel = 1\n"
