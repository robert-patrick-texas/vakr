# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests for the step-by-step GitHub reachability test (FIELD-010, EGRESS-005).

The report must say which hop failed, and when TLS verification fails it
must name the issuer of the certificate the host was shown, which is how
development spots an enterprise TLS-inspection proxy. A loopback TLS server
signed by a throwaway "inspection CA" and a loopback CONNECT proxy stand in
for GitHub and the enterprise proxy. A second server presents a leaf
without an authority key identifier, which only the strict X.509 profile
rejects (EGRESS-T05).

Dependencies:
    pytest: Test runner.
    fieldkit_egress, fieldkit_tunnel, fieldkit_der: Modules under test.
    conftest: Throwaway PKI, TLS server, and proxy fixtures.

Safety:
    Nothing leaves 127.0.0.1; ``.invalid`` names never resolve (RFC 6761).

Example:
    $ uv run pytest tools/fieldkit/test_fieldkit_egress.py
"""

import ast
import socket
import ssl
import tomllib
from pathlib import Path
from typing import Any

import fieldkit_egress
import pytest
from fieldkit_der import certificate_names
from fieldkit_egress import _strict_profile, probe, tls_context
from fieldkit_testkit import Pki
from fieldkit_tunnel import bypasses_proxy, choose_proxy, redact_url


def options(**overrides: Any) -> dict[str, Any]:
    """Build probe options with test defaults.

    Args:
        **overrides: Option values to replace.

    Returns:
        dict[str, Any]: Options for ``probe``.

    Raises:
        None
    """
    return {
        "proxy": None,
        "ca_file": None,
        "timeout": 5.0,
        "agent": "t/1",
        "environ": {},
    } | overrides


def test_verified_get_reports_each_stage_and_release_tag(pki: Pki, tls_server: int) -> None:
    """With the CA trusted, DNS, connect, TLS, and HTTP all succeed.

    Args:
        pki: Session PKI.
        tls_server: Loopback TLS server port.

    Returns:
        None

    Raises:
        None
    """
    record = probe("GET", f"https://localhost:{tls_server}/r", options(ca_file=pki.ca))
    assert "error" not in record
    assert record["dns"]["addresses"]
    assert record["tls"]["verified"] is True
    assert "Fieldkit Test Inspection CA" in record["tls"]["issuer"]
    assert record["http"] == {"status": 200, "reason": "OK", "tag_name": "v9.9.9"}
    assert str(pki.ca) in record["trust"]


def test_untrusted_issuer_is_named(tls_server: int) -> None:
    """Without the CA, verification fails and the presented issuer is reported (FIELD-010).

    Args:
        tls_server: Loopback TLS server port.

    Returns:
        None

    Raises:
        None
    """
    record = probe("HEAD", f"https://localhost:{tls_server}/r", options())
    assert record["error"].startswith("tls: certificate verification failed")
    presented = record["tls"]["presented"]
    assert "CN=Fieldkit Test Inspection CA" in presented["issuer"]
    assert "CN=localhost" in presented["subject"]
    assert len(presented["sha256"]) == 64
    assert "http" not in record


def test_connect_proxy_with_credentials_is_used_and_redacted(
    pki: Pki, tls_server: int, connect_proxy: tuple[int, list[bytes]]
) -> None:
    """The proxy gets Basic credentials, but the record shows a redacted URL.

    Args:
        pki: Session PKI.
        tls_server: Loopback TLS server port.
        connect_proxy: Proxy port and the request heads it saw.

    Returns:
        None

    Raises:
        None
    """
    port, seen = connect_proxy
    proxy = f"http://proxyuser:proxy%20pass@127.0.0.1:{port}"
    record = probe("GET", f"https://localhost:{tls_server}/r", options(proxy=proxy, ca_file=pki.ca))
    assert "error" not in record, record
    assert record["proxy"] == f"http://[REDACTED]@127.0.0.1:{port}"
    assert record["proxy_connect"]["status"].startswith("HTTP/1.1 200")
    assert seen[0].startswith(f"CONNECT localhost:{tls_server} ".encode())
    assert "proxy pass" not in repr(record)


def test_proxy_refusal_and_bad_scheme_are_recorded(
    tls_server: int, connect_proxy: tuple[int, list[bytes]]
) -> None:
    """A 407 from the proxy and an unsupported proxy scheme are reported per stage.

    Args:
        tls_server: Loopback TLS server port.
        connect_proxy: Proxy port and the request heads it saw.

    Returns:
        None

    Raises:
        None
    """
    port, _ = connect_proxy
    url = f"https://localhost:{tls_server}/r"
    refused = probe("GET", url, options(proxy=f"http://127.0.0.1:{port}"))
    assert refused["error"].startswith("connect: OSError: proxy refused CONNECT")
    assert "407" in refused["error"]
    socks = probe("GET", url, options(proxy="socks5://127.0.0.1:1080"))
    assert "not supported" in socks["error"]


def test_dns_failure_is_recorded() -> None:
    """An unresolvable name fails at the connect stage, not with an exception.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    record = probe("GET", "https://release-host.invalid/x", options(timeout=2.0))
    assert record["error"].startswith("connect:")
    assert "dns" not in record


def test_unreadable_ca_file_is_recorded(tmp_path: Path) -> None:
    """A bad ``--ca-file`` is a recorded setup error.

    Args:
        tmp_path: Per-test directory.

    Returns:
        None

    Raises:
        None
    """
    record = probe("GET", "https://localhost:1/x", options(ca_file=tmp_path / "missing.pem"))
    assert record["error"].startswith("tls-setup:")


