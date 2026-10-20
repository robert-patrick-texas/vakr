# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Verify and safely extract a decrypted field bundle (FIELD-008).

A bundle is data from another site, and even after GCM authentication it
must not be able to write outside its own directory. This module streams the
archive once: every member must be a regular file with a safe relative name
(no absolute paths, no ``..``, no symbolic or hard links, no devices or
FIFOs), sizes are bounded, and each member is hashed as it is written into a
private staging directory. Only when every SHA-256 matches ``manifest.json``
and every listed member is present is the staging directory renamed to
``inbox/<bundle-id>/``.

Dependencies:
    tarfile: Read in streaming mode; members are written with ``os.open``,
        never with ``tarfile`` extraction.
    fieldkit_bundle: Shared member-name rules and the manifest name.

Safety:
    Files are created with ``O_EXCL | O_NOFOLLOW`` beneath a fresh staging
    directory, so an existing file or planted link is never followed or
    overwritten. On any failure the staging directory is removed.

Example:
    >>> BUNDLE_ID_RE.match("vakr-field-east1-20261020T101500Z") is not None
    True
"""

import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import IO, Any

from fieldkit_bundle import MANIFEST, BundleError, check_member_name

BUNDLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
MAX_MEMBERS = 100_000
CHUNK = 1024 * 1024


class UnsafeBundleError(Exception):
    """The bundle is malformed, unsafe to extract, or fails verification."""


def check_member(info: tarfile.TarInfo) -> None:
    """Reject any archive member that is not a plain file with a safe name.

    Args:
        info: Member header.

    Returns:
        None

    Raises:
        UnsafeBundleError: For absolute paths, ``..`` components, links,
            devices, FIFOs, directories, or names outside the safe set.
    """
    name = info.name
    if name.startswith("/") or PurePosixPath(name).is_absolute():
        raise UnsafeBundleError(f"{name}: absolute path")
    if ".." in PurePosixPath(name).parts:
        raise UnsafeBundleError(f"{name}: '..' path component")
    if info.issym() or info.islnk():
        raise UnsafeBundleError(f"{name}: links are not allowed")
    if info.isdev():
        raise UnsafeBundleError(f"{name}: device or FIFO")
    if not info.isreg():
        raise UnsafeBundleError(f"{name}: only regular files are allowed")
    try:
        check_member_name(name)
    except BundleError as exc:
        raise UnsafeBundleError(str(exc)) from None


def _copy(source: IO[bytes], target: Path, limit: int) -> tuple[str, int]:
    """Stream one member to a new file, hashing as it goes.

    Args:
        source: Member stream from ``TarFile.extractfile``.
        target: New file; must not exist.
        limit: Maximum bytes accepted.

    Returns:
        tuple[str, int]: SHA-256 hex digest and size.

    Raises:
        UnsafeBundleError: If the member exceeds ``limit``.
        OSError: If the file exists or cannot be written.
    """
    digest, size = hashlib.sha256(), 0
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
    with os.fdopen(fd, "wb") as out:
        while chunk := source.read(CHUNK):
            size += len(chunk)
            if size > limit:
                raise UnsafeBundleError(f"{target.name}: larger than {limit} bytes")
            digest.update(chunk)
            out.write(chunk)
    return digest.hexdigest(), size


@contextmanager
def _staging(dest_root: Path) -> Generator[Path]:
    """Provide a private staging directory that is removed unless renamed.

    Args:
        dest_root: Directory that will hold ``<bundle-id>/``.

    Returns:
        Generator[Path]: The staging directory.

    Raises:
        OSError: If it cannot be created.
    """
    dest_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".incoming-", dir=dest_root))
    try:
        yield staging
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _stream_members(archive: Path, staging: Path, limit: int) -> dict[str, dict[str, Any]]:
    """Check and write every member, returning what was actually received.

    Args:
        archive: Decrypted ``.tar.gz``.
        staging: Empty staging directory.
        limit: Maximum total uncompressed bytes.

    Returns:
        dict[str, dict[str, Any]]: Member name to ``sha256`` and ``bytes``.

    Raises:
        UnsafeBundleError: On any unsafe or oversized member.
        tarfile.TarError, OSError: If the archive is corrupt.
    """
    received: dict[str, dict[str, Any]] = {}
    total = 0
    with tarfile.open(archive, mode="r|gz") as tar:
        for info in tar:
            check_member(info)
            if info.name in received or len(received) >= MAX_MEMBERS:
                raise UnsafeBundleError(f"{info.name}: duplicate member or too many members")
            stream = tar.extractfile(info)
            if stream is None:
                raise UnsafeBundleError(f"{info.name}: unreadable member")
            sha, size = _copy(stream, staging / info.name, limit - total)
            total += size
            received[info.name] = {"sha256": sha, "bytes": size}
    return received


def verify_manifest(received: dict[str, dict[str, Any]], staging: Path) -> dict[str, Any]:
    """Compare received members with ``manifest.json`` (FIELD-003).

    Args:
        received: Name to digest and size for every extracted member.
        staging: Directory holding the extracted manifest.

    Returns:
        dict[str, Any]: The parsed manifest.

    Raises:
        UnsafeBundleError: If the manifest is missing or malformed, a member
            is missing or unlisted, a checksum differs, or the bundle id is
            unsafe.
    """
    if MANIFEST not in received:
        raise UnsafeBundleError("manifest.json is missing")
    try:
        manifest = json.loads((staging / MANIFEST).read_text(encoding="utf-8"))
        listed: dict[str, Any] = manifest["members"]
    except (ValueError, KeyError, TypeError) as exc:
        raise UnsafeBundleError(f"manifest.json is malformed: {exc}") from None
    actual = {k: v for k, v in received.items() if k != MANIFEST}
    missing, unlisted = sorted(set(listed) - set(actual)), sorted(set(actual) - set(listed))
    if missing or unlisted:
        raise UnsafeBundleError(
            f"members differ from manifest: missing {missing}, unlisted {unlisted}"
        )
    for name, entry in listed.items():
        if (
            entry.get("sha256") != actual[name]["sha256"]
            or entry.get("bytes") != actual[name]["bytes"]
        ):
            raise UnsafeBundleError(f"{name}: SHA-256 or size differs from manifest.json")
    if not BUNDLE_ID_RE.match(str(manifest.get("bundle_id", ""))):
        raise UnsafeBundleError("manifest.json has a missing or unsafe bundle_id")
    return manifest


def extract_verified(archive: Path, dest_root: Path, limit: int) -> tuple[Path, dict[str, Any]]:
    """Verify a decrypted bundle and extract it into ``dest_root/<bundle-id>/``.

    Args:
        archive: Decrypted ``.tar.gz``.
        dest_root: Git-ignored ``inbox/`` directory.
        limit: Maximum total uncompressed bytes.

    Returns:
        tuple[Path, dict[str, Any]]: Extraction directory and manifest.

    Raises:
        UnsafeBundleError: If the bundle is unsafe, corrupt, or fails
            verification, or was already extracted.
    """
    with _staging(dest_root) as staging:
        try:
            received = _stream_members(archive, staging, limit)
        except (tarfile.TarError, EOFError, OSError) as exc:
            raise UnsafeBundleError(f"corrupt archive: {exc}") from None
        manifest = verify_manifest(received, staging)
        final = dest_root / manifest["bundle_id"]
        if final.exists():
            raise UnsafeBundleError(f"{final} already exists; remove it to process again")
        staging.rename(final)
    return final, manifest
