"""Apply an import in one metadata unit of work, with audit (META-010, SEC-018, E.38).

``apply_import`` reads storage, plans the import against it, and writes
every change in the same unit of work, so an import happens completely or
not at all (DB-002). Records it creates are ``api:import``; records it
updates keep their owner, and only the fields a row gives are written.
Tags and groups it names are created ``api:import`` when missing. Each
change has its own audit event (``inventory.import.<kind>.<action>``)
naming the operator, plus one ``inventory.import`` event for the whole
file. An import with nothing to change writes nothing (CFG-011).

Dependencies:
    vakr.inventory.import_plan: What to change, and whether it may.
    vakr.inventory.catalog, plan_interfaces: Tags, groups, annotations.

Safety:
    Writes API-managed records only; repositories refuse any write to a
    file-managed record (META-008). Audit summaries never echo comments.

Example:
    >>> plan, _ = apply_import(factory, imp, Checks(), "cli:netops", clock)  # doctest: +SKIP
"""

from dataclasses import dataclass, fields, replace
from datetime import datetime
from uuid import UUID

from vakr.domain.control import AuditEvent
from vakr.domain.enums import AuditOutcome
from vakr.domain.ids import AuditEventId, DeviceId, InterfaceId, SiteId, new_id
from vakr.domain.inventory import Device, Site
from vakr.inventory.catalog import ensure_groups, ensure_tags
from vakr.inventory.import_plan import import_snapshot, plan_import
from vakr.inventory.model import (
    IMPORT_OWNER,
    AnnotationEntry,
    DeviceEntry,
    InventoryFile,
    SiteEntry,
)
from vakr.inventory.plan_interfaces import ENTRY_LABEL, wanted_annotation
from vakr.inventory.plan_model import Change, InventoryPlan
from vakr.inventory.validate import Checks, Findings
from vakr.inventory.writes import found
from vakr.ports.clock import Clock
from vakr.ports.metadata import MetadataUnitOfWork, MetadataUnitOfWorkFactory

MAX_SUMMARY = 1_000  # audit summaries stay readable and bounded
FROM_IMPORT = "Created by vakr inventory import."
"""Description of tags and groups an import creates."""

_DEVICE_ATTR = {
    "name": "name", "address": "poll_address", "address_family": "address_family",
    "site": "site_id", "admin_state": "admin_state", "interval_s": "poll_interval_s",
    "discovery_interval_s": "discovery_interval_s", "reachability": "reachability",
    "credential": "credential_ref", "comment": "comment", "tags": "tags", "groups": "groups",
}  # fmt: skip
"""Device record attribute of each device entry field."""


@dataclass(slots=True)
class _Run:
    """One import being written.

    Attributes:
        uow: The open unit of work.
        actor: Who imports, for audit.
        now: Import time (UTC).
        sites: Site UUID by name, growing as sites are created.
    """

    uow: MetadataUnitOfWork
    actor: str
    now: datetime
    sites: dict[str, SiteId]


def _audit(run: _Run, action: str, kind: str, object_id: UUID | None, summary: str) -> None:
    """Append one audit event (SEC-018).

    Args:
        run: The import.
        action: Action code.
        kind: Object type.
        object_id: The object, if any.
        summary: Redacted change summary.

    Returns:
        None

    Raises:
        DuplicateError: Never in practice; IDs are new.
    """
    event = AuditEvent(new_id(AuditEventId), run.now, run.actor, action, kind, object_id,
                       AuditOutcome.SUCCESS, summary[:MAX_SUMMARY])  # fmt: skip
    run.uow.audit.append(event)


def _site(run: _Run, change: Change, entry: SiteEntry) -> UUID:
    """Create a site, or write the fields a row gives to an API-managed one.

    Args:
        run: The import.
        change: The change.
        entry: The row.

    Returns:
        UUID: The site.

    Raises:
        DomainError: From the repository.
    """
    if change.record_id is None:
        site_id = SiteId(entry.uuid or new_id(SiteId))
        site = run.uow.sites.add(Site(site_id, entry.name, entry.code, IMPORT_OWNER, entry.region,
                                      entry.timezone, entry.description))  # fmt: skip
    else:
        stored = found(run.uow.sites.get(SiteId(change.record_id)), "site", change.record_id)
        names = ("name", "code", "region", "timezone", "description")
        values = {n: getattr(entry, n) for n in names if n in entry.given}
        site = run.uow.sites.update(replace(stored, **values), writer=IMPORT_OWNER)
    run.sites[site.name] = site.id
    return site.id


