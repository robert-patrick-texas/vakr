"""Typed identifiers for entities and observations (META-001, META-002, API-002).

Every public identity in vakr is a UUID that the application assigns. It is
never a database row ID, a hostname, an ``ifIndex``, or a ``sysName``, so a
router that renumbers its interfaces or a device that changes its name keeps
its history (invariant 6). Each kind of ID is its own ``NewType`` so a type
checker catches an interface ID passed where a device ID belongs.

New IDs are UUIDv7 (RFC 9562): the leading 48 bits are a millisecond
timestamp, so IDs created close together sort close together and storage
indexes stay local instead of scattering inserts across the whole B-tree.

Dependencies:
    uuid: ``uuid7()`` (Python 3.14+) generation and ``UUID`` values.
    vakr.domain.errors: ``ValidationError`` for rejected input.

Safety:
    Parsing accepts only the canonical 36-character hyphenated form and
    rejects the nil and max UUIDs, which usually mean an uninitialised
    value rather than a real identity.

Example:
    >>> device = new_id(DeviceId)
    >>> parse_id(DeviceId, str(device)) == device
    True
    >>> device.version
    7
"""

import re
import uuid
from collections.abc import Callable
from typing import NewType
from uuid import UUID

from vakr.domain.errors import ValidationError

DeviceId = NewType("DeviceId", UUID)
InterfaceId = NewType("InterfaceId", UUID)
SiteId = NewType("SiteId", UUID)
TagId = NewType("TagId", UUID)
GroupId = NewType("GroupId", UUID)
PolicyId = NewType("PolicyId", UUID)
CollectorId = NewType("CollectorId", UUID)
WorkerId = NewType("WorkerId", UUID)
PollId = NewType("PollId", UUID)
ObservationId = NewType("ObservationId", UUID)
BatchId = NewType("BatchId", UUID)
AuditEventId = NewType("AuditEventId", UUID)
IdentityHistoryId = NewType("IdentityHistoryId", UUID)
ReviewId = NewType("ReviewId", UUID)  # an identity case awaiting admin review (IFID-007)
UserId = NewType("UserId", UUID)  # a local web/API account (SEC-005)
SessionId = NewType("SessionId", UUID)  # a server-side login session (SEC-010)
KioskTokenId = NewType("KioskTokenId", UUID)  # a read-only display token (E.12)
DashboardId = NewType("DashboardId", UUID)  # a custom dashboard (UI-004)

_CANONICAL = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_RESERVED = {UUID(int=0), UUID(int=(1 << 128) - 1)}
_MAX_ECHO = 64  # long enough to show the mistake, short enough not to flood a log line


def new_id[T: UUID](kind: Callable[[UUID], T]) -> T:
    """Create a fresh time-ordered identifier of the given kind.

    Args:
        kind: The ID type, such as ``DeviceId``.

    Returns:
        T: A new UUIDv7 typed as ``kind``.

    Raises:
        None
    """
    return kind(uuid.uuid7())


def parse_id[T: UUID](kind: Callable[[UUID], T], text: object) -> T:
    """Parse the canonical text form of an identifier.

    Upper-case hex is accepted because people paste IDs; the canonical
    output of ``str()`` is always lower case.

    Args:
        kind: The ID type, such as ``DeviceId``.
        text: Candidate text, normally from an API path, a file, or an envelope.

    Returns:
        T: The parsed identifier typed as ``kind``.

    Raises:
        ValidationError: If ``text`` is not a string in the 36-character
            hyphenated UUID form, or is the nil or max UUID.
    """
    return kind(parse_uuid(text, getattr(kind, "__name__", "id")))


def parse_uuid(text: object, field: str = "id") -> UUID:
    """Parse the canonical text form of a UUID without assigning an ID type.

    Args:
        text: Candidate text.
        field: Field name for the error message.

    Returns:
        UUID: The parsed value.

    Raises:
        ValidationError: If ``text`` is not a string in the 36-character
            hyphenated UUID form, or is the nil or max UUID.
    """
    if not isinstance(text, str) or not _CANONICAL.fullmatch(text.lower()):
        shown = repr(text)[:_MAX_ECHO]
        raise ValidationError(f"{field} must be a hyphenated UUID string, got {shown}", field=field)
    value = UUID(text)
    if value in _RESERVED:
        raise ValidationError(f"{field} must not be the nil or max UUID", field=field)
    return value
