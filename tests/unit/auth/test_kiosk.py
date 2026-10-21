"""Kiosk tokens: operator role, no idle expiry, 30-day absolute expiry, revocation (E.12).

Dependencies:
    pytest; tests.unit.auth.auth_testkit.

Example:
    $ uv run pytest tests/unit/auth/test_kiosk.py
"""

from datetime import timedelta

import pytest

from tests.unit.auth.auth_testkit import AuthKit
from vakr.auth import AuthSettings, Permission, PrincipalKind, allows
from vakr.domain.errors import DuplicateError, ValidationError
from vakr.domain.users import Role

US = timedelta(microseconds=1)


def test_e12_kiosk_token_is_operator_without_idle_expiry() -> None:
    """E.12: a kiosk token views only, survives 29 idle days, and ends at exactly 30 days.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = AuthKit.create()
    issued = kit.services.kiosk.create("noc-wall", actor="alice", request_id="req-1")
    principal = kit.services.authenticate(issued.token)
    assert principal is not None
    assert (principal.kind, principal.name, principal.role) == (
        PrincipalKind.KIOSK, "kiosk:noc-wall", Role.OPERATOR
    )  # fmt: skip
    assert not allows(principal.role, Permission.ADMINISTER)
    kit.clock.advance(timedelta(days=30) - US)
    assert kit.services.authenticate(issued.token) is not None  # no idle expiry
    kit.clock.advance(US)
    assert kit.services.authenticate(issued.token) is None
    assert kit.audit_actions() == [("kiosk.create", "success", "alice")]
    assert issued.token.reveal() not in kit.audit_text()


def test_kiosk_use_is_recorded_at_most_once_per_interval() -> None:
    """E.12: the last-used time is written on first use, then at most once a minute.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = AuthKit.create()
    issued = kit.services.kiosk.create("lobby", actor="alice")
    start = kit.clock.now_utc()
    kit.services.authenticate(issued.token)
    kit.clock.advance(timedelta(seconds=30))
    kit.services.authenticate(issued.token)
    (stored,) = kit.services.kiosk.tokens()
    assert stored.last_used_at == start


def test_kiosk_revoke_labels_and_limit() -> None:
    """E.12: revocation is immediate and audited; labels are unique; the count is bounded.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = AuthKit.create(AuthSettings(max_kiosk_tokens=2))
    wall = kit.services.kiosk.create("noc-wall", actor="alice")
    with pytest.raises(DuplicateError):
        kit.services.kiosk.create("noc-wall", actor="alice")
    with pytest.raises(ValidationError):
        kit.services.kiosk.create("bad\tlabel", actor="alice")
    kit.services.kiosk.create("lobby", actor="alice")
    with pytest.raises(ValidationError, match="at most 2"):
        kit.services.kiosk.create("third", actor="alice")
    assert kit.services.kiosk.revoke(wall.record.id, actor="alice")
    assert not kit.services.kiosk.revoke(wall.record.id, actor="alice")
    assert kit.services.authenticate(wall.token) is None
    assert [t.label for t in kit.services.kiosk.tokens()] == ["lobby"]
    assert kit.audit_actions()[-1] == ("kiosk.revoke", "success", "alice")
    assert "token_hash" not in repr(wall)
    assert wall.token.reveal() not in repr(wall)
