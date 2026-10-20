r"""Read ``net.ipv4.ping_group_range`` and work out the exact fix (ICMP-012).

Linux lets a process open an unprivileged ICMP socket (IPv4 and IPv6 alike)
only if its effective or a supplementary GID lies in this inclusive range.
systemd's default is ``0 2147483647``, but hardened images often narrow it,
for example to ``1000 9999`` for human users. ``systemd-sysusers`` gives the
``vakr`` service group a system GID below 1000, so a default install on such
a host is refused.

The range is one interval, so the fix widens it just enough to include the
GID, never narrowing it for anyone else. The fix goes in a sysctl.d drop-in
that sorts after the usual local files; if a file read later still sets the
key, the fix names that file instead, because a drop-in would be overridden.

Dependencies:
    dataclasses, pathlib: Values and file access.

Safety:
    Read-only. Never changes a sysctl: the operator applies the fix.

Example:
    >>> current = parse_range("1000\t9999\n")
    >>> 987 in current, str(current.widened(987))
    (False, '987 9999')
"""

from dataclasses import dataclass
from pathlib import Path

KEY = "net.ipv4.ping_group_range"
PROC_FILE = "proc/sys/net/ipv4/ping_group_range"
DROP_IN = "etc/sysctl.d/99-vakr-icmp.conf"
SYSCTL_CONF = "etc/sysctl.conf"
SEARCH_DIRS = (
    "etc/sysctl.d",
    "run/sysctl.d",
    "usr/local/lib/sysctl.d",
    "usr/lib/sysctl.d",
    "lib/sysctl.d",
)
"""Directories ``sysctl --system`` reads, highest precedence first (procps, sysctl.d(5))."""
MAX_FILE_BYTES = 1 << 20  # sysctl files are tiny; anything bigger is skipped


@dataclass(frozen=True, slots=True)
class GroupRange:
    """An inclusive GID range; ``low > high`` means nobody (the kernel's ``1 0``).

    Attributes:
        low: First GID allowed.
        high: Last GID allowed.
    """

    low: int
    high: int

    def __contains__(self, gid: object) -> bool:
        """Report whether a GID is allowed.

        Args:
            gid: Group ID.

        Returns:
            bool: True when ``low <= gid <= high``.

        Raises:
            None
        """
        return isinstance(gid, int) and self.low <= gid <= self.high

    def __str__(self) -> str:
        """Render as the sysctl writes it.

        Args:
            None

        Returns:
            str: ``"low high"``.

        Raises:
            None
        """
        return f"{self.low} {self.high}"

    def widened(self, gid: int) -> GroupRange:
        """Return the smallest range containing this one and ``gid``.

        Args:
            gid: Group ID to admit.

        Returns:
            GroupRange: ``gid gid`` for an empty range, otherwise the union.

        Raises:
            None
        """
        if self.low > self.high:
            return GroupRange(gid, gid)
        return GroupRange(min(self.low, gid), max(self.high, gid))


@dataclass(frozen=True, slots=True)
class Setting:
    """One line in a sysctl file that sets the key.

    Attributes:
        path: The file.
        line: 1-based line number.
        value: The value written there.
    """

    path: Path
    line: int
    value: str


@dataclass(frozen=True, slots=True)
class Fix:
    """What the operator should do.

    Attributes:
        range: The range to configure.
        file: The file to write or edit.
        override: The later setting that a drop-in would lose to, if any.
    """

    range: GroupRange
    file: Path
    override: Setting | None

    def text(self) -> str:
        """Render the fix as commands to run.

        Args:
            None

        Returns:
            str: One line of shell an operator can paste.

        Raises:
            None
        """
        line = f"{KEY} = {self.range}"
        if self.override is not None:
            where = f"{self.override.path} line {self.override.line} (read after any drop-in)"
            return f"edit {where} to read '{line}', then run sudo sysctl --system"
        return f"echo '{line}' | sudo tee {self.file} && sudo sysctl --system"


def parse_range(text: str) -> GroupRange:
    """Parse the two GIDs the kernel or a sysctl file holds.

    Args:
        text: Two integers separated by whitespace.

    Returns:
        GroupRange: The range.

    Raises:
        ValueError: If ``text`` is not two integers.
    """
    low, high = (int(part) for part in text.split())
    return GroupRange(low, high)


def read_range(root: Path) -> GroupRange:
    """Read the kernel's current value.

    Args:
        root: Filesystem root, ``/`` outside tests.

    Returns:
        GroupRange: The running value.

    Raises:
        OSError: If the file cannot be read.
        ValueError: If its contents are malformed.
    """
    return parse_range((root / PROC_FILE).read_text(encoding="ascii"))


def _sysctl_files(root: Path) -> list[Path]:
    """List sysctl files in the order ``sysctl --system`` applies them.

    Args:
        root: Filesystem root.

    Returns:
        list[Path]: ``*.conf`` files, one per name (earlier directories
        win), sorted by name, then ``/etc/sysctl.conf``.

    Raises:
        None
    """
    chosen: dict[str, Path] = {}
    for directory in SEARCH_DIRS:
        base = root / directory
        for path in sorted(base.glob("*.conf")) if base.is_dir() else ():
            chosen.setdefault(path.name, path)
    files = [chosen[name] for name in sorted(chosen)]
    return [*files, root / SYSCTL_CONF] if (root / SYSCTL_CONF).is_file() else files


def find_settings(root: Path) -> list[Setting]:
    """Find every line that sets the key, in the order they are applied.

    Args:
        root: Filesystem root.

    Returns:
        list[Setting]: Settings; the last one is the persistent value.
        Unreadable or oversized files are skipped.

    Raises:
        None
    """
    found: list[Setting] = []
    for path in _sysctl_files(root):
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for number, raw in enumerate(lines, start=1):
            key, sep, value = raw.strip().partition("=")
            key = key.strip().lstrip("-").strip().replace("/", ".")
            if sep and key == KEY and not raw.lstrip().startswith(("#", ";")):
                found.append(Setting(path, number, value.strip()))
    return found


def plan_fix(current: GroupRange, gid: int, root: Path) -> Fix:
    """Work out how to admit ``gid`` without narrowing the range for anyone.

    Args:
        current: The running range.
        gid: The collector's group ID.
        root: Filesystem root.

    Returns:
        Fix: The range to set and where to set it.

    Raises:
        None
    """
    drop_in = root / DROP_IN
    later = [
        s for s in find_settings(root) if s.path.name > drop_in.name or s.path == root / SYSCTL_CONF
    ]
    return Fix(current.widened(gid), Path("/") / DROP_IN, later[-1] if later else None)
