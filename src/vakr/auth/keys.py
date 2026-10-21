"""The per-installation authentication key, ``/var/lib/vakr/auth.key`` (SEC-011, SEC-012).

One random 256-bit key, created on first use and kept in a file only the
``vakr`` service user can read (mode 0600). Two purpose-bound keys are
derived from it with HMAC-SHA-256 and distinct labels, so neither use can
stand in for the other:

- ``csrf``: CSRF tokens are ``HMAC(csrf, session ID)`` (``vakr.auth.csrf``);
- ``throttle``: login-failure rows store ``HMAC(throttle, attempted
  username)``, so a password typed into the username field is never stored
  readable (``vakr.auth.login``).

Losing or replacing the file is harmless: open pages need a reload for a
fresh CSRF token, and failure counts restart. It is not in backups.

Dependencies:
    hmac, hashlib, os, secrets: Derivation, file creation, and randomness.

Safety:
    The file is created atomically (written to a temporary name, then
    linked into place, so two processes never see a half-written key) and
    refused when another user owns it or anyone else can read it. Keys
    never appear in ``repr``.

Example:
    >>> keys = AuthKeys.from_master(bytes(32))
    >>> keys, len(keys.csrf), keys.csrf != keys.throttle
    (AuthKeys(...), 32, True)
"""

import contextlib
import hashlib
import hmac
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

KEY_FILE = "auth.key"
"""File name of the key inside the state directory."""

KEY_BYTES = 32
_MODE = 0o600


class AuthKeyError(Exception):
    """The key file exists but is unsafe or damaged; the message says how to fix it."""


@dataclass(frozen=True, slots=True)
class AuthKeys:
    """Purpose-bound keys derived from the installation key.

    Attributes:
        csrf: Key for CSRF token derivation.
        throttle: Key for login-failure account digests.
    """

    csrf: bytes
    throttle: bytes

    @classmethod
    def from_master(cls, master: bytes) -> AuthKeys:
        """Derive the purpose keys from the installation key.

        Args:
            master: The 32-byte installation key.

        Returns:
            AuthKeys: The derived keys.

        Raises:
            ValueError: If ``master`` is not 32 bytes.
        """
        if len(master) != KEY_BYTES:
            raise ValueError("the installation key must be 32 bytes")
        return cls(
            csrf=hmac.digest(master, b"vakr-csrf-v1", hashlib.sha256),
            throttle=hmac.digest(master, b"vakr-throttle-v1", hashlib.sha256),
        )

    def __repr__(self) -> str:
        """Render without the keys.

        Args:
            None

        Returns:
            str: ``AuthKeys(...)``.

        Raises:
            None
        """
        return "AuthKeys(...)"


def load_or_create_keys(path: Path) -> AuthKeys:
    """Read the installation key, creating it first if it does not exist.

    Args:
        path: The key file, normally ``state_dir / KEY_FILE``.

    Returns:
        AuthKeys: The derived keys.

    Raises:
        AuthKeyError: If the file is unsafe or damaged.
        OSError: If the directory cannot be written or the file read.
    """
    if not path.exists():
        _create(path)
    return AuthKeys.from_master(_read(path))


def _create(path: Path) -> None:
    """Write a new random key atomically; a concurrent creator's key wins.

    Args:
        path: The key file.

    Returns:
        None

    Raises:
        OSError: If the directory cannot be written.
    """
    temp = path.with_name(f".{path.name}.{secrets.token_hex(8)}")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _MODE)
    try:
        os.write(fd, secrets.token_bytes(KEY_BYTES).hex().encode() + b"\n")
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.link(temp, path)
    except FileExistsError:
        pass  # another process created it first; both then read the same key
    finally:
        with contextlib.suppress(FileNotFoundError):
            temp.unlink()


def _read(path: Path) -> bytes:
    """Read and check the key file.

    Args:
        path: The key file.

    Returns:
        bytes: The 32-byte key.

    Raises:
        AuthKeyError: If it is not a regular file of this user, is readable
            by others, or does not hold 64 hex digits.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        data = os.read(fd, 256)
    finally:
        os.close(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
        raise AuthKeyError(f"{path} must be a regular file owned by this service user")
    if info.st_mode & 0o077:
        raise AuthKeyError(f"{path} must not be readable by others: run chmod 600 {path}")
    try:
        key = bytes.fromhex(data.decode("ascii").strip())
    except ValueError:
        key = b""
    if len(key) != KEY_BYTES:
        raise AuthKeyError(f"{path} is damaged; delete it and vakr creates a new one")
    return key
