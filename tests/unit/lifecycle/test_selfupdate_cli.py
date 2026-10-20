"""``vakr self-update`` CLI binding, the timer invocation, health check, and ``doctor --network``.

The timer test runs the exact ``ExecStart`` command line
(``self-update --yes --if-newer --unattended``) against a local HTTPS
"GitHub" with real signature verification; only ``apt-get``,
``systemctl``, and the newly installed binary are stand-ins (UPD-003,
UPD-T05, EGRESS-005).

Dependencies:
    tests.unit.lifecycle.helpers: PKI, HTTPS server, signer, fakes.

Example:
    $ uv run pytest tests/unit/lifecycle/test_selfupdate_cli.py
"""

import json
import os
from pathlib import Path

import pytest

from tests.unit.lifecycle.helpers import (
    FakeInstaller,
    HttpsServer,
    Pki,
    Route,
    Signer,
    as_json,
    build_release,
    release_json,
)
from vakr.cli import cmd_selfupdate, cmd_tls
from vakr.cli.main import main
from vakr.health.model import Status
from vakr.lifecycle.update.lock import exclusive_lock
from vakr.lifecycle.update.process import RunResult
from vakr.lifecycle.update.state import UpdateState, load_state, save_state
from vakr.paths import InstallPaths

NEW_VAKR = '#!/bin/sh\n[ "$1" = config ] && exit 3\nexit 0\n'
"""Stand-in for the newly installed binary: ``config upgrade`` is not implemented yet."""

pytestmark = pytest.mark.skipif(
    not Path("/usr/bin/ssh-keygen").exists(), reason="the CLI verifies with /usr/bin/ssh-keygen"
)


@pytest.fixture
def host(
    tmp_path: Path, pki: Pki, signer: Signer, monkeypatch: pytest.MonkeyPatch
) -> tuple[InstallPaths, FakeInstaller]:
    """Relocate an installed vakr under ``tmp_path`` with fakes for host changes.

    Args:
        tmp_path: Temporary directory.
        pki: Test certificates (the test CA plays the OS bundle).
        signer: Release signer installed as the package's release signers.
        monkeypatch: Pytest monkeypatch.

    Returns:
        tuple: Paths and the fake installer.

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path / "root"))
    for name in ("HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    paths = InstallPaths.from_environment()
    paths.release_signers.parent.mkdir(parents=True)
    paths.release_signers.write_bytes(signer.allowed.read_bytes())
    binary = paths.opt_dir / "venv/bin/vakr"
    binary.parent.mkdir(parents=True)
    binary.write_text(NEW_VAKR)
    binary.chmod(0o755)
    paths.config_dir.mkdir(parents=True)
    (paths.config_dir / "config.toml").write_text('[update]\nrepository = "o/r"\n')
    installer = FakeInstaller()
    monkeypatch.setattr(cmd_selfupdate, "INSTALLER", lambda _run: installer)
    monkeypatch.setattr(cmd_selfupdate, "SYSTEMCTL", "/bin/true")
    monkeypatch.setattr(cmd_tls, "OS_BUNDLES", (pki.ca.cert_path,))
    return paths, installer


def _serve_release(server: HttpsServer, signer: Signer, tmp_path: Path, semver: str) -> None:
    """Publish a signed release on the local HTTPS server.

    Args:
        server: Local HTTPS server.
        signer: Release signer.
        tmp_path: Temporary directory.
        semver: Release version (final releases only).

    Returns:
        None

    Raises:
        None
    """
    package = build_release(tmp_path / "rel" / semver, signer, semver, f"pkg {semver}".encode())
    assets = {}
    for path in (package, package.with_name("SHA256SUMS"), package.with_name("SHA256SUMS.sig")):
        route = f"/dl/v{semver}/{path.name}"
        server.routes[route] = Route(200, path.read_bytes())
        assets[path.name] = f"{server.base}{route}"
    body = as_json([release_json(f"v{semver}", assets, body="Release notes line")])
    server.routes["/repos/o/r/releases?per_page=30"] = Route(200, body)


def test_timer_invocation_updates_once(
    host: tuple[InstallPaths, FakeInstaller],
    https_server: HttpsServer,
    signer: Signer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """UPD-003/UPD-T05: the unit's ExecStart updates once, then no-ops.

    Args:
        host: Relocated installation and fake installer.
        https_server: Local "GitHub".
        signer: Release signer.
        tmp_path: Temporary directory.
        monkeypatch: Pytest monkeypatch.
        capsys: Pytest output capture.

    Returns:
        None

    Raises:
        None
    """
    paths, installer = host
    monkeypatch.setattr(cmd_selfupdate, "API_BASE", https_server.base)
    _serve_release(https_server, signer, tmp_path, "99.0.0")
    timer = ["self-update", "--yes", "--if-newer", "--unattended"]
    assert main(timer) == 0
    out = capsys.readouterr().out
    assert installer.installed == ["vakr_99.0.0_amd64.deb"]
    assert "update.hook.skipped: config upgrade" in out
    assert "vakr self-update: updated:" in out
    assert "  Release notes line" in out
    state = load_state(paths.state_dir / "update-state.json")
    assert (state.result, state.to_version) == ("success", "99.0.0")
    monkeypatch.setattr(cmd_selfupdate, "get_version", lambda: "99.0.0")
    assert main(timer) == 0
    assert "up-to-date" in capsys.readouterr().out
    assert installer.installed == ["vakr_99.0.0_amd64.deb"]
    assert cmd_selfupdate.check_update().summary.startswith("last update success")


def test_offline_file_install_without_trust_store(
    host: tuple[InstallPaths, FakeInstaller],
    signer: Signer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UPD-T04: ``--file`` works even when no HTTPS trust store is usable.

    Args:
        host: Relocated installation and fake installer.
        signer: Release signer.
        tmp_path: Temporary directory.
        monkeypatch: Pytest monkeypatch.

    Returns:
        None

    Raises:
        None
    """
    _, installer = host
    monkeypatch.setattr(cmd_tls, "OS_BUNDLES", ())
    package = build_release(tmp_path / "usb", signer, "98.0.0", b"pkg")
    sums, sig = package.with_name("SHA256SUMS"), package.with_name("SHA256SUMS.sig")
    argv = ["self-update", "--file", str(package), "--sums", str(sums), "--sig", str(sig)]
    assert main([*argv, "--yes"]) == 0
    assert installer.installed == [package.name]
    assert main(["self-update", "--file", str(package)]) == 2
    assert main(["self-update", "--check"]) == 1


