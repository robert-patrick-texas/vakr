"""Admin operations: work an administrator asks for and then watches (API-011, DISC-010).

A manual rediscovery never runs inside the web request or the CLI command
that asks for it. The request stores an ``Operation`` in ``queued`` state
and answers at once with its ID; a collector claims it, runs it beside the
scheduled discoveries, and records the outcome. The record is the progress
and the result the requester follows (API-T06). States move one way only:

- ``queued`` → ``running`` (a collector claimed it) → ``succeeded`` or ``failed``;
- ``queued`` → ``expired`` (no collector picked it up in time).

Only ``rediscovery`` exists in this release; ``kind`` keeps the record
generic for imports, projection rebuilds, and maintenance (API-011).

Dependencies:
    dataclasses, enum: Frozen records and their vocabularies.
    vakr.domain.checks, timestamps: Shared invariant checks.
    vakr.domain.operation_results: ``OperationResult``, re-exported here.

Safety:
    ``error`` holds operator words built by vakr from fixed phrases and the
    SNMP adapter's own messages, which never carry a community or other
    secret; it is capped at ``MAX_ERROR_LENGTH``.

Example:
    >>> from datetime import UTC, datetime
    >>> from vakr.domain.ids import DeviceId, OperationId, new_id
    >>> t = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    >>> op = Operation(new_id(OperationId), OperationKind.REDISCOVERY, new_id(DeviceId),
    ...                OperationState.QUEUED, "alice", t)
    >>> op.active, op.expired(t, "nobody came").state.value
    (True, 'expired')
"""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from vakr.domain.checks import require_enum, require_int, require_name, require_order, require_uuid
from vakr.domain.errors import ValidationError
from vakr.domain.ids import CollectorId, DeviceId, OperationId
from vakr.domain.operation_results import OperationResult
from vakr.domain.timestamps import require_optional_utc, require_utc

MAX_ERROR_LENGTH = 500
"""Longest stored failure reason (Appendix E.32)."""
MAX_ACTOR_LENGTH = 128
"""Longest stored requester name, such as ``cli:alice (sudo)`` or ``local:root``."""


class OperationKind(StrEnum):
    """What an operation does."""

    REDISCOVERY = "rediscovery"


