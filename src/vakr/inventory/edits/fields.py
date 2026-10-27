"""Edits in the terms of ``inventory.toml``: stored records as tables, parsed with the file's rules.

An edit is a mapping of the same keys a ``[[devices]]`` or
``[[interface_annotations]]`` table uses (``name``, ``address``,
``interval = "60s"``, ``wan_circuit``, ...). A change is merged over the
stored record written as such a table, and the whole table is parsed by
``vakr.inventory.parse``, so an edit obeys exactly the bounds, label rule,
address rule, and interval limits of the file (META-010, E.26). A value of
``None`` clears an optional field; an absent key keeps it.

Dependencies:
    vakr.inventory.parse, model, snapshot: The parser and its records.

Safety:
    Pure. Problems name fields, never echo a comment.

Example:
    >>> parse_device({"name": "edge9", "address": "192.0.2.9", "interval": "2m"}).interval_s
    120
"""

from collections.abc import Mapping
from typing import Any

from vakr.domain.interfaces import Interface
from vakr.inventory.errors import InventoryError, Issue
from vakr.inventory.model import AnnotationEntry, DeviceEntry
from vakr.inventory.parse import parse_document
from vakr.inventory.snapshot import StoredDevice

DEVICE_KEYS = ("name", "address", "site", "credential", "tags", "groups", "comment",
               "admin_state", "interval", "discovery_interval", "reachability",
               "address_family")  # fmt: skip
"""Device fields an edit may set: the ``[[devices]]`` keys but ``uuid``."""

ANNOTATION_KEYS = ("interest", "high_priority", "sticky", "wan_circuit", "provider",
                   "circuit_id", "contracted_bps", "capacity_override_bps", "comment",
                   "tags")  # fmt: skip
"""Annotation fields an edit may set (META-007, META-011, META-013)."""

_RENAMES = {"interval_s": "interval", "discovery_interval_s": "discovery_interval",
            "is_wan_circuit": "wan_circuit", "public_dashboard": "public_dashboard_eligible",
            "comment changed": "comment"}  # fmt: skip
_PLACEHOLDER = "-"  # an annotation table needs device and ifname; edits never use them


def _without_none(table: Mapping[str, Any]) -> dict[str, Any]:
    """Drop cleared fields, which a table expresses by leaving the key out.

    Args:
        table: Keys and values, ``None`` meaning "not set".

    Returns:
        dict[str, Any]: The table without ``None`` values.

    Raises:
        None
    """
    return {k: v for k, v in table.items() if v is not None}


def _issues(exc: InventoryError, prefix: str) -> InventoryError:
    """Re-root the parser's paths (``devices[0].address``) at the edit's own fields.

    Args:
        exc: The parser's error.
        prefix: The table path to remove, such as ``devices[0].``.

    Returns:
        InventoryError: The same problems, with paths such as ``address``.

    Raises:
        None
    """
    found = [Issue(i.path.removeprefix(prefix), i.reason) for i in exc.issues]
    return InventoryError(exc.source, found)


def parse_device(table: Mapping[str, Any], source: str = "device") -> DeviceEntry:
    """Parse a device table with the ``[[devices]]`` rules.

    Args:
        table: The fields; ``None`` values are left out.
        source: Names the edit in messages.

    Returns:
        DeviceEntry: The checked entry.

    Raises:
        InventoryError: Listing every problem by field.
    """
    try:
        return parse_document({"devices": [_without_none(table)]}, source).devices[0]
    except InventoryError as exc:
        raise _issues(exc, "devices[0].") from None


def parse_annotation(table: Mapping[str, Any], source: str = "annotation") -> AnnotationEntry:
    """Parse an annotation table with the ``[[interface_annotations]]`` rules.

    Args:
        table: The fields; ``None`` values are left out.
        source: Names the edit in messages.

    Returns:
        AnnotationEntry: The checked entry.

    Raises:
        InventoryError: Listing every problem by field.
    """
    full = {"device": _PLACEHOLDER, "ifname": _PLACEHOLDER} | _without_none(table)
    try:
        return parse_document({"interface_annotations": [full]}, source).annotations[0]
    except InventoryError as exc:
        raise _issues(exc, "interface_annotations[0].") from None


