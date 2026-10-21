"""A vakr-api over memory stores, with accounts, kiosk tokens, and socket peers, for API tests.

``ApiKit`` builds the real application (``create_app``) on a ``Fleet``
(memory metadata and telemetry, a manual clock) with authentication
services sharing the same metadata store. Argon2 runs with tiny test-only
parameters; passwords are invented per run, so no fixture holds a
credential (CRED-002). Failed-login delays are recorded instead of slept.

``serving`` runs the app under a real uvicorn server on a socket.
``client()`` speaks HTTP to the app as a loopback, proxied, or remote
client; ``socket_client(role)`` simulates the Unix-socket protocol by
attaching a ``SocketPeer`` to each request's scope, exactly where
``PeerProtocol`` puts it.

Dependencies:
    fastapi.testclient, httpx2; tests.unit.query.query_testkit; tests.unit.auth.auth_testkit.

Example:
    >>> kit = ApiKit.create()
    >>> kit.client().get("/api/v1/health/live").json()["status"]
    'ok'
"""

import asyncio
import ipaddress
import secrets
import socket
import threading
import time
from collections.abc import Generator, MutableMapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.unit.auth.auth_testkit import cheap_hasher, new_password
from tests.unit.query.history_testkit import UTC_SETTINGS
from tests.unit.query.query_testkit import Fleet
from vakr.api.app import create_app
from vakr.api.context import ApiContext
from vakr.api.keys import PEER_KEY
from vakr.api.peer import SocketPeer
from vakr.api.settings import ApiSettings
from vakr.app.api.server import QuietServer, uvicorn_config
from vakr.auth import (
    AuthKeys,
    AuthServices,
    AuthSettings,
    Passwords,
    Principal,
    PrincipalKind,
    build_auth_services,
)
from vakr.credentials.secret import Secret
from vakr.domain.users import Role
from vakr.query import HistoryQueries, OverviewQueries

LOOPBACK = "127.0.0.1"
_APPS: dict[tuple[tuple[str, ...], int], FastAPI] = {}
"""Apps by middleware settings: FastAPI compiles routes on an app's first request (~0.4 s),
so kits share apps and only swap the services (``app.state.vakr``): use one kit per
settings at a time."""
REMOTE = "198.51.100.20"
PROXY = "127.0.0.1"


