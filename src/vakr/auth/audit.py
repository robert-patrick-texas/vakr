"""Audit records for authentication and user administration (SEC-012, SEC-018).

Logins, logouts, failed logins, session revocations, kiosk tokens, and
every user change append an ``AuditEvent`` in the same unit of work as the
change itself, so a change is never stored without its record (DB-002).
Summaries are built here from fixed phrases, account names, roles, counts,
and client addresses only.

The action vocabulary is closed (``ACTIONS``), and each action names the
kind of object it acts on, so every record of one action looks alike.

Dependencies:
    vakr.domain: Audit events, IDs, and outcomes.
    vakr.ports.metadata: The unit of work.

Safety:
    No caller passes a password, hash, or token here, and the summary
    vocabulary has no slot for one. ``AUDIT_ANONYMOUS`` is the actor of a
    failed login for an account that does not exist, so a password typed
    into the username field never reaches the audit log.

Example:
    >>> AUDIT_ANONYMOUS
    'anonymous'
"""

from datetime import datetime
from uuid import UUID

from vakr.domain.control import AuditEvent
from vakr.domain.enums import AuditOutcome
from vakr.domain.ids import AuditEventId, new_id
from vakr.ports.metadata.unit_of_work import MetadataUnitOfWork

AUDIT_ANONYMOUS = "anonymous"
ACTIONS = {
    "auth.login": "user",
    "auth.logout": "session",
    "user.create": "user",
    "user.password": "user",
    "user.enable": "user",
    "user.disable": "user",
    "user.role": "user",
    "user.revoke_sessions": "user",
    "kiosk.create": "kiosk",
    "kiosk.revoke": "kiosk",
    "kiosk.setup": "kiosk",
}
"""Audited action codes and the object type each acts on."""
_MAX_SUMMARY = 500


def record(
    uow: MetadataUnitOfWork,
    action: str,
    object_id: UUID | None,
    summary: str,
    *,
    at: datetime,
    actor: str,
    outcome: AuditOutcome = AuditOutcome.SUCCESS,
    request_id: str | None = None,
) -> None:
    """Append one audit event to the unit of work.

    Args:
        uow: The unit of work that makes the audited change.
        action: A code from ``ACTIONS``.
        object_id: The object's UUID, if any.
        summary: Redacted, operator-readable summary.
        at: When it happened (UTC).
        actor: Who did it: a username, ``cli:<login>``, or ``anonymous``.
        outcome: Success, failure, or denied.
        request_id: API request ID for correlation, if any.

    Returns:
        None

    Raises:
        KeyError: If ``action`` is not in ``ACTIONS`` (a programming error).
        ValidationError: If a field breaks the ``AuditEvent`` rules.
        DuplicateError: If the event ID collides (practically impossible).
    """
    event = AuditEvent(
        id=new_id(AuditEventId),
        occurred_at=at,
        actor=actor,
        action=action,
        object_type=ACTIONS[action],
        object_id=object_id,
        outcome=outcome,
        summary=summary[:_MAX_SUMMARY],
        request_id=request_id,
    )
    uow.audit.append(event)
