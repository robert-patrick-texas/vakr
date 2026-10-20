"""Signals for a service on an asyncio loop: stop on SIGTERM/SIGINT, reload on SIGHUP.

systemd stops a unit with SIGTERM and ``systemctl reload`` sends SIGHUP
through ``ExecReload`` (CONF-003). The handlers only call the service's
``request_stop`` or ``request_reload``, which set flags the service loop
acts on, so a signal never interrupts a write halfway (16.4).

Dependencies:
    asyncio, signal: ``loop.add_signal_handler``.

Safety:
    Handlers are removed on exit, so a test or a second service on the same
    loop starts clean.

Example:
    >>> import signal
    >>> STOP_SIGNALS == (signal.SIGTERM, signal.SIGINT)
    True
"""

import asyncio
import signal
from collections.abc import Callable, Generator
from contextlib import contextmanager

STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)
RELOAD_SIGNALS = (signal.SIGHUP,)


@contextmanager
def service_signals(stop: Callable[[], None], reload: Callable[[], None]) -> Generator[None]:
    """Route stop and reload signals to callbacks while the block runs.

    Args:
        stop: Called on SIGTERM or SIGINT.
        reload: Called on SIGHUP.

    Returns:
        Generator[None]: A context manager; the handlers are removed on exit.

    Raises:
        RuntimeError: If there is no running event loop.
    """
    loop = asyncio.get_running_loop()
    installed = []
    for signum in STOP_SIGNALS:
        loop.add_signal_handler(signum, stop)
        installed.append(signum)
    for signum in RELOAD_SIGNALS:
        loop.add_signal_handler(signum, reload)
        installed.append(signum)
    try:
        yield
    finally:
        for signum in installed:
            loop.remove_signal_handler(signum)