@dataclass
class ApiKit:
    """The application over one fleet, with its accounts.

    Attributes:
        fleet: Memory stores and the manual clock.
        auth: Authentication services on the fleet's metadata store.
        context: The services the app uses.
        app: The FastAPI application.
        sleeps: Delays the login handler asked for, instead of sleeping.
        passwords: Username to its invented password.
    """

    fleet: Fleet
    auth: AuthServices
    context: ApiContext
    app: FastAPI
    sleeps: list[float] = field(default_factory=list)
    passwords: dict[str, str] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        positions: tuple[str, ...] = ("east",),
        proxies: tuple[str, ...] = (),
        auth_settings: AuthSettings | None = None,
        max_body: int = 65536,
        telemetry: bool = True,
        fleet: Fleet | None = None,
    ) -> ApiKit:
        """Build the kit.

        Args:
            positions: Collector positions of a new, empty fleet.
            proxies: ``api.trusted_proxies`` entries.
            auth_settings: Authentication settings; E.12 defaults when ``None``.
            max_body: ``api.max_body_bytes``.
            telemetry: False for a node without a telemetry database.
            fleet: A fleet built already (``mixed_fleet()``), instead of an empty one.

        Returns:
            ApiKit: The kit.

        Raises:
            None
        """
        fleet = fleet or Fleet(positions)
        keys = AuthKeys.from_master(secrets.token_bytes(32))
        hashing = Passwords(cheap_hasher())
        auth = build_auth_services(fleet.uow, clock=fleet.clock, keys=keys,
                                   settings=auth_settings, passwords=hashing)  # fmt: skip
        tele = fleet.telemetry if telemetry else None
        overview = OverviewQueries(fleet.uow, tele, UTC_SETTINGS, fleet.clock)
        history = HistoryQueries(fleet.uow, tele, UTC_SETTINGS, fleet.clock)
        settings = ApiSettings(trusted_proxies=tuple(ipaddress.ip_network(p) for p in proxies),
                               max_body_bytes=max_body)  # fmt: skip
        sleeps: list[float] = []

        async def record(seconds: float) -> None:
            """Record a login delay instead of waiting.

            Args:
                seconds: The delay.

            Returns:
                None

            Raises:
                None
            """
            sleeps.append(seconds)

        state = "ok" if telemetry else "no telemetry database yet"
        context = ApiContext(auth, overview, history, fleet.uow, fleet.clock, settings,
                             fleet.clock.now_utc(), state, record)  # fmt: skip
        app = _APPS.get((proxies, max_body))
        if app is None:
            app = _APPS[(proxies, max_body)] = create_app(context)
        app.state.vakr = context
        return cls(fleet, auth, context, app, sleeps)

    def add_user(self, username: str, role: Role) -> str:
        """Create an account with an invented password.

        Args:
            username: Username.
            role: Its role.

        Returns:
            str: The password.

        Raises:
            None
        """
        password = new_password()
        self.auth.users.create(username, role, Secret(password), actor="test")
        self.passwords[username] = password
        return password

    def kiosk_token(self, label: str = "noc-wall") -> str:
        """Create a kiosk token.

        Args:
            label: The display label.

        Returns:
            str: The token (``vkk_...``).

        Raises:
            None
        """
        return self.auth.kiosk.create(label, actor="test").token.reveal()

    def client(
        self, *, peer: str = LOOPBACK, scheme: str = "http", headers: dict[str, str] | None = None
    ) -> TestClient:
        """Return an HTTP client that connects from ``peer``.

        Args:
            peer: The connecting address the app sees.
            scheme: ``http``, or ``https`` for a connection that is itself TLS.
            headers: Default headers, such as forwarded ones.

        Returns:
            TestClient: The client (cookies persist between its requests).

        Raises:
            None
        """
        return TestClient(self.app, base_url=f"{scheme}://vakr.test", client=(peer, 50000),
                          headers=headers or {}, raise_server_exceptions=False)  # fmt: skip

    def login(self, client: TestClient, username: str, **headers: str) -> Any:
        """Sign in with the account's password.

        Args:
            client: The client.
            username: Account created with ``add_user``.
            **headers: Extra headers.

        Returns:
            Any: The response.

        Raises:
            None
        """
        body = {"username": username, "password": self.passwords[username]}
        return client.post("/api/v1/auth/login", json=body, headers=headers)

    def socket_client(self, role: Role | None) -> TestClient:
        """Return a client that behaves as a Unix-socket peer with ``role`` (``None`` refused).

        Args:
            role: The role ``role_for_peer`` would give, or ``None``.

        Returns:
            TestClient: The client.

        Raises:
            None
        """
        return peer_client(self.app, role)


def peer_client(app: Any, role: Role | None) -> TestClient:
    """Return a client of ``app`` that behaves as a Unix-socket peer with ``role``.

    Args:
        app: The ASGI application.
        role: The role ``role_for_peer`` would give, or ``None`` (refused).

    Returns:
        TestClient: The client.

    Raises:
        None
    """
    principal = None if role is None else Principal(PrincipalKind.LOCAL, "local:tester", role)
    peer = SocketPeer(sock=None, principal=principal, resolved=True, uid=4242)

    async def with_peer(scope: MutableMapping[str, Any], receive: Any, send: Any) -> None:
        """Attach the peer, as ``PeerProtocol`` does.

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
            scope[PEER_KEY] = peer
            scope["client"] = None
        await app(scope, receive, send)

    return TestClient(with_peer, base_url="http://localhost", raise_server_exceptions=False)


@contextmanager
def serving(app: Any, sock: socket.socket, http: Any = "h11") -> Generator[QuietServer]:
    """Run one uvicorn server on ``sock`` in a background thread.

    Args:
        app: The ASGI app.
        sock: A listening socket.
        http: Protocol class or name.

    Returns:
        Generator[QuietServer]: The running server, stopped on exit.

    Raises:
        None
    """
    server = QuietServer(uvicorn_config(app, concurrency=8, http=http))
    thread = threading.Thread(target=lambda: asyncio.run(server.serve(sockets=[sock])),
                              daemon=True)  # fmt: skip
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.02)
    try:
        yield server
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
