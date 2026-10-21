"""Overview widgets computed from rows and the index: device counts and quick filters (12.2).

``summary`` counts devices per bucket, per E.2 status label (most severe
first), and per freshness of the newest evidence (TIME-005): fresh, stale,
or never observed. ``filter_options`` lists the quick-filter choices of
12.2 widget 8: providers, sites, tags, groups, and the WAN circuit count.

Dependencies:
    vakr.query.listing, catalog_index, overview_views: Counts, index, views.

Safety:
    Pure.

Example:
    >>> summary([], False).total
    0
"""

from collections import Counter
from collections.abc import Sequence

from vakr.logic.natural import natural_key
from vakr.query.catalog_index import CatalogIndex, Named
from vakr.query.listing import bucket_counts
from vakr.query.overview_views import DeviceSummary, FilterOptions, StateCount
from vakr.query.views import DeviceRow


def summary(rows: list[DeviceRow], truncated: bool) -> DeviceSummary:
    """Count devices per bucket, per status label, and per freshness.

    Args:
        rows: Every device row (retired excluded).
        truncated: Whether the inventory read was cut short.

    Returns:
        DeviceSummary: The counts.

    Raises:
        None
    """
    labels = Counter(
        (
            r.status.severity,
            r.status.label,
            r.status.bucket.value,
            r.status.color.value,
            r.status.icon,
        )
        for r in rows
    )
    by_status = tuple(
        StateCount(label, bucket, color, icon, n)
        for (_, label, bucket, color, icon), n in sorted(labels.items())
    )
    fresh = sum(1 for r in rows if r.freshness.value == "fresh")
    never = sum(1 for r in rows if r.observed_at is None)
    return DeviceSummary(
        len(rows),
        bucket_counts(rows),
        by_status,
        fresh,
        len(rows) - fresh - never,
        never,
        truncated,
    )


def filter_options(index: CatalogIndex) -> FilterOptions:
    """List the quick-filter choices (12.2 widget 8).

    Args:
        index: The inventory.

    Returns:
        FilterOptions: Providers, sites, tags, groups, and the WAN circuit count.

    Raises:
        None
    """
    providers = {i.annotation.provider for i in index.interfaces if i.annotation.provider}

    def pairs(records: Sequence[Named]) -> tuple[tuple[str, str], ...]:
        """Return ``(UUID, name)`` pairs in natural name order.

        Args:
            records: Records with ``id`` and ``name``.

        Returns:
            tuple: The pairs.

        Raises:
            None
        """
        named = sorted(records, key=lambda r: natural_key(r.name))
        return tuple((str(r.id), r.name) for r in named)

    return FilterOptions(
        tuple(sorted(providers, key=natural_key)),
        pairs(list(index.sites.values())),
        pairs(list(index.tags.values())),
        pairs(list(index.groups.values())),
        sum(1 for i in index.interfaces if i.annotation.is_wan_circuit),
    )
