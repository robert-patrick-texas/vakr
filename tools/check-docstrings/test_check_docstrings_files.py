# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""File-level tests for check-docstrings: modules, config, discovery and CLI.

CI relies on the gate's exit status and on ``path:line: name: message``
lines that editors can jump to (QG-003). These tests cover the module
docstring rules, reading and validating ``[tool.check-docstrings]``, which
files are visited (hidden directories, ``exclude`` globs, ``include-tests``),
the report format and exit codes of ``main``, and that the tool passes its
own rules. Function-level rules are in ``test_check_docstrings.py``.

Dependencies:
    pytest: Test runner; ``tmp_path``, ``capsys`` and ``monkeypatch`` isolate
        files, output and the working directory.
    check_docstrings: Module under test, importable through ``conftest.py``.

Safety:
    Tests only write beneath ``tmp_path`` and never access the network.

Example:
    $ uv run pytest tools/check-docstrings
"""

from pathlib import Path

import pytest
from check_docstrings import Config, check_file, load_config, main

GOOD = '"""Mod.\n\nDependencies:\n    None\n\nExample:\n    $ true\n"""\n'
BAD = GOOD + "\n\ndef helper(x):\n    return x\n"


def write(root: Path, files: dict[str, str]) -> None:
    """Create source files below ``root`` from a path-to-content mapping.

    Args:
        root (Path): Directory that receives the files.
        files (dict[str, str]): POSIX relative path mapped to file content.

    Returns:
        None.

    Raises:
        OSError: If a file cannot be written.
    """
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("x = 1\n", ["1: <module>: missing module docstring"]),
        ('"""Mod.\n\nExample:\n    >>> 1\n"""\n', ["1: <module>: missing Dependencies section"]),
        (
            '# c\n"""Mod.\n\nDependencies:\n\nExamples:\n    None\n"""\n',
            ["2: <module>: Dependencies section is empty; write None"],
        ),
        ("", []),
        ("def broken(:\n", ["1: <module>: cannot parse: invalid syntax (sample.py, line 1)"]),
    ],
    ids=["no-docstring", "missing-section", "empty-section", "empty-file", "syntax-error"],
)
def test_module_rules(tmp_path: Path, source: str, expected: list[str]) -> None:
    """Modules need a docstring with the configured sections (QG-003).

    Args:
        tmp_path (Path): Per-test temporary directory from pytest.
        source (str): Whole file content.
        expected (list[str]): ``line: name: message`` lines.

    Returns:
        None.

    Raises:
        AssertionError: If module problems are reported wrongly.
    """
    path = tmp_path / "sample.py"
    path.write_text(source, encoding="utf-8")
    assert [line.split(":", 1)[1].lstrip() for line in check_file(path, Config())] == expected


def test_configured_sections_replace_defaults(tmp_path: Path) -> None:
    """Projects choose their own required headers (QG-003, portability).

    Args:
        tmp_path (Path): Per-test temporary directory from pytest.

    Returns:
        None.

    Raises:
        AssertionError: If the configured sections are not the ones required.
    """
    cfg = Config(module_sections=("Safety",), function_sections=("Returns",))
    path = tmp_path / "sample.py"
    code = 'def f(x):\n    """Do it.\n\n    Returns:\n        None\n    """\n'
    path.write_text(GOOD + code, encoding="utf-8")
    assert check_file(path, cfg) == [f"{path.as_posix()}:1: <module>: missing Safety section"]


def test_load_config_reads_and_validates_table(tmp_path: Path) -> None:
    """``[tool.check-docstrings]`` overrides defaults; typos and bad types fail.

    Args:
        tmp_path (Path): Per-test temporary directory from pytest.

    Returns:
        None.

    Raises:
        AssertionError: If the table is misread or bad values are accepted.
    """
    path = tmp_path / "pyproject.toml"
    path.write_text("[project]\nname = 'x'\n", encoding="utf-8")
    assert load_config(path) == Config() == load_config(None)
    path.write_text(
        '[tool.check-docstrings]\nfunction-sections = ["Args"]\ninclude-tests = false\n'
        'exclude = ["gen/**"]\n',
        encoding="utf-8",
    )
    assert load_config(path) == Config(("Dependencies", "Example"), ("Args",), False, ("gen/**",))
    for body, error in [
        ("modul-sections = []", ValueError),
        ("exclude = 'gen'", TypeError),
        ("include-tests = 'no'", TypeError),
    ]:
        path.write_text(f"[tool.check-docstrings]\n{body}\n", encoding="utf-8")
        with pytest.raises(error):
            load_config(path)


def test_report_format_and_exit_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Problems print as ``path:line: name: message`` and exit 1 (QG-003).

    Args:
        tmp_path (Path): Per-test temporary directory from pytest.
        capsys (pytest.CaptureFixture[str]): Captures the report.
        monkeypatch (pytest.MonkeyPatch): Moves into ``tmp_path``.

    Returns:
        None.

    Raises:
        AssertionError: If the report, summary or status is wrong.
    """
    monkeypatch.chdir(tmp_path)
    write(tmp_path, {"pkg/a.py": GOOD, "pkg/b.py": BAD, "pkg/notes.txt": "not python"})
    assert main(["pkg"]) == 1
    out, err = capsys.readouterr()
    assert out == "pkg/b.py:11: helper: missing docstring\n"
    assert err == "check-docstrings: 2 files checked, 1 problems\n"
    assert main(["pkg/a.py"]) == 0
    assert capsys.readouterr().out == ""


