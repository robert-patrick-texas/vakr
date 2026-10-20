"""Layering, source tracking, and validation tests (CONF-006, CONF-009, CFGM-001, CFGM-006).

Dependencies:
    pytest: Temporary directories and parametrisation.

Example:
    $ uv run pytest tests/unit/config/test_layering.py
"""

import secrets
from collections.abc import Callable
from pathlib import Path

import pytest

from vakr.config import project
from vakr.lifecycle.configmerge import (
    Catalog,
    ConfigLayout,
    Layer,
    LoadResult,
    Migration,
    load_config,
    read_layers,
)


def _load(layout: ConfigLayout, overrides: dict | None = None) -> LoadResult:
    """Load ``layout`` with vakr's packaged catalog.

    Args:
        layout: Operator-file layout.
        overrides: Optional highest-precedence values.

    Returns:
        LoadResult: The merged result.

    Raises:
        OSError: If a file cannot be read.
    """
    return load_config(project.catalog(), project.migrations(), read_layers(layout), overrides)


def _messages(result: LoadResult) -> list[str]:
    """Render every issue of a result.

    Args:
        result: A load result.

    Returns:
        list[str]: ``file:line: key: message`` strings.

    Raises:
        None
    """
    return [str(issue) for issue in result.issues]


def test_precedence_defaults_main_dropins_overrides_with_sources(
    layout: ConfigLayout, write: Callable[[Path, str], Path]
) -> None:
    """CONF-006: defaults < config.toml < lexical conf.d < overrides; sources name file:line.

    Args:
        layout: Operator-file layout.
        write: File-writing helper.

    Returns:
        None

    Raises:
        None
    """
    main = write(layout.main_path, 'config_version = 1\n\n[update]\nchannel = "stable"\n')
    write(layout.config_dir / "conf.d/20-z.toml", "[telemetry]\nnative_retention_days = 45\n")
    late = write(layout.config_dir / "conf.d/30-late.toml", '[update]\nchannel = "prerelease"\n')
    write(layout.config_dir / "conf.d/10-a.toml", "[telemetry]\nnative_retention_days = 7\n")
    write(layout.config_dir / "conf.d/ignored.txt", "not toml at all [")
    result = _load(layout)
    assert result.issues == []
    assert result.values["update.channel"] == "prerelease"
    assert [str(o) for o in result.sources["update.channel"]] == [f"{late}:2"]
    assert result.values["telemetry.native_retention_days"] == 45
    assert [str(o) for o in result.sources["profile.name"]] == ["default"]
    assert result.versions[str(main)] == 1
    forced = _load(layout, {"update.channel": "stable"})
    assert forced.values["update.channel"] == "stable"
    assert [str(o) for o in forced.sources["update.channel"]] == ["override"]
    assert "profile.name" not in forced.operator_keys()
    assert result.nested()["telemetry"]["rollup_interval"] == "15m"


def test_unknown_keys_suggest_the_closest_valid_key_with_file_and_line(
    layout: ConfigLayout, write: Callable[[Path, str], Path]
) -> None:
    """CFGM-006: unknown keys name file, line, and key, and suggest the closest key.

    Args:
        layout: Operator-file layout.
        write: File-writing helper.

    Returns:
        None

    Raises:
        None
    """
    path = write(
        layout.main_path,
        '[update]\nchanel = "stable"\n\n[updates]\nchannel = "stable"\n\n[zzz]\nq = 1\n',
    )
    result = _load(layout, {"bogus.key": 1})
    assert _messages(result) == [
        f"{path}:2: update.chanel: unknown key; did you mean update.channel?",
        f"{path}:4: updates: unknown table; did you mean update?",
        f"{path}:7: zzz: unknown table",
        "override: bogus.key: unknown key",
    ]


