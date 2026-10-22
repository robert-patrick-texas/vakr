"""The services one ``vakr-api`` process shares across requests (6.2, TUI-007).

``ApiContext`` holds every service a handler may call: authentication, the
query services, the metadata unit of work for the few direct reads and the
identity decisions, the clock, and the settings. ``vakr.api.build`` makes
one per process from the installed databases; tests make one over memory
stores. Handlers reach it through ``get_context``, so the application object
itself can be built without services, which is how the OpenAPI document is
generated.

The services are synchronous and cache internally (inventory index,
ranking scans), so each exists once and handlers call them with
``asyncio.to_thread`` (``call``), never on the event loop.

Dependencies:
    vakr.auth, vakr.query: The services.
    vakr.ports: Metadata unit of work and clock.

Safety:
    Holds no secret itself; the auth keys stay inside ``AuthServices``.

Example:
    >>> ApiContext.__name__
    'ApiContext'
"""

import asyncio
import pwd
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime

from fastapi import Request

from vakr.api.errors import ApiError
from vakr.api.settings import ApiSettings
from vakr.auth import AuthServices
from vakr.metrics.service import MetricsService
from vakr.operations import OperationService
from vakr.ports.clock import Clock
from vakr.ports.metadata import MetadataUnitOfWorkFactory
from vakr.query import HistoryQueries, OverviewQueries


def account_name(uid: int) -> str:
    """Name a local account for audit records (``local:<name>``).

    Args:
        uid: User ID reported by the kernel.

    Returns:
        str: The login name, or ``uid:<n>`` without a passwd entry.

    Raises:
        None
    """
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return f"uid:{uid}"


LOGINS_ACTIVE = 8
LOGINS_WAITING = 32
"""Password logins checked at once, and waiting beyond them; more are refused (E.30)."""
KIOSK_ACTIVE = 4
KIOSK_WAITING = 16
"""Kiosk token exchanges checked at once, and waiting beyond them (E.30 #17)."""


class LoginGate:
    """A bounded queue in front of password verification (SEC-012, global rate limit).

    Per-account throttling (E.12) slows one guesser down; this bounds the
    total, so a flood of logins for many accounts queues at most
    ``LOGINS_WAITING`` requests and refuses the rest at once.
    """

    def __init__(self, active: int = LOGINS_ACTIVE, waiting: int = LOGINS_WAITING) -> None:
        """Create the gate.

        Args:
            active: Logins verified at once.
            waiting: Logins allowed to wait beyond those.

        Returns:
            None

        Raises:
            None
        """
        self._slots = asyncio.Semaphore(active)
        self._inside = 0
        self._limit = active + waiting

    @asynccontextmanager
    async def slot(self) -> AsyncGenerator[None]:
        """Hold one verification slot for the block.

        Args:
            None

        Returns:
            AsyncGenerator[None]: The context manager.

        Raises:
            ApiError: 429 when the queue is full.
        """
        if self._inside >= self._limit:
            raise ApiError(429, "too_many_requests", "too many sign-ins at once; retry shortly",
                           headers={"Retry-After": "5"})  # fmt: skip
        self._inside += 1
        try:
            async with self._slots:
                yield
        finally:
            self._inside -= 1


@dataclass(slots=True)
class ApiContext:
    """Everything the handlers of one process use.

    Attributes:
        auth: Login, sessions, kiosk tokens, CSRF.
        overview: Overview, device lists, browsing, and (``.catalog``,
            ``.rankings``) interface lists, search, and rankings.
        history: Graphs, latency, and state timelines.
        metadata: Opens metadata units of work.
        clock: Time source.
        settings: ``[api]`` settings.
        started_at: When the process became ready (UTC).
        telemetry: ``ok``, or why the telemetry store is not available.
        sleep: Waits before answering a failed login (``asyncio.sleep``; tests record it).
        logins: Bounds password logins in progress (``LoginGate``).
        kiosk_exchanges: Bounds kiosk token exchanges in progress, apart from
            logins so neither can crowd out the other.
        operations: Admin operations (manual rediscovery, E.32); ``None``
            builds one with the default reachability strategy (tests).
        metrics: Self-metrics for ``GET /api/v1/metrics`` (E.33); tests get one
            without a collector snapshot or database readers.
    """

    auth: AuthServices
    overview: OverviewQueries
    history: HistoryQueries
    metadata: MetadataUnitOfWorkFactory
    clock: Clock
    settings: ApiSettings
    started_at: datetime
    telemetry: str = "ok"
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    logins: LoginGate = field(default_factory=LoginGate)
    kiosk_exchanges: LoginGate = field(
        default_factory=lambda: LoginGate(KIOSK_ACTIVE, KIOSK_WAITING)
    )
    operations: OperationService | None = None
    metrics: MetricsService = field(default_factory=MetricsService)


def get_context(request: Request) -> ApiContext:
    """Return the process's services (a FastAPI dependency).

    Args:
        request: The request.

    Returns:
        ApiContext: The services.

    Raises:
        ApiError: 503 when the app was built without services (schema generation only).
    """
    context = getattr(request.app.state, "vakr", None)
    if not isinstance(context, ApiContext):
        raise ApiError(503, "unavailable", "the API is not connected to its databases")
    return context


async def call[T](function: Callable[..., T], *args: object, **kwargs: object) -> T:
    """Run a blocking service call in a worker thread, off the event loop.

    Args:
        function: The synchronous service method.
        *args: Positional arguments.
        **kwargs: Keyword arguments.

    Returns:
        T: The method's result.

    Raises:
        Exception: Whatever the method raises.
    """
    return await asyncio.to_thread(function, *args, **kwargs)
