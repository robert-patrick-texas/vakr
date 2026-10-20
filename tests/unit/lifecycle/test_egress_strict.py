"""EGRESS-T05: strict X.509 profile for enterprise TLS-inspection CAs (EGRESS-003/005/007).

Python 3.13+ verifies TLS with OpenSSL's strict X.509 profile. These tests
generate a CA that lacks ``keyUsage`` (and a subject key identifier), a
compliant CA, and a server leaf without an authority key identifier issued
by the compliant CA. By default the non-compliant CA is refused with every
reason and the ``network.tls_strict_x509 = false`` fallback named. With the
fallback set, it installs with a warning, a local TLS handshake succeeds,
and health reports WARNING. A wrong host name fails in both modes, and the
diagnosis names a strict-profile failure without ever relaxing a request.

Dependencies:
    tests.unit.lifecycle.helpers: Certificate generation and HTTPS server.

Example:
    $ uv run pytest tests/unit/lifecycle/test_egress_strict.py
"""

import shutil
import ssl
import subprocess
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from cryptography import x509

from tests.unit.lifecycle.helpers import (
    Cert,
    HttpsServer,
    Pki,
    Route,
    make_cert,
    start_https_server,
)
from vakr.cli import cmd_selfupdate, cmd_tls, tls_strict
from vakr.cli.main import main
from vakr.config.loader import load_effective_config
from vakr.health import lifecycle_checks
from vakr.health.lifecycle_checks import check_tls_policy
from vakr.health.model import Status
from vakr.health.registry import run_checks
from vakr.lifecycle.egress import castore, certinfo, strict, truststore
from vakr.lifecycle.egress.client import EgressSettings, HttpsClient
from vakr.lifecycle.egress.diagnose import diagnose, render
from vakr.lifecycle.egress.errors import CertificateVerifyError
from vakr.paths import InstallPaths

PROXY_VARS = ("HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy")
SERVER_NAMES = ("127.0.0.1", "localhost")
NONCOMPLIANT = frozenset({"keyUsage", "subjectKeyIdentifier"})
OPENSSL = shutil.which("openssl")


