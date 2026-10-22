"""The node-local record of observation pulls, for health, metrics, and views (MPOS-006, 27.5 #7).

The collector writes ``/var/lib/vakr/replication/observation-pull.json``
after every pull: when the newest attempt and the newest success were, why
the newest attempt failed, and per partner collector the cursor, the
partner's newest seq, the backlog between them, the timestamp of the newest
observation stored here, the gaps found, and counts. ``vakr check health``
ages it against E.13 (warning 5 min, critical 30 min); the API's metrics and
health detail read it. A replication failure is node health, never target
state (MPOS-006).

Dependencies:
    json, os: The atomic write (temporary file, fsync, rename).

Safety:
    Holds times, seqs, counts, names, positions, a URL, and an error text
    that names a URL or a file, never the token.

Example:
    >>> blank().pulls
    {'ok': 0, 'failed': 0}
"""

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FILE = "replication/observation-pull.json"
"""Under the state directory (``/var/lib/vakr``)."""
_MAX_ERROR = 300
_MAX_BYTES = 64 * 1024
MAX_SOURCES = 8


@dataclass(slots=True)
class SourceStatus:
    """One partner collector as this node replicates it.

    Attributes:
        name: The collector's name, as the partner reported it.
        position: Its position label.
        cursor: Newest partner seq stored here.
        newest_seq: Newest seq the partner's log held at the last exchange.
        newest_ts: Timestamp of the newest observation stored here (ISO 8601 UTC).
        gaps: Observations the partner no longer retained when asked (MPOS-005).
        inserted: Observations stored for the first time, since the record began.
        duplicates: Observations already held (replays), since the record began.
    """

    name: str | None = None
    position: str | None = None
    cursor: int = 0
    newest_seq: int | None = None
    newest_ts: str | None = None
    gaps: int = 0
    inserted: int = 0
    duplicates: int = 0

    @property
    def backlog(self) -> int:
        """Partner observations logged but not yet stored here.

        Args:
            None

        Returns:
            int: ``newest_seq - cursor``, at least 0.

        Raises:
            None
        """
        return max((self.newest_seq or 0) - self.cursor, 0)


@dataclass(slots=True)
class ObservationPullStatus:
    """What the newest observation pulls did.

    Attributes:
        partner_url: Where this node pulls from.
        last_attempt_at: Newest attempt (ISO 8601 UTC), or ``None``.
        last_success_at: Newest attempt that completed.
        last_error: Why the newest attempt failed, or ``None``.
        sources: Per partner collector UUID.
        pulls: Attempts by result.
    """

    partner_url: str | None = None
    last_attempt_at: str | None = None
    last_success_at: str | None = None
    last_error: str | None = None
    sources: dict[str, SourceStatus] = field(default_factory=dict)
    pulls: dict[str, int] = field(default_factory=lambda: {"ok": 0, "failed": 0})

    def lag_s(self, now: datetime) -> float | None:
        """Seconds between ``now`` and the oldest of the sources' newest observations.

        Args:
            now: Current time (UTC).

        Returns:
            float | None: The worst lag, or ``None`` before any observation arrived.

        Raises:
            None
        """
        stamps = [datetime.fromisoformat(s.newest_ts) for s in self.sources.values()
                  if s.newest_ts]  # fmt: skip
        return None if not stamps else max((now - min(stamps)).total_seconds(), 0.0)


def blank() -> ObservationPullStatus:
    """Return an empty record.

    Args:
        None

    Returns:
        ObservationPullStatus: No attempts yet.

    Raises:
        None
    """
    return ObservationPullStatus()


def read_status(path: Path) -> ObservationPullStatus | None:
    """Read the record, or ``None`` when there is none or it cannot be used.

    Args:
        path: The record file.

    Returns:
        ObservationPullStatus | None: The record.

    Raises:
        None
    """
    try:
        with path.open("rb") as handle:
            data = json.loads(handle.read(_MAX_BYTES + 1)[:_MAX_BYTES])
        sources = {k: SourceStatus(**v) for k, v in data.pop("sources", {}).items()}
        return ObservationPullStatus(**data, sources=sources)
    except OSError, ValueError, TypeError, AttributeError, RecursionError:
        return None


def write_status(path: Path, record: ObservationPullStatus) -> None:
    """Write the record atomically.

    Args:
        path: The record file.
        record: The record.

    Returns:
        None

    Raises:
        OSError: If the file cannot be written.
    """
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    body: dict[str, Any] = asdict(record)
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(body, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    temp.replace(path)


def stamp(moment: datetime) -> str:
    """Format a time for the record.

    Args:
        moment: An aware time.

    Returns:
        str: ISO 8601 in UTC.

    Raises:
        None
    """
    return moment.astimezone(UTC).isoformat()


def short_error(text: str) -> str:
    """Bound an error message for the record.

    Args:
        text: The message.

    Returns:
        str: At most 300 characters.

    Raises:
        None
    """
    return text[:_MAX_ERROR]
