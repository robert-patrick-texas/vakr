"""Render the option catalog as a Markdown configuration reference.

Operators read documentation on the published site, not in the package. A
reference generated from the same catalog that validates their files cannot
drift from what the software accepts, and a test that compares the committed
page with this renderer keeps it current.

Dependencies:
    render: Shared value and type formatting.

Safety:
    Pure function of the catalog; table cells are escaped so a description
    cannot break the table layout.

Example:
    >>> render_markdown(Catalog(1, []), "Configuration reference", ["Intro."]).splitlines()[0]
    '# Configuration reference'
"""

from collections.abc import Sequence

from .catalog import Catalog, Option
from .checks import with_field_defaults
from .render import groups, requirement, toml_literal, type_label


def _cell(text: str) -> str:
    """Escape text for a Markdown table cell.

    Args:
        text: Cell content.

    Returns:
        str: Content with pipes escaped and newlines flattened.

    Raises:
        None
    """
    return text.replace("|", "\\|").replace("\n", " ")


def _default_cell(option: Option) -> str:
    """Render an option's default for the summary table.

    Args:
        option: The option.

    Returns:
        str: Inline code, or a pointer to the entry table below.

    Raises:
        None
    """
    if option.spec.kind == "list[table]" and option.default:
        return f"{len(option.default)} entries, listed below"
    return f"`{_cell(toml_literal(option.default))}`"


def _array_section(option: Option) -> list[str]:
    """Render the fields and default entries of an array-of-tables option.

    Args:
        option: A ``list[table]`` option.

    Returns:
        list[str]: Markdown lines.

    Raises:
        None
    """
    names = [field.name for field in option.spec.fields]
    out = ["", f"#### `[[{option.key}]]`", "", "| Field | Type | Presence | Description |"]
    out.append("|---|---|---|---|")
    for field in option.spec.fields:
        out.append(
            f"| `{field.name}` | {_cell(type_label(field.spec))} | {_cell(requirement(field))} "
            f"| {_cell(field.description)} |"
        )
    if option.default:
        out += ["", "Packaged entries:", "", "| " + " | ".join(names) + " |"]
        out.append("|" + "---|" * len(names))
        for item in with_field_defaults(option.spec, option.default):
            cells = [f"`{toml_literal(item[n])}`" if n in item else "" for n in names]
            out.append("| " + " | ".join(cells) + " |")
    return out


def render_markdown(
    catalog: Catalog, title: str, intro: Sequence[str], tag_prefix: str = "v"
) -> str:
    """Render a Markdown reference page for every option.

    Args:
        catalog: Option catalog.
        title: Page title.
        intro: Paragraphs placed before the option tables.
        tag_prefix: Prefix that turns a version into its release tag name.

    Returns:
        str: Markdown text ending with a newline.

    Raises:
        None
    """
    out = [f"# {title}", ""]
    for paragraph in intro:
        out += [paragraph, ""]
    out += ["## Options", ""]
    for table, options in groups(catalog):
        heading = f"`[{table}]`" if table else "Top level"
        out += [
            f"### {heading}",
            "",
            "| Option | Type | Default | Reload | Added in | Description |",
        ]
        out.append("|---|---|---|---|---|---|")
        for option in options:
            out.append(
                f"| `{option.key}` | {_cell(type_label(option.spec))} | {_default_cell(option)} "
                f"| {option.reload} | {tag_prefix}{option.since} | {_cell(option.description)} |"
            )
        for option in options:
            if option.spec.kind == "list[table]":
                out += _array_section(option)
        out.append("")
    return "\n".join(out).rstrip() + "\n"
