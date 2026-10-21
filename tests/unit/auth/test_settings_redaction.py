"""Auth settings match Appendix E.12, and no record or result shows a secret (CRED-002, SEC-003).

Dependencies:
    pytest; vakr.auth; vakr.config; tests.unit.auth.auth_testkit.

Example:
    $ uv run pytest tests/unit/auth/test_settings_redaction.py
"""

from datetime import timedelta

import pytest

from tests.unit.auth.auth_testkit import SOURCE, AuthKit, new_password
from vakr.auth import AuthSettings, auth_problems, auth_settings_from_config
from vakr.config.cross_checks import first_problem
from vakr.config.loader import EffectiveConfig
from vakr.credentials.secret import Secret


def test_e12_defaults_from_the_catalog_match_the_dataclass() -> None:
    """E.12: packaged ``[auth]`` defaults build the same settings as ``AuthSettings()``.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    built = auth_settings_from_config(EffectiveConfig({}))
    assert built == AuthSettings()
    assert (built.session_idle, built.session_lifetime) == (
        timedelta(minutes=30), timedelta(hours=12)
    )  # fmt: skip
    assert (built.kiosk_lifetime, built.throttle.start, built.throttle.max_delay_s) == (
        timedelta(days=30), 5, 30.0
    )  # fmt: skip


def test_configured_values_and_the_idle_rule() -> None:
    """CONF-009, 11.4: settings follow the file; an idle timeout above the lifetime is refused.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    tuned = EffectiveConfig({"auth": {"session_idle_timeout": "10m", "login_throttle_after": 3,
                                      "password_min_length": 20}})  # fmt: skip
    settings = auth_settings_from_config(tuned)
    assert (settings.session_idle, settings.throttle.start, settings.password_min_length) == (
        timedelta(minutes=10), 3, 20
    )  # fmt: skip
    wrong = EffectiveConfig({"auth": {"session_idle_timeout": "2h", "session_lifetime": "1h"}})
    assert auth_problems(wrong) == [
        "auth.session_idle_timeout (2h) must not exceed auth.session_lifetime (1h)"
    ]
    assert first_problem(wrong) == ("inconsistent auth settings", auth_problems(wrong))
    with pytest.raises(ValueError, match="must not exceed"):
        auth_settings_from_config(wrong)


def test_cred002_no_repr_shows_a_password_hash_or_token() -> None:
    """CRED-002: users, sessions, kiosk tokens, and login results render without secrets.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit, pw = AuthKit.create(), new_password()
    user = kit.add_user("alice", pw)
    result = kit.services.login.login("alice", Secret(pw), source=SOURCE)
    kiosk = kit.services.kiosk.create("noc-wall", actor="alice")
    assert result.issued is not None
    secrets = (pw, user.password_hash, result.issued.token.reveal(), kiosk.token.reveal(),
               result.issued.session.token_hash.hex(), repr(result.issued.session.token_hash),
               kiosk.record.token_hash.hex())  # fmt: skip
    rendered = " ".join(
        f"{obj!r} {obj!s}" for obj in (user, result, result.issued, kiosk, kiosk.record)
    )
    for secret in secrets:
        assert secret not in rendered
    assert "Secret('***')" in rendered
