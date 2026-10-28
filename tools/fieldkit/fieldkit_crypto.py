# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Encrypt and decrypt field bundles with ``openssl cms`` (FIELD-006, ADR-028).

Bundles cross an untrusted transfer location (a file share, a web server,
an e-mail attachment), so they are encrypted to the development host's
recipient certificate as CMS AuthEnvelopedData (RFC 5083) with AES-256-GCM
(RFC 5084) and RSA-OAEP/SHA-256 key transport. Only the development host's
private key opens them, and GCM authentication makes any modification fail
decryption. The standard library has no CMS support, and the
``cryptography`` package only does AES-CBC EnvelopedData, so the field kit
runs the ``openssl`` command (3.0 or newer) with an argument list.

Dependencies:
    fieldkit_run: Bounded command execution.
    secrets: Generates the production-side pseudonym key (FIELD-005), the
        only file the field kit writes outside its output directory.
    hashlib: The key's public id (``key_id``).

Safety:
    Output files are created with umask 077. ``openssl cms -decrypt`` writes
    plaintext *before* it checks the GCM tag, so ``decrypt`` deletes the
    output whenever openssl reports failure; unauthenticated plaintext is
    never left behind. The pseudonym key is created 0600 in a 0700
    directory and refused if anyone else can read it.

Example:
    >>> parse_openssl_version("OpenSSL 3.5.5 27 Jan 2026 (Library: OpenSSL 3.5.5 27 Jan 2026)")
    (3, 5, 5)