@pytest.mark.parametrize(
    ("host", "no_proxy", "bypass"),
    [
        ("api.github.com", "github.com", True),
        ("api.github.com", ".github.com", True),
        ("notgithub.com", "github.com", False),
        ("anything", "*", True),
        ("api.github.com", "", False),
    ],
)
def test_no_proxy_matching(host: str, no_proxy: str, bypass: bool) -> None:
    """``NO_PROXY`` suffix rules match curl and urllib.

    Args:
        host: Destination host.
        no_proxy: ``NO_PROXY`` value.
        bypass: Expected result.

    Returns:
        None

    Raises:
        None
    """
    assert bypasses_proxy(host, no_proxy) is bypass


def test_proxy_choice_and_redaction() -> None:
    """``--proxy`` wins, then ``HTTPS_PROXY`` unless ``NO_PROXY`` matches.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    env = {"HTTPS_PROXY": "http://p:3128", "NO_PROXY": "internal.example"}
    assert choose_proxy("http://x:1", "github.com", env) == "http://x:1"
    assert choose_proxy(None, "github.com", env) == "http://p:3128"
    assert choose_proxy(None, "git.internal.example", env) is None
    assert choose_proxy(None, "github.com", {}) is None
    assert redact_url("http://p:3128") == "http://p:3128"


def test_der_names_match_the_ssl_module(pki: Pki) -> None:
    """The DER parser agrees with the issuer and subject of a known certificate.

    Args:
        pki: Session PKI.

    Returns:
        None

    Raises:
        None
    """
    der = ssl.PEM_cert_to_DER_cert(pki.ca.read_text(encoding="ascii"))
    names = certificate_names(der)
    assert names["issuer"] == names["subject"] == "O=Fieldkit Tests, CN=Fieldkit Test Inspection CA"
    assert names["not_after"].endswith("Z")
    with pytest.raises(ValueError, match="DER"):
        certificate_names(b"\x04\x01x")


def test_strict_profile_failure_names_both_fallbacks(pki: Pki, tls_server_no_aki: int) -> None:
    """EGRESS-T05: a proxy leaf without AKI is reported as a strict-profile failure.

    Args:
        pki: Session PKI.
        tls_server_no_aki: Server presenting a leaf without an AKI.

    Returns:
        None

    Raises:
        None
    """
    url = f"https://localhost:{tls_server_no_aki}/r"
    record = probe("GET", url, options(ca_file=pki.ca))
    assert record["error"].startswith(
        "tls: certificate verification failed: Missing Authority Key Identifier"
    )
    assert "only the strict X.509 profile rejects it" in record["error"]
    assert "--no-strict-x509" in record["error"]
    assert "network.tls_strict_x509 = false" in record["error"]
    assert (record["strict_x509"], record["tls"]["verify_code"]) == (True, 85)
    assert record["tls"]["passes_without_strict"] is True
    assert "http" not in record
    relaxed = probe("GET", url, options(ca_file=pki.ca, strict_x509=False))
    assert "error" not in relaxed, relaxed
    assert (relaxed["strict_x509"], relaxed["http"]["status"]) == (False, 200)


def test_wrong_host_fails_in_both_modes(pki: Pki, tls_server: int, tls_server_no_aki: int) -> None:
    """EGRESS-T05: ``--no-strict-x509`` keeps verification and host name checks on.

    Args:
        pki: Session PKI.
        tls_server: Server with a compliant chain.
        tls_server_no_aki: Server whose leaf passes only with the profile relaxed.

    Returns:
        None

    Raises:
        None
    """
    for strict, port in ((True, tls_server), (False, tls_server_no_aki)):
        context, _ = tls_context(pki.ca, strict)
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
        assert bool(context.verify_flags & ssl.VERIFY_X509_STRICT) is strict
        with (
            socket.create_connection(("127.0.0.1", port), timeout=5) as sock,
            pytest.raises(ssl.SSLCertVerificationError, match="mismatch"),
        ):
            context.wrap_socket(sock, server_hostname="wrong.example")


def test_unconfirmed_strict_failure_keeps_the_reason() -> None:
    """EGRESS-T05: if the relaxed confirmation fails, the report says why and gives no hint.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    record: dict[str, Any] = {"error": "tls: certificate verification failed: x", "tls": {}}
    _strict_profile(record, "localhost", 1, None, options(timeout=2.0))
    assert record["tls"]["passes_without_strict"] is False
    assert "without it: confirmation failed" in record["error"]
    assert "--no-strict-x509" not in record["error"]


def test_strict_confirmation_sends_nothing() -> None:
    """EGRESS-004: the relaxed confirmation only handshakes and closes.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    tree = ast.parse(Path(fieldkit_egress.__file__).read_text(encoding="utf-8"))
    function = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_strict_profile"
    )
    called = {
        node.func.attr
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert called.isdisjoint({"send", "sendall", "write", "makefile", "request"})
    assert {"wrap_socket", "close"} <= called


def test_release_api_target_sees_pre_releases() -> None:
    """FIELD-010: the GET target lists releases, as self-update does.

    ``/releases/latest`` ignores pre-releases, so when every release is one
    (as until 2026-10-28) it answers 404 and the report reads like a failure
    on a healthy host.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    config = Path(fieldkit_egress.__file__).with_name("fieldkit.toml")
    targets = tomllib.loads(config.read_text(encoding="utf-8"))["fieldkit"]["egress_get"]
    assert targets
    for url in targets:
        assert not url.endswith("/releases/latest"), url
        assert "/releases?" in url, url
