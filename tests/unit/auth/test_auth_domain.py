"""Domain invariants of accounts, sessions, kiosk tokens, and failure runs (7.1, E.12).

Dependencies:
    pytest; vakr.domain.users, sessions; tests.contract.contract_auth_samples.

Example:
    $ uv run pytest tests/unit/auth/test_auth_domain.py
"""

from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest

from tests.contract import contract_auth_samples as a
from tests.contract.contract_samples import T0
from vakr.domain.errors import ValidationError
from vakr.domain.ids import UserId, new_id
from vakr.domain.users import LoginFailure, User, canonical_username, require_source


@pytest.mark.parametrize(
    ("text", "expected"),
    [(" Alice ", "alice"), ("ops.admin@example.com", "ops.admin@example.com"), ("A-1_b", "a-1_b")],
)
def test_canonical_usernames(text: str, expected: str) -> None:
    """E.12: usernames are trimmed and lower-cased.

    Args:
        text: As typed.
        expected: Canonical form.

    Returns:
        None

    Raises:
        None
    """
    assert canonical_username(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "-lead", "two words", "x" * 65, "café", 7])
def test_invalid_usernames_are_refused_without_echo(text: object) -> None:
    """E.12: bad usernames fail; the message never repeats the text.

    Args:
        text: Rejected input.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValidationError) as caught:
        canonical_username(text)
    assert not isinstance(text, str) or not text.strip() or text not in str(caught.value)


@pytest.mark.parametrize(
    "change",
    [
        {"username": "Alice"},
        {"display_name": "x" * 129},
        {"display_name": " padded"},
        {"password_hash": "plaintext"},
        {"password_hash": "$" + "x" * 600},
        {"last_login_at": T0.replace(tzinfo=None)},
        {"version": 0},
    ],
)
def test_user_invariants(change: dict[str, Any]) -> None:
    """7.1 LocalUser: every field rule holds; a bad hash is refused without being quoted.

    Args:
        change: Field values that break a rule.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValidationError) as caught:
        a.user(**change)
    if "password_hash" in change:
        assert change["password_hash"] not in str(caught.value)


def test_user_label_prefers_the_display_name() -> None:
    """UI: the display name is shown when set, else the username.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert a.user(display_name="Alice Ops").label == "Alice Ops"
    assert a.user(display_name="").label == "alice"


@pytest.mark.parametrize(
    "change",
    [
        {"token_hash": b"short"},
        {"last_seen_at": T0 - timedelta(seconds=1)},
        {"expires_at": T0},
        {"source": "two words"},
        {"source": "x" * 65},
    ],
)
def test_session_invariants(change: dict[str, Any]) -> None:
    """SEC-010: digests are 32 bytes, times are ordered, and the source is a short token.

    Args:
        change: Field values that break a rule.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValidationError):
        a.session(new_id(UserId), **change)


@pytest.mark.parametrize(
    "change",
    [{"label": "x" * 65}, {"expires_at": T0}, {"last_used_at": T0.replace(tzinfo=None)}],
)
def test_kiosk_invariants(change: dict[str, Any]) -> None:
    """E.12: labels are bounded and expiry follows creation.

    Args:
        change: Field values that break a rule.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValidationError):
        a.kiosk(**change)


def test_failure_run_invariants() -> None:
    """SEC-012: account keys are 64 hex digits; the run's times are ordered.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    good = LoginFailure(a.account_key(), a.SOURCE, 1, T0, T0)
    for change in ({"account_key": "Alice"}, {"failures": 0},
                   {"last_failed_at": T0 - timedelta(seconds=1)}):  # fmt: skip
        with pytest.raises(ValidationError):
            replace(good, **change)
    with pytest.raises(ValidationError):
        require_source("192.0.2.1\x00")


def test_reprs_omit_hashes_and_digests() -> None:
    """CRED-002: ``repr`` of each record leaves out the hash or digest field.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    user: User = a.user()
    session, kiosk = a.session(user.id), a.kiosk()
    assert "password_hash" not in repr(user)
    assert user.password_hash not in repr(user)
    for record in (session, kiosk):
        assert "token_hash" not in repr(record)
        assert record.token_hash.hex() not in repr(record)
