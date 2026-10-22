"""CPU, memory, file descriptors, and start time of the running process (OBS-001, E.33).

Both ``vakr-api`` and the collector report these about themselves, from
``getrusage`` and ``/proc/self`` (Linux, as vakr is). A value that cannot
be read is ``None`` and its series is left out, never written as 0
(invariant 1).

Dependencies:
    os, resource, time: The readings.

Safety:
    Reads only the process's own ``/proc`` entries; never another process's.

Example:
    >>> stats = process_stats()
    >>> stats.cpu_s is not None and stats.cpu_s >= 0
    True
"""

import os
import resource
import time
from dataclasses import dataclass
from pathlib import Path

_PROC = Path("/proc")


@dataclass(frozen=True, slots=True)
class ProcessStats:
    """Readings of one process.

    Attributes:
        cpu_s: User plus system CPU seconds since start.
        rss_bytes: Resident memory now.
        open_fds: Open file descriptors now.
        start_s: Start time, Unix seconds.
    """

    cpu_s: float | None
    rss_bytes: int | None
    open_fds: int | None
    start_s: float | None


def _rss(proc: Path) -> int | None:
    """Read resident memory from ``statm``.

    Args:
        proc: ``/proc/self`` or a test directory.

    Returns:
        int | None: Bytes, or ``None`` when unreadable.

    Raises:
        None
    """
    try:
        pages = int((proc / "statm").read_text().split()[1])
    except OSError, ValueError, IndexError:
        return None
    return pages * os.sysconf("SC_PAGE_SIZE")


def _fds(proc: Path) -> int | None:
    """Count open file descriptors.

    Args:
        proc: ``/proc/self`` or a test directory.

    Returns:
        int | None: The count, or ``None`` when unreadable.

    Raises:
        None
    """
    try:
        return sum(1 for _ in (proc / "fd").iterdir())
    except OSError:
        return None


def _start(proc: Path, root: Path) -> float | None:
    """Work out the start time from ``stat`` field 22 and the boot time.

    Args:
        proc: ``/proc/self`` or a test directory.
        root: ``/proc``, for ``stat``'s ``btime``.

    Returns:
        float | None: Unix seconds, or ``None`` when unreadable.

    Raises:
        None
    """
    try:
        # The command name in parentheses may hold spaces, so split after its last ")".
        fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
        ticks = int(fields[19])
        boot = next(int(line.split()[1]) for line in (root / "stat").read_text().splitlines()
                    if line.startswith("btime "))  # fmt: skip
    except OSError, ValueError, IndexError, StopIteration:
        return None
    return boot + ticks / os.sysconf("SC_CLK_TCK")


def process_stats(proc: Path | None = None, root: Path = _PROC) -> ProcessStats:
    """Read the current process's statistics.

    Args:
        proc: Its ``/proc`` directory; ``/proc/self`` by default.
        root: The ``/proc`` root, for the boot time.

    Returns:
        ProcessStats: The readings; each is ``None`` when it cannot be read.

    Raises:
        None
    """
    proc = proc or root / "self"
    usage = resource.getrusage(resource.RUSAGE_SELF)
    cpu = usage.ru_utime + usage.ru_stime
    start = _start(proc, root)
    if start is not None and start > time.time() + 60:  # a clock step; the reading means nothing
        start = None
    return ProcessStats(cpu, _rss(proc), _fds(proc), start)
