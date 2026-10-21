"""Password login: outcomes, throttling per account and address, rehash, rotation, audit.

Covers SEC-009 (rehash on login), SEC-010 (rotation after login), SEC-012
and AUTH-004 (progressive delay without revealing whether an account
exists), and SEC-018 (audit without passwords).

Dependencies:
    pytest; tests.unit.auth.auth_testkit (memory adapter, manual clock, cheap Argon2).

Example:
    $ uv run pytest tests/unit/auth/test_login.py
"""

from datetime import timedelta

from tests.unit.auth.auth_testkit import SOURCE, AuthKit, cheap_hasher, new_password
from vakr.auth import AuthKeys, LoginOutcome, Passwords, build_auth_services
from vakr.credentials.secret import Secret
from vakr.domain.users import Role

OTHER = "198.51.100.7"


def _fail(kit: AuthKit, username: str, source: str | None = SOURCE) -> tuple[LoginOutcome, float]:
    """Attempt a login with a wrong password.

    Args:
        kit: The services.
        username: Account to try.
        source: Client address.

    Returns:
        tuple[LoginOutcome, float]: Outcome and delay (or retry-after when throttled).

    Raises:
        None
    """
    result = kit.services.login.login(username, Secret("wrong-" + new_password()), source=source)
    return result.outcome, result.delay_s or result.retry_after_s


def test_success_starts_a_session_and_audits_without_the_password() -> None:
    """SEC-010, SEC-018: a correct password issues a session and an audit event, no secret.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit, pw = AuthKit.create(), new_password()
    kit.add_user("Alice", pw)
    result = kit.services.login.login(" ALICE ", Secret(pw), source=SOURCE, request_id="req-1")
    assert result.outcome is LoginOutcome.SUCCESS
    assert result.issued is not None
    principal = kit.services.authenticate(result.issued.token)
    assert principal is not None
    assert (principal.name, principal.role) == ("alice", Role.ADMIN)
    assert kit.audit_actions()[-1] == ("auth.login", "success", "alice")
    assert pw not in kit.audit_text()
    assert result.issued.token.reveal() not in kit.audit_text()
    assert pw not in repr(result)
    assert result.issued.token.reveal() not in repr(result)


def test_auth004_unknown_and_known_accounts_fail_alike() -> None:
    """AUTH-004: an unknown account and a wrong password give the same outcome and delays.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = AuthKit.create()
    kit.add_user("alice", new_password())
    known = [_fail(kit, "alice") for _ in range(6)]
    kit.clock.advance(timedelta(hours=1))
    unknown = [_fail(kit, "mallory") for _ in range(6)]
    assert known == unknown
    assert [d for _, d in known] == [0.0, 0.0, 0.0, 0.0, 1.0, 1.0]  # 6th arrives inside the wait
    assert known[-1][0] is LoginOutcome.THROTTLED
    actions = kit.audit_actions()
    assert ("auth.login", "failure", "alice") in actions
    assert ("auth.login", "failure", "anonymous") in actions
    assert "mallory" not in kit.audit_text()  # a mistyped password could sit in that field


