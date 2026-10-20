"""TUI-T05 and ARCH-001: the query services and the TUI use ports only, never SQL or adapters.

A static scan of every module in ``vakr.query`` and ``vakr.tui`` refuses
imports of storage drivers or adapters and any SQL text, so the TUI can
only ever see data through ``StatusQueries`` (TUI-007). The modules'
``>>>`` examples are run too, so their documentation stays true.

Dependencies:
    ast, doctest, importlib, re: Static scan and example runner.
    pytest: Parametrisation per module.

Example:
    $ uv run pytest tests/unit/query/test_query_architecture.py
"""

import ast
import doctest
import importlib
import re
from pathlib import Path

import pytest

import vakr
import vakr.query
import vakr.tui

SRC = Path(vakr.__file__).parent.parent
ROOTS = (Path(vakr.query.__file__).parent, Path(vakr.tui.__file__).parent)
MODULES = sorted(p for root in ROOTS for p in root.glob("*.py"))
FORBIDDEN = ("vakr.adapters", "sqlite3", "psycopg", "clickhouse", "vakr.cli")
SQL = re.compile(r"\b(SELECT\s.+\sFROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM)\b", re.I)


def module_name(path: Path) -> str:
    """Return the dotted module name of a source file.

    Args:
        path: A file under ``src/vakr``.

    Returns:
        str: Such as ``vakr.query.service``.

    Raises:
        None
    """
    relative = path.with_suffix("").relative_to(SRC)
    return ".".join(relative.parts).removesuffix(".__init__")


def imports(tree: ast.Module) -> set[str]:
    """List the modules a syntax tree imports.

    Args:
        tree: Parsed module.

    Returns:
        set[str]: Dotted module names.

    Raises:
        None
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize("path", MODULES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_no_driver_adapter_or_sql(path: Path) -> None:
    """TUI-T05: depends only on ports and query services; no ad hoc SQL (TUI-007, ARCH-001).

    Args:
        path: One module.

    Returns:
        None

    Raises:
        None
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad = sorted(m for m in imports(tree) if m.startswith(FORBIDDEN))
    assert bad == [], f"{path.name} imports {bad}"
    strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)]
    assert not [s for s in strings if isinstance(s, str) and SQL.search(s)]


def test_tui_reads_only_through_the_query_package() -> None:
    """TUI-007: the TUI imports no port or adapter directly; ``vakr.query`` is its data source.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    tui = [p for p in MODULES if p.parent.name == "tui"]
    for path in tui:
        found = imports(ast.parse(path.read_text(encoding="utf-8")))
        assert not [m for m in found if m.startswith("vakr.ports")], path.name


@pytest.mark.parametrize("path", MODULES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_documented_examples_hold(path: Path) -> None:
    """Every ``>>>`` example in the module docstrings is true.

    Args:
        path: One module.

    Returns:
        None

    Raises:
        None
    """
    module = importlib.import_module(module_name(path))
    result = doctest.testmod(module, optionflags=doctest.ELLIPSIS)
    assert result.failed == 0
