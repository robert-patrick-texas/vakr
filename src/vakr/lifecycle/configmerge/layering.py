"""Merge catalog defaults with operator files and validate the result.

Precedence is catalog defaults < the main file < lexically ordered drop-ins
< explicit overrides (CONF-006). Operator files hold only what the operator
changed (CFGM-001), so an option a new release adds takes its documented
default with no edits, and every effective value records where it came from.

A file whose ``config_version`` is older than the catalog's is migrated in
memory before merging, so services keep running on the operator's intent
between a package upgrade and ``config upgrade`` rewriting the file.

Dependencies:
    tomllib: Strict TOML parsing of operator files.
    tomlkit: Only to migrate older files in memory.
    difflib: Closest-key suggestions for unknown keys (CFGM-006).

Safety:
    Read-only. Messages name files, lines, and keys, never values. A value is
    checked where it takes effect: one overridden by a later file is not.

Example:
    >>> from vakr.lifecycle.configmerge.values import ValueSpec
    >>> option = Option("update.channel", ValueSpec("str"), "stable", "1.0.0", "live", "")
    >>> result = load_config(Catalog(1, [option]), (), [])
    >>> result.values["update.channel"], str(result.sources["update.channel"][0])
    ('stable', 'default')
"""

import copy
import difflib
import tomllib
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import tomlkit
from tomlkit.exceptions import TOMLKitError

from .catalog import Catalog, Option
from .checks import problems, with_field_defaults
from .lines import key_lines, line_of
from .migrations import BASE_VERSION, Migration, MigrationError, migration_path, retired_keys
from .migrations import retirement as retirement_of
from .model import DEFAULT_ORIGIN, VERSION_KEY, Issue, Layer, LoadResult, Origin
from .operations import apply_migrations

OVERRIDE_ORIGIN = Origin("override")
"""Origin of values passed as ``overrides``."""


@dataclass(frozen=True, slots=True)
class _Setting:
    """One value set by one layer.

    Attributes:
        value: The value as parsed.
        origin: File and line of the key.
        lines: Key-to-line map of that file, to locate nested problems.
    """

    value: Any
    origin: Origin
    lines: Mapping[str, int]


def load_config(
    catalog: Catalog,
    migrations: Sequence[Migration],
    layers: Sequence[Layer],
    overrides: Mapping[str, Any] | None = None,
) -> LoadResult:
    """Merge defaults, layers, and overrides; validate every effective value.

    Args:
        catalog: Option catalog.
        migrations: Full migration chain, for in-memory upgrades and hints.
        layers: Operator files in precedence order.
        overrides: Highest-precedence values by dotted key, such as
            environment or one-shot CLI overrides (CONF-006).

    Returns:
        LoadResult: Effective values, sources, issues, and file versions.

    Raises:
        None
    """
    result = LoadResult()
    entries: list[tuple[str, _Setting]] = []
    for layer in layers:
        data, lines = _parse_layer(catalog, migrations, layer, result)
        for key, value in _flatten(catalog, data):
            origin = Origin(str(layer.path), line_of(lines, key))
            entries.append((key, _Setting(value, origin, lines)))
    entries += [(k, _Setting(v, OVERRIDE_ORIGIN, {})) for k, v in (overrides or {}).items()]
    retired = retired_keys(migrations)
    found: dict[str, list[_Setting]] = {}
    for key, setting in entries:
        if catalog.option(key) is not None:
            found.setdefault(key, []).append(setting)
        else:
            message = _unknown(catalog, retired, key, setting.value)
            result.issues.append(Issue(setting.origin, key, message))
    # Append-merged options read their defaults switch, so resolve them last.
    for option in sorted(catalog.options, key=lambda o: o.merge == "append"):
        _resolve(option, found.get(option.key, []), result)
    _check_required_when(catalog, result)
    result.values = {o.key: result.values[o.key] for o in catalog.options}
    return result


