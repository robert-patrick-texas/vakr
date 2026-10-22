"""The collector's snapshot file: atomic 0640 writes, and a strict read (E.33, invariant 1).

A snapshot is used whole or not at all: missing, stale, oversized,
unreadable, or corrupt files give no collector series and say why.

Dependencies:
    pytest; vakr.metrics.snapshot, catalog.

Example:
    $ uv run pytest tests/unit/metrics/test_metrics_snapshot.py
"""

import json
import os
from pathlib import Path

import pytest

from vakr.metrics.catalog import build
from vakr.metrics.snapshot import (
    MAX_SNAPSHOT_BYTES,
    STALE_AFTER_S,
    SnapshotTooLargeError,
    encode_snapshot,
    read_snapshot,
    write_snapshot,
)

T = 1_800_000_000.0
ROOT = os.geteuid() == 0


def _good() -> bytes:
    """Encode a small valid snapshot written at ``T``.

    Args:
        None

    Returns:
        bytes: The file content.

    Raises:
        None
    """
    families = [build("vakr_collector_workers", [((), 4)]),
                build("vakr_process_open_fds", [(("collector",), 12)]),
                build("vakr_collector_state", [(("healthy",), 1), (("degraded",), 0)])]  # fmt: skip
    return encode_snapshot(families, T)


def test_e33_writes_are_atomic_mode_0640_and_round_trip(tmp_path: Path) -> None:
    """E.33: a rename replaces the file whole; mode 0640 whatever the umask; it reads back.

    Args:
        tmp_path: Directory.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "collector-metrics.json"
    old = os.umask(0o077)
    try:
        write_snapshot(path, b"{}")
        write_snapshot(path, _good())
    finally:
        os.umask(old)
    assert path.stat().st_mode & 0o777 == 0o640
    assert [p.name for p in tmp_path.iterdir()] == [path.name]  # no temporary file left
    read = read_snapshot(path, T + 10)
    assert (read.reason, read.age_s) == ("ok", 10.0)
    assert [f.name for f in read.families] == ["vakr_collector_workers", "vakr_process_open_fds",
                                               "vakr_collector_state"]  # fmt: skip
    with pytest.raises(SnapshotTooLargeError):
        encode_snapshot([build("vakr_collector_workers", [((), 1)])] * 30_000, T)


def _doc(**changes: object) -> bytes:
    """Return the valid snapshot with top-level changes.

    Args:
        **changes: Keys to replace.

    Returns:
        bytes: JSON.

    Raises:
        None
    """
    return json.dumps({**json.loads(_good()), **changes}).encode()


def _family(name: str, *samples: object) -> bytes:
    """Return a snapshot holding one raw family.

    Args:
        name: Family name.
        *samples: Raw samples.

    Returns:
        bytes: JSON.

    Raises:
        None
    """
    return _doc(families=[{"name": name, "samples": list(samples)}])


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"not json", "corrupt"),
        (b"[1, 2]", "corrupt"),
        (b"[" * 100_000 + b"]" * 100_000, "corrupt"),
        (_doc(format=2), "corrupt"),
        (_doc(written_at="soon"), "corrupt"),
        (_doc(written_at=T + 3600), "corrupt"),  # far in the future: the clocks disagree
        (_doc(written_at=T - STALE_AFTER_S - 1), "stale"),
        (_family("vakr_collector_workers", [[], -1, ""]), "ok"),  # a gauge may be negative
        (_family("vakr_collector_polls_started_total", [[], -1, ""]), "corrupt"),
        (_family("vakr_collector_state", [[["state", "on fire"]], 1, ""]), "corrupt"),
        (_family("vakr_collector_workers", [[["device", "core1"]], 1, ""]), "corrupt"),
        (_family("vakr_process_open_fds", [[["process", "api"]], 1, ""]), "corrupt"),
        (_family("vakr_collector_workers", [[], True, ""]), "corrupt"),
        (_family("vakr_collector_workers", [[], 1, "_count"]), "corrupt"),
        (_family("vakr_api_connections", [[["listener", "tcp"]], 1, ""]), "ok"),  # not ours: skip
        (_family("vakr_from_a_newer_release", [[], 1, ""]), "ok"),
        (b" " * (MAX_SNAPSHOT_BYTES + 1), "too_large"),
    ],
    ids=lambda v: v if isinstance(v, str) else None,
)
def test_invariant1_a_bad_snapshot_gives_no_collector_series(
    tmp_path: Path, content: bytes, reason: str
) -> None:
    """Invariant 1, E.33: a snapshot is used whole or not at all, and the reason is named.

    Args:
        tmp_path: Directory.
        content: The file.
        reason: Expected outcome.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "s.json"
    path.write_bytes(content)
    read = read_snapshot(path, T)
    assert read.reason == reason
    if reason != "ok":
        assert read.families == ()
    else:
        names = {f.name for f in read.families}
        assert "vakr_api_connections" not in names
        assert "vakr_from_a_newer_release" not in names


def test_missing_and_unreadable(tmp_path: Path) -> None:
    """E.33: no file is ``missing``; a file this account cannot read is ``unreadable``.

    Args:
        tmp_path: Directory.

    Returns:
        None

    Raises:
        None
    """
    assert read_snapshot(tmp_path / "none.json", T).reason == "missing"
    assert read_snapshot(None, T).reason == "missing"
    if not ROOT:
        locked = tmp_path / "locked.json"
        locked.write_bytes(_good())
        locked.chmod(0o000)
        assert read_snapshot(locked, T).reason == "unreadable"
