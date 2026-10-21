"""Filter parameters of the device list and a device's interface list (12.3, UI-005, UI-017).

The device list filters by bucket, retired devices, evidence freshness, and
collector (12.3 requires status, freshness, and collector filters beside
the groupings of ``device_scope``). A device's interface list filters by
retired interfaces and interest class, so a device page can show its
primary-interest interfaces in full and page through the others (UI-017).
Each set is one FastAPI dependency, so the routes stay small.

Dependencies:
    fastapi: ``Query`` declarations.
    vakr.query: The filter enums.

Safety:
    Repeatable parameters are bounded (at most 3 values each); unknown
    values are refused with the 422 envelope (SEC-019).

Example:
    >>> device_filter(bucket=[], include_retired=False, freshness=[], collector=None).buckets
    frozenset()
"""

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Query

from vakr.api.params import collector_id
from vakr.domain.enums import InterestClass, StatusBucket
from vakr.domain.ids import CollectorId
from vakr.query import FreshnessFilter

DeviceCollector = Annotated[
    UUID | None, Query(description="Only devices this collector (position) has evidence for.")
]


@dataclass(frozen=True, slots=True)
class DeviceFilter:
    """The device list's state filters (12.3, UI-005).

    Attributes:
        buckets: Only these buckets; empty means all.
        include_retired: Also list retired devices.
        freshness: Only these freshness classes; empty means all.
        collector: Only devices this collector has evidence for.
    """

    buckets: frozenset[StatusBucket]
    include_retired: bool
    freshness: frozenset[FreshnessFilter]
    collector: CollectorId | None


def device_filter(
    bucket: Annotated[list[StatusBucket], Query(max_length=3)] = [],  # noqa: B006 - FastAPI copies it
    include_retired: bool = False,
    freshness: Annotated[list[FreshnessFilter], Query(max_length=3)] = [],  # noqa: B006
    collector: DeviceCollector = None,
) -> DeviceFilter:
    """Read the state filters of the device list.

    Args:
        bucket: Only these buckets (repeat the parameter).
        include_retired: Also list retired devices.
        freshness: Only ``fresh``, ``stale``, or ``never`` observed devices (repeatable).
        collector: Only devices this collector (position) has evidence for.

    Returns:
        DeviceFilter: The filters.

    Raises:
        None
    """
    return DeviceFilter(frozenset(bucket), include_retired, frozenset(freshness),
                        collector_id(collector))  # fmt: skip


@dataclass(frozen=True, slots=True)
class DeviceInterfaceFilter:
    """Which of a device's interfaces to list (UI-017).

    Attributes:
        include_retired: Also list retired interfaces.
        interest: Only this interest class; ``None`` lists every class.
    """

    include_retired: bool
    interest: InterestClass | None


def device_interface_filter(
    include_retired: bool = False,
    interest: Annotated[InterestClass | None, Query(description="primary = high priority.")] = None,
) -> DeviceInterfaceFilter:
    """Read the filters of a device's interface list.

    Args:
        include_retired: Also list retired interfaces.
        interest: Only ``primary`` or only ``secondary`` interfaces.

    Returns:
        DeviceInterfaceFilter: The filters.

    Raises:
        None
    """
    return DeviceInterfaceFilter(include_retired, interest)
