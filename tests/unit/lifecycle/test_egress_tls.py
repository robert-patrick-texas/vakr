"""EGRESS-T01: TLS inspection CA, step-by-step diagnosis, and the shared HTTPS client.

A local HTTPS server presents a certificate signed by a throwaway
"enterprise inspection" CA, standing in for a TLS-intercepting proxy. The
update check fails with a diagnosis that names that issuer, and succeeds
after ``vakr tls trust-ca`` installs the CA (EGRESS-002, EGRESS-003,
EGRESS-005). A local CONNECT proxy covers EGRESS-001 tunnelling.

Dependencies:
    tests.unit.lifecycle.helpers: PKI, HTTPS server, CONNECT proxy.

Example:
    $ uv run pytest tests/unit/lifecycle/test_egress_tls.py
"""

from pathlib import Path

import pytest

from tests.unit.lifecycle.helpers import (
    HttpsServer,
    Pki,
    Proxy,
    Route,
    as_json,
    make_cert,
    release_json,
    start_https_server,
)
from vakr.cli import cmd_selfupdate, cmd_tls
from vakr.cli.main import main
from vakr.config.loader import EffectiveConfig
from vakr.lifecycle.egress.client import EgressSettings, HttpsClient
from vakr.lifecycle.egress.diagnose import diagnose, render
from vakr.lifecycle.egress.errors import CertificateVerifyError, EgressError
from vakr.lifecycle.update.github import fetch_releases
from vakr.paths import InstallPaths

PROXY_VARS = ("HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy")