def test_auth004_delay_grows_per_account_and_source_and_success_clears() -> None:
    """AUTH-004, E.12: 4 failures free, 5th 1 s, doubling to 30 s; per account and address.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit, pw = AuthKit.create(), new_password()
    kit.add_user("alice", pw)
    kit.add_user("bob", new_password())
    delays: list[float] = []
    for _ in range(11):
        if delays:  # the client waits as long as the last answer said
            kit.clock.advance(timedelta(seconds=delays[-1]))
        outcome, delay = _fail(kit, "alice")
        assert outcome is LoginOutcome.FAILED
        delays.append(delay)
    assert delays == [0, 0, 0, 0, 1, 2, 4, 8, 16, 30, 30]
    assert _fail(kit, "alice")[0] is LoginOutcome.THROTTLED  # no wait elapsed since the 11th
    assert _fail(kit, "alice", OTHER) == (LoginOutcome.FAILED, 0.0)  # another address
    assert _fail(kit, "bob") == (LoginOutcome.FAILED, 0.0)  # another account
    kit.clock.advance(timedelta(seconds=30))
    ok = kit.services.login.login("alice", Secret(pw), source=SOURCE)
    assert ok.outcome is LoginOutcome.SUCCESS
    assert _fail(kit, "alice") == (LoginOutcome.FAILED, 0.0)  # the run was cleared


def test_auth004_throttled_attempts_skip_the_password_check() -> None:
    """AUTH-004: inside the wait even the right password is refused, and nothing is hashed.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit, pw = AuthKit.create(), new_password()
    kit.add_user("alice", pw)
    for _ in range(5):
        _fail(kit, "alice")
    audit_before = len(kit.audit_actions())
    result = kit.services.login.login("alice", Secret(pw), source=SOURCE)
    assert (result.outcome, result.retry_after_s, result.issued) == (
        LoginOutcome.THROTTLED, 1.0, None
    )  # fmt: skip
    assert len(kit.audit_actions()) == audit_before  # a flood adds no audit rows
    kit.clock.advance(timedelta(seconds=1))
    assert kit.services.login.login("alice", Secret(pw), source=SOURCE).outcome is (
        LoginOutcome.SUCCESS
    )  # fmt: skip


def test_auth004_quiet_run_is_forgotten_after_the_reset_window() -> None:
    """E.12: 15 minutes without a failure restarts the count.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = AuthKit.create()
    for _ in range(5):
        _fail(kit, "alice")
    kit.clock.advance(timedelta(minutes=15))
    assert _fail(kit, "alice") == (LoginOutcome.FAILED, 0.0)


def test_disabled_account_fails_like_a_wrong_password_and_is_audited_denied() -> None:
    """SEC-005, AUTH-004: a disabled account cannot sign in; only the audit says why.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit, pw = AuthKit.create(), new_password()
    kit.add_user("root-admin", new_password())
    kit.add_user("bob", pw, Role.OPERATOR)
    kit.services.users.set_enabled("bob", False, actor="test")
    result = kit.services.login.login("bob", Secret(pw), source=SOURCE)
    assert (result.outcome, result.issued) == (LoginOutcome.FAILED, None)
    assert kit.audit_actions()[-1] == ("auth.login", "denied", "bob")


def test_sec009_login_rehashes_when_parameters_change() -> None:
    """SEC-009: a hash with old parameters is replaced on the next successful login.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit, pw = AuthKit.create(), new_password()
    old = kit.add_user("alice", pw)
    upgraded = build_auth_services(
        kit.factory, clock=kit.clock, keys=AuthKeys.from_master(bytes(32)),
        passwords=Passwords(cheap_hasher(time_cost=2)),
    )  # fmt: skip
    assert upgraded.login.login("alice", Secret(pw), source=SOURCE).outcome is LoginOutcome.SUCCESS
    with kit.factory() as uow:
        stored = uow.users.get(old.id)
    assert stored is not None
    assert stored.password_hash != old.password_hash
    assert "t=2" in stored.password_hash
    assert stored.version == old.version  # a login is not an administrative change
    assert "upgraded" in kit.audit_text()
    assert upgraded.login.login("alice", Secret(pw), source=SOURCE).outcome is LoginOutcome.SUCCESS


def test_sec010_login_ends_the_presented_session() -> None:
    """SEC-010: logging in again with an old cookie rotates: the old session ends.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit, pw = AuthKit.create(), new_password()
    kit.add_user("alice", pw)
    first = kit.services.login.login("alice", Secret(pw), source=SOURCE).issued
    assert first is not None
    second = kit.services.login.login("alice", Secret(pw), source=SOURCE, replaces=first.token)
    assert second.issued is not None
    assert kit.services.authenticate(first.token) is None
    assert kit.services.authenticate(second.issued.token) is not None
    assert second.issued.session.id != first.session.id


def test_malformed_source_is_stored_as_unknown() -> None:
    """SEC-012: a missing or odd client address still throttles, under ``unknown``.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = AuthKit.create()
    for source in (None, "two words", "x" * 300):
        assert _fail(kit, "alice", source)[0] is LoginOutcome.FAILED
    with kit.factory() as uow:
        assert uow.login_failures.count() == 1
