"""Sample accounts, sessions, kiosk tokens, and failure keys for the auth contract suites.

Builders return valid records with fresh UUIDs and fixed times. Hashes and
digests are placeholders built when the tests run, never real credentials:
a PHC-shaped string and random digests (CRED-002).

Dependencies:
    vakr.domain: The records being built.

Example:
    >>> user().username
    'alice'
"""

import secrets
from datetime import timedelta
from typing import Any

from tests.contract.contract_samples import T0
from vakr.domain.ids import KioskTokenId, SessionId, UserId, new_id
from vakr.domain.sessions import KioskToken, Session
from vakr.domain.users import Role, User

HOUR = timedelta(hours=1)
SOURCE = "192.0.2.10"


def fake_hash() -> str:
    """Return a PHC-shaped placeholder hash, unique per call.

    Args:
        None

    Returns:
        str: ``$argon2id$...`` text that no real password produced.

    Raises:
        None
    """
    return f"$argon2id$v=19$m=8,t=1,p=1${secrets.token_hex(8)}${secrets.token_hex(16)}"


def digest() -> bytes:
    """Return a random 32-byte token digest.

    Args:
        None

    Returns:
        bytes: The digest.

    Raises:
        None
    """
    return secrets.token_bytes(32)


def account_key(seed: str = "alice") -> str:
    """Return a 64-hex-digit account key derived from ``seed``.

    Args:
        seed: Distinguishes keys.

    Returns:
        str: The key.

    Raises:
        None
    """
    return (seed.encode().hex() * 64)[:64]


def user(username: str = "alice", **overrides: Any) -> User:
    """Build an enabled admin account.

    Args:
        username: Canonical username.
        **overrides: Field values to replace.

    Returns:
        User: The account.

    Raises:
        None
    """
    base: dict[str, Any] = {
        "id": new_id(UserId), "username": username, "display_name": username.title(),
        "role": Role.ADMIN, "password_hash": fake_hash(), "created_at": T0, "updated_at": T0,
        "password_changed_at": T0,
    }  # fmt: skip
    return User(**base | overrides)


def session(user_id: UserId, **overrides: Any) -> Session:
    """Build a session created at ``T0`` with a 12-hour lifetime.

    Args:
        user_id: The account.
        **overrides: Field values to replace.

    Returns:
        Session: The session.

    Raises:
        None
    """
    base: dict[str, Any] = {
        "id": new_id(SessionId), "user_id": user_id, "token_hash": digest(), "created_at": T0,
        "last_seen_at": T0, "expires_at": T0 + 12 * HOUR, "source": SOURCE,
    }  # fmt: skip
    return Session(**base | overrides)


def kiosk(label: str = "noc-wall", **overrides: Any) -> KioskToken:
    """Build a kiosk token created at ``T0`` with a 30-day lifetime.

    Args:
        label: Unique label.
        **overrides: Field values to replace.

    Returns:
        KioskToken: The record.

    Raises:
        None
    """
    base: dict[str, Any] = {
        "id": new_id(KioskTokenId), "label": label, "token_hash": digest(), "created_by": "alice",
        "created_at": T0, "expires_at": T0 + timedelta(days=30),
    }  # fmt: skip
    return KioskToken(**base | overrides)