@pytest.fixture
def root(tmp_path: Path, pki: Pki, monkeypatch: pytest.MonkeyPatch) -> InstallPaths:
    """Relocate vakr under ``tmp_path`` with an OS bundle that lacks the test CA.

    Args:
        tmp_path: Temporary directory.
        pki: Test certificates.
        monkeypatch: Pytest monkeypatch.

    Returns:
        InstallPaths: The relocated layout.

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path / "root"))
    for name in PROXY_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cmd_tls, "OS_BUNDLES", (pki.other_ca.cert_path,))
    return InstallPaths.from_environment()


def _settings(paths: InstallPaths, config: dict | None = None) -> EgressSettings:
    """Build vakr's egress settings from a config dict and an empty environment.

    Args:
        paths: Installation paths.
        config: Configuration tables.

    Returns:
        EgressSettings: Settings as vakr builds them.

    Raises:
        None
    """
    return cmd_tls.build_egress_settings(paths, EffectiveConfig(config or {}), {})


def test_egress_t01_issuer_named_then_trusted(
    root: InstallPaths, pki: Pki, https_server: HttpsServer, capsys: pytest.CaptureFixture[str]
) -> None:
    """EGRESS-T01: verification fails naming the issuer; succeeds after trust-ca.

    Args:
        root: Relocated installation.
        pki: Test certificates.
        https_server: Server presenting the inspection-CA-signed certificate.
        capsys: Pytest output capture.

    Returns:
        None

    Raises:
        None
    """
    https_server.routes["/repos/o/r/releases?per_page=30"] = Route(200, as_json([]))
    client = HttpsClient(_settings(root))
    with pytest.raises(CertificateVerifyError, match="unable to get local issuer"):
        fetch_releases(client, "o/r", https_server.base)
    diagnosis = diagnose(f"{https_server.base}/", _settings(root), cmd_tls.TRUST_HINT)
    assert [s.name for s in diagnosis.steps] == ["dns", "tcp", "tls"]
    assert not diagnosis.ok
    assert "issued by 'CN=Example Inspection CA'" in diagnosis.steps[-1].detail
    assert "subject 'CN=server'" in diagnosis.steps[-1].detail
    assert diagnosis.hint is not None
    assert "vakr tls trust-ca" in "\n".join(render(diagnosis))

    assert main(["tls", "trust-ca", str(pki.ca.cert_path), "--yes", "--name", "corp"]) == 0
    installed = root.ca_dir / "corp.pem"
    assert installed.stat().st_mode & 0o777 == 0o644
    assert "SHA-256" in capsys.readouterr().out
    assert fetch_releases(HttpsClient(_settings(root)), "o/r", https_server.base) == []
    healthy = diagnose(f"{https_server.base}/", _settings(root))
    assert healthy.ok
    assert [s.name for s in healthy.steps] == ["dns", "tcp", "tls", "http"]
    assert "Example Inspection CA" in healthy.steps[2].detail


def test_egress_t01_update_check_cli(
    root: InstallPaths,
    pki: Pki,
    https_server: HttpsServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """EGRESS-T01/EGRESS-005: ``self-update --check`` diagnoses, then works after trust-ca.

    Args:
        root: Relocated installation.
        pki: Test certificates.
        https_server: Local "GitHub API".
        monkeypatch: Pytest monkeypatch.
        capsys: Pytest output capture.

    Returns:
        None

    Raises:
        None
    """
    root.config_dir.mkdir(parents=True)
    (root.config_dir / "config.toml").write_text('[update]\nrepository = "o/r"\n')
    monkeypatch.setattr(cmd_selfupdate, "API_BASE", https_server.base)
    releases = [release_json("v99.0.0", {}, body="Big release\nwith notes")]
    https_server.routes["/repos/o/r/releases?per_page=30"] = Route(200, as_json(releases))
    assert main(["self-update", "--check"]) == 1
    out = capsys.readouterr()
    assert "issued by 'CN=Example Inspection CA'" in out.out
    assert "vakr tls trust-ca" in out.out
    assert "not reachable" in out.err
    assert main(["tls", "trust-ca", str(pki.ca.cert_path), "--yes"]) == 0
    capsys.readouterr()
    assert main(["self-update", "--check"]) == 0
    out = capsys.readouterr().out
    assert "[ok  ] http:" in out
    assert "available: 99.0.0 is available" in out
    assert "  Big release" in out


def test_connect_proxy_tunnels_and_is_diagnosed(
    root: InstallPaths, pki: Pki, https_server: HttpsServer, connect_proxy: Proxy
) -> None:
    """EGRESS-001: configured proxies tunnel HTTPS with CONNECT, also for the peek.

    Args:
        root: Relocated installation.
        pki: Test certificates.
        https_server: Destination server.
        connect_proxy: Local CONNECT proxy.

    Returns:
        None

    Raises:
        None
    """
    config = {"network": {"https_proxy": connect_proxy.url}}
    untrusted = diagnose(f"{https_server.base}/", _settings(root, config))
    assert [s.name for s in untrusted.steps] == ["dns", "tcp", "proxy", "tls"]
    assert "Example Inspection CA" in untrusted.steps[-1].detail
    assert connect_proxy.targets == [f"127.0.0.1:{https_server.port}"] * 2
    assert main(["tls", "trust-ca", str(pki.ca.cert_path), "--yes"]) == 0
    https_server.routes["/x"] = Route(200, b"payload")
    response = HttpsClient(_settings(root, config)).get(f"{https_server.base}/x")
    assert (response.status, response.body) == (200, b"payload")
    assert diagnose(f"{https_server.base}/", _settings(root, config)).ok
    bypass = {"network": {"https_proxy": connect_proxy.url, "no_proxy": ["127.0.0.1"]}}
    connect_proxy.targets.clear()
    assert HttpsClient(_settings(root, bypass)).get(f"{https_server.base}/x").status == 200
    assert connect_proxy.targets == []


def test_refusing_proxy_is_reported(
    root: InstallPaths, https_server: HttpsServer, connect_proxy: Proxy
) -> None:
    """A proxy that refuses CONNECT fails at the ``proxy`` step with its status.

    Args:
        root: Relocated installation.
        https_server: Destination server.
        connect_proxy: Local CONNECT proxy, set to refuse.

    Returns:
        None

    Raises:
        None
    """
    connect_proxy.refuse = True
    settings = _settings(root, {"network": {"https_proxy": connect_proxy.url}})
    diagnosis = diagnose(f"{https_server.base}/", settings)
    assert diagnosis.steps[-1].name == "proxy"
    assert "403" in diagnosis.steps[-1].detail
    with pytest.raises(EgressError, match="proxy refused CONNECT") as exc:
        HttpsClient(settings).get(f"{https_server.base}/")
    assert exc.value.step == "proxy"


def test_hostname_mismatch_gets_no_trust_hint(root: InstallPaths, pki: Pki) -> None:
    """A trusted CA with the wrong host name is diagnosed without suggesting trust-ca.

    Args:
        root: Relocated installation.
        pki: Test certificates.

    Returns:
        None

    Raises:
        None
    """
    wrong = make_cert(pki.directory, "wrong", ca=False, issuer=pki.ca, sans=("wrong.example",))
    server, stop = start_https_server(wrong)
    try:
        assert main(["tls", "trust-ca", str(pki.ca.cert_path), "--yes"]) == 0
        diagnosis = diagnose(f"{server.base}/", _settings(root))
    finally:
        stop()
    assert "mismatch" in diagnosis.steps[-1].detail
    assert "subject 'CN=wrong'" in diagnosis.steps[-1].detail
    assert diagnosis.hint is None


def test_client_redirects_limits_and_downloads(
    root: InstallPaths, pki: Pki, https_server: HttpsServer, tmp_path: Path
) -> None:
    """Redirects are followed only to HTTPS and bounded; bodies and files are size-limited.

    Args:
        root: Relocated installation.
        pki: Test certificates.
        https_server: Local server.
        tmp_path: Temporary directory.

    Returns:
        None

    Raises:
        None
    """
    assert main(["tls", "trust-ca", str(pki.ca.cert_path), "--yes"]) == 0
    base, routes = https_server.base, https_server.routes
    routes["/asset"] = Route(302, headers={"Location": f"{base}/blob?sig=1"})
    routes["/blob?sig=1"] = Route(200, b"x" * 100)
    routes["/plain"] = Route(301, headers={"Location": "http://127.0.0.1/insecure"})
    routes["/loop"] = Route(307, headers={"Location": "/loop"})
    routes["/json"] = Route(200, b"not json")
    settings = _settings(root)
    client = HttpsClient(settings)
    dest = tmp_path / "file.bin"
    assert client.download(f"{base}/asset", dest, 1000) == 100
    assert dest.read_bytes() == b"x" * 100
    assert https_server.requests[-2:] == ["/asset", "/blob?sig=1"]
    with pytest.raises(EgressError, match="exceeds 50 bytes"):
        client.download(f"{base}/asset", tmp_path / "big.bin", 50)
    assert not (tmp_path / "big.bin").exists()
    assert not (tmp_path / "big.bin.part").exists()
    with pytest.raises(EgressError, match="HTTP 404"):
        client.download(f"{base}/missing", tmp_path / "missing.bin", 50)
    with pytest.raises(EgressError, match="refusing non-HTTPS"):
        client.get(f"{base}/plain")
    with pytest.raises(EgressError, match="too many redirects"):
        client.get(f"{base}/loop")
    with pytest.raises(EgressError, match="not valid JSON"):
        client.get(f"{base}/json").json()
    small = HttpsClient(EgressSettings(settings.context, settings.proxies, max_body_bytes=10))
    with pytest.raises(EgressError, match="exceeds 10 bytes"):
        small.get(f"{base}/blob?sig=1")


def test_diagnose_reports_dns_tcp_and_url_failures(root: InstallPaths) -> None:
    """Each early step reports its own failure.

    Args:
        root: Relocated installation.

    Returns:
        None

    Raises:
        None
    """
    settings = _settings(root)
    assert diagnose("http://example.invalid/", settings).steps[0].name == "config"
    dns = diagnose("https://name-does-not-exist.invalid/", settings)
    assert (dns.steps[-1].name, dns.ok) == ("dns", False)
    tcp = diagnose("https://127.0.0.1:1/", settings)
    assert tcp.steps[-1].name == "tcp"
    assert "cannot connect" in tcp.steps[-1].detail
    assert "FAILED" in render(tcp)[0]
