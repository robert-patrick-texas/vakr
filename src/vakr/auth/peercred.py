"""Local Unix-socket authorization by peer credentials (Appendix E.12, 13.1).

``vakr tui`` and the CLI will talk to the API over ``/run/vakr/api.sock``
without a password: the kernel tells the server who connected
(``SO_PEERCRED``), and the role follows from that account's groups:

- root, or a member of ``vakr-admin``: admin;
- a member of ``vakr``: operator;
- anyone else: refused.

Groups are the connecting *process's* groups (``SO_PEERGROUPS``), the same
credentials the kernel uses for file permissions, so after ``usermod -aG
vakr-admin alice`` Alice must log in again, exactly as for file access.
Where ``SO_PEERGROUPS`` is unavailable or the peer is in more groups than
fit the reply, the group database is used instead.

Dependencies:
    grp, os, pwd, socket, struct: Linux credentials and group lookups.
    vakr.domain.users: Roles.

Safety:
    ``role_for_peer`` is a pure function of numbers; a missing group grants
    nothing. Only the kernel-reported UID and GIDs are trusted, never
    anything the client sends.

Example:
    >>> groups = LocalGroups(admin_gid=990, operator_gid=991)
    >>> role_for_peer(0, (), groups), role_for_peer(1000, (991,), groups)
    (<Role.ADMIN: 'admin'>, <Role.OPERATOR: 'operator'>)
    >>> role_for_peer(1000, (100,), groups) is None
    True
"""

import grp
import os
import pwd
import socket
import struct
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from vakr.domain.users import Role

ADMIN_GROUP = "vakr-admin"
OPERATOR_GROUP = "vakr"
_UCRED = struct.Struct("=iII")  # struct ucred: pid_t, uid_t, gid_t
_NO_ID = 0xFFFFFFFF  # (uid_t)-1: the kernel's "no credentials" answer
_SO_PEERGROUPS = getattr(socket, "SO_PEERGROUPS", 59)  # Linux >= 4.13; 59 on x86-64
_GROUPS_BUFLEN = 1024  # CPython's getsockopt limit: room for 256 groups


@dataclass(frozen=True, slots=True)
class LocalGroups:
    """The GIDs of the two vakr groups on this host.

    Attributes:
        admin_gid: GID of ``vakr-admin``, or ``None`` when it does not exist.
        operator_gid: GID of ``vakr``, or ``None`` when it does not exist.
    """

    admin_gid: int | None
    operator_gid: int | None


@dataclass(frozen=True, slots=True)
class PeerCredentials:
    """Who is on the other end of a Unix socket, as the kernel reports it.

    Attributes:
        pid: Peer process ID.
        uid: Peer user ID.
        gid: Peer primary group ID.
        groups: Peer supplementary group IDs.
    """

    pid: int
    uid: int
    gid: int
    groups: tuple[int, ...]


def _group_gid(name: str) -> int | None:
    """Look up a group's GID.

    Args:
        name: Group name.

    Returns:
        int | None: The GID, or ``None`` when the group does not exist.

    Raises:
        None
    """
    try:
        return grp.getgrnam(name).gr_gid
    except KeyError:
        return None


def local_groups(lookup: Callable[[str], int | None] = _group_gid) -> LocalGroups:
    """Resolve the vakr groups on this host.

    Args:
        lookup: Group-name-to-GID function; tests pass a table.

    Returns:
        LocalGroups: The two GIDs.

    Raises:
        None
    """
    return LocalGroups(lookup(ADMIN_GROUP), lookup(OPERATOR_GROUP))


def role_for_peer(uid: int, gids: Iterable[int], groups: LocalGroups) -> Role | None:
    """Map a peer's UID and groups to a role, per Appendix E.12.

    Args:
        uid: Peer user ID.
        gids: Peer primary and supplementary group IDs.
        groups: The vakr groups on this host.

    Returns:
        Role | None: ``ADMIN`` for root or ``vakr-admin`` members, ``OPERATOR``
        for ``vakr`` members, ``None`` (refuse) for everyone else.

    Raises:
        None
    """
    if uid == 0:
        return Role.ADMIN
    member = set(gids)
    if groups.admin_gid is not None and groups.admin_gid in member:
        return Role.ADMIN
    if groups.operator_gid is not None and groups.operator_gid in member:
        return Role.OPERATOR
    return None


def read_peer_credentials(sock: socket.socket) -> PeerCredentials:
    """Read the connecting process's credentials from a connected Unix socket (Linux).

    Args:
        sock: The server side of an ``AF_UNIX`` connection.

    Returns:
        PeerCredentials: PID, UID, primary GID, and supplementary GIDs.

    Raises:
        OSError: If the socket is not a connected Unix socket.
    """
    if sock.family != socket.AF_UNIX:
        raise OSError("peer credentials need a Unix socket")
    raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, _UCRED.size)
    pid, uid, gid = _UCRED.unpack(raw)
    if _NO_ID in (uid, gid):
        raise OSError("the socket has no connected peer")
    return PeerCredentials(pid, uid, gid, _peer_groups(sock, uid, gid))


def _peer_groups(sock: socket.socket, uid: int, gid: int) -> tuple[int, ...]:
    """Read the peer's supplementary groups, falling back to the group database.

    Args:
        sock: The connected socket.
        uid: Peer UID, for the fallback.
        gid: Peer primary GID, for the fallback.

    Returns:
        tuple[int, ...]: Group IDs.

    Raises:
        None
    """
    try:
        raw = sock.getsockopt(socket.SOL_SOCKET, _SO_PEERGROUPS, _GROUPS_BUFLEN)
    except OSError:
        raw = None  # an old kernel, or more groups than the reply holds (ERANGE)
    if raw is not None:
        count = len(raw) // 4
        return struct.unpack(f"{count}I", raw[: count * 4])
    try:
        return tuple(os.getgrouplist(pwd.getpwuid(uid).pw_name, gid))
    except KeyError:
        return (gid,)  # a UID without a passwd entry has no supplementary groups to find
