"""Parse and self-check option catalog files.

The catalog ships inside the package, so a malformed entry is a packaging
bug. Parsing therefore checks everything it can (types, choices, formats,
that every default passes its own validation, duplicate keys, and dangling
references) so mistakes fail in CI rather than on a production host.

Dependencies:
    tomllib: Parses the catalog file.

Safety:
    Read-only; catalogs are trusted package data but are still validated.

Example:
    >>> text = '''
    ... [catalog]
    ... config_version = 1
    ... [[options]]
    ... key = "update.channel"
    ... type = "enum"
    ... choices = ["stable", "prerelease"]
    ... default = "stable"
    ... since = "1.0.0"
    ... reload = "live"
    ... description = "Release channel."
    ... '''
    >>> parse_catalog(text).option("update.channel").default
    'stable'
"""

import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .catalog import (
    MERGE_MODES,
    RELOAD_CLASSES,
    Catalog,
    CatalogError,
    Option,
    table_prefixes,
    version_key,
)
from .checks import problems
from .values import TEXT_FORMATS, VALUE_TYPES, FieldSpec, ValueSpec

_REQUIRED = ("key", "type", "default", "since", "reload", "description")


def load_catalog(path: Path) -> Catalog:
    """Read and validate a catalog file.

    Args:
        path: Catalog TOML file.

    Returns:
        Catalog: The parsed catalog.

    Raises:
        CatalogError: If the catalog is malformed.
        OSError: If the file cannot be read.
    """
    return parse_catalog(path.read_text(encoding="utf-8"), str(path))


def parse_catalog(text: str, origin: str = "catalog") -> Catalog:
    """Parse and validate catalog TOML text.

    Args:
        text: Catalog TOML.
        origin: Name used in error messages.

    Returns:
        Catalog: The parsed catalog.

    Raises:
        CatalogError: If the TOML or any option definition is invalid.
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise CatalogError(f"{origin}: {exc}") from exc
    version = data.get("catalog", {}).get("config_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise CatalogError(f"{origin}: [catalog] config_version must be a positive integer")
    options = [_parse_option(entry, origin) for entry in data.get("options", [])]
    _check_consistency(options, origin)
    return Catalog(version, options)


def _parse_spec(entry: Mapping[str, Any], where: str) -> ValueSpec:
    """Build the value specification of an option or field definition.

    Args:
        entry: Option or field table from the catalog.
        where: Location used in error messages.

    Returns:
        ValueSpec: The specification.

    Raises:
        CatalogError: If the type, choices, format, or fields are invalid.
    """
    kind = entry.get("type")
    if kind not in VALUE_TYPES:
        raise CatalogError(f"{where}: unknown type {kind!r}")
    fields = tuple(
        _parse_field(name, body, where) for name, body in entry.get("fields", {}).items()
    )
    spec = ValueSpec(
        kind=kind,
        choices=tuple(entry.get("choices", ())),
        minimum=entry.get("min"),
        maximum=entry.get("max"),
        pattern=entry.get("pattern", ""),
        text_format=entry.get("format", ""),
        fields=fields,
    )
    if (kind == "enum") != bool(spec.choices):
        raise CatalogError(f"{where}: choices are required for, and only for, enum")
    if spec.text_format not in TEXT_FORMATS:
        raise CatalogError(f"{where}: unknown format {spec.text_format!r}")
    if (kind == "list[table]") != bool(fields):
        raise CatalogError(f"{where}: fields are required for, and only for, list[table]")
    return spec


def _parse_field(name: str, body: Mapping[str, Any], where: str) -> FieldSpec:
    """Build one ``list[table]`` field definition.

    Args:
        name: Field name.
        body: Field table from the catalog.
        where: Location of the owning option, for error messages.

    Returns:
        FieldSpec: The field.

    Raises:
        CatalogError: If the field is nested or its default is invalid.
    """
    spec = _parse_spec(body, f"{where} field {name}")
    if spec.kind == "list[table]":
        raise CatalogError(f"{where} field {name}: nested arrays of tables are not supported")
    field = FieldSpec(
        name,
        spec,
        required=bool(body.get("required", False)),
        default=body.get("default"),
        description=body.get("description", ""),
    )
    if field.default is not None and problems(spec, field.default):
        raise CatalogError(f"{where} field {name}: default does not validate")
    return field


def _parse_option(entry: Mapping[str, Any], origin: str) -> Option:
    """Build and self-check one option definition.

    Args:
        entry: One ``[[options]]`` table.
        origin: Catalog name for error messages.

    Returns:
        Option: The option.

    Raises:
        CatalogError: If attributes are missing or inconsistent.
    """
    where = f"{origin}: option {entry.get('key', '?')}"
    missing = [name for name in _REQUIRED if name not in entry]
    if missing:
        raise CatalogError(f"{where}: missing {', '.join(missing)}")
    try:
        version_key(entry["since"])
    except ValueError as exc:
        raise CatalogError(f"{where}: {exc}") from exc
    when = entry.get("required_when")
    option = Option(
        key=entry["key"],
        spec=_parse_spec(entry, where),
        default=entry["default"],
        since=entry["since"],
        reload=entry["reload"],
        description=entry["description"],
        merge=entry.get("merge", "replace"),
        defaults_switch=entry.get("defaults_switch", ""),
        required_when=(when[0], when[1]) if when else None,
    )
    if option.reload not in RELOAD_CLASSES or option.merge not in MERGE_MODES:
        raise CatalogError(f"{where}: reload must be live|restart, merge replace|append")
    if option.merge == "append" and option.spec.kind != "list[table]":
        raise CatalogError(f"{where}: merge = append needs type list[table]")
    if problems(option.spec, option.default):
        raise CatalogError(f"{where}: default does not validate")
    return option


def _check_consistency(options: Sequence[Option], origin: str) -> None:
    """Reject duplicate keys, key/table clashes, and dangling references.

    Args:
        options: Parsed options.
        origin: Catalog name for error messages.

    Returns:
        None

    Raises:
        CatalogError: On the first inconsistency found.
    """
    index = {o.key: o for o in options}
    if len(index) != len(options):
        raise CatalogError(f"{origin}: duplicate option keys")
    tables = {t for o in options for t in table_prefixes(o.key)}
    if clash := sorted(tables & index.keys()):
        raise CatalogError(f"{origin}: {clash[0]} is both an option and a table")
    for option in options:
        switch = index.get(option.defaults_switch)
        if option.defaults_switch and (switch is None or switch.spec.kind != "bool"):
            raise CatalogError(f"{origin}: {option.key}: defaults_switch must name a bool option")
        if option.required_when and option.required_when[0] not in index:
            raise CatalogError(f"{origin}: {option.key}: required_when names an unknown key")
