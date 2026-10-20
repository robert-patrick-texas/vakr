# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Write one field bundle: a tar.gz with a checksummed manifest (FIELD-003).

Everything a collection run produces goes into a single
``<prefix>-<host>-<UTC>.tar.gz`` whose ``manifest.json`` lists the tool
version, profiles, non-secret parameters, per-step timings and errors, and a
SHA-256 for every member, so the development side can prove nothing was lost
or altered in transfer. Members are streamed into the archive as they are
produced, so memory stays flat even for a hundred devices.

Dependencies:
    tarfile, gzip, hashlib: Standard library archive and checksums, so the
        bundle opens anywhere.

Safety:
    The archive is created 0600 with ``O_EXCL`` under a ``.partial`` name and
    renamed only when complete. Every member is checked for the run's
    credentials before it is written; a hit is replaced by ``[REDACTED]`` and
    noted. Per-member and total size caps keep the bundle bounded.

Example:
    >>> SAFE_NAME_RE.match("snmp/host-1a2b3c4d/system.txt") is not None
    True
    >>> SAFE_NAME_RE.match("../etc/passwd") is None
    True
"""

import contextlib
import gzip
import hashlib
import io
import json
import os
import re
import tarfile
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

MANIFEST = "manifest.json"
FORMAT_VERSION = 1
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._@+-]*(?:/[A-Za-z0-9_][A-Za-z0-9._@+-]*)*$")
REDACTED = b"[REDACTED]"


class BundleError(Exception):
    """A bundle could not be written or failed verification."""


def utc_stamp(when: datetime) -> str:
    """Format a UTC time as ``YYYYmmddTHHMMSSZ`` for file names.

    Args:
        when: Timezone-aware UTC time.

    Returns:
        str: Compact timestamp.

    Raises:
        None
    """
    return when.strftime("%Y%m%dT%H%M%SZ")


def safe_label(text: str) -> str:
    """Turn a host or device name into a safe path component.

    Args:
        text: Name, address, or pseudonym.

    Returns:
        str: Name using only ``A-Za-z0-9._-``, never starting with a dot.

    Raises:
        None
    """
    label = re.sub(r"[^A-Za-z0-9._-]", "_", text)[:100].lstrip(".")
    return label or "unknown"


def check_member_name(name: str) -> None:
    """Reject archive member names that could escape the extraction directory.

    Args:
        name: Member path inside the archive.

    Returns:
        None

    Raises:
        BundleError: If the name is absolute, has ``..`` or empty
            components, or uses characters outside the safe set.
    """
    if not SAFE_NAME_RE.match(name) or ".." in name.split("/") or len(name) > 240:
        raise BundleError(f"unsafe member name {name!r}")


class BundleWriter:
    """Stream members into a deterministic tar.gz and finish with the manifest.

    Not thread-safe. ``close`` must be called exactly once; ``abort``
    removes the partial file instead.

    Attributes:
        path: Final archive path, created by ``close``.
        members: Member name to ``{"sha256", "bytes"}`` for the manifest.
        notes: Truncations, skips, and redactions, for the manifest.
    """

    def __init__(
        self,
        path: Path,
        when: datetime,
        limits: tuple[int, int],
        secrets: Sequence[bytes] = (),
        scrubber: Callable[[str, str], str] | None = None,
    ) -> None:
        """Open the partial archive.

        Args:
            path: Final ``.tar.gz`` path.
            when: Run start time; used as every member's mtime.
            limits: ``(max_total_bytes, max_member_bytes)`` before compression.
            secrets: Byte strings that must never appear in any member.
            scrubber: ``(kind, text) -> text`` pseudonymization hook.

        Returns:
            None

        Raises:
            OSError: If the partial file exists or cannot be created.
        """
        self.path = path
        self._partial = path.with_name(path.name + ".partial")
        self._mtime = int(when.timestamp())
        self._max_total, self._max_member = limits
        self._secrets = [s for s in secrets if s]
        self._scrubber = scrubber
        self._total = 0
        self.members: dict[str, dict[str, Any]] = {}
        self.notes: list[str] = []
        fd = os.open(self._partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        # These stay open across many add_* calls; close() or abort() closes them.
        self._raw = os.fdopen(fd, "wb")
        # Fixed gzip mtime and no file name in the header: same input, same bytes.
        self._gz = gzip.GzipFile(filename="", mode="wb", fileobj=self._raw, mtime=self._mtime)
        self._tar = tarfile.open(fileobj=self._gz, mode="w", format=tarfile.PAX_FORMAT)  # noqa: SIM115

    def add_text(self, name: str, text: str, kind: str = "text") -> None:
        """Pseudonymize (if enabled) and add a UTF-8 text member.

        Args:
            name: Member path such as ``host/df.txt``.
            text: Content.
            kind: ``"text"`` or ``"snmpwalk"``; selects the scrub rules.

        Returns:
            None

        Raises:
            BundleError: If the name is unsafe or already used.
        """
        if self._scrubber is not None:
            text = self._scrubber(kind, text)
        self._add(name, text.encode("utf-8"))

    def add_json(self, name: str, data: object) -> None:
        """Add a JSON member with sorted keys.

        Args:
            name: Member path ending in ``.json``.
            data: JSON-serializable value.

        Returns:
            None

        Raises:
            BundleError: If the name is unsafe or already used.
        """
        self.add_text(name, json.dumps(data, indent=2, sort_keys=True, default=str) + "\n")

    def _redact(self, name: str, data: bytes) -> bytes:
        """Replace any credential bytes, noting that it happened.

        Args:
            name: Member path, for the note.
            data: Member content.

        Returns:
            bytes: Content without credentials.

        Raises:
            None
        """
        for secret in self._secrets:
            if secret in data:
                data = data.replace(secret, REDACTED)
                self.notes.append(f"{name}: a credential was found and redacted")
        return data

    def _add(self, name: str, data: bytes) -> None:
        """Write one member, enforcing name rules and size caps.

        Args:
            name: Member path.
            data: Content.

        Returns:
            None

        Raises:
            BundleError: If the name is unsafe or already used.
        """
        check_member_name(name)
        if name == MANIFEST or name in self.members:
            raise BundleError(f"duplicate member {name!r}")
        data = self._redact(name, data)
        if len(data) > self._max_member:
            data = data[: self._max_member]
            self.notes.append(f"{name}: truncated to {self._max_member} bytes")
        if self._total + len(data) > self._max_total:
            self.notes.append(f"{name}: skipped, bundle size cap {self._max_total} reached")
            return
        self._write(name, data)
        self._total += len(data)
        self.members[name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}

    def _write(self, name: str, data: bytes) -> None:
        """Append a regular-file entry with fixed ownership and mtime.

        Args:
            name: Member path.
            data: Content.

        Returns:
            None

        Raises:
            OSError: If the archive cannot be written.
        """
        info = tarfile.TarInfo(name)
        info.size, info.mtime, info.mode = len(data), self._mtime, 0o644
        self._tar.addfile(info, io.BytesIO(data))

    def close(self, manifest: dict[str, Any]) -> Path:
        """Write ``manifest.json`` last, close, and rename into place.

        Args:
            manifest: Manifest fields; ``members`` and ``notes`` are added.

        Returns:
            Path: The finished archive.

        Raises:
            OSError: If writing or renaming fails.
        """
        manifest = {**manifest, "members": self.members, "notes": self.notes}
        text = json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n"
        if self._scrubber is not None:
            text = self._scrubber("text", text)
        self._write(MANIFEST, self._redact(MANIFEST, text.encode("utf-8")))
        self._tar.close()
        self._gz.close()
        self._raw.close()
        self._partial.replace(self.path)
        return self.path

    def abort(self) -> None:
        """Close and delete the partial archive after a fatal error.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        for handle in (self._tar, self._gz, self._raw):
            # Already broken; removing the partial file is what matters.
            with contextlib.suppress(OSError, ValueError, EOFError):
                handle.close()
        self._partial.unlink(missing_ok=True)
