"""Run the API: two uvicorn servers, readiness, session purge, and a clean stop (16.4, SVC-001).

One process serves the same application on both listeners, each with its
own uvicorn server: the TCP one with the stock h11 protocol, the socket one
with ``PeerProtocol`` (E.12). uvicorn's own signal handling and proxy
header rewriting are off; vakr's signal handlers ask both servers to stop,
and trusted-proxy handling is vakr's (SEC-013). Once both listen, systemd
hears ``READY=1``. Every ``PURGE_INTERVAL_S`` (5 minutes) expired sessions,
kiosk tokens, and quiet failure runs are deleted in at most
``PURGE_MAX_BATCHES`` bounded batches (E.12).

On SIGTERM the service says ``STOPPING=1``, stops accepting, lets requests
in progress finish for up to ``GRACEFUL_SHUTDOWN_S`` (10 s), then closes the
stores and removes the socket file. SIGHUP is logged and ignored: every
``[api]`` and ``[auth]`` option applies at restart.

Dependencies:
    uvicorn: The HTTP servers.
    vakr.service: sd_notify and signals.

Safety:
    Blocking work (purge, store close) runs in worker threads.

Example:
    >>> uvicorn_config(object(), concurrency=1).proxy_headers
    False
"""

import asyncio
import contextlib
import logging
import socket
from collections.abc import Generator
from typing import Any

import uvicorn

from vakr.api.settings import (
    BACKLOG,
    GRACEFUL_SHUTDOWN_S,
    KEEP_ALIVE_S,
    PURGE_INTERVAL_S,
    PURGE_MAX_BATCHES,
)
from vakr.app.api.build import ApiParts
from vakr.app.api.listeners import Listeners
from vakr.domain.errors import StorageError
from vakr.scheduler.lag import LoopLagMonitor
from vakr.service.sdnotify import notify

_LOG = logging.getLogger(__name__)
STARTUP_WAIT_S = 10.0
MAX_HEADER_BYTES = 16384
"""Largest request head h11 buffers (E.30); a longer one is answered 400."""


class QuietServer(uvicorn.Server):
    """A uvicorn server that leaves signals to vakr."""

    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None]:
        """Install nothing: ``vakr.service.signals`` routes SIGTERM and SIGHUP.

        Args:
            None

        Returns:
            Generator[None]: An empty context.

        Raises:
            None
        """
        yield


def uvicorn_config(app: Any, *, concurrency: int, http: Any = "h11") -> uvicorn.Config:
    """Return the uvicorn settings of one listener (E.30).

    Args:
        app: The ASGI application.
        concurrency: Connections plus requests in progress; more get 503.
        http: The protocol: ``h11``, or ``PeerProtocol`` for the socket.

    Returns:
        uvicorn.Config: Settings with proxy headers, server header, access
        log, lifespan, and websockets off.

    Raises:
        None
    """
    return uvicorn.Config(
        app, http=http, ws="none", lifespan="off", proxy_headers=False, server_header=False,
        access_log=False, log_config=None, limit_concurrency=concurrency, backlog=BACKLOG,
        timeout_keep_alive=KEEP_ALIVE_S, timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S,
        h11_max_incomplete_event_size=MAX_HEADER_BYTES,
    )  # fmt: skip


class ApiService:
    """Runs the servers until asked to stop."""

    def __init__(
        self, parts: ApiParts, listeners: Listeners, servers: list[tuple[QuietServer, Any]]
    ) -> None:
        """Bind the service to its parts.

        Args:
            parts: Services and stores.
            listeners: The open sockets.
            servers: Each server with the socket it serves.

        Returns:
            None

        Raises:
            None
        """
        self.parts, self.listeners, self.servers = parts, listeners, servers
        self._stop = asyncio.Event()

    def request_stop(self) -> None:
        """Ask the service to stop (SIGTERM, SIGINT).

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._stop.set()

    def request_reload(self) -> None:
        """Explain that reload does not apply (SIGHUP).

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        _LOG.info("vakr-api options apply at restart; ignoring SIGHUP",
                  extra={"event_id": "api.reload_ignored"})  # fmt: skip

    async def _started(self, tasks: list[asyncio.Task[None]]) -> bool:
        """Wait until every server listens, or one fails.

        Args:
            tasks: The servers' tasks.

        Returns:
            bool: True when all started.

        Raises:
            None
        """
        deadline = asyncio.get_running_loop().time() + STARTUP_WAIT_S
        while asyncio.get_running_loop().time() < deadline:
            if all(server.started for server, _ in self.servers):
                return True
            if any(t.done() for t in tasks):
                return False
            await asyncio.sleep(0.05)
        return False

    async def _purge(self) -> None:
        """Delete expired authentication rows every few minutes, bounded (E.12).

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        auth = self.parts.context.auth
        while True:
            await asyncio.sleep(PURGE_INTERVAL_S)
            for _ in range(PURGE_MAX_BATCHES):
                try:
                    counts = await asyncio.to_thread(auth.purge_expired)
                except StorageError as exc:
                    _LOG.warning("could not purge expired sessions: %s", exc,
                                 extra={"event_id": "api.purge_failed"})  # fmt: skip
                    break
                full = auth.settings.purge_batch
                if max(counts.sessions, counts.kiosk_tokens, counts.login_failures) < full:
                    break

    def _listeners(self) -> list[tuple[str, int, int]]:
        """Report each listener's open connections and limit (E.33).

        Args:
            None

        Returns:
            list[tuple[str, int, int]]: ``tcp`` or ``socket``, connections, limit.

        Raises:
            None
        """
        return [("socket" if sock.family == socket.AF_UNIX else "tcp",
                 len(server.server_state.connections), int(server.config.limit_concurrency or 0))
                for server, sock in self.servers]  # fmt: skip

    async def _watch_metrics(self) -> None:
        """Measure the API's event-loop lag and report its listeners (OBS-007, E.33).

        Args:
            None

        Returns:
            None

        Raises:
            asyncio.CancelledError: When the service stops.
        """
        metrics = self.parts.context.metrics
        metrics.lag = LoopLagMonitor(self.parts.context.clock)
        metrics.listeners = self._listeners
        await metrics.lag.run()

    async def run(self) -> int:
        """Serve until stopped.

        Args:
            None

        Returns:
            int: 0 after a clean stop, 1 when a server failed.

        Raises:
            None
        """
        tasks = [asyncio.create_task(s.serve(sockets=[sock])) for s, sock in self.servers]
        purge = asyncio.create_task(self._purge())
        lag = asyncio.create_task(self._watch_metrics())
        failed = not await self._started(tasks)
        if not failed:
            notify("READY=1\nSTATUS=serving API v1")
            _LOG.info("vakr-api ready", extra={"event_id": "api.ready"})
            stop = asyncio.create_task(self._stop.wait())
            done, _ = await asyncio.wait([stop, *tasks], return_when=asyncio.FIRST_COMPLETED)
            failed = stop not in done
            stop.cancel()
        notify("STOPPING=1\nSTATUS=stopping")
        for server, _ in self.servers:
            server.should_exit = True
        await asyncio.gather(*tasks, return_exceptions=True)
        purge.cancel()
        lag.cancel()
        await asyncio.gather(purge, lag, return_exceptions=True)
        await asyncio.to_thread(self.parts.close)
        self.listeners.close()
        _LOG.info("vakr-api stopped", extra={"event_id": "api.stopped"})
        return 1 if failed else 0