"""

import hashlib
import os
import re
import secrets
from pathlib import Path

from fieldkit_run import CommandResult, run_command

MIN_OPENSSL = (3, 0, 0)
CMS_TIMEOUT = 600.0
"""Seconds allowed for one encryption or decryption of a bundle."""


class CryptoError(Exception):
    """OpenSSL is unsuitable or a CMS operation failed."""


def parse_openssl_version(text: str) -> tuple[int, int, int]:
    """Parse ``openssl version`` output.

    Args:
        text: First line printed by ``openssl version``.

    Returns:
        tuple[int, int, int]: Major, minor, patch.

    Raises:
        CryptoError: If the output is not from OpenSSL (LibreSSL lacks
            the needed CMS AuthEnvelopedData support).
    """
    match = re.match(r"OpenSSL (\d+)\.(\d+)\.(\d+)", text.strip())
    if not match:
        raise CryptoError(f"not OpenSSL: {text.strip()[:80]!r}")
    return int(match[1]), int(match[2]), int(match[3])


def preflight(openssl: str) -> str:
    """Check that ``openssl`` exists and is version 3.0 or newer.

    Args:
        openssl: Program name or path.

    Returns:
        str: The version line, for the manifest.

    Raises:
        CryptoError: If openssl is missing, fails, or is too old.
    """
    result = run_command([openssl, "version"], timeout=30)
    if not result.ok:
        raise CryptoError(f"{openssl} version failed: {result.error or result.stderr.strip()}")
    if parse_openssl_version(result.stdout) < MIN_OPENSSL:
        raise CryptoError(f"OpenSSL 3.0 or newer is required, found {result.stdout.strip()}")
    return result.stdout.strip()


def certificate_info(openssl: str, cert: Path) -> dict[str, str]:
    """Read a recipient certificate's subject, expiry, and SHA-256 fingerprint.

    Args:
        openssl: Program name or path.
        cert: PEM certificate file.

    Returns:
        dict[str, str]: ``subject``, ``not_after``, and ``sha256``.

    Raises:
        CryptoError: If the file is not a readable certificate.
    """
    argv = [openssl, "x509", "-in", str(cert), "-noout", "-subject", "-enddate"]
    result = run_command([*argv, "-fingerprint", "-sha256"], timeout=30)
    if not result.ok:
        raise CryptoError(f"cannot read certificate {cert}: {_first_line(result)}")
    info: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition("=")
        if key == "subject":
            info["subject"] = value.strip()
        elif key == "notAfter":
            info["not_after"] = value.strip()
        elif "Fingerprint" in key:
            info["sha256"] = value.strip()
    return info


def _first_line(result: CommandResult) -> str:
    """Return the most useful single line of a failed command's output.

    Args:
        result: Finished command.

    Returns:
        str: Error, first stderr line, or the exit status.

    Raises:
        None
    """
    lines = result.stderr.strip().splitlines()
    return result.error or (lines[0] if lines else f"exit status {result.returncode}")


def _run_private(argv: list[str]) -> CommandResult:
    """Run openssl so that every file it creates is private to this user.

    Args:
        argv: Command argument list.

    Returns:
        CommandResult: Outcome of the command.

    Raises:
        None
    """
    # Bundles hold production data; files openssl creates must start at 0600.
    return run_command(argv, timeout=CMS_TIMEOUT, umask=0o077)


def encrypt(openssl: str, recipient: Path, plaintext: Path, output: Path) -> None:
    """Encrypt ``plaintext`` to ``recipient`` as AES-256-GCM CMS (DER).

    Args:
        openssl: Program name or path.
        recipient: Recipient certificate (PEM).
        plaintext: Bundle to encrypt.
        output: Destination ``*.p7m``; written under a ``.partial`` name
            and renamed on success.

    Returns:
        None

    Raises:
        CryptoError: If openssl fails.
    """
    partial = output.with_name(output.name + ".partial")
    # -keyopt applies to the preceding -recip, so it must follow it.
    argv = [openssl, "cms", "-encrypt", "-binary", "-aes-256-gcm", "-recip", str(recipient)]
    argv += ["-keyopt", "rsa_padding_mode:oaep", "-keyopt", "rsa_oaep_md:sha256"]
    argv += ["-in", str(plaintext), "-outform", "DER", "-out", str(partial)]
    result = _run_private(argv)
    if not result.ok:
        partial.unlink(missing_ok=True)
        raise CryptoError(f"encryption failed: {_first_line(result)}")
    partial.replace(output)


def decrypt(openssl: str, recipient: Path, key: Path, encrypted: Path, output: Path) -> None:
    """Decrypt and authenticate a ``*.p7m`` bundle.

    Args:
        openssl: Program name or path.
        recipient: Recipient certificate (PEM).
        key: Recipient private key (PEM, mode 0600).
        encrypted: CMS file in DER form.
        output: Destination for the plaintext tar.gz.

    Returns:
        None

    Raises:
        CryptoError: If the bundle is for another recipient, was modified
            (GCM authentication failure), or openssl fails.
    """
    argv = [openssl, "cms", "-decrypt", "-binary", "-inform", "DER", "-recip", str(recipient)]
    argv += ["-inkey", str(key), "-in", str(encrypted), "-out", str(output)]
    result = _run_private(argv)
    if not result.ok:
        output.unlink(missing_ok=True)  # it may hold unauthenticated plaintext
        raise CryptoError(
            "decryption failed: the bundle is for another recipient or was modified "
            f"({_first_line(result)})"
        )


def load_or_create_key(path: Path) -> bytes:
    """Read the pseudonym key, creating it on first use.

    Args:
        path: Key file, normally ``~/.config/<tool>/pseudonym.key``.

    Returns:
        bytes: The 32-byte HMAC key.

    Raises:
        PermissionError: If an existing key is accessible by group or others.
        ValueError: If an existing key file is malformed.
        OSError: If the file cannot be read or created.
    """
    if path.exists():
        if path.stat().st_mode & 0o077:
            raise PermissionError(f"{path} must be mode 0600; run: chmod 600 {path}")
        key = bytes.fromhex(path.read_text(encoding="ascii").strip())
        if len(key) != 32:
            raise ValueError(f"{path} does not hold a 32-byte hex key")
        return key
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    # O_EXCL: two concurrent first runs must not silently use different keys.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(key.hex() + "\n")
    return key


def key_id(key: bytes | None) -> str | None:
    """Name the pseudonym key without revealing it (FIELD-005, E.34 decision 7).

    Every host of one collection uses the same key, so every host shows the
    same id: the manifest carries it for the report, and the final line for
    the operator, who can compare the hosts on site.

    Args:
        key: The pseudonym key, or ``None`` without ``--pseudonymize``.

    Returns:
        str | None: 16 hex digits of a domain-separated hash, or ``None``.

    Raises:
        None
    """
    return hashlib.sha256(b"fieldkit key id\0" + key).hexdigest()[:16] if key else None
