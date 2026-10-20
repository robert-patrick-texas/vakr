"""Migration operation tests: rename, move, remove, set, map, scale, convert, split (CFGM-002).

Dependencies:
    pytest: Parametrised cases.
    tomlkit: Documents the operations edit.

Example:
    $ uv run pytest tests/unit/config/test_operations.py
"""

import pytest
import tomlkit

from vakr.lifecycle.configmerge import MigrationError
from vakr.lifecycle.configmerge.migrations import Migration, Operation
from vakr.lifecycle.configmerge.operations import apply_migrations, apply_operation


def _run(text: str, op: str, **params: object) -> str:
    """Apply one operation to TOML text.

    Args:
        text: TOML document.
        op: Operation name.
        **params: Operation parameters.

    Returns:
        str: The edited document.

    Raises:
        ValueError: If the operation fails.
    """
    doc = tomlkit.parse(text)
    apply_operation(doc, Operation(op, dict(params)))
    return doc.as_string()


@pytest.mark.parametrize(
    ("text", "op", "params", "expected"),
    [
        (
            "[a]\n# n\nx = 1  # c\ny = 2\n",
            "rename",
            {"from": "a.x", "to": "a.z"},
            "[a]\n# n\nz = 1  # c\ny = 2\n",
        ),
        (
            "[a]\nx = 1  # c\n[b]\ny = 2\n",
            "rename",
            {"from": "a.x", "to": "b.x"},
            "[b]\ny = 2\nx = 1  # c\n",
        ),
        (
            "[a]\n# keep\nx = 1\n",
            "rename",
            {"from": "a.x", "to": "c.d.x"},
            "[a]\n# keep\n\n[c.d]\nx = 1\n",
        ),
        ("[a]\nx = 1\n", "rename", {"from": "a.missing", "to": "a.y"}, "[a]\nx = 1\n"),
        (
            "# t\n[old]\nx = 1\n\n[k]\n",
            "move_table",
            {"from": "old", "to": "new"},
            "# t\n[new]\nx = 1\n\n[k]\n",
        ),
        (
            "[old]\nx = 1\n[new]\ny = 2\n",
            "move_table",
            {"from": "old", "to": "new"},
            "[new]\ny = 2\nx = 1\n",
        ),
        ("[a]\nx = 1\n", "move_table", {"from": "zz", "to": "a"}, "[a]\nx = 1\n"),
        ("[a]\nx = 1\ny = 2\n", "remove", {"key": "a.x"}, "[a]\ny = 2\n"),
        ("[a]\nx = 1\n[b]\ny = 2\n", "remove", {"key": "a"}, "[b]\ny = 2\n"),
        ("[a]\nx = 1\n", "remove", {"key": "a.zz"}, "[a]\nx = 1\n"),
        ("[a]\nx = 1\n", "set", {"key": "a.x", "value": 5}, "[a]\nx = 5\n"),
        ("[a]\nx = 1\n", "set", {"key": "b.c.y", "value": [1]}, "[a]\nx = 1\n\n[b.c]\ny = [1]\n"),
        (
            '[a]\nm = "beta"  # c\n',
            "map_values",
            {"key": "a.m", "map": {"beta": "rc"}},
            '[a]\nm = "rc"  # c\n',
        ),
        ("[a]\nm = 3\n", "map_values", {"key": "a.m", "map": {"3": "x"}}, "[a]\nm = 3\n"),
        ("[a]\nt = 2  # s\n", "scale", {"key": "a.t", "factor": 1000}, "[a]\nt = 2000  # s\n"),
        ("[a]\nt = 3\n", "scale", {"key": "a.t", "factor": 0.5}, "[a]\nt = 1.5\n"),
        (
            "[a]\nt = 90\n",
            "convert",
            {"key": "a.t", "to": "duration", "unit": "s"},
            '[a]\nt = "90s"\n',
        ),
        ('[a]\nt = "5m"\n', "convert", {"key": "a.t", "to": "duration"}, '[a]\nt = "5m"\n'),
        ('[a]\nt = "8"\n', "convert", {"key": "a.t", "to": "int"}, "[a]\nt = 8\n"),
        ("[a]\nt = 8\n", "convert", {"key": "a.t", "to": "str"}, '[a]\nt = "8"\n'),
        ("[a]\nt = 8\n", "convert", {"key": "a.t", "to": "float"}, "[a]\nt = 8.0\n"),
        ('[a]\nl = "x"\n', "convert", {"key": "a.l", "to": "list"}, '[a]\nl = ["x"]\n'),
        ("[a]\nl = [1]\n", "convert", {"key": "a.l", "to": "list"}, "[a]\nl = [1]\n"),
        (
            '[a]\nl = "x, y,"\n',
            "convert",
            {"key": "a.l", "to": "list", "separator": ","},
            '[a]\nl = ["x", "y"]\n',
        ),
        (
            '[a]\nv = "h:1"\n',
            "split",
            {"from": "a.v", "into": ["a.h", "a.p"], "separator": ":"},
            '[a]\nh = "h"\np = "1"\n',
        ),
        ("[a]\nv = 7\n", "split", {"from": "a.v", "into": ["b.x", "b.y"]}, "\n[b]\nx = 7\ny = 7\n"),
        ("[a]\nq = 1\n", "split", {"from": "a.v", "into": ["a.x"]}, "[a]\nq = 1\n"),
        (
            "[[d]]\nold = 1  # c\n[[d]]\nold = 2\n",
            "rename",
            {"from": "d[].old", "to": "d[].new"},
            "[[d]]\nnew = 1  # c\n[[d]]\nnew = 2\n",
        ),
        ("x = 1\n", "rename", {"from": "d[].old", "to": "d[].new"}, "x = 1\n"),
        ("[[d]]\nt = 1\n", "scale", {"key": "d[].t", "factor": 60}, "[[d]]\nt = 60\n"),
    ],
)
def test_operations_edit_documents_and_keep_comments(
    text: str, op: str, params: dict, expected: str
) -> None:
    """Each operation produces the expected text, keeping inline comments with their values.

    Args:
        text: Input document.
        op: Operation name.
        params: Operation parameters.
        expected: Output document.

    Returns:
        None

    Raises:
        None
    """
    assert _run(text, op, **params) == expected


