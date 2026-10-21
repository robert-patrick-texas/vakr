"""The display timezone's IANA name, disclosed with history answers (TIME-002, TIME-003, UI-010).

Stored and returned timestamps are UTC (TIME-001, TIME-004). A graph must
still say which zone its axis is drawn in (12.6, API-006), and a browser
can draw any IANA zone, so the services disclose a *name*: the configured
``time.timezone`` when it is a known IANA zone, else the host's zone
(TIME-002) from ``TZ`` or the ``/etc/localtime`` link, else ``UTC``. A
site's own zone (``Site.timezone``) is disclosed beside it when the
subject belongs to a site that has one, for views that offer site-local
time.

Dependencies:
    os, zoneinfo: Host zone lookup and validation.

Safety:
    Reads one environment variable and one symbolic link; no secrets.

Example:
    >>> zone_name("America/Chicago"), zone_name("Not/AZone", host=False)
    ('America/Chicago', 'UTC')
"""

import os
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC_NAME = "UTC"
_LINK = "/etc/localtime"


def _known(name: str) -> bool:
    """Tell whether ``name`` is an IANA zone this host's tz database knows.

    Args:
        name: Candidate zone name.

    Returns:
        bool: True when ``ZoneInfo`` accepts it.

    Raises:
        None
    """
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError, ValueError:
        return False
    return True


@lru_cache(maxsize=1)
def host_zone_name() -> str:
    """Return the host's IANA zone name, or ``UTC`` when it cannot be told.

    Args:
        None

    Returns:
        str: From ``TZ`` (without a leading ``:``), else the part of the
        ``/etc/localtime`` link after ``zoneinfo/``, else ``UTC``.

    Raises:
        None
    """
    env = os.environ.get("TZ", "").lstrip(":")
    if env and _known(env):
        return env
    try:
        target = str(Path(_LINK).readlink())
    except OSError:
        return UTC_NAME
    _, found, name = target.partition("zoneinfo/")
    return name if found and _known(name) else UTC_NAME


def zone_name(configured: str, *, host: bool = True) -> str:
    """Return the display timezone name (TIME-002, TIME-003).

    Args:
        configured: ``time.timezone``; empty means the host's zone.
        host: Fall back to the host's zone (False falls back to UTC; for tests).

    Returns:
        str: An IANA zone name.

    Raises:
        None
    """
    if configured:
        return configured if _known(configured) else UTC_NAME
    return host_zone_name() if host else UTC_NAME


def site_zone(name: str | None) -> str | None:
    """Return a site's zone when it names a known IANA zone.

    Args:
        name: ``Site.timezone``, if any.

    Returns:
        str | None: The name, or ``None`` when unset or unknown.

    Raises:
        None
    """
    return name if name and _known(name) else None
