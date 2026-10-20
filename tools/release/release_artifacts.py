# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Collect release artifacts, write ``SHA256SUMS``, and sign and verify it (ADR-026).

Production hosts trust a release only if ``SHA256SUMS`` carries a valid SSH
signature from a key listed in the package's ``allowed_signers`` file, and
every downloaded artifact matches its checksum (UPD-001). Verification needs
only ``ssh-keygen``, which every supported host has. This module produces
exactly those files and checks the signature against the shipped signers
file *before* anything is published, so a release that production would
reject is never uploaded.

Dependencies:
    hashlib, shutil: Checksums and copies; patterns are relative globs.
    release_proc: Runs ``ssh-keygen`` with argument lists.

Safety:
    The private key is passed to ``ssh-keygen`` by path and never read here.
    Throwaway dry-run keys live in a caller-supplied temporary directory.
    Staging never deletes directories; it only replaces the files it writes.

Example:
    >>> sums = write_sha256sums([Path("dist/pkg.deb")], Path("dist/release"))
    >>> sums.name
    'SHA256SUMS'
"""

import hashlib
import shutil
from pathlib import Path

from release_config import ReleaseConfig
from release_proc import Runner
from release_version import ReleaseVersion

SUMS_NAME = "SHA256SUMS"
SIG_NAME = "SHA256SUMS.sig"


class ArtifactError(RuntimeError):
    """Release artifacts are missing or ambiguous."""


def collect(cfg: ReleaseConfig, version: ReleaseVersion) -> list[Path]:
    """Find the files to release from the configured patterns.

    Args:
        cfg: Release configuration.
        version: Version being released, for pattern placeholders.

    Returns:
        list[Path]: Matching files, required patterns first, each sorted.

    Raises:
        ArtifactError: If a required pattern matches nothing, or two
            artifacts share a file name (checksums list names only).
        release_config.ConfigError: If a pattern has a bad placeholder.
    """
    found: list[Path] = []
    for pattern, required in [(p, True) for p in cfg.artifacts] + [
        (p, False) for p in cfg.optional_artifacts
    ]:
        expanded = cfg.expand(pattern, version)
        matches = sorted(cfg.root.glob(expanded))
        if required and not matches:
            raise ArtifactError(f"required artifact missing: {expanded}")
        found += [m for m in matches if m.is_file()]
    names = [path.name for path in found]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ArtifactError(f"artifacts share a file name: {', '.join(duplicates)}")
    return found


def stage(files: list[Path], out_dir: Path) -> list[Path]:
    """Copy artifacts into the release directory, replacing same-named files.

    Args:
        files: Artifacts from ``collect``.
        out_dir: Release staging directory; created if absent.

    Returns:
        list[Path]: The staged copies, in the same order.

    Raises:
        OSError: If a copy fails.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    staged = []
    for path in files:
        target = out_dir / path.name
        if path.resolve() != target.resolve():
            shutil.copy2(path, target)
        staged.append(target)
    return staged


def sha256_of(path: Path) -> str:
    """Return the hex SHA-256 digest of a file, read in chunks.

    Args:
        path: File to hash.

    Returns:
        str: 64 lowercase hex characters.

    Raises:
        OSError: If the file cannot be read.
    """
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def write_sha256sums(files: list[Path], out_dir: Path) -> Path:
    """Write ``SHA256SUMS`` in ``sha256sum`` format, sorted by file name.

    Args:
        files: Staged artifacts; only their base names are recorded, because
            a downloader verifies files sitting next to the checksum file.
        out_dir: Directory that receives ``SHA256SUMS``.

    Returns:
        Path: The checksum file.

    Raises:
        OSError: If a file cannot be read or the output cannot be written.
    """
    lines = [f"{sha256_of(path)}  {path.name}\n" for path in sorted(files, key=lambda p: p.name)]
    sums = out_dir / SUMS_NAME
    sums.write_text("".join(lines), encoding="utf-8")
    return sums


def sign(runner: Runner, key: Path, namespace: str, sums: Path) -> Path:
    """Sign ``SHA256SUMS`` with an SSH key, producing ``SHA256SUMS.sig``.

    ``ssh-keygen`` may prompt for the key's passphrase, so output is not
    captured and the prompt reaches the terminal.

    Args:
        runner: Command runner.
        key: Private signing key file.
        namespace: Signature namespace, for example ``vakr-release``.
        sums: The checksum file.

    Returns:
        Path: The detached signature file.

    Raises:
        release_proc.CommandError: If signing fails.
    """
    signature = sums.with_name(SIG_NAME)
    # ssh-keygen asks interactively before overwriting an existing signature.
    signature.unlink(missing_ok=True)
    runner.run(
        ["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", namespace, str(sums)], capture=False
    )
    return signature


def verify(
    runner: Runner, signers: Path, identity: str, namespace: str, sums: Path
) -> tuple[bool, str]:
    """Check ``SHA256SUMS.sig`` exactly as production self-update will (UPD-001).

    Args:
        runner: Command runner.
        signers: ``allowed_signers`` file to trust.
        identity: Principal the signature must belong to.
        namespace: Required signature namespace.
        sums: The checksum file; its ``.sig`` sibling is checked.

    Returns:
        tuple[bool, str]: Whether the signature is valid, and ``ssh-keygen``'s
        one-line explanation.

    Raises:
        release_proc.CommandError: If ``ssh-keygen`` is not installed.
    """
    argv = ["ssh-keygen", "-Y", "verify", "-f", str(signers), "-I", identity, "-n", namespace]
    result = runner.run([*argv, "-s", str(sums.with_name(SIG_NAME))], check=False, stdin=sums)
    lines = (result.stdout + result.stderr).strip().splitlines()
    return result.returncode == 0, lines[0] if lines else "no output from ssh-keygen"


def throwaway_signer(
    runner: Runner, workdir: Path, identity: str, namespace: str
) -> tuple[Path, Path]:
    """Create an unprotected key and matching signers file for a dry run.

    Args:
        runner: Command runner.
        workdir: Empty temporary directory owned by the caller.
        identity: Principal to list in the signers file.
        namespace: Namespace the key is allowed to sign.

    Returns:
        tuple[Path, Path]: Private key path and ``allowed_signers`` path.

    Raises:
        release_proc.CommandError: If key generation fails.
    """
    key = workdir / "throwaway_ed25519"
    comment = "throwaway dry-run key"
    runner.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(key)])
    public = key.with_suffix(".pub").read_text(encoding="utf-8").strip()
    signers = workdir / "allowed_signers"
    signers.write_text(f'{identity} namespaces="{namespace}" {public}\n', encoding="utf-8")
    return key, signers


def signer_entries(signers: Path) -> int:
    """Count key entries in an ``allowed_signers`` file, ignoring comments.

    Args:
        signers: The signers file.

    Returns:
        int: Number of non-blank, non-comment lines; 0 for a placeholder or
        a missing file.

    Raises:
        None
    """
    if not signers.is_file():
        return 0
    lines = signers.read_text(encoding="utf-8").splitlines()
    return sum(1 for line in lines if line.strip() and not line.lstrip().startswith("#"))