def test_usage_and_environment_errors(
    host: tuple[InstallPaths, FakeInstaller],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Bad settings, missing root, unreachable API, and a held lock all fail clearly.

    Args:
        host: Relocated installation and fake installer.
        monkeypatch: Pytest monkeypatch.
        capsys: Pytest output capture.

    Returns:
        None

    Raises:
        None
    """
    paths, installer = host
    monkeypatch.setattr(cmd_selfupdate, "API_BASE", "https://127.0.0.1:1")
    assert main(["self-update", "--yes"]) == 1
    captured = capsys.readouterr()
    assert "[FAIL] tcp" in captured.out
    assert "cannot connect" in captured.err
    with exclusive_lock(paths.run_dir / "self-update.lock"):
        assert main(["self-update", "--yes", "--rollback"]) == 1
    assert "already running" in capsys.readouterr().err
    assert main(["self-update", "--rollback", "--yes"]) == 1
    assert "no previous version" in capsys.readouterr().out
    (paths.config_dir / "config.toml").write_text('[update]\nhold_version = "latest"\n')
    assert main(["self-update", "--check"]) == 2
    monkeypatch.delenv("VAKR_ROOT")
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    assert main(["self-update", "--yes"]) == 1
    assert "must run as root" in capsys.readouterr().err
    assert installer.installed == []


def test_check_update_health(host: tuple[InstallPaths, FakeInstaller]) -> None:
    """UPD-006: ``check health`` reports the last update result without network I/O.

    Args:
        host: Relocated installation.

    Returns:
        None

    Raises:
        None
    """
    paths, _ = host
    state_file = paths.state_dir / "update-state.json"
    assert cmd_selfupdate.check_update().summary == "never updated"
    save_state(state_file, UpdateState("failed", "t", "0.2.0", "0.3.0", "health check failed"))
    failed = cmd_selfupdate.check_update()
    assert failed.status is Status.WARNING
    assert "health check failed" in failed.summary
    save_state(state_file, UpdateState("rolled-back", "t", "0.3.0", "0.2.0", "ok"))
    assert cmd_selfupdate.check_update().status is Status.OK
    state_file.write_text(json.dumps({"result": "bogus"}))
    assert cmd_selfupdate.check_update().status is Status.WARNING


def test_doctor_network_names_untrusted_issuer(
    host: tuple[InstallPaths, FakeInstaller],
    https_server: HttpsServer,
    pki: Pki,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """EGRESS-005: ``doctor --network`` lists each step and the trust-ca hint.

    Args:
        host: Relocated installation.
        https_server: Server signed by the inspection CA.
        pki: Test certificates.
        monkeypatch: Pytest monkeypatch.
        capsys: Pytest output capture.

    Returns:
        None

    Raises:
        None
    """
    paths, _ = host
    monkeypatch.setattr(cmd_tls, "OS_BUNDLES", (pki.other_ca.cert_path,))
    monkeypatch.setattr(cmd_tls, "EGRESS_URLS", (f"{https_server.base}/",))
    assert main(["doctor", "--network"]) == 2
    out = capsys.readouterr().out
    assert "[OK      ] egress 127.0.0.1 dns:" in out
    assert "[CRITICAL] egress 127.0.0.1 tls:" in out
    assert "issued by 'CN=Example Inspection CA'" in out
    assert "vakr tls trust-ca FILE" in out
    (paths.config_dir / "config.toml").write_text("not = [valid")
    assert main(["doctor", "--network"]) == 2
    assert "egress config" in capsys.readouterr().out


def test_fake_installer_records_failures() -> None:
    """The installer stand-in reports failures like ``apt-get`` does.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    installer = FakeInstaller()
    installer.fail_for.add("x.deb")
    assert installer(Path("x.deb")) == RunResult(
        100, "", "E: Sub-process /usr/bin/dpkg returned an error code (1)"
    )
