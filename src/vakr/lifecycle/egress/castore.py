"""Install, list, and remove operator-trusted CA certificates (EGRESS-003).

Hosts behind a TLS-inspection proxy must trust the enterprise CA for
updates to work. The application trust directory (``ca_dir``) affects only
this application's outbound HTTPS; ``--system`` additionally places the CA
in the OS anchor directory and refreshes the OS bundle, which changes trust
for every program on the host, so it is opt-in.

Dependencies:
    vakr.lifecycle.egress.certinfo: Validation and summaries.
    pathlib, re: File handling and name checks.

Safety:
    A certificate is validated before anything is written, names are
    restricted to a safe character set (no path traversal), files are written
    atomically at mode 0644, and an existing name is never overwritten. The
    OS refresh command is injectable so tests never touch the host store.

Example:
    >>> path = install(pem_bytes, "corp-inspection", ca_dir, now)  # doctest: +SKIP
    >>> [ca.name for ca in list_installed(ca_dir)]  # doctest: +SKIP
    ['corp-inspection']
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from vakr.lifecycle.egress.certinfo import (
    CertificateRejectedError,
    CertSummary,
    load_single_pem,
    summarize,
    validate_ca,
)
from vakr.lifecycle.egress.truststore import ca_files

type CommandRunner = Callable[[Sequence[str]], int]
"""Runs an argument list (never a shell) and returns its exit status."""

NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
FILE_MODE = 0o644


@dataclass(frozen=True, slots=True)
class SystemStore:
    """An OS trust-anchor directory and the command that rebuilds the bundle.

    Attributes:
        anchor_dir: Directory for extra anchors (``<name>.crt``).
        refresh: Argument list that regenerates the OS bundle.
    """

    anchor_dir: Path
    refresh: tuple[str, ...]


SYSTEM_STORES: tuple[SystemStore, ...] = (
    SystemStore(Path("/usr/local/share/ca-certificates"), ("/usr/sbin/update-ca-certificates",)),
    SystemStore(Path("/etc/pki/ca-trust/source/anchors"), ("/usr/bin/update-ca-trust", "extract")),
)
"""Debian/Ubuntu first, then the RHEL family."""


@dataclass(frozen=True, slots=True)
class InstalledCa:
    """One file in the CA directory, parsed when possible.

    Attributes:
        name: File name without suffix.
        path: Full path.
        summary: Parsed fields, or ``None`` when the file is unreadable.
        error: Why parsing failed, or ``None``.
    """

    name: str
    path: Path
    summary: CertSummary | None
    error: str | None = None


def check_name(name: str) -> str:
    """Validate a CA name used as a file name.

    Args:
        name: Proposed name.

    Returns:
        str: The same name when valid.

    Raises:
        CertificateRejectedError: When the name has unsafe characters or length.
    """
    if not NAME_PATTERN.fullmatch(name):
        raise CertificateRejectedError(f"invalid name {name!r}: use letters, digits, '.', '_', '-'")
    return name


def default_name(source: Path) -> str:
    """Derive a safe CA name from the file the operator supplied.

    Args:
        source: Path of the certificate file.

    Returns:
        str: Sanitized file stem, or ``"ca"`` if nothing usable remains.

    Raises:
        None
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", source.stem).strip(".-_")[:64]
    return cleaned or "ca"


def install(pem: bytes, name: str, ca_dir: Path, now: datetime) -> Path:
    """Validate ``pem`` and write it to ``ca_dir/<name>.pem`` at mode 0644.

    Args:
        pem: Candidate certificate contents.
        name: Safe name (see ``check_name``).
        ca_dir: Application CA directory; created if missing.
        now: Current UTC time for the validity check.

    Returns:
        Path: The installed file.

    Raises:
        CertificateRejectedError: When validation fails or the name is taken.
        OSError: When the directory or file cannot be written.
    """
    validate_ca(pem, now)
    target = ca_dir / f"{check_name(name)}.pem"
    if any(p.stem == name for p in ca_files(ca_dir)):
        raise CertificateRejectedError(f"a CA named {name!r} is already installed; remove it first")
    ca_dir.mkdir(parents=True, exist_ok=True)
    _write_atomic(target, pem)
    return target


