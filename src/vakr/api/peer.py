"""The local Unix socket's caller: kernel-reported credentials to a role (E.12, 13.1).

``vakr tui`` and the CLI on the monitoring node call the API over
``/run/vakr/api.sock`` without a password. The kernel reports the
connecting process's UID and groups (``SO_PEERCRED``, ``SO_PEERGROUPS``)
once per connection; ``vakr.auth.role_for_peer`` turns them into a role:
root and members of ``vakr-admin`` are admins, members of ``vakr-view`` and
``vakr`` are operators, and everyone else is refused, on every endpoint.

The socket's server uses ``PeerProtocol``, an h11 protocol that attaches a
``SocketPeer`` to each request's scope. The TCP listener uses the stock
protocol, so a network client can never present peer credentials: the
scope key is not something any header can set. No cookie, kiosk token, or
CSRF token is consulted on the socket; there is no browser on it.

Dependencies:
    uvicorn.protocols.http.h11_impl: The HTTP/1.1 protocol extended here.
    vakr.auth: Peer credentials and the role rule.

Safety:
    Only kernel-reported numbers are trusted. The credential read and the
    account-name lookup (NSS) run in a worker thread, once per connection.

Example:
    >>> SocketPeer(sock=None).principal is None
    True
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from uvicorn.protocols.http.h11_impl import H11Protocol

from vakr.api.keys import PEER_KEY
from vakr.auth import (
    LocalGroups,
    Principal,
    PrincipalKind,
    read_peer_credentials,
    role_for_peer,
)
from vakr.domain.users import Role

_LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class SocketPeer:
    """The process on the other end of one socket connection.

    Attributes:
        sock: The connection's socket (asyncio's transport socket).
        principal: The caller once resolved; ``None`` until then, or when refused.
        resolved: Whether the credentials have been read.
        uid: The peer's UID, once read (for the refusal message).
    """

    sock: Any
    principal: Principal | None = None
    resolved: bool = False
    uid: int | None = None


def peer_principal(
    sock: Any, groups: LocalGroups, name_of: Callable[[int], str]
) -> tuple[Principal | None, int | None]:
    """Read a connection's peer credentials and decide its role (blocking).

    Args:
        sock: The connected Unix socket.
        groups: GIDs of ``vakr-admin``, ``vakr``, and ``vakr-view`` on this host.
        name_of: UID to account name, for the audit actor ``local:<name>``.

    Returns:
        tuple[Principal | None, int | None]: The principal (``None`` when refused)
        and the peer UID (``None`` when unreadable).

    Raises:
        None
    """
    try:
        creds = read_peer_credentials(sock)
    except OSError:
        return None, None
    role: Role | None = role_for_peer(creds.uid, (creds.gid, *creds.groups), groups)
    if role is None:
        return None, creds.uid
    return Principal(PrincipalKind.LOCAL, f"local:{name_of(creds.uid)}", role), creds.uid


def peer_protocol(groups: LocalGroups, name_of: Callable[[int], str]) -> type[H11Protocol]:
    """Build the socket listener's protocol class for this host's vakr groups.

    Args:
        groups: GIDs of ``vakr-admin``, ``vakr``, and ``vakr-view``, resolved once at startup.
        name_of: UID to account name.

    Returns:
        type[H11Protocol]: The protocol class for uvicorn's ``http`` setting.

    Raises:
        None
    """

    class PeerProtocol(H11Protocol):
        """h11 with the connection's ``SocketPeer`` in every request scope."""

        def connection_made(self, transport: Any) -> None:  # uvicorn types it as asyncio.Transport
            """Start a connection and wrap the app to attach its peer.

            Args:
                transport: The connection's transport.

            Returns:
                None

            Raises:
                None
            """
            super().connection_made(transport)
            peer = SocketPeer(transport.get_extra_info("socket"))
            inner = self.app

            async def with_peer(scope: Any, receive: Any, send: Any) -> None:
                """Resolve the peer once, then hand the request on with it attached.

                Args:
                    scope: The ASGI scope.
                    receive: Receive callable.
                    send: Send callable.

                Returns:
                    None

                Raises:
                    None
                """
                if scope["type"] == "http":
                    if not peer.resolved:
                        found = await asyncio.to_thread(peer_principal, peer.sock, groups, name_of)
                        peer.principal, peer.uid = found
                        peer.resolved = True
                    scope[PEER_KEY] = peer
                await inner(scope, receive, send)

            self.app = with_peer

    return PeerProtocol
