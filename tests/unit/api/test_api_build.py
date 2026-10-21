"""16.3 for the API: what each startup check refuses, and what it tolerates (SVC-005, invariant 2).

A missing or unusable telemetry database is tolerated: the API serves
inventory with telemetry "unavailable". An unsafe key file, a bad ``[api]``
section, a stale or foreign socket file, and a missing run directory are
refused with the check's name.

Dependencies:
    vakr.app.api.build, vakr.app.api.listeners; tests.unit.api.test_api_server (layout).

Example:
    $ uv run pytest tests/unit/api/test_api_build.py
"""

import socket
from pathlib import Path

import pytest

from tests.unit.api.test_api_server import install
from tests.unit.auth.auth_testkit import cheap_hasher
from vakr.adapters.sqlite.telemetry import TELEMETRY_FILE
from vakr.app.api.build import build_parts, incident_reader
from vakr.app.api.listeners import open_listeners, open_tcp, open_unix
from vakr.app.collector.preflight import PreflightError
from vakr.auth import KEY_FILE, Passwords
from vakr.config.loader import EffectiveConfig
from vakr.paths import InstallPaths
from vakr.query import DeviceListRequest

CHEAP = Passwords(cheap_hasher())


def test_svc005_telemetry_trouble_is_tolerated_and_reported(tmp_path: Path) -> None:
    """Invariant 2, 13.4: no or unusable telemetry still starts; views say why.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    paths = install(tmp_path, 8080)
    (paths.state_dir / TELEMETRY_FILE).unlink()
    parts = build_parts(paths, passwords=CHEAP)
    try:
        assert parts.context.telemetry.startswith("no telemetry database yet")
    finally:
        parts.close()
    (paths.state_dir / TELEMETRY_FILE).write_bytes(b"not a database")
    parts = build_parts(paths, passwords=CHEAP)
    try:
        assert parts.context.telemetry.startswith("telemetry database unusable")
        health = parts.context.overview.device_list(DeviceListRequest(limit=1)).health
        assert health.telemetry.value != "ok"
    finally:
        parts.close()


def test_svc005_unsafe_key_and_bad_settings_are_refused(tmp_path: Path) -> None:
    """16.3, E.12: a world-readable key file and an invalid ``[api]`` stop the start.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    paths = install(tmp_path, 8080)
    key = paths.state_dir / KEY_FILE
    key.write_text("00" * 32 + "\n")
    key.chmod(0o644)
    with pytest.raises(PreflightError) as refused:
        build_parts(paths, passwords=CHEAP)
    assert refused.value.check == "auth_key"
    key.unlink()
    (paths.config_dir / "config.toml").write_text(
        'config_version = 1\n\n[api]\nlisten_address = "localhost"\n'
    )
    with pytest.raises(PreflightError) as bad:
        build_parts(paths, passwords=CHEAP)
    assert bad.value.check == "config"
    assert "listen_address" in bad.value.message


def test_16_3_socket_files_and_directories(tmp_path: Path) -> None:
    """16.3: a stale socket is replaced; a live one, a regular file, or no directory is refused.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "api.sock"
    stale = socket.socket(socket.AF_UNIX)
    stale.bind(str(path))
    stale.close()
    live = open_unix(path)  # the stale file is removed and replaced
    try:
        with pytest.raises(PreflightError, match="another vakr-api"):
            open_unix(path)
    finally:
        live.close()
        path.unlink()
    path.write_text("not a socket")
    with pytest.raises(PreflightError, match="is not a socket"):
        open_unix(path)
    with pytest.raises(PreflightError, match="does not exist"):
        open_unix(tmp_path / "missing" / "api.sock")
    taken = open_tcp("127.0.0.1", 0)
    try:
        with pytest.raises(PreflightError, match="cannot listen"):
            open_tcp("127.0.0.1", taken.getsockname()[1])
    finally:
        taken.close()
    six = open_tcp("::1", 0) if socket.has_ipv6 else None
    if six is not None:
        six.close()


def test_listeners_open_both_or_neither(tmp_path: Path) -> None:
    """16.3: when the socket cannot be made, the TCP listener is closed again.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    from vakr.api.settings import ApiSettings

    port_probe = open_tcp("127.0.0.1", 0)
    port = port_probe.getsockname()[1]
    port_probe.close()
    settings = ApiSettings(port=port, socket_path=tmp_path / "missing" / "api.sock")
    with pytest.raises(PreflightError):
        open_listeners(settings)
    again = open_tcp("127.0.0.1", port)  # free again: nothing stayed open
    again.close()
    off = open_listeners(ApiSettings(listen_address="", socket_path=tmp_path / "api.sock"))
    try:
        assert off.tcp is None
        assert len(off.sockets()) == 1
    finally:
        off.close()
    assert not (tmp_path / "api.sock").exists()


def test_overview_incidents_reader_is_read_only_and_tolerant(tmp_path: Path) -> None:
    """12.2: the overview reads each spool's incident log; a missing spool root gives none.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    paths = InstallPaths.under(tmp_path)
    read = incident_reader(EffectiveConfig({}), paths)
    assert list(read()) == []
    (paths.state_dir / "spool" / "0190a3c4-0000-7000-8000-000000000001").mkdir(parents=True)
    assert list(read()) == []
    bad = incident_reader(EffectiveConfig({"spool": {"warning_pct": 99, "critical_pct": 1}}), paths)
    assert list(bad()) == []
