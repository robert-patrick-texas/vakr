# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests for check-docstrings, the docstring section and Args gate (QG-003).

The gate is only useful if it accepts every form the project's convention
allows (``None`` sections, typed and starred ``Args`` entries, skipped
``@overload`` stubs and attribute-only ``__init__`` methods) and rejects each
kind of gap in a function or class docstring with its own message. Every rule
has a passing and a failing case here; module, configuration and command-line
behaviour is in ``test_check_docstrings_files.py``. Each test writes
throwaway sources under ``tmp_path``.

Dependencies:
    pytest: Test runner; ``tmp_path`` isolates the sample files.
    check_docstrings, check_docstrings_rules: Modules under test, importable
        through this directory's ``conftest.py``.

Safety:
    Tests only write beneath ``tmp_path`` and never access the network.

Example:
    $ uv run pytest tools/check-docstrings
"""

import textwrap
from pathlib import Path

import pytest
from check_docstrings import Config, check_file

MODULE = '"""Sample.\n\nDependencies:\n    None.\n\nExample:\n    >>> 1 + 1\n    2\n"""\n'
PASSING = '''
from typing import overload

def every_kind(a, b, /, c, *args, d=1, **kwargs):
    """Take every kind of parameter.

    Args:
        a: Positional-only.
        b (int): Positional-only, with a type.
        c (dict[str, tuple[int, int]] | None): Ordinary, nested type.
        *args: Extra positionals, written with a star.
        d (int, optional): Keyword-only with a default; the description
            continues here: and may contain colons.
        kwargs: Extra keywords, written without stars.

    Returns:
        None.

    Raises:
        None
    """

def no_params():
    """Take nothing.

    Args:
        None

    Returns:
        int: One.

    Raises:
        ValueError: Never, in practice.
    """
    return 1

def one_line_none():
    """Use the one-line form of empty sections.

    Args: None
    Returns: None.
    Raises: None
    """

def numbers(limit):
    """Yield numbers; Yields may replace Returns in a generator.

    Args:
        limit (int): Exclusive upper bound.

    Yields:
        int: The next number.

    Raises:
        None.
    """
    yield from range(limit)

class Thing:
    """A sample class whose __init__ only stores attributes."""

    def __init__(self, size: int, label: str) -> None:
        self.size, self.label = size, label
        self.ready: bool = True

    @property
    def double(self) -> int:
        """Return twice the size.

        Args:
            None.

        Returns:
            int: Twice ``size``.

        Raises:
            None.
        """
        return self.size * 2

    @classmethod
    async def make(cls, size):
        """Build one asynchronously.

        Args:
            size (int): Size of the new thing.

        Returns:
            Thing: The new instance.

        Raises:
            None.

        Examples:
            >>> await Thing.make(3)
        """
        return cls(size, "")

@overload
def over(x: int) -> int: ...
@overload
def over(x: str) -> str: ...
def over(x):
    """Return the argument unchanged.

    Args:
        x (int | str): Any value.

    Returns:
        int | str: ``x``.

    Raises:
        None.
    """
    return x
'''


def problems(tmp_path: Path, code: str) -> list[str]:
    """Check ``MODULE`` followed by ``code`` and return ``name: message`` lines.

    Args:
        tmp_path (Path): Per-test temporary directory from pytest.
        code (str): Source appended after a valid module docstring; it is
            dedented first.

    Returns:
        list[str]: Each reported problem without its ``path:line:`` prefix.

    Raises:
        OSError: If the sample file cannot be written.
    """
    path = tmp_path / "sample.py"
    path.write_text(MODULE + textwrap.dedent(code), encoding="utf-8")
    return [line.split(": ", 1)[1] for line in check_file(path, Config())]


def test_every_allowed_form_passes(tmp_path: Path) -> None:
    """``None`` sections, typed and starred entries and skips pass (QG-003).

    Args:
        tmp_path (Path): Per-test temporary directory from pytest.

    Returns:
        None.

    Raises:
        AssertionError: If an allowed form is reported.
    """
    assert problems(tmp_path, PASSING) == []


def func(signature: str, args: str = "x: The input.", tail: str = "") -> str:
    """Build a function whose docstring has every section, for one-line cases.

    Args:
        signature (str): Name and parameters, such as ``"f(x)"``.
        args (str): Text of the ``Args`` section body.
        tail (str): Docstring lines added after the ``Raises`` section.

    Returns:
        str: Source of a function ``def`` with an empty body.

    Raises:
        None.
    """
    sections = f"Args:\n        {args}\n\n    Returns:\n        None\n\n    Raises:\n        None\n"
    return f'def {signature}:\n    """Do it.\n\n    {sections}{tail}    """\n'


NESTED = "\n    def g(y):\n        return y\n\n    class Inner:\n"
NESTED += "        def m(self):\n            pass\n"
INIT = 'class C:\n    """C."""\n\n    def __init__(self, x):\n'


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        pytest.param("def f(x):\n    pass\n", ["f: missing docstring"], id="no-docstring"),
        pytest.param(
            'def f(x):\n    """Do it.\n\n    Args:\n        x: In.\n    """\n',
            ["f: missing Returns section", "f: missing Raises section"],
            id="missing-sections",
        ),
        pytest.param(func("f()", ""), ["f: Args section is empty; write None"], id="empty-section"),
        pytest.param(
            func("f(x, y)", "None"),
            ["f: Args is None but the function takes x, y"],
            id="none-with-params",
        ),
        pytest.param(
            func("f()"),
            ["f: Args must be None: the function takes no parameters"],
            id="entries-without-params",
        ),
        pytest.param(func("f(x, y)"), ["f: Args missing: y"], id="missing-param"),
        pytest.param(
            func("f(y)"),
            ["f: Args missing: y", "f: Args documents unknown parameters: x"],
            id="unknown-param",
        ),
        pytest.param(
            func("f(x)", "x: In.\n        x: Again."),
            ["f: Args documents x more than once"],
            id="duplicate-entry",
        ),
        pytest.param(
            func("f(x)", "The input x."),
            ["f: Args entry is not 'name:' or 'name (type):': The input x.", "f: Args missing: x"],
            id="malformed-entry",
        ),
        pytest.param(func("f(*x)", "**x: In."), ["f: Args entry **x should be *x"], id="stars"),
        pytest.param(
            func("f(self)", "None"),
            ["f: Args is None but the function takes self"],
            id="self-outside-class",
        ),
        pytest.param(
            func("f(x)").replace("Returns:", "Yields:"),
            ["f: missing Returns section"],
            id="yields-outside-generator",
        ),
        pytest.param(
            func("f(x)", tail="\n    Example:\n        >>> f(1 +)\n"),
            ["f: example at docstring line 13 does not parse: invalid syntax"],
            id="example-syntax",
        ),
        pytest.param("class C:\n    pass\n", ["C: missing class docstring"], id="class"),
        pytest.param(
            INIT + "        self.x = x.strip()\n        print(x)\n",
            ["C.__init__: missing docstring"],
            id="init-with-behavior",
        ),
        pytest.param(
            INIT + '        """Store x.\n\n        Returns:\n            None\n\n'
            '        Raises:\n            None\n        """\n        self.x = x\n',
            ["C.__init__: missing Args section"],
            id="init-docstring-still-checked",
        ),
        pytest.param(
            func("f(x)") + NESTED,
            [
                "f.g: missing docstring",
                "f.Inner: missing class docstring",
                "f.Inner.m: missing docstring",
            ],
            id="nested-definitions",
        ),
    ],
)
def test_each_rule_reports_its_problem(tmp_path: Path, code: str, expected: list[str]) -> None:
    """Every rule produces its own message for the offending definition (QG-003).

    Args:
        tmp_path (Path): Per-test temporary directory from pytest.
        code (str): Source with exactly the problems in ``expected``.
        expected (list[str]): ``name: message`` lines in report order.

    Returns:
        None.

    Raises:
        AssertionError: If a problem is missed or an extra one reported.
    """
    assert problems(tmp_path, code) == expected