def test_unknown_key_names_the_migration_that_removed_or_renamed_it(
    catalog4: Catalog,
    migrations4: tuple[Migration, ...],
    layout: ConfigLayout,
    write: Callable[[Path, str], Path],
) -> None:
    """CFGM-006: a stale key in a current file points at the migration that retired it.

    Args:
        catalog4: Fixture catalog.
        migrations4: Fixture migration chain.
        layout: Operator-file layout.
        write: File-writing helper.

    Returns:
        None

    Raises:
        None
    """
    stale = '[poll]\nwait = 1\n[legacy]\noption = true\n[upgrade]\nhold_version = ""\n'
    write(layout.main_path, "config_version = 4\n" + stale)
    messages = _messages(load_config(catalog4, migrations4, read_layers(layout)))
    assert (
        "poll.wait: unknown key; poll.wait was renamed to poll.timeout by config migration 1->2"
        in messages[0]
    )
    assert "legacy: unknown table; legacy was removed by config migration 3->4" in messages[1]
    assert "upgrade: unknown table; upgrade was renamed to update" in messages[2]


@pytest.mark.parametrize(
    ("text", "key", "message"),
    [
        ('[profile]\nname = "huge"', "profile.name", "expected one of: lite, compact"),
        ('[telemetry]\nnative_retention_days = "30"', "telemetry.native_retention_days", "integer"),
        ("[telemetry]\nnative_retention_days = true", "telemetry.native_retention_days", "integer"),
        ("[telemetry]\nnative_retention_days = 0", "telemetry.native_retention_days", "at least 1"),
        ('[telemetry]\nrollup_interval = "15 minutes"', "telemetry.rollup_interval", "duration"),
        ('[telemetry]\nrollup_interval = "10s"', "telemetry.rollup_interval", "at least 60"),
        ("[network]\nno_proxy = [1, 2]", "network.no_proxy", "list of strings"),
        ('[network]\nhttps_proxy = "ftp://proxy"', "network.https_proxy", "http:// or https://"),
        ('[network]\nhttps_proxy = "http://[bad"', "network.https_proxy", "http:// or https://"),
        ('[time]\ntimezone = "Mars/Olympus"', "time.timezone", "IANA timezone"),
        ('[update]\nrepository = "not a repo"', "update.repository", "must match"),
        ('[update]\nhold_version = "latest"', "update.hold_version", "must match"),
        (
            "[interface_classification]\nuse_default_keywords = 1",
            "interface_classification",
            "true or false",
        ),
        (
            "[interface_classification]\nkeywords = 3",
            "interface_classification.keywords",
            "array of tables",
        ),
        ("[node]\nposition = 5", "node.position", "expected a string"),
    ],
)
def test_type_enum_range_format_errors_are_reported(
    layout: ConfigLayout, write: Callable[[Path, str], Path], text: str, key: str, message: str
) -> None:
    """CONF-009: invalid types, enums, ranges, durations, URLs, and timezones fail validation.

    Args:
        layout: Operator-file layout.
        write: File-writing helper.
        text: Operator file content.
        key: Expected key prefix of the issue.
        message: Expected message fragment.

    Returns:
        None

    Raises:
        None
    """
    write(layout.main_path, text + "\n")
    (issue,) = _load(layout).issues
    assert issue.key.startswith(key)
    assert message in issue.message
    assert issue.origin.line == 2


def test_proxy_credentials_are_rejected_without_echoing_them(
    layout: ConfigLayout, write: Callable[[Path, str], Path]
) -> None:
    """EGRESS-001: credentials in a proxy URL are invalid, and messages never repeat values.

    Args:
        layout: Operator-file layout.
        write: File-writing helper.

    Returns:
        None

    Raises:
        None
    """
    throwaway = secrets.token_hex(8)  # generated per run; never a real credential
    write(layout.main_path, f'[network]\nhttps_proxy = "http://ops:{throwaway}@proxy:3128"\n')
    messages = _messages(_load(layout))
    assert len(messages) == 1
    assert "must not contain credentials" in messages[0]
    assert throwaway not in messages[0]


def test_keyword_entries_extend_defaults_or_replace_them(
    layout: ConfigLayout, write: Callable[[Path, str], Path]
) -> None:
    """Appendix E.15: operator keywords extend the packaged list unless defaults are switched off.

    Args:
        layout: Operator-file layout.
        write: File-writing helper.

    Returns:
        None

    Raises:
        None
    """
    entry = '[[interface_classification.keywords]]\nkeyword = "TRANSIT"\ntag = "transit"\n'
    write(layout.main_path, entry)
    write(layout.config_dir / "conf.d/50-x.toml", entry.replace("TRANSIT", "re:^PEER"))
    keywords = _load(layout).values["interface_classification.keywords"]
    assert [k["keyword"] for k in keywords] == ["ISP", "WAN", "UPLINK", "TRANSIT", "re:^PEER"]
    assert keywords[3] == {
        "keyword": "TRANSIT",
        "tag": "transit",
        "wan_circuit": False,
        "sticky": False,
    }
    write(
        layout.config_dir / "conf.d/60-only.toml",
        "[interface_classification]\nuse_default_keywords = false\n",
    )
    result = _load(layout)
    assert [k["keyword"] for k in result.values["interface_classification.keywords"]] == [
        "TRANSIT",
        "re:^PEER",
    ]
    assert "default" not in [str(o) for o in result.sources["interface_classification.keywords"]]