def _parse_layer(
    catalog: Catalog, migrations: Sequence[Migration], layer: Layer, result: LoadResult
) -> tuple[dict[str, Any], dict[str, int]]:
    """Parse one file, record its version, and migrate it in memory if older.

    Args:
        catalog: Option catalog.
        migrations: Full migration chain.
        layer: The file.
        result: Receives the file's version and any parse problems.

    Returns:
        tuple[dict[str, Any], dict[str, int]]: Settings without
        ``config_version``, and the key-to-line map of the original text.

    Raises:
        None
    """
    path = str(layer.path)
    try:
        text = layer.raw.decode("utf-8")
        data = tomllib.loads(text)
    except UnicodeDecodeError:
        result.issues.append(Issue(Origin(path), "", "file is not valid UTF-8"))
        return {}, {}
    except tomllib.TOMLDecodeError as exc:
        result.issues.append(Issue(Origin(path, exc.lineno), "", f"invalid TOML: {exc.msg}"))
        return {}, {}
    lines = key_lines(text)
    where = Origin(path, lines.get(VERSION_KEY))
    version = data.pop(VERSION_KEY, BASE_VERSION)
    if isinstance(version, bool) or not isinstance(version, int) or version < BASE_VERSION:
        result.issues.append(Issue(where, VERSION_KEY, "must be a positive integer"))
        version = catalog.config_version
    result.versions[path] = version
    if version == catalog.config_version:
        return data, lines
    try:
        doc = tomlkit.parse(text)
        apply_migrations(doc, migration_path(migrations, version, catalog.config_version))
    except (MigrationError, TOMLKitError) as exc:
        result.issues.append(Issue(where, VERSION_KEY, str(exc)))
        return {}, lines
    migrated = doc.unwrap()
    migrated.pop(VERSION_KEY, None)
    return migrated, lines


def _flatten(
    catalog: Catalog, data: Mapping[str, Any], prefix: str = ""
) -> Iterator[tuple[str, Any]]:
    """Walk parsed TOML, yielding option values and unknown entries.

    Args:
        catalog: Option catalog.
        data: Parsed tables.
        prefix: Dotted prefix of ``data``.

    Returns:
        Iterator[tuple[str, Any]]: ``(key, value)`` for each option value or
        unknown entry. An unknown table is reported once, not per key, and
        empty tables (kept by migrations to preserve comments) are skipped.

    Raises:
        None
    """
    for name, value in data.items():
        key = prefix + name
        if catalog.option(key) is not None:
            yield key, value
        elif isinstance(value, dict) and catalog.is_table(key):
            yield from _flatten(catalog, value, f"{key}.")
        elif value != {}:
            yield key, value


def _unknown(catalog: Catalog, retired: Mapping[str, str], key: str, value: Any) -> str:
    """Explain an unknown key: the migration that retired it, or the closest valid key.

    Args:
        catalog: Option catalog.
        retired: Result of ``retired_keys``.
        key: Unknown dotted key.
        value: Its value, only to distinguish tables from keys.

    Returns:
        str: Message such as ``"unknown key; did you mean update.channel?"``.

    Raises:
        None
    """
    noun = "unknown table" if isinstance(value, dict) else "unknown key"
    if hint := retirement_of(retired, key):
        return f"{noun}; {hint}"
    close = difflib.get_close_matches(key, catalog.names(), n=1, cutoff=0.6)
    return f"{noun}; did you mean {close[0]}?" if close else noun


def _resolve(option: Option, settings: list[_Setting], result: LoadResult) -> None:
    """Pick (or, for ``append`` options, combine) an option's effective value.

    Args:
        option: The option.
        settings: Values set by layers and overrides, lowest precedence first.
        result: Receives the value, its sources, and any problems.

    Returns:
        None

    Raises:
        None
    """
    if option.merge == "append":
        keep_defaults = result.values.get(option.defaults_switch, True) is not False
        merged = list(option.default) if keep_defaults else []
        origins = [DEFAULT_ORIGIN] if keep_defaults else []
        for setting in settings:
            if _report(option, setting, result):
                merged += setting.value
                origins.append(setting.origin)
        result.values[option.key] = with_field_defaults(option.spec, merged)
        result.sources[option.key] = tuple(origins)
        return
    default = _Setting(copy.deepcopy(option.default), DEFAULT_ORIGIN, {})
    winner = settings[-1] if settings else default
    _report(option, winner, result)
    result.values[option.key] = winner.value
    result.sources[option.key] = (winner.origin,)


def _report(option: Option, setting: _Setting, result: LoadResult) -> bool:
    """Record every problem with one value of an option, located to the line.

    Args:
        option: The option.
        setting: A value and where it was set.
        result: Receives the problems.

    Returns:
        bool: ``True`` when the value is valid.

    Raises:
        None
    """
    found = problems(option.spec, setting.value)
    for suffix, text in found:
        key = option.key + suffix
        line = line_of(setting.lines, key) if suffix else None
        origin = Origin(setting.origin.path, line or setting.origin.line)
        result.issues.append(Issue(origin, key, text))
    return not found


def _check_required_when(catalog: Catalog, result: LoadResult) -> None:
    """Enforce conditional requirements such as ``primary_url`` for peers.

    Args:
        catalog: Option catalog.
        result: Effective values; receives problems.

    Returns:
        None

    Raises:
        None
    """
    for option in catalog.options:
        if option.required_when is None:
            continue
        key, expected = option.required_when
        if result.values.get(key) == expected and result.values[option.key] in ("", []):
            message = f"must be set when {key} = {expected!r}"
            result.issues.append(Issue(result.sources[key][-1], option.key, message))
