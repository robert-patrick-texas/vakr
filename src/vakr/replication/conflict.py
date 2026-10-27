"""Two primaries: a primary whose partner says it is a primary too (MPOS-006, ADR-024, E.39).

The observation puller records what the partner's state answered in
``partner.json``. A primary whose partner said ``primary`` after this
node's own newest promotion reports CRITICAL node health, naming the node
that looks like the old primary (the one promoted earlier, or never) and
the command to run there. It never touches a target's state.

Dependencies:
    vakr.replication.role_record: The partner and role records.
    vakr.config.node: The node settings.

Safety:
    Read-only; messages name positions, URLs, and times.

Example:
    >>> from vakr.config.node import NodeSettings
    >>> two_primaries(NodeSettings("primary", "east", "", "https://w"), {"role": "peer"}, None)
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vakr.config.node import PRIMARY, NodeSettings
from vakr.replication import role_record

DOC = "docs/operations/two-positions.md#when-the-old-primary-comes-back"


def conflict_at(state_dir: Path, node: NodeSettings) -> str | None:
    """Read the records and say whether this node and its partner are both primaries.

    Args:
        state_dir: ``/var/lib/vakr``.
        node: This node's settings.

    Returns:
        str | None: The CRITICAL summary, or ``None``.

    Raises:
        None
    """
    partner = role_record.read_json(state_dir / role_record.PARTNER_FILE)
    own = role_record.promotion(state_dir / role_record.ROLE_FILE)
    return two_primaries(node, partner, own["at"] if own else None)


def two_primaries(node: NodeSettings, partner: Mapping[str, Any] | None,
                  own_promoted_at: str | None) -> str | None:  # fmt: skip
    """Say why this node and its partner are both primaries, or ``None`` (E.39).

    A partner seen before this node's own promotion does not count: as a
    peer, this node saw its primary say ``primary``, which says nothing
    about whether that old primary is still one.

    Args:
        node: This node's settings.
        partner: ``partner.json``: what the partner's state reported last.
        own_promoted_at: When this node was promoted, if it was.

    Returns:
        str | None: The CRITICAL summary, naming the node to demote.

    Raises:
        None
    """
    if node.is_peer or not node.peer_url or not partner or partner.get("role") != PRIMARY:
        return None
    if own_promoted_at and _moment(partner.get("seen_at")) <= _moment(own_promoted_at):
        return None
    where = partner.get("position") or node.peer_url
    theirs = partner.get("promoted_at")
    if theirs and (not own_promoted_at or _moment(theirs) > _moment(own_promoted_at)):
        advice = f"{where} was promoted at {theirs}: this node looks like the old primary; " \
            "run `sudo vakr node demote` here"  # fmt: skip
    elif own_promoted_at:
        advice = f"this node was promoted at {own_promoted_at}: {where} looks like the old " \
            "primary; run `sudo vakr node demote` there"  # fmt: skip
    else:
        advice = "run `sudo vakr node demote` on the node whose metadata edits may be discarded"
    return f"two primaries: this node and {where} both have node.role = primary, and " \
        f"metadata edits on either diverge; {advice} ({DOC})"  # fmt: skip


def _moment(text: object) -> datetime:
    """Read a recorded time; unreadable or missing is the earliest time.

    Args:
        text: ISO 8601 text from a record.

    Returns:
        datetime: The time (aware), or ``datetime.min`` in UTC.

    Raises:
        None
    """
    try:
        found = datetime.fromisoformat(text) if isinstance(text, str) else None
    except ValueError:
        found = None
    if found is None or found.tzinfo is None:
        return datetime.min.replace(tzinfo=UTC)
    return found
