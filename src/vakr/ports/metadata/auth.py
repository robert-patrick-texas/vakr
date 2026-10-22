"""The authentication repositories every metadata unit of work exposes (SEC-005, SEC-010).

``MetadataUnitOfWork`` inherits this protocol, so a login can verify a
password, record the session, clear the failure counter, and append the
audit event in one transaction (DB-002). It lives in its own module to keep
the unit-of-work definition within the file-size limit.

Dependencies:
    vakr.ports.metadata.users, sessions: The repository protocols.

Safety:
    See those modules: digests and hashes never leave the adapters in logs.

``users``, ``login_failures``, ``sessions``, and ``kiosk_tokens`` were
added with M4 local authentication (port extension, E.12).

Example:
    >>> sorted(n for n in dir(AuthRepositories) if not n.startswith("_"))
    ['kiosk_tokens', 'login_failures', 'replication_tokens', 'sessions', 'users']
"""

from typing import Protocol

from vakr.ports.metadata.sessions import KioskTokenRepository, SessionRepository
from vakr.ports.metadata.users import LoginFailureRepository, UserRepository


class AuthRepositories(Protocol):
    """The authentication part of a metadata unit of work."""

    @property
    def users(self) -> UserRepository:
        """Local accounts in this unit of work.

        Args:
            None

        Returns:
            UserRepository: The repository.

        Raises:
            None
        """
        ...

    @property
    def login_failures(self) -> LoginFailureRepository:
        """Login throttle state in this unit of work.

        Args:
            None

        Returns:
            LoginFailureRepository: The repository.

        Raises:
            None
        """
        ...

    @property
    def sessions(self) -> SessionRepository:
        """Login sessions in this unit of work.

        Args:
            None

        Returns:
            SessionRepository: The repository.

        Raises:
            None
        """
        ...

    @property
    def kiosk_tokens(self) -> KioskTokenRepository:
        """Kiosk tokens in this unit of work.

        Args:
            None

        Returns:
            KioskTokenRepository: The repository.

        Raises:
            None
        """
        ...

    @property
    def replication_tokens(self) -> KioskTokenRepository:
        """Replication tokens, which share the kiosk record shape (``vakr.domain.replication``).

        Args:
            None

        Returns:
            KioskTokenRepository: The repository.

        Raises:
            None
        """
        ...
