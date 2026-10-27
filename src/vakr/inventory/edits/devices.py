"""``DeviceEditor``: create, change, and retire devices by hand, audited (27.4, META-008, META-009).

- ``create`` stores a new API-managed device after the file's field rules
  and checks (``fields``, ``checks``); tags and groups it names are created
  API-managed when missing, and only referenced when they exist.
- ``update`` merges a change over the stored device. A change based on an
  older ``version`` is a conflict (DB-003); a change to a file-managed
  device is refused naming the file (``FileManagedError``); a change that
  changes nothing writes nothing.
- ``retire`` is the soft delete (META-009): the device stops being polled,
  and its history, interfaces, and identity history stay. Setting
  ``admin_state`` back to ``enabled`` restores it.

Each write is one unit of work with its audit event, and moves the change
counter, so collectors discover a new device and reload a changed one as
after ``vakr inventory apply`` (6.2, E.27).

Dependencies:
    vakr.inventory.edits: Fields, checks, ownership, records.
    vakr.inventory.plan: The file's device diff.

Safety:
    Names a credential only (SEC-001). Synchronous: the API calls it in a
    worker thread.

Example:
    >>> editor = DeviceEditor(factory, SystemClock(), lambda: Checks())  # doctest: +SKIP
    >>> editor.create({"name": "edge9", "address": "192.0.2.9"}, actor="alice")  # doctest: +SKIP
"""

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any

from vakr.domain.enums import AdminState
from vakr.domain.errors import NotFoundError
from vakr.domain.ids import DeviceId, new_id
from vakr.domain.inventory import Device
from vakr.domain.ownership import API_MANAGED
from vakr.inventory.catalog import ensure_groups, ensure_tags
from vakr.inventory.edits.checks import device_findings
from vakr.inventory.edits.fields import (
    DEVICE_KEYS,
    changed_field,
    device_table,
    only_keys,
    parse_device,
)
from vakr.inventory.edits.ownership import require_api_owner
from vakr.inventory.edits.records import FROM_VAKR, DeviceRecord, audit, stored_device
from vakr.inventory.errors import InventoryError, Issue
from vakr.inventory.model import DeviceEntry
from vakr.inventory.plan import device_diff
from vakr.inventory.plan_model import diff
from vakr.inventory.reads import all_devices, all_sites
from vakr.inventory.validate import Checks
from vakr.ports.clock import Clock
from vakr.ports.metadata import MetadataUnitOfWork, MetadataUnitOfWorkFactory

type ChecksSource = Callable[[], Checks]
"""Reads credential names and guardrails at the time of the edit."""


def _record(uow: MetadataUnitOfWork, base: Device, entry: DeviceEntry, now: datetime) -> Device:
    """Return ``base`` with every field the entry sets, as ``inventory apply`` writes them.

    Args:
        uow: The open unit of work.
        base: A new or stored device.
        entry: The checked fields.
        now: The edit time (UTC).

    Returns:
        Device: The record to write.

    Raises:
        DuplicateError: If another writer created the same tag or group first.
    """
    site = uow.sites.get_by_name(entry.site) if entry.site else None
    return replace(
        base, name=entry.name, poll_address=entry.address, address_family=entry.address_family,
        site_id=site.id if site else None, admin_state=entry.admin_state,
        poll_interval_s=entry.interval_s, discovery_interval_s=entry.discovery_interval_s,
        reachability=entry.reachability, credential_ref=entry.credential, comment=entry.comment,
        tags=ensure_tags(uow, entry.tags, API_MANAGED, FROM_VAKR),
        groups=ensure_groups(uow, entry.groups, API_MANAGED, FROM_VAKR),
        updated_at=max(now, base.updated_at),
    )  # fmt: skip


def _created(entry: DeviceEntry) -> tuple[str, ...]:
    """Describe a new device for its audit event: every field it sets but the comment.

    Args:
        entry: The checked fields.

    Returns:
        tuple[str, ...]: Change lines.

    Raises:
        None
    """
    pairs: list[tuple[str, object]] = [
        ("address", entry.address), ("site", entry.site), ("credential", entry.credential),
        ("tags", entry.tags), ("groups", entry.groups), ("admin_state", entry.admin_state.value),
        ("interval_s", entry.interval_s), ("discovery_interval_s", entry.discovery_interval_s),
        ("reachability", entry.reachability), ("address_family", entry.address_family),
        ("comment", entry.comment),
    ]  # fmt: skip
    return diff([(name, () if isinstance(v, tuple) else "" if v == "" else None, v)
                 for name, v in pairs])  # fmt: skip


