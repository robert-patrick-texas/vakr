"""Local authentication below the HTTP layer (SEC-005..012, SEC-018, Appendix E.12).

vakr's web UI and API sign people in with local accounts: admins manage
users, inventory, and policy; operators view. This package holds every
decision about that and no HTTP: Argon2id passwords with transparent
rehash, a login service with per-account-and-address throttling, opaque
server-side sessions with idle and absolute expiry, kiosk tokens for wall
displays, CSRF tokens bound to sessions, and the Unix-socket peer
credential rule for the local TUI and CLI. The API process wires
``AuthServices`` into its middleware.

Dependencies:
    argon2 (argon2-cffi): Password hashing.
    vakr.ports.metadata: Users, sessions, kiosk tokens, failures, audit.
    vakr.credentials.secret: ``Secret`` for passwords and tokens.

Safety:
    Passwords and tokens travel as ``Secret``; only Argon2id hashes and
    SHA-256 token digests are stored; nothing here logs a secret.

Example:
    >>> from vakr.auth import Permission, Role, allows
    >>> allows(Role.ADMIN, Permission.ADMINISTER)
    True
"""

from vakr.auth.accounts import UserAdmin
from vakr.auth.csrf import CsrfGuard
from vakr.auth.keys import KEY_FILE, AuthKeyError, AuthKeys, load_or_create_keys
from vakr.auth.kiosk import IssuedKiosk, KioskService
from vakr.auth.login import LoginOutcome, LoginResult, LoginService, clean_source
from vakr.auth.passwords import AuthBusyError, PasswordPolicy, Passwords
from vakr.auth.peercred import (
    LocalGroups,
    PeerCredentials,
    local_groups,
    read_peer_credentials,
    role_for_peer,
)
from vakr.auth.principal import Permission, Principal, PrincipalKind, allows
from vakr.auth.purge import PurgeCounts, purge_expired
from vakr.auth.service import AuthServices, build_auth_services
from vakr.auth.sessions import IssuedSession, SessionService
from vakr.auth.settings import AuthSettings, auth_problems, auth_settings_from_config
from vakr.auth.throttle import ThrottlePolicy, delay_after, wait_remaining
from vakr.domain.users import Role

__all__ = [
    "KEY_FILE",
    "AuthBusyError",
    "AuthKeyError",
    "AuthKeys",
    "AuthServices",
    "AuthSettings",
    "CsrfGuard",
    "IssuedKiosk",
    "IssuedSession",
    "KioskService",
    "LocalGroups",
    "LoginOutcome",
    "LoginResult",
    "LoginService",
    "PasswordPolicy",
    "Passwords",
    "PeerCredentials",
    "Permission",
    "Principal",
    "PrincipalKind",
    "PurgeCounts",
    "Role",
    "SessionService",
    "ThrottlePolicy",
    "UserAdmin",
    "allows",
    "auth_problems",
    "auth_settings_from_config",
    "build_auth_services",
    "clean_source",
    "delay_after",
    "load_or_create_keys",
    "local_groups",
    "purge_expired",
    "read_peer_credentials",
    "role_for_peer",
    "wait_remaining",
]
