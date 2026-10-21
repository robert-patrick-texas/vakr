"""Session lifecycle: idle and absolute expiry, bounded touches, rotation, revocation (AUTH-002).

Times come from a manual clock (``vakr.ports.clock``), so the boundaries
are tested to the microsecond: a session is valid until, and not at, its
idle or absolute deadline (SEC-010, Appendix E.12).

Dependencies:
    pytest; tests.unit.auth.auth_testkit.

Example:
    $ uv run pytest tests/unit/auth/test_sessions.py
"""

from datetime import timedelta

import pytest

from tests.unit.auth.auth_testkit import SOURCE, T0, AuthKit, new_password
from vakr.adapters.memory.metadata_uow import MemoryUnitOfWork
from vakr.auth import AuthSettings, IssuedSession
from vakr.credentials.secret import Secret
from vakr.domain.errors import StorageError
from vakr.domain.users import Role

US = timedelta(microseconds=1)


def _signed_in(kit: AuthKit, username: str = "alice") -> IssuedSession:
    """Create an account and sign it in.

    Args:
        kit: The services.
        username: The account.

    Returns:
        IssuedSession: The session and token.

    Raises:
        AssertionError: If the login fails.
    """
    pw = new_password()
    kit.add_user(username, pw)
    issued = kit.services.login.login(username, Secret(pw), source=SOURCE).issued
    assert issued is not None
    return issued


def test_auth002_idle_expiry_boundary() -> None:
    """AUTH-002: idle for 30 min minus 1 us is valid; idle for exactly 30 min is not.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = AuthKit.create()
    issued = _signed_in(kit)
    kit.clock.advance(timedelta(minutes=30) - US)
    assert kit.services.authenticate(issued.token) is not None  # this request touches it
    kit.clock.advance(timedelta(minutes=30) - US)
    assert kit.services.authenticate(issued.token) is not None
    kit.clock.advance(timedelta(minutes=30))
    assert kit.services.authenticate(issued.token) is None


def test_auth002_absolute_expiry_boundary_despite_activity() -> None:
    """AUTH-002: an active session still ends exactly 12 h after login.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = AuthKit.create()
    issued = _signed_in(kit)
    for _ in range(71):  # every 10 minutes up to 11 h 50 min
        kit.clock.advance(timedelta(minutes=10))
        assert kit.services.authenticate(issued.token) is not None
    kit.clock.advance(timedelta(minutes=10) - US)
    assert kit.services.authenticate(issued.token) is not None
    kit.clock.advance(US)
    assert kit.clock.now_utc() == T0 + timedelta(hours=12)
    assert kit.services.authenticate(issued.token) is None


def test_auth002_touch_writes_at_most_once_per_interval() -> None:
    """E.12: requests within 60 s of the last recorded use do not write.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = AuthKit.create()
    issued = _signed_in(kit)

    def last_seen() -> object:
        """Read the stored last-seen time.

        Args:
            None

        Returns:
            object: The time.

        Raises:
            None
        """
        with kit.factory() as uow:
            stored = uow.sessions.get(issued.session.id)
        return stored.last_seen_at if stored else None

    for _ in range(5):
        kit.clock.advance(timedelta(seconds=10))
        assert kit.services.authenticate(issued.token) is not None
    assert last_seen() == T0
    kit.clock.advance(timedelta(seconds=10))
    kit.services.authenticate(issued.token)
    assert last_seen() == T0 + timedelta(seconds=60)


def test_auth002_rotation_keeps_the_absolute_expiry() -> None:
    """SEC-010: rotation replaces the token and ID; the old token stops working at once.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = AuthKit.create()
    issued = _signed_in(kit)
    kit.clock.advance(timedelta(minutes=20))
    rotated = kit.services.sessions.rotate(issued.token, source="198.51.100.7")
    assert rotated is not None
    assert rotated.token.reveal() != issued.token.reveal()
    assert rotated.session.id != issued.session.id
    assert rotated.session.expires_at == issued.session.expires_at
    assert rotated.session.source == "198.51.100.7"
    assert kit.services.authenticate(issued.token) is None
    assert kit.services.authenticate(rotated.token) is not None
    assert kit.services.sessions.rotate(issued.token) is None
    kit.clock.advance(timedelta(minutes=29))
    assert kit.services.authenticate(rotated.token) is not None  # idle restarts at rotation


