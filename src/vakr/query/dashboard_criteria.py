"""Dashboard widget criteria as query scopes and as words (UI-004, DASH-006).

A widget's criteria name sites, tags, and groups by UUID. The queries need
them as ``InterfaceScope`` and ``DeviceScope``; headings and kiosk displays
need them as words ("site Dallas DC1, tag core, WAN only"). A UUID the
inventory no longer holds reads "unknown site" and so on rather than
failing the dashboard: the write-time check (``vakr.dashboards``) keeps
that rare.

Dependencies:
    vakr.query.catalog_index, catalog_scope, overview: The index and scopes.
    vakr.domain.dashboard_widgets: The criteria.

Safety:
    Display names only.

Example:
    >>> from vakr.adapters.memory import memory_uow_factory
    >>> from vakr.query.catalog_index import IndexCache
    >>> index = IndexCache(memory_uow_factory()).get()
    >>> criteria_view(index, InterfaceCriteria(wan_only=True)).text
    'WAN only'
"""

from vakr.domain.dashboard_widgets import DeviceCriteria, InterfaceCriteria
from vakr.query.catalog_index import CatalogIndex
from vakr.query.catalog_scope import InterfaceScope
from vakr.query.dashboard_views import CriteriaView
from vakr.query.overview import DeviceScope

_INTEREST = {"primary": "high priority", "secondary": "normal priority"}


def interface_scope(criteria: InterfaceCriteria) -> InterfaceScope:
    """Return the interface-list scope of interface criteria.

    Args:
        criteria: The widget's criteria.

    Returns:
        InterfaceScope: The same filters; retired interfaces are left out, as on every list.

    Raises:
        None
    """
    return InterfaceScope(site_id=criteria.site_id, tag_id=criteria.tag_id,
                          group_id=criteria.group_id, provider=criteria.provider,
                          wan_only=criteria.wan_only, interest=criteria.interest)  # fmt: skip


def device_scope(criteria: DeviceCriteria) -> DeviceScope:
    """Return the device-browsing scope of device criteria.

    Args:
        criteria: The widget's criteria.

    Returns:
        DeviceScope: The same filters.

    Raises:
        None
    """
    return DeviceScope(site_id=criteria.site_id, tag_id=criteria.tag_id,
                       group_id=criteria.group_id)  # fmt: skip


def criteria_view(
    index: CatalogIndex, criteria: InterfaceCriteria | DeviceCriteria
) -> CriteriaView:
    """Describe criteria with the names they refer to.

    Args:
        index: The inventory.
        criteria: Interface or device criteria.

    Returns:
        CriteriaView: The names, and the whole filter in words.

    Raises:
        None
    """
    site = tag = group = None
    if criteria.site_id is not None:
        found = index.sites.get(criteria.site_id)
        site = found.name if found else "unknown site"
    if criteria.tag_id is not None:
        found_tag = index.tags.get(criteria.tag_id)
        tag = found_tag.name if found_tag else "unknown tag"
    if criteria.group_id is not None:
        found_group = index.groups.get(criteria.group_id)
        group = found_group.name if found_group else "unknown group"
    provider = wan = interest = None
    if isinstance(criteria, InterfaceCriteria):
        provider, wan = criteria.provider, criteria.wan_only
        interest = criteria.interest.value if criteria.interest else None
    words = [f"site {site}" if site else "", f"tag {tag}" if tag else "",
             f"group {group}" if group else "", f"provider {provider}" if provider else "",
             "WAN only" if wan else "", _INTEREST.get(interest or "", "")]  # fmt: skip
    text = ", ".join(w for w in words if w) or "every interface"
    return CriteriaView(site, tag, group, provider, bool(wan), interest, text)
