"""The protected file credential provider (SEC-002, SEC-016, CONF-007).

``FileCredentialProvider`` reads ``/etc/vakr/secrets/credentials.toml``, or
the same file under ``InstallPaths.from_environment().secrets_dir`` when
tests relocate the install, and implements ``CredentialResolver``.

Lifetime of secrets in memory: the provider keeps the parsed file, every
value wrapped in ``Secret``, for as long as the file is unchanged. Each
``resolve`` or ``secret`` call first compares the directory's and file's
inode, size, and change times with what was loaded (one ``stat`` each). On
any difference it drops the old values *before* re-reading, so a rotated or
broken file never leaves stale secrets in service, and rotation needs no
device-record change (15.2). ``forget()`` drops everything, for shutdown or
for a process that no longer polls. Callers resolve immediately before
creating a session, reveal inline into the library call, and keep neither the
credential nor the revealed string afterwards.

Dependencies:
    vakr.credentials.policy, vakr.credentials.protected: Permission rules
        and the race-free read.
    vakr.credentials.parse: TOML parsing and validation.
    vakr.paths: Locates the secrets directory.

Safety:
    Strict permission checks unless ``development=True`` is passed
    explicitly; then each tolerated problem is logged as a WARNING on every
    (re)load. Calls do blocking local file I/O; asyncio code runs them in a
    worker thread (``asyncio.to_thread``), never on the event loop.

Example:
    >>> from pathlib import Path
    >>> FileCredentialProvider(Path("/srv/t/etc/vakr/secrets")).path.name
    'credentials.toml'
"""

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from vakr.credentials.errors import (
    CredentialFileMissingError,
    CredentialFileUnreadableError,
    CredentialNotFoundError,
)
from vakr.credentials.model import Credential, CredentialSummary
from vakr.credentials.parse import CredentialSet, parse_credentials
from vakr.credentials.policy import FilePolicy, Signature, signature
from vakr.credentials.protected import read_protected
from vakr.credentials.secret import Secret
from vakr.credentials.vocabulary import CredentialReference, ProviderKind
from vakr.paths import InstallPaths

CREDENTIALS_FILE = "credentials.toml"
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _Loaded:
    """One successfully loaded file.

    Attributes:
        signature: Identity of what was read.
        data: The parsed content.
        warnings: Permission problems tolerated in development mode.
    """

    signature: Signature
    data: CredentialSet
    warnings: tuple[str, ...]


