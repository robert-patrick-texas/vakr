"""The node-local record of the newest metadata pull, for health and metrics (MPOS-006).

The peer's collector writes ``/var/lib/vakr/replication/metadata-pull.json``
after every attempt: when the newest attempt and the newest success were,
the error of a failed attempt in operator words, the digest applied, and
counts by result since the record began. ``vakr check health`` ages the
newest success against E.13 (warning 5 min, critical 30 min), and the API's
self-metrics read it. A pull failure is node health, never target state.

Dependencies:
    json, os: The atomic write (temporary file, fsync, rename).

Safety:
    The record holds times, a digest, counts, and an error message that
    names a URL or a file, never the token.

Example:
    >>> blank().pulls
    {'applied': 0, 'unchanged': 0, 'failed': 0}
"""

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

FILE = "replication/metadata-pull.json"
"""Under the state directory (``/var/lib/vakr``)."""
RESULTS = ("applied", "unchanged", "failed")
_MAX_ERROR = 300
_MAX_BYTES = 64 * 1024


@dataclass(slots=True)
class PullStatus:
    """What the newest metadata pulls did.

    Attributes:
        last_attempt_at: Newest attempt (ISO 8601 UTC), or ``None``.
        last_success_at: Newest attempt that applied or found nothing new.
        last_error: Why the newest attempt failed, or ``None`` if it did not.
        digest: Digest of the snapshot applied last.
        primary_position: The primary's position, as its state reported.
        pulls: Attempts by result.
    """

    last_attempt_at: str | None = None
    last_success_at: str | None = None
    last_error: str | None = None
    digest: str | None = None
    primary_position: str | None = None
    pulls: dict[str, int] = field(default_factory=lambda: dict.fromkeys(RESULTS, 0))

    def success_age_s(self, now: datetime) -> float | None:
        """Seconds since the newest success, or ``None`` if there was none.

        Args:
            now: Current time (UTC).

        Returns:
            float | None: The age.

        Raises:
            None
        """
        if self.last_success_at is None:
            return None
        try:
            return max((now - datetime.fromisoformat(self.last_success_at)).total_seconds(), 0.0)
        except ValueError:
            return None


def blank() -> PullStatus:
    """Return the record of a node that has not pulled yet.

    Args:
        None

    Returns:
        PullStatus: Empty.

    Raises:
        None
    """
    return PullStatus()


def read_status(path: Path) -> PullStatus | None:
    """Read the record; ``None`` when there is none or it is unusable.

    Args:
        path: The record file.

    Returns:
        PullStatus | None: The record.

    Raises:
        None
    """
    try:
        with path.open("rb") as handle:
            doc = json.loads(handle.read(_MAX_BYTES))
    except OSError, ValueError, RecursionError:
        return None
    if not isinstance(doc, dict):
        return None
    pulls = doc.get("pulls") if isinstance(doc.get("pulls"), dict) else {}
    counts = {k: int(v) for k, v in pulls.items() if k in RESULTS and isinstance(v, int)}
    text = {k: doc.get(k) if isinstance(doc.get(k), str) else None
            for k in ("last_attempt_at", "last_success_at", "last_error", "digest",
                      "primary_position")}  # fmt: skip
    return PullStatus(**text, pulls={**dict.fromkeys(RESULTS, 0), **counts})


def record(path: Path, result: str, now: datetime, *, error: str | None = None,
           digest: str | None = None, position: str | None = None) -> PullStatus:  # fmt: skip
    """Record one attempt, atomically.

    Args:
        path: The record file.
        result: ``applied``, ``unchanged``, or ``failed``.
        now: When it finished (UTC).
        error: Why it failed, in operator words.
        digest: The digest now applied, after a success.
        position: The primary's position, if its state answered.

    Returns:
        PullStatus: The new record.

    Raises:
        OSError: If the file cannot be written.
    """
    status = read_status(path) or blank()
    stamp = now.astimezone(UTC).isoformat(timespec="seconds")
    status.last_attempt_at = stamp
    status.pulls[result] = status.pulls.get(result, 0) + 1
    if result == "failed":
        status.last_error = (error or "the pull failed")[:_MAX_ERROR]
    else:
        status.last_success_at, status.last_error = stamp, None
        status.digest = digest or status.digest
    status.primary_position = position or status.primary_position
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    with part.open("w", encoding="utf-8") as handle:
        json.dump(asdict(status), handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    part.chmod(0o640)
    part.replace(path)
    return status
