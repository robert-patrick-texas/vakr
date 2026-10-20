"""EGRESS-T02 and EGRESS-006: ``vakr tls`` validation, management, and CA expiry health.

Dependencies:
    tests.unit.lifecycle.helpers: Certificate generation.

Example:
    $ uv run pytest tests/unit/lifecycle/test_tls_cli.py
"""

import io
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization

from tests.unit.lifecycle.helpers import Pki, make_cert
from vakr.cli import cmd_tls
from vakr.cli.main import main
from vakr.health.model import Status
from vakr.lifecycle.egress import castore, truststore
from vakr.lifecycle.egress.errors import EgressError
from vakr.lifecycle.update import console
from vakr.paths import InstallPaths


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> InstallPaths:
    """Relocate vakr under ``tmp_path`` with a fake OS trust store.

    Args:
        tmp_path: Temporary directory.
        monkeypatch: Pytest monkeypatch.

    Returns:
        InstallPaths: The relocated layout.

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path / "root"))
    anchors = tmp_path / "anchors"
    anchors.mkdir()
    monkeypatch.setattr(cmd_tls, "SYSTEM_STORES", (castore.SystemStore(anchors, ("/bin/true",)),))
    return InstallPaths.from_environment()


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        ("leaf", "not a CA certificate"),
        ("expired", "expired on"),
        ("future", "not valid until"),
        ("der", "not a PEM certificate"),
        ("text", "not a PEM certificate"),
        ("broken", "could not be parsed"),
        ("two", "holds 2 certificates"),
        ("missing", "No such file"),
    ],
)
def test_egress_t02_rejects_bad_certificates(
    root: InstallPaths,
    pki: Pki,
    kind: str,
    reason: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """EGRESS-T02: leaf, expired, and non-PEM certificates are refused; nothing installed.

    Args:
        root: Relocated installation.
        pki: Test certificates.
        kind: Which bad input to offer.
        reason: Text the refusal must contain.
        capsys: Pytest output capture.

    Returns:
        None

    Raises:
        None
    """
    candidate = pki.directory / f"{kind}.input"
    pem = pki.ca.pem
    if kind == "leaf":
        pem = pki.server.pem
    elif kind == "expired":
        pem = make_cert(pki.directory, "Old CA", ca=True, days=(-400, -1)).pem
    elif kind == "future":
        pem = make_cert(pki.directory, "Future CA", ca=True, days=(5, 400)).pem
    elif kind == "der":
        pem = pki.ca.cert.public_bytes(serialization.Encoding.DER)
    elif kind == "text":
        pem = b"hello, not a certificate\n"
    elif kind == "broken":
        pem = b"-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n"
    elif kind == "two":
        pem = pki.ca.pem + pki.other_ca.pem
    if kind != "missing":
        candidate.write_bytes(pem)
    assert main(["tls", "trust-ca", str(candidate), "--yes", "--system"]) == 1
    err = capsys.readouterr().err
    assert reason in err
    assert "nothing was installed" in err
    assert not root.ca_dir.exists() or not any(root.ca_dir.iterdir())


def test_trust_list_remove_with_system_store(
    root: InstallPaths, pki: Pki, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """EGRESS-003: install (also into the OS store), list, refuse duplicates, remove.

    Args:
        root: Relocated installation.
        pki: Test certificates.
        tmp_path: Temporary directory (fake OS anchors live here).
        capsys: Pytest output capture.

    Returns:
        None

    Raises:
        None
    """
    assert main(["tls", "trust-ca", str(pki.ca.cert_path), "--yes", "--system"]) == 0
    out = capsys.readouterr().out
    assert "subject:  CN=Example Inspection CA" in out
    assert "trusted CA Example-Inspection-CA" in out
    anchor = tmp_path / "anchors/Example-Inspection-CA.crt"
    assert anchor.read_bytes() == pki.ca.pem
    assert anchor.stat().st_mode & 0o777 == 0o644
    assert main(["tls", "trust-ca", str(pki.ca.cert_path), "--yes"]) == 1
    assert "already installed" in capsys.readouterr().err
    (root.ca_dir / "junk.pem").write_text("garbage")
    assert main(["tls", "list"]) == 0
    listing = capsys.readouterr().out
    assert "Example-Inspection-CA: ok, expires" in listing
    assert "junk: UNREADABLE" in listing
    assert main(["tls", "remove", "Example-Inspection-CA"]) == 0
    assert not anchor.exists()
    assert "removed CA Example-Inspection-CA from the OS trust store" in capsys.readouterr().out
    assert main(["tls", "remove", "Example-Inspection-CA"]) == 1
    assert main(["tls", "remove", "../etc"]) == 1
    assert "invalid name" in capsys.readouterr().err


def test_list_empty_and_bare_tls_is_usage_error(
    root: InstallPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    """``tls list`` on a fresh host says so; ``vakr tls`` alone is a usage error.

    Args:
        root: Relocated installation.
        capsys: Pytest output capture.

    Returns:
        None

    Raises:
        None
    """
    assert main(["tls", "list"]) == 0
    assert f"no extra trusted CAs in {root.ca_dir}" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        main(["tls"])
    assert exc.value.code == 2


def test_system_refresh_failure_leaves_os_store_unchanged(
    root: InstallPaths, pki: Pki, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing ``update-ca-certificates`` removes the anchor again and reports it.

    Args:
        root: Relocated installation.
        pki: Test certificates.
        tmp_path: Temporary directory.
        monkeypatch: Pytest monkeypatch.

    Returns:
        None

    Raises:
        None
    """
    anchors = tmp_path / "anchors"
    monkeypatch.setattr(cmd_tls, "SYSTEM_STORES", (castore.SystemStore(anchors, ("/bin/false",)),))
    assert main(["tls", "trust-ca", str(pki.ca.cert_path), "--yes", "--system"]) == 1
    assert list(anchors.iterdir()) == []
    assert (root.ca_dir / "Example-Inspection-CA.pem").is_file()
    monkeypatch.setattr(cmd_tls, "SYSTEM_STORES", ())
    assert main(["tls", "trust-ca", str(pki.other_ca.cert_path), "--yes", "--system"]) == 1