def _duration(seconds: int | None) -> str | None:
    """Write stored seconds the way the file writes a duration.

    Args:
        seconds: Whole seconds, or ``None``.

    Returns:
        str | None: Such as ``120s``.

    Raises:
        None
    """
    return None if seconds is None else f"{seconds}s"


def device_table(stored: StoredDevice) -> dict[str, Any]:
    """Write a stored device as the table that would declare it.

    Args:
        stored: The device with its site, tag, and group names.

    Returns:
        dict[str, Any]: ``[[devices]]`` keys; unset fields are ``None``.

    Raises:
        None
    """
    d = stored.device
    return {
        "name": d.name, "address": d.poll_address, "site": stored.site,
        "credential": d.credential_ref, "tags": list(stored.tags), "groups": list(stored.groups),
        "comment": d.comment, "admin_state": d.admin_state.value,
        "interval": _duration(d.poll_interval_s),
        "discovery_interval": _duration(d.discovery_interval_s),
        "reachability": d.reachability.value if d.reachability else None,
        "address_family": d.address_family.value if d.address_family else None,
    }  # fmt: skip


def annotation_table(iface: Interface, tags: tuple[str, ...]) -> dict[str, Any]:
    """Write a stored interface's owned fields as the table that would declare them.

    Args:
        iface: The interface.
        tags: Its vakr tag names (META-013), not the description-derived ones.

    Returns:
        dict[str, Any]: ``[[interface_annotations]]`` keys; unset fields are ``None``.

    Raises:
        None
    """
    a = iface.annotation
    return {
        "interest": a.interest.value, "sticky": a.sticky, "wan_circuit": a.is_wan_circuit,
        "provider": a.provider, "circuit_id": a.circuit_id, "contracted_bps": a.contracted_bps,
        "capacity_override_bps": a.capacity_override_bps,
        "public_dashboard_eligible": a.public_dashboard, "display_rank": a.display_rank,
        "comment": iface.comment, "tags": list(tags),
    }  # fmt: skip


def merge_annotation(stored: Mapping[str, Any], changes: Mapping[str, Any]) -> dict[str, Any]:
    """Merge a change over the stored annotation; ``high_priority`` is ``interest`` (META-011).

    The alias replaces the stored interest. When a change names both, both
    stay, so the parser refuses them if they disagree.

    Args:
        stored: ``annotation_table`` of the interface.
        changes: The fields that change.

    Returns:
        dict[str, Any]: The table to parse.

    Raises:
        None
    """
    merged = dict(stored) | dict(changes)
    high = changes.get("high_priority")
    if high is None:
        merged.pop("high_priority", None)
    elif isinstance(high, bool) and "interest" not in changes:
        merged["interest"] = "primary" if high else "secondary"
        del merged["high_priority"]
    return merged


def changed_field(detail: str) -> str:
    """Name the edit field of a change line such as ``interval_s: 60 -> 120``.

    Args:
        detail: One line of ``vakr.inventory.plan_model.diff``.

    Returns:
        str: The field as an edit names it, such as ``interval``.

    Raises:
        None
    """
    head = detail if detail == "comment changed" else detail.split(":", 1)[0]
    return _RENAMES.get(head, head)


def only_keys(changes: Mapping[str, Any], allowed: tuple[str, ...]) -> dict[str, Any]:
    """Refuse fields an edit may not set, such as a device's ``uuid``.

    Args:
        changes: The fields of the edit.
        allowed: ``DEVICE_KEYS`` or ``ANNOTATION_KEYS``.

    Returns:
        dict[str, Any]: The same fields.

    Raises:
        InventoryError: Naming every field that is not allowed.
    """
    bad = [Issue(k, f"not an editable field; expected one of: {', '.join(allowed)}")
           for k in changes if k not in allowed]  # fmt: skip
    if bad:
        raise InventoryError("edit", bad)
    return dict(changes)
