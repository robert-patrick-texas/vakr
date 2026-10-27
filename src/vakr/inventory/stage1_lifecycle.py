"""Interface lifecycle from each Stage 1 poll, as Appendix E.6 says (LIFE-001, LIFE-006).

E.6 makes an interface ``active`` when it is in the latest discovery and
up/up at the last Stage 1 poll, and ``inactive`` when it is present but
not up/up. Discovery runs every 24 hours, so judging lifecycle only there
left a link that went down showing ``active`` for up to a day. The
collector now hands each Stage 1 status change to ``apply_stage1_status``,
which applies it with the same rules as discovery (``vakr.logic.lifecycle``,
source ``stage1``) and writes only the interfaces whose state changes, in
one unit of work per batch.

Rules beyond E.6, chosen to keep identity and evidence honest:

- The interface is looked up by its persistent UUID; ``ifIndex`` plays no part.
- Evidence is never compared with the record's ``updated_at``: annotation and
  tag edits move it too, and the collector does not repeat an unchanged
  status, so such a comparison lost changes for good (M6 review). Stage 1
  evidence is the newest status the collector read; if discovery decided in
  the seconds before the batch arrived and saw another status, the next
  Stage 1 differs from the batch's and puts it right within one poll.
- An interface discovery found missing (no current ``ifIndex``) keeps that
  decision: Stage 1 evidence was read under an older plan. ``retired`` never
  changes here.
- Missing status (neither value read) is not evidence (the lifecycle rules).

Dependencies:
    vakr.logic.lifecycle: The transition rules.
    vakr.discovery.reconcile.records: The record a stored interface implies.
    vakr.ports.metadata: Units of work.

Safety:
    Bounded: the caller passes at most one batch; each item is one lookup and
    at most one write, all in one transaction.

Example:
    >>> apply_stage1_status.__name__
    'apply_stage1_status'
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from vakr.discovery.reconcile.records import current_record
from vakr.domain.enums import IfAdminStatus, IfOperStatus, InterfaceLifecycle
from vakr.domain.ids import InterfaceId
from vakr.logic.lifecycle import apply_evidence
from vakr.logic.lifecycle_model import InterfaceEvidence, LifecycleSource
from vakr.ports.metadata import MetadataUnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class StatusEvidence:
    """One interface's status at one Stage 1, for its lifecycle (LIFE-006 source ``stage1``).

    Attributes:
        interface_id: The interface.
        at: When Stage 1 read it.
        admin_status: ``ifAdminStatus``, or ``None``.
        oper_status: ``ifOperStatus``, or ``None``.
    """

    interface_id: InterfaceId
    at: datetime
    admin_status: IfAdminStatus | None
    oper_status: IfOperStatus | None


def apply_stage1_status(
    uow_factory: MetadataUnitOfWorkFactory, items: Sequence[StatusEvidence], stale_after: timedelta
) -> int:
    """Apply Stage 1 status changes to interface lifecycles in one transaction.

    Args:
        uow_factory: Opens a metadata unit of work.
        items: Status changes, at most one batch.
        stale_after: ``lifecycle.stale_after`` (LIFE-003).

    Returns:
        int: Interfaces whose lifecycle changed.

    Raises:
        StorageError: If the metadata database is unavailable.
        ConflictError: If an interface changed meanwhile; the caller retries the batch.
    """
    changed = 0
    with uow_factory() as uow:
        for item in items:
            stored = uow.interfaces.get(item.interface_id)
            if stored is None or stored.lifecycle is InterfaceLifecycle.RETIRED:
                continue
            if stored.if_index is None:
                continue  # discovery lost it after this Stage 1: its decision stands
            evidence = InterfaceEvidence(item.at, LifecycleSource.STAGE1, True,
                                         item.admin_status, item.oper_status)  # fmt: skip
            state = apply_evidence(current_record(stored), evidence, stale_after).record.state
            if state is not stored.lifecycle:
                uow.interfaces.set_lifecycle(stored.id, state, expected_version=stored.version,
                                             at=item.at)  # fmt: skip
                changed += 1
        uow.commit()
    return changed
