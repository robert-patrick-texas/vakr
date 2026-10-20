# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests for CMS encryption of bundles with ``openssl cms`` (FIELD-006, ADR-028).

Bundles must round-trip through AES-256-GCM AuthEnvelopedData with
RSA-OAEP/SHA-256, a modified bundle must fail authentication without leaving
plaintext behind, a bundle for another recipient must not open, and the
preflight must refuse OpenSSL older than 3.0 or LibreSSL.

Dependencies:
    pytest: Test runner.
    fieldkit_crypto: Module under test.
    conftest: Throwaway recipient key pairs and fake commands.

Safety:
    Keys are generated per session; ciphertext is written under ``tmp_path``.

Example:
    $ uv run pytest tools/fieldkit/test_fieldkit_crypto.py
"""

import os
import subprocess
from pathlib import Path

import pytest
from fieldkit_crypto import (
    CryptoError,
    certificate_info,
    decrypt,
    encrypt,
    parse_openssl_version,
    preflight,
)
from fieldkit_testkit import Pki, fake_command


def test_round_trip_is_auth_enveloped_gcm_with_oaep(tmp_path: Path, pki: Pki) -> None:
    """Encrypt then decrypt restores the bytes; the structure is GCM + OAEP (FIELD-006).

    Args:
        tmp_path: Per-test directory.
        pki: Session PKI.

    Returns:
        None

    Raises:
        None
    """
    plain = tmp_path / "b.tar.gz"
    plain.write_bytes(os.urandom(200_000))
    sealed = tmp_path / "b.tar.gz.p7m"
    encrypt("openssl", pki.recipient, plain, sealed)
    assert sealed.stat().st_mode & 0o077 == 0
    printed = subprocess.run(  # noqa: S603
        ["openssl", "cms", "-cmsout", "-print", "-inform", "DER", "-in", str(sealed)],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "authEnvelopedData" in printed
    assert "aes-256-gcm" in printed
    assert "rsaesOaep" in printed
    assert "sha256" in printed
    out = tmp_path / "out.tar.gz"
    decrypt("openssl", pki.recipient, pki.recipient_key, sealed, out)
    assert out.read_bytes() == plain.read_bytes()


def test_tampered_bundle_fails_and_leaves_no_plaintext(tmp_path: Path, pki: Pki) -> None:
    """Flipping one ciphertext bit fails GCM authentication; output is removed.

    Args:
        tmp_path: Per-test directory.
        pki: Session PKI.

    Returns:
        None

    Raises:
        None
    """
    plain = tmp_path / "b.tar.gz"
    plain.write_bytes(b"production facts " * 5000)
    sealed = tmp_path / "b.p7m"
    encrypt("openssl", pki.recipient, plain, sealed)
    data = bytearray(sealed.read_bytes())
    data[len(data) // 2] ^= 0x01
    sealed.write_bytes(bytes(data))
    out = tmp_path / "out.tar.gz"
    with pytest.raises(CryptoError, match="modified"):
        decrypt("openssl", pki.recipient, pki.recipient_key, sealed, out)
    assert not out.exists()


def test_other_recipient_cannot_decrypt(tmp_path: Path, pki: Pki) -> None:
    """Only the intended recipient's key opens the bundle.

    Args:
        tmp_path: Per-test directory.
        pki: Session PKI.

    Returns:
        None

    Raises:
        None
    """
    plain = tmp_path / "b.tar.gz"
    plain.write_bytes(b"x" * 100)
    sealed = tmp_path / "b.p7m"
    encrypt("openssl", pki.other, plain, sealed)
    with pytest.raises(CryptoError):
        decrypt("openssl", pki.recipient, pki.recipient_key, sealed, tmp_path / "o")


def test_encrypt_failure_leaves_no_partial(tmp_path: Path) -> None:
    """A missing recipient certificate fails cleanly.

    Args:
        tmp_path: Per-test directory.

    Returns:
        None

    Raises:
        None
    """
    plain = tmp_path / "b.tar.gz"
    plain.write_bytes(b"x")
    with pytest.raises(CryptoError, match="encryption failed"):
        encrypt("openssl", tmp_path / "missing.pem", plain, tmp_path / "b.p7m")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["b.tar.gz"]


def test_version_parsing_and_preflight(tmp_path: Path) -> None:
    """OpenSSL 3.x passes; 1.1.1, LibreSSL, and a missing binary are refused.

    Args:
        tmp_path: Per-test directory.

    Returns:
        None

    Raises:
        None
    """
    assert parse_openssl_version("OpenSSL 3.0.13 30 Jan 2024") == (3, 0, 13)
    assert preflight("openssl").startswith("OpenSSL 3")
    old = fake_command(tmp_path, "old-openssl", "print('OpenSSL 1.1.1w  11 Sep 2023')")
    with pytest.raises(CryptoError, match=r"3\.0 or newer"):
        preflight(str(old))
    libre = fake_command(tmp_path, "libressl", "print('LibreSSL 3.8.2')")
    with pytest.raises(CryptoError, match="not OpenSSL"):
        preflight(str(libre))
    with pytest.raises(CryptoError, match="not installed"):
        preflight(str(tmp_path / "nope"))


def test_certificate_info_reports_fingerprint(tmp_path: Path, pki: Pki) -> None:
    """Recipient details come back for the manifest; junk files are refused.

    Args:
        tmp_path: Per-test directory.
        pki: Session PKI.

    Returns:
        None

    Raises:
        None
    """
    info = certificate_info("openssl", pki.recipient)
    assert "test recipient" in info["subject"]
    assert len(info["sha256"].split(":")) == 32
    junk = tmp_path / "junk.pem"
    junk.write_text("not a certificate", encoding="ascii")
    with pytest.raises(CryptoError, match="cannot read certificate"):
        certificate_info("openssl", junk)
