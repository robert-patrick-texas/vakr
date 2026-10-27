"""Interface status records: an interface's admin and oper status when it changes (E.6, E.35).

Stage 1 reads every planned interface's ``ifAdminStatus`` and
``ifOperStatus`` each poll, but only up/up interfaces get an
``InterfaceObservation`` (E.27 #7: down interfaces get no traffic records).
An ``InterfaceStatusObservation`` keeps the rest of that evidence cheaply:
the collector writes one when an interface's status pair differs from the
last one it recorded, and one for every interface at the first Stage 1 of
a device after it starts (``baseline``). A status holds from its record
until the interface's next record from the same collector, so a link that
went down and came back while its device answered SNMP leaves two
records, never a zero and never a traffic sample.

Dependencies:
    dataclasses: Frozen, slotted records with ``__post_init__`` invariants.
    vakr.domain.checks, enums, quality, timestamps: Shared rules.

Safety:
    Holds identities, IF-MIB integers, and flags only. ``if_index`` is the
    index observed then, kept for diagnosis; the interface UUID is the identity.

Example:
    >>> InterfaceStatusObservation.__dataclass_fields__["baseline"].default
    False
"""

from dataclasses import dataclass
from datetime import datetime

from vakr.domain.checks import require_enum, require_int, require_uuid
from vakr.domain.enums import IfAdminStatus, IfOperStatus
from vakr.domain.errors import ValidationError
from vakr.domain.ids import CollectorId, DeviceId, InterfaceId, ObservationId, PollId
from vakr.domain.interfaces import MAX_IF_INDEX
from vakr.domain.quality import Quality
from vakr.domain.timestamps import require_utc


@dataclass(frozen=True, slots=True)
class InterfaceStatusObservation:
    """One interface's status at one Stage 1 poll, recorded because it changed (E.35).

    Attributes:
        observation_id: UUID of this record.
        poll_id: The poll whose Stage 1 read the status.
        collector_id: The collector.
        device_id: The device.
        interface_id: The interface's persistent identity.
        timestamp: When Stage 1 read it (UTC).
        if_index: The ``ifIndex`` it had then (diagnosis only, never identity).
        admin_status: ``ifAdminStatus``, or ``None`` when not returned.
        oper_status: ``ifOperStatus``, or ``None`` when not returned.
        baseline: The collector's first record of this interface since it started.
        quality: Flags, such as ``MAINTENANCE``.
    """

    observation_id: ObservationId
    poll_id: PollId
    collector_id: CollectorId
    device_id: DeviceId
    interface_id: InterfaceId
    timestamp: datetime
    if_index: int
    admin_status: IfAdminStatus | None
    oper_status: IfOperStatus | None
    baseline: bool = False
    quality: Quality = Quality.NONE

    @property
    def up_up(self) -> bool:
        """Whether the interface was admin up and oper up (SNMP-006).

        Args:
            None

        Returns:
            bool: Both statuses ``up``.

        Raises:
            None
        """
        return self.admin_status is IfAdminStatus.UP and self.oper_status is IfOperStatus.UP

    def __post_init__(self) -> None:
        """Validate the record.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field breaks its rule, or neither status is known.
        """
        for name in ("observation_id", "poll_id", "collector_id", "device_id", "interface_id"):
            require_uuid(getattr(self, name), name)
        require_utc(self.timestamp, "timestamp")
        require_int(self.if_index, "if_index", minimum=1, maximum=MAX_IF_INDEX)
        require_enum(self.admin_status, IfAdminStatus, "admin_status", optional=True)
        require_enum(self.oper_status, IfOperStatus, "oper_status", optional=True)
        require_enum(self.quality, Quality, "quality")
        if not isinstance(self.baseline, bool):
            raise ValidationError("baseline must be a bool", field="baseline")
        if self.admin_status is None and self.oper_status is None:
            raise ValidationError("a status record needs a status", field="oper_status")
