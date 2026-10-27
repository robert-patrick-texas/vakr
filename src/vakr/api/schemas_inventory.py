"""Shapes of the manual inventory endpoints: device records, annotations, and tag changes (27.4).

Bodies use the keys of ``inventory.toml`` (``address``, ``interval =
"60s"``, ``wan_circuit``), so the file, the CLI, and the API describe a
device the same way; the bounds here only spare the service obviously bad
input, and ``vakr.inventory.edits`` applies the file's own rules to the
rest. In a ``PATCH``, an absent field keeps its value and ``null`` clears
it (an optional field) or resets it to its default (a flag, tags, or the
comment). Unknown fields are refused rather than ignored.

Dependencies:
    pydantic: Body and answer models.
    vakr.domain: Enums and bounds.
    vakr.inventory.edits: Records and the bulk bound.

Safety:
    Answers name a credential reference only, never a secret (SEC-001).

Example:
    >>> DeviceCreate(name="edge9", address="192.0.2.9").admin_state is None
    True
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from vakr.domain.enums import AddressFamily, InterestClass, ManagedByKind, ReachabilityStrategy
from vakr.inventory.edits.bulk import MAX_BULK_INTERFACES
from vakr.inventory.edits.records import AnnotationRecord, DeviceRecord
from vakr.inventory.errors import Issue
from vakr.inventory.parse_fields import MAX_COMMENT, MAX_NAME
from vakr.policy.reader import MAX_LIST_LENGTH
from vakr.query.jsonable import iso_utc

_STRICT = ConfigDict(extra="forbid")
Name = Annotated[str, Field(min_length=1, max_length=MAX_NAME)]
Labels = Annotated[list[Annotated[str, Field(min_length=1, max_length=64)]],
                   Field(max_length=MAX_LIST_LENGTH)]  # fmt: skip
Duration = Annotated[str, Field(min_length=1, max_length=32, description="Such as 60s, 5m, 1h.")]
Comment = Annotated[str, Field(max_length=MAX_COMMENT)]
Rate = Annotated[int, Field(ge=1, le=2**63 - 1, description="Bits per second.")]
Settable = Literal["enabled", "maintenance", "disabled"]
"""Admin states an edit sets; ``DELETE`` retires (META-009)."""


class _DeviceFields(BaseModel):
    """The optional ``[[devices]]`` keys."""

    model_config = _STRICT
    site: Name | None = Field(default=None, description="An existing site's name.")
    credential: Name | None = Field(default=None, description="SNMP credential name, not secret.")
    tags: Labels | None = None
    groups: Labels | None = None
    comment: Comment | None = None
    admin_state: Settable | None = None
    interval: Duration | None = Field(default=None, description="Poll interval override.")
    discovery_interval: Duration | None = None
    reachability: ReachabilityStrategy | None = None
    address_family: AddressFamily | None = None


class DeviceCreate(_DeviceFields):
    """Body of ``POST /api/v1/devices``."""

    name: Name
    address: Name = Field(description="IP literal or DNS name to poll.")


class DevicePatch(_DeviceFields):
    """Body of ``PATCH /api/v1/devices/{uuid}``: ``version`` plus what changes."""

    version: int = Field(ge=1, description="The version this change is based on (DB-003).")
    name: Name | None = None
    address: Name | None = None


class FieldNote(BaseModel):
    """Advice about one field that did not block the change."""

    field: str
    message: str


class DeviceRecordOut(BaseModel):
    """A device's editable record, as the record endpoint and every write answer it."""

    id: UUID
    name: str
    address: str
    address_family: AddressFamily | None
    site: str | None
    site_id: UUID | None
    credential: str | None
    tags: list[str]
    groups: list[str]
    comment: str
    admin_state: Literal["enabled", "maintenance", "disabled", "retired"]
    interval: str | None
    discovery_interval: str | None
    reachability: ReachabilityStrategy | None
    managed_by: ManagedByKind
    managed_by_source: str = Field(description="The managing file, or `api`.")
    editable: bool = Field(description="False when a file manages it (META-008).")
    created_at: datetime
    updated_at: datetime
    version: int
    changes: list[str] = Field(description="What this write changed; empty for a read.")
    warnings: list[FieldNote]


class AnnotationPatch(BaseModel):
    """Body of ``PATCH /api/v1/interfaces/{uuid}/annotation``."""

    model_config = _STRICT
    version: int = Field(ge=1, description="The version this change is based on (DB-003).")
    interest: InterestClass | None = None
    high_priority: bool | None = Field(default=None, description="Alias: true is primary.")
    sticky: bool | None = None
    wan_circuit: bool | None = None
    provider: Name | None = None
    circuit_id: Name | None = None
    contracted_bps: Rate | None = None
    capacity_override_bps: Rate | None = None
    comment: Comment | None = None
    tags: Labels | None = Field(default=None, description="vakr tags; replaces the set.")


