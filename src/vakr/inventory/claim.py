"""``vakr interface claim``: let an inventory annotation govern a discovered interface (E.19).

Discovered interfaces start API-managed, so the UI can annotate them. To
manage one from ``inventory.toml`` instead, an operator claims it: in one
unit of work its ownership moves to ``file:inventory.toml`` (the port's
``transfer_owner``), the file's annotation is written, and an audit event
records who did it and what changed (META-008, SEC-018). Nothing is ever
taken over silently: until the claim, ``vakr inventory plan`` lists the
annotation as waiting.

Dependencies:
    vakr.inventory.plan_interfaces, snapshot, writes: Finding and annotating.

Safety:
    Refuses interfaces another file owns, and any interface that is not
    exactly one discovered, non-retired interface of a file-managed device.

Example:
    >>> claim_interface(factory, inv, "edge1", "Gi0/0/0", "cli:netops", clock)  # doctest: +SKIP
"""

from vakr.domain.control import AuditEvent
from vakr.domain.enums import AuditOutcome
from vakr.domain.ids import AuditEventId, new_id
from vakr.domain.interfaces import Interface
from vakr.inventory.model import FILE_OWNER, AnnotationEntry, InventoryFile
from vakr.inventory.ownership import match_device
from vakr.inventory.plan_interfaces import interface_diff, resolve
from vakr.inventory.plan_model import Waiting
from vakr.inventory.snapshot import load_snapshot
from vakr.inventory.writes import annotate
from vakr.ports.clock import Clock
from vakr.ports.metadata import MetadataUnitOfWorkFactory


class ClaimError(ValueError):
    """The interface cannot be claimed; the message says why and what to do."""


def _entry(inv: InventoryFile, device: str, ifname: str) -> AnnotationEntry:
    """Find the file's annotation for an interface.

    Args:
        inv: The file.
        device: Device name.
        ifname: Interface name; compared exactly, then case-insensitively.

    Returns:
        AnnotationEntry: The annotation.

    Raises:
        ClaimError: If the file has none, or its device is not declared.
    """
    mine = [a for a in inv.annotations if a.device == device]
    entry = next((a for a in mine if a.ifname == ifname), None) or next(
        (a for a in mine if a.ifname.casefold() == ifname.casefold()), None
    )
    if entry is None:
        raise ClaimError(f"{inv.source} has no [[interface_annotations]] entry for {device} "
                         f"{ifname}; add one, then claim")  # fmt: skip
    if not any(d.name == device for d in inv.devices):
        raise ClaimError(f"{inv.source} has no [[devices]] entry named {device!r}")
    return entry


def claim_interface(
    factory: MetadataUnitOfWorkFactory, inv: InventoryFile, device: str, ifname: str, actor: str,
    clock: Clock,
) -> tuple[Interface, tuple[str, ...]]:  # fmt: skip
    """Transfer an interface to ``file:inventory.toml`` and write its annotation.

    Args:
        factory: Opens units of work.
        inv: The parsed file.
        device: Device name.
        ifname: Interface name.
        actor: Who claims, for audit.
        clock: Time source.

    Returns:
        tuple[Interface, tuple[str, ...]]: The claimed interface and what changed.

    Raises:
        ClaimError: If the interface cannot be claimed; nothing is written.
        DomainError: If storage refuses the write; nothing is written.
    """
    entry = _entry(inv, device, ifname)
    with factory() as uow:
        snapshot = load_snapshot(uow, inv)
        stored, conflict = match_device(next(d for d in inv.devices if d.name == device), snapshot)
        if conflict or stored is None:
            raise ClaimError(conflict or f"{device} is not stored yet; run `vakr inventory apply`")
        target = resolve(entry, inv, snapshot)
        if isinstance(target, Waiting):
            raise ClaimError(f"{device} {entry.ifname}: {target.reason}")
        before = target.interface
        if before.managed_by == FILE_OWNER:
            raise ClaimError(f"already governed by {inv.source}; `vakr inventory apply` updates it")
        if before.managed_by.kind is FILE_OWNER.kind:
            raise ClaimError(f"owned by {before.managed_by}, another file; it cannot be claimed")
        now = max(clock.now_utc(), before.updated_at)
        changes = interface_diff(entry, target)
        moved = uow.interfaces.transfer_owner(
            before.id, FILE_OWNER, expected_version=before.version, at=now
        )
        claimed = annotate(uow, moved.id, entry, now)
        head = f"{device} {before.if_name}: {before.managed_by} -> {FILE_OWNER}"
        summary = "; ".join((head, *changes))[:1000]
        event = AuditEvent(new_id(AuditEventId), now, actor, "interface.claim", "interface",
                           before.id, AuditOutcome.SUCCESS, summary)  # fmt: skip
        uow.audit.append(event)
        uow.commit()
    return claimed, changes
