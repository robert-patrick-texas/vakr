# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests of the requirements-file check: every exact pin outside ``uv.lock`` gets a row.

A hash-pinned constraints file, such as the build backend a package build
installs, is read by neither the per-package PyPI rows nor the lockfile
dry run. These tests check that its exact pins are parsed the way pip
reads the file, that each is compared with PyPI, and that a missing or
pin-less file cannot vanish from the report (TOOLS-T11), plus that the
``[pypi]`` keys are validated (TOOLS-T05).

Dependencies:
    pytest: ``tmp_path`` and output capture.
    check_updates, updates_config, updates_requirements: Code under test.

Safety:
    Offline: every PyPI request is answered by ``FakeFetcher``. Writes only
    beneath ``tmp_path``.

Example:
    $ uv run pytest tools/check-updates/test_updates_requirements.py
"""

import json
from pathlib import Path
from typing import Any

import check_updates
import pytest
from updates_config import ConfigError, load_config
from updates_requirements import requirement_pins

SAMPLE = """\
# Regenerate with uv pip compile (a header comment)
hatchling==1.32.4 \\
    --hash=sha256:aa \\
    --hash=sha256:bb
Packaging==26.3 ; python_version >= "3.15" \\
    --hash=sha256:cc
uvicorn[standard]==0.40.0
    # via hatchling
pip>=26          # a range, not a pin
wild==1.*
arbitrary===1.0
-r other.txt
--index-url https://example.invalid/simple
pkg @ https://example.invalid/pkg-1.0-py3-none-any.whl
"""


def _pypi(version: str) -> dict[str, Any]:
    """Build a minimal PyPI JSON document whose newest stable release is ``version``.

    Args:
        version: Newest release.

    Returns:
        dict[str, Any]: The document.

    Raises:
        None
    """
    releases = {version: [{"yanked": False}], "0.0.1": [{"yanked": False}]}
    return {"info": {"version": version, "project_urls": {}}, "releases": releases}


def _repo(root: Path, files: list[str]) -> Path:
    """Write a config whose ``[pypi]`` checks only the given requirements files.

    Args:
        root: Repository root.
        files: Paths relative to ``root``.

    Returns:
        Path: The config file.

    Raises:
        OSError: If the file cannot be written.
    """
    config = root / "check-updates.toml"
    listed = ", ".join(f'"{file}"' for file in files)
    config.write_text(
        f'[pypi]\ndirect_dependencies = false\nlockfile = "uv.lock"\nrequirements = [{listed}]\n',
        encoding="utf-8",
    )
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    return config


def _run(config: Path, root: Path, fetcher: Any, gate: bool = False) -> int:
    """Run check-updates with JSON output.

    Args:
        config: Config file.
        root: Repository root.
        fetcher: Offline fetcher.
        gate: Whether to pass ``--gate``.

    Returns:
        int: Exit status; the caller reads the report through ``capsys``.

    Raises:
        None
    """
    argv = ["--config", str(config), "--root", str(root), "--json", *(["--gate"] if gate else [])]
    return check_updates.main(argv, fetcher)


def test_pins_are_read_as_pip_reads_the_file() -> None:
    """Continuations, hashes, markers, and extras are read; ranges and options skipped (TOOLS-T11).

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If a pin is missed or a non-pin is reported.
    """
    assert requirement_pins(SAMPLE) == [
        ("hatchling", "1.32.4"),
        ("packaging", "26.3"),
        ("uvicorn", "0.40.0"),
    ]


def test_each_pin_is_compared_with_pypi(
    tmp_path: Path, fake_fetcher: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """A pin behind PyPI is an update, an equal one current, and the gate stops (TOOLS-T11).

    Args:
        tmp_path: Repository root.
        fake_fetcher: Offline fetcher class.
        capsys: Output capture.

    Returns:
        None

    Raises:
        AssertionError: If a row or the exit status differs.
    """
    build = tmp_path / "build"
    build.mkdir()
    (build / "constraints.txt").write_text(
        "hatchling==1.32.4 \\\n    --hash=sha256:aa\ntomlkit==0.15.3 \\\n    --hash=sha256:bb\n",
        encoding="utf-8",
    )
    fetcher = fake_fetcher(
        {
            "https://pypi.org/pypi/hatchling/json": _pypi("1.33.0"),
            "https://pypi.org/pypi/tomlkit/json": _pypi("0.15.3"),
        }
    )
    status = _run(_repo(tmp_path, ["build/constraints.txt"]), tmp_path, fetcher, gate=True)
    rows = {row["item"]: row for row in json.loads(capsys.readouterr().out)["items"]}
    hatchling = rows["hatchling (build/constraints.txt)"]
    assert (hatchling["current"], hatchling["latest"]) == ("1.32.4", "1.33.0")
    assert hatchling["status"] == "update available"
    assert hatchling["detail"] == "pinned in build/constraints.txt"
    assert rows["tomlkit (build/constraints.txt)"]["status"] == "current"
    assert status == 1


def test_a_missing_or_pinless_file_is_unknown(
    tmp_path: Path, fake_fetcher: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """A renamed or emptied file shows as unknown, never as nothing (TOOLS-T11).

    Args:
        tmp_path: Repository root.
        fake_fetcher: Offline fetcher class.
        capsys: Output capture.

    Returns:
        None

    Raises:
        AssertionError: If either file drops out of the report.
    """
    (tmp_path / "ranges.txt").write_text("# only ranges\npip>=26\n", encoding="utf-8")
    config = _repo(tmp_path, ["missing.txt", "ranges.txt"])
    status = _run(config, tmp_path, fake_fetcher(), gate=True)
    rows = {row["item"]: row for row in json.loads(capsys.readouterr().out)["items"]}
    assert rows["missing.txt"]["status"] == "unknown"
    assert "missing.txt" in rows["missing.txt"]["detail"]
    assert rows["ranges.txt"]["status"] == "unknown"
    assert rows["ranges.txt"]["detail"] == "no name==version pins found"
    assert status == 1


@pytest.mark.parametrize(
    ("table", "message"),
    [
        ('requirements = "build.txt"', r"\[pypi\] 'requirements' must be a list of strings"),
        ('requirement = ["build.txt"]', r"\[pypi\] unknown key\(s\) requirement"),
    ],
)
def test_pypi_keys_are_validated(tmp_path: Path, table: str, message: str) -> None:
    """A mistyped or misspelt ``[pypi]`` key is refused, not ignored (TOOLS-T05).

    Args:
        tmp_path: Directory for the config.
        table: Body of the ``[pypi]`` table.
        message: Expected error pattern.

    Returns:
        None

    Raises:
        AssertionError: If the config is accepted.
    """
    config = tmp_path / "check-updates.toml"
    config.write_text(f"[pypi]\n{table}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_config(config)
