"""Who is asking, and what their role permits (SEC-005, SEC-006, AUTH-001, blueprint 3.1).

Every authenticated request resolves to a ``Principal``: a signed-in user,
a kiosk display, or a local process on the Unix socket. Authorization is a
decision on the principal's role in the application layer, never just a
hidden UI control (3.1). In v1 the matrix is small: operators and kiosks
view; admins view and administer; anonymous callers get nothing here
(public dashboards, deferred past the MVP, have their own allowlist).

Dependencies:
    dataclasses, enum: The principal record and permission names.
    vakr.domain: Roles and IDs.

Safety:
    A principal carries no token, hash, or password, so it is safe to log.

Example:
    >>> allows(Role.OPERATOR, Permission.VIEW), allows(Role.OPERATOR, Permission.ADMINISTER)
    (True, False)
    >>> allows(None, Permission.VIEW)
    False
"""

from dataclasses import dataclass
from enum import StrEnum

from vakr.domain.ids import KioskTokenId, SessionId, UserId
from vakr.domain.users import Role


class PrincipalKind(StrEnum):
    """How the caller authenticated."""

    USER = "user"
    KIOSK = "kiosk"
    LOCAL = "local"


class Permission(StrEnum):
    """What a protected use case needs (SEC-006)."""

    VIEW = "view"
    ADMINISTER = "administer"


_GRANTS = {
    Role.ADMIN: frozenset({Permission.VIEW, Permission.ADMINISTER}),
    Role.OPERATOR: frozenset({Permission.VIEW}),
}


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated caller.

    Attributes:
        kind: Session user, kiosk display, or local socket peer.
        name: Audit actor: the username, ``kiosk:<label>``, or ``local:<user>``.
        role: The role it acts with.
        user_id: The account, for session users.
        session_id: The session, for session users; CSRF tokens bind to it.
        kiosk_id: The kiosk token, for kiosk displays.
    """

    kind: PrincipalKind
    name: str
    role: Role
    user_id: UserId | None = None
    session_id: SessionId | None = None
    kiosk_id: KioskTokenId | None = None


def allows(role: Role | None, permission: Permission) -> bool:
    """Decide whether a role grants a permission (AUTH-001).

    Args:
        role: The caller's role, or ``None`` for an anonymous caller.
        permission: What the use case needs.

    Returns:
        bool: Whether the use case may proceed.

    Raises:
        None
    """
    return role is not None and permission in _GRANTS.get(role, frozenset())
