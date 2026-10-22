"""Checks that a dashboard's widgets refer to inventory that exists (UI-004, CFG-004).

A dashboard names interfaces, sites, tags, and groups by UUID. A write that
names one that does not exist is refused with the widget and field it came
from, so an admin never saves a dashboard that silently shows less than it
was told to. Retired interfaces exist (inventory is never deleted,
META-009) and stay allowed: the view marks them retired, and editing a
dashboard that already holds one must not fail.

Dependencies:
    vakr.domain: Widgets, IDs, and errors.
    vakr.ports.metadata: The unit of work.

Safety:
    Messages name the widget path and the kind of record, never other data.

Example:
    >>> check_references(uow, widgets)  # doctest: +SKIP
"""

from vakr.domain.dashboard_widgets import (
    DeviceCriteria,
    InterfaceCriteria,
    PinnedInterfaces,
    Widget,
)
from vakr.domain.errors import ValidationError
from vakr.ports.metadata import MetadataUnitOfWork


def _criteria(uow: MetadataUnitOfWork, criteria: InterfaceCriteria | DeviceCriteria,
              where: str) -> None:  # fmt: skip
    """Require the site, tag, and group a criteria object names to exist.

    Args:
        uow: An open unit of work.
        criteria: The criteria.
        where: Field path for messages, such as ``widgets[1].criteria``.

    Returns:
        None

    Raises:
        ValidationError: If one does not exist.
    """
    missing = (
        ("site_id", criteria.site_id is not None and uow.sites.get(criteria.site_id) is None),
        ("tag_id", criteria.tag_id is not None and uow.tags.get(criteria.tag_id) is None),
        ("group_id", criteria.group_id is not None and uow.groups.get(criteria.group_id) is None),
    )
    for name, absent in missing:
        if absent:
            kind = name.removesuffix("_id")
            raise ValidationError(f"{where}.{name}: no such {kind}", field=f"{where}.{name}")


def check_references(uow: MetadataUnitOfWork, widgets: tuple[Widget, ...]) -> None:
    """Require every interface, site, tag, and group the widgets name to exist.

    Args:
        uow: An open unit of work.
        widgets: The widgets, in order.

    Returns:
        None

    Raises:
        ValidationError: Naming the first missing reference by its path.
    """
    for n, widget in enumerate(widgets):
        where = f"widgets[{n}]"
        if isinstance(widget, PinnedInterfaces):
            for k, iface in enumerate(widget.interfaces):
                if uow.interfaces.get(iface) is None:
                    raise ValidationError(f"{where}.interfaces[{k}]: no such interface",
                                          field=f"{where}.interfaces")  # fmt: skip
        else:
            _criteria(uow, widget.criteria, f"{where}.criteria")
