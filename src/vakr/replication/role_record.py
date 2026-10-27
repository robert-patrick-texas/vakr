"""This node's role changes, its pending resync, and the resync report (MPOS-003, ADR-024, E.39).

Three small JSON files under ``/var/lib/vakr/replication``:

- ``node-role.json``: every ``vakr node promote`` and ``demote`` on this
  node, newest last, at most ``MAX_CHANGES``: when, by whom, forced or
  not, the ``[node]`` settings before and after (the record of the old
  value), the newest metadata pull, what the old partner answered, and
  the configuration backup. The newest promotion's time and fork point
  (the newest pull from the old primary) are served to the partner in
  both replication state answers.
- ``resync.json``: written by ``vakr node demote``; while it exists, the
  collector's next successful metadata pull replaces this node's
  replicated metadata with the new primary's, then removes it.
- ``resync-report.json``: written by that pull: the backup taken, the
  rows discarded, and this node's metadata edits since the fork point.
- ``partner.json``: the role, position, and promotion time the partner's
  observation state reported at the newest pull, written by the
  observation puller; two primaries are found from it.

Dependencies:
    json, os: Atomic writes (temporary file, fsync, rename).

Safety:
    The records hold times, URLs, labels, keys, audit summaries, and paths,
    never a token, a password hash, or a column value. Files written as
    root get the directory's owner, so the ``vakr`` services can read them.

Example:
    >>> read_changes(Path("/nonexistent"))
    []
"""

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ROLE_FILE = "replication/node-role.json"
RESYNC_FILE = "replication/resync.json"
REPORT_FILE = "replication/resync-report.json"
PARTNER_FILE = "replication/partner.json"
"""Under the state directory (``/var/lib/vakr``)."""
MAX_CHANGES = 20
_MAX_BYTES = 1024 * 1024


def read_json(path: Path) -> dict[str, Any] | None:
    """Read one record; ``None`` when there is none or it is unusable.

    Args:
        path: The file.

    Returns:
        dict | None: The document.

    Raises:
        None
    """
    try:
        with path.open("rb") as handle:
            doc = json.loads(handle.read(_MAX_BYTES))
    except OSError, ValueError, RecursionError:
        return None
    return doc if isinstance(doc, dict) else None


def write_json(path: Path, doc: Mapping[str, Any]) -> None:
    """Replace one record atomically, mode 0640, owned like its directory when root writes it.

    Args:
        path: The file.
        doc: The document.

    Returns:
        None

    Raises:
        OSError: If it cannot be written.
    """
    _ensure_dir(path.parent)
    part = path.with_name(path.name + ".part")
    with part.open("w", encoding="utf-8") as handle:
        json.dump(doc, handle, sort_keys=True, indent=1)
        handle.flush()
        os.fsync(handle.fileno())
    part.chmod(0o640)
    if os.geteuid() == 0:
        owner = path.parent.stat()
        os.chown(part, owner.st_uid, owner.st_gid)
    part.replace(path)


def _ensure_dir(directory: Path) -> None:
    """Create the replication directory like the collector does, owned like the state directory.

    Args:
        directory: ``/var/lib/vakr/replication``.

    Returns:
        None

    Raises:
        OSError: If it cannot be created.
    """
    if directory.is_dir():
        return
    directory.mkdir(mode=0o750, parents=True, exist_ok=True)
    if os.geteuid() == 0:
        owner = directory.parent.stat()
        os.chown(directory, owner.st_uid, owner.st_gid)


def read_changes(path: Path) -> list[dict[str, Any]]:
    """Return the recorded role changes, oldest first.

    Args:
        path: ``node-role.json``.

    Returns:
        list[dict]: The changes; empty without a record.

    Raises:
        None
    """
    doc = read_json(path) or {}
    changes = doc.get("changes")
    return [c for c in changes if isinstance(c, dict)] if isinstance(changes, list) else []


def append_change(path: Path, change: Mapping[str, Any]) -> None:
    """Add one role change, keeping the newest ``MAX_CHANGES``.

    Args:
        path: ``node-role.json``.
        change: The change.

    Returns:
        None

    Raises:
        OSError: If the record cannot be written.
    """
    changes = [*read_changes(path), dict(change)][-MAX_CHANGES:]
    write_json(path, {"changes": changes})


def promotion(path: Path) -> dict[str, str | None] | None:
    """Describe this node's newest role change if it was a promotion, for the partner.

    Args:
        path: ``node-role.json``.

    Returns:
        dict | None: ``{"at", "fork_at"}``: when it was promoted, and its newest
        successful metadata pull from the old primary; ``None`` otherwise.

    Raises:
        None
    """
    changes = read_changes(path)
    newest = changes[-1] if changes else None
    if newest is None or newest.get("action") != "promote":
        return None
    at, fork = newest.get("at"), newest.get("last_pull_at")
    return {"at": at if isinstance(at, str) else None,
            "fork_at": fork if isinstance(fork, str) else None}  # fmt: skip


def note_partner(path: Path, state: Any, at: str | None) -> None:
    """Record the role the partner's observation state answer reported (two primaries, E.39).

    Args:
        path: ``partner.json``.
        state: The partner's ``observations/state`` answer.
        at: When it answered (ISO 8601 UTC).

    Returns:
        None

    Raises:
        OSError: If the record cannot be written.
    """
    doc: dict[str, Any] = state if isinstance(state, dict) else {}
    promo = doc.get("promotion")
    promoted_at = promo.get("at") if isinstance(promo, dict) else None
    write_json(path, {"role": _text(doc.get("role")), "position": _text(doc.get("position")),
                      "promoted_at": _text(promoted_at), "seen_at": at})  # fmt: skip


def _text(value: object) -> str | None:
    """Keep a short non-empty string from a partner's answer.

    Args:
        value: The answer's value.

    Returns:
        str | None: At most 64 characters, or ``None``.

    Raises:
        None
    """
    return value[:64] if isinstance(value, str) and value else None


def promotion_reader(state_dir: Path) -> Any:
    """Return a reader of ``promotion`` for the API's replication state answers.

    Args:
        state_dir: The state directory.

    Returns:
        Callable[[], dict | None]: Reads the record at each call (a small file).

    Raises:
        None
    """
    return lambda: promotion(state_dir / ROLE_FILE)
