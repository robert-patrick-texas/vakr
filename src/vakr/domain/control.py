"""Control records: collection policies, identity history, and audit events.

A ``CollectionPolicy`` is stored here as plain data: the rule model that
interprets ``rules`` belongs to ``vakr.policy``, so storage never needs to
change when the policy language grows. ``IdentityHistoryEntry`` records every
interface identity decision with its evidence (IFID-005), and ``AuditEvent``
records who did what (SEC-018). Both are append-only facts.

Dependencies:
    dataclasses: Frozen, slotted records with ``__post_init__`` invariants.
    vakr.domain.checks, json_values, timestamps: Shared invariant checks.

Safety:
    ``AuditEvent.summary`` must already be redacted by the caller; audit
    records are visible to administrators and in support bundles.

Example:
    >>> from vakr.domain.ids import PolicyId, new_id
    >>> from vakr.domain.ownership import API_MANAGED
    >>> p = CollectionPolicy(new_id(PolicyId), "skip-loopbacks", 10, "interface",
    ...                      {"exclude": {"if_type": [24]}}, API_MANAGED)
    >>> p.enabled
    True
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from vakr.domain.checks import (
    require_bool,
    require_enum,
    require_int,
    require_name,
    require_text,
    require_uuid,
)
from vakr.domain.enums import AuditOutcome, IdentityDecisionSource, ReconcileOutcome
from vakr.domain.errors import ValidationError
from vakr.domain.ids import AuditEventId, DeviceId, IdentityHistoryId, InterfaceId, PolicyId
from vakr.domain.json_values import require_json_object
from vakr.domain.ownership import ManagedBy, require_record
from vakr.domain.timestamps import require_utc


@dataclass(frozen=True, slots=True)
class CollectionPolicy:
    """An ordered collection rule document, stored verbatim (POLICY-*, 7.1).

    Attributes:
        id: Immutable policy UUID.
        name: Unique policy name; the natural key.
        order: Evaluation order; lower runs first.
        scope: What the policy applies to, as named by ``vakr.policy``.
        rules: The rule document as JSON-compatible data.
        managed_by: Owning source.
        enabled: Whether the policy takes part in evaluation.
        version: Optimistic-concurrency version.
    """

    id: PolicyId
    name: str
    order: int
    scope: str
    rules: Mapping[str, Any]
    managed_by: ManagedBy
    enabled: bool = True
    version: int = 1

    def __post_init__(self) -> None:
        """Validate the policy.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field breaks its rule, including a
                ``rules`` value that JSON cannot represent exactly.
        """
        require_uuid(self.id, "id")
        require_name(self.name, "name")
        require_int(self.order, "order")
        require_name(self.scope, "scope")
        require_json_object(self.rules, "rules")
        require_bool(self.enabled, "enabled")
        require_record(self.version, self.managed_by)


@dataclass(frozen=True, slots=True)
class IdentityHistoryEntry:
    """One interface identity decision and its evidence (IFID-005, IFID-007).

    Attributes:
        id: UUID of this history entry.
        interface_id: The persistent interface the decision concerns.
        device_id: The device both candidates belong to.
        decided_at: When the decision was made (UTC).
        outcome: Reconciliation outcome (IFID-003).
        source: Automatic reconciliation or an administrator.
        previous_if_index: ``ifIndex`` the identity had before, if any.
        observed_if_index: ``ifIndex`` observed in this discovery, if any.
        score: Match score of the chosen candidate, if scored.
        runner_up_score: Score of the next-best candidate, if any (IFID-006).
        evidence: Human-readable evidence items, such as ``"ifName equal"``.
        actor: Administrator username for ``ADMIN`` decisions.
    """

    id: IdentityHistoryId
    interface_id: InterfaceId
    device_id: DeviceId
    decided_at: datetime
    outcome: ReconcileOutcome
    source: IdentityDecisionSource
    previous_if_index: int | None = None
    observed_if_index: int | None = None
    score: float | None = None
    runner_up_score: float | None = None
    evidence: tuple[str, ...] = ()
    actor: str | None = None

    def __post_init__(self) -> None:
        """Validate the entry.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field breaks its rule, or an administrator
                decision does not name the administrator.
        """
        for name in ("id", "interface_id", "device_id"):
            require_uuid(getattr(self, name), name)
        require_utc(self.decided_at, "decided_at")
        require_enum(self.outcome, ReconcileOutcome, "outcome")
        require_enum(self.source, IdentityDecisionSource, "source")
        require_int(self.previous_if_index, "previous_if_index", minimum=1, optional=True)
        require_int(self.observed_if_index, "observed_if_index", minimum=1, optional=True)
        for name in ("score", "runner_up_score"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, float) or not math.isfinite(value)):
                raise ValidationError(f"{name} must be a finite float", field=name)
        if not isinstance(self.evidence, tuple):
            raise ValidationError("evidence must be a tuple of strings", field="evidence")
        for item in self.evidence:
            require_name(item, "evidence")
        require_name(self.actor, "actor", optional=True)
        if self.source is IdentityDecisionSource.ADMIN and self.actor is None:
            raise ValidationError("an admin decision must name its actor", field="actor")


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """A redacted record of an administrative or security action (SEC-018).

    Attributes:
        id: UUID of the event.
        occurred_at: When the action happened (UTC).
        actor: Username, ``"file:<path>"`` for reloads, or ``"system"``.
        action: Stable action code, such as ``"device.update"``.
        object_type: Kind of object acted on, such as ``"device"``.
        object_id: UUID of the object, if it has one.
        outcome: Whether the action succeeded, failed, or was denied.
        summary: Redacted, operator-readable change summary.
        request_id: API request ID for correlation, if any.
    """

    id: AuditEventId
    occurred_at: datetime
    actor: str
    action: str
    object_type: str
    object_id: UUID | None
    outcome: AuditOutcome
    summary: str = ""
    request_id: str | None = None

    def __post_init__(self) -> None:
        """Validate the event.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field breaks its rule.
        """
        require_uuid(self.id, "id")
        require_utc(self.occurred_at, "occurred_at")
        for name in ("actor", "action", "object_type"):
            require_name(getattr(self, name), name)
        require_uuid(self.object_id, "object_id", optional=True)
        require_enum(self.outcome, AuditOutcome, "outcome")
        require_text(self.summary, "summary")
        require_name(self.request_id, "request_id", optional=True)
