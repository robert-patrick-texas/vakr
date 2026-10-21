"""User administration rules: uniqueness, policy, last admin, and audit (SEC-005, SEC-006, SEC-018).

Dependencies:
    pytest; tests.unit.auth.auth_testkit.

Example:
    $ uv run pytest tests/unit/auth/test_accounts.py
"""

import pytest

from tests.unit.auth.auth_testkit import AuthKit, new_password
from vakr.auth import AuthSettings
from vakr.credentials.secret import Secret
from vakr.domain.errors import DuplicateError, NotFoundError, ValidationError
from vakr.domain.users import Role


def test_create_is_case_insensitive_unique_and_audited() -> None:
    """SEC-005, SEC-018: usernames are canonical; creation is audited without the password.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit, pw = AuthKit.create(), new_password()
    alice = kit.services.users.create(" Alice ", Role.ADMIN, Secret(pw), actor="cli:root",
                                      display_name=" Alice Ops ")  # fmt: skip
    assert (alice.username, alice.display_name, alice.role) == ("alice", "Alice Ops", Role.ADMIN)
    with pytest.raises(DuplicateError):
        kit.add_user("ALICE", new_password())
    with pytest.raises(ValidationError, match="username"):
        kit.add_user("bad name", new_password())
    assert kit.audit_actions() == [("user.create", "success", "cli:root")]
    assert pw not in kit.audit_text()
    assert alice.password_hash not in kit.audit_text()


def test_password_policy_applies_with_configured_minimum() -> None:
    """E.12: ``auth.password_min_length`` governs new passwords; messages omit the text.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = AuthKit.create(AuthSettings(password_min_length=20))
    short = "seventeen-chars-x"
    with pytest.raises(ValidationError, match="at least 20") as caught:
        kit.add_user("alice", short)
    assert short not in str(caught.value)
    kit.add_user("alice", "a-passphrase-of-twenty-two")


def test_last_enabled_admin_cannot_be_disabled_or_demoted() -> None:
    """SEC-006: a change that would leave no enabled admin is refused and rolled back.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = AuthKit.create()
    users = kit.services.users
    kit.add_user("alice", new_password())
    kit.add_user("bob", new_password(), Role.OPERATOR)
    for change in (lambda: users.set_enabled("alice", False, actor="t"),
                   lambda: users.set_role("alice", Role.OPERATOR, actor="t")):  # fmt: skip
        with pytest.raises(ValidationError, match="enabled admin"):
            change()
    stored = users.get("alice")
    assert stored is not None
    assert (stored.enabled, stored.role, stored.version) == (True, Role.ADMIN, 1)
    users.set_role("bob", Role.ADMIN, actor="t")
    assert users.set_enabled("alice", False, actor="t").enabled is False
    assert users.set_enabled("alice", False, actor="t").version == 2  # no-op: unchanged
    assert users.admin_exists()


def test_changes_need_an_existing_account_and_are_audited() -> None:
    """SEC-018: every change names its actor; unknown accounts are refused.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = AuthKit.create()
    users = kit.services.users
    kit.add_user("alice", new_password())
    kit.add_user("bob", new_password(), Role.OPERATOR)
    for change in (
        lambda: users.set_password("ghost", Secret(new_password()), actor="t"),
        lambda: users.set_enabled("ghost", False, actor="t"),
        lambda: users.set_role("ghost", Role.ADMIN, actor="t"),
        lambda: users.revoke_sessions("ghost", actor="t"),
    ):
        with pytest.raises(NotFoundError):
            change()
    users.set_password("bob", Secret(new_password()), actor="cli:alice (sudo)")
    users.set_enabled("bob", False, actor="alice")
    users.set_enabled("bob", True, actor="alice")
    users.set_role("bob", Role.ADMIN, actor="alice")
    users.revoke_sessions("bob", actor="alice")
    assert [a for a, _, _ in kit.audit_actions()] == [
        "user.create", "user.create", "user.password", "user.disable", "user.enable",
        "user.role", "user.revoke_sessions",
    ]  # fmt: skip
    assert [u.username for u in users.users()] == ["alice", "bob"]
    users.set_enabled("bob", False, actor="alice")
    assert [u.username for u in users.users(include_disabled=False)] == ["alice"]


def test_admin_exists_only_counts_enabled_admins() -> None:
    """``vakr setup``: an operator or a disabled admin does not count as an admin.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = AuthKit.create()
    assert not kit.services.users.admin_exists()
    kit.add_user("bob", new_password(), Role.OPERATOR)
    assert not kit.services.users.admin_exists()
    kit.add_user("alice", new_password())
    assert kit.services.users.admin_exists()
