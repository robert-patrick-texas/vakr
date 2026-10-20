"""A virtual-time asyncio event loop driven by ``ManualClock`` (SCHED-T03..T07, TEST-004).

The scheduler waits with ordinary asyncio sleeps and timeouts, which read
``loop.time()``. This loop's ``time()`` is a ``ManualClock``'s monotonic
reading, and its selector never blocks: when the loop would wait for the
next timer, the selector advances the clock to it instead. Simulated hours
therefore run in milliseconds, deterministically, with the real scheduler
code and no patched sleeps.

Only timers exist here. The loop's self-pipe is registered but never
polled, so nothing may depend on file-descriptor readiness or threads; a
wait with no timer at all would hang forever in a real loop, and raises
``VirtualDeadlockError`` here instead.

Dependencies:
    asyncio, selectors: The loop and a selector that only advances time.
    vakr.ports.clock: ``ManualClock``.

Example:
    >>> import asyncio
    >>> clock = new_clock()
    >>> run_virtual(clock, asyncio.sleep(3600))
    >>> clock.monotonic()
    4600.0
"""

import asyncio
import math
import selectors
from collections.abc import Coroutine, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from vakr.ports.clock import ManualClock

START = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


class VirtualDeadlockError(RuntimeError):
    """The loop had nothing ready and no timer: a real loop would hang."""


def _fd(fileobj: Any) -> int:
    """Return the file descriptor of a selectable object.

    Args:
        fileobj: An integer descriptor or an object with ``fileno()``.

    Returns:
        int: The descriptor.

    Raises:
        None
    """
    return fileobj if isinstance(fileobj, int) else fileobj.fileno()


class _TimeOnlySelector(selectors.BaseSelector):
    """Accepts registrations, reports no I/O, and advances virtual time."""

    def __init__(self, clock: ManualClock) -> None:
        """Bind the selector to the clock it advances.

        Args:
            clock: The virtual clock.

        Returns:
            None

        Raises:
            None
        """
        self._clock = clock
        self._keys: dict[int, selectors.SelectorKey] = {}

    def register(self, fileobj: Any, events: int, data: Any = None) -> selectors.SelectorKey:
        """Remember a registration (the loop's self-pipe).

        Args:
            fileobj: The selectable object.
            events: Event mask.
            data: Attached data.

        Returns:
            selectors.SelectorKey: The key.

        Raises:
            None
        """
        key = selectors.SelectorKey(fileobj, _fd(fileobj), events, data)
        self._keys[key.fd] = key
        return key

    def unregister(self, fileobj: Any) -> selectors.SelectorKey:
        """Forget a registration.

        Args:
            fileobj: The selectable object.

        Returns:
            selectors.SelectorKey: The removed key.

        Raises:
            KeyError: If it was not registered.
        """
        return self._keys.pop(_fd(fileobj))

    def select(self, timeout: float | None = None) -> list[tuple[selectors.SelectorKey, int]]:
        """Advance the clock to the next timer instead of waiting.

        Args:
            timeout: Seconds until the loop's next timer, or ``None``.

        Returns:
            list[tuple[selectors.SelectorKey, int]]: Always empty.

        Raises:
            VirtualDeadlockError: When there is no timer to advance to.
        """
        if timeout is None:
            raise VirtualDeadlockError("nothing is ready and no timer is scheduled")
        if timeout > 0:
            # Round up to the clock's microsecond step so the timer is due on wake-up.
            self._clock.advance(timedelta(microseconds=math.ceil(timeout * 1e6)))
        return []

    def get_map(self) -> Mapping[Any, selectors.SelectorKey]:
        """Return the registrations.

        Args:
            None

        Returns:
            Mapping[Any, selectors.SelectorKey]: Keys by descriptor.

        Raises:
            None
        """
        return self._keys

    def close(self) -> None:
        """Drop all registrations.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._keys.clear()


class VirtualTimeLoop(asyncio.SelectorEventLoop):
    """An event loop whose time is a ``ManualClock``'s monotonic reading."""

    def __init__(self, clock: ManualClock) -> None:
        """Create the loop.

        Args:
            clock: The virtual clock; the loop advances it.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(_TimeOnlySelector(clock))
        self._virtual_clock = clock
        self._clock_resolution = 1e-6  # ManualClock moves in whole microseconds

    def time(self) -> float:
        """Return virtual monotonic time.

        Args:
            None

        Returns:
            float: ``clock.monotonic()``.

        Raises:
            None
        """
        return self._virtual_clock.monotonic()


def new_clock() -> ManualClock:
    """Return a manual clock at the shared test start time.

    Args:
        None

    Returns:
        ManualClock: Wall clock at ``START``, monotonic at 1000 s.

    Raises:
        None
    """
    return ManualClock(START, monotonic_start=1000.0)


def run_virtual[T](clock: ManualClock, coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine to completion in virtual time.

    Args:
        clock: The virtual clock.
        coro: The coroutine.

    Returns:
        T: The coroutine's result.

    Raises:
        VirtualDeadlockError: When the coroutine waits on something with no timer.
    """
    with asyncio.Runner(loop_factory=lambda: VirtualTimeLoop(clock)) as runner:
        return runner.run(coro)
