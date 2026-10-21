"""One object the API wires into its middleware: ``AuthServices`` (SEC-005..012).

``build_auth_services`` assembles the password, login, session, kiosk,
user-administration, and CSRF services over one metadata store, and
``AuthServices.authenticate`` resolves any presented token (session cookie
or kiosk token) to a ``Principal``. Everything is synchronous: database
and Argon2 work block, so the HTTP layer calls these from a worker thread
(``asyncio.to_thread``) and applies login delays with ``asyncio.sleep``.

Dependencies:
    vakr.auth.*: The individual services.
    vakr.ports: The metadata unit of work and the clock.

Safety:
    Holds the derived keys only inside ``CsrfGuard`` and ``LoginService``;
    nothing here has a ``repr`` that shows a key, token, or hash.

Example:
    >>> services = build_auth_services(factory, clock=SystemClock(), keys=keys)  # doctest: +SKIP
    >>> services.authenticate(Secret(cookie_value))  # doctest: +SKIP
"""

from dataclasses import dataclass

from vakr.auth.accounts import UserAdmin
from vakr.auth.csrf import CsrfGuard
from vakr.auth.keys import AuthKeys
from vakr.auth.kiosk import KioskService
from vakr.auth.login import LoginService
from vakr.auth.passwords import Passwords
from vakr.auth.principal import Principal
from vakr.auth.purge import PurgeCounts, purge_expired
from vakr.auth.sessions import SessionService
from vakr.auth.settings import AuthSettings
from vakr.auth.tokens import token_kind
from vakr.credentials.secret import Secret
from vakr.ports.clock import Clock
from vakr.ports.metadata.unit_of_work import MetadataUnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class AuthServices:
    """The authentication services of one process.

    Attributes:
        uow_factory: Opens metadata units of work.
        clock: Time source.
        settings: The effective authentication settings.
        passwords: Argon2id hashing.
        login: Password login.
        sessions: Session lifecycle.
        kiosk: Kiosk tokens.
        users: User administration.
        csrf: CSRF tokens.
    """

    uow_factory: MetadataUnitOfWorkFactory
    clock: Clock
    settings: AuthSettings
    passwords: Passwords
    login: LoginService
    sessions: SessionService
    kiosk: KioskService
    users: UserAdmin
    csrf: CsrfGuard

    def authenticate(self, token: Secret) -> Principal | None:
        """Resolve a session cookie or kiosk token to its principal.

        Args:
            token: The presented token.

        Returns:
            Principal | None: The caller, or ``None`` (treat as anonymous).

        Raises:
            StorageError: If the database cannot be read.
        """
        match token_kind(token):
            case "session":
                return self.sessions.validate(token)
            case "kiosk":
                return self.kiosk.validate(token)
            case _:
                return None

    def purge_expired(self) -> PurgeCounts:
        """Delete expired sessions and kiosk tokens and quiet failure runs (bounded).

        Args:
            None

        Returns:
            PurgeCounts: Rows deleted; run again while any count equals the batch size.

        Raises:
            StorageError: If the deletes cannot be written.
        """
        return purge_expired(self.uow_factory, self.clock, self.settings)


def build_auth_services(
    uow_factory: MetadataUnitOfWorkFactory,
    *,
    clock: Clock,
    keys: AuthKeys,
    settings: AuthSettings | None = None,
    passwords: Passwords | None = None,
) -> AuthServices:
    """Assemble the authentication services.

    Args:
        uow_factory: Opens metadata units of work.
        clock: Time source.
        keys: From ``load_or_create_keys(state_dir / KEY_FILE)``.
        settings: From ``auth_settings_from_config``; ``None`` for the E.12 defaults.
        passwords: Hashing; ``None`` for Argon2id with the library defaults.
            Tests inject cheap parameters here, never in production.

    Returns:
        AuthServices: The services.

    Raises:
        None
    """
    chosen = settings or AuthSettings()
    hashing = passwords or Passwords()
    sessions = SessionService(uow_factory, clock, chosen)
    return AuthServices(
        uow_factory=uow_factory,
        clock=clock,
        settings=chosen,
        passwords=hashing,
        login=LoginService(uow_factory, clock, hashing, sessions, keys, chosen),
        sessions=sessions,
        kiosk=KioskService(uow_factory, clock, chosen),
        users=UserAdmin(uow_factory, clock, hashing, chosen),
        csrf=CsrfGuard(keys.csrf),
    )
