"""ICMP preflight and self-health check for ``vakr doctor`` and ``check health`` (ICMP-012).

The collector needs ``net.ipv4.ping_group_range`` to include its group. On
an installed node that is the ``vakr`` service user's primary GID, whoever
runs the check; in a development checkout (``VAKR_ROOT`` set, or no ``vakr``
account) it is this process's groups, and the check also opens an ICMP
socket to prove it. When the range excludes the group, the summary carries
the exact fix: a sysctl.d drop-in line and ``sysctl --system``.

The result is WARNING, not CRITICAL. A wrong sysctl is a host setting that
a rollback cannot fix, and ``check health`` gates self-update (UPD-006): a
CRITICAL here would roll back every update on a host that has not applied
the fix yet. Devices still show UNKNOWN, never down, while it is wrong.

Dependencies:
    os, pwd, socket: Identity lookup and the socket test.
    vakr.adapters.icmp.sysctl: Range parsing and the fix.
    vakr.health.model: Check results.

Safety:
    Read-only; never changes a sysctl or a unit. The socket test sends
    nothing.

Example:
    >>> check_icmp().name
    'icmp'
"""

import os
import pwd
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from vakr.adapters.icmp import sysctl
from vakr.health.model import CheckResult, Status

SERVICE_USER = "vakr"
FALLBACK_DIR = "etc/systemd/system/vakr-collector.service.d"
"""Where the documented ``CAP_NET_RAW`` drop-in is installed (packaging/examples)."""


@dataclass(frozen=True, slots=True)
class Identity:
    """Whose groups the kernel will check when the collector opens a socket.

    Attributes:
        label: Operator-readable name, such as "the vakr service user".
        gids: Groups to test; the first is the one the fix admits.
        service: True for the installed service account.
    """

    label: str
    gids: tuple[int, ...]
    service: bool


def collector_identity(
    environ: Mapping[str, str] | None = None,
    lookup: Callable[[str], pwd.struct_passwd] = pwd.getpwnam,
) -> Identity:
    """Decide whose groups to check.

    Args:
        environ: Environment; ``os.environ`` when ``None``.
        lookup: Account lookup, replaceable in tests.

    Returns:
        Identity: The service account on an installed node, otherwise
        this process's effective and supplementary groups.

    Raises:
        None
    """
    env = os.environ if environ is None else environ
    if not env.get("VAKR_ROOT"):
        try:
            gid = lookup(SERVICE_USER).pw_gid
        except KeyError:
            pass
        else:
            return Identity(f"the {SERVICE_USER} service user", (gid,), service=True)
    groups = (os.getegid(), *(g for g in os.getgroups() if g != os.getegid()))
    return Identity("this process", groups, service=False)


def fallback_dropin(root: Path) -> Path | None:
    """Find a collector unit drop-in that grants ``CAP_NET_RAW``.

    Args:
        root: Filesystem root.

    Returns:
        Path | None: The drop-in, or ``None`` if none is installed.

    Raises:
        None
    """
    base = root / FALLBACK_DIR
    for path in sorted(base.glob("*.conf")) if base.is_dir() else ():
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        if any("CAP_NET_RAW" in ln and not ln.lstrip().startswith("#") for ln in lines):
            return path
    return None


def socket_problem() -> str | None:
    """Try to open an unprivileged IPv4 ICMP socket in this process.

    Args:
        None

    Returns:
        str | None: The error, or ``None`` when the socket opened.

    Raises:
        None
    """
    try:
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP).close()
    except OSError as exc:
        return exc.strerror or type(exc).__name__
    return None


def evaluate(
    current: sysctl.GroupRange, who: Identity, root: Path, probe: Callable[[], str | None]
) -> CheckResult:
    """Judge the range against the collector's groups.

    Args:
        current: The running ``ping_group_range``.
        who: Whose groups to check.
        root: Filesystem root, for the fix and the fallback drop-in.
        probe: Socket test, run only for the development identity.

    Returns:
        CheckResult: OK when allowed (or the raw-socket fallback is
        installed); WARNING with the exact fix otherwise.

    Raises:
        None
    """
    shown = f"{sysctl.KEY} {current}"
    allowed = [g for g in who.gids if g in current]
    if allowed:
        problem = None if who.service else probe()
        if problem is None:
            summary = f"unprivileged ICMP allowed for {who.label} (GID {allowed[0]} in {shown})"
            return CheckResult("icmp", Status.OK, summary)
        summary = f"{shown} admits GID {allowed[0]}, but an ICMP socket failed: {problem}"
        return CheckResult("icmp", Status.WARNING, summary)
    excluded = f"{who.label} (GID {who.gids[0]}) is outside {shown}"
    if (dropin := fallback_dropin(root)) is not None:
        summary = f"{excluded}; using the CAP_NET_RAW fallback from {dropin}"
        return CheckResult("icmp", Status.OK, summary)
    fix = sysctl.plan_fix(current, who.gids[0], root)
    summary = f"unprivileged ICMP refused: {excluded}; fix: {fix.text()}"
    return CheckResult("icmp", Status.WARNING, summary)


def check_icmp(root: Path = Path("/")) -> CheckResult:
    """Health check: can the collector open unprivileged ICMP sockets (ICMP-012)?

    Args:
        root: Filesystem root; ``/`` except in tests (``/proc`` is not
            relocated by ``VAKR_ROOT``).

    Returns:
        CheckResult: OK, WARNING with the fix, or UNKNOWN when the sysctl
        cannot be read.

    Raises:
        None
    """
    try:
        current = sysctl.read_range(root)
    except (OSError, ValueError) as exc:
        summary = f"cannot read {sysctl.KEY}: {type(exc).__name__}"
        return CheckResult("icmp", Status.UNKNOWN, summary)
    return evaluate(current, collector_identity(), root, socket_problem)
