"""Architecture tests: ``vakr.logic`` stays pure, and its documented examples stay true.

The measurement rules are pure functions by contract: no I/O, no clock
reads, no logging, no randomness, and no imports beyond the standard
library and the shared domain vocabulary (ARCH-001). A static scan enforces
that, so a convenience ``datetime.now()`` cannot slip in and make results
depend on when a test happens to run. Every module's ``>>>`` examples are
executed so the documentation cannot drift from the behaviour.

Dependencies:
    ast, doctest, importlib, pkgutil: Static scan and example runner.
    pytest: Parametrisation per module.

Example:
    $ uv run pytest tests/unit/logic/test_logic_purity.py
"""

import ast
import doctest
import importlib
import pkgutil
from pathlib import Path

import pytest

import vakr.logic

PACKAGE = Path(vakr.logic.__file__).parent
MODULES = sorted(p for p in PACKAGE.glob("*.py"))
ALLOWED_IMPORTS = {
    "collections",  # deque: the bounded window of vakr.logic.window (E.33); no I/O
    "collections.abc",
    "dataclasses",
    "datetime",
    "decimal",
    "enum",
    "fractions",
    "math",
    "re",
    "vakr.domain.enums",
    "vakr.domain.quality",
    "vakr.logic",
}
FORBIDDEN_CALLS = {"now", "utcnow", "today", "time", "monotonic", "perf_counter", "open", "print"}


def imported_modules(tree: ast.Module) -> set[str]:
    """List every module a syntax tree imports.

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
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_imports_only_stdlib_and_domain_vocabulary(path: Path) -> None:
    """ARCH-001: no drivers, network libraries, frameworks, adapters, or I/O modules.

    Args:
        path: One module of ``vakr.logic``.

    Returns:
        None

    Raises:
        None
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    allowed = {
        m for m in imported_modules(tree) if m in ALLOWED_IMPORTS or m.startswith("vakr.logic.")
    }
    assert imported_modules(tree) == allowed


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_no_clock_io_or_logging_calls(path: Path) -> None:
    """Pure functions: callers pass ``now`` and elapsed times in.

    Args:
        path: One module of ``vakr.logic``.

    Returns:
        None

    Raises:
        None
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = {
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert not calls & FORBIDDEN_CALLS


@pytest.mark.parametrize(
    "name",
    [
        "vakr.logic",
        *sorted(f"vakr.logic.{m.name}" for m in pkgutil.iter_modules(vakr.logic.__path__)),
    ],
)
def test_docstring_examples_run(name: str) -> None:
    """QG-003: every ``>>>`` example in ``vakr.logic`` produces what it shows.

    Args:
        name: Module to check.

    Returns:
        None

    Raises:
        None
    """
    result = doctest.testmod(importlib.import_module(name), optionflags=doctest.ELLIPSIS)
    assert result.attempted > 0
    assert result.failed == 0
