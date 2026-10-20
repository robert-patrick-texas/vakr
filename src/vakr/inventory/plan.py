"""The deterministic dry-run diff between ``inventory.toml`` and storage (META-010, CFG-010).

A plan lists, in a fixed order (sites, devices, interfaces; each by name),
what applying the file would do:

- ``create`` a site or device the file declares and storage lacks;
- ``update`` one whose stored fields differ, naming each field;
- ``disable`` a file-managed device the file no longer declares: it stops
  being polled and keeps all history (11.2, META-009); nothing is deleted;
- ``update`` a claimed interface whose annotation changed, or ``release``
  one whose annotation left the file, returning it to API management.

Annotations that cannot apply yet are listed as *waiting*: the interface is
not discovered, or it is still API-managed and needs ``vakr interface
claim`` (E.19). The same file and storage always give the same plan, and
applying it twice changes nothing the second time (CFG-011).

Dependencies:
    vakr.inventory.ownership, snapshot: Which records entries manage.
    vakr.inventory.plan_model, plan_interfaces: Plan records; interface planning.

Safety:
    Pure: the plan is computed from a snapshot.

Example:
    >>> from vakr.inventory.model import InventoryFile
    >>> from vakr.inventory.snapshot import EMPTY
    >>> make_plan(InventoryFile("inventory.toml"), EMPTY).summary
    'no changes'
"""

from vakr.domain.enums import AdminState
from vakr.inventory.model import DeviceEntry, InventoryFile
from vakr.inventory.ownership import match_device, match_site
from vakr.inventory.plan_interfaces import interface_changes
from vakr.inventory.plan_model import Action, Change, InventoryPlan, diff
from vakr.inventory.snapshot import Snapshot, StoredDevice

__all__ = ["Action", "Change", "InventoryPlan", "device_diff", "make_plan"]

_ENDED = frozenset({AdminState.DISABLED, AdminState.RETIRED})


def _site_changes(inv: InventoryFile, snapshot: Snapshot) -> tuple[list[Change], int]:
    """Plan site creates and updates.

    Args:
        inv: The file.
        snapshot: Stored records.

    Returns:
        tuple[list[Change], int]: Changes and the unchanged count.

    Raises:
        None
    """
    changes, same = [], 0
    for s in sorted(inv.sites, key=lambda e: e.name):
        stored, _ = match_site(s, snapshot)
        if stored is None:
            changes.append(Change(Action.CREATE, "site", s.name, None, (f"code {s.code}",), s))
            continue
        found = diff([
            ("name", stored.name, s.name), ("code", stored.code, s.code),
            ("region", stored.region, s.region), ("timezone", stored.timezone, s.timezone),
            ("description", stored.description, s.description),
        ])  # fmt: skip
        if found:
            changes.append(Change(Action.UPDATE, "site", s.name, stored.id, found, s))
        same += not found
    return changes, same


def device_diff(entry: DeviceEntry, stored: StoredDevice) -> tuple[str, ...]:
    """List the fields in which a stored device differs from its entry.

    Args:
        entry: The file entry.
        stored: The stored device.

    Returns:
        tuple[str, ...]: Changed fields; empty when they match.

    Raises:
        None
    """
    d = stored.device
    return diff([
        ("name", d.name, entry.name), ("address", d.poll_address, entry.address),
        ("site", stored.site, entry.site), ("credential", d.credential_ref, entry.credential),
        ("tags", stored.tags, entry.tags), ("groups", stored.groups, entry.groups),
        ("admin_state", d.admin_state.value, entry.admin_state.value),
        ("interval_s", d.poll_interval_s, entry.interval_s),
        ("discovery_interval_s", d.discovery_interval_s, entry.discovery_interval_s),
        ("reachability", d.reachability, entry.reachability),
        ("address_family", d.address_family, entry.address_family),
        ("comment", d.comment, entry.comment),
    ])  # fmt: skip


def _device_changes(inv: InventoryFile, snapshot: Snapshot) -> tuple[list[Change], int]:
    """Plan device creates, updates, and disables.

    Args:
        inv: The file.
        snapshot: Stored records.

    Returns:
        tuple[list[Change], int]: Changes and the unchanged count.

    Raises:
        None
    """
    changes, same, kept = [], 0, set()
    for d in sorted(inv.devices, key=lambda e: e.name):
        stored, _ = match_device(d, snapshot)
        if stored is None:
            where = f"{d.address}" + (f" at site {d.site}" if d.site else "")
            changes.append(Change(Action.CREATE, "device", d.name, None, (where,), d))
            continue
        kept.add(stored.device.id)
        found = device_diff(d, stored)
        if found:
            changes.append(Change(Action.UPDATE, "device", d.name, stored.device.id, found, d))
        same += not found
    for stored in sorted(snapshot.devices, key=lambda s: s.device.name):
        dev = stored.device
        if stored.file_managed and dev.id not in kept and dev.admin_state not in _ENDED:
            why = (f"no longer in {inv.source}; polling stops, history is kept",)
            changes.append(Change(Action.DISABLE, "device", dev.name, dev.id, why))
    return changes, same


def make_plan(inv: InventoryFile, snapshot: Snapshot) -> InventoryPlan:
    """Compute the plan for a validated file.

    Args:
        inv: The file; it must have passed ``validate``.
        snapshot: Stored records.

    Returns:
        InventoryPlan: Changes in apply order and waiting annotations.

    Raises:
        None
    """
    sites, same_sites = _site_changes(inv, snapshot)
    devices, same_devices = _device_changes(inv, snapshot)
    interfaces, waiting, same_ifaces = interface_changes(inv, snapshot)
    unchanged = same_sites + same_devices + same_ifaces
    return InventoryPlan(inv.source, (*sites, *devices, *interfaces), waiting, unchanged)
