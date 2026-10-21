"""Argon2id password hashing, transparent rehash, and the password policy (SEC-009, E.12).

Hashes come from ``argon2-cffi``'s ``PasswordHasher`` with its defaults,
the RFC 9106 low-memory profile (Argon2id, 3 passes, 64 MiB, 4 lanes). The
parameters are stored in each hash, so when a later release raises them,
``needs_rehash`` notices on the next successful login and the login
service stores a fresh hash (SEC-009). Passwords are NFKC-normalised
first, so the same password typed on a terminal and in a browser hashes
the same even when the two compose accented letters differently.

The policy follows NIST SP 800-63B: a minimum length (15 characters by
default, because the password is the only factor), a generous maximum,
no composition rules, and a refusal of the username, a single repeated
character, and control characters.

Each hash takes tens of milliseconds and 64 MiB, so at most four run at
once; a login that cannot start one within 10 s fails as busy instead of
queueing without bound. Callers on an event loop run these methods in a
worker thread.

Dependencies:
    argon2 (argon2-cffi): Argon2id hashing.
    secrets, threading, unicodedata: Dummy password, concurrency bound, normalisation.
    vakr.credentials.secret: Passwords arrive and travel as ``Secret``.

Safety:
    No method logs, returns, or raises with a password. Problem messages
    describe the rule broken, never the text.

Example:
    >>> import argon2
    >>> cheap = Passwords(argon2.PasswordHasher(time_cost=1, memory_cost=8, parallelism=1))
    >>> stored = cheap.hash(Secret("correct horse battery"))
    >>> cheap.verify(stored, Secret("correct horse battery")), cheap.needs_rehash(stored)
    (True, False)
"""

import logging
import secrets
import threading
import unicodedata
from dataclasses import dataclass

import argon2

from vakr.credentials.secret import Secret

MAX_PASSWORD_LENGTH = 256
MAX_CONCURRENT_HASHES = 4
HASH_WAIT_S = 10.0
_LOG = logging.getLogger(__name__)


class AuthBusyError(Exception):
    """Too many password hashes are already running; retry shortly (HTTP 503)."""


def _prepared(password: Secret) -> str:
    """Return the normalised password text to hash; use it immediately.

    Args:
        password: The password.

    Returns:
        str: NFKC-normalised text.

    Raises:
        None
    """
    return unicodedata.normalize("NFKC", password.reveal())


@dataclass(frozen=True, slots=True)
class PasswordPolicy:
    """Rules a new password must meet (E.12).

    Attributes:
        min_length: Fewest characters, after normalisation.
        max_length: Most characters; bounds hashing work.
    """

    min_length: int = 15
    max_length: int = MAX_PASSWORD_LENGTH

    def problems(self, password: Secret, username: str) -> list[str]:
        """List the rules a candidate password breaks.

        Args:
            password: The candidate.
            username: The account's username, which the password must not be.

        Returns:
            list[str]: One message per broken rule; empty when acceptable.

        Raises:
            None
        """
        text = _prepared(password)
        found = []
        if len(text) < self.min_length:
            found.append(f"must be at least {self.min_length} characters")
        if len(text) > self.max_length:
            found.append(f"must be at most {self.max_length} characters")
        if any(unicodedata.category(c) == "Cc" for c in text):
            found.append("must not contain control characters")
        if text and len(set(text)) == 1:
            found.append("must not be a single repeated character")
        if text.casefold() == username.casefold():
            found.append("must not be the username")
        return found


class Passwords:
    """Hashes and verifies passwords with a bounded number running at once."""

    def __init__(
        self,
        hasher: argon2.PasswordHasher | None = None,
        *,
        max_concurrent: int = MAX_CONCURRENT_HASHES,
        wait_s: float = HASH_WAIT_S,
    ) -> None:
        """Create the service.

        Args:
            hasher: The Argon2 hasher; ``None`` uses the library defaults
                (production). Tests inject cheap parameters here.
            max_concurrent: Most hashes computing at once.
            wait_s: How long a caller waits for a slot.

        Returns:
            None

        Raises:
            None
        """
        self._hasher = hasher if hasher is not None else argon2.PasswordHasher()
        self._slots = threading.BoundedSemaphore(max_concurrent)
        self._wait_s = wait_s
        self._dummy: str | None = None

    def hash(self, password: Secret) -> str:
        """Hash a password with the current parameters.

        Args:
            password: The password.

        Returns:
            str: The Argon2id PHC string, parameters included.

        Raises:
            AuthBusyError: If no hashing slot frees up in time.
        """
        self._acquire()
        try:
            return self._hasher.hash(_prepared(password))
        finally:
            self._slots.release()

    def verify(self, stored_hash: str, password: Secret) -> bool:
        """Check a password against a stored hash.

        Args:
            stored_hash: The account's PHC string.
            password: The presented password.

        Returns:
            bool: Whether it matches; ``False`` for a damaged stored hash too.

        Raises:
            AuthBusyError: If no hashing slot frees up in time.
        """
        self._acquire()
        try:
            return self._hasher.verify(stored_hash, _prepared(password))
        except argon2.exceptions.VerifyMismatchError:
            return False
        except argon2.exceptions.VerificationError, argon2.exceptions.InvalidHashError:
            _LOG.warning("a stored password hash is not a valid Argon2 hash; reset that password")
            return False
        finally:
            self._slots.release()

    def warm_up(self) -> None:
        """Prepare the dummy hash now, so the first unknown-account login is not slower.

        A long-running service calls this once at startup; a CLI need not.

        Args:
            None

        Returns:
            None

        Raises:
            AuthBusyError: If no hashing slot frees up in time.
        """
        if self._dummy is None:
            # A random password nobody knows, hashed with the current parameters, so the
            # unknown-account path costs what the known-account path costs.
            self._dummy = self.hash(Secret(secrets.token_urlsafe(16)))

    def verify_dummy(self, password: Secret) -> None:
        """Spend the time of one verification, for an unknown account (AUTH-004).

        Args:
            password: The presented password.

        Returns:
            None

        Raises:
            AuthBusyError: If no hashing slot frees up in time.
        """
        self.warm_up()
        if self._dummy is not None:
            self.verify(self._dummy, password)

    def needs_rehash(self, stored_hash: str) -> bool:
        """Tell whether a hash was made with other parameters than the current ones.

        Args:
            stored_hash: The account's PHC string.

        Returns:
            bool: ``True`` when the next successful login should store a new hash.

        Raises:
            None
        """
        try:
            return self._hasher.check_needs_rehash(stored_hash)
        except argon2.exceptions.InvalidHashError:
            return True

    def _acquire(self) -> None:
        """Take a hashing slot.

        Args:
            None

        Returns:
            None

        Raises:
            AuthBusyError: If none frees up within ``wait_s``.
        """
        if not self._slots.acquire(timeout=self._wait_s):
            raise AuthBusyError("too many password checks are running; retry shortly")