@pytest.fixture
def root(tmp_path: Path, pki: Pki, monkeypatch: pytest.MonkeyPatch) -> InstallPaths:
    """Relocate vakr under ``tmp_path`` with an OS bundle that lacks the test CAs.

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


@contextmanager
def serving(cert: Cert) -> Generator[HttpsServer]:
    """Run a local HTTPS server presenting ``cert`` for the ``with`` block.

    Args:
        cert: Server certificate and key.

    Returns:
        Generator[HttpsServer]: The running server; stopped afterwards.

    Raises:
        None
    """
    server, stop = start_https_server(cert)
    server.routes["/x"] = Route(200, b"payload")
    try:
        yield server
    finally:
        stop()


def relax(paths: InstallPaths, relaxed: bool = True) -> None:
    """Write (or clear) ``network.tls_strict_x509 = false`` in ``config.toml``.

    Args:
        paths: Installation paths.
        relaxed: ``True`` to relax the strict profile, ``False`` for defaults.

    Returns:
        None

    Raises:
        None
    """
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    body = "[network]\ntls_strict_x509 = false\n" if relaxed else ""
    (paths.config_dir / "config.toml").write_text(body)


def settings(paths: InstallPaths) -> EgressSettings:
    """Build egress settings exactly as vakr does, from the real config loader.

    Args:
        paths: Installation paths.

    Returns:
        EgressSettings: Settings with an empty proxy environment.

    Raises:
        None
    """
    config = load_effective_config(paths.config_dir, strict=False)
    return cmd_tls.build_egress_settings(paths, config, {})


@pytest.mark.parametrize(
    ("omit", "intermediate", "expected"),
    [
        (frozenset(), False, []),
        (frozenset({"keyUsage"}), False, ["keyUsage extension"]),
        (frozenset({"keyCertSign"}), False, ["keyCertSign in the keyUsage"]),
        (frozenset({"critical"}), False, ["basicConstraints marked critical"]),
        (frozenset({"subjectKeyIdentifier"}), False, ["subject key identifier"]),
        (frozenset({"authorityKeyIdentifier"}), True, ["authority key identifier"]),
        (frozenset(), True, []),
        (
            frozenset({"keyUsage", "critical", "subjectKeyIdentifier"}),
            False,
            ["basicConstraints marked critical", "keyUsage extension", "subject key identifier"],
        ),
    ],
)
def test_strict_profile_gaps_name_each_missing_item(
    pki: Pki, omit: frozenset[str], intermediate: bool, expected: list[str]
) -> None:
    """EGRESS-003: each strict-profile rule reports its own human-readable item.

    Args:
        pki: Test certificates (``pki.ca`` issues the intermediates).
        omit: Items left out of the generated CA.
        intermediate: Issue the CA from ``pki.ca`` instead of self-signing it.
        expected: Substrings of the expected gaps, in order.

    Returns:
        None

    Raises:
        None
    """
    issuer = pki.ca if intermediate else None
    ca = make_cert(pki.directory, "Case CA", ca=True, issuer=issuer, omit=omit)
    gaps = certinfo.strict_profile_gaps(ca.cert)
    assert len(gaps) == len(expected)
    for gap, phrase in zip(gaps, expected, strict=True):
        assert phrase in gap
    assert certinfo.is_self_signed(ca.cert) is not intermediate


def openssl_strict_ok(cafile: Path, cert: Path, strict_x509: bool = True) -> bool:
    """Run ``openssl verify [-x509_strict]`` and report whether it accepted ``cert``.

    Args:
        cafile: Trusted certificates (``-CAfile``).
        cert: Certificate to verify.
        strict_x509: Pass ``-x509_strict``.

    Returns:
        bool: ``True`` when openssl exits 0.

    Raises:
        None
    """
    flags = ["-x509_strict"] if strict_x509 else []
    argv = [str(OPENSSL), "verify", *flags, "-CAfile", str(cafile), str(cert)]
    return subprocess.run(argv, capture_output=True, check=False).returncode == 0  # noqa: S603


@pytest.mark.skipif(OPENSSL is None, reason="openssl is not installed")
@pytest.mark.parametrize(
    ("omit", "intermediate"),
    [
        (frozenset(), False),
        (frozenset({"keyUsage"}), False),
        (frozenset({"keyCertSign"}), False),
        (frozenset({"critical"}), False),
        (frozenset({"subjectKeyIdentifier"}), False),
        (frozenset({"authorityKeyIdentifier"}), True),
        (frozenset(), True),
    ],
)
def test_rules_agree_with_openssl_x509_strict(
    pki: Pki, tmp_path: Path, omit: frozenset[str], intermediate: bool
) -> None:
    """EGRESS-003: the portable rules match ``openssl verify -x509_strict`` on a real chain.

    Where they disagree: ``openssl verify -x509_strict -CAfile ca.pem ca.pem``
    checks a one-certificate chain, and OpenSSL 3 applies the strict rules
    only to chains of two or more, so that form accepts every self-signed
    non-compliant CA here; for a CA that is not self-signed it fails with
    "unable to get local issuer certificate", which is not a strict verdict.
    The chain form below (a leaf issued by the CA, as in a TLS handshake)
    agrees with ``strict_profile_gaps`` on every case. A ``keyUsage``
    without ``keyCertSign`` fails openssl with or without ``-x509_strict``,
    so ``validate_ca`` refuses it in both modes; every other gap passes
    once the strict profile is off.

    Args:
        pki: Test certificates (``pki.ca`` issues the intermediates).
        tmp_path: Temporary directory.
        omit: Items left out of the generated CA.
        intermediate: Issue the CA from ``pki.ca`` instead of self-signing it.

    Returns:
        None

    Raises:
        None
    """
    issuer = pki.ca if intermediate else None
    ca = make_cert(tmp_path, "Case CA", ca=True, issuer=issuer, omit=omit)
    leaf = make_cert(tmp_path, "case-leaf", ca=False, issuer=ca, sans=("localhost",))
    bundle = tmp_path / "bundle.pem"
    bundle.write_bytes(ca.pem + (pki.ca.pem if intermediate else b""))
    compliant = certinfo.strict_profile_gaps(ca.cert) == []
    assert openssl_strict_ok(bundle, leaf.cert_path) is compliant
    assert openssl_strict_ok(ca.cert_path, ca.cert_path) is not intermediate
    usable_relaxed = "keyCertSign" not in omit
    assert openssl_strict_ok(bundle, leaf.cert_path, strict_x509=False) is usable_relaxed


def test_self_signed_needs_matching_name_and_own_signature(pki: Pki) -> None:
    """EGRESS-003: only a CA that signed itself is exempt from the AKI rule.

    Args:
        pki: Test certificates.

    Returns:
        None

    Raises:
        None
    """
    namesake_issuer = make_cert(pki.directory, "Same Name", ca=True)
    namesake = make_cert(
        pki.directory,
        "Same Name",
        ca=True,
        issuer=namesake_issuer,
        omit=frozenset({"authorityKeyIdentifier"}),
    )
    assert namesake.cert.issuer == namesake.cert.subject
    assert not certinfo.is_self_signed(namesake.cert)
    assert "authority key identifier" in certinfo.strict_profile_gaps(namesake.cert)[0]
    assert "CA:TRUE" in certinfo.strict_profile_gaps(pki.server.cert)[0]


def test_non_compliant_ca_is_refused_by_default(
    root: InstallPaths, pki: Pki, capsys: pytest.CaptureFixture[str]
) -> None:
    """EGRESS-T05: refused with every reason, a reissue request, and EGRESS-007 named.

    Args:
        root: Relocated installation.
        pki: Test certificates.
        capsys: Pytest output capture.

    Returns:
        None

    Raises:
        None
    """
    ca = make_cert(pki.directory, "Lax Inspection CA", ca=True, omit=NONCOMPLIANT)
    assert main(["tls", "trust-ca", str(ca.cert_path), "--yes"]) == 1
    out, err = capsys.readouterr()
    assert "keyUsage extension (with keyCertSign)" in err
    assert "subject key identifier" in err
    assert "reissue" in err
    assert "network.tls_strict_x509 = false" in err
    assert "EGRESS-007" in err
    assert "nothing was installed" in err
    assert "installed" not in out
    assert not root.ca_dir.exists() or not any(root.ca_dir.iterdir())
    with pytest.raises(certinfo.CertificateRejectedError, match=r"strict X\.509 profile"):
        castore.install(ca.pem, "lax", root.ca_dir, datetime.now(UTC))


def test_relaxed_install_warns_connects_and_health_warns(
    root: InstallPaths, pki: Pki, capsys: pytest.CaptureFixture[str]
) -> None:
    """EGRESS-T05: with the fallback, install warns, TLS works, health is WARNING.

    Re-enabling the strict profile afterwards makes the same handshake fail,
    and the diagnosis names the strict profile rather than the issuer.

    Args:
        root: Relocated installation.
        pki: Test certificates.
        capsys: Pytest output capture.

    Returns:
        None

    Raises:
        None
    """
    ca = make_cert(pki.directory, "Lax Inspection CA", ca=True, omit=NONCOMPLIANT)
    leaf = make_cert(pki.directory, "lax-leaf", ca=False, issuer=ca, sans=SERVER_NAMES)
    assert check_tls_policy().status is Status.OK
    relax(root)
    assert main(["tls", "trust-ca", str(ca.cert_path), "--yes", "--name", "lax"]) == 0
    out, err = capsys.readouterr()
    assert "WARNING: this CA does not meet the strict X.509 profile" in err
    assert "keyUsage extension (with keyCertSign)" in err
    assert "network.tls_strict_x509 = false" in err
    assert f"installed {root.ca_dir / 'lax.pem'}" in out
    relaxed = settings(root)
    assert relaxed.strict_x509 is False
    with serving(leaf) as server:
        assert HttpsClient(relaxed).get(f"{server.base}/x").body == b"payload"
        assert diagnose(f"{server.base}/", relaxed).ok
        relax(root, relaxed=False)
        with pytest.raises(CertificateVerifyError) as exc:
            HttpsClient(settings(root)).get(f"{server.base}/x")
        diagnosis = diagnose(f"{server.base}/", settings(root), strict_hint=cmd_tls.STRICT_HINT)
    assert exc.value.code == 92
    assert "key usage" in exc.value.reason
    assert diagnosis.strict_profile
    assert diagnosis.hint == cmd_tls.STRICT_HINT
    relax(root)
    health = check_tls_policy()
    assert health.status is Status.WARNING
    assert "strict X.509 checks relaxed for outbound HTTPS" in health.summary
    assert Status.WARNING in {r.status for r in run_checks() if r.name == "tls_policy"}
    assert main(["check", "health"]) == 1
    assert "tls_policy: strict X.509 checks relaxed" in capsys.readouterr().out


def test_wrong_host_name_fails_in_both_modes(root: InstallPaths, pki: Pki) -> None:
    """EGRESS-T05: relaxing the strict profile never relaxes host name checks.

    Args:
        root: Relocated installation.
        pki: Test certificates.

    Returns:
        None

    Raises:
        None
    """
    lax = make_cert(pki.directory, "Lax Inspection CA", ca=True, omit=NONCOMPLIANT)
    root.ca_dir.mkdir(parents=True)
    (root.ca_dir / "lax.pem").write_bytes(lax.pem)
    (root.ca_dir / "corp.pem").write_bytes(pki.ca.pem)
    wrong = make_cert(pki.directory, "wrong", ca=False, issuer=pki.ca, sans=("wrong.example",))
    lax_wrong = make_cert(pki.directory, "lax-wrong", ca=False, issuer=lax, sans=("wrong.example",))
    with serving(wrong) as server:
        with pytest.raises(CertificateVerifyError, match="mismatch"):
            HttpsClient(settings(root)).get(f"{server.base}/x")
        relax(root)
        with pytest.raises(CertificateVerifyError, match="mismatch"):
            HttpsClient(settings(root)).get(f"{server.base}/x")
    with (
        serving(lax_wrong) as server,
        pytest.raises(CertificateVerifyError, match="mismatch") as exc,
    ):
        HttpsClient(settings(root)).get(f"{server.base}/x")
    assert not strict.is_strict_only(exc.value.code, exc.value.reason)


def test_leaf_without_aki_is_diagnosed_as_strict_profile(
    root: InstallPaths,
    pki: Pki,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """EGRESS-T05/EGRESS-005: a proxy leaf without AKI is named a strict-profile failure.

    The confirmation handshake sends no HTTP request, and the real request
    is never retried in relaxed mode. With the fallback set, the same
    server works and ``doctor --network`` names the setting.

    Args:
        root: Relocated installation.
        pki: Test certificates.
        monkeypatch: Pytest monkeypatch.
        capsys: Pytest output capture.

    Returns:
        None

    Raises:
        None
    """
    assert main(["tls", "trust-ca", str(pki.ca.cert_path), "--yes", "--name", "corp"]) == 0
    no_aki = frozenset({"authorityKeyIdentifier"})
    leaf = make_cert(
        pki.directory, "minted", ca=False, issuer=pki.ca, sans=SERVER_NAMES, omit=no_aki
    )
    with serving(leaf) as server:
        url = f"{server.base}/"
        monkeypatch.setattr(cmd_selfupdate, "API_BASE", server.base)
        with pytest.raises(CertificateVerifyError, match="Missing Authority Key Identifier") as exc:
            HttpsClient(settings(root)).get(f"{server.base}/x")
        diagnosis = diagnose(url, settings(root), cmd_tls.TRUST_HINT, cmd_tls.STRICT_HINT)
        checks = cmd_tls.network_checks((url,))
        assert server.requests == []
        config = load_effective_config(root.config_dir, strict=False)
        assert cmd_selfupdate._print_diagnosis(root, config) is False
        strict_out = capsys.readouterr().out
        relax(root)
        assert HttpsClient(settings(root)).get(f"{server.base}/x").status == 200
        relaxed_checks = cmd_tls.network_checks((url,))
        config = load_effective_config(root.config_dir, strict=False)
        assert cmd_selfupdate._print_diagnosis(root, config) is True
    assert exc.value.code == 85
    assert (diagnosis.ok, diagnosis.strict_profile) == (False, True)
    assert "only the strict X.509 profile rejects it" in diagnosis.steps[-1].detail
    assert "network.tls_strict_x509 = false" in "\n".join(render(diagnosis))
    assert "breaks the strict X.509 profile" in "\n".join(render(diagnosis))
    assert "network.tls_strict_x509 = false" in checks[-1].summary
    assert checks[-1].status is Status.CRITICAL
    assert "breaks the strict X.509 profile" in strict_out
    tls_step = next(c for c in relaxed_checks if c.name.endswith(" tls"))
    assert tls_step.status is Status.OK
    assert "strict X.509 profile relaxed" in tls_step.summary
    assert "(network.tls_strict_x509 = false)" in tls_step.summary
    assert "note: strict X.509 checks relaxed" in capsys.readouterr().out


def test_strict_failure_hiding_another_problem_gets_no_fallback_hint(
    root: InstallPaths, pki: Pki
) -> None:
    """EGRESS-005: when relaxing would not help, the fallback is not suggested.

    The leaf lacks an AKI (a strict-only rule fires first) and also names
    the wrong host, so the relaxed confirmation still fails.

    Args:
        root: Relocated installation.
        pki: Test certificates.

    Returns:
        None

    Raises:
        None
    """
    root.ca_dir.mkdir(parents=True)
    (root.ca_dir / "corp.pem").write_bytes(pki.ca.pem)
    no_aki = frozenset({"authorityKeyIdentifier"})
    leaf = make_cert(
        pki.directory, "both", ca=False, issuer=pki.ca, sans=("x.example",), omit=no_aki
    )
    with serving(leaf) as server:
        diagnosis = diagnose(f"{server.base}/", settings(root), strict_hint=cmd_tls.STRICT_HINT)
    assert not diagnosis.strict_profile
    assert diagnosis.hint is None
    assert "with the strict X.509 profile relaxed it still fails" in diagnosis.steps[-1].detail
    assert "mismatch" in diagnosis.steps[-1].detail


def test_context_keeps_verification_in_both_modes(pki: Pki) -> None:
    """EGRESS-004/EGRESS-007: relaxing clears only VERIFY_X509_STRICT.

    Args:
        pki: Test certificates.

    Returns:
        None

    Raises:
        None
    """
    strict_ctx = truststore.build_context(None, (pki.ca.cert_path,))
    relaxed_ctx = truststore.build_context(None, (pki.ca.cert_path,), strict_x509=False)
    copied = strict.relaxed_copy(strict_ctx)
    for context in (strict_ctx, relaxed_ctx, copied):
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
        assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert strict_ctx.verify_flags & ssl.VERIFY_X509_STRICT
    assert not relaxed_ctx.verify_flags & ssl.VERIFY_X509_STRICT
    assert strict_ctx.verify_flags & ~ssl.VERIFY_X509_STRICT == relaxed_ctx.verify_flags
    assert copied.verify_flags == relaxed_ctx.verify_flags
    assert copied.get_ca_certs() == strict_ctx.get_ca_certs()


@pytest.mark.parametrize(
    ("code", "message", "expected"),
    [
        (85, "Missing Authority Key Identifier", True),
        (89, "", True),
        (None, "CA cert does not include key usage extension", True),
        (20, "unable to get local issuer certificate", False),
        (62, "Hostname mismatch", False),
        (None, "", False),
    ],
)
def test_strict_only_codes(code: int | None, message: str, expected: bool) -> None:
    """EGRESS-005: strict-only OpenSSL codes and messages are recognized; others are not.

    Args:
        code: OpenSSL verify code.
        message: OpenSSL verify message.
        expected: Whether it is a strict-profile rule.

    Returns:
        None

    Raises:
        None
    """
    assert strict.is_strict_only(code, message) is expected


def test_unconfirmable_strict_failure_still_names_the_profile(pki: Pki) -> None:
    """EGRESS-005: when the confirmation cannot connect, the strict rule is still named.

    Args:
        pki: Test certificates.

    Returns:
        None

    Raises:
        None
    """
    context = truststore.build_context(None, (pki.ca.cert_path,))
    exc = CertificateVerifyError("127.0.0.1", 1, "Missing Authority Key Identifier", 85)
    verdict = strict.assess(exc, None, 2.0, context)
    assert verdict.strict_only
    assert "confirmation failed" in verdict.note
    empty = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    assert strict.relaxed_copy(empty).get_ca_certs() == []


def test_ca_that_cannot_sign_is_refused_even_when_relaxed(
    root: InstallPaths, pki: Pki, capsys: pytest.CaptureFixture[str]
) -> None:
    """EGRESS-003: a keyUsage without keyCertSign fails in every mode, so it is always refused.

    Args:
        root: Relocated installation.
        pki: Test certificates.
        capsys: Pytest output capture.

    Returns:
        None

    Raises:
        None
    """
    relax(root)
    ca = make_cert(pki.directory, "No Signing CA", ca=True, omit=frozenset({"keyCertSign"}))
    assert main(["tls", "trust-ca", str(ca.cert_path), "--yes"]) == 1
    err = capsys.readouterr().err
    assert "keyUsage does not include keyCertSign" in err
    assert "nothing was installed" in err
    assert not root.ca_dir.exists()


def test_version_1_and_unreadable_configuration_fail_safe(
    root: InstallPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EGRESS-003/EGRESS-007: a v1 certificate is one gap; unreadable config keeps strict on.

    Args:
        root: Relocated installation.
        monkeypatch: Pytest monkeypatch.

    Returns:
        None

    Raises:
        None
    """

    def unreadable_config(config_dir: Path, *, strict: bool = True) -> None:  # noqa: ARG001
        """Simulate a configuration file that exists but cannot be read.

        Args:
            config_dir: Configuration directory.
            strict: Ignored.

        Returns:
            None

        Raises:
            PermissionError: Always.
        """
        raise PermissionError(f"{config_dir}/config.toml")

    class VersionOne:
        """Stands in for a version 1 certificate, which cryptography cannot build."""

        version = x509.Version.v1

    gaps = certinfo.strict_profile_gaps(cast("x509.Certificate", VersionOne()))
    assert gaps == ["X.509 version 3 (this certificate is version 1, so it has no extensions)"]
    monkeypatch.setattr(tls_strict, "load_effective_config", unreadable_config)
    monkeypatch.setattr(lifecycle_checks, "load_effective_config", unreadable_config)
    assert tls_strict.configured(root.config_dir) is True
    unreadable = check_tls_policy()
    assert unreadable.status is Status.UNKNOWN
    assert "cannot read" in unreadable.summary
