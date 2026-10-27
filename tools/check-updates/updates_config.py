# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Load and validate ``check-updates.toml``, the only project-specific input.

check-updates is portable (blueprint section 29): every project-specific
value, from the files that hold pins to the GitHub repositories to watch,
lives in one TOML file. A typo there would otherwise make an item vanish
silently from the release checklist, so unknown sections and keys,
missing sources, and invalid patterns are rejected with the location of
the mistake before anything is fetched.

Dependencies:
    tomllib: Standard-library TOML parser.
    updates_sources: The list of supported upstream sources.

Safety:
    Reading the file never executes anything; ``command`` values are only
    checked to be argument lists here and later run without a shell.

Example:
    >>> _validate_entry({"name": "uv", "command": ["uv", "--version"], "pypi": "uv"},
    ...                 "tools[0]", needs_version=False)
"""

import re
import tomllib
from pathlib import Path
from typing import Any

from updates_sources import SOURCES

SECTIONS = frozenset(
    {
        *("settings", "python", "go", "node", "pypi", "lockfile", "npm", "actions"),
        *("tools", "assets", "exceptions"),
    }
)
_ENTRY_KEYS = frozenset({"name", "command", "file", "version", "regex", "per_page", *SOURCES})
# Keys of the checked tables; a misspelt key must not hide a row or a hold.
_TABLE_KEYS = {
    "go": frozenset({"releases_url", "binary", "go_mod", "modules", "pins"}),
    "node": frozenset({"releases_url", "version_file", "engines", "binary"}),
    "npm": frozenset({"package_json", "lockfile", "direct_dependencies", "extra", "lines"}),
    "exceptions": frozenset({"file"}),
}


class ConfigError(Exception):
    """The configuration file is missing, malformed, or inconsistent."""


def _validate_entry(entry: Any, where: str, needs_version: bool) -> None:
    """Check one ``[[tools]]`` or ``assets`` entry.

    Args:
        entry: Parsed TOML value.
        where: Location for error messages, such as ``tools[2]``.
        needs_version: True for assets, which must state their version.

    Returns:
        None

    Raises:
        ConfigError: If the entry is not usable.
    """
    if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
        raise ConfigError(f"{where}: each entry needs a string 'name'")
    where = f"{where} ({entry['name']})"
    if unknown := sorted(set(entry) - _ENTRY_KEYS):
        raise ConfigError(f"{where}: unknown key(s) {', '.join(unknown)}")
    if sum(key in entry for key in SOURCES) != 1:
        raise ConfigError(f"{where}: set exactly one of {', '.join(SOURCES)}")
    if sum(key in entry for key in ("command", "file", "version")) > 1:
        raise ConfigError(f"{where}: set at most one of command, file, version")
    if "file" in entry and "regex" not in entry:
        raise ConfigError(f"{where}: 'file' needs a 'regex' whose first group is the version")
    if needs_version and not isinstance(entry.get("version"), str):
        raise ConfigError(f"{where}: assets need a string 'version'")
    command = entry.get("command", [])
    if not isinstance(command, list) or not all(isinstance(arg, str) for arg in command):
        raise ConfigError(f"{where}: 'command' must be a list of strings")
    try:
        re.compile(entry.get("regex", ""))
    except re.error as exc:
        raise ConfigError(f"{where}: invalid regex: {exc}") from None


def _validate_table(table: Any, name: str, where: str) -> None:
    """Check the keys and value types of one checked table, such as ``[npm]``.

    Args:
        table: Parsed TOML value of the section.
        name: Section name, a key of ``_TABLE_KEYS``.
        where: Location for error messages (the file name).

    Returns:
        None

    Raises:
        ConfigError: If the section is not a table, names an unknown key,
            or has a value of the wrong type.
    """
    if not isinstance(table, dict):
        raise ConfigError(f"{where}: [{name}] must be a table")
    if unknown := sorted(set(table) - _TABLE_KEYS[name]):
        raise ConfigError(f"{where}: [{name}] unknown key(s) {', '.join(unknown)}")
    for key in ("binary", "extra", "go_mod"):
        value = table.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ConfigError(f"{where}: [{name}] '{key}' must be a list of strings")
    lines = table.get("lines", {})
    if not isinstance(lines, dict) or not all(isinstance(v, str) for v in lines.values()):
        raise ConfigError(f"{where}: [{name}] 'lines' must map package names to version files")
    pins = table.get("pins", [])
    if not isinstance(pins, list):
        raise ConfigError(f"{where}: [{name}] 'pins' must be a list of tables")
    for index, pin in enumerate(pins):
        _validate_pin(pin, f"{where}: [{name}] pins[{index}]")
    if name == "exceptions" and not isinstance(table.get("file"), str):
        raise ConfigError(f"{where}: [exceptions] needs a string 'file'")


def _validate_pin(pin: Any, where: str) -> None:
    """Check one ``pins`` entry: a file and a regex whose first group is the version.

    Args:
        pin: Parsed TOML value.
        where: Location for error messages.

    Returns:
        None

    Raises:
        ConfigError: If the entry is not a table of exactly ``file`` and
            ``regex`` strings, or the regex does not compile.
    """
    if not isinstance(pin, dict) or set(pin) != {"file", "regex"}:
        raise ConfigError(f"{where}: needs exactly 'file' and 'regex'")
    if not all(isinstance(value, str) for value in pin.values()):
        raise ConfigError(f"{where}: 'file' and 'regex' must be strings")
    try:
        re.compile(pin["regex"])
    except re.error as exc:
        raise ConfigError(f"{where}: invalid regex: {exc}") from None


def load_config(path: Path) -> dict[str, Any]:
    """Read and validate ``check-updates.toml``.

    Args:
        path: Configuration file.

    Returns:
        dict[str, Any]: The parsed configuration.

    Raises:
        ConfigError: If the file cannot be read or parsed, names an unknown
            section, or has an invalid tool, asset, ``[go]``, ``[node]``,
            ``[npm]``, or ``[exceptions]`` entry.
    """
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{path}: {exc}") from None
    if unknown := sorted(set(data) - SECTIONS):
        raise ConfigError(f"{path.name}: unknown section(s) {', '.join(unknown)}")
    for kind in ("tools", "assets"):
        entries = data.get(kind, [])
        if not isinstance(entries, list):
            raise ConfigError(f"{path.name}: '{kind}' must be a list of tables")
        for index, entry in enumerate(entries):
            _validate_entry(entry, f"{path.name}: {kind}[{index}]", kind == "assets")
    for name in _TABLE_KEYS:
        if name in data:
            _validate_table(data[name], name, path.name)
    return data