@pytest.mark.parametrize(
    ("text", "op", "params", "error", "message"),
    [
        (
            "[a]\nx = 1\ny = 2\n",
            "rename",
            {"from": "a.x", "to": "a.y"},
            MigrationError,
            "a.y is already set",
        ),
        (
            "[a]\nx = 1\n[b]\nx = 2\n",
            "rename",
            {"from": "a.x", "to": "b.x"},
            MigrationError,
            "already set",
        ),
        (
            "[a]\nx = 1\nb = 2\n",
            "rename",
            {"from": "a.x", "to": "a.b.c"},
            TypeError,
            "a.b is not a table",
        ),
        (
            "[a]\nx = 1\n[b]\nx = 2\n",
            "move_table",
            {"from": "a", "to": "b"},
            MigrationError,
            "already set",
        ),
        (
            "x = 1\n[b]\ny = 1\n",
            "move_table",
            {"from": "x", "to": "b"},
            MigrationError,
            "needs tables",
        ),
        ('[a]\nt = "x"\n', "scale", {"key": "a.t", "factor": 2}, ValueError, "expected a number"),
        (
            "[a]\nt = 1.5\n",
            "convert",
            {"key": "a.t", "to": "duration"},
            ValueError,
            "whole numbers",
        ),
        (
            '[a]\nt = "soon"\n',
            "convert",
            {"key": "a.t", "to": "duration"},
            ValueError,
            "expected a duration",
        ),
        ("[a]\nt = true\n", "convert", {"key": "a.t", "to": "int"}, ValueError, "cannot convert"),
        ('[a]\nt = "x"\n', "convert", {"key": "a.t", "to": "int"}, ValueError, "invalid literal"),
        (
            '[a]\nv = "h"\n',
            "split",
            {"from": "a.v", "into": ["a.h", "a.p"], "separator": ":"},
            ValueError,
            "2 parts",
        ),
        (
            '[a]\nv = "h:1"\nh = 0\n',
            "split",
            {"from": "a.v", "into": ["a.h", "a.p"], "separator": ":"},
            ValueError,
            "already set",
        ),
        (
            "[[d]]\nx = 1\n",
            "rename",
            {"from": "d[].x", "to": "e[].x"},
            MigrationError,
            "same array prefix",
        ),
    ],
)
def test_operations_refuse_conflicts_and_bad_values(
    text: str, op: str, params: dict, error: type[Exception], message: str
) -> None:
    """Conflicting destinations and untransformable values raise instead of guessing.

    Args:
        text: Input document.
        op: Operation name.
        params: Operation parameters.
        error: Expected exception type.
        message: Expected message fragment.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(error, match=message):
        _run(text, op, **params)


def test_apply_migrations_names_the_failing_migration() -> None:
    """A failing operation is reported with the migration that contains it.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    doc = tomlkit.parse("[a]\nx = 1\nb = 2\n")
    bad = Migration(1, 2, "d", (Operation("rename", {"from": "a.x", "to": "a.b.c"}),))
    with pytest.raises(MigrationError, match=r"config migration 1->2 failed: a\.b is not a table"):
        apply_migrations(doc, [bad])
