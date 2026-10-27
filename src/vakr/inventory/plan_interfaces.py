"""Plan interface annotations: update claimed interfaces, release unclaimed ones (E.19).

Discovered interfaces start API-managed (E.19). An annotation in
``inventory.toml`` governs an interface only after ``vakr interface claim``
transferred its ownership to the file; until then the plan lists it as
waiting, with the command to run. An interface the file governs whose
annotation was removed is released: its annotation returns to the defaults
and it becomes API-managed again, so the UI can annotate it.

An annotation names an interface by ``ifName`` as the device reports it
(compared exactly, then case-insensitively), or by its ``uuid``, which
keeps naming it after a rename (META-010, E.38). Two interfaces with that
name on one device mean an identity review is open, and the annotation waits.

Dependencies:
    vakr.inventory.ownership, plan_model, snapshot.

Safety:
    Pure.

Example:
    >>> from vakr.inventory.model import AnnotationEntry
    >>> wanted_annotation(AnnotationEntry("a[0]", "edge1", "Gi0/0/0", sticky=True)).sticky
    True
"""

from dataclasses import fields
from uuid import UUID

from vakr.domain.enums import InterfaceLifecycle
from vakr.domain.interfaces import InterfaceAnnotation
from vakr.inventory.model import FILE_OWNER, AnnotationEntry, InventoryFile
from vakr.inventory.ownership import match_device
from vakr.inventory.plan_model import Action, Change, Waiting, diff
from vakr.inventory.snapshot import Snapshot, StoredInterface


def wanted_annotation(entry: AnnotationEntry) -> InterfaceAnnotation:
    """Build the interface annotation an entry asks for.

    Args:
        entry: The annotation entry.

    Returns:
        InterfaceAnnotation: The annotation (META-007, META-011).

    Raises:
        None
    """
    return InterfaceAnnotation(
        interest=entry.interest, sticky=entry.sticky, is_wan_circuit=entry.wan_circuit,
        provider=entry.provider, circuit_id=entry.circuit_id, contracted_bps=entry.contracted_bps,
        capacity_override_bps=entry.capacity_override_bps, public_dashboard=entry.public_dashboard,
        display_rank=entry.display_rank,
    )  # fmt: skip


ENTRY_LABEL = {"wan_circuit": "is_wan_circuit"}
"""Diff label of an annotation entry field whose stored name differs (META-012)."""


def interface_pairs(
    entry: AnnotationEntry | None, stored: StoredInterface
) -> list[tuple[str, object, object]]:
    """Pair each owned interface field's stored value with the annotation's.

    Args:
        entry: The annotation, or ``None`` for the defaults a release restores.
        stored: The stored interface.

    Returns:
        list[tuple[str, object, object]]: ``(field, stored, wanted)``; labels
        are ``InterfaceAnnotation`` field names, then ``tags`` and ``comment``.

    Raises:
        None
    """
    want = wanted_annotation(entry) if entry else InterfaceAnnotation()
    have = stored.interface.annotation
    pairs = [(f.name, getattr(have, f.name), getattr(want, f.name)) for f in fields(have)]
    tags, comment = (entry.tags, entry.comment) if entry else ((), "")
    return [*pairs, ("tags", stored.tags, tags), ("comment", stored.interface.comment, comment)]


def interface_diff(entry: AnnotationEntry | None, stored: StoredInterface) -> tuple[str, ...]:
    """List the owned fields in which a claimed interface differs from its annotation.

    Args:
        entry: The annotation, or ``None`` for the defaults a release restores.
        stored: The stored interface.

    Returns:
        tuple[str, ...]: Changed fields.

    Raises:
        None
    """
    return diff(interface_pairs(entry, stored))


def named(entry: AnnotationEntry, device_id: UUID, snapshot: Snapshot) -> list[StoredInterface]:
    """Find the stored interfaces an annotation names: by ``uuid`` when given, else by name.

    Args:
        entry: The annotation.
        device_id: The stored device's UUID.
        snapshot: Stored records.

    Returns:
        list[StoredInterface]: Non-retired matches on that device, exact
        names first. With a ``uuid``, the one interface with that UUID, so
        the annotation follows it across a rename and ``ifname`` is a label.

    Raises:
        None
    """
    mine = [i for i in snapshot.interfaces_of(device_id)
            if i.interface.lifecycle is not InterfaceLifecycle.RETIRED]  # fmt: skip
    if entry.uuid is not None:
        return [i for i in mine if i.interface.id == entry.uuid]
    exact = [i for i in mine if i.interface.if_name == entry.ifname]
    folded = entry.ifname.casefold()
    return exact or [i for i in mine if i.interface.if_name.casefold() == folded]


def resolve(
    entry: AnnotationEntry, inv: InventoryFile, snapshot: Snapshot
) -> StoredInterface | Waiting:
    """Resolve an annotation to one interface, or say why it waits.

    Args:
        entry: The annotation.
        inv: The file.
        snapshot: Stored records.

    Returns:
        StoredInterface | Waiting: The interface (which may still be
        API-managed only for the claim command), or why the annotation waits.

    Raises:
        None
    """
    device = next(d for d in inv.devices if d.name == entry.device)
    stored, _ = match_device(device, snapshot)
    if stored is None:
        return Waiting(entry, "the device is created by this apply; wait for its discovery")
    found = named(entry, stored.device.id, snapshot)
    if not found and entry.uuid is not None:
        return Waiting(entry, f"no interface of {entry.device} has uuid {entry.uuid}")
    if not found:
        return Waiting(entry, "not discovered yet")
    if len(found) > 1:
        why = f"{len(found)} interfaces are named {entry.ifname!r}; resolve the identity review"
        return Waiting(entry, why)
    return found[0]


def interface_changes(
    inv: InventoryFile, snapshot: Snapshot
) -> tuple[list[Change], tuple[Waiting, ...], int]:
    """Plan annotation updates and releases.

    Args:
        inv: The file.
        snapshot: Stored records.

    Returns:
        tuple[list[Change], tuple[Waiting, ...], int]: Changes in
        ``(device, ifname)`` order, waiting annotations in file order, and
        the unchanged count.

    Raises:
        None
    """
    changes, waiting, same, governed = [], [], 0, set()
    for entry in inv.annotations:
        target = resolve(entry, inv, snapshot)
        if isinstance(target, StoredInterface) and target.interface.managed_by != FILE_OWNER:
            claim = f"vakr interface claim {entry.device} {entry.ifname}"
            why = f"API-managed; run `{claim}` to let {inv.source} govern it (E.19)"
            target = Waiting(entry, why, target.interface.id)
        if isinstance(target, Waiting):
            waiting.append(target)
            continue
        governed.add(target.interface.id)
        if found := interface_diff(entry, target):
            name = f"{entry.device} {target.interface.if_name}"
            changes.append(
                Change(Action.UPDATE, "interface", name, target.interface.id, found, entry)
            )
        same += not found
    for i in snapshot.interfaces:
        if i.interface.managed_by == FILE_OWNER and i.interface.id not in governed:
            why = (
                *interface_diff(None, i),
                f"no annotation in {inv.source}; back to API management",
            )
            name = f"{i.device} {i.interface.if_name}"
            changes.append(Change(Action.RELEASE, "interface", name, i.interface.id, why))
    return sorted(changes, key=lambda c: c.name), tuple(waiting), same
