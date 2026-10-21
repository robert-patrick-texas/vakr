"""Authentication settings: the ``[auth]`` options and fixed bounds (Appendix E.12).

Operators can tune session lifetimes, the kiosk lifetime, the throttle
schedule, and the minimum password length in ``/etc/vakr/config.toml``.
The remaining values are bounds that keep every table and every loop
finite (rule: everything is bounded); they are fixed in code and listed in
Appendix E.12.

Dependencies:
    vakr.config: The effective configuration and option catalog.
    vakr.auth.throttle: The throttle policy record.

Safety:
    Reads configuration only. ``auth_problems`` names options and values,
    never secrets.

Example:
    >>> s = AuthSettings()
    >>> s.session_idle.total_seconds(), s.session_lifetime.total_seconds(), s.throttle.start
    (1800.0, 43200.0, 5)
"""

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from vakr.auth.throttle import ThrottlePolicy
from vakr.config import project
from vakr.config.loader import EffectiveConfig
from vakr.lifecycle.configmerge import parse_duration


@dataclass(frozen=True, slots=True)
class AuthSettings:
    """Everything the authentication services need to know (E.12).

    Attributes:
        session_idle: ``auth.session_idle_timeout``.
        session_lifetime: ``auth.session_lifetime`` (absolute expiry).
        kiosk_lifetime: ``auth.kiosk_token_lifetime``.
        throttle: ``auth.login_throttle_after``, ``login_throttle_max_delay``,
            and ``login_failure_reset``.
        password_min_length: ``auth.password_min_length``.
        touch_interval: Least time between two last-seen writes of one session.
        max_sessions_per_user: Sessions kept per user; the oldest go first.
        max_kiosk_tokens: Kiosk tokens allowed in total.
        max_tracked_failures: Failure runs kept; the quietest go first.
        purge_batch: Rows one purge call deletes per table.
    """

    session_idle: timedelta = timedelta(minutes=30)
    session_lifetime: timedelta = timedelta(hours=12)
    kiosk_lifetime: timedelta = timedelta(days=30)
    throttle: ThrottlePolicy = field(default_factory=ThrottlePolicy)
    password_min_length: int = 15
    touch_interval: timedelta = timedelta(seconds=60)
    max_sessions_per_user: int = 20
    max_kiosk_tokens: int = 100
    max_tracked_failures: int = 10_000
    purge_batch: int = 500


def _get(config: EffectiveConfig, key: str) -> Any:
    """Read one option, falling back to its catalog default (CFGM-001).

    Args:
        config: Effective configuration.
        key: Dotted option key.

    Returns:
        Any: The configured value, or the packaged default.

    Raises:
        KeyError: If the catalog does not declare ``key``.
    """
    option = project.catalog().option(key)
    if option is None:
        raise KeyError(key)
    return config.get(key, option.default)


def _duration(config: EffectiveConfig, key: str) -> timedelta:
    """Read a duration option.

    Args:
        config: Effective configuration.
        key: Dotted option key.

    Returns:
        timedelta: The duration.

    Raises:
        ValueError: If the value is not a duration.
    """
    return timedelta(seconds=parse_duration(_get(config, key)))


def auth_settings_from_config(config: EffectiveConfig) -> AuthSettings:
    """Build authentication settings from the effective configuration.

    Args:
        config: Effective configuration, validated by the catalog.

    Returns:
        AuthSettings: The settings.

    Raises:
        ValueError: If the idle timeout exceeds the session lifetime.
    """
    if problems := auth_problems(config):
        raise ValueError("; ".join(problems))
    throttle = ThrottlePolicy(
        start=int(_get(config, "auth.login_throttle_after")),
        max_delay_s=_duration(config, "auth.login_throttle_max_delay").total_seconds(),
        reset_after_s=_duration(config, "auth.login_failure_reset").total_seconds(),
    )
    return AuthSettings(
        session_idle=_duration(config, "auth.session_idle_timeout"),
        session_lifetime=_duration(config, "auth.session_lifetime"),
        kiosk_lifetime=_duration(config, "auth.kiosk_token_lifetime"),
        throttle=throttle,
        password_min_length=int(_get(config, "auth.password_min_length")),
    )


def auth_problems(config: EffectiveConfig) -> list[str]:
    """Return cross-key problems for ``vakr config validate``.

    Args:
        config: Effective configuration; single-key ranges are already checked.

    Returns:
        list[str]: One line per problem; empty when consistent.

    Raises:
        None
    """
    idle = _duration(config, "auth.session_idle_timeout")
    lifetime = _duration(config, "auth.session_lifetime")
    if idle <= lifetime:
        return []
    shown = _get(config, "auth.session_idle_timeout"), _get(config, "auth.session_lifetime")
    return [
        f"auth.session_idle_timeout ({shown[0]}) must not exceed auth.session_lifetime ({shown[1]})"
    ]
