"""Device-list requests: search, bucket filter, sort, and paging (TUI-003, 12.3, E.29).

The default order is the 7.7 order: Attention, then Active, then
Offline/Retired; within a bucket by E.2 severity, then most recent state
change, then natural display name, then device UUID, so the order is
total and repeatable (E.29 decision 1). State changes are not recorded yet,
so that key is the same for every device today and the name decides.

Other sorts keep the device UUID as the last key for the same reason:
``name`` (natural order), ``site`` (devices without a site last), and
``age`` (never-observed first, then oldest evidence first: what has been
dark longest comes first).

Search is a case-insensitive substring match over name, poll address,
site, tags, collector position, and status label, so ``/down`` finds the
red devices and ``/dal`` finds a site.

Dependencies:
    vakr.logic.status, vakr.logic.natural: The 7.7 key and name order.
    vakr.query.views: The rows being ordered.

Safety:
    Bounded: a page is at most E.19's 500 rows; search text is at most 256
    characters and is never interpreted as a pattern.

Example:
    >>> DeviceListRequest().sort.value, DeviceListRequest().limit
    ('status', 100)
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from vakr.domain.checks import require_int
from vakr.domain.enums import StatusBucket
from vakr.domain.errors import ValidationError
from vakr.domain.paging import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from vakr.logic.natural import natural_key
from vakr.query.views import BucketCounts, DeviceRow

MAX_SEARCH_LENGTH = 256
_BUCKET_RANK = {StatusBucket.ATTENTION: 0, StatusBucket.ACTIVE: 1, StatusBucket.OFFLINE: 2}


class DeviceSort(StrEnum):
    """Device-list orders (TUI-003)."""

    STATUS = "status"
    NAME = "name"
    SITE = "site"
    AGE = "age"


@dataclass(frozen=True, slots=True)
class DeviceListRequest:
    """What part of the device list to show.

    Attributes:
        search: Case-insensitive substring; empty matches everything.
        buckets: Only these buckets; empty means all.
        sort: Order of the list.
        offset: Index of the first row to return.
        limit: Rows per page, 1 to 500 (E.19).
        include_retired: Also list ``RETIRED`` devices (hidden by default, 7.5).
    """

    search: str = ""
    buckets: frozenset[StatusBucket] = frozenset()
    sort: DeviceSort = DeviceSort.STATUS
    offset: int = 0
    limit: int = DEFAULT_PAGE_LIMIT
    include_retired: bool = False

    def __post_init__(self) -> None:
        """Validate the request.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If the page bounds or search length are out of range.
        """
        require_int(self.offset, "offset", minimum=0)
        require_int(self.limit, "limit", minimum=1, maximum=MAX_PAGE_LIMIT)
        if len(self.search) > MAX_SEARCH_LENGTH:
            raise ValidationError(
                f"search text is limited to {MAX_SEARCH_LENGTH} characters", field="search"
            )


def matches(row: DeviceRow, request: DeviceListRequest) -> bool:
    """Tell whether a row passes the request's bucket filter and search.

    Args:
        row: A device row.
        request: The request.

    Returns:
        bool: True when the row should be listed.

    Raises:
        None
    """
    if request.buckets and row.status.bucket not in request.buckets:
        return False
    needle = request.search.strip().casefold()
    if not needle:
        return True
    haystack = (
        row.name,
        row.address,
        row.site.text,
        row.collector.text,
        row.status.label,
        *row.tags,
    )
    return any(needle in text.casefold() for text in haystack)


type SortKey = tuple[Any, ...]


def _status_key(row: DeviceRow) -> SortKey:
    """Return the 7.7 key: bucket, severity, (no state-change time yet), name, UUID.

    Args:
        row: A device row.

    Returns:
        SortKey: Smaller sorts first.

    Raises:
        None
    """
    s = row.status
    return (_BUCKET_RANK[s.bucket], s.severity, natural_key(row.name), str(row.device_id))


def _name_key(row: DeviceRow) -> SortKey:
    """Return the natural-name key.

    Args:
        row: A device row.

    Returns:
        SortKey: Name, then UUID.

    Raises:
        None
    """
    return (natural_key(row.name), str(row.device_id))


def _site_key(row: DeviceRow) -> SortKey:
    """Return the site key; devices without a site sort last.

    Args:
        row: A device row.

    Returns:
        SortKey: Site presence, site name, device name, UUID.

    Raises:
        None
    """
    site = row.site.value
    return (site is None, natural_key(str(site or "")), *_name_key(row))


def _age_key(row: DeviceRow) -> SortKey:
    """Return the age key: never observed first, then oldest evidence first.

    Args:
        row: A device row.

    Returns:
        SortKey: Observation presence, time, name, UUID.

    Raises:
        None
    """
    at = row.observed_at
    return (at is not None, at.timestamp() if at else 0.0, *_name_key(row))


SORT_KEYS: dict[DeviceSort, Callable[[DeviceRow], SortKey]] = {
    DeviceSort.STATUS: _status_key,
    DeviceSort.NAME: _name_key,
    DeviceSort.SITE: _site_key,
    DeviceSort.AGE: _age_key,
}


def ordered(rows: Iterable[DeviceRow], sort: DeviceSort) -> list[DeviceRow]:
    """Sort rows by the requested order.

    Args:
        rows: Device rows.
        sort: The order.

    Returns:
        list[DeviceRow]: Sorted copy.

    Raises:
        None
    """
    return sorted(rows, key=SORT_KEYS[sort])


def bucket_counts(rows: Iterable[DeviceRow]) -> BucketCounts:
    """Count rows per bucket.

    Args:
        rows: Device rows.

    Returns:
        BucketCounts: Attention, Active, and Offline counts.

    Raises:
        None
    """
    tally = dict.fromkeys(StatusBucket, 0)
    for row in rows:
        tally[row.status.bucket] += 1
    return BucketCounts(
        tally[StatusBucket.ATTENTION], tally[StatusBucket.ACTIVE], tally[StatusBucket.OFFLINE]
    )