class OperationState(StrEnum):
    """Where an operation is; see the module docstring for the transitions."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"


ACTIVE_STATES = frozenset({OperationState.QUEUED, OperationState.RUNNING})
"""States of an operation that has not finished."""
FINISHED_STATES = frozenset(OperationState) - ACTIVE_STATES


@dataclass(frozen=True, slots=True)
class Operation:
    """One admin operation and its progress (API-011).

    Attributes:
        id: The operation's UUID, which the requester follows.
        kind: What it does.
        device_id: The device it concerns.
        state: Where it is.
        requested_by: Who asked: a username, ``local:<account>``, or ``cli:<login>``.
        requested_at: When it was asked for (UTC).
        started_at: When a collector claimed it.
        finished_at: When it ended.
        collector_id: The collector that ran it.
        result: What a succeeded operation found.
        error: Why a failed or expired operation did not succeed.
        version: Optimistic-concurrency version; every change adds one.
    """

    id: OperationId
    kind: OperationKind
    device_id: DeviceId
    state: OperationState
    requested_by: str
    requested_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    collector_id: CollectorId | None = None
    result: OperationResult | None = None
    error: str | None = None
    version: int = 1

    def __post_init__(self) -> None:
        """Validate fields and that they fit the state.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field breaks its rule or does not fit the state.
        """
        require_uuid(self.id, "id")
        require_uuid(self.device_id, "device_id")
        require_uuid(self.collector_id, "collector_id", optional=True)
        require_enum(self.kind, OperationKind, "kind")
        require_enum(self.state, OperationState, "state")
        require_name(self.requested_by, "requested_by")
        if len(self.requested_by) > MAX_ACTOR_LENGTH:
            raise ValidationError(f"requested_by is limited to {MAX_ACTOR_LENGTH} characters",
                                  field="requested_by")  # fmt: skip
        require_utc(self.requested_at, "requested_at")
        require_optional_utc(self.started_at, "started_at")
        require_optional_utc(self.finished_at, "finished_at")
        require_order(self.requested_at, self.started_at, "started_at")
        require_order(self.started_at or self.requested_at, self.finished_at, "finished_at")
        require_int(self.version, "version", minimum=1)
        if self.error is not None and not 0 < len(self.error) <= MAX_ERROR_LENGTH:
            raise ValidationError(f"error must be 1 to {MAX_ERROR_LENGTH} characters",
                                  field="error")  # fmt: skip
        self._require_state_fit()

    def _require_state_fit(self) -> None:
        """Require exactly the fields the state implies.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field is missing or present against the state.
        """
        started = self.state is not OperationState.QUEUED and (
            self.state is not OperationState.EXPIRED
        )
        checks = (
            ("started_at", (self.started_at is not None) == started),
            ("collector_id", (self.collector_id is not None) == started),
            ("finished_at", (self.finished_at is not None) == (self.state in FINISHED_STATES)),
            ("result", (self.result is not None) == (self.state is OperationState.SUCCEEDED)),
            ("error", (self.error is not None) == (self.state in _ERROR_STATES)),
        )
        for name, fits in checks:
            if not fits:
                raise ValidationError(f"{name} does not fit state {self.state.value}", field=name)

    @property
    def active(self) -> bool:
        """Whether the operation has not finished.

        Args:
            None

        Returns:
            bool: True while ``queued`` or ``running``.

        Raises:
            None
        """
        return self.state in ACTIVE_STATES

    def claimed(self, collector_id: CollectorId, at: datetime) -> Operation:
        """Return the operation as a collector that claimed it at ``at`` has it.

        Args:
            collector_id: The claiming collector.
            at: When (UTC).

        Returns:
            Operation: ``running``, one version later.

        Raises:
            ValidationError: If it is not ``queued``.
        """
        self._require(OperationState.QUEUED)
        return replace(self, state=OperationState.RUNNING, started_at=at,
                       collector_id=collector_id, version=self.version + 1)  # fmt: skip

    def succeeded(self, at: datetime, result: OperationResult) -> Operation:
        """Return the operation finished with a result.

        Args:
            at: When it ended (UTC).
            result: What it found.

        Returns:
            Operation: ``succeeded``, one version later.

        Raises:
            ValidationError: If it is not ``running``.
        """
        self._require(OperationState.RUNNING)
        return replace(self, state=OperationState.SUCCEEDED, finished_at=at, result=result,
                       version=self.version + 1)  # fmt: skip

    def failed(self, at: datetime, error: str) -> Operation:
        """Return the operation finished without a result.

        Args:
            at: When it ended (UTC).
            error: Why, in operator words; cut to ``MAX_ERROR_LENGTH``.

        Returns:
            Operation: ``failed``, one version later.

        Raises:
            ValidationError: If it is not ``running``.
        """
        self._require(OperationState.RUNNING)
        return replace(self, state=OperationState.FAILED, finished_at=at,
                       error=clip(error), version=self.version + 1)  # fmt: skip

    def expired(self, at: datetime, error: str) -> Operation:
        """Return the operation given up because no collector picked it up.

        Args:
            at: When it was given up (UTC).
            error: Why, in operator words.

        Returns:
            Operation: ``expired``, one version later.

        Raises:
            ValidationError: If it is not ``queued``.
        """
        self._require(OperationState.QUEUED)
        return replace(self, state=OperationState.EXPIRED, finished_at=at, error=clip(error),
                       version=self.version + 1)  # fmt: skip

    def _require(self, state: OperationState) -> None:
        """Refuse a transition from any other state.

        Args:
            state: The state the transition starts from.

        Returns:
            None

        Raises:
            ValidationError: If the operation is in another state.
        """
        if self.state is not state:
            raise ValidationError(f"the operation is {self.state.value}, not {state.value}",
                                  field="state")  # fmt: skip


_ERROR_STATES = frozenset({OperationState.FAILED, OperationState.EXPIRED})


def clip(text: str) -> str:
    """Cut an operator message to ``MAX_ERROR_LENGTH`` characters, never to nothing.

    Args:
        text: The message.

    Returns:
        str: At most ``MAX_ERROR_LENGTH`` characters; ``"failed"`` for an empty one.

    Raises:
        None
    """
    clean = " ".join(text.split()) or "failed"
    return clean if len(clean) <= MAX_ERROR_LENGTH else clean[: MAX_ERROR_LENGTH - 1] + "…"


__all__ = [
    "ACTIVE_STATES",
    "FINISHED_STATES",
    "MAX_ACTOR_LENGTH",
    "MAX_ERROR_LENGTH",
    "Operation",
    "OperationKind",
    "OperationResult",
    "OperationState",
    "clip",
]
