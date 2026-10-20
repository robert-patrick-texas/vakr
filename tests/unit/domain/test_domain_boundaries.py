"""ARCH-001 and 5.2 rule 1: the domain and ports import nothing but the standard library.

``vakr.domain`` may import only the standard library and itself;
``vakr.ports`` may add ``vakr.domain``. Neither may import a database
driver, a network library, a web framework, or ``vakr.adapters``, so the
model stays testable without infrastructure and portable to other backends.

Dependencies:
    ast, sys: Static import scanning against ``sys.stdlib_module_names``.

Example:
    $ uv run pytest tests/unit/domain/test_domain_boundaries.py
"""

import ast
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[3] / "src" / "vakr"


def _imports(path: Path) -> set[str]:
    """Return the top-level-qualified module names a file imports.

    Args:
        path: Python source file.

    Returns:
        set[str]: Module names such as ``datetime`` or ``vakr.domain.ids``.

    Raises:
        SyntaxError: If the file does not parse.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


@pytest.mark.parametrize(
    ("package", "allowed"), [("domain", ("vakr.domain",)), ("ports", ("vakr.domain", "vakr.ports"))]
)
def test_arch001_import_boundary(package: str, allowed: tuple[str, ...]) -> None:
    """ARCH-001: every import is standard library or an allowed vakr package.

    Args:
        package: ``domain`` or ``ports``.
        allowed: vakr packages the package may import.

    Returns:
        None

    Raises:
        None
    """
    files = sorted((SRC / package).rglob("*.py"))
    assert files
    offenders = []
    for path in files:
        for module in _imports(path):
            stdlib = module.split(".")[0] in sys.stdlib_module_names
            if not stdlib and not any(module == a or module.startswith(a + ".") for a in allowed):
                offenders.append(f"{path.relative_to(SRC)}: {module}")
    assert offenders == []