class AnnotationOut(BaseModel):
    """An interface's owned fields (META-007, META-011, META-013)."""

    id: UUID
    device_id: UUID
    device: str
    if_name: str
    lifecycle: str
    managed_by: ManagedByKind
    managed_by_source: str
    editable: bool
    interest: InterestClass
    sticky: bool
    wan_circuit: bool
    provider: str | None
    circuit_id: str | None
    contracted_bps: int | None
    capacity_override_bps: int | None
    comment: str
    tags: list[str] = Field(description="Tags added in vakr.")
    description_tags: list[str] = Field(description="Tags from description keywords.")
    classified: bool = Field(description="False: not yet classified (META-013).")
    version: int
    updated_at: datetime
    changes: list[str]


class TagChange(BaseModel):
    """Body of ``POST /api/v1/interfaces/tags``: add and remove vakr tags on many interfaces."""

    model_config = _STRICT
    interfaces: list[UUID] = Field(min_length=1, max_length=MAX_BULK_INTERFACES)
    add: Labels = Field(default_factory=list)
    remove: Labels = Field(default_factory=list)


class TagChangeOut(BaseModel):
    """What a tag change did."""

    changed: list[UUID]
    unchanged: list[UUID]


def notes(issues: tuple[Issue, ...]) -> list[dict[str, str]]:
    """Return warnings as ``FieldNote`` objects.

    Args:
        issues: Warnings by field.

    Returns:
        list[dict[str, str]]: One object per warning.

    Raises:
        None
    """
    return [{"field": f"body.{i.path}", "message": i.reason} for i in issues]


def device_json(record: DeviceRecord) -> dict[str, Any]:
    """Return a device record as the ``DeviceRecordOut`` JSON object.

    Args:
        record: The record.

    Returns:
        dict[str, Any]: Times in UTC ``Z`` (TIME-004).

    Raises:
        None
    """
    d, owner = record.stored.device, record.stored.device.managed_by
    seconds = (d.poll_interval_s, d.discovery_interval_s)
    interval, discovery = (None if s is None else f"{s}s" for s in seconds)
    return {
        "id": str(d.id), "name": d.name, "address": d.poll_address,
        "address_family": d.address_family, "site": record.stored.site,
        "site_id": str(d.site_id) if d.site_id else None, "credential": d.credential_ref,
        "tags": list(record.stored.tags), "groups": list(record.stored.groups),
        "comment": d.comment, "admin_state": d.admin_state.value, "interval": interval,
        "discovery_interval": discovery, "reachability": d.reachability,
        "managed_by": owner.kind.value, "managed_by_source": owner.source,
        "editable": owner.kind is ManagedByKind.API, "created_at": iso_utc(d.created_at),
        "updated_at": iso_utc(d.updated_at), "version": d.version,
        "changes": list(record.changes), "warnings": notes(record.warnings),
    }  # fmt: skip


def annotation_json(record: AnnotationRecord) -> dict[str, Any]:
    """Return an annotation record as the ``AnnotationOut`` JSON object.

    Args:
        record: The record.

    Returns:
        dict[str, Any]: Times in UTC ``Z`` (TIME-004).

    Raises:
        None
    """
    i, owner = record.interface, record.interface.managed_by
    a = i.annotation
    classified = (a.interest is InterestClass.PRIMARY or a.sticky or a.is_wan_circuit
                  or bool(record.tags) or bool(record.description_tags))  # fmt: skip
    return {
        "id": str(i.id), "device_id": str(i.device_id), "device": record.device,
        "if_name": i.if_name or i.if_descr, "lifecycle": i.lifecycle.value,
        "managed_by": owner.kind.value, "managed_by_source": owner.source,
        "editable": owner.kind is ManagedByKind.API, "interest": a.interest.value,
        "sticky": a.sticky, "wan_circuit": a.is_wan_circuit, "provider": a.provider,
        "circuit_id": a.circuit_id, "contracted_bps": a.contracted_bps,
        "capacity_override_bps": a.capacity_override_bps, "comment": i.comment,
        "tags": list(record.tags), "description_tags": list(record.description_tags),
        "classified": classified, "version": i.version, "updated_at": iso_utc(i.updated_at),
        "changes": list(record.changes),
    }  # fmt: skip
