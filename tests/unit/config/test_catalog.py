"""Option catalog and migration-file validation tests (CFGM-001, CFGM-002, ADR-027).

Catalog and migration mistakes are packaging bugs; these tests prove they
fail loudly at build time rather than on a production host.

Dependencies:
    pytest: Parametrised error cases.

Example:
    $ uv run pytest tests/unit/config/test_catalog.py
"""

from pathlib import Path

import pytest

from vakr.config import project
from vakr.lifecycle.configmerge import (
    CatalogError,
    MigrationError,
    load_catalog,
    load_migrations,
    parse_catalog,
    parse_migrations,
    version_key,
)
from vakr.lifecycle.configmerge.migrations import migration_path, retired_keys, retirement

OPTION = (
    '[[options]]\nkey = "a.b"\ntype = "str"\ndefault = ""\n'
    'since = "1.0.0"\nreload = "live"\ndescription = "d"\n'
)
NESTED = '[options.fields.f]\ntype = "list[table]"\n[options.fields.f.fields.g]\ntype = "str"\n'
RENAME_ONLY_FROM = "operations = [{ op = 'rename', from = 'a' }]\n"


def _catalog(*options: str, version: str = "1") -> str:
    """Build catalog TOML from option snippets.

    Args:
        *options: ``[[options]]`` snippets.
        version: ``config_version`` literal.

    Returns:
        str: Catalog text.

    Raises:
        None
    """
    return f"[catalog]\nconfig_version = {version}\n" + "\n".join(options)


def test_packaged_catalog_matches_appendix_e15_and_the_task_list() -> None:
    """The packaged catalog defines the initial options with the specified defaults.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    catalog = project.catalog()
    defaults = catalog.defaults()
    assert catalog.config_version == 1
    assert project.migrations() == ()
    assert defaults["profile.name"] == "compact"
    profile = catalog.option("profile.name")
    assert profile is not None
    assert profile.spec.choices == (
        "lite",
        "compact",
        "hybrid-small",
        "standard",
        "enterprise",
    )
    assert defaults["update.repository"] == "robert-patrick-texas/vakr"
    assert defaults["update.channel"] == "prerelease"
    assert (
        defaults["telemetry.native_retention_days"],
        defaults["telemetry.rollup_retention_days"],
    ) == (30, 400)
    assert defaults["telemetry.rollup_interval"] == "15m"
    assert [
        (k["keyword"], k["tag"], k["interest"], k["wan_circuit"])
        for k in defaults["interface_classification.keywords"]
    ] == [
        ("ISP", "isp", "primary", True),
        ("WAN", "wan", "primary", True),
        ("UPLINK", "uplink", "primary", False),
    ]
    assert {o.reload for o in catalog.options} == {"live", "restart"}


def test_load_from_files(tmp_path: Path) -> None:
    """Catalogs and migrations load from paths as well as text.

    Args:
        tmp_path: Temporary directory.

    Returns:
        None

    Raises:
        None
    """
    (tmp_path / "options.toml").write_text(_catalog(OPTION))
    (tmp_path / "migrations.toml").write_text("")
    assert load_catalog(tmp_path / "options.toml").option("a.b") is not None
    assert load_migrations(tmp_path / "migrations.toml", 1) == ()


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[catalog\n", "Expected"),
        (_catalog(version='"1"'), "config_version must be a positive integer"),
        (_catalog(OPTION.replace('description = "d"\n', "")), "missing description"),
        (_catalog(OPTION.replace('"str"', '"strng"')), "unknown type"),
        (_catalog(OPTION.replace('"str"', '"enum"')), "choices are required"),
        (_catalog(OPTION + 'format = "colour"\n'), "unknown format"),
        (
            _catalog(OPTION.replace('"str"', '"list[table]"').replace('""', "[]")),
            "fields are required",
        ),
        (_catalog(OPTION.replace('"1.0.0"', '"soon"')), "not a release version"),
        (_catalog(OPTION.replace('"live"', '"reboot"')), "reload must be"),
        (_catalog(OPTION + 'merge = "append"\n'), "needs type list[table]"),
        (_catalog(OPTION.replace('default = ""', "default = 3")), "default does not validate"),
        (_catalog(OPTION, OPTION), "duplicate option keys"),
        (_catalog(OPTION, OPTION.replace('"a.b"', '"a.b.c"')), "both an option and a table"),
        (_catalog(OPTION + 'defaults_switch = "a.x"\n'), "defaults_switch must name a bool"),
        (_catalog(OPTION + 'required_when = ["a.x", 1]\n'), "required_when names an unknown key"),
        (
            _catalog(OPTION.replace('"str"', '"list[table]"').replace('""', "[]") + NESTED),
            "nested arrays of tables",
        ),
        (
            _catalog(
                OPTION.replace('"str"', '"list[table]"').replace('""', "[]")
                + '[options.fields.f]\ntype = "int"\ndefault = "x"\n'
            ),
            "field f: default does not validate",
        ),
    ],
)
def test_malformed_catalogs_are_rejected(text: str, message: str) -> None:
    """Every catalog consistency rule raises ``CatalogError`` with a precise message.

    Args:
        text: Catalog TOML.
        message: Expected message fragment.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(CatalogError, match=message.replace("[", r"\[").replace("]", r"\]")):
        parse_catalog(text)


