"""Plan the interface-annotation rows of an import (META-007, META-011, META-013, E.38).

An annotation row names a device (by name, as stored or as a device row of
the same import calls it) and an interface: by ``uuid`` when given, which
keeps naming it after a rename, else by ``ifName`` (exactly, then without
case). Discovered interfaces are API-managed (E.19), so an import can
classify every one of them in bulk without a claim each, which is what
META-013 asks for. An interface ``inventory.toml`` governs is changed only
through that file: a row that would change it is a conflict, and a row
matching it is unchanged.

A row waits, and is listed with the reason, when its interface is not
discovered yet, when two interfaces have that name (an identity review is
open), or when this import creates the device. Run the import again later.

Dependencies:
    vakr.inventory.import_checks, plan_interfaces: Checks; matching and pairs.

Safety:
    Pure.

Example:
    >>> annotation_changes(scope)  # doctest: +SKIP
"""

from vakr.domain.enums import ManagedByKind
from vakr.inventory.import_checks import (
    LABEL_KEY,
    MISSING,
    Scope,
    file_conflict,
    owner_problem,
    patch,
)
from vakr.inventory.model import AnnotationEntry
from vakr.inventory.plan_interfaces import ENTRY_LABEL, interface_pairs, named
from vakr.inventory.plan_model import Action, Change, Waiting, diff
from vakr.inventory.snapshot import StoredDevice, StoredInterface


def _target(scope: Scope, a: AnnotationEntry) -> StoredInterface | Waiting | None:
    """Find the interface a row names, or why it waits.

    Args:
        scope: The import.
        a: The row.

    Returns:
        StoredInterface | Waiting | None: The interface; why it waits; or
        ``None`` after recording a problem.

    Raises:
        None
    """
    device = scope.device_for(a.device)
    if device is MISSING:
        scope.problem(f"{a.at}.device", f"no device named {a.device!r} in storage or this import")
        return None
    if not isinstance(device, StoredDevice):
        return Waiting(a, "this import creates the device; import its annotations after discovery")
    found = named(a, device.id, scope.snapshot)
    if a.uuid is not None and not found:
        why = f"no interface of {a.device} has uuid {a.uuid} (retired ones cannot be annotated)"
        scope.problem(f"{a.at}.uuid", why)
        return None
    if not found:
        return Waiting(a, "not discovered yet")
    if len(found) > 1:
        return Waiting(
            a, f"{len(found)} interfaces are named {a.ifname!r}; resolve the identity review"
        )
    return found[0]


def annotation_changes(scope: Scope) -> tuple[list[Change], tuple[Waiting, ...], int]:
    """Plan annotation rows: update, unchanged, waiting, or conflict.

    Args:
        scope: The import, with its device rows already planned.

    Returns:
        tuple[list[Change], tuple[Waiting, ...], int]: Updates by
        ``device ifname``, waiting rows in file order, and the unchanged count.

    Raises:
        None
    """
    changes, waiting, same, seen = [], [], 0, {}
    for a in scope.imp.annotations:
        target = _target(scope, a)
        if not isinstance(target, StoredInterface):
            waiting.extend([target] if target is not None else [])
            continue
        iface = target.interface
        if (other := seen.setdefault(iface.id, a.at)) != a.at:
            scope.problem(
                f"{a.at}.{'uuid' if a.uuid else 'ifname'}", f"names the same interface as {other}"
            )
        elif why := owner_problem(a, iface.managed_by, "interface"):
            scope.problem(f"{a.at}.managed_by", why)
        elif changed := patch(interface_pairs(a, target), {ENTRY_LABEL.get(f, f) for f in a.given}):
            if iface.managed_by.kind is ManagedByKind.FILE:
                why = file_conflict(
                    "interface", iface.id, iface.managed_by, "change its annotation"
                )
                scope.problem(f"{a.at}.{LABEL_KEY.get(changed[0][0], changed[0][0])}", why)
            else:
                name = f"{a.device} {iface.if_name}"
                changes.append(Change(Action.UPDATE, "interface", name, iface.id, diff(changed), a))
        else:
            same += 1
    return sorted(changes, key=lambda c: c.name), tuple(waiting), same
