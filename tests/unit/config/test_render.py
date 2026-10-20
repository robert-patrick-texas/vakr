"""Rendering tests: packaged defaults, starter template, and reference page.

Requirements: CFGM-001, CFGM-005.

Dependencies:
    pytest: Assertions.

Example:
    $ uv run pytest tests/unit/config/test_render.py
"""

import tomllib
from pathlib import Path

from vakr.config import project
from vakr.lifecycle.configmerge import (
    Catalog,
    Layer,
    load_config,
    parse_catalog,
    render_markdown,
    toml_literal,
    version_key,
)
from vakr.lifecycle.configmerge.checks import with_field_defaults
from vakr.version import get_version

REPO = Path(__file__).resolve().parents[3]


def _nested(flat: dict) -> dict:
    """Turn dotted keys into nested tables.

    Args:
        flat: Values by dotted key.

    Returns:
        dict: Nested tables.

    Raises:
        None
    """
    root: dict = {}
    for key, value in flat.items():
        *tables, leaf = key.split(".")
        node = root
        for table in tables:
            node = node.setdefault(table, {})
        node[leaf] = value
    return root


def test_rendered_defaults_parse_to_exactly_the_catalog_defaults() -> None:
    """CFGM-001: the packaged defaults file is valid TOML equal to every catalog default.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    text = project.defaults_text()
    assert tomllib.loads(text) == _nested(project.catalog().defaults())
    assert text.startswith("# vakr built-in configuration defaults (CFGM-001).")
    for option in project.catalog().options:
        assert option.description.split()[0] in text


def test_rendered_defaults_validate_as_an_operator_file(tmp_path: Path) -> None:
    """Copying the whole defaults file into /etc/vakr would itself be a valid configuration.

    Args:
        tmp_path: Temporary directory.

    Returns:
        None

    Raises:
        None
    """
    layer = Layer(tmp_path / "config.toml", project.defaults_text().encode())
    result = load_config(project.catalog(), project.migrations(), [layer])
    assert result.issues == []


def test_template_is_minimal_valid_and_versioned(tmp_path: Path) -> None:
    """CFGM-005: the starter file holds only ``config_version`` plus commented hints.

    Args:
        tmp_path: Temporary directory.

    Returns:
        None

    Raises:
        None
    """
    text = project.template_text()
    assert tomllib.loads(text) == {"config_version": project.catalog().config_version}
    assert '# role = "primary"  # primary | peer' in text
    assert '# position = "east"' in text
    layer = Layer(tmp_path / "config.toml", text.encode())
    assert load_config(project.catalog(), project.migrations(), [layer]).issues == []


def test_committed_reference_page_is_current() -> None:
    """The committed docs page equals ``vakr config defaults --markdown`` output.

    Regenerate with ``uv run vakr config defaults --markdown >
    docs/operations/configuration.md`` when this fails.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    committed = (REPO / "docs/operations/configuration.md").read_text(encoding="utf-8")
    assert committed == project.reference_markdown()


def test_reference_page_lists_every_option_and_keyword() -> None:
    """Every option appears with its release tag, and the E.15 keywords are tabulated.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    page = project.reference_markdown()
    for option in project.catalog().options:
        assert f"| `{option.key}` |" in page
        assert f"| v{option.since} |" in page
    assert '| `"UPLINK"` | `"uplink"` | `"primary"` | `false` | `false` |' in page


def test_every_option_was_added_no_later_than_this_release() -> None:
    """Catalog ``since`` values never name a release newer than the running one (VER-001).

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    current = version_key(get_version())
    assert all(version_key(o.since) <= current for o in project.catalog().options)


def test_literals_and_markdown_cells_are_escaped() -> None:
    """Keys needing quotes are quoted and table cells cannot break Markdown layout.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert toml_literal({"a b": 1, "ok": [True, 1.5]}) == '{ "a b" = 1, ok = [true, 1.5] }'
    assert toml_literal({}) == "{}"

    catalog: Catalog = parse_catalog(
        '[catalog]\nconfig_version = 1\n[[options]]\nkey = "top"\ntype = "str"\n'
        'default = "a|b"\nsince = "1.0.0"\nreload = "live"\ndescription = "x | y"\n'
    )
    page = render_markdown(catalog, "T", ["Intro."], tag_prefix="")
    assert "### Top level" in page
    assert '| `top` | str | `"a\\|b"` | live | 1.0.0 | x \\| y |' in page
    assert with_field_defaults(catalog.options[0].spec, []) == []
