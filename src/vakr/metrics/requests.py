"""API request counts and durations by fixed route group (OBS-004 query latency, E.33).

Every answer of ``vakr-api`` is counted once, by the route group its path
falls in and its status code, and its duration joins that group's summary.
Groups and codes are fixed sets: a path never becomes a label, so a client
asking for a million different URLs adds no series (E.33 #7). A code
outside the set counts as ``other``; a request the client abandoned before
an answer counts as ``none``.

Sign-in failures and throttling are ``group="login"`` with codes ``401``
and ``429``; a full sign-in queue is ``429`` too (E.30).

Dependencies:
    vakr.metrics.catalog, window: Families and summaries.

Safety:
    Paths are matched, never stored; nothing about the caller is kept.

Example:
    >>> route_group("/api/v1/interfaces/x/history/traffic"), route_group("/devices")
    ('history', 'web')
"""

from collections import Counter

from vakr.logic.window import WINDOW, Summary
from vakr.metrics.catalog import build, summary
from vakr.metrics.catalog_spec import ROUTE_GROUPS, STATUS_CODES
from vakr.metrics.exposition import Family

_PREFIXES = (
    ("/api/v1/metrics", "metrics"),
    ("/api/v1/auth/login", "login"),
    ("/api/v1/auth/", "auth"),
    ("/api/v1/kiosk-tokens", "kiosk"),
    ("/api/v1/health/", "health"),
    ("/api/v1/version", "health"),
    ("/api/v1/exports/", "exports"),
    ("/api/v1/rankings/", "rankings"),
    ("/api/v1/dashboards", "dashboards"),
    ("/api/v1/operations", "operations"),
)
_WINDOW = WINDOW // 4
"""Duration samples kept per group: twelve groups stay under 50,000 floats."""


def route_group(path: str) -> str:
    """Return the fixed group a request path belongs to.

    Args:
        path: The request path (no query string).

    Returns:
        str: One of ``ROUTE_GROUPS``.

    Raises:
        None
    """
    for prefix, group in _PREFIXES:
        if path.startswith(prefix):
            return group
    if not path.startswith("/api/"):
        return "web"
    if "/history/" in path:
        return "history"
    if path.endswith("/rediscovery"):
        return "operations"
    return "reads"


def status_code(status: int) -> str:
    """Return the label of a status code.

    Args:
        status: The HTTP status; 0 when no answer started.

    Returns:
        str: The code, ``other``, or ``none``.

    Raises:
        None
    """
    if status == 0:
        return "none"
    text = str(status)
    return text if text in STATUS_CODES else "other"


class RequestStats:
    """Counts and durations of the answers one ``vakr-api`` process gave."""

    def __init__(self) -> None:
        """Start with nothing counted.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._counts: Counter[tuple[str, str]] = Counter()
        self._durations = {group: Summary(_WINDOW) for group in sorted(ROUTE_GROUPS)}

    def observe(self, path: str, status: int, seconds: float) -> None:
        """Count one answer.

        Args:
            path: The request path.
            status: Its status (0 when the client went away first).
            seconds: How long it took.

        Returns:
            None

        Raises:
            None
        """
        group = route_group(path)
        self._counts[group, status_code(status)] += 1
        self._durations[group].observe(seconds)

    def families(self) -> list[Family]:
        """Return the request families.

        Args:
            None

        Returns:
            list[Family]: Counts and duration summaries; groups never asked are absent.

        Raises:
            None
        """
        counts = build("vakr_api_requests_total",
                       ((key, n) for key, n in sorted(self._counts.items())))  # fmt: skip
        rows = (((g,), s.quantiles()) for g, s in self._durations.items())
        durations = summary("vakr_api_request_duration_seconds", rows)
        return [counts, durations]
