"""Typed field readers for ``inventory.toml`` tables (11.4, blueprint 17.6).

Each reader trims strings first, because a stray trailing space must not
become a different DNS name or credential reference, then checks one kind
of value: a bounded single-line name, a tag or group label (the E.15 tag
rule), a duration within the E.18 policy limits, a UUID, a comment, or a
poll address. IP literals are written canonically, so ``2001:DB8::1`` and
``2001:db8::1`` are the same device address. Problems go to the shared
``TableReader`` issue list with their path.

Dependencies:
    vakr.policy.reader, settings: Typed reads and the E.18 limits.
    vakr.domain: Enums and the poll-address rule.

Safety:
    Pure: no I/O.

Example:
    >>> from vakr.policy.reader import TableReader
    >>> issues = []
    >>> address(TableReader({"address": " 2001:DB8::1 "}, "devices[0]", issues, {"address"}), None)
    '2001:db8::1'
"""

import ipaddress
import re
from enum import StrEnum
from uuid import UUID

from vakr.domain.addressing import require_poll_address
from vakr.domain.enums import AddressFamily
from vakr.domain.errors import ValidationError
from vakr.domain.ids import parse_uuid
from vakr.lifecycle.configmerge.values import parse_duration
from vakr.policy.reader import TableReader
from vakr.policy.settings import SETTING_LIMITS, format_seconds

MAX_NAME = 255  # long enough for a FQDN; bounded so a pasted blob cannot become a name
MAX_COMMENT = 4_096

_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")  # the keyword tag rule (E.15)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def name(r: TableReader, key: str, *, required: bool = False) -> str | None:
    """Read a trimmed single-line name of at most ``MAX_NAME`` characters.

    Args:
        r: The table reader.
        key: Key name.
        required: Whether the key must be present.

    Returns:
        str | None: The name, or ``None`` when absent or invalid.

    Raises:
        None
    """
    raw = r.text(key, required=required)
    value = None if raw is None else raw.strip()
    if raw is not None and (not value or len(value) > MAX_NAME or _CONTROL.search(value)):
        r.problem(key, f"must be 1-{MAX_NAME} characters on one line")
        return None
    return value


def labels(r: TableReader, key: str) -> tuple[str, ...]:
    """Read a list of tag or group names, sorted and without repeats.

    Args:
        r: The table reader.
        key: Key name.

    Returns:
        tuple[str, ...]: Valid names in code-point order.

    Raises:
        None
    """
    names = tuple(sorted({n.strip() for n in r.strings(key)}))
    for name in names:
        if not _LABEL.fullmatch(name):
            r.problem(key, f"{name!r}: use 1-64 letters, digits, '.', '_', ':' or '-'")
    return tuple(n for n in names if _LABEL.fullmatch(n))


def enum_value[E: StrEnum](r: TableReader, key: str, kind: type[E]) -> E | None:
    """Read one value of a string enum.

    Args:
        r: The table reader.
        key: Key name.
        kind: The enum.

    Returns:
        E | None: The member, or ``None`` when absent or invalid.

    Raises:
        None
    """
    value = r.choice(key, [m.value for m in kind])
    return None if value is None else kind(value)


def seconds(r: TableReader, key: str, setting: str) -> int | None:
    """Read a duration within the policy limits of ``setting`` (E.18).

    Args:
        r: The table reader.
        key: Key name.
        setting: Policy setting whose range applies.

    Returns:
        int | None: Whole seconds, or ``None`` when absent or invalid.

    Raises:
        None
    """
    text = r.text(key)
    if text is None:
        return None
    limit = SETTING_LIMITS[setting]
    try:
        seconds = parse_duration(text)
    except ValueError as exc:
        r.problem(key, str(exc))
        return None
    if not limit.low <= seconds <= limit.high or seconds != int(seconds):
        r.problem(key, f"must be whole seconds between {format_seconds(limit.low)} and "
                       f"{format_seconds(limit.high)}")  # fmt: skip
        return None
    return int(seconds)


def uuid_value(r: TableReader, key: str) -> UUID | None:
    """Read an optional explicit UUID.

    Args:
        r: The table reader.
        key: Key name.

    Returns:
        UUID | None: The UUID, or ``None`` when absent or invalid.

    Raises:
        None
    """
    text = r.text(key)
    if text is None:
        return None
    try:
        return parse_uuid(text.strip(), key)
    except ValidationError as exc:
        r.problem(key, str(exc))
        return None


def comment(r: TableReader) -> str:
    """Read a free-form comment of at most ``MAX_COMMENT`` characters.

    Args:
        r: The table reader.

    Returns:
        str: The comment, trimmed; empty when absent or invalid.

    Raises:
        None
    """
    text = (r.text("comment") or "").strip()
    if len(text) > MAX_COMMENT:
        r.problem("comment", f"at most {MAX_COMMENT} characters")
        return ""
    return text


def address(r: TableReader, family: AddressFamily | None) -> str | None:
    """Read the poll address: a canonical IP literal or an RFC 1123 host name.

    Args:
        r: The table reader.
        family: The preferred family, if set.

    Returns:
        str | None: The address, IP literals in canonical form.

    Raises:
        None
    """
    text = name(r, "address", required=True)
    if text is None:
        return None
    try:
        text = str(ipaddress.ip_address(text))
    except ValueError:
        text = text.lower()
    try:
        require_poll_address(text, family)
    except ValidationError as exc:
        r.problem("address", str(exc).removeprefix("poll_address "))
        return None
    return text
