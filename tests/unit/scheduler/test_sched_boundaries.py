"""Architecture rules for ``vakr.scheduler``: no adapters, no blocking calls (6.2, 17.1).

The scheduler reaches collectors only through its own step protocols, so it
must not import a protocol adapter, a collector, or a storage adapter. It
runs on the event loop, so it must not call anything that blocks it.

Dependencies:
    ast, sys: Static scanning of the package source.

Example:
    $ uv run pytest tests/unit/scheduler/test_sched_boundaries.py
"""

import ast
import sys
from pathlib import Path

import pytest

import vakr.scheduler

PACKAGE = Path(vakr.scheduler.__file__).parent
FILES = sorted(PACKAGE.glob("*.py"))
ALLOWED = (
    "vakr.domain",
    "vakr.ports.clock",
    "vakr.logic",
    "vakr.scheduler",
    "vakr.policy.settings",
    "vakr.config.loader",
    "vakr.config.project",
    "vakr.lifecycle.configmerge",
)
BLOCKING = {("time", "sleep"), ("subprocess", "run"), ("os", "system")}
BLOCKING_NAMES = {"open", "input"}


def _tree(path: Path) -> ast.Module:
    """Parse one source file.

    Args:
        path: The file.

    Returns:
        ast.Module: Its syntax tree.

    Raises:
        SyntaxError: If it does not parse.
    """
    return ast.parse(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_imports_stay_inside_the_scheduler_boundary(path: Path) -> None:
    """Only the standard library, the domain, the clock port, and shared rules are imported.

    Args:
        path: One scheduler module.

    Returns:
        None

    Raises:
        None
    """
    found = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    for module in found:
        stdlib = module.split(".")[0] in sys.stdlib_module_names
        assert stdlib or module.startswith(ALLOWED), module


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_no_blocking_calls(path: Path) -> None:
    """No ``time.sleep``, ``open``, subprocesses, or ``input`` in event-loop code (6.2).

    Args:
        path: One scheduler module.

    Returns:
        None

    Raises:
        None
    """
    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            assert func.id not in BLOCKING_NAMES, f"{path.name}:{node.lineno}"
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            assert (func.value.id, func.attr) not in BLOCKING, f"{path.name}:{node.lineno}"