class FileCredentialProvider:
    """Resolve credentials and named secrets from the protected TOML file.

    Attributes:
        path: The credentials file.
        development: Whether unsafe permissions only warn.
        kind: The provider kind this class serves.
    """

    kind = ProviderKind.FILE

    def __init__(
        self,
        secrets_dir: Path | None = None,
        *,
        development: bool = False,
        policy: FilePolicy | None = None,
    ) -> None:
        """Locate the file; nothing is read until first use.

        Args:
            secrets_dir: Directory holding ``credentials.toml``; ``None``
                means ``InstallPaths.from_environment().secrets_dir``.
            development: Warn instead of refusing unsafe permissions. Pass
                it only for a developer workstation, never in a service.
            policy: Accepted owners and group; ``None`` means this host's
                root, ``vakr`` user, and ``vakr`` group.

        Returns:
            None

        Raises:
            None
        """
        directory = (
            InstallPaths.from_environment().secrets_dir if secrets_dir is None else secrets_dir
        )
        self.path = directory / CREDENTIALS_FILE
        self.development = development
        self._policy = FilePolicy.for_host() if policy is None else policy
        self._loaded: _Loaded | None = None
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        """Describe the provider without any loaded content.

        Args:
            None

        Returns:
            str: Path and mode only.

        Raises:
            None
        """
        return f"FileCredentialProvider({str(self.path)!r}, development={self.development})"

    def resolve(self, ref: CredentialReference) -> Credential:
        """Return the current credential for a reference (CredentialResolver).

        Args:
            ref: Reference from a device record.

        Returns:
            Credential: The credential as the file defines it now.

        Raises:
            CredentialNotFoundError: If no credential has that name.
            CredentialError: If the file is missing, unreadable, unsafe, or invalid.
        """
        credential = self._current().credentials.get(ref.name)
        if credential is None:
            raise CredentialNotFoundError(ref.name, self.path)
        return credential

    def secret(self, name: str) -> Secret:
        """Return a ``[secrets]`` entry, such as a DSN (CredentialResolver).

        Args:
            name: Secret name.

        Returns:
            Secret: The wrapped value.

        Raises:
            CredentialNotFoundError: If no secret has that name.
            CredentialError: If the file is missing, unreadable, unsafe, or invalid.
        """
        value = self._current().secrets.get(name)
        if value is None:
            raise CredentialNotFoundError(name, self.path)
        return value

    def summaries(self) -> tuple[CredentialSummary, ...]:
        """Describe every entry without any secret value.

        Args:
            None

        Returns:
            tuple[CredentialSummary, ...]: SNMP credentials, then named
            secrets, each sorted by name.

        Raises:
            CredentialError: If the file is missing, unreadable, unsafe, or invalid.
        """
        data = self._current()
        snmp = (
            CredentialSummary(c.name, "snmp", c.version, c.security_level, c.is_recommended)
            for c in sorted(data.credentials.values(), key=lambda c: c.name)
        )
        named = (CredentialSummary(n, "secret") for n in sorted(data.secrets))
        return (*snmp, *named)

    @property
    def warnings(self) -> tuple[str, ...]:
        """Permission problems tolerated by the last load in development mode.

        Args:
            None

        Returns:
            tuple[str, ...]: Problem phrases; empty in strict mode.

        Raises:
            None
        """
        loaded = self._loaded
        return () if loaded is None else loaded.warnings

    def known_secrets(self) -> tuple[Secret, ...]:
        """Return the loaded secret values for log redaction; never reads the file.

        Args:
            None

        Returns:
            tuple[Secret, ...]: Every secret currently held, or none.

        Raises:
            None
        """
        loaded = self._loaded
        return () if loaded is None else loaded.data.all_secrets()

    def forget(self) -> None:
        """Drop every loaded secret; the next call reads the file again.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        with self._lock:
            self._loaded = None

    def _current(self) -> CredentialSet:
        """Return the parsed file, reloading it when it changed on disk.

        Args:
            None

        Returns:
            CredentialSet: Content matching the file as it is now.

        Raises:
            CredentialError: If the file is missing, unreadable, unsafe, or invalid.
        """
        with self._lock:
            loaded, self._loaded = self._loaded, None
            # Forgotten until proven current: a deleted, unreadable, or changed
            # file must not keep the old secrets in service or in memory.
            now = self._signature()
            if loaded is not None and loaded.signature == now:
                self._loaded = loaded
                return loaded.data
            del loaded
            protected = read_protected(self.path, self._policy, strict=not self.development)
            data = parse_credentials(protected.content, self.path)
            self._loaded = _Loaded(protected.signature, data, protected.warnings)
            for problem in protected.warnings:
                _LOG.warning(
                    "development mode: %s: %s (strict mode refuses this)", self.path, problem
                )
            return data

    def _signature(self) -> Signature:
        """Stat the directory and file as ``read_protected`` would see them.

        Args:
            None

        Returns:
            Signature: Current identity of the directory and file.

        Raises:
            CredentialFileMissingError: If either does not exist.
            CredentialFileUnreadableError: If either cannot be examined.
        """
        try:
            directory = self.path.parent.stat()
            # Strict mode looks at the link itself, so replacing the file with
            # a symlink is noticed and refused on the next call.
            file = self.path.stat(follow_symlinks=self.development)
        except FileNotFoundError:
            raise CredentialFileMissingError(self.path) from None
        except OSError as exc:
            reason = exc.strerror or type(exc).__name__
            raise CredentialFileUnreadableError(self.path, reason) from None
        return signature(directory, file)
