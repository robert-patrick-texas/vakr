"""Local socket roles by peer credentials, and the role authorization matrix (AUTH-001, E.12).

Dependencies:
    pytest; socket; vakr.auth.peercred, principal.

Example:
    $ uv run pytest tests/unit/auth/test_peercred_principal.py
"""

import os
import socket

import pytest

from vakr.auth.peercred import (
    ADMIN_GROUP,
    OPERATOR_GROUP,
    LocalGroups,
    local_groups,
    read_peer_credentials,
    role_for_peer,
)
from vakr.auth.principal import Permission, allows
from vakr.domain.users import Role

GROUPS = LocalGroups(admin_gid=990, operator_gid=991)


@pytest.mark.parametrize(
    ("uid", "gids", "groups", "role"),
    [
        (0, (), GROUPS, Role.ADMIN),
        (0, (), LocalGroups(None, None), Role.ADMIN),
        (1000, (100, 990), GROUPS, Role.ADMIN),
        (1000, (991, 990), GROUPS, Role.ADMIN),
        (1000, (991,), GROUPS, Role.OPERATOR),
        (1000, (100,), GROUPS, None),
        (1000, (), GROUPS, None),
        (1000, (990, 991), LocalGroups(None, None), None),
        (1000, (991,), LocalGroups(admin_gid=None, operator_gid=991), Role.OPERATOR),
    ],
)
def test_e12_peer_role_mapping(
    uid: int, gids: tuple[int, ...], groups: LocalGroups, role: Role | None
) -> None:
    """E.12: root and vakr-admin are admin, vakr is operator, everyone else is refused.

    Args:
        uid: Peer UID.
        gids: Peer group IDs.
        groups: The vakr groups on this host.
        role: Expected role, or ``None`` for refusal.

    Returns:
        None

    Raises:
        None
    """
    assert role_for_peer(uid, gids, groups) is role


def test_local_groups_uses_the_lookup_and_tolerates_missing_groups() -> None:
    """E.12: group names resolve through the lookup; a missing group is ``None``.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    table = {ADMIN_GROUP: 990}
    assert local_groups(table.get) == LocalGroups(990, None)
    assert OPERATOR_GROUP == "vakr"
    local_groups()  # the real group database answers without raising


def test_read_peer_credentials_reports_this_process() -> None:
    """E.12: SO_PEERCRED on a Unix socket pair names this process, its UID, and groups.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    server, client = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    with server, client:
        peer = read_peer_credentials(server)
    assert (peer.pid, peer.uid, peer.gid) == (os.getpid(), os.geteuid(), os.getegid())
    assert set(os.getgroups()) <= set(peer.groups) | {peer.gid}


def test_read_peer_credentials_refuses_a_non_unix_socket() -> None:
    """E.12: only the kernel's answer counts; a TCP or unconnected socket has none.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock,
        pytest.raises(OSError, match="Unix socket"),
    ):
        read_peer_credentials(sock)
    with (
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as lonely,
        pytest.raises(OSError, match="no connected peer"),
    ):
        read_peer_credentials(lonely)  # not connected: the kernel reports no peer


@pytest.mark.parametrize(
    ("role", "view", "administer"),
    [(Role.ADMIN, True, True), (Role.OPERATOR, True, False), (None, False, False)],
)
def test_auth001_authorization_matrix(role: Role | None, view: bool, administer: bool) -> None:
    """AUTH-001, SEC-006: operators view, admins view and administer, anonymous gets nothing.

    Args:
        role: Caller role, or ``None`` for anonymous.
        view: Expected VIEW decision.
        administer: Expected ADMINISTER decision.

    Returns:
        None

    Raises:
        None
    """
    assert allows(role, Permission.VIEW) is view
    assert allows(role, Permission.ADMINISTER) is administer
