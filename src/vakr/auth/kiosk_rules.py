"""Rules for kiosk token labels and lifetimes, shared by the API and ``vakr kiosk`` (Appendix E.12).

Labels are 1 to 64 characters of ASCII letters, digits, space, ``.``,
``_``, and ``-``, starting with a letter or digit and not ending with a
space. They appear in audit records and in the display's principal name
(``kiosk:<label>``), and an administrator types one to revoke a token, so
they stay plain and printable.

A token may live 1 to 365 days, the range of ``auth.kiosk_token_lifetime``,
which is the default. Lifetimes are written as configuration durations:
``30d``, ``12h``, ``3600s``.

Dependencies:
    re, datetime: The label pattern and lifetimes.
    vakr.lifecycle.configmerge: The configuration's duration syntax.

Safety:
    Error messages state the rule, never the text that broke it.

Example:
    >>> check_label("noc-wall")
    'noc-wall'
    >>> parse_lifetime("90d").days
    90
"""

import re
from datetime import timedelta

from vakr.domain.errors import ValidationError
from vakr.lifecycle.configmerge import parse_duration

MIN_LIFETIME = timedelta(days=1)
MAX_LIFETIME = timedelta(days=365)
"""Per-token lifetime bounds, the range of ``auth.kiosk_token_lifetime`` (E.12)."""
MAX_LABEL = 64
_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9 ._-]{0,62}[A-Za-z0-9._-])?")


def check_label(label: object) -> str:
    """Return a kiosk label if it follows the rule (module docstring), else refuse it.

    Args:
        label: The label as given.

    Returns:
        str: The same label.

    Raises:
        ValidationError: If it breaks the rule; the message never repeats it.
    """
    if not isinstance(label, str) or _LABEL.fullmatch(label) is None:
        raise ValidationError(
            f"a kiosk label is 1 to {MAX_LABEL} characters of letters, digits, space, '.', "
            "'_', and '-', starting with a letter or digit and not ending with a space",
            field="label",
        )
    return label


def check_lifetime(lifetime: timedelta) -> timedelta:
    """Return a per-token lifetime if it is within 1 to 365 days, else refuse it.

    Args:
        lifetime: The requested lifetime.

    Returns:
        timedelta: The same lifetime.

    Raises:
        ValidationError: If it is out of range.
    """
    if not MIN_LIFETIME <= lifetime <= MAX_LIFETIME:
        raise ValidationError("a kiosk token lives 1 to 365 days", field="lifetime")
    return lifetime


def parse_lifetime(text: str | None) -> timedelta | None:
    """Read a lifetime such as ``90d`` and check its range.

    Args:
        text: The duration as typed, or ``None`` for the configured default.

    Returns:
        timedelta | None: The lifetime, or ``None`` when ``text`` is ``None``.

    Raises:
        ValidationError: If it is not a duration or is out of range.
    """
    if text is None:
        return None
    try:
        seconds = parse_duration(text.strip())
    except TypeError, ValueError:
        raise ValidationError(
            "lifetime is a duration such as 30d, 12h, or 3600s", field="lifetime"
        ) from None
    if seconds > MAX_LIFETIME.total_seconds():  # before timedelta, which overflows on huge input
        raise ValidationError("a kiosk token lives 1 to 365 days", field="lifetime")
    return check_lifetime(timedelta(seconds=seconds))
