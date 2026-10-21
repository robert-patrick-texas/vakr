"""Shared helpers for the authentication tests: cheap hashing, a manual clock, services.

Argon2 runs with tiny parameters here, injected through ``Passwords``; the
production defaults are never weakened (E.12). Test passwords are made up
when the tests run, so no fixture holds a credential (CRED-002).

Dependencies:
    argon2-cffi; vakr.auth; the memory metadata adapter.

Example:
    >>> kit = AuthKit.create()
    >>> kit.services.authenticate(Secret("vks_unknown")) is None
    True
"""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

import argon2

from vakr.adapters.memory import memory_uow_factory
from vakr.auth import AuthKeys, AuthServices, AuthSettings, Passwords, build_auth_services
from vakr.credentials.secret import Secret
from vakr.domain.paging import PageRequest
from vakr.domain.users import Role, User
from vakr.ports.clock import ManualClock
from vakr.ports.metadata import MetadataUnitOfWorkFactory

T0 = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
SOURCE = "192.0.2.10"


def cheap_hasher(time_cost: int = 1) -> argon2.PasswordHasher:
    """Return an Argon2id hasher with test-only parameters.

    Args:
        time_cost: Passes; change it to simulate a parameter upgrade.

    Returns:
        argon2.PasswordHasher: A fast hasher.

    Raises:
        None
    """
    return argon2.PasswordHasher(time_cost=time_cost, memory_cost=8, parallelism=1)


def new_password() -> str:
    """Invent a policy-compliant password for one test run.

    Args:
        None

    Returns:
        str: 32 random hex characters behind a recognisable prefix.

    Raises:
        None
    """
    return f"pw-{secrets.token_hex(16)}"


@dataclass
class AuthKit:
    """Services over one in-memory store with a manual clock.

    Attributes:
        factory: Opens units of work on the store.
        clock: The manual clock.
        services: The assembled services.
    """

    factory: MetadataUnitOfWorkFactory
    clock: ManualClock
    services: AuthServices

    @classmethod
    def create(
        cls, settings: AuthSettings | None = None, hasher: argon2.PasswordHasher | None = None
    ) -> AuthKit:
        """Build services on a fresh store.

        Args:
            settings: Authentication settings; defaults to E.12.
            hasher: Argon2 hasher; defaults to ``cheap_hasher()``.

        Returns:
            AuthKit: The kit.

        Raises:
            None
        """
        factory, clock = memory_uow_factory(), ManualClock(T0)
        passwords = Passwords(hasher or cheap_hasher())
        keys = AuthKeys.from_master(secrets.token_bytes(32))
        services = build_auth_services(
            factory, clock=clock, keys=keys, settings=settings, passwords=passwords
        )
        return cls(factory, clock, services)

    def add_user(self, username: str, password: str, role: Role = Role.ADMIN) -> User:
        """Create an account through the administration service.

        Args:
            username: Username.
            password: Its password.
            role: Its role.

        Returns:
            User: The account.

        Raises:
            None
        """
        return self.services.users.create(username, role, Secret(password), actor="test")

    def audit_text(self) -> str:
        """Return every stored audit event rendered as text, for leak checks.

        Args:
            None

        Returns:
            str: ``repr`` of each event, one per line.

        Raises:
            None
        """
        with self.factory() as uow:
            page = uow.audit.list_page(PageRequest(limit=500))
        return "\n".join(repr(e) for e in page.items)

    def audit_actions(self) -> list[tuple[str, str, str]]:
        """Return ``(action, outcome, actor)`` of each audit event, oldest first.

        Args:
            None

        Returns:
            list[tuple[str, str, str]]: The events.

        Raises:
            None
        """
        with self.factory() as uow:
            page = uow.audit.list_page(PageRequest(limit=500))
        return [(e.action, e.outcome.value, e.actor) for e in reversed(page.items)]