def test_discovery_skips_hidden_generated_and_excluded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hidden trees, caches and ``exclude`` globs are never checked.

    Args:
        tmp_path (Path): Per-test temporary directory from pytest.
        capsys (pytest.CaptureFixture[str]): Captures the report.
        monkeypatch (pytest.MonkeyPatch): Moves into ``tmp_path``.

    Returns:
        None.

    Raises:
        AssertionError: If a skipped file is checked.
    """
    monkeypatch.chdir(tmp_path)
    files = {
        "src/ok.py": GOOD,
        "src/.venv/lib/site.py": BAD,
        "src/__pycache__/x.py": BAD,
        "src/gen/deep/out.py": BAD,
        "src/legacy.py": BAD,
    }
    write(tmp_path, files)
    config = '[tool.check-docstrings]\nexclude = ["src/gen/**", "legacy.py"]\n'
    (tmp_path / "pyproject.toml").write_text(config, encoding="utf-8")
    assert main(["src"]) == 0
    assert capsys.readouterr().err == "check-docstrings: 1 files checked, 0 problems\n"


def test_include_tests_false_skips_test_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test files are checked by default and skipped with ``include-tests = false``.

    Args:
        tmp_path (Path): Per-test temporary directory from pytest.
        capsys (pytest.CaptureFixture[str]): Captures the report.
        monkeypatch (pytest.MonkeyPatch): Moves into ``tmp_path``.

    Returns:
        None.

    Raises:
        AssertionError: If test files are included or skipped wrongly.
    """
    monkeypatch.chdir(tmp_path)
    tests = ["tests/helpers.py", "lib/test_a.py", "lib/a_test.py", "lib/conftest.py"]
    write(tmp_path, {"lib/core.py": GOOD} | dict.fromkeys(tests, BAD))
    assert main(["lib", "tests"]) == 1
    assert sorted(line.split(":")[0] for line in capsys.readouterr().out.splitlines()) == sorted(
        tests
    )
    config = tmp_path / "custom.toml"
    config.write_text("[tool.check-docstrings]\ninclude-tests = false\n", encoding="utf-8")
    assert main(["--config", str(config), "lib", "tests"]) == 0
    assert capsys.readouterr().err == "check-docstrings: 1 files checked, 0 problems\n"


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["missing-dir"], "no such file or directory: missing-dir"),
        (["--config", "absent.toml", "."], "absent.toml"),
        (["--config", "bad.toml", "."], "unknown [tool.check-docstrings] keys: strict"),
    ],
    ids=["missing-path", "missing-config", "unknown-key"],
)
def test_usage_errors_exit_2(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    message: str,
) -> None:
    """Bad paths and configuration are usage errors, not silent passes.

    Args:
        tmp_path (Path): Per-test temporary directory from pytest.
        capsys (pytest.CaptureFixture[str]): Captures the error text.
        monkeypatch (pytest.MonkeyPatch): Moves into ``tmp_path``.
        argv (list[str]): Command line under test.
        message (str): Text the error must contain.

    Returns:
        None.

    Raises:
        AssertionError: If the status is not 2 or the reason is missing.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "bad.toml").write_text("[tool.check-docstrings]\nstrict = true\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2
    assert message in capsys.readouterr().err


def test_tool_passes_its_own_rules() -> None:
    """check-docstrings' own files satisfy the default rules (QG-003).

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If any file in this directory has a problem.
    """
    here = Path(__file__).parent
    assert [p for f in sorted(here.glob("*.py")) for p in check_file(f, Config())] == []