def _device_value(run: _Run, entry: DeviceEntry, name: str) -> object:
    """Return the record value of one device entry field.

    Args:
        run: The import.
        entry: The row.
        name: Entry field name.

    Returns:
        object: The value to store; tags and groups are created when missing.

    Raises:
        DomainError: From the repositories.
    """
    if name == "site":
        return run.sites[entry.site] if entry.site else None
    if name == "tags":
        return ensure_tags(run.uow, entry.tags, IMPORT_OWNER, FROM_IMPORT)
    if name == "groups":
        return ensure_groups(run.uow, entry.groups, IMPORT_OWNER, FROM_IMPORT)
    return getattr(entry, name)


def _device(run: _Run, change: Change, entry: DeviceEntry) -> UUID:
    """Create a device, or write the fields a row gives to an API-managed one.

    Args:
        run: The import.
        change: The change.
        entry: The row.

    Returns:
        UUID: The device.

    Raises:
        DomainError: From the repository.
    """
    if change.record_id is None:
        device_id = DeviceId(entry.uuid or new_id(DeviceId))
        base = Device(device_id, entry.name, entry.address, IMPORT_OWNER, run.now, run.now,
                      address_family=entry.address_family)  # fmt: skip
        values = {attr: _device_value(run, entry, n) for n, attr in _DEVICE_ATTR.items()}
        return run.uow.devices.add(replace(base, **values)).id
    stored = found(run.uow.devices.get(DeviceId(change.record_id)), "device", change.record_id)
    values = {a: _device_value(run, entry, n) for n, a in _DEVICE_ATTR.items() if n in entry.given}
    changed = replace(stored, **values, updated_at=max(run.now, stored.updated_at))
    return run.uow.devices.update(changed, writer=IMPORT_OWNER).id


def _interface(run: _Run, change: Change, entry: AnnotationEntry) -> UUID:
    """Write the annotation fields a row gives to an API-managed interface.

    Args:
        run: The import.
        change: The change.
        entry: The row.

    Returns:
        UUID: The interface.

    Raises:
        DomainError: From the repository.
    """
    key = InterfaceId(found(change.record_id, "interface", None))
    stored = found(run.uow.interfaces.get(key), "interface", key)
    labels, wanted = {ENTRY_LABEL.get(f, f) for f in entry.given}, wanted_annotation(entry)
    note = replace(stored.annotation, **{f.name: getattr(wanted, f.name)
                                          for f in fields(wanted) if f.name in labels})  # fmt: skip
    values: dict[str, object] = {"annotation": note, "updated_at": max(run.now, stored.updated_at)}
    if "tags" in entry.given:
        values["tags"] = ensure_tags(run.uow, entry.tags, IMPORT_OWNER, FROM_IMPORT)
    if "comment" in entry.given:
        values["comment"] = entry.comment
    return run.uow.interfaces.update(replace(stored, **values), writer=IMPORT_OWNER).id


def _execute(run: _Run, plan: InventoryPlan) -> None:
    """Write every change in plan order, with one audit event each and one for the file.

    Args:
        run: The import.
        plan: The plan.

    Returns:
        None

    Raises:
        DomainError: From the repositories.
        TypeError: For a change without a row, which no import plans.
    """
    for change in plan.changes:
        entry = change.entry
        if isinstance(entry, SiteEntry):
            object_id = _site(run, change, entry)
        elif isinstance(entry, DeviceEntry):
            object_id = _device(run, change, entry)
        elif isinstance(entry, AnnotationEntry):
            object_id = _interface(run, change, entry)
        else:  # plan_import never disables or releases anything
            raise TypeError(f"an import cannot {change.action.value} {change.name}")
        summary = f"{change.name}: {'; '.join(change.details)}"
        action = f"inventory.import.{change.kind}.{change.action.value}"
        _audit(run, action, change.kind, object_id, summary)
    _audit(run, "inventory.import", "inventory", None, f"{plan.source}: {plan.summary}")


def apply_import(
    factory: MetadataUnitOfWorkFactory, imp: InventoryFile, checks: Checks, actor: str, clock: Clock
) -> tuple[InventoryPlan, Findings]:
    """Plan an import against storage and apply it atomically.

    Args:
        factory: Opens units of work.
        imp: The parsed import file.
        checks: Credential names and target guardrails.
        actor: Who imports, such as ``cli:netops`` (SEC-018).
        clock: Time source.

    Returns:
        tuple[InventoryPlan, Findings]: What was applied, and the warnings.

    Raises:
        InventoryError: If the import has problems; nothing is written.
        DomainError: If storage refuses a write, such as a concurrent edit;
            nothing is written.
    """
    with factory() as uow:
        snapshot = import_snapshot(uow, imp)
        plan, findings = plan_import(imp, snapshot, checks)
        if plan.changes:
            sites = {s.name: s.id for s in snapshot.sites}
            _execute(_Run(uow, actor, clock.now_utc(), sites), plan)
            uow.commit()
    return plan, findings
