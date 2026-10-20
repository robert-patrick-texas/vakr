# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests for the step-by-step GitHub reachability test (FIELD-010, EGRESS-005).

The report must say which hop failed, and when TLS verification fails it
must name the issuer of the certificate the host was shown, which is how
development spots an enterprise TLS-inspection proxy. A loopback TLS server
signed by a throwaway "inspection CA" and a loopback CONNECT proxy stand in
for GitHub and the enterprise proxy.

Dependencies:
    pytest: Test runner.
    fieldkit_egress, fieldkit_tunnel, fieldkit_der: Modules under test.
    conftest: Throwaway PKI, TLS server, and proxy fixtures.

Safety:
    Nothing leaves 127.0.0.1; ``.invalid`` names never resolve (RFC 6761).

Example:
    $ uv run pytest tools/fieldkit/test_fieldkit_egress.py
"""

import ssl
from pathlib import Path
from typing import Any

import pytest
from fieldkit_der import certificate_names
from fieldkit_egress import probe
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
