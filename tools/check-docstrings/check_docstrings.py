# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Fail when Python docstrings lack the sections a project requires.

Code that explains itself needs every module to say what it is for, what it
depends on and how to use it, and every function to say what it takes,
returns and raises, even when the honest answer is "None". ruff's ``D`` rules
check docstring layout but not section presence, and pydoclint cannot parse
``Args: None``. This gate parses each file with ``ast`` and reports missing
docstrings, missing or empty sections, ``Args`` entries that disagree with the
real parameters, and ``>>>`` examples that do not parse, for every module,
class and function, nested ones and tests included. It is portable: the
required sections, test inclusion and exclusions come from an optional
``[tool.check-docstrings]`` table, and nothing is imported from the project.

Dependencies:
    check_docstrings_rules: Sibling module with the per-docstring rules, so
        each file stays within the 300-line limit.
    ast: Parses source without importing it, so checking runs no project code.
    tomllib: Reads ``[tool.check-docstrings]`` from ``pyproject.toml``.

Safety:
    Read-only: files are parsed, never imported, executed or modified.

Example:
    $ uv run python tools/check-docstrings/check_docstrings.py src tests tools
    check-docstrings: 34 files checked, 0 problems
"""

import argparse
import ast
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from check_docstrings_rules import function_problems, section_problems

# Generated or vendored trees; hidden directories (.git, .venv) are skipped too.
SKIPPED_DIRS = frozenset({"__pycache__", "_site", "node_modules"})
LIST_KEYS = ("module-sections", "function-sections", "exclude")


@dataclass(frozen=True, slots=True)
class Config:
    """Project rules read from ``[tool.check-docstrings]``. Immutable.

    Attributes:
        module_sections (tuple[str, ...]): Headers every module docstring needs.
        function_sections (tuple[str, ...]): Headers every function needs.
        include_tests (bool): Whether test files and ``tests/`` trees are checked.
        exclude (tuple[str, ...]): Globs matched against the cwd-relative POSIX
            path (``**`` spans directories) and against the bare name.
    """

    module_sections: tuple[str, ...] = ("Dependencies", "Example")
    function_sections: tuple[str, ...] = ("Args", "Returns", "Raises")
    include_tests: bool = True
    exclude: tuple[str, ...] = ()


def load_config(path: Path | None) -> Config:
    """Read the ``[tool.check-docstrings]`` table, rejecting unknown keys.

    Args:
        path (Path | None): TOML file, or None to use the defaults.

    Returns:
        Config: Defaults overridden by the keys present in the table; the
        defaults alone when the file has no such table.

    Raises:
        OSError: If the file cannot be read.
        ValueError: If the TOML is invalid or a key is unknown.
        TypeError: If a value has the wrong type.
    """
    if path is None:
        return Config()
    table = tomllib.loads(path.read_text(encoding="utf-8")).get("tool", {})
    table = table.get("check-docstrings", {})
    # A misspelt key would otherwise silently fall back to a default.
    if unknown := sorted(set(table) - {*LIST_KEYS, "include-tests"}):
        raise ValueError(f"{path}: unknown [tool.check-docstrings] keys: {', '.join(unknown)}")
    defaults, lists = Config(), {}
    for key in LIST_KEYS:
        value = table.get(key, list(getattr(defaults, key.replace("-", "_"))))
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise TypeError(f"{path}: {key} must be a list of strings")
        lists[key] = tuple(value)
    include_tests = table.get("include-tests", defaults.include_tests)
    if not isinstance(include_tests, bool):
        raise TypeError(f"{path}: include-tests must be true or false")
    return Config(
        lists["module-sections"], lists["function-sections"], include_tests, lists["exclude"]
    )


def skipped(path: Path, cfg: Config) -> bool:
    """Report whether a file or directory is excluded from checking.

    Args:
        path (Path): Candidate file or directory.
        cfg (Config): Project rules.

    Returns:
        bool: True when an ``exclude`` glob matches, or when tests are
        excluded and the path is a test file or lies in a ``tests`` tree.

    Raises:
        None.
    """
    rel = PurePosixPath(path.absolute().relative_to(Path.cwd(), walk_up=True).as_posix())
    name = PurePosixPath(rel.name)
    if any(rel.full_match(g) or name.full_match(g) for g in cfg.exclude):
        return True
    if cfg.include_tests:
        return False
    return (
        "tests" in rel.parts
        or name.match("test_*.py")
        or name.match("*_test.py")
        or (rel.name == "conftest.py")
    )


def discover(paths: list[Path], cfg: Config) -> list[Path]:
    """Find the Python files to check, pruning skipped directories early.

    Args:
        paths (list[Path]): Files and directories named on the command line.
        cfg (Config): Project rules.

    Returns:
        list[Path]: Sorted, de-duplicated ``*.py`` files that are not skipped.

    Raises:
        None.
    """
    found: set[Path] = set()
    for top in paths:
        if top.is_file():
            found.add(top)
            continue
        for root, dirs, files in top.walk():
            # Pruning in place keeps the walk out of virtualenvs and worktrees.
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith(".") and d not in SKIPPED_DIRS and not skipped(root / d, cfg)
            ]
            found.update(root / f for f in files if f.endswith(".py"))
    return sorted(f for f in found if not skipped(f, cfg))


def walk(
    node: ast.AST, scope: str, in_class: bool, required: tuple[str, ...]
) -> list[tuple[int, str, str]]:
    """Check every class and function below ``node``, nested ones included.

    Args:
        node (ast.AST): Module, class or function whose children are visited.
        scope (str): Dotted name prefix such as ``"Outer."``, or "".
        in_class (bool): True when ``node`` is a class body.
        required (tuple[str, ...]): Section headers every function needs.

    Returns:
        list[tuple[int, str, str]]: ``(line, qualified name, message)`` per
        problem, in source order.

    Raises:
        None.
    """
    found: list[tuple[int, str, str]] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
            msgs = function_problems(child, in_class, required)
        elif isinstance(child, ast.ClassDef):
            doc = ast.get_docstring(child)
            msgs = ["missing class docstring"] if doc is None else section_problems(doc, ())
        else:
            # Definitions under if/try/with keep the enclosing scope.
            found += walk(child, scope, in_class, required)
            continue
        name = scope + child.name
        found += [(child.lineno, name, msg) for msg in msgs]
        found += walk(child, name + ".", isinstance(child, ast.ClassDef), required)
    return found


def check_file(path: Path, cfg: Config) -> list[str]:
    """Check one file and format its problems for the report.

    Args:
        path (Path): Python source file.
        cfg (Config): Project rules.

    Returns:
        list[str]: ``path:line: name: message`` lines in source order, where
        name is ``<module>`` or a dotted name such as ``Class.method``.

    Raises:
        OSError: If the file cannot be read.
    """
    try:
        # Bytes let ast honour coding declarations and a UTF-8 BOM.
        tree = ast.parse(path.read_bytes(), filename=str(path))
    except (SyntaxError, ValueError) as exc:
        line = getattr(exc, "lineno", None) or 1
        return [f"{path.as_posix()}:{line}: <module>: cannot parse: {exc}"]
    doc = ast.get_docstring(tree)
    if doc is None:
        # An empty file, such as a bare package marker, has nothing to document.
        found = [(1, "<module>", "missing module docstring")] if tree.body else []
    else:
        found = [
            (tree.body[0].lineno, "<module>", msg)
            for msg in section_problems(doc, cfg.module_sections)
        ]
    found += walk(tree, "", False, cfg.function_sections)
    found.sort(key=lambda item: item[0])
    return [f"{path.as_posix()}:{line}: {name}: {msg}" for line, name, msg in found]


def main(argv: list[str] | None = None) -> int:
    """Check every Python file under the given paths and print the problems.

    Args:
        argv (list[str] | None): Arguments without the program name, or None
            to read ``sys.argv``.

    Returns:
        int: 0 when every docstring passes, 1 when any problem was found.

    Raises:
        SystemExit: On ``--help`` (status 0), or status 2 for invalid
            arguments, a missing path or an invalid configuration.
        OSError: If a source file cannot be read.
    """
    parser = argparse.ArgumentParser(
        prog="check_docstrings.py",
        description="Check docstring sections and Args entries in Python files.",
    )
    parser.add_argument("paths", nargs="+", type=Path, metavar="PATH", help="file or directory")
    parser.add_argument(
        "--config",
        type=Path,
        metavar="TOML",
        help="file with [tool.check-docstrings] (default: ./pyproject.toml when present)",
    )
    args = parser.parse_args(argv)
    if missing := [str(p) for p in args.paths if not p.exists()]:
        parser.error(f"no such file or directory: {', '.join(missing)}")
    default = Path("pyproject.toml")
    try:
        cfg = load_config(args.config or (default if default.is_file() else None))
    except (OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    files = discover(args.paths, cfg)
    problems = [problem for path in files for problem in check_file(path, cfg)]
    # Flush so problems precede the stderr summary when both go to one CI log.
    print("\n".join(problems), end="\n" if problems else "", flush=True)
    print(
        f"check-docstrings: {len(files)} files checked, {len(problems)} problems", file=sys.stderr
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
