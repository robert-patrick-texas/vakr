"""A demoted primary's resync from its successor, and the report of what it lost (ADR-024, E.39).

``vakr node demote`` leaves ``resync.json`` behind. While it exists, the
peer's metadata pull runs as a ``Resync``: its next successful pull
replaces the replicated metadata with the new primary's, removing or
overwriting every row the new primary does not hold exactly (the
replica's replace mode), even when the digests already match. Just
before it writes, ``backup`` takes a verified online copy of
``metadata.db``. Afterwards ``resync-report.json`` lists:

- the backup, so a discarded edit can be read back and made again on the
  new primary;
- every row removed or overwritten, by table, key, and name, never by
  value (at most 1,000, with the total);
- this node's own audited metadata edits since the fork point, the new
  primary's newest successful pull from this node before its promotion,
  minus one pull interval (at most ``MAX_EDITS``); node-local actions
  (sign-ins, kiosk and replication tokens, role changes) are left out.

Then the marker goes, and later pulls are ordinary again.

Dependencies:
    vakr.replication.role_record: The marker and the report.
    vakr.ports.metadata: Audit pages.

Safety:
    Runs in the collector's worker thread. The report holds keys, names,
    actors, and redacted audit summaries, never a column value or a hash.

Example:
    >>> MAX_EDITS
    500
"""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from vakr.domain.paging import PageRequest
from vakr.ports.metadata.replica import ApplyResult
from vakr.replication import role_record

_LOG = logging.getLogger(__name__)
MAX_EDITS = 500
MAX_AUDIT_PAGES = 100
"""Audit pages of 100 read at most: 10,000 events, newest first."""
FORK_MARGIN = timedelta(seconds=60)
"""An edit within one pull interval before the fork point may or may not have been copied."""
LOCAL_ACTIONS = ("auth.", "kiosk.", "replication.", "node.", "user.revoke_sessions")
"""Audited actions on node-local records, which replication never carries."""


class Resync:
    """The pending resync of one demoted node."""

    def __init__(self, state_dir: Path, uow_factory: Callable[[], Any],
                 backup: Callable[[], Path | None],
                 now: Callable[[], datetime]) -> None:  # fmt: skip
        """Bind the resync.

        Args:
            state_dir: ``/var/lib/vakr``.
            uow_factory: Metadata units of work (audit reads).
            backup: Writes a verified backup of ``metadata.db`` and returns its path.
            now: Current time (UTC).

        Returns:
            None

        Raises:
            None
        """
        self._dir, self._uow, self._backup, self._now = state_dir, uow_factory, backup, now
        self._copy: Path | None = None

    @staticmethod
    def pending(state_dir: Path) -> bool:
        """Tell whether a demotion left a resync to do.

        Args:
            state_dir: ``/var/lib/vakr``.

        Returns:
            bool: ``True`` while ``resync.json`` exists.

        Raises:
            None
        """
        return (state_dir / role_record.RESYNC_FILE).exists()

    def before_apply(self) -> None:
        """Back up ``metadata.db`` before anything is discarded.

        Args:
            None

        Returns:
            None

        Raises:
            StorageError: If the backup fails; nothing is applied then.
        """
        self._copy = self._backup()

    def finish(self, result: ApplyResult | None, remote: Any) -> dict[str, Any]:
        """Write the report and remove the marker.

        Args:
            result: What the replacing apply did; ``None`` when the digests matched.
            remote: The new primary's state answer (its ``promotion``).

        Returns:
            dict: The report.

        Raises:
            OSError: If the report cannot be written; the marker then stays.
        """
        marker = role_record.read_json(self._dir / role_record.RESYNC_FILE) or {}
        promo = remote.get("promotion") if isinstance(remote, dict) else None
        fork = promo.get("fork_at") if isinstance(promo, dict) else None
        edits, total = self._edits(fork if isinstance(fork, str) else None)
        discarded = [] if result is None else [_row(d) for d in result.discarded]
        report = {"at": self._now().isoformat(timespec="seconds"),
                  "primary_url": marker.get("primary_url"), "demoted_at": marker.get("at"),
                  "fork_at": fork, "backup": str(self._copy) if self._copy else None,
                  "discarded_total": 0 if result is None else result.discarded_total,
                  "discarded": discarded, "edits_total": total, "edits": edits}  # fmt: skip
        role_record.write_json(self._dir / role_record.REPORT_FILE, report)
        (self._dir / role_record.RESYNC_FILE).unlink(missing_ok=True)
        _LOG.warning("resynced metadata from the new primary: %d local row(s) discarded, "
                     "%d edit(s) since the fork point; see vakr node status",
                     report["discarded_total"], total,
                     extra={"event_id": "replication.resync_done"})  # fmt: skip
        return report

    def _edits(self, fork: str | None) -> tuple[list[dict[str, Any]], int]:
        """List this node's audited metadata edits since the fork point, newest first.

        Args:
            fork: The fork point (ISO 8601), or ``None`` when the new primary gave none.

        Returns:
            tuple: Up to ``MAX_EDITS`` edits, and how many were seen (bounded).

        Raises:
            StorageError: If the audit cannot be read.
        """
        try:
            since = datetime.fromisoformat(fork) - FORK_MARGIN if fork else None
        except ValueError:
            since = None
        found: list[dict[str, Any]] = []
        total, cursor = 0, None
        with self._uow() as uow:
            for _ in range(MAX_AUDIT_PAGES):
                page = uow.audit.list_page(PageRequest(limit=100, cursor=cursor))
                for event in page.items:
                    if since is not None and event.occurred_at < since:
                        return found, total
                    if event.action.startswith(LOCAL_ACTIONS):
                        continue
                    total += 1
                    if len(found) < MAX_EDITS:
                        found.append(_edit(event))
                # Without a fork point only the newest edits are listed.
                if (since is None and total >= MAX_EDITS) or (cursor := page.next_cursor) is None:
                    break
        return found, total


def _row(row: Any) -> dict[str, str]:
    """Describe a discarded row for the report.

    Args:
        row: A ``DiscardedRow``.

    Returns:
        dict: ``table``, ``key``, ``label``, ``change``.

    Raises:
        None
    """
    return {"table": row.table, "key": row.key, "label": row.label, "change": row.change}


def _edit(event: Any) -> dict[str, Any]:
    """Describe one audited edit for the report.

    Args:
        event: An ``AuditEvent``.

    Returns:
        dict: When, who, what, on which object, and its redacted summary.

    Raises:
        None
    """
    return {"at": event.occurred_at.isoformat(timespec="seconds"), "actor": event.actor,
            "action": event.action, "object_type": event.object_type,
            "object_id": str(event.object_id) if event.object_id else None,
            "summary": event.summary}  # fmt: skip
