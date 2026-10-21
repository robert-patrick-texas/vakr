"""ARCH-001, 6.2: the HTTP layer sees ports only, and the API never writes telemetry.

``vakr.api`` imports no adapter, driver, ingest writer, or SQL; only the
composition root ``vakr.app.api`` knows the SQLite adapters. The read-only
telemetry view refuses the batch writer, rollup writes, and retention, so
the collector stays the only writer (6.2). The ``[api]`` options parse and
validate as Appendix E.30 says, and every documented example holds.

Dependencies:
    ast, doctest, importlib; vakr.api; vakr.app.api.

Example:
    $ uv run pytest tests/unit/api/test_api_architecture.py
"""

import ast
import doctest
import importlib
import ipaddress
import re
from pathlib import Path

import pytest

import vakr
import vakr.api
import vakr.app.api
from tests.unit.query.query_testkit import Fleet
from vakr.api.settings import ApiSettings, api_problems, api_settings_from_config
from vakr.app.api.readonly import ReadOnlyTelemetry
from vakr.config.cross_checks import first_problem
from vakr.config.loader import EffectiveConfig
from vakr.paths import InstallPaths

SRC = Path(vakr.__file__).parent.parent
HTTP_LAYER = sorted(Path(vakr.api.__file__).parent.glob("*.py"))
ROOT_LAYER = sorted(Path(vakr.app.api.__file__).parent.glob("*.py"))
FORBIDDEN = ("vakr.adapters", "sqlite3", "psycopg", "clickhouse", "vakr.ingest", "vakr.cli")
WRITES = re.compile(r"write_batch|retention\.apply|\.maintain\(|\.writer\b(?!\s*\()")
"""Telemetry writes; ``csv.writer(...)`` is not one."""


def module_name(path: Path) -> str:
    """Return the dotted name of a source file.

    Args:
        path: A file under ``src/vakr``.

    Returns:
        str: Such as ``vakr.api.app``.

    Raises:
        None
    """
    return ".".join(path.with_suffix("").relative_to(SRC).parts).removesuffix(".__init__")


def imported(path: Path) -> set[str]:
    """List the modules a file imports.

    Args:
        path: A source file.

    Returns:
        set[str]: Dotted module names.

    Raises:
        None
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize("path", HTTP_LAYER, ids=lambda p: p.name)
def test_arch001_http_layer_uses_ports_only(path: Path) -> None:
    """ARCH-001, TUI-007: no adapter, driver, ingest, or SQL in ``vakr.api``.

    Args:
        path: One module.

    Returns:
        None

    Raises:
        None
    """
    bad = sorted(m for m in imported(path) if m.startswith(FORBIDDEN))
    assert bad == [], f"{path.name} imports {bad}"
    text = path.read_text(encoding="utf-8")
    assert WRITES.search(text) is None, path.name


@pytest.mark.parametrize("path", HTTP_LAYER + ROOT_LAYER, ids=lambda p: p.name)
def test_documented_examples_hold(path: Path) -> None:
    """Every ``>>>`` example in the API modules' docstrings is true.

    Args:
        path: One module.

    Returns:
        None

    Raises:
        None
    """
    module = importlib.import_module(module_name(path))
    assert doctest.testmod(module, optionflags=doctest.ELLIPSIS).failed == 0


def test_6_2_the_api_view_of_telemetry_cannot_write() -> None:
    """6.2: writer, rollup writes, retention, and maintenance are refused; reads pass.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    view = ReadOnlyTelemetry(Fleet().telemetry())
    for name in ("writer", "maintain", "ranking_projection"):  # E.28: only the collector projects
        with pytest.raises(PermissionError, match="only the collector writes"):
            getattr(view, name)
    with pytest.raises(PermissionError):
        view.rollups.write(())
    with pytest.raises(PermissionError):
        view.retention.apply(None)
    assert view.retention.policy is not None
    assert view.rollups.coverage_end() is None or view.rollups.coverage_end()
    assert view.capabilities is not None
    assert view.query is not None


def test_e30_api_settings_and_validation(tmp_path: Path) -> None:
    """E.30, SEC-013: defaults, the run-directory socket, proxies, and refused values.

    Args:
        tmp_path: Relocated install root.

    Returns:
        None

    Raises:
        None
    """
    paths = InstallPaths.under(tmp_path)
    default = api_settings_from_config(EffectiveConfig({}), paths)
    assert default == ApiSettings(socket_path=paths.run_dir / "api.sock")
    chosen = api_settings_from_config(EffectiveConfig({"api": {
        "listen_address": "::1", "socket_path": "/srv/vakr.sock",
        "trusted_proxies": ["127.0.0.1", "10.0.0.0/8"]}}), paths)  # fmt: skip
    assert chosen.socket_path == Path("/srv/vakr.sock")
    assert chosen.trusted_proxies == (ipaddress.ip_network("127.0.0.1/32"),
                                      ipaddress.ip_network("10.0.0.0/8"))  # fmt: skip
    wrong = {"listen_address": "localhost", "socket_path": "relative.sock",
             "trusted_proxies": ["10.0.0.1/8", "proxy.example"]}  # fmt: skip
    bad = EffectiveConfig({"api": wrong})
    problems = api_problems(bad)
    assert len(problems) == 4
    assert first_problem(bad) == ("invalid api settings", problems)
    with pytest.raises(ValueError, match="listen_address"):
        api_settings_from_config(bad, paths)
    off = api_settings_from_config(EffectiveConfig({"api": {"listen_address": ""}}), paths)
    assert off.listen_address == ""