def test_keyword_entry_problems_are_located_per_field(
    layout: ConfigLayout, write: Callable[[Path, str], Path]
) -> None:
    """Keyword tables report unknown fields, missing fields, and bad patterns by index and line.

    Args:
        layout: Operator-file layout.
        write: File-writing helper.

    Returns:
        None

    Raises:
        None
    """
    path = write(
        layout.main_path,
        "[[interface_classification.keywords]]\n"
        'keyword = "re:(unclosed"\ntag = "x"\ncolour = "red"\n\n'
        "[[interface_classification.keywords]]\n"
        'interest = "maybe"\n',
    )
    key = "interface_classification.keywords"
    assert _messages(_load(layout)) == [
        f"{path}:4: {key}[0].colour: unknown field; "
        "expected one of: keyword, tag, interest, wan_circuit, sticky",
        f"{path}:2: {key}[0].keyword: invalid regular expression after re:",
        f"{path}:6: {key}[1].keyword: required field is missing",
        f"{path}:6: {key}[1].tag: required field is missing",
        f"{path}:7: {key}[1].interest: expected one of: primary, secondary",
    ]


def test_peer_requires_primary_url(
    layout: ConfigLayout, write: Callable[[Path, str], Path]
) -> None:
    """A peer node without ``node.primary_url`` is invalid (section 27.9).

    Args:
        layout: Operator-file layout.
        write: File-writing helper.

    Returns:
        None

    Raises:
        None
    """
    path = write(layout.main_path, '[node]\nrole = "peer"\n')
    assert _messages(_load(layout)) == [
        f"{path}:2: node.primary_url: must be set when node.role = 'peer'"
    ]
    write(
        layout.main_path, '[node]\nrole = "peer"\nprimary_url = "https://vakr-east.example.net"\n'
    )
    assert _load(layout).issues == []


def test_overridden_invalid_values_are_not_reported(
    layout: ConfigLayout, write: Callable[[Path, str], Path]
) -> None:
    """Values are checked where they take effect; an overridden value is not.

    Args:
        layout: Operator-file layout.
        write: File-writing helper.

    Returns:
        None

    Raises:
        None
    """
    write(layout.config_dir / "conf.d/10-a.toml", '[update]\nchannel = "ignored"\n')
    write(layout.config_dir / "conf.d/20-z.toml", '[update]\nchannel = "stable"\n')
    assert _load(layout).issues == []


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b"[update\nchannel = 1\n", "invalid TOML"),
        (b"\xff\xfe[update]\n", "not valid UTF-8"),
        (b'config_version = "one"\n', "config_version: must be a positive integer"),
        (b"config_version = 7\n", "newer than this release supports (1)"),
    ],
)
def test_unreadable_or_wrongly_versioned_files_are_reported(
    tmp_path: Path, raw: bytes, message: str
) -> None:
    """Syntax, encoding, and ``config_version`` problems become issues, not crashes.

    Args:
        tmp_path: Temporary directory.
        raw: File bytes.
        message: Expected message fragment.

    Returns:
        None

    Raises:
        None
    """
    layer = Layer(tmp_path / "config.toml", raw)
    result = load_config(project.catalog(), project.migrations(), [layer])
    assert any(message in str(issue) for issue in result.issues)


def test_empty_unknown_tables_are_ignored(
    layout: ConfigLayout, write: Callable[[Path, str], Path]
) -> None:
    """Empty tables (left by migrations to keep operator comments) are harmless.

    Args:
        layout: Operator-file layout.
        write: File-writing helper.

    Returns:
        None

    Raises:
        None
    """
    write(layout.main_path, "[upgrade]\n# kept note\n")
    assert _load(layout).issues == []