def test_auth002_logout_and_revoke_all_are_audited() -> None:
    """AUTH-002, SEC-018: logout ends one session; revocation ends every one; both audited.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit, pw = AuthKit.create(), new_password()
    kit.add_user("alice", pw)
    sessions = [kit.services.login.login("alice", Secret(pw), source=SOURCE).issued
                for _ in range(3)]  # fmt: skip
    tokens = [s.token for s in sessions if s is not None]
    assert kit.services.sessions.revoke(tokens[0], request_id="req-9")
    assert not kit.services.sessions.revoke(tokens[0])
    assert kit.services.authenticate(tokens[0]) is None
    assert kit.services.authenticate(tokens[1]) is not None
    assert kit.services.users.revoke_sessions("alice", actor="admin") == 2
    assert all(kit.services.authenticate(t) is None for t in tokens)
    actions = [a for a, _, _ in kit.audit_actions()]
    assert actions.count("auth.logout") == 1
    assert actions[-1] == "user.revoke_sessions"


@pytest.mark.parametrize("change", ["password", "role", "disable"])
def test_sec010_privilege_and_password_changes_end_sessions(change: str) -> None:
    """SEC-010: a password, role, or enabled-state change signs the account out everywhere.

    Args:
        change: Which change to make.

    Returns:
        None

    Raises:
        None
    """
    kit = AuthKit.create()
    kit.add_user("root-admin", new_password())
    issued = _signed_in(kit, "bob")
    users = kit.services.users
    match change:
        case "password":
            assert users.set_password("bob", Secret(new_password()), actor="admin") == 1
        case "role":
            users.set_role("bob", Role.OPERATOR, actor="admin")
        case _:
            users.set_enabled("bob", False, actor="admin")
    assert kit.services.authenticate(issued.token) is None


def test_rejects_foreign_and_malformed_tokens() -> None:
    """AUTH-002: kiosk-prefixed, empty, oversized, and non-ASCII tokens never authenticate.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = AuthKit.create()
    issued = _signed_in(kit)
    assert kit.services.sessions.validate(Secret("vkk_" + issued.token.reveal()[4:])) is None
    for junk in ("", "vks_", "vks_" + "x" * 500, "vks_café", "Bearer abc"):
        assert kit.services.authenticate(Secret(junk)) is None
    assert kit.services.authenticate(issued.token) is not None


def test_sessions_per_user_are_capped_oldest_first() -> None:
    """Bounded: the 21st login of one account ends its oldest session (E.12: 20 kept).

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = AuthKit.create(AuthSettings(max_sessions_per_user=3))
    pw = new_password()
    user = kit.add_user("alice", pw)
    tokens = []
    for _ in range(4):
        kit.clock.advance(timedelta(seconds=1))
        issued = kit.services.login.login("alice", Secret(pw), source=SOURCE).issued
        assert issued is not None
        tokens.append(issued.token)
    assert len(kit.services.sessions.sessions_of(user.id)) == 3
    assert kit.services.authenticate(tokens[0]) is None
    assert all(kit.services.authenticate(t) is not None for t in tokens[1:])


def test_purge_is_bounded_and_removes_only_expired_rows() -> None:
    """E.12: one purge deletes at most ``purge_batch`` rows per table.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = AuthKit.create(AuthSettings(purge_batch=2))
    pw = new_password()
    kit.add_user("alice", pw)
    for _ in range(3):
        kit.services.login.login("alice", Secret(pw), source=SOURCE)
    kit.clock.advance(timedelta(hours=1))
    live = kit.services.login.login("alice", Secret(pw), source=SOURCE).issued
    assert live is not None
    first, second = kit.services.purge_expired(), kit.services.purge_expired()
    assert (first.sessions, second.sessions) == (2, 1)
    assert kit.services.purge_expired().sessions == 0
    assert kit.services.authenticate(live.token) is not None


def test_storage_trouble_on_touch_does_not_sign_the_user_out(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Invariant 2: a failed last-seen write is logged; the request is still authenticated.

    Args:
        monkeypatch: Breaks ``commit``.
        caplog: Captures the warning.

    Returns:
        None

    Raises:
        None
    """
    kit = AuthKit.create()
    issued = _signed_in(kit)
    kit.clock.advance(timedelta(minutes=5))

    def broken(_uow: object) -> None:
        """Fail like a full disk.

        Args:
            _uow: The unit of work (``self``).

        Returns:
            None

        Raises:
            StorageError: Always.
        """
        raise StorageError("the disk or the database is full (SQLITE_FULL)", retryable=False)

    monkeypatch.setattr(MemoryUnitOfWork, "commit", broken)
    assert kit.services.authenticate(issued.token) is not None
    assert "could not record session use" in caplog.text
    assert issued.token.reveal() not in caplog.text
