# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""End-to-end tests of check-updates: one scripted repository, every source, both outputs.

A throwaway repository pins Python, Go, a Makefile tool, locked packages,
two workflow actions, and a vendored asset, some current and some behind.
The offline fetcher answers with recorded documents and canned command
output, and one source fails as a network outage would. The tests check
the table, the JSON, which rows are flagged, and the exit status with and
without ``--fail-on-outdated`` (TOOLS-001, TOOLS-002, TOOLS-003).

Dependencies:
    pytest: ``tmp_path`` and ``capsys``.
    check_updates: Entry point under test, driven through ``main``.

Safety:
    Offline: every request and command is answered by ``FakeFetcher``.
    Writes only beneath ``tmp_path``.

Example:
    $ uv run pytest tools/check-updates/test_check_updates.py
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import check_updates
import pytest
from updates_fetch import CommandResult, FetchError
from updates_upstream import GO_RELEASES_URL, PYTHON_RELEASES_URL

SHA = "1c37ad07a6a961277cf70c0d37d6f313000f5884"
GH = "https://api.github.com/repos"
CONFIG = """\
assets = [{ name = "echarts", version = "5.6.0", npm = "echarts" }]
[python]
version_file = ".python-version"
requires_python = "pyproject.toml"
[go]
binary = ["go", "version"]
go_mod = ["**/go.mod"]
[pypi]
[lockfile]
[actions]
[[tools]]
name = "uv"
command = ["uv", "--version"]
pypi = "uv"
[[tools]]
name = "pip-audit (make audit)"
file = "Makefile"
regex = 'pip-audit==(\\S+)'
pypi = "pip-audit"
"""
FILES = {
    ".python-version": "3.15\n",
    "pyproject.toml": '[project]\nrequires-python = ">=3.15"\ndependencies = ["tomlkit>=0.13"]\n'
    '[dependency-groups]\ndev = ["ruff>=0.17"]\n',
    "uv.lock": 'version = 1\n[[package]]\nname = "tomlkit"\nversion = "0.13.0"\n'
    '[[package]]\nname = "ruff"\nversion = "0.17.1"\n'
    '[[package]]\nname = "cffi"\nversion = "2.1.0"\n',
    "Makefile": "PIP_AUDIT := pip-audit==2.9.0\n",
    "cmd/probe/go.mod": "module probe\n\ngo 1.26\n",
    ".github/workflows/ci.yml": f"steps:\n  - uses: actions/checkout@v7\n"
    f"  - uses: astral-sh/setup-uv@{SHA} # v10.2.0\n",
    ".github/workflows/pages.yml": "steps:\n  - uses: actions/checkout@v7\n",
}
LOCK_OUTPUT = (
    "Resolved 4 packages\nUpdate cffi v2.1.0 -> v2.1.1\nUpdate tomlkit v0.13.0 -> v0.15.2\n"
)
GO_LIST_OUTPUT = (
    '{"Path": "probe", "Main": true}\n'
    '{"Path": "golang.org/x/sys", "Version": "v0.30.0", "Update": {"Version": "v0.31.0"}}\n'
    '{"Path": "golang.org/x/text", "Version": "v0.20.0", "Indirect": true}\n'
    '{"Path": "github.com/miekg/dns", "Version": "v1.1.68"}\n'
)
EXPECTED_STATUS = {  # every row of the scripted repository, in report order
    "Python (.python-version)": "current",
    "Python (requires-python in pyproject.toml)": "current",
    "Python (interpreter uv selects)": "current",
    "Go (installed)": "current",
    "Go (cmd/probe/go.mod go directive)": "update available",
    "golang.org/x/sys": "update available",
    "github.com/miekg/dns": "current",
    "uv": "unknown",
    "pip-audit (make audit)": "update available",
    "tomlkit": "update available",
    "ruff": "current",
    "uv.lock (all packages)": "update available",
    "cffi": "update available",
    "actions/checkout@v7": "current",
    "astral-sh/setup-uv@1c37ad0": "update available",
    "echarts": "update available",
}


