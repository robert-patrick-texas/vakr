"""Whether any collector has reported lately, for commands that queue work for one (DISC-010).

``vakr inventory rediscover`` queues an operation a collector picks up. If
no collector has written a fresh health report (E.3: 150 s at the 60 s
default interval), nothing will pick it up soon, and the command says so at
once instead of leaving the operator waiting. The answer comes from the
same status views ``vakr status`` reads in-process (E.29 #4).

Dependencies:
    vakr.cli.status_stores: Opens the databases and the status queries.

Safety:
    Read-only; any failure to read gives ``None`` (unknown), never an error.

Example:
    >>> collectors_reporting.__name__
    'collectors_reporting'
"""

from vakr.cli.status_stores import NoDataYetError, open_status
from vakr.domain.errors import DomainError
from vakr.paths import InstallPaths
from vakr.ports.migrations import MigrationError
from vakr.query import DeviceListRequest

_GONE = frozenset({None, "unavailable"})
"""Collector states that mean no fresh health report: none at all, or one past E.3."""


def collectors_reporting(command: str) -> bool | None:
    """Report whether at least one collector has a fresh health report.

    Args:
        command: Command name for configuration warnings.

    Returns:
        bool | None: True or False (no telemetry database means no report);
        ``None`` when the metadata database cannot be read.

    Raises:
        None
    """
    try:
        stores = open_status(InstallPaths.from_environment(), command)
    except NoDataYetError, MigrationError, DomainError, OSError:
        return None
    try:
        health = stores.queries.device_list(DeviceListRequest(limit=1)).health
    except DomainError:
        return None
    finally:
        stores.close()
    return any(c.state.value not in _GONE for c in health.collectors)
