"""Interface-keyword safety check used by ``vakr config validate`` (POLICY-005).

The portable configuration library only checks that a ``re:`` keyword
compiles. vakr's policy package also rejects patterns that could backtrack
catastrophically, so validation runs both: an unsafe keyword is caught when
the operator validates, not when a service starts.

Dependencies:
    vakr.policy.keywords: Builds and validates the effective keyword list.

Safety:
    Messages name the keyword's path and the reason, never anything secret
    (keywords are not secrets).

Example:
    >>> from vakr.config.loader import EffectiveConfig
    >>> keyword_problems(EffectiveConfig({}))
    []
"""

from vakr.config.loader import EffectiveConfig
from vakr.policy.errors import PolicyError
from vakr.policy.keywords import keywords_from_config


def keyword_problems(config: EffectiveConfig) -> list[str]:
    """Return one ``path: reason`` line per invalid or unsafe keyword.

    Args:
        config: Effective configuration to check.

    Returns:
        list[str]: Problems; empty when every keyword is valid and safe.

    Raises:
        None
    """
    try:
        keywords_from_config(config)
    except PolicyError as exc:
        return [f"{issue.path}: {issue.reason}" for issue in exc.issues]
    return []