def make_repo(root: Path) -> Path:
    """Write the scripted repository and its config below ``root``.

    Args:
        root: Empty directory.

    Returns:
        Path: The config file path.

    Raises:
        OSError: If a file cannot be written.
    """
    for rel, text in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    config = root / "check-updates.toml"
    config.write_text(CONFIG, encoding="utf-8")
    return config


def scripted(fake_fetcher: Any, recorded: Callable[[str], Any]) -> Any:
    """Build the offline fetcher for the scripted repository.

    Args:
        fake_fetcher: ``FakeFetcher`` class.
        recorded: Fixture loader.

    Returns:
        Any: A fetcher where PyPI's ``uv`` lookup fails like an outage.

    Raises:
        OSError: If a fixture is missing.
    """
    pypi = "https://pypi.org/pypi"
    responses = {
        PYTHON_RELEASES_URL: recorded("python-releases.json"),
        GO_RELEASES_URL: recorded("go-dl.json"),
        f"{pypi}/ruff/json": recorded("pypi-ruff.json"),
        f"{pypi}/tomlkit/json": {"info": {"version": "0.15.2"}, "releases": {"0.15.2": [{}]}},
        f"{pypi}/pip-audit/json": {"info": {"version": "2.10.1"}},
        f"{pypi}/uv/json": FetchError("https://pypi.org/pypi/uv/json: timed out"),
        f"{GH}/actions/checkout/releases?per_page=10": recorded("github-checkout.json"),
        f"{GH}/astral-sh/setup-uv/releases?per_page=10": [{"tag_name": "v10.3.0"}],
        "https://registry.npmjs.org/echarts/latest": {"version": "6.0.0"},
    }
    commands = {
        ("uv", "python", "find", "--show-version"): CommandResult(0, "3.15.0\n", ""),
        ("go", "version"): CommandResult(0, "go version go1.27.3 linux/amd64\n", ""),
        ("uv", "--version"): CommandResult(0, "uv 0.13.3 (x86_64-unknown-linux-gnu)\n", ""),
        ("uv", "lock", "--upgrade", "--dry-run"): CommandResult(0, "", LOCK_OUTPUT),
        ("go", "list", "-m", "-u", "-json", "all"): CommandResult(0, GO_LIST_OUTPUT, ""),
    }
    return fake_fetcher(responses, commands)


