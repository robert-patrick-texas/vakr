"""Remember which release a deployment ran before its latest upgrade.

``new-options`` must answer "what did this upgrade add?" without the operator
remembering version numbers (CFGM-004). Each successful ``config upgrade``
records the running release in a small JSON state file; when the release
changes, the old one becomes ``previous``, which is the baseline for
new-option reporting.

Dependencies:
    json: State file format.

Safety:
    Written atomically. A corrupt state file is replaced rather than
    blocking an upgrade, because it only feeds reporting.

Example:
    >>> import tempfile
    >>> path = Path(tempfile.mkdtemp()) / "config-state.json"
    >>> record_version(path, "1.0.0").previous is None
    True
    >>> record_version(path, "1.1.0").previous
    '1.0.0'
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .fileio import atomic_write


@dataclass(frozen=True, slots=True)
class DeploymentState:
    """Releases this deployment has run.

    Attributes:
        current: Release recorded by the latest upgrade, if any.
        previous: Release before that, if any.
    """

    current: str | None = None
    previous: str | None = None


def read_state(path: Path) -> DeploymentState:
    """Read the state file; a missing file means nothing is recorded yet.

    Args:
        path: State file.

    Returns:
        DeploymentState: The recorded releases.

    Raises:
        ValueError: If the file exists but is not valid JSON.
        TypeError: If the JSON is not an object.
        OSError: If the file exists but cannot be read.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return DeploymentState()
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: not valid JSON") from exc
    if not isinstance(data, dict):
        raise TypeError(f"{path}: expected a JSON object")
    current, previous = data.get("current_version"), data.get("previous_version")
    return DeploymentState(
        current if isinstance(current, str) else None,
        previous if isinstance(previous, str) else None,
    )


def record_version(path: Path, version: str, now: datetime | None = None) -> DeploymentState:
    """Record ``version`` as current, shifting a different current to previous.

    Recording the same version again changes nothing, so repeated upgrades
    of one release keep the true previous release.

    Args:
        path: State file; its directory is created if needed.
        version: Release now running.
        now: Timestamp to record; ``None`` means now.

    Returns:
        DeploymentState: The state after recording.

    Raises:
        OSError: If the state file cannot be written.
    """
    try:
        state = read_state(path)
    except ValueError, TypeError:
        state = DeploymentState()
    if state.current == version:
        return state
    new = DeploymentState(version, state.current or state.previous)
    payload = {
        "current_version": new.current,
        "previous_version": new.previous,
        "recorded_at": (now or datetime.now(UTC)).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, (json.dumps(payload, indent=2) + "\n").encode("utf-8"))
    return new
