"""The API process from start to exit: logging, startup checks, listeners, serve (SVC-005, 16.3).

``run_api`` is what ``vakr api serve`` (and so ``vakr-api.service``)
executes. It installs journald-friendly logging, runs the startup checks
(``vakr.app.api.build``), opens both listeners, and serves until SIGTERM.
A failed check exits ``EX_PREFLIGHT`` (78) with an operator sentence naming
it; the unit does not restart on 78 (``RestartPreventExitStatus``), because
a bad configuration, a schema mismatch, or a port in use needs an operator.
The web UI build (``/opt/vakr/share/web``, or ``VAKR_WEB_DIR``) is read
once before serving; a missing build is logged once and the API serves
without pages (ADR-035, E.31).

Dependencies:
    vakr.api.app, vakr.api.peer: The application and the socket protocol.
    vakr.api.web: The web UI's pages.
    vakr.app.api: Build, listeners, server.
    vakr.service: Logging, signals, sd_notify.

Safety:
    No secret is ever logged: the API loads no SNMP credentials, and tokens
    and passwords are never formatted into a message.

Example:
    >>> EX_PREFLIGHT
    78
"""

import asyncio
import logging

from vakr.api.app import WebMount, create_app
from vakr.api.context import account_name
from vakr.api.peer import peer_protocol
from vakr.api.settings import SOCKET_CONCURRENCY, TCP_CONCURRENCY
from vakr.api.web import mount_web
from vakr.api.web_files import web_directory
from vakr.app.api.build import ApiParts, build_parts
from vakr.app.api.listeners import Listeners, open_listeners
from vakr.app.api.server import ApiService, QuietServer, uvicorn_config
from vakr.app.collector.preflight import PreflightError
from vakr.app.collector.timings import EX_PREFLIGHT
from vakr.auth import Passwords
from vakr.paths import InstallPaths
from vakr.ports.clock import Clock
from vakr.service.logs import configure_service_logging
from vakr.service.sdnotify import notify
from vakr.service.signals import service_signals

_LOG = logging.getLogger(__name__)
__all__ = ["EX_PREFLIGHT", "run_api"]


def _servers(
    parts: ApiParts, listeners: Listeners, web: WebMount | None
) -> list[tuple[QuietServer, object]]:
    """Make one uvicorn server per listener, sharing one application.

    Args:
        parts: Services.
        listeners: The open sockets.
        web: The web UI hook, or ``None`` to serve the API only.

    Returns:
        list: ``(server, socket)`` pairs, TCP first.

    Raises:
        None
    """
    app = create_app(parts.context, web=web)
    servers: list[tuple[QuietServer, object]] = []
    if listeners.tcp is not None:
        config = uvicorn_config(app, concurrency=TCP_CONCURRENCY)
        servers.append((QuietServer(config), listeners.tcp))
    if listeners.unix is not None:
        protocol = peer_protocol(parts.groups, account_name)
        config = uvicorn_config(app, concurrency=SOCKET_CONCURRENCY, http=protocol)
        servers.append((QuietServer(config), listeners.unix))
    return servers


async def _start(
    paths: InstallPaths, clock: Clock | None, passwords: Passwords | None
) -> tuple[ApiParts, Listeners]:
    """Run the startup checks and open the listeners.

    Args:
        paths: Install layout.
        clock: Time source override (tests).
        passwords: Hashing override (tests).

    Returns:
        tuple[ApiParts, Listeners]: Ready to serve.

    Raises:
        PreflightError: If a check fails; nothing stays open.
    """
    parts = await asyncio.to_thread(build_parts, paths, clock=clock, passwords=passwords)
    try:
        listeners = await asyncio.to_thread(open_listeners, parts.context.settings)
    except PreflightError:
        await asyncio.to_thread(parts.close)
        raise
    return parts, listeners


async def run_api(
    paths: InstallPaths,
    *,
    level: int = logging.INFO,
    clock: Clock | None = None,
    passwords: Passwords | None = None,
) -> int:
    """Run the API until it is stopped.

    Args:
        paths: Install layout.
        level: Lowest log level written.
        clock: Time source override, for tests.
        passwords: Hashing override, for tests; never in production.

    Returns:
        int: 0 after a clean stop, 1 after a server failure, 78 when a
        startup check refused to start.

    Raises:
        None
    """
    handler = configure_service_logging(tuple, level=level)
    uvicorn_log = logging.getLogger("uvicorn")
    uvicorn_log.addHandler(handler)
    uvicorn_log.setLevel(logging.WARNING)
    try:
        try:
            parts, listeners = await _start(paths, clock, passwords)
        except PreflightError as exc:
            _LOG.error("startup check %s failed: %s", exc.check, exc.message,
                       extra={"event_id": "api.preflight_failed"})  # fmt: skip
            notify(f"STATUS=startup check {exc.check} failed")
            return EX_PREFLIGHT
        # The build is read once, off the event loop; without one the API still serves.
        web = await asyncio.to_thread(mount_web, web_directory(paths))
        service = ApiService(parts, listeners, _servers(parts, listeners, web))
        with service_signals(service.request_stop, service.request_reload):
            return await service.run()
    finally:
        uvicorn_log.removeHandler(handler)
        logging.getLogger("vakr").removeHandler(handler)
