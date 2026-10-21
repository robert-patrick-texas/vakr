"""Kiosk labels and per-token lifetimes (Appendix E.12).

Dependencies:
    pytest; vakr.auth.kiosk_rules; tests.unit.auth.auth_testkit.

Example:
    $ uv run pytest tests/unit/auth/test_kiosk_rules.py
"""

from datetime import timedelta

import pytest

from tests.unit.auth.auth_testkit import AuthKit
from vakr.auth.kiosk_rules import check_label, parse_lifetime
from vakr.credentials.secret import Secret
from vakr.domain.errors import ValidationError


@pytest.mark.parametrize("label", ["noc-wall", "NOC wall 2", "a", "lobby_tv.1", "x" * 64])
def test_e12_good_labels(label: str) -> None:
    """E.12: letters, digits, space, '.', '_', '-', starting with a letter or digit.

    Args:
        label: A label that follows the rule.

    Returns:
        None

    Raises:
        None
    """
    assert check_label(label) == label


@pytest.mark.parametrize(
    "label", ["", " wall", "wall ", "-wall", "wall\t2", "wäll", "x" * 65, "a/b", "<b>"]
)
def test_e12_bad_labels_are_refused_without_echo(label: str) -> None:
    """E.12: anything else is refused, and the message states the rule only.

    Args:
        label: A label that breaks the rule.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValidationError) as exc:
        check_label(label)
    if label.strip():
        assert label not in str(exc.value)


@pytest.mark.parametrize(
    ("text", "days"), [("1d", 1), ("24h", 1), ("90d", 90), ("365d", 365), (" 30d ", 30)]
)
def test_e12_lifetimes_within_one_to_365_days(text: str, days: int) -> None:
    """E.12: the range of auth.kiosk_token_lifetime, written as a configuration duration.

    Args:
        text: The duration.
        days: Its length in days.

    Returns:
        None

    Raises:
        None
    """
    assert parse_lifetime(text) == timedelta(days=days)


@pytest.mark.parametrize("text", ["23h", "366d", "99999999999999999d", "soon", "", "-1d"])
def test_e12_lifetimes_outside_the_range_or_malformed_are_refused(text: str) -> None:
    """E.12: shorter than a day, longer than a year, or not a duration (no overflow either).

    Args:
        text: The duration.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValidationError):
        parse_lifetime(text)


def test_e12_create_takes_a_lifetime_and_exchange_is_audited() -> None:
    """E.12, SEC-018: a per-token lifetime; setup names the display and the client address.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = AuthKit.create()
    issued = kit.services.kiosk.create("wall", actor="alice", lifetime=timedelta(days=7))
    assert issued.record.expires_at - issued.record.created_at == timedelta(days=7)
    found = kit.services.kiosk.exchange(issued.token, source="192.0.2.7", request_id="r1")
    assert found is not None
    principal, record = found
    assert (principal.name, record.label) == ("kiosk:wall", "wall")
    assert kit.audit_actions()[-1] == ("kiosk.setup", "success", "kiosk:wall")
    assert "192.0.2.7" in kit.audit_text()
    assert issued.token.reveal() not in kit.audit_text()
    assert kit.services.kiosk.exchange(Secret("vkk_" + "x" * 43), source="192.0.2.7") is None
    assert kit.services.kiosk.find("wall") == kit.services.kiosk.get(record.id)