class DeviceEditor:
    """Manual device writes and the record reads they need."""

    def __init__(self, metadata: MetadataUnitOfWorkFactory, clock: Clock, checks: ChecksSource
                 ) -> None:  # fmt: skip
        """Bind the editor.

        Args:
            metadata: Opens metadata units of work.
            clock: Time source for ``updated_at`` and the audit events.
            checks: Returns credential names and target guardrails when called.

        Returns:
            None

        Raises:
            None
        """
        self._metadata = metadata
        self._clock = clock
        self._checks = checks

    def get(self, device_id: DeviceId) -> DeviceRecord | None:
        """Return a device's editable record.

        Args:
            device_id: The device.

        Returns:
            DeviceRecord | None: The record, or ``None`` when there is no such device.

        Raises:
            StorageError: If the metadata store cannot be read.
        """
        with self._metadata() as uow:
            device = uow.devices.get(device_id)
            return DeviceRecord(stored_device(uow, device)) if device else None

    def _checked(self, uow: MetadataUnitOfWork, entry: DeviceEntry, self_id: DeviceId | None
                 ) -> list[Issue]:  # fmt: skip
        """Run every check; refuse the edit on any problem.

        Args:
            uow: The open unit of work.
            entry: The device as the edit would leave it.
            self_id: The device being changed, or ``None``.

        Returns:
            list[Issue]: The warnings.

        Raises:
            InventoryError: Listing every problem by field (422 in the API).
        """
        problems, warnings = device_findings(entry, all_devices(uow), all_sites(uow),
                                             self._checks(), self_id)  # fmt: skip
        if problems:
            raise InventoryError("device", problems)
        return warnings

    def create(self, fields: Mapping[str, Any], *, actor: str, request_id: str | None = None
               ) -> DeviceRecord:  # fmt: skip
        """Store a new API-managed device and its audit event.

        Args:
            fields: ``[[devices]]`` keys; ``name`` and ``address`` are required.
            actor: Who creates it.
            request_id: The API request, if any.

        Returns:
            DeviceRecord: The stored device (version 1) and the warnings.

        Raises:
            InventoryError: A field or check problem (422).
            StorageError: If the metadata store fails.
        """
        entry = parse_device(only_keys(fields, DEVICE_KEYS))
        now = self._clock.now_utc()
        with self._metadata() as uow:
            warnings = self._checked(uow, entry, None)
            base = Device(DeviceId(new_id(DeviceId)), entry.name, entry.address, API_MANAGED,
                          now, now, address_family=entry.address_family)  # fmt: skip
            device = uow.devices.add(_record(uow, base, entry, now))
            changes = ("created", *_created(entry))
            audit(uow, now, actor, "device.create", ("device", device.id),
                  f"{device.name}: {'; '.join(changes)}", request_id)  # fmt: skip
            record = DeviceRecord(stored_device(uow, device), changes, tuple(warnings))
            uow.commit()
        return record

    def update(self, device_id: DeviceId, changes: Mapping[str, Any], *, version: int | None,
               actor: str, request_id: str | None = None) -> DeviceRecord:  # fmt: skip
        """Change a device, if it is API-managed and still at ``version`` (DB-003).

        Args:
            device_id: The device.
            changes: ``[[devices]]`` keys that change; ``None`` clears a field.
            version: The version the change is based on; ``None`` is the stored one (CLI).
            actor: Who changes it.
            request_id: The API request, if any.

        Returns:
            DeviceRecord: The device, one version later unless nothing changed.

        Raises:
            NotFoundError: No such device (404).
            FileManagedError: A file manages it (409 ``managed_by_file``).
            ConflictError: Someone changed it since ``version`` (409).
            InventoryError: A field or check problem (422).
        """
        now = self._clock.now_utc()
        with self._metadata() as uow:
            device = uow.devices.get(device_id)
            if device is None:
                raise NotFoundError("device", device_id)
            stored = stored_device(uow, device)
            entry = parse_device(device_table(stored) | only_keys(changes, DEVICE_KEYS))
            details = device_diff(entry, stored)
            require_api_owner("device", device.name, device.managed_by,
                              [changed_field(d) for d in details])  # fmt: skip
            if not details:
                return DeviceRecord(stored)
            warnings = self._checked(uow, entry, device.id)
            base = replace(device, version=device.version if version is None else version)
            new = uow.devices.update(_record(uow, base, entry, now), writer=API_MANAGED)
            audit(uow, new.updated_at, actor, "device.update", ("device", new.id),
                  f"{device.name}: {'; '.join(details)}", request_id)  # fmt: skip
            record = DeviceRecord(stored_device(uow, new), details, tuple(warnings))
            uow.commit()
        return record

    def retire(self, device_id: DeviceId, *, version: int | None, actor: str,
               request_id: str | None = None) -> DeviceRecord:  # fmt: skip
        """Retire a device: the soft delete (META-009); history and identities stay.

        Args:
            device_id: The device.
            version: The version the delete is based on; ``None`` is the stored one.
            actor: Who retires it.
            request_id: The API request, if any.

        Returns:
            DeviceRecord: The retired device; unchanged when it was retired already.

        Raises:
            NotFoundError: No such device (404).
            FileManagedError: A file manages it (409); removing it from the file disables it.
            ConflictError: Someone changed it since ``version`` (409).
        """
        with self._metadata() as uow:
            device = uow.devices.get(device_id)
            if device is None:
                raise NotFoundError("device", device_id)
            if device.admin_state is AdminState.RETIRED:
                return DeviceRecord(stored_device(uow, device))
            require_api_owner("device", device.name, device.managed_by, ["admin_state"])
            at = max(self._clock.now_utc(), device.updated_at)
            new = uow.devices.set_admin_state(
                device.id, AdminState.RETIRED, writer=API_MANAGED, at=at,
                expected_version=device.version if version is None else version)  # fmt: skip
            detail = f"admin_state: {device.admin_state.value} -> retired"
            audit(uow, at, actor, "device.retire", ("device", device.id),
                  f"{device.name}: {detail}", request_id)  # fmt: skip
            record = DeviceRecord(stored_device(uow, new), (detail,))
            uow.commit()
        return record
