"""Inventory, review, operation, and account counts for the scrape (OBS-001, E.33).

Each figure is one bounded read through the metadata ports: a one-row page
whose ``total`` is the count, or a repository ``count``. Device status
buckets come from the status query service, the same order and judgement
the device list uses (7.7). If the metadata database cannot be read, no
count is answered at all and ``vakr_state_metrics_up{reason=
"metadata_unavailable"}`` is 0: a count that could not be taken is never
written as zero (invariant 1). ``vakr.metrics.service`` caches the result
for ``STATE_CACHE_S``, so scrapes cost bounded database work.

Dependencies:
    vakr.ports.metadata: The unit of work and filters.
    vakr.query: ``OverviewQueries.device_list`` for the buckets.

Safety:
    Counts only; no name, address, or token leaves this module.

Example:
    >>> from vakr.adapters.memory import memory_uow_factory
    >>> [f.name for f in state_families(memory_uow_factory(), None)][:2]
    ['vakr_state_metrics_up', 'vakr_devices']
"""

import logging
from collections.abc import Callable

from vakr.domain.enums import AdminState, InterfaceLifecycle
from vakr.domain.errors import StorageError
from vakr.domain.operations import OperationState
from vakr.domain.paging import PageRequest
from vakr.domain.reviews import ReviewState
from vakr.metrics.catalog import build
from vakr.metrics.exposition import Family
from vakr.ports.metadata import MetadataUnitOfWork, MetadataUnitOfWorkFactory
from vakr.ports.metadata.filters import DeviceFilter, InterfaceFilter
from vakr.ports.metadata.operations import OperationFilter
from vakr.ports.metadata.reviews import ReviewFilter
from vakr.query import DeviceListRequest, OverviewQueries

_LOG = logging.getLogger(__name__)
_ONE = PageRequest(limit=1)


def _counts(uow: MetadataUnitOfWork) -> list[Family]:
    """Count inventory, reviews, operations, and accounts in one unit of work.

    Args:
        uow: An open unit of work.

    Returns:
        list[Family]: The count families.

    Raises:
        StorageError: If the store cannot be read.
    """
    devices = [((s.value,), uow.devices.list_page(_ONE, DeviceFilter(admin_state=s)).total)
               for s in AdminState]  # fmt: skip
    interfaces = [((s.value,), uow.interfaces.list_page(_ONE, InterfaceFilter(lifecycle=s)).total)
                  for s in InterfaceLifecycle]  # fmt: skip
    reviews = uow.reviews.list_page(_ONE, ReviewFilter(state=ReviewState.OPEN)).total
    active = (OperationState.QUEUED, OperationState.RUNNING)
    where = {s: OperationFilter(states=frozenset({s})) for s in active}
    operations = [((s.value,), uow.operations.list_page(_ONE, where[s]).total) for s in active]
    return [
        build("vakr_devices", devices),
        build("vakr_interfaces", interfaces),
        build("vakr_identity_reviews_open", [((), reviews)]),
        build("vakr_operations", operations),
        build("vakr_sessions", [((), uow.sessions.count())]),
        build("vakr_kiosk_tokens", [((), uow.kiosk_tokens.count())]),
        build("vakr_users", [((), uow.users.list_page(_ONE).total)]),
    ]


def _buckets(overview: OverviewQueries | None) -> Family | None:
    """Count listed devices by status bucket, as the device list does (7.7).

    Args:
        overview: The status query service, or ``None`` (counts only).

    Returns:
        Family | None: The bucket family, or ``None`` without a service.

    Raises:
        StorageError: If the metadata store cannot be read.
    """
    if overview is None:
        return None
    counts = overview.device_list(DeviceListRequest(limit=1)).counts
    rows = [(("attention",), counts.attention), (("active",), counts.active),
            (("offline",), counts.offline)]  # fmt: skip
    return build("vakr_devices_by_status", rows)


def state_families(
    metadata: MetadataUnitOfWorkFactory, overview: OverviewQueries | None
) -> list[Family]:
    """Read the inventory and account counts (blocking; a worker thread).

    Args:
        metadata: Opens metadata units of work.
        overview: The status query service, for the buckets.

    Returns:
        list[Family]: ``vakr_state_metrics_up`` first, then the counts when readable.

    Raises:
        None
    """
    try:
        with metadata() as uow:
            counts = _counts(uow)
        buckets = _buckets(overview)
    except StorageError as exc:
        _LOG.debug("metrics: the metadata database cannot be read (%s)", type(exc).__name__)
        return [build("vakr_state_metrics_up", [(("metadata_unavailable",), 0)])]
    found = [*counts, *([buckets] if buckets is not None else [])]
    return [build("vakr_state_metrics_up", [(("ok",), 1)]), *found]


type StateReader = Callable[[], list[Family]]
"""What ``vakr.metrics.service`` calls for the cached state and storage families."""
