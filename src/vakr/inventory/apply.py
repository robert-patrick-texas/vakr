"""Apply an inventory plan through one metadata unit of work, with audit (META-008, SEC-018).

``apply_inventory`` reads storage, validates the file against it, computes
the plan, and writes every change in the same unit of work, so an apply
either happens completely or not at all (DB-002). Records it creates are
owned by ``file:inventory.toml``. Each change gets its own audit event
naming the operator and what changed, plus one event for the whole apply.
An apply with nothing to change writes nothing (CFG-011).

Dependencies:
    vakr.inventory.plan, validate, snapshot: What to change, and whether it may.
    vakr.inventory.catalog: Tags and groups by name.

Safety:
    Audit summaries list field names and values the operator wrote; a
    comment is never echoed, and nothing here can see a secret.

Example:
    >>> plan, _ = apply_inventory(factory, inv, Checks(), "cli:netops", clock)  # doctest: +SKIP
"""

from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from vakr.domain.control import AuditEvent
from vakr.domain.enums import AdminState, AuditOutcome
from vakr.domain.ids import AuditEventId, DeviceId, InterfaceId, SiteId, new_id
from vakr.domain.inventory import Device, Site
from vakr.domain.ownership import API_MANAGED
from vakr.inventory.catalog import ensure_groups, ensure_tags
from vakr.inventory.errors import InventoryError
from vakr.inventory.model import FILE_OWNER, AnnotationEntry, DeviceEntry, InventoryFile, SiteEntry
from vakr.inventory.plan import make_plan
from vakr.inventory.plan_model import Action, Change, InventoryPlan
from vakr.inventory.snapshot import Snapshot, load_snapshot
from vakr.inventory.validate import Checks, Findings, validate
from vakr.inventory.writes import FROM_FILE, annotate, found
from vakr.ports.clock import Clock
from vakr.ports.metadata import MetadataUnitOfWork, MetadataUnitOfWorkFactory

MAX_SUMMARY = 1_000  # audit summaries stay readable and bounded


@dataclass(slots=True)
class _Run:
    """One apply in progress.

    Attributes:
        uow: The open unit of work.
        actor: Who applies, for audit.
        now: Apply time (UTC).
        sites: Site UUID by name, growing as sites are created.
    """

    uow: MetadataUnitOfWork
    actor: str
    now: datetime
    sites: dict[str, SiteId]


