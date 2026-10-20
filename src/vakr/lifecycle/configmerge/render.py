"""Render the catalog as documented defaults and as a minimal operator template.

``render_defaults`` produces the package-owned, read-only TOML that ships in
the defaults directory (CFGM-001): every option with its description, type,
reload class, and release of introduction, followed by its default value.
The result is valid TOML that parses to exactly the catalog defaults.
``render_template`` produces the small operator file a package installs
when none exists (CFGM-005).

Dependencies:
    tomlkit: Correct TOML quoting of scalar values.
    textwrap: Wraps descriptions into comment lines.

Safety:
    Pure functions of the catalog; nothing is read or written.

Example:
    >>> toml_literal({"keyword": "ISP", "wan_circuit": True})
    '{ keyword = "ISP", wan_circuit = true }'
"""

import re
import textwrap
from collections.abc import Mapping, Sequence
from typing import Any

import tomlkit

from .catalog import Catalog, Option
from .model import VERSION_KEY
from .values import FieldSpec, ValueSpec

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def toml_literal(value: Any) -> str:
    """Render a value as a single-line TOML literal.

    Args:
        value: String, number, boolean, list, or table.

    Returns:
        str: TOML text such as ``"stable"``, ``["a", "b"]``, or ``{ a = 1 }``.

    Raises:
        None
    """
    if isinstance(value, Mapping):
        pairs = [f"{_key(k)} = {toml_literal(v)}" for k, v in value.items()]
        return "{ " + ", ".join(pairs) + " }" if pairs else "{}"
    if isinstance(value, list):
        return "[" + ", ".join(toml_literal(v) for v in value) + "]"
    return tomlkit.item(value).as_string()


def _key(name: str) -> str:
    """Quote a key only when TOML requires it.

    Args:
        name: Key.

    Returns:
        str: Bare or quoted key.

    Raises:
        None
    """
    return name if _BARE_KEY.match(name) else toml_literal(name)


def type_label(spec: ValueSpec) -> str:
    """Describe a value type for people, such as ``enum: stable | prerelease``.

    Args:
        spec: Value specification.

    Returns:
        str: Short description including choices, bounds, and format.

    Raises:
        None
    """
    label = spec.kind
    unit = "s" if spec.kind == "duration" else ""
    if spec.choices:
        label += ": " + " | ".join(spec.choices)
    if spec.minimum is not None:
        label += f", >= {spec.minimum:g}{unit}"
    if spec.maximum is not None:
        label += f", <= {spec.maximum:g}{unit}"
    if spec.text_format:
        label += f" ({spec.text_format})"
    return label


def groups(catalog: Catalog) -> list[tuple[str, list[Option]]]:
    """Group options by table, top-level first, then in catalog order.

    Args:
        catalog: Option catalog.

    Returns:
        list[tuple[str, list[Option]]]: ``(table, options)`` pairs.

    Raises:
        None
    """
    grouped: dict[str, list[Option]] = {}
    for option in catalog.options:
        grouped.setdefault(option.table, []).append(option)
    return sorted(grouped.items(), key=lambda item: item[0] != "")


def _comments(option: Option) -> list[str]:
    """Render an option's documentation as TOML comment lines.

    Args:
        option: The option.

    Returns:
        list[str]: Description, metadata, and table fields.

    Raises:
        None
    """
    lines = [f"# {line}" for line in textwrap.wrap(option.description, 76)]
    lines.append(f"# Type: {type_label(option.spec)}. Reload: {option.reload}.")
    lines.append(f"# Since: {option.since}.")
    for field in option.spec.fields:
        lines.append(f"#   {field.name} ({type_label(field.spec)}, {requirement(field)}):")
        lines += [f"#     {line}" for line in textwrap.wrap(field.description, 72)]
    return lines


def requirement(field: FieldSpec) -> str:
    """Describe whether a table field is required or what it defaults to.

    Args:
        field: Field of a ``list[table]`` option.

    Returns:
        str: ``"required"``, ``"default <literal>"``, or ``"optional"``.

    Raises:
        None
    """
    if field.required:
        return "required"
    return "optional" if field.default is None else f"default {toml_literal(field.default)}"


def render_defaults(catalog: Catalog, header: Sequence[str]) -> str:
    """Render every option's default as documented TOML.

    Args:
        catalog: Option catalog.
        header: Lines for the opening comment block (without ``#``).

    Returns:
        str: TOML whose parsed content equals the catalog defaults.

    Raises:
        None
    """
    out = [f"# {line}".rstrip() for line in header]
    out.append(f"# Schema: {VERSION_KEY} {catalog.config_version}.")
    for table, options in groups(catalog):
        out += ["", f"[{table}]"] if table else []
        # Arrays of tables must follow the table's plain keys in TOML.
        arrays = [o for o in options if o.spec.kind == "list[table]" and o.default]
        for option in [o for o in options if o not in arrays]:
            out += [*_comments(option), f"{option.name} = {toml_literal(option.default)}", ""]
        for option in arrays:
            out += _comments(option)
            for item in option.default:
                out += [
                    f"[[{option.key}]]",
                    *(f"{_key(k)} = {toml_literal(v)}" for k, v in item.items()),
                ]
                out.append("")
        while out[-1] == "":
            out.pop()
    return "\n".join(out) + "\n"


def render_template(
    catalog: Catalog, header: Sequence[str], hints: Mapping[str, Any], version_note: str = ""
) -> str:
    """Render a minimal operator file: ``config_version`` plus commented hints.

    Args:
        catalog: Option catalog.
        header: Lines for the opening comment block (without ``#``).
        hints: Option keys to show commented out, mapped to an example value
            (``None`` shows the default).
        version_note: Inline comment for the ``config_version`` line.

    Returns:
        str: TOML text that parses to ``{config_version: N}`` only.

    Raises:
        KeyError: If a hint names an option the catalog does not define.
    """
    note = f"  # {version_note}" if version_note else ""
    out = [f"# {line}".rstrip() for line in header]
    out += ["", f"{VERSION_KEY} = {catalog.config_version}{note}"]
    tables: dict[str, list[str]] = {}
    for key, example in hints.items():
        option = catalog.option(key)
        if option is None:
            raise KeyError(key)
        value = toml_literal(option.default if example is None else example)
        choices = f"  # {' | '.join(option.spec.choices)}" if option.spec.choices else ""
        tables.setdefault(option.table, []).append(f"# {option.name} = {value}{choices}")
    for table, lines in tables.items():
        out += ["", f"# [{table}]", *lines]
    return "\n".join(out) + "\n"