def test_json_report_covers_every_source(
    tmp_path: Path, fake_fetcher: Any, recorded: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every configured source yields a row with the right status (TOOLS-001, TOOLS-003).

    Args:
        tmp_path: Repository root.
        fake_fetcher: Offline fetcher class.
        recorded: Fixture loader.
        capsys: Output capture.

    Returns:
        None

    Raises:
        AssertionError: If a row or status differs.
    """
    fetcher = scripted(fake_fetcher, recorded)
    argv = ["--config", str(make_repo(tmp_path)), "--root", str(tmp_path), "--json"]
    assert check_updates.main(argv, fetcher) == 0
    report = json.loads(capsys.readouterr().out)
    rows = {row["item"]: row for row in report["items"]}
    assert {item: row["status"] for item, row in rows.items()} == EXPECTED_STATUS
    assert list(rows) == list(EXPECTED_STATUS)  # planned order survives concurrency
    assert report["summary"] == {"current": 7, "update available": 8, "unknown": 1}
    assert rows["uv"]["detail"].endswith("timed out")
    assert rows["uv"]["current"] == "0.13.3"
    assert rows["actions/checkout@v7"]["detail"] == (
        "used in .github/workflows/ci.yml, .github/workflows/pages.yml"
    )
    assert rows["astral-sh/setup-uv@1c37ad0"]["current"] == "v10.2.0"
    assert rows["cffi"]["notes"] == "https://pypi.org/project/cffi/2.1.1/"
    assert rows["pip-audit (make audit)"]["detail"] == "pinned in Makefile"
    assert "Update tomlkit 0.13.0 -> 0.15.2" in rows["uv.lock (all packages)"]["detail"]
    assert rows["golang.org/x/sys"]["notes"] == "https://pkg.go.dev/golang.org/x/sys@v0.31.0"
    assert rows["golang.org/x/sys"]["detail"] == "required by cmd/probe/go.mod"
    # uv runs at the root so .python-version applies; go list runs in its module.
    cwds = {argv[0:2]: cwd for argv, cwd in fetcher.commands_run}
    assert cwds[("uv", "lock")] == tmp_path.resolve()
    assert cwds[("go", "list")] == tmp_path.resolve() / "cmd/probe"


def test_table_report_and_exit_codes(
    tmp_path: Path, fake_fetcher: Any, recorded: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """The table lists notes links and reasons; only --fail-on-outdated exits 1 (TOOLS-002).

    Args:
        tmp_path: Repository root.
        fake_fetcher: Offline fetcher class.
        recorded: Fixture loader.
        capsys: Output capture.

    Returns:
        None

    Raises:
        AssertionError: If the table or exit status differs.
    """
    config = make_repo(tmp_path)
    argv = ["--config", str(config), "--root", str(tmp_path)]
    assert check_updates.main(argv, scripted(fake_fetcher, recorded)) == 0
    table = capsys.readouterr().out
    lines = table.splitlines()
    assert lines[0].split() == ["ITEM", "CURRENT", "LATEST", "STATUS", "RELEASE", "NOTES"]
    assert any(
        line.startswith("tomlkit ") and "0.13.0" in line and "update available" in line
        for line in lines
    )
    assert "https://go.dev/doc/devel/release#go1.27.3" in table
    assert "unknown: uv: https://pypi.org/pypi/uv/json: timed out" in table
    assert lines[-1] == "check-updates: 16 items: 7 current, 8 update available, 1 unknown"
    failing = [*argv, "--fail-on-outdated"]
    assert check_updates.main(failing, scripted(fake_fetcher, recorded)) == 1


def test_outage_reports_unknown_and_never_fails(
    tmp_path: Path, fake_fetcher: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """With no network and no tools, every row is unknown and the exit status is 0.

    Args:
        tmp_path: Repository root.
        fake_fetcher: Offline fetcher class with nothing recorded.
        capsys: Output capture.

    Returns:
        None

    Raises:
        AssertionError: If the tool crashes, fails, or invents a status (TOOLS-001).
    """
    config = make_repo(tmp_path)
    argv = ["--config", str(config), "--root", str(tmp_path), "--json", "--fail-on-outdated"]
    assert check_updates.main(argv, fake_fetcher()) == 0
    report = json.loads(capsys.readouterr().out)
    assert {row["status"] for row in report["items"]} == {"unknown"}
    assert all(row["detail"] for row in report["items"])


def test_all_current_passes_the_gate(
    tmp_path: Path, fake_fetcher: Any, recorded: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """When nothing is behind, --fail-on-outdated exits 0 (TOOLS-002).

    Args:
        tmp_path: Repository root.
        fake_fetcher: Offline fetcher class.
        recorded: Fixture loader.
        capsys: Output capture.

    Returns:
        None

    Raises:
        AssertionError: If the gate fails.
    """
    make_repo(tmp_path)
    config = tmp_path / "python-only.toml"
    config.write_text('[python]\nversion_file = ".python-version"\nuv_interpreter = false\n')
    fetcher = fake_fetcher({PYTHON_RELEASES_URL: recorded("python-releases.json")})
    argv = ["--config", str(config), "--root", str(tmp_path), "--fail-on-outdated"]
    assert check_updates.main(argv, fetcher) == 0
    assert capsys.readouterr().out.splitlines()[-1].startswith("check-updates: 1 items: 1 current")


def test_shipped_config_is_valid_and_plans_offline(
    fake_fetcher: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """The repository's own check-updates.toml loads and runs without network (TOOLS-001).

    Args:
        fake_fetcher: Offline fetcher class.
        capsys: Output capture.

    Returns:
        None

    Raises:
        AssertionError: If the shipped config is invalid or the run fails.
    """
    root = Path(check_updates.__file__).resolve().parents[2]
    argv = ["--root", str(root), "--json"]
    assert check_updates.main(argv, fake_fetcher()) == 0
    items = {row["item"] for row in json.loads(capsys.readouterr().out)["items"]}
    assert {"Python (.python-version)", "uv", "ruff", "ty", "uv.lock (all packages)"} <= items
    assert any(item.startswith("astral-sh/setup-uv@") for item in items)