def _audit(run: _Run, action: str, kind: str, object_id: UUID | None, summary: str) -> None:
    """Append one audit event (SEC-018).

    Args:
        run: The apply.
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
    """Create or update a site.

    Args:
        run: The apply.
        change: The change.
        entry: The site entry.

    Returns:
        UUID: The site's ID.

    Raises:
        DomainError: From the repository.
    """
    if change.record_id is None:
        site = run.uow.sites.add(Site(
            SiteId(entry.uuid or new_id(SiteId)), entry.name, entry.code, FILE_OWNER,
            entry.region, entry.timezone, entry.description,
        ))  # fmt: skip
    else:
        key = SiteId(change.record_id)
        stored = found(run.uow.sites.get(key), "site", key)
        site = run.uow.sites.update(replace(
            stored, name=entry.name, code=entry.code, region=entry.region,
            timezone=entry.timezone, description=entry.description,
        ), writer=FILE_OWNER)  # fmt: skip
    run.sites[site.name] = site.id
    return site.id


def _device_record(run: _Run, entry: DeviceEntry, base: Device) -> Device:
    """Return ``base`` with every field the file sets.

    Args:
        run: The apply.
        entry: The device entry.
        base: A new or stored device.

    Returns:
        Device: The record to write.

    Raises:
        ValidationError: If the address contradicts the address family.
    """
    return replace(
        base, name=entry.name, poll_address=entry.address, address_family=entry.address_family,
        site_id=run.sites[entry.site] if entry.site else None, admin_state=entry.admin_state,
        poll_interval_s=entry.interval_s, discovery_interval_s=entry.discovery_interval_s,
        reachability=entry.reachability, credential_ref=entry.credential, comment=entry.comment,
        tags=ensure_tags(run.uow, entry.tags, FILE_OWNER, FROM_FILE),
        groups=ensure_groups(run.uow, entry.groups, FILE_OWNER, FROM_FILE),
        updated_at=max(run.now, base.updated_at),
    )  # fmt: skip


def _device(run: _Run, change: Change, entry: DeviceEntry) -> UUID:
    """Create or update a device.

    Args:
        run: The apply.
        change: The change.
        entry: The device entry.

    Returns:
        UUID: The device's ID.

    Raises:
        DomainError: From the repository.
    """
    if change.record_id is None:
        device_id = DeviceId(entry.uuid or new_id(DeviceId))
        base = Device(device_id, entry.name, entry.address, FILE_OWNER, run.now, run.now,
                      address_family=entry.address_family)  # fmt: skip
        return run.uow.devices.add(_device_record(run, entry, base)).id
    key = DeviceId(change.record_id)
    stored = found(run.uow.devices.get(key), "device", key)
    return run.uow.devices.update(_device_record(run, entry, stored), writer=FILE_OWNER).id


def _disable(run: _Run, change: Change) -> UUID:
    """Disable a device the file no longer declares (11.2, META-009).

    Args:
        run: The apply.
        change: The change.

    Returns:
        UUID: The device's ID.

    Raises:
        DomainError: From the repository.
    """
    key = DeviceId(found(change.record_id, "device", None))
    stored = found(run.uow.devices.get(key), "device", key)
    at = max(run.now, stored.updated_at)
    run.uow.devices.set_admin_state(key, AdminState.DISABLED, expected_version=stored.version,
                                    writer=FILE_OWNER, at=at)  # fmt: skip
    return key


def _interface(run: _Run, change: Change) -> UUID:
    """Update or release a file-governed interface.

    Args:
        run: The apply.
        change: The change.

    Returns:
        UUID: The interface's ID.

    Raises:
        DomainError: From the repository.
    """
    entry = change.entry if isinstance(change.entry, AnnotationEntry) else None
    key = InterfaceId(found(change.record_id, "interface", None))
    stored = annotate(run.uow, key, entry, run.now)
    if change.action is Action.RELEASE:
        run.uow.interfaces.transfer_owner(key, API_MANAGED, expected_version=stored.version,
                                          at=stored.updated_at)  # fmt: skip
    return key


def _execute(run: _Run, plan: InventoryPlan) -> None:
    """Write every change of the plan, in order, with one audit event each.

    Args:
        run: The apply.
        plan: The plan.

    Returns:
        None

    Raises:
        DomainError: From the repositories.
    """
    for change in plan.changes:
        entry = change.entry
        if isinstance(entry, SiteEntry):
            object_id = _site(run, change, entry)
        elif isinstance(entry, DeviceEntry):
            object_id = _device(run, change, entry)
        elif change.action is Action.DISABLE:
            object_id = _disable(run, change)
        else:
            object_id = _interface(run, change)
        summary = f"{change.name}: {'; '.join(change.details)}"
        _audit(
            run, f"inventory.{change.kind}.{change.action.value}", change.kind, object_id, summary
        )
    _audit(run, "inventory.apply", "inventory", None, f"{plan.source}: {plan.summary}")


def apply_inventory(
    factory: MetadataUnitOfWorkFactory, inv: InventoryFile, checks: Checks, actor: str, clock: Clock
) -> tuple[InventoryPlan, Findings]:
    """Validate the file against storage and apply its plan atomically.

    Args:
        factory: Opens units of work.
        inv: The parsed file.
        checks: Credential names and target guardrails.
        actor: Who applies, such as ``cli:netops`` (SEC-018).
        clock: Time source.

    Returns:
        tuple[InventoryPlan, Findings]: What was applied, and the warnings.

    Raises:
        InventoryError: If validation finds problems; nothing is written.
        DomainError: If storage refuses a write, such as a concurrent edit;
            nothing is written.
    """
    with factory() as uow:
        snapshot = load_snapshot(uow, inv)
        findings = validate(inv, snapshot, checks)
        if findings.problems:
            raise InventoryError(inv.source, findings.problems)
        plan = make_plan(inv, snapshot)
        if plan.changes:
            _execute(_Run(uow, actor, clock.now_utc(), _site_ids(snapshot)), plan)
            uow.commit()
    return plan, findings


def _site_ids(snapshot: Snapshot) -> dict[str, SiteId]:
    """Map stored site names to IDs.

    Args:
        snapshot: Stored records.

    Returns:
        dict[str, SiteId]: Site UUID by name.

    Raises:
        None
    """
    return {s.name: s.id for s in snapshot.sites}
