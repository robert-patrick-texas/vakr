"""Credential errors that explain what is wrong without showing any value (SEC-003).

An operator fixing ``credentials.toml`` needs the file, the line, and the
key; nobody needs the value, and a value in an exception message ends up in
journald, support bundles, and pasted chat logs. Every error here is built
from paths, line numbers, key names, and fixed reason phrases only. None of
them is ever chained to a parser exception: ``tomllib.TOMLDecodeError``
keeps the whole document in its ``doc`` attribute.

Dependencies:
    dataclasses, pathlib: The ``Issue`` value and file locations.

Safety:
    Key names are printed after replacing characters outside a safe set,
    so a hostile key cannot inject terminal escape sequences.

Example:
    >>> from pathlib import Path
    >>> Issue(4, "credentials.core.community", "missing required field").render(Path("c.toml"))
    'c.toml:4: credentials.core.community: missing required field'
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

_UNSAFE_KEY_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def display_key(key: str) -> str:
    """Make a TOML key safe to print in a terminal or log line.

    Args:
        key: Key name exactly as parsed.

    Returns:
        str: The key with every character outside ``[A-Za-z0-9._-]``
        replaced by ``?``.

    Raises:
        None
    """
    return _UNSAFE_KEY_CHARS.sub("?", key)


@dataclass(frozen=True, slots=True)
class Issue:
    """One problem found in the credentials file.

    Attributes:
        line: 1-based line number, or ``None`` when it cannot be located.
        key: Dotted key such as ``credentials.core.community``, or ``None``.
        reason: Fixed phrase describing the rule that failed; never a value.
    """

    line: int | None
    key: str | None
    reason: str

    def render(self, path: Path) -> str:
        """Format the issue as ``path:line: key: reason``.

        Args:
            path: The credentials file.

        Returns:
            str: One operator-readable line.

        Raises:
            None
        """
        where = f"{path}:{self.line}" if self.line else str(path)
        return f"{where}: {self.key}: {self.reason}" if self.key else f"{where}: {self.reason}"


class CredentialError(Exception):
    """Base class for credential failures; messages never contain secret values."""


class CredentialValueError(CredentialError, ValueError):
    """A credential object was built with an incoherent or invalid field.

    Attributes:
        field: Name of the offending field.
        reason: Fixed phrase describing the rule that failed.
    """

    def __init__(self, field: str, reason: str) -> None:
        """Record which field broke which rule.

        Args:
            field: Field name, such as ``auth_secret``.
            reason: Rule description, such as ``is required for authPriv``.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(f"{field} {reason}")
        self.field = field
        self.reason = reason


class CredentialFileMissingError(CredentialError):
    """The credentials file, or its directory, does not exist.

    Attributes:
        path: The expected credentials file.
    """

    def __init__(self, path: Path) -> None:
        """Record the missing path.

        Args:
            path: The expected credentials file.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(f"no credentials file at {path}")
        self.path = path


class CredentialFileUnreadableError(CredentialError):
    """The credentials file exists but this process cannot open or read it.

    Attributes:
        path: The credentials file.
        reason: Operating-system reason, such as ``Permission denied``.
    """

    def __init__(self, path: Path, reason: str) -> None:
        """Record the path and the operating-system reason.

        Args:
            path: The credentials file.
            reason: ``strerror`` of the failure.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(f"cannot read {path}: {reason}")
        self.path = path
        self.reason = reason


class CredentialPermissionError(CredentialError):
    """Strict mode refused the file because its ownership or mode is unsafe.

    Attributes:
        path: The credentials file.
        problems: One phrase per failed rule, with modes and owners only.
    """

    def __init__(self, path: Path, problems: Sequence[str]) -> None:
        """Record every permission problem found.

        Args:
            path: The credentials file.
            problems: Failed rules, at least one.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(f"refusing unsafe credentials file {path}: {'; '.join(problems)}")
        self.path = path
        self.problems = tuple(problems)


class CredentialFileError(CredentialError):
    """The file is not valid TOML or does not describe valid credentials.

    Attributes:
        path: The credentials file.
        issues: Every problem found, in file order where known.
    """

    def __init__(self, path: Path, issues: Sequence[Issue]) -> None:
        """Record the problems found while parsing and validating.

        Args:
            path: The credentials file.
            issues: Problems found, at least one.

        Returns:
            None

        Raises:
            None
        """
        self.path = path
        self.issues = tuple(issues)
        more = f" (and {len(self.issues) - 1} more)" if len(self.issues) > 1 else ""
        super().__init__(f"{self.issues[0].render(path)}{more}")


class CredentialNotFoundError(CredentialError, LookupError):
    """No credential or secret with the requested name exists.

    Attributes:
        name: The requested name, which is a reference and not a secret.
    """

    def __init__(self, name: str, where: Path) -> None:
        """Record the missing name and where it was looked up.

        Args:
            name: Requested credential or secret name.
            where: The credentials file searched.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(f"no entry named {display_key(name)!r} in {where}")
        self.name = name