def list_installed(ca_dir: Path) -> list[InstalledCa]:
    """Describe every CA file in ``ca_dir``.

    Args:
        ca_dir: Application CA directory.

    Returns:
        list[InstalledCa]: One entry per file, sorted by name; unreadable
        files carry an ``error`` instead of a summary.

    Raises:
        None
    """
    entries: list[InstalledCa] = []
    for path in ca_files(ca_dir):
        try:
            summary = summarize(load_single_pem(path.read_bytes()))
        except (CertificateRejectedError, OSError) as exc:
            entries.append(InstalledCa(path.stem, path, None, str(exc)))
            continue
        entries.append(InstalledCa(path.stem, path, summary))
    return entries


def remove(name: str, ca_dir: Path) -> Path:
    """Delete the CA called ``name`` from ``ca_dir``.

    Args:
        name: Installed CA name.
        ca_dir: Application CA directory.

    Returns:
        Path: The removed file.

    Raises:
        CertificateRejectedError: For an invalid name.
        FileNotFoundError: When no CA has that name.
    """
    check_name(name)
    for path in ca_files(ca_dir):
        if path.stem == name:
            path.unlink()
            return path
    raise FileNotFoundError(f"no installed CA named {name!r}")


def detect_system_store(stores: Sequence[SystemStore] = SYSTEM_STORES) -> SystemStore | None:
    """Return the first OS trust store whose anchor directory exists.

    Args:
        stores: Candidates in preference order.

    Returns:
        SystemStore | None: The host's store, or ``None`` when unknown.

    Raises:
        None
    """
    return next((s for s in stores if s.anchor_dir.is_dir()), None)


def install_system(pem: bytes, name: str, store: SystemStore, run: CommandRunner) -> Path:
    """Add a validated CA to the OS store and rebuild the OS bundle.

    Args:
        pem: Certificate already accepted by ``install``.
        name: Safe CA name.
        store: Target OS store.
        run: Command runner for the refresh command.

    Returns:
        Path: The anchor file written.

    Raises:
        CertificateRejectedError: For an invalid name.
        RuntimeError: When the refresh command fails; the anchor is removed
            again so the OS store is left as it was.
        OSError: When the anchor cannot be written.
    """
    target = store.anchor_dir / f"{check_name(name)}.crt"
    _write_atomic(target, pem)
    if (code := run(store.refresh)) != 0:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"{' '.join(store.refresh)} exited {code}; OS store unchanged")
    return target


def remove_system(name: str, pem: bytes, store: SystemStore, run: CommandRunner) -> bool:
    """Remove a CA from the OS store if this tool installed the same bytes there.

    Args:
        name: Safe CA name.
        pem: Contents of the application copy being removed.
        store: OS store to clean.
        run: Command runner for the refresh command.

    Returns:
        bool: ``True`` when an anchor was removed and the bundle rebuilt.

    Raises:
        RuntimeError: When the refresh command fails.
    """
    target = store.anchor_dir / f"{check_name(name)}.crt"
    # Only remove an anchor we can prove is ours, never one an admin added.
    if not target.is_file() or target.read_bytes() != pem:
        return False
    target.unlink()
    if (code := run(store.refresh)) != 0:
        raise RuntimeError(f"{' '.join(store.refresh)} exited {code}")
    return True


def _write_atomic(target: Path, data: bytes) -> None:
    """Write ``data`` to ``target`` via a temporary file and rename, mode 0644.

    Args:
        target: Destination path.
        data: Bytes to write.

    Returns:
        None

    Raises:
        OSError: When writing or renaming fails.
    """
    temp = target.with_name(f".{target.name}.tmp")
    temp.write_bytes(data)
    temp.chmod(FILE_MODE)
    temp.replace(target)
