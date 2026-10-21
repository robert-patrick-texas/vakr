"""How the TUI's screens read: one read at a time, off the event loop, and what a failure means.

Each screen owns a ``Reader``. A refresh starts a read in a worker thread
(``asyncio.to_thread``), so neither a slow disk nor a slow ``vakr-api``
freezes the screen. A thread cannot be cancelled, so when a newer refresh
replaces one that has not started yet (an operator typing a search fires
one refresh per key), the old one gives up before it reads. A screen
therefore never has more than one request in flight (Appendix E.29).

A failed read keeps the rows already shown, marked stale (13.4). What the
operator is told depends on the failure: storage trouble points at
``vakr doctor``; an API that cannot be used already says what to check.

Dependencies:
    asyncio, threading, time: The worker and its lock.
    vakr.query: ``SourceUnavailableError``, the views, and the labels.

Safety:
    Read-only. The lock is held only around one read.

Example:
    >>> failure_text(SourceUnavailableError("vakr-api is not running"))
    'vakr-api is not running'
"""

import asyncio
import threading
import time
from collections.abc import Callable
from datetime import tzinfo

from rich.markup import escape

from vakr.domain.errors import StorageError
from vakr.query import DeviceListView, SourceUnavailableError
from vakr.query.labels import health_line
from vakr.query.text import local_time

READ_ERRORS = (StorageError, SourceUnavailableError)
"""Failures a refresh survives by keeping the old rows, marked stale (13.4)."""


class SupersededError(Exception):
    """A newer read replaced this one before it started."""


def failure_text(exc: Exception) -> str:
    """Say why a read failed, and what to run.

    Args:
        exc: One of ``READ_ERRORS``.

    Returns:
        str: Plain text (not markup).

    Raises:
        None
    """
    if isinstance(exc, StorageError):
        return f"inventory unreadable: {exc}; run `vakr doctor`"
    return str(exc)


class Reader:
    """Runs a screen's reads one at a time in a worker thread.

    Attributes:
        elapsed_s: Seconds the last successful read took, or ``None`` before one.
    """

    def __init__(self) -> None:
        """Start with no read made.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.elapsed_s: float | None = None
        self._lock = threading.Lock()
        self._latest = 0

    async def read[**P, T](self, function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        """Call ``function`` in a worker thread unless a newer read has been asked for.

        Args:
            function: The blocking read, such as ``StatusSource.device_list``.
            *args: Its positional arguments.
            **kwargs: Its keyword arguments.

        Returns:
            T: What it returned.

        Raises:
            SupersededError: If a newer read was asked for before this one started.
            Exception: Whatever ``function`` raises.
        """
        self._latest += 1
        ticket = self._latest

        def run() -> T:
            """Read under the lock, or give way to a newer read.

            Args:
                None

            Returns:
                T: The result.

            Raises:
                SupersededError: If this read is no longer the latest.
            """
            with self._lock:
                if ticket != self._latest:
                    raise SupersededError
                started = time.monotonic()
                result = function(*args, **kwargs)
                self.elapsed_s = time.monotonic() - started
                return result

        return await asyncio.to_thread(run)


def header_text(
    title: str, view: DeviceListView, zone: tzinfo, refresh_s: float, elapsed_s: float | None
) -> str:
    """Build the list header: release and data path, time, health, read time, refresh (13.2).

    Args:
        title: Product, release, and data path, such as ``"vakr <release> · data: vakr-api"``.
        view: The list shown.
        zone: Display timezone.
        refresh_s: Seconds between refreshes.
        elapsed_s: Seconds the read took, if known.

    Returns:
        str: Console markup, values escaped.

    Raises:
        None
    """
    when = local_time(view.generated_at, zone)
    took = f" · read in {elapsed_s * 1000:.0f} ms" if elapsed_s is not None else ""
    return (f"{escape(title)} · {when} · {escape(health_line(view.health))}{took} · "
            f"refresh {refresh_s:g} s")  # fmt: skip
