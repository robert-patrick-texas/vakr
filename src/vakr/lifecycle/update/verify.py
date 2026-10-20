"""Verify a release before installing it: SSH signature, then SHA-256 checksum.

Release trust (ADR-026): the release tool signs ``SHA256SUMS`` with an SSH
key held off GitHub, and the installed package ships the signer in
``allowed_signers`` format. ``ssh-keygen -Y verify``, present on every
supported host, checks the signature; the package's SHA-256 must then match
its line in the verified list (UPD-001). A compromised GitHub account or a
tampered mirror therefore cannot get a package installed.

Dependencies:
    hashlib: SHA-256 of the package file.
    vakr.lifecycle.update.process: Runs ``ssh-keygen`` as an argument list.

Safety:
    Any failure raises ``VerificationError`` before the caller changes the
    system. The checksum list is size-bounded and parsed strictly; a file
    must appear in it exactly once.

Example:
    >>> verify_release(artifacts, trust, run_command)  # doctest: +SKIP
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from vakr.lifecycle.update.process import Runner

MAX_SUMS_BYTES = 1024 * 1024
_SUM_LINE = re.compile(r"^(?P<digest>[0-9a-fA-F]{64}) [ *](?P<name>[^/\\]+)$")


class VerificationError(Exception):
    """Release artifacts failed verification; nothing may be installed."""


@dataclass(frozen=True, slots=True)
class ReleaseTrust:
    """Who may sign releases, in ``ssh-keygen -Y`` terms.

    Attributes:
        signers: ``allowed_signers`` file shipped in the installed package.
        identity: Principal the signature must belong to (``-I``).
        namespace: Signature namespace (``-n``).
        ssh_keygen: Absolute path of ``ssh-keygen``.
    """

    signers: Path
    identity: str
    namespace: str
    ssh_keygen: str = "/usr/bin/ssh-keygen"


@dataclass(frozen=True, slots=True)
class Artifacts:
    """Local copies of one release's files.

    Attributes:
        package: The ``.deb`` file, under its canonical name.
        sums: ``SHA256SUMS``.
        signature: ``SHA256SUMS.sig``.
    """

    package: Path
    sums: Path
    signature: Path


def verify_signature(sums: bytes, signature: Path, trust: ReleaseTrust, run: Runner) -> None:
    """Check the SSH signature over the checksum list.

    Args:
        sums: Exact bytes of ``SHA256SUMS``.
        signature: ``SHA256SUMS.sig`` path.
        trust: Allowed signers, identity, and namespace.
        run: Command runner.

    Returns:
        None

    Raises:
        VerificationError: When the signers file or signature is missing or
            ``ssh-keygen`` rejects the signature.
    """
    if not trust.signers.is_file():
        raise VerificationError(f"release signers file missing: {trust.signers}")
    if not signature.is_file():
        raise VerificationError(f"signature missing: {signature.name}")
    argv = [
        trust.ssh_keygen,
        "-Y",
        "verify",
        "-f",
        str(trust.signers),
        "-I",
        trust.identity,
        "-n",
        trust.namespace,
        "-s",
        str(signature),
    ]
    result = run(argv, stdin=sums, timeout=60.0)
    if result.returncode != 0:
        reason = result.tail(3) or f"exit status {result.returncode}"
        raise VerificationError(f"signature verification failed: {reason}")


def expected_digest(sums: bytes, name: str) -> str:
    """Find the SHA-256 that the checksum list records for ``name``.

    Args:
        sums: ``SHA256SUMS`` contents (``<hex>  <name>`` per line).
        name: File name to look up.

    Returns:
        str: Lower-case hex digest.

    Raises:
        VerificationError: When the list is malformed, or ``name`` is absent
            or listed more than once.
    """
    digests: list[str] = []
    for number, raw in enumerate(sums.decode("utf-8", "replace").splitlines(), 1):
        if not raw.strip():
            continue
        match = _SUM_LINE.fullmatch(raw.rstrip("\r"))
        if match is None:
            raise VerificationError(f"SHA256SUMS line {number} is malformed")
        if match["name"] == name:
            digests.append(match["digest"].lower())
    if len(digests) != 1:
        found = "not listed" if not digests else "listed more than once"
        raise VerificationError(f"{name} is {found} in SHA256SUMS")
    return digests[0]


def sha256_file(path: Path) -> str:
    """Hash a file in chunks.

    Args:
        path: File to hash.

    Returns:
        str: Lower-case hex SHA-256.

    Raises:
        OSError: When the file cannot be read.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_release(artifacts: Artifacts, trust: ReleaseTrust, run: Runner) -> None:
    """Verify signature then checksum; return only if both pass (UPD-001).

    Args:
        artifacts: Local release files.
        trust: Release signers.
        run: Command runner for ``ssh-keygen``.

    Returns:
        None

    Raises:
        VerificationError: On any verification failure.
    """
    try:
        if artifacts.sums.stat().st_size > MAX_SUMS_BYTES:
            raise VerificationError("SHA256SUMS is implausibly large")
        sums = artifacts.sums.read_bytes()
    except OSError as exc:
        raise VerificationError(f"cannot read SHA256SUMS: {exc}") from exc
    verify_signature(sums, artifacts.signature, trust, run)
    want = expected_digest(sums, artifacts.package.name)
    try:
        have = sha256_file(artifacts.package)
    except OSError as exc:
        raise VerificationError(f"cannot read package: {exc}") from exc
    if have != want:
        raise VerificationError(f"checksum mismatch for {artifacts.package.name}")