@pytest.mark.parametrize(
    ("text", "current", "message"),
    [
        ("[[migrations\n", 1, "Expected"),
        ("[[migrations]]\nfrom = 1\nto = 2\ndescription = 'd'\n", 3, "end at 2, catalog is at 3"),
        ("[[migrations]]\nfrom = 1\nto = 1\ndescription = 'd'\n", 1, "breaks the chain"),
        (
            "[[migrations]]\nfrom = 1\nto = 2\ndescription = 'd'\n"
            "[[migrations]]\nfrom = 3\nto = 4\ndescription = 'd'\n",
            4,
            "migration 3->4 breaks the chain",
        ),
        ("[[migrations]]\nfrom = 'one'\nto = 2\ndescription = 'd'\n", 2, "integer from and to"),
        ("[[migrations]]\nfrom = 1\nto = 2\n", 2, "description is required"),
        (
            "[[migrations]]\nfrom = 1\nto = 2\ndescription = 'd'\noperations = [{ op = 'zap' }]\n",
            2,
            "unknown operation 'zap'",
        ),
        (
            "[[migrations]]\nfrom = 1\nto = 2\ndescription = 'd'\n" + RENAME_ONLY_FROM,
            2,
            "rename needs to",
        ),
        (
            "[[migrations]]\nfrom = 1\nto = 2\ndescription = 'd'\n"
            "operations = [{ op = 'convert', key = 'a', to = 'colour' }]\n",
            2,
            "convert to must be one of",
        ),
    ],
)
def test_malformed_migrations_are_rejected(text: str, current: int, message: str) -> None:
    """Migration files must form one contiguous chain of known operations.

    Args:
        text: Migrations TOML.
        current: Catalog ``config_version``.
        message: Expected message fragment.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(MigrationError, match=message):
        parse_migrations(text, current)


def test_migration_paths_and_retired_keys(migrations4: tuple) -> None:
    """Paths start at the file's version; retired keys explain renames, splits, and removals.

    Args:
        migrations4: Fixture migration chain.

    Returns:
        None

    Raises:
        None
    """
    assert [m.label for m in migration_path(migrations4, 2, 4)] == ["2->3", "3->4"]
    assert migration_path(migrations4, 4, 4) == ()
    with pytest.raises(MigrationError, match="newer than this release supports"):
        migration_path(migrations4, 5, 4)
    with pytest.raises(MigrationError, match="no config migration starts at config_version 0"):
        migration_path(migrations4, 0, 4)
    retired = retired_keys(migrations4)
    assert retired["node.address"].startswith(
        "split into node.host, node.port by config migration 3->4"
    )
    assert str(retirement(retired, "legacy.option")).startswith("legacy was removed by")
    assert retirement(retired, "poll.interval") is None


def test_version_keys_order_releases() -> None:
    """Release candidates sort before finals; SemVer and PEP 440 spellings agree.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert version_key("1.2.0-rc.1") == version_key("1.2.0rc1") < version_key("1.2.0")
    assert version_key("v1.2.0") == version_key("1.2.0+local.1") < version_key("1.10.0")
    with pytest.raises(ValueError, match="not a release version"):
        version_key(None)
