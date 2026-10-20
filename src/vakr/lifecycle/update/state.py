"""Durable record of the last update attempt, read by health checks.

``vakr check health`` runs every minute and must report a failed update
without touching the network (UPD-006), and the timer must not retry a
version that already failed until something newer is published. Both read
this small JSON file, which self-update rewrites atomically after every
attempt.

Dependencies:
    json, dataclasses: Standard library only.

Safety:
    The file holds versions, timestamps, and short messages only; never
    secrets. Writes go to a temporary file renamed over the old one, so a
    crash never leaves a truncated record. Mode 0644 lets an unprivileged
    monitoring agent read it.

Example:
    >>> UpdateState().result
    'never'
"""

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

STATE_MODE = 0o644
RESULTS = ("never", "success", "failed", "refused", "rolled-back")
"""``refused`` means verification failed before any change was made."""


class StateError(Exception):
    """The update state file exists but cannot be parsed."""


@dataclass(frozen=True, slots=True)
class UpdateState:
    """Outcome of the most recent update attempt.

    Attributes:
        result: One of ``RESULTS``.
        at: ISO-8601 UTC time of the attempt, or ``""``.
        from_version: Version installed before the attempt.
        to_version: Version the attempt targeted.
        message: One-line operator-readable outcome.
        failed_versions: Versions (semver) whose install failed; the timer
            skips them (UPD-006).
        backup: Path of the pre-update backup archive, or ``""``.
    """

    result: str = "never"
    at: str = ""
    from_version: str = ""
    to_version: str = ""
    message: str = ""
    failed_versions: tuple[str, ...] = field(default_factory=tuple)
    backup: str = ""


def load_state(path: Path) -> UpdateState:
    """Read the state file; a missing file means no update was ever attempted.

    Args:
        path: State file path.

    Returns:
        UpdateState: The recorded state, or the default.

    Raises:
        StateError: When the file is unreadable or malformed.
    """
    if not path.exists():
        return UpdateState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StateError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("result") not in RESULTS:
        raise StateError(f"{path} is not an update state record")
    known = {f.name for f in fields(UpdateState)}
    values = {k: v for k, v in data.items() if k in known}
    values["failed_versions"] = tuple(str(v) for v in values.get("failed_versions") or ())
    return UpdateState(**values)


def save_state(path: Path, state: UpdateState) -> None:
    """Write ``state`` atomically at mode 0644.

    Args:
        path: State file path; its directory is created if missing.
        state: Record to write.

    Returns:
        None

    Raises:
        OSError: When the file cannot be written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    payload = asdict(state)
    payload["failed_versions"] = list(state.failed_versions)
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.chmod(STATE_MODE)
    temp.replace(path)
