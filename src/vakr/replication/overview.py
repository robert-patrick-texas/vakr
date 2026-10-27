"""Replication health in one reading, for the views' health line (27.5 item 7, MPOS-006).

``replication_reader`` returns the callable the status queries put in
``HealthView.replication``: on a node with a partner, how old the peer's
metadata is and how far behind the other position's observations are, with
the backlog and any gap, and ``TWO PRIMARIES`` when a primary's partner
is a primary too (E.39); ``None`` on a node without a partner, so a single
node's header is unchanged. ``vakr status``, the TUI header, and the web UI
health panel all show it. The numbers come from the records the collector
writes after every pull; nothing here contacts the partner.

Dependencies:
    vakr.replication.status, observation_status: The two records.
    vakr.query.views: ``Reading``.

Safety:
    The text names positions, ages, and counts, and the newest error, which
    names a URL or a file, never the token.

Example:
    >>> from pathlib import Path
    >>> from vakr.config.node import NodeSettings
    >>> replication_reader(Path("/nonexistent"), NodeSettings())() is None
    True
"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from vakr.config.node import NodeSettings
from vakr.query.text import age_text
from vakr.query.views import Reading
from vakr.replication import observation_status as obs
from vakr.replication import status
from vakr.replication.conflict import conflict_at


def replication_reader(state_dir: Path, node: NodeSettings) -> Callable[[], Reading | None]:
    """Return the reader of this node's replication health.

    Args:
        state_dir: Where the collector writes its records (``/var/lib/vakr``).
        node: This node's role and partner.

    Returns:
        Callable[[], Reading | None]: ``None`` on a node without a partner.

    Raises:
        None
    """

    def read() -> Reading | None:
        """Read both records now.

        Args:
            None

        Returns:
            Reading | None: The reading; ``value`` is the observation lag in seconds.

        Raises:
            None
        """
        if not node.partner_url:
            return None
        now = datetime.now(UTC)
        parts = [_metadata(state_dir, now)] if node.is_peer else []
        if conflict_at(state_dir, node):
            parts.append("TWO PRIMARIES: the partner is a primary too; see vakr node status")
        record = obs.read_status(state_dir / obs.FILE)
        lag = None if record is None else record.lag_s(now)
        if record is None or lag is None:
            error = f" ({record.last_error})" if record is not None and record.last_error else ""
            parts.append(f"no observation pulled from the other position yet{error}")
            return Reading("; ".join(parts), None, "no observation replicated yet")
        backlog = sum(s.backlog for s in record.sources.values())
        gaps = sum(s.gaps for s in record.sources.values())
        where = ", ".join(sorted({s.position for s in record.sources.values() if s.position}))
        gap = f", {gaps} lost" if gaps else ""
        parts.append(f"{where or 'partner'} {age_text(lag)} behind (backlog {backlog}{gap})")
        if record.last_error:
            parts.append(f"newest pull failed: {record.last_error}")
        return Reading("; ".join(parts), round(lag, 1), stale=bool(record.last_error))

    return read


def _metadata(state_dir: Path, now: datetime) -> str:
    """Describe a peer's newest metadata pull.

    Args:
        state_dir: The state directory.
        now: Current time.

    Returns:
        str: Such as ``metadata 20 s old``.

    Raises:
        None
    """
    record = status.read_status(state_dir / status.FILE)
    age = None if record is None else record.success_age_s(now)
    return "metadata never pulled" if age is None else f"metadata {age_text(age)} old"