def test_confirmation_required_without_yes(
    root: InstallPaths, pki: Pki, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without ``--yes``, only an explicit yes on a terminal installs the CA.

    Args:
        root: Relocated installation.
        pki: Test certificates.
        monkeypatch: Pytest monkeypatch.

    Returns:
        None

    Raises:
        None
    """
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))
    assert main(["tls", "trust-ca", str(pki.ca.cert_path)]) == 1
    assert not root.ca_dir.exists()

    class Tty(io.StringIO):
        """A fake terminal."""

        def isatty(self) -> bool:
            """Claim to be a terminal.

            Args:
                None

            Returns:
                bool: Always ``True``.

            Raises:
                None
            """
            return True

    assert console.confirmer(False, Tty("no\n"))("Trust?") is False
    monkeypatch.setattr("sys.stdin", Tty("yes\n"))
    assert main(["tls", "trust-ca", str(pki.ca.cert_path)]) == 0


def test_egress_006_ca_expiry_health(root: InstallPaths, pki: Pki) -> None:
    """EGRESS-006: OK, WARNING within 30 days or unreadable, CRITICAL once expired.

    Args:
        root: Relocated installation.
        pki: Test certificates.

    Returns:
        None

    Raises:
        None
    """
    assert cmd_tls.check_trusted_cas().summary == "no extra trusted CAs"
    root.ca_dir.mkdir(parents=True)
    (root.ca_dir / "good.pem").write_bytes(pki.ca.pem)
    ok = cmd_tls.check_trusted_cas()
    assert ok.status is Status.OK
    assert ok.perfdata[0].label == "ca_min_days_left"
    soon = make_cert(pki.directory, "Soon CA", ca=True, days=(-10, 10)).pem
    (root.ca_dir / "soon.pem").write_bytes(soon)
    warning = cmd_tls.check_trusted_cas()
    assert warning.status is Status.WARNING
    assert "soon" in warning.summary
    (root.ca_dir / "junk.crt").write_text("x")
    assert "unreadable: junk" in cmd_tls.check_trusted_cas().summary
    old = make_cert(pki.directory, "Gone CA", ca=True, days=(-400, -1)).pem
    (root.ca_dir / "gone.pem").write_bytes(old)
    critical = cmd_tls.check_trusted_cas()
    assert critical.status is Status.CRITICAL
    assert "expired CA: gone" in critical.summary


def test_trust_store_errors_name_the_file(tmp_path: Path, pki: Pki) -> None:
    """EGRESS-002: a broken CA file or no anchors at all is a clear configuration error.

    Args:
        tmp_path: Temporary directory.
        pki: Test certificates.

    Returns:
        None

    Raises:
        None
    """
    ca_dir = tmp_path / "ca.d"
    with pytest.raises(EgressError, match="no trusted CA certificates"):
        truststore.build_context(ca_dir, os_bundles=())
    ca_dir.mkdir()
    (ca_dir / "bad.pem").write_text("garbage")
    with pytest.raises(EgressError, match=r"bad\.pem"):
        truststore.build_context(ca_dir, os_bundles=(pki.other_ca.cert_path,))
    (ca_dir / "bad.pem").unlink()
    (ca_dir / "ok.crt").write_bytes(pki.ca.pem)
    context = truststore.build_context(
        ca_dir, os_bundles=(tmp_path / "absent", pki.other_ca.cert_path)
    )
    assert len(context.get_ca_certs()) == 2
    sources = truststore.describe_sources(ca_dir, (pki.other_ca.cert_path,))
    assert sources.endswith("+ 1 extra CA")
    assert truststore.describe_sources(None, ()) == "no OS bundle + 0 extra CAs"
    assert castore.default_name(Path("@@@.pem")) == "ca"
