"""User administration: create, password, enable/disable, role, revoke sessions (SEC-005..006).

Shared by ``vakr setup``, ``vakr user``, and (later) the admin pages of the
web UI. Every change is one unit of work with its audit record (SEC-018).
A password change, a role change, and disabling an account end every
session of that account, so a privilege change never outlives the session
that had the old privileges (SEC-010). The last enabled admin cannot be
disabled or demoted: the change is made, the admins are counted under the
write lock, and the change is rolled back if none would remain.

Dependencies:
    vakr.auth.account_queries, audit: Reads, guards, and audit records.
    vakr.ports: The metadata unit of work and the clock.

Safety:
    Passwords arrive as ``Secret`` and are hashed before any transaction
    opens; audit summaries name accounts and roles only.

Example:
    >>> admin = UserAdmin(memory_uow_factory(), SystemClock(), Passwords())  # doctest: +SKIP
    >>> admin.create("alice", Role.ADMIN, Secret("..."), actor="cli:root")  # doctest: +SKIP
"""

from dataclasses import replace

from vakr.auth.account_queries import AccountQueries, keep_an_admin, require_user
from vakr.auth.audit import record
from vakr.credentials.secret import Secret
from vakr.domain.ids import UserId, new_id
from vakr.domain.users import Role, User, canonical_username


class UserAdmin(AccountQueries):
    """Administrative operations on local accounts (reads come from ``AccountQueries``)."""

    def create(
        self,
        username: str,
        role: Role,
        password: Secret,
        *,
        actor: str,
        display_name: str = "",
        request_id: str | None = None,
    ) -> User:
        """Create an account, with audit.

        Args:
            username: Username as typed; stored canonically.
            role: ``ADMIN`` or ``OPERATOR``.
            password: Initial password.
            actor: Who creates it, such as ``cli:root`` or an admin's username.
            display_name: Optional name to show.
            request_id: API request ID, if any.

        Returns:
            User: The stored account.

        Raises:
            ValidationError: If the username, display name, or password is unacceptable.
            DuplicateError: If the username is taken.
            AuthBusyError: If no hashing slot frees up in time.
        """
        name = canonical_username(username)
        self.check_password(name, password)
        digest = self._passwords.hash(password)
        now = self._clock.now_utc()
        user = User(new_id(UserId), name, display_name.strip(), role, digest, now, now, now)
        with self._uow() as uow:
            uow.users.add(user)
            summary = f"created {role.value} account {name}"
            record(uow, "user.create", user.id, summary, at=now, actor=actor,
                   request_id=request_id)  # fmt: skip
            uow.commit()
        return user

    def set_password(
        self, username: str, password: Secret, *, actor: str, request_id: str | None = None
    ) -> int:
        """Replace an account's password and end its sessions, with audit.

        Args:
            username: The account.
            password: The new password.
            actor: Who changes it.
            request_id: API request ID, if any.

        Returns:
            int: Sessions ended.

        Raises:
            NotFoundError: If there is no such account.
            ValidationError: If the password is unacceptable.
            ConflictError: If the account changed concurrently; retry.
            AuthBusyError: If no hashing slot frees up in time.
        """
        with self._uow() as uow:
            name = require_user(uow, username).username
        self.check_password(name, password)
        digest = self._passwords.hash(password)
        now = self._clock.now_utc()
        with self._uow() as uow:
            current = require_user(uow, name)
            changed = replace(
                current, password_hash=digest, password_changed_at=now, updated_at=now
            )
            uow.users.update(changed)
            ended = uow.sessions.delete_for_user(current.id)
            summary = f"changed the password of {name}; {ended} session(s) ended"
            record(uow, "user.password", current.id, summary, at=now, actor=actor,
                   request_id=request_id)  # fmt: skip
            uow.commit()
        return ended

    def set_enabled(
        self, username: str, enabled: bool, *, actor: str, request_id: str | None = None
    ) -> User:
        """Enable or disable an account; disabling ends its sessions.

        Args:
            username: The account.
            enabled: The new state.
            actor: Who changes it.
            request_id: API request ID, if any.

        Returns:
            User: The stored account (unchanged if it already had that state).

        Raises:
            NotFoundError: If there is no such account.
            ValidationError: If it would leave no enabled admin.
        """
        now = self._clock.now_utc()
        with self._uow() as uow:
            current = require_user(uow, username)
            if current.enabled is enabled:
                return current
            stored = uow.users.update(replace(current, enabled=enabled, updated_at=now))
            ended = 0 if enabled else uow.sessions.delete_for_user(current.id)
            keep_an_admin(uow)
            verb, action = ("enabled", "user.enable") if enabled else ("disabled", "user.disable")
            summary = f"{verb} {current.username}; {ended} session(s) ended"
            record(uow, action, current.id, summary, at=now, actor=actor, request_id=request_id)
            uow.commit()
        return stored

    def set_role(
        self, username: str, role: Role, *, actor: str, request_id: str | None = None
    ) -> User:
        """Change an account's role and end its sessions (privilege change, SEC-010).

        Args:
            username: The account.
            role: The new role.
            actor: Who changes it.
            request_id: API request ID, if any.

        Returns:
            User: The stored account (unchanged if it already had that role).

        Raises:
            NotFoundError: If there is no such account.
            ValidationError: If it would leave no enabled admin.
        """
        now = self._clock.now_utc()
        with self._uow() as uow:
            current = require_user(uow, username)
            if current.role is role:
                return current
            stored = uow.users.update(replace(current, role=role, updated_at=now))
            ended = uow.sessions.delete_for_user(current.id)
            keep_an_admin(uow)
            summary = (
                f"changed {current.username} from {current.role.value} to {role.value}; "
                f"{ended} session(s) ended"
            )
            record(uow, "user.role", current.id, summary, at=now, actor=actor,
                   request_id=request_id)  # fmt: skip
            uow.commit()
        return stored

    def revoke_sessions(self, username: str, *, actor: str, request_id: str | None = None) -> int:
        """End every session of an account, with audit.

        Args:
            username: The account.
            actor: Who ends them.
            request_id: API request ID, if any.

        Returns:
            int: Sessions ended.

        Raises:
            NotFoundError: If there is no such account.
        """
        now = self._clock.now_utc()
        with self._uow() as uow:
            current = require_user(uow, username)
            ended = uow.sessions.delete_for_user(current.id)
            summary = f"ended {ended} session(s) of {current.username}"
            record(uow, "user.revoke_sessions", current.id, summary, at=now, actor=actor,
                   request_id=request_id)  # fmt: skip
            uow.commit()
        return ended
