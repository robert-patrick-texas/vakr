"""What ``vakr node status`` shows: role, primary, newest pull, conflicts, and resync (E.39).

One place reads every record a role change touches: the metadata pull
record (a peer), the partner's role as the newest observation pull saw it
(a primary), the role changes, a pending resync, and the newest resync
report. Nothing here contacts the partner.

Dependencies:
    vakr.replication: The records and the two-primaries rule.

Safety:
    Read-only. The output names URLs, positions, keys, and audit summaries,
    never a token or a column value.

Example:
    >>> from pathlib import Path
    >>> from datetime import UTC, datetime
    >>> lines, _ = node_status(InstallPaths.under(Path("/nonexistent")), NodeSettings(),
    ...                        datetime(2026, 10, 27, tzinfo=UTC))
    >>> lines[0]
    'role primary, position (none)'
"""

from dataclasses import asdict
from datetime import datetime
from typing import Any

from vakr.config.node import NodeSettings
from vakr.paths import InstallPaths
from vakr.query.text import age_text
from vakr.replication import role_record, status
from vakr.replication.conflict import conflict_at


def node_status(paths: InstallPaths, node: NodeSettings, now: datetime) -> tuple[list[str], dict]:
    """Describe this node's role and its records.

    Args:
        paths: The layout.
        node: This node's settings.
        now: Current time (UTC).

    Returns:
        tuple: Text lines and the JSON document.

    Raises:
        None
    """
    state = paths.state_dir
    pull = status.read_status(state / status.FILE) if node.is_peer else None
    partner = role_record.read_json(state / role_record.PARTNER_FILE)
    changes = role_record.read_changes(state / role_record.ROLE_FILE)
    conflict = conflict_at(state, node)
    resync = role_record.read_json(state / role_record.RESYNC_FILE)
    report = role_record.read_json(state / role_record.REPORT_FILE)
    lines = [f"role {node.role}, position {node.position or '(none)'}"]
    lines += _primary_lines(node, pull, partner, now)
    lines += [f"CRITICAL {conflict}"] if conflict else []
    if changes:
        newest = changes[-1]
        forced = " (forced)" if newest.get("forced") else ""
        lines.append(f"newest role change: {newest.get('action')} at {newest.get('at')} by "
                     f"{newest.get('actor')}{forced}")  # fmt: skip
    if resync:
        lines.append(f"resync pending since {resync.get('at')}: the next successful pull from "
                     f"{resync.get('primary_url')} replaces this node's metadata")  # fmt: skip
    lines += _report_lines(report) if report else []
    data = {"role": node.role, "position": node.position, "primary_url": node.primary_url or None,
            "peer_url": node.peer_url or None,
            "metadata_pull": None if pull is None else asdict(pull),
            "partner": partner, "two_primaries": conflict, "role_changes": changes,
            "resync_pending": resync, "resync_report": report}  # fmt: skip
    return lines, data


def _primary_lines(node: NodeSettings, pull: status.PullStatus | None,
                   partner: dict[str, Any] | None, now: datetime) -> list[str]:  # fmt: skip
    """Name the primary and the newest pull, or the partner of a primary.

    Args:
        node: This node's settings.
        pull: The metadata pull record (a peer).
        partner: What the partner's observation state said last.
        now: Current time.

    Returns:
        list[str]: The lines.

    Raises:
        None
    """
    if node.is_peer:
        where = f" ({pull.primary_position})" if pull and pull.primary_position else ""
        age = None if pull is None else pull.success_age_s(now)
        newest = "none has succeeded" if age is None or pull is None else \
            f"{pull.last_success_at} ({age_text(age)} ago)"  # fmt: skip
        failed = f"; newest attempt failed: {pull.last_error}" if pull and pull.last_error else ""
        return [f"primary {node.primary_url}{where}", f"newest metadata pull: {newest}{failed}"]
    lines = ["primary: this node"]
    if node.peer_url:
        role = partner.get("role") if partner else None
        seen = f", as of {partner.get('seen_at')}" if partner and partner.get("seen_at") else ""
        lines.append(f"partner {node.peer_url}: {role or 'role not seen yet'}{seen}")
    return lines


def _report_lines(report: dict[str, Any]) -> list[str]:
    """Summarise the newest resync report.

    Args:
        report: ``resync-report.json``.

    Returns:
        list[str]: The lines: totals, the backup, and the first discarded rows and edits.

    Raises:
        None
    """
    lines = [f"resynced at {report.get('at')} from {report.get('primary_url')}: "
             f"{report.get('discarded_total', 0)} local row(s) discarded, "
             f"{report.get('edits_total', 0)} metadata edit(s) here since "
             f"{report.get('fork_at') or 'an unknown fork point'}; backup "
             f"{report.get('backup') or 'not needed'}"]  # fmt: skip
    rows, edits = _list(report, "discarded"), _list(report, "edits")
    lines += [f"  discarded {r.get('change')} {r.get('table')} {r.get('label') or r.get('key')}"
              for r in rows[:20] if isinstance(r, dict)]  # fmt: skip
    lines += [f"  edit {e.get('at')} {e.get('actor')} {e.get('action')}: {e.get('summary')}"
              for e in edits[:20] if isinstance(e, dict)]  # fmt: skip
    if len(rows) > 20 or len(edits) > 20:
        lines.append("  ... the whole list: vakr node status --json")
    return lines


def _list(report: dict[str, Any], key: str) -> list[Any]:
    """Return one list of the report, or an empty one.

    Args:
        report: The report.
        key: ``discarded`` or ``edits``.

    Returns:
        list: The entries.

    Raises:
        None
    """
    found = report.get(key)
    return found if isinstance(found, list) else []
