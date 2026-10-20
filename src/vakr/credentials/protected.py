"""Read the credentials file only after checking it, in one race-free step (SEC-016).

The file is opened with ``O_NOFOLLOW`` and checked with ``fstat`` on the open
descriptor, so the file whose ownership and mode were checked is the file
that is read; a swap between check and read is impossible. Its text is
decoded straight into a ``Secret``, so no local variable in a frame that can
raise ever holds it.

Dependencies:
    os, errno, stat: ``O_NOFOLLOW`` open, ``fstat``, and file types.
    vakr.credentials.policy: The ownership and permission rules.
    vakr.credentials.secret: The wrapper for the file text.

Safety:
    Strict mode refuses an unsafe file; development mode returns the
    problems as warnings. A file that is not a regular file is refused in
    both modes, because there is nothing sensible to read.

Example:
    >>> from pathlib import Path
    >>> rules = FilePolicy(frozenset({0}), None)
    >>> read_protected(Path("/nonexistent/credentials.toml"), rules, strict=True)
    Traceback (most recent call last):
    ...
    vakr.credentials.errors.CredentialFileMissingError: no credentials file at ...
"""

import errno
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from vakr.credentials.errors import (
    CredentialError,
    CredentialFileError,
    CredentialFileMissingError,
    CredentialFileUnreadableError,
    CredentialPermissionError,
    Issue,
)
from vakr.credentials.policy import (
    FilePolicy,
    Signature,
    directory_problems,
    file_problems,
    signature,
)
from vakr.credentials.secret import Secret

_OPEN_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK  # NONBLOCK: a FIFO cannot hang the open


@dataclass(frozen=True, slots=True)
class ProtectedFile:
    """The checked credentials file, read in full.

    Attributes:
        content: The decoded file text, wrapped so no rendering shows it.
        signature: Identity of the directory and file that were read.
        warnings: Permission problems tolerated in development mode.
    """

    content: Secret = field(repr=False)
    signature: Signature
    warnings: tuple[str, ...]


def read_protected(path: Path, policy: FilePolicy, *, strict: bool) -> ProtectedFile:
    """Check and read the credentials file in one race-free step.

    Args:
        path: The credentials file.
        policy: Accepted owners and reader group.
        strict: Refuse unsafe files (production) instead of warning.

    Returns:
        ProtectedFile: The text, its signature, and tolerated problems.

    Raises:
        CredentialFileMissingError: If the file or directory does not exist.
        CredentialFileUnreadableError: If it cannot be opened or read.
        CredentialPermissionError: If it is unsafe in strict mode, or is
            not a regular file in any mode.
        CredentialFileError: If it is not valid UTF-8.
    """
    dir_stat = _stat(path.parent, path)
    problems = directory_problems(path.parent, dir_stat, policy)
    fd = _open(path, problems, strict=strict)
    try:
        file_stat = os.fstat(fd)
        problems += file_problems(file_stat, policy)
        if (problems and strict) or not stat.S_ISREG(file_stat.st_mode):
            raise CredentialPermissionError(path, problems)
        with open(fd, "rb", closefd=False) as handle:
            content = _decode(handle.read(), path)
    except OSError as exc:
        raise _os_error(path, exc) from None
    finally:
        os.close(fd)
    return ProtectedFile(content, signature(dir_stat, file_stat), tuple(problems))


def _stat(directory: Path, path: Path) -> os.stat_result:
    """Stat the credentials directory, mapping errors to credential errors.

    Args:
        directory: The directory to stat.
        path: The credentials file, for error messages.

    Returns:
        os.stat_result: The directory's metadata.

    Raises:
        CredentialFileMissingError: If the directory does not exist.
        CredentialFileUnreadableError: If it cannot be examined.
    """
    try:
        return directory.stat()
    except OSError as exc:
        raise _os_error(path, exc) from None


def _open(path: Path, problems: list[str], *, strict: bool) -> int:
    """Open the file without following a symlink, unless development mode allows it.

    Args:
        path: The credentials file.
        problems: Problems so far; a symlink problem is appended.
        strict: Whether a symlink is refused.

    Returns:
        int: An open read-only descriptor.

    Raises:
        CredentialFileMissingError: If the file does not exist.
        CredentialFileUnreadableError: If it cannot be opened.
        CredentialPermissionError: If it is a symlink in strict mode.
    """
    try:
        return os.open(path, _OPEN_FLAGS | os.O_NOFOLLOW)
    except OSError as exc:
        if exc.errno != errno.ELOOP:
            raise _os_error(path, exc) from None
    problems.append("file is a symbolic link; it must be a regular file")
    if strict:
        raise CredentialPermissionError(path, problems)
    try:
        return os.open(path, _OPEN_FLAGS)
    except OSError as exc:
        raise _os_error(path, exc) from None


def _decode(raw: bytes, path: Path) -> Secret:
    """Decode the file as UTF-8 straight into a ``Secret``.

    Args:
        raw: File bytes.
        path: The credentials file, for error messages.

    Returns:
        Secret: The decoded text.

    Raises:
        CredentialFileError: If the bytes are not UTF-8; reports the line only.
    """
    try:
        return Secret(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        line = raw.count(b"\n", 0, exc.start) + 1
    # Raised outside the handler so the decode error, which quotes a byte, is
    # not chained, and after dropping the bytes so captured locals show nothing.
    del raw
    raise CredentialFileError(path, [Issue(line, None, "file is not valid UTF-8")])


def _os_error(path: Path, exc: OSError) -> CredentialError:
    """Translate an ``OSError`` into the matching credential error.

    Args:
        path: The credentials file.
        exc: The operating-system error.

    Returns:
        CredentialError: Missing for ``ENOENT``, unreadable otherwise.

    Raises:
        None
    """
    if isinstance(exc, FileNotFoundError):
        return CredentialFileMissingError(path)
    return CredentialFileUnreadableError(path, exc.strerror or type(exc).__name__)
